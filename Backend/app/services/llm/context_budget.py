"""Generic evidence-context budgeting for the live-web pipeline (specs/07
hardening). Nothing here is topic/company/category-specific — every
threshold is a config constant (app/config.py), and ranking uses only
signals every provider result already carries (provider relevance score,
publish date, term overlap with the query).

Never raises. Every function degrades to a safe, deterministic default on
bad input (empty list, non-string text, etc.) so a bug here can only ever
make evidence selection worse, never crash a chat request.
"""
import re
from datetime import datetime, timezone
from typing import Optional

CHARS_PER_TOKEN_ESTIMATE = 4  # heuristic; intentionally no tokenizer dependency


def estimate_tokens(text: str) -> int:
    """Cheap token-count proxy (chars / 4). Good enough for budgeting;
    never used for anything that needs to be exact."""
    return max(0, len(text or "")) // CHARS_PER_TOKEN_ESTIMATE


def _query_terms(query: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))


def _term_overlap_score(text: str, query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    text_terms = set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))
    if not text_terms:
        return 0.0
    return len(text_terms & query_terms) / len(query_terms)


def _recency_score(published_date: Optional[str]) -> float:
    """0..1, newer = higher. Missing date (e.g. every DuckDuckGo result)
    gets a NEUTRAL 0.4, never a penalty -- a dated-but-stale Tavily hit
    must not automatically outrank an undated-but-relevant DDG hit."""
    if not published_date:
        return 0.4
    try:
        parsed = datetime.fromisoformat(str(published_date)[:10])
        days_old = (datetime.now(timezone.utc).date() - parsed.date()).days
        if days_old <= 7:
            return 1.0
        if days_old <= 30:
            return 0.8
        if days_old <= 180:
            return 0.6
        if days_old <= 365:
            return 0.4
        return 0.2
    except Exception:
        return 0.4


def rank_snippet_pairs(
    texts: list[str], sources: list[dict], query: str
) -> tuple[list[str], list[dict]]:
    """Stable-sort (text, source) PAIRS together by a generic relevance
    score: provider score (Tavily's own 0..1 relevance, when present) +
    recency + query-term overlap. texts and sources MUST be the same
    length and already 1:1 aligned by position (as search_web produces
    them) -- this function preserves that alignment, it only reorders
    both lists identically. Never raises: on any error, returns the
    inputs unchanged (today's order).
    """
    try:
        if len(texts) != len(sources):
            return texts, sources
        terms = _query_terms(query)
        indexed = list(enumerate(zip(texts, sources)))

        def _score(item) -> float:
            _idx, (text, source) = item
            try:
                provider_score = float(source.get("score") or 0.0)
            except (TypeError, ValueError):
                provider_score = 0.0
            recency = _recency_score(source.get("published_date"))
            overlap = _term_overlap_score(text, terms)
            return provider_score * 0.4 + recency * 0.3 + overlap * 0.3

        ranked = sorted(indexed, key=_score, reverse=True)
        return (
            [text for _i, (text, _s) in ranked],
            [source for _i, (_t, source) in ranked],
        )
    except Exception:
        return texts, sources


def fit_pairs_to_budget(
    texts: list[str], sources: list[dict], max_chars: int
) -> tuple[list[str], list[dict], int]:
    """Greedily keep (text, source) pairs in the given order while the
    running character total stays under max_chars. Assumes the caller has
    already ranked best-first (rank_snippet_pairs) -- this function does
    not re-rank, it only decides where to stop. Returns
    (kept_texts, kept_sources, dropped_count). Never raises.
    """
    try:
        if len(texts) != len(sources):
            return texts, sources, 0
        kept_texts: list[str] = []
        kept_sources: list[dict] = []
        used = 0
        for text, source in zip(texts, sources):
            cost = len(text or "") + 20  # small per-item overhead
            if used + cost > max_chars:
                break
            kept_texts.append(text)
            kept_sources.append(source)
            used += cost
        dropped = len(texts) - len(kept_texts)
        return kept_texts, kept_sources, dropped
    except Exception:
        return texts, sources, 0
