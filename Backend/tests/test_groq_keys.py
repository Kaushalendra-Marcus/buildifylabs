"""Groq multi-key rotation + failover (no network — faked SDK clients)."""
import asyncio
from types import SimpleNamespace

import app.services.llm.groq_service as groq_mod


class _KeyedError(Exception):
    def __init__(self, status_code):
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def _install(monkeypatch, scripts, hf_text="hf answer"):
    """Scripts: {api_key: [reply|Exception, ...]} consumed per create() call."""
    seen_keys: list[str] = []

    class _Completions:
        def __init__(self, api_key):
            self.api_key = api_key

        async def create(self, **kwargs):
            seen_keys.append(self.api_key)
            action = scripts[self.api_key].pop(0)
            if isinstance(action, Exception):
                raise action
            message = SimpleNamespace(content=action)
            choice = SimpleNamespace(message=message)
            return SimpleNamespace(choices=[choice])

    class _Client:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=_Completions(api_key))

    def _factory(api_key):
        return _Client(api_key)

    async def _hf(prompt, system_prompt):
        return {"content": hf_text, "usage": None, "source": "huggingface"}

    monkeypatch.setattr(groq_mod, "AsyncGroq", _factory)
    monkeypatch.setattr(groq_mod, "hf_fallback", _hf)
    monkeypatch.setattr(
        groq_mod,
        "settings",
        SimpleNamespace(
            groq_api_keys=list(scripts.keys()),
            GROQ_MODEL="test-model",
            GROQ_FAST_MODEL="test-fast-model",
        ),
    )
    groq_mod._reset_key_state()
    return seen_keys


def _run(prompt="q"):
    return asyncio.run(
        groq_mod.generate_response(prompt=prompt, system_prompt="You are JSON.")
    )


class TestKeyRotation:
    def test_round_robins_across_keys(self, monkeypatch):
        seen = _install(monkeypatch, {"k1": ["a1", "a3"], "k2": ["a2"]})

        assert _run()["content"] == "a1"
        assert _run()["content"] == "a2"
        assert _run()["content"] == "a3"
        assert seen == ["k1", "k2", "k1"]

    def test_rate_limited_key_fails_over_within_one_call(self, monkeypatch):
        seen = _install(
            monkeypatch, {"k1": [_KeyedError(429)], "k2": ["fine"]}
        )

        assert _run()["content"] == "fine"
        assert seen == ["k1", "k2"]

    def test_unauthorized_key_retired_for_later_calls(self, monkeypatch):
        seen = _install(
            monkeypatch, {"k1": [_KeyedError(401), "unused"], "k2": ["b1", "b2"]}
        )

        assert _run()["content"] == "b1"
        assert _run()["content"] == "b2"
        assert "k1" not in seen[1:]

    def test_all_keys_down_falls_back_to_hf(self, monkeypatch):
        seen = _install(
            monkeypatch,
            {"k1": [_KeyedError(429)] * 4, "k2": [_KeyedError(500)] * 4},
            hf_text="hf answer",
        )

        assert _run()["content"] == "hf answer"
        assert set(seen) == {"k1", "k2"}

    def test_no_keys_goes_straight_to_hf(self, monkeypatch):
        seen = _install(monkeypatch, {}, hf_text="hf direct")

        assert _run()["content"] == "hf direct"
        assert seen == []

    def test_json_mode_reaches_the_sdk(self, monkeypatch):
        captured: dict = {}

        class _Completions:
            async def create(self, **kwargs):
                captured.update(kwargs)
                message = SimpleNamespace(content="{}")
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        class _Client:
            def __init__(self, api_key):
                self.chat = SimpleNamespace(completions=_Completions())

        monkeypatch.setattr(groq_mod, "AsyncGroq", _Client)
        monkeypatch.setattr(
            groq_mod,
            "settings",
            SimpleNamespace(
                groq_api_keys=["k1"],
                GROQ_MODEL="test-model",
                GROQ_FAST_MODEL="test-fast-model",
            ),
        )
        groq_mod._reset_key_state()

        asyncio.run(
            groq_mod.generate_response(
                prompt="p", system_prompt="Return JSON.", json_mode=True
            )
        )
        assert captured.get("response_format") == {"type": "json_object"}
