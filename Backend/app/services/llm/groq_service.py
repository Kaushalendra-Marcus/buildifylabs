import asyncio
import logging
from typing import AsyncGenerator

import httpx
from groq import AsyncGroq
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)

HF_API_URL = f"https://api-inference.huggingface.co/models/{settings.HF_MODEL}"
HF_HEADERS = {"Authorization": f"Bearer {settings.HF_API_KEY}"}


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


async def generate_response(
    prompt: str,
    system_prompt: str = "You are a helpful AI assistant.",
    model: str = settings.GROQ_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 512,
    json_mode: bool = False,
) -> dict:
    """Plain-text by default; `json_mode=True` forces Groq's JSON-object mode
    for structured calls (judge/narration/rewriter). The HF fallback takes no
    such flag and simply answers, so JSON callers must still validate/parse
    defensively. Requires the word "JSON" in the messages (all our JSON
    prompts have it)."""
    retries = 3
    for attempt in range(retries):
        try:
            kwargs: dict = {}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            content = response.choices[0].message.content
            usage = response.usage if hasattr(response, "usage") else None
            return {"content": content, "usage": usage, "source": "groq"}

        except Exception as e:
            logger.warning(f"Groq attempt {attempt + 1} failed: {e}")
            if attempt == retries - 1:
                return await hf_fallback(prompt, system_prompt)
            await asyncio.sleep(1 * (attempt + 1))


async def stream_response(
    prompt: str,
    system_prompt: str = "You are a helpful AI assistant.",
    model: str = settings.GROQ_MODEL,
) -> AsyncGenerator[str, None]:
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    except Exception as e:
        logger.error(f"Groq streaming failed: {e}")
        yield "LLM service unavailable. Please try again."
