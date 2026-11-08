"""Deterministic post-narration prose guards.

``apply_narration_contract`` (grounding.py) already *detects* ungrounded numbers
but only writes them to the trace. These guards act on what they find:

* **Number guard** - every number in the answer / summary / insights /
  root causes / recommendations must trace to the evidence (rows, computed
  stats, structured history, snippets, the user's own question) or be
  *derivable* from it (sum, difference, ratio, share, % change of two evidence
  values). Anything else is an untraceable number.
* **Citation-support guard** - a sentence that cites ``[n]`` must actually be
  supported by snippet n: enough shared content words, and its non-structural
  numbers must appear in (or be derivable from) that snippet. This catches the
  classic failure of attaching a real citation to an unrelated claim.

Modes (settings ``PROSE_NUMBER_GUARD_MODE`` / ``CITATION_SUPPORT_GUARD_MODE``):
``off`` | ``flag`` (record, cap confidence at 0.65, note in the trace) |
``strip`` (flag + remove the offending sentences / list items).

Number extraction reuses ``grounding._parse_scaled_number`` so the runtime guard
and the nightly eval (scripts/eval_pipeline.py) agree on what a number is.
Everything here is pure and never raises into the pipeline.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.config import get_settings

from . import guard_metrics
from .grounding import (
    _evidence_numbers,
    _is_citation_token,
    _numbers_match,
    _parse_scaled_number,
    _strip_citations,
)
from .models import PipelineOutput
from .prompting import fallback_output

logger = logging.getLogger(__name__)

CONFIDENCE_CAP = 0.65

_CITATION_RE = re.compile(r"\[(\d+)\]")
_SPLIT_RE = re.compile(r"((?<=[.!?])\s+|\n+)")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_MONTH_DAY_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"\s+\d{1,2}(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
# Labels glued to digits (Q1, FY24, H2, B2B, E1) are names, not claims.
_LABEL_TOKEN_RE = re.compile(r"\b[A-Za-z]{1,6}\d+[A-Za-z0-9]*\b")
_SHORT_SPAN_RE = re.compile(r"\b\d+(?:Y|y|yr|yrs)\b")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_UNICODE_DASHES = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
}
_WORD_RE = re.compile(r"[A-Za-z]{4,}")
_STOPWORDS = frozenset(
    "that this with from have been were will would could about their there which "
    "while than then into over under also more most some such very between during "
    "after before each both other these those where when what your they them here "
    "across because through within without should might many much only just like "
    "being does done make made".split()
)

_NOTHING_VERIFIED = (
    "I couldn't verify the figures in my draft answer against your data, so I'm "
    "not stating them. Please check the query and rows below, or rephrase the "
    "question."
)
_STRIPPED_NOTE = (
    "(Some statements were removed because their figures or citations could "
    "not be verified against the evidence.)"
)

# Direct match tolerance (rounding in prose: "about 1.2M"), and a tighter one
# for *derived* values so sums/ratios can't accidentally "ground" a wrong number.
_DIRECT_TOL = 0.03
_DERIVED_TOL = 0.015
_PAIR_POOL_MAX = 220
_SNIPPET_SUPPORT_TOL = 0.05
_MIN_WORDS_FOR_LEXICAL_CHECK = 5
_MIN_OVERLAP = 0.15


def _mode(value: Any) -> str:
    text = str(value or "flag").strip().lower()
    return text if text in ("off", "flag", "strip") else "flag"


# ---------------------------------------------------------------------------
# Sentence helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> Tuple[List[str], List[str]]:
    """Split into sentences + the separators that followed them."""
    parts = _SPLIT_RE.split(text or "")
    return parts[0::2], parts[1::2]


def _rejoin(sentences: Sequence[str], seps: Sequence[str], drop: Set[int]) -> str:
    out: List[str] = []
    for index, sentence in enumerate(sentences):
        if index in drop:
            continue
        out.append(sentence)
        if index < len(seps):
            out.append(seps[index])
    return "".join(out).strip()


# ---------------------------------------------------------------------------
# Number extraction + grounding
# ---------------------------------------------------------------------------

def _claimed_numbers(text: str) -> List[float]:
    """Numbers a reader would take as claims.

    Citation markers, ISO dates, month-day dates, label tokens (Q1, FY24),
    year-like integers and short spans (3Y) are not claims. Whole numbers 0-10
    are treated as counts/ranks ("3 regions", "top 5") unless written with a
    percent sign, where a small number is still a factual claim.
    """
    scrubbed = _strip_citations(text or "")
    for dash, ascii_dash in _UNICODE_DASHES.items():
        scrubbed = scrubbed.replace(dash, ascii_dash)
    scrubbed = _ISO_DATE_RE.sub(" ", scrubbed)
    scrubbed = _MONTH_DAY_RE.sub(" ", scrubbed)
    scrubbed = _LABEL_TOKEN_RE.sub(" ", scrubbed)
    scrubbed = _SHORT_SPAN_RE.sub(" ", scrubbed)
    percents: Set[float] = set()
    for raw in _PERCENT_RE.findall(scrubbed):
        try:
            percents.add(float(raw))
        except ValueError:
            continue
    claimed: List[float] = []
    for value in _parse_scaled_number(scrubbed):
        number = float(value)
        if number.is_integer() and 0 <= number <= 10 and number not in percents:
            continue
        claimed.append(number)
    return claimed


@dataclass
class GroundingContext:
    """Everything a number may legitimately be traced to."""

    direct: List[float] = field(default_factory=list)
    structured: List[float] = field(default_factory=list)
    query_numbers: List[float] = field(default_factory=list)
    pair_pool: List[float] = field(default_factory=list)


def _dedupe(values: Sequence[float], limit: int) -> List[float]:
    seen: Set[float] = set()
    out: List[float] = []
    for value in values:
        try:
            key = round(float(value), 6)
        except (TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(float(value))
        if len(out) >= limit:
            break
    return out


def build_context(
    *,
    queries: Sequence[str],
    snippets: Optional[list] = None,
    rows: Optional[Sequence[dict]] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    market_data: Optional[list] = None,
    computed_numbers: Optional[dict] = None,
) -> GroundingContext:
    structured = _evidence_numbers(
        snippets=None, rows=rows, price_history=price_history,
        financial_history=financial_history, market_data=market_data,
        computed_numbers=computed_numbers,
    )
    snippet_values = _evidence_numbers(snippets=snippets)
    query_numbers: List[float] = []
    for query in queries:
        try:
            query_numbers.extend(float(v) for v in _parse_scaled_number(query or ""))
        except Exception:
            continue
    structured_u = _dedupe(structured, 5000)
    snippet_u = _dedupe(snippet_values, 5000)
    return GroundingContext(
        direct=_dedupe(list(structured_u) + list(snippet_u), 8000),
        structured=structured_u,
        query_numbers=query_numbers,
        pair_pool=_dedupe(list(structured_u[:160]) + list(snippet_u[:60]), _PAIR_POOL_MAX),
    )


def _derivable(value: float, pool: Sequence[float]) -> bool:
    """True when ``value`` is a sum/difference/ratio/share/%-change of two pool values."""
    target = abs(value)
    count = len(pool)
    for i in range(count):
        a = pool[i]
        for j in range(count):
            b = pool[j]
            if i < j:
                if _numbers_match(a + b, target, _DERIVED_TOL):
                    return True
                if _numbers_match(abs(a - b), target, _DERIVED_TOL):
                    return True
                total = a + b
                if total:
                    if _numbers_match(a / total * 100.0, target, _DERIVED_TOL):
                        return True
                    if _numbers_match(b / total * 100.0, target, _DERIVED_TOL):
                        return True
            if b:
                ratio = a / b
                if _numbers_match(ratio, target, _DERIVED_TOL):
                    return True
                if _numbers_match(ratio * 100.0, target, _DERIVED_TOL):
                    return True
                pct = abs((a - b) / b * 100.0)
                if abs(pct - target) <= max(0.06, target * 0.02):
                    return True
    return False


def _grounded(value: float, ctx: GroundingContext) -> bool:
    for candidate in ctx.direct:
        if _numbers_match(candidate, value, _DIRECT_TOL):
            return True
    for candidate in ctx.query_numbers:
        if _numbers_match(candidate, value, 0.01):
            return True
    return _derivable(value, ctx.pair_pool)


def find_ungrounded_numbers(text: str, ctx: GroundingContext) -> List[float]:
    """Numbers in ``text`` that trace to no evidence."""
    try:
        return [v for v in _claimed_numbers(text) if not _grounded(v, ctx)]
    except Exception as exc:
        logger.warning("Number grounding check failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Citation support
# ---------------------------------------------------------------------------

def _content_words(text: str) -> Set[str]:
    return {
        word.lower()
        for word in _WORD_RE.findall(_strip_citations(text or ""))
        if word.lower() not in _STOPWORDS
    }


def _cited_indices(sentence: str, snippet_count: int) -> List[int]:
    cited: List[int] = []
    for match in _CITATION_RE.finditer(sentence or ""):
        raw = match.group(1)
        if not _is_citation_token(raw):
            continue
        try:
            number = int(raw)
        except ValueError:
            continue
        if 1 <= number <= snippet_count and number not in cited:
            cited.append(number)
    return cited


def citation_issue(
    sentence: str, snippets: Sequence[Any], ctx: GroundingContext
) -> Optional[str]:
    """Why ``sentence``'s citation(s) don't support it, or None when they do."""
    try:
        cited = _cited_indices(sentence, len(snippets))
        if not cited:
            return None
        words = _content_words(sentence)
        # Numbers backed by structured evidence (rows/computed/history) need no
        # snippet support; only the rest must be found in the cited snippet.
        needs_snippet = [
            v for v in _claimed_numbers(sentence)
            if not any(_numbers_match(c, v, _DIRECT_TOL) for c in ctx.structured)
        ]
        reason = "no cited snippet supports this sentence"
        for number in cited:
            snippet = str(snippets[number - 1] or "")
            snippet_words = _content_words(snippet)
            lexical_ok = (
                len(words) < _MIN_WORDS_FOR_LEXICAL_CHECK
                or (len(words & snippet_words) / max(1, len(words))) >= _MIN_OVERLAP
            )
            snippet_values = [float(v) for v in _parse_scaled_number(snippet)]
            numbers_ok = all(
                any(_numbers_match(c, v, _SNIPPET_SUPPORT_TOL) for c in snippet_values)
                or _derivable(v, snippet_values[:150])
                for v in needs_snippet
            )
            if lexical_ok and numbers_ok:
                return None
            if not lexical_ok:
                reason = f"[{number}] shares almost no content with this sentence"
            elif not numbers_ok:
                reason = f"a figure in this sentence is not in [{number}]"
        return reason
    except Exception as exc:
        logger.warning("Citation support check failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def _fmt(values: Sequence[float]) -> str:
    return ", ".join(f"{v:,.4g}" for v in list(values)[:4])


def _cap_confidence(output: PipelineOutput) -> bool:
    try:
        current = float(output.confidence or 0.0)
        if current > CONFIDENCE_CAP:
            output.confidence = CONFIDENCE_CAP
            return True
    except (TypeError, ValueError):
        pass
    return False


def enforce_prose_guards(
    output: PipelineOutput,
    *,
    query: str,
    plan_query: str,
    snippets: Optional[list],
    rows: Optional[Sequence[dict]],
    price_history: Optional[list],
    financial_history: Optional[list],
    market_data: Optional[list],
    computed_numbers: Optional[dict],
    thinking: Optional[list],
) -> PipelineOutput:
    """Run the number + citation guards on a validated narration."""
    settings = get_settings()
    number_mode = _mode(getattr(settings, "PROSE_NUMBER_GUARD_MODE", "flag"))
    citation_mode = _mode(getattr(settings, "CITATION_SUPPORT_GUARD_MODE", "flag"))
    if output.clarification is not None:
        return output  # a question makes no numeric claims
    if number_mode == "off" and citation_mode == "off":
        return output

    snippets = list(snippets or [])
    ctx = build_context(
        queries=[query, plan_query], snippets=snippets, rows=rows,
        price_history=price_history, financial_history=financial_history,
        market_data=market_data, computed_numbers=computed_numbers,
    )
    flagged = False

    sentences, seps = _split_sentences(output.answer or "")
    bad_numbers: Dict[int, List[float]] = {}
    bad_citations: Dict[int, str] = {}
    for index, sentence in enumerate(sentences):
        if number_mode != "off":
            values = find_ungrounded_numbers(sentence, ctx)
            if values:
                bad_numbers[index] = values
        if citation_mode != "off" and snippets:
            issue = citation_issue(sentence, snippets, ctx)
            if issue:
                bad_citations[index] = issue

    if bad_numbers:
        flat = [v for vals in bad_numbers.values() for v in vals]
        guard_metrics.record_guard(
            "prose_number_untraceable", f"{len(flat)} number(s): {_fmt(flat)}"
        )
        if thinking is not None:
            thinking.append(
                f"Guard: {len(flat)} number(s) in the answer could not be traced "
                f"to the evidence ({_fmt(flat)})."
            )
        flagged = True
    if bad_citations:
        guard_metrics.record_guard(
            "citation_unsupported", f"{len(bad_citations)} sentence(s)"
        )
        if thinking is not None:
            first = next(iter(bad_citations.values()))
            thinking.append(
                f"Guard: {len(bad_citations)} cited sentence(s) not supported by "
                f"their source ({first})."
            )
        flagged = True

    drops: Set[int] = set()
    if number_mode == "strip":
        drops |= set(bad_numbers)
    if citation_mode == "strip":
        drops |= set(bad_citations)
    if drops:
        kept = _rejoin(sentences, seps, drops)
        if kept:
            output.answer = (
                kept if _STRIPPED_NOTE in kept else f"{kept}\n\n{_STRIPPED_NOTE}"
            )
        else:
            output.answer = _NOTHING_VERIFIED
        guard_metrics.record_guard("prose_sentences_stripped", f"{len(drops)}")

    # Secondary prose fields: same number guard, whole-item granularity.
    if number_mode != "off":
        summary = getattr(output, "summary", "") or ""
        if isinstance(summary, str) and summary.strip():
            values = find_ungrounded_numbers(summary, ctx)
            if values:
                guard_metrics.record_guard(
                    "summary_number_untraceable", _fmt(values)
                )
                flagged = True
                if number_mode == "strip":
                    output.summary = ""
        for field_name in ("insights", "root_causes", "recommendations"):
            items = getattr(output, field_name, None)
            if not isinstance(items, list) or not items:
                continue
            kept_items: List[Any] = []
            for item in items:
                if isinstance(item, str):
                    values = find_ungrounded_numbers(item, ctx)
                    if values:
                        guard_metrics.record_guard(
                            f"{field_name}_number_untraceable", _fmt(values)
                        )
                        flagged = True
                        if number_mode == "strip":
                            continue
                kept_items.append(item)
            if number_mode == "strip" and len(kept_items) != len(items):
                try:
                    setattr(output, field_name, kept_items)
                except Exception:
                    pass

    if flagged and _cap_confidence(output) and thinking is not None:
        thinking.append(
            f"Confidence capped at {CONFIDENCE_CAP}: some statements are unverified."
        )
    return output


def guard_rescued_prose(
    output: PipelineOutput,
    *,
    query: str,
    snippets: Optional[list],
    rows: Optional[Sequence[dict]],
    market_data: Optional[list],
    computed_numbers: Optional[dict],
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
) -> PipelineOutput:
    """Strict check for the prose-rescue path (structured narration failed).

    That path has no schema, no visual grounding and no narration contract, so
    any sentence with an untraceable number is removed outright. If nothing is
    left, the honest zero-confidence fallback is returned instead.
    """
    if not getattr(get_settings(), "PROSE_RESCUE_STRICT", True):
        return output
    try:
        ctx = build_context(
            queries=[query], snippets=snippets, rows=rows,
            price_history=price_history, financial_history=financial_history,
            market_data=market_data, computed_numbers=computed_numbers,
        )
        sentences, seps = _split_sentences(output.answer or "")
        drops = {
            index for index, sentence in enumerate(sentences)
            if find_ungrounded_numbers(sentence, ctx)
        }
        if not drops:
            return output
        guard_metrics.record_guard("rescue_prose_stripped", f"{len(drops)} sentence(s)")
        kept = _rejoin(sentences, seps, drops)
        if not kept:
            return fallback_output(reason=_NOTHING_VERIFIED, confidence=0.0)
        output.answer = kept
        return output
    except Exception as exc:
        logger.warning("Rescue prose guard failed: %s", exc)
        guard_metrics.record_guard("guard_error:rescue_prose", str(exc))
        return output


def fail_closed_after_guard_error(
    output: Optional[PipelineOutput],
    thinking: Optional[list],
    guard_name: str,
    exc: BaseException,
) -> Optional[PipelineOutput]:
    """A verification step crashed: say so and cap confidence, don't pass silently."""
    try:
        guard_metrics.record_guard(f"guard_error:{guard_name}", str(exc))
        if not getattr(get_settings(), "FAIL_CLOSED_GUARDS", True):
            return output
        if output is not None and getattr(output, "clarification", None) is None:
            _cap_confidence(output)
        if thinking is not None:
            thinking.append(
                f"A verification step ({guard_name}) was unavailable; "
                "confidence capped because the answer is unverified."
            )
    except Exception:
        pass
    return output


__all__ = [
    "CONFIDENCE_CAP",
    "GroundingContext",
    "build_context",
    "citation_issue",
    "enforce_prose_guards",
    "fail_closed_after_guard_error",
    "find_ungrounded_numbers",
    "guard_rescued_prose",
]
