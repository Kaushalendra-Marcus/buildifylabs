"""HF Inference API embeddings (Part C). HTTP is mocked at the
httpx.AsyncClient seam — no network, no key needed."""

import asyncio

import pytest

import app.services.data.embeddings as embeddings_mod
from app.services.data.embeddings import (
    EmbeddingError,
    embed_text,
    embed_texts,
)


class _FakeResponse:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    def raise_for_status(self):
        if self._exc is not None:
            raise self._exc

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.seen = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self.seen = {"url": url, "headers": headers, "json": json}
        if self._exc is not None:
            raise self._exc
        return self._response


def _patch_client(monkeypatch, response=None, exc=None):
    fake = _FakeClient(response=response, exc=exc)

    def factory(*args, **kwargs):
        return fake

    monkeypatch.setattr(embeddings_mod.httpx, "AsyncClient", factory)
    return fake


def run(coro):
    return asyncio.run(coro)


class TestEmbedTexts:
    def test_batch_returns_one_vector_per_input(self, monkeypatch):
        vec = [0.1] * 384
        fake = _patch_client(
            monkeypatch, response=_FakeResponse(payload=[vec, vec])
        )
        out = run(embed_texts(["hello", "world"]))
        assert out == [vec, vec]
        assert fake.seen["json"]["inputs"] == ["hello", "world"]
        assert "wait_for_model" in str(fake.seen["json"])

    def test_per_token_shape_is_mean_pooled(self, monkeypatch):
        _patch_client(
            monkeypatch,
            response=_FakeResponse(
                payload=[[[1.0, 2.0], [3.0, 4.0]]]
            ),
        )
        out = run(embed_texts(["hello"]))
        assert out == [[2.0, 3.0]]

    def test_unexpected_shape_item_is_none_not_crash(self, monkeypatch):
        vec = [0.1] * 384
        _patch_client(
            monkeypatch, response=_FakeResponse(payload=[vec, "garbage"])
        )
        out = run(embed_texts(["a", "b"]))
        assert out[0] == vec
        assert out[1] is None

    def test_http_error_raises_embedding_error(self, monkeypatch):
        import httpx

        _patch_client(
            monkeypatch,
            exc=httpx.HTTPError("boom"),
        )
        with pytest.raises(EmbeddingError):
            run(embed_texts(["hello"]))

    def test_empty_input_returns_empty(self, monkeypatch):
        assert run(embed_texts([])) == []

    def test_single_wrapper(self, monkeypatch):
        vec = [0.5] * 384
        _patch_client(monkeypatch, response=_FakeResponse(payload=[vec]))
        assert run(embed_text("hello")) == vec
