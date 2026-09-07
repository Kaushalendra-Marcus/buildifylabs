"""Groq primary LLM with HuggingFace fallback (Phase B0/B4 interim stack).

Key management: every configured key (GROQ_API_KEY..4) gets its own client.
Calls rotate round-robin across healthy keys; a key that 401s is retired for
the process lifetime, and any key that errors is skipped for the rest of the
call, so rate limits and revoked keys degrade instead of failing. Only after
every key is exhausted (or none is configured) does the request fall to HF.
"""
import asyncio
import itertools
import logging
import threading
import time
from typing import Optional

import httpx
from groq import AsyncGroq
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

HF_API_URL = f"https://api-inference.huggingface.co/models/{settings.HF_MODEL}"
HF_HEADERS = {"Authorization": f"Bearer {settings.HF_API_KEY}"}

_clients: dict[str, AsyncGroq] = {}
_key_cycle: Optional[itertools.cycle] = None
_cycle_lock = threading.Lock()
_bad_keys: set[str] = set()

# JSON-transport circuit breaker: some model deployments reject
# response_format=json_object (400 json_validate_failed) AND return empty
# completions on the plain retry for the same prompt. Rotating keys and
# re-sending response_format cannot fix a model-level incapability — it only
# burns latency (3 attempts x keys + backoff sleeps) before the inevitable
# fallback. Once a model trips it, skip response_format for that model for a
# cooldown. Prompts already demand "JSON only" in prose and extract_json()
# recovers prose-wrapped JSON, so plain mode stays parseable.
_no_json_transport: dict[str, float] = {}
_JSON_TRANSPORT_COOLDOWN_S = 600.0


def _reset_key_state() -> None:
    """Test seam: drop cached clients/rotation so settings overrides apply."""
    global _key_cycle
    _clients.clear()
    _bad_keys.clear()
    _no_json_transport.clear()
    _key_cycle = None


def _json_transport_disabled(model: str) -> bool:
    return _no_json_transport.get(model, 0.0) > time.monotonic()


def _disable_json_transport(model: str) -> None:
    _no_json_transport[model] = time.monotonic() + _JSON_TRANSPORT_COOLDOWN_S
    logger.warning(
        "JSON response_format disabled for model=%s for %ds "
        "(json_validate_failed); using prose-JSON mode.",
        model,
        int(_JSON_TRANSPORT_COOLDOWN_S),
    )


def _healthy_keys() -> list[str]:
    keys = [key for key in settings.groq_api_keys if key not in _bad_keys]
    return keys or [key for key in settings.groq_api_keys]


def _next_client() -> tuple[AsyncGroq, str]:
    """Round-robin client across healthy keys. Returns (client, key)."""
    global _key_cycle
    keys = _healthy_keys()
    with _cycle_lock:
        if _key_cycle is None:
            _key_cycle = itertools.cycle(range(10**9))
        position = next(_key_cycle)
    key = keys[position % len(keys)]
    if key not in _clients:
        _clients[key] = AsyncGroq(api_key=key)
    return _clients[key], key


async def hf_fallback(prompt: str, system_prompt: str) -> dict:
    logger.warning("Groq failed - switching to HuggingFace fallback")
    full_prompt = f"{system_prompt}\n\nUser: {prompt}\nAssistant:"
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.post(
                HF_API_URL,
                headers=HF_HEADERS,
                json={
                    "inputs": full_prompt,
                    "parameters": {"max_new_tokens": 512},
                },
            )
            response.raise_for_status()
            data = response.json()
            content = data[0].get("generated_text", "").replace(full_prompt, "").strip()
            return {"content": content, "usage": None, "source": "huggingface"}
    except Exception as e:
        logger.error(f"HuggingFace fallback also failed: {e}")
        raise RuntimeError("All LLM services failed")


