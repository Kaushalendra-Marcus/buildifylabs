"""Call-time indirection to the public shim namespace.

Why this exists: tests patch generate_response / _historical_comparison_gate /
_visual_numbers_grounded / check_research_completeness on
app.services.llm.langchain_pipeline. After the split, the real implementations
live in this package's submodules -- a direct module-global reference would
freeze the original and silently ignore those patches. Resolving through the
shim at call time keeps every existing test (and any future patching) working.
Production is unaffected: the shim holds the very same objects.
"""
from typing import Any


async def call_llm(*args: Any, **kwargs: Any) -> Any:
    from app.services.llm import langchain_pipeline as _shim
    result = await _shim.generate_response(*args, **kwargs)
    # Cost ledger: one LLM call + its token usage (no-op outside a request).
    try:
        from app.services import cost_ledger

        cost_ledger.record_llm_call(result)
    except Exception:
        pass
    return result


async def call_llm_stream(*args: Any, **kwargs: Any) -> Any:
    """Yield streaming content deltas via the public shim namespace.

    Same indirection as `call_llm` (an async generator, so callers use
    `async for`): tests patch `stream_response` on
    `app.services.llm.langchain_pipeline` and production resolves the same
    object the shim re-exports from `groq_service`.
    """
    from app.services.llm import langchain_pipeline as _shim
    try:
        from app.services import cost_ledger

        cost_ledger.add("llm_calls", 1)  # streamed calls report no usage
    except Exception:
        pass
    async for chunk in _shim.stream_response(*args, **kwargs):
        yield chunk


def call_history_gate(*args: Any, **kwargs: Any) -> Any:
    from app.services.llm import langchain_pipeline as _shim
    return _shim._historical_comparison_gate(*args, **kwargs)


def call_numbers_grounded(*args: Any, **kwargs: Any) -> Any:
    from app.services.llm import langchain_pipeline as _shim
    return _shim._visual_numbers_grounded(*args, **kwargs)


def call_research_completeness(*args: Any, **kwargs: Any) -> Any:
    from app.services.llm import langchain_pipeline as _shim
    return _shim.check_research_completeness(*args, **kwargs)


def has_research_completeness() -> bool:
    from app.services.llm import langchain_pipeline as _shim
    return _shim.check_research_completeness is not None
