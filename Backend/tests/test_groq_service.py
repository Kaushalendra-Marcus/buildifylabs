"""Groq structured outputs: strict json_schema mode + fallbacks (Phase 1).

Covers the transport layer in `app/services/llm/groq_service.py`: supported
models request `json_schema`/`strict: true`, unsupported models degrade cleanly
to today's `json_object` (or plain) behavior, and the existing
`_disable_json_transport` circuit breaker also gates strict mode. No network —
the SDK client is faked at the `AsyncGroq` seam, same as `test_groq_keys.py`.
"""
import asyncio
from types import SimpleNamespace

import app.services.llm.groq_service as groq_mod


def _install(monkeypatch, model="openai/gpt-oss-120b", replies=None):
    """Fake AsyncGroq so create() captures kwargs and returns canned text."""
    captured: dict = {}
    replies = list(replies or ["{}"])

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            action = replies.pop(0) if replies else "{}"
            if isinstance(action, Exception):
                raise action
            message = SimpleNamespace(content=action)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)], usage=None
            )

    class _Client:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=_Completions())

    async def _hf(prompt, system_prompt):
        return {"content": "hf", "usage": None, "source": "huggingface"}

    monkeypatch.setattr(groq_mod, "AsyncGroq", _Client)
    monkeypatch.setattr(groq_mod, "hf_fallback", _hf)
    monkeypatch.setattr(
        groq_mod,
        "settings",
        SimpleNamespace(
            groq_api_keys=["k1"],
            GROQ_MODEL=model,
            GROQ_FAST_MODEL=model,
        ),
    )
    groq_mod._reset_key_state()
    return captured


SCHEMA = {"type": "object", "properties": {"decision": {"type": "string"}}}


class TestStrictSchemaTransport:
    def test_strict_schema_requested_for_supported_model(self, monkeypatch):
        captured = _install(monkeypatch, model="openai/gpt-oss-120b")
        out = asyncio.run(
            groq_mod.generate_response(
                prompt="Return JSON.",
                system_prompt="Return JSON.",
                model="openai/gpt-oss-120b",
                json_schema={"name": "decision", "schema": SCHEMA},
            )
        )
        assert out["content"] == "{}"
        assert captured.get("response_format") == {
            "type": "json_schema",
            "json_schema": {
                "name": "decision",
                "strict": True,
                "schema": SCHEMA,
            },
        }

    def test_unsupported_model_falls_back_to_json_object_when_json_mode_set(
        self, monkeypatch
    ):
        # A model swap outside _STRICT_SCHEMA_MODELS must degrade cleanly to
        # today's json_object behavior — never raise, never send strict mode.
        captured = _install(monkeypatch, model="llama-3.3-70b-versatile")
        out = asyncio.run(
            groq_mod.generate_response(
                prompt="Return JSON.",
                system_prompt="Return JSON.",
                model="llama-3.3-70b-versatile",
                json_mode=True,
                json_schema={"name": "decision", "schema": SCHEMA},
            )
        )
        assert out["content"] == "{}"
        assert captured.get("response_format") == {"type": "json_object"}

    def test_unsupported_model_with_schema_only_falls_back_to_plain(
        self, monkeypatch
    ):
        # json_schema alone (no json_mode) on an unsupported model falls back
        # to plain text — the prose "JSON only" prompt + extract_json() still
        # recover it, so the schema requirement degrades without error.
        captured = _install(monkeypatch, model="llama-3.3-70b-versatile")
        out = asyncio.run(
            groq_mod.generate_response(
                prompt="Return JSON.",
                system_prompt="Return JSON.",
                model="llama-3.3-70b-versatile",
                json_schema={"name": "decision", "schema": SCHEMA},
            )
        )
        assert out["content"] == "{}"
        assert "response_format" not in captured

    def test_circuit_breaker_disables_strict_schema_during_cooldown(
        self, monkeypatch
    ):
        captured = _install(monkeypatch, model="openai/gpt-oss-120b")
        groq_mod._disable_json_transport("openai/gpt-oss-120b")
        out = asyncio.run(
            groq_mod.generate_response(
                prompt="Return JSON.",
                system_prompt="Return JSON.",
                model="openai/gpt-oss-120b",
                json_schema={"name": "decision", "schema": SCHEMA},
            )
        )
        assert out["content"] == "{}"
        # Strict mode is gated by the same breaker — no strict transport while
        # the cooldown holds; the call still succeeds via the fallback path.
        assert captured.get("response_format", {}).get("type") != "json_schema"