def _classify_key_error(exc: Exception) -> str:
    """Best-effort status bucket for rotation decisions (SDK-agnostic)."""
    status = getattr(exc, "status_code", None)
    if status is None and getattr(exc, "response", None) is not None:
        status = getattr(exc.response, "status_code", None)
    if status == 401:
        return "unauthorized"
    if status == 429:
        return "rate_limited"
    if status == 404 or "model" in str(exc).lower() and "not exist" in str(exc).lower():
        return "model_not_found"
    return "error"


def _error_detail(exc: Exception) -> str:
    """Keep provider validation details visible without exposing API keys."""
    status = getattr(exc, "status_code", None)
    detail = str(exc)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            detail = response.text or detail
        except Exception:
            pass
    return f"status={status or 'unknown'} detail={detail[:400]}"


async def generate_response(
    prompt: str,
    system_prompt: str = "You are a helpful AI assistant.",
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 512,
    json_mode: bool = False,
) -> dict:
    """Plain-text by default; `json_mode=True` forces Groq's JSON-object mode
    for structured calls (judge/narration/rewriter). The HF fallback takes no
    such flag and simply answers, so JSON callers must still validate/parse
    defensively. Requires the word "JSON" in the messages (all our JSON
    prompts have it)."""
    selected_model = model or settings.GROQ_MODEL

    if not settings.groq_api_keys:
        return await hf_fallback(prompt, system_prompt)

    use_json_transport = bool(json_mode) and not _json_transport_disabled(
        selected_model
    )
    extra: dict = {}
    if use_json_transport:
        extra["response_format"] = {"type": "json_object"}

    last_error: Optional[Exception] = None
    for attempt in range(3):
        tried_this_round = 0
        for _ in range(len(_healthy_keys()) or 1):
            client, key = _next_client()
            try:
                response = await client.chat.completions.create(
                    model=selected_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **extra,
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise RuntimeError("Groq returned an empty completion")
                usage = response.usage if hasattr(response, "usage") else None
                return {"content": content, "usage": usage, "source": "groq"}
            except Exception as e:
                last_error = e
                tried_this_round += 1
                kind = _classify_key_error(e)
                # Some Groq model deployments reject JSON response_format for
                # particular prompts even though they can return JSON when
                # instructed in the prompt. Retry the same key without that
                # optional transport constraint before rotating credentials.
                # A 400 on the JSON transport also trips the circuit breaker
                # so later calls skip the doomed attempt outright.
                if (
                    kind == "error"
                    and json_mode
                    and getattr(e, "status_code", None) == 400
                ):
                    _disable_json_transport(selected_model)
                if kind == "error" and use_json_transport and getattr(e, "status_code", None) == 400:
                    try:
                        response = await client.chat.completions.create(
                            model=selected_model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        content = response.choices[0].message.content
                        if content and content.strip():
                            usage = response.usage if hasattr(response, "usage") else None
                            return {"content": content, "usage": usage, "source": "groq"}
                        logger.warning(
                            "Groq plain-text retry returned empty content "
                            "(model=%s); rotating.",
                            selected_model,
                        )
                    except Exception as retry_error:
                        last_error = retry_error
                        logger.warning(
                            "Groq plain-text retry failed (model=%s): %s",
                            selected_model,
                            _error_detail(retry_error),
                        )
                logger.warning(
                    "Groq request failed (%s, model=%s, json_mode=%s): %s",
                    kind,
                    selected_model,
                    json_mode,
                    _error_detail(e),
                )
                if kind == "model_not_found":
                    raise RuntimeError(
                        f"Configured Groq model '{selected_model}' was not found or is unavailable. "
                        "Update GROQ_MODEL in Backend/.env."
                    ) from e
                if kind == "unauthorized":
                    _bad_keys.add(key)
        if tried_this_round == 0:
            break
        await asyncio.sleep(1 * (attempt + 1))

    logger.warning(f"All Groq keys exhausted, last error: {last_error}")
    return await hf_fallback(prompt, system_prompt)