class TestToStrictSchema:
    def test_forces_additional_properties_and_required(self):
        from pydantic import BaseModel, Field
        from typing import List, Optional

        class _Nested(BaseModel):
            kind: str
            spec: str = ""

        class _Top(BaseModel):
            decision: str
            missing: str = ""
            nested: Optional[_Nested] = None
            tags: List[str] = Field(default_factory=list)

        schema = groq_mod._to_strict_schema(_Top)
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"].keys())
        nested = schema["$defs"]["_Nested"]
        assert nested["additionalProperties"] is False
        assert sorted(nested["required"]) == sorted(nested["properties"].keys())


def _install_stream(monkeypatch, scripts, model="openai/gpt-oss-120b"):
    """Fake AsyncGroq for streaming: {api_key: [chunk-list|Exception]}."""
    captured: dict = {}

    def _chunk_objects(chunks):
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c))])
            for c in chunks
        ]

    class _Completions:
        def __init__(self, api_key):
            self.api_key = api_key

        async def create(self, **kwargs):
            captured.update(kwargs)
            action = scripts[self.api_key].pop(0)
            if isinstance(action, Exception):
                raise action

            async def _gen():
                for chunk in _chunk_objects(action):
                    yield chunk

            return _gen()

    class _Client:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=_Completions(api_key))

    async def _hf(prompt, system_prompt):
        raise AssertionError("streaming must never touch the HF fallback")

    monkeypatch.setattr(groq_mod, "AsyncGroq", _Client)
    monkeypatch.setattr(groq_mod, "hf_fallback", _hf)
    monkeypatch.setattr(
        groq_mod,
        "settings",
        SimpleNamespace(
            groq_api_keys=list(scripts.keys()),
            GROQ_MODEL=model,
            GROQ_FAST_MODEL=model,
        ),
    )
    groq_mod._reset_key_state()
    return captured


async def _collect(gen):
    return [chunk async for chunk in gen]


class TestStreamResponse:
    def test_yields_deltas_in_order_without_response_format(self, monkeypatch):
        captured = _install_stream(monkeypatch, {"k1": [["Hel", "lo", " world"]]})
        chunks = asyncio.run(
            _collect(
                groq_mod.stream_response(
                    prompt="p", system_prompt="Return JSON.", model="openai/gpt-oss-120b"
                )
            )
        )
        assert chunks == ["Hel", "lo", " world"]
        assert captured.get("stream") is True
        # Prose-JSON mode: no response_format constraint on the stream.
        assert "response_format" not in captured

    def test_failed_key_fails_over_to_next_key(self, monkeypatch):
        class _RateLimited(Exception):
            status_code = 429

        captured = _install_stream(
            monkeypatch, {"k1": [_RateLimited("429")], "k2": [["fine"]]}
        )
        chunks = asyncio.run(
            _collect(groq_mod.stream_response(prompt="p", system_prompt="s"))
        )
        assert chunks == ["fine"]
        assert captured.get("stream") is True

    def test_all_keys_down_raises(self, monkeypatch):
        import pytest

        _install_stream(monkeypatch, {"k1": [RuntimeError("down")]})
        with pytest.raises(RuntimeError):
            asyncio.run(_collect(groq_mod.stream_response(prompt="p", system_prompt="s")))

    def test_empty_stream_raises(self, monkeypatch):
        import pytest

        _install_stream(monkeypatch, {"k1": [[]]})
        with pytest.raises(RuntimeError):
            asyncio.run(_collect(groq_mod.stream_response(prompt="p", system_prompt="s")))
