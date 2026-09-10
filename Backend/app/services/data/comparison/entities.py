"""Generic entity detection + ghost-entity floor. Split from comparison.py; behavior unchanged."""

from __future__ import annotations

import logging
import re

from typing import Dict, List

from .symbols import KNOWN_COMPANIES, _DISPLAY_NAMES
from .timeframe import is_comparison_query

logger = logging.getLogger(__name__)



# Function words that are never tradable/comparable entities on their own.
# Shared with web_search._fallback_entities_from_text so both deterministic
# entity inlets apply the same floor (ghost-entity fix: "My"/"If"/"US").
ENTITY_FUNCTION_STOPWORDS = frozenset({
    "my", "mine", "if", "it", "its", "we", "us", "our", "ours",
    "you", "your", "yours", "he", "him", "his", "she", "her", "hers",
    "they", "them", "their", "theirs",
    "this", "that", "these", "those",
    "so", "but", "not", "no", "nor",
    "do", "does", "did", "done",
    "can", "will", "would", "shall", "should", "could", "may", "might", "must",
    "has", "have", "had", "having", "been", "being", "was", "were", "are",
    "be", "am",
    "in", "on", "at", "to", "of", "by", "as", "into", "onto", "out",
    "up", "down", "off",
    "all", "each", "every", "few", "more", "most", "other", "some",
    "such", "only", "own", "same", "than", "too", "very", "just",
    "about", "through", "during", "before", "after", "above", "below",
    "under", "again", "further", "once", "here", "there",
    "when", "where", "why", "because", "until", "while",
    "although", "though", "since", "unless", "whereas", "whether",
    "plus", "minus", "per", "via", "etc", "eg", "ie",
    "vs", "uk", "usa",
    # Generic tech-category acronyms: never a single tradable company.
    "saas",
    # Imperative/analysis verbs: sentence-initial in instructions
    # ("Model three scenarios", "Keep pricing constant", "Rank them").
    # Never a standalone single-word entity; multi-word names are
    # unaffected (only a leading stopword is ever stripped).
    "model", "models", "keep", "keeps", "rank", "ranks",
    "assume", "assumes", "suppose", "consider", "estimate", "estimates",
    "project", "projects", "forecast", "forecasts", "predict", "predicts",
    "simulate", "simulates", "compute", "computes", "analyze", "analyses",
    "evaluate", "evaluates", "determine", "determines", "examine", "examines",
    "explore", "explores", "discuss", "discusses", "describe", "describes",
    "outline", "outlines", "summarize", "summarizes", "suggest", "suggests",
    "recommend", "recommends", "review", "reviews", "assess", "assesses",
    "measure", "measures", "track", "tracks", "monitor", "monitors",
    "plan", "plans", "build", "builds", "create", "creates",
    "make", "makes", "take", "takes", "use", "uses", "run", "runs",
    "display", "displays", "plot", "plots", "draw", "draws",
    "illustrate", "illustrates", "present", "presents",
    "provide", "provides", "send", "sends", "set", "puts", "put",
    "get", "gets", "hold", "holds", "apply", "applies",
    "try", "tries", "check", "checks", "test", "tests",
    "verify", "verifies", "confirm", "confirms", "ensure", "ensures",
    "choose", "chooses", "select", "selects", "pick", "picks",
    "add", "adds", "remove", "removes", "include", "includes",
    "exclude", "excludes", "ignore", "ignores", "skip", "skips",
    "start", "starts", "begin", "begins", "stop", "stops",
    "end", "ends", "continue", "continues", "remain", "remains",
    "stay", "stays", "become", "becomes", "seem", "seems",
    "look", "looks", "help", "helps", "need", "needs",
    "want", "wants", "tell", "tells", "find", "finds",
    "investigate", "identify", "identifies",
    "highlight", "highlights", "draft", "drafts",
    "write", "writes", "generate", "generates",
    "produce", "produces", "break", "breaks",
    "combine", "combines", "convert", "converts",
    "map", "maps", "match", "matches", "fit", "fits",
    "scale", "scales", "optimize", "optimizes",
    "improve", "improves", "walk",
    # Scenario-analysis nouns: never standalone entities.
    "scenario", "scenarios", "sensitivity",
    "conservative", "aggressive",
})

# Generic entity extraction (no question-specific lists): arbitrary
# Title-case / CamelCase / ALL-CAPS candidates minus stopwords, plus a
# structural "Compare A, B and C" fallback for lowercase phrasing. Known
# companies above are alias resolution only; detection itself is generic.
# The CamelCase word ([A-Z] ... with at least one lowercase) covers
# "OpenAI"/"WooCommerce"/"BigCommerce", which the old Title-case-only
# pattern could never match (its trailing \b fails mid-word). Pure-caps
# tokens ("US", "AI", "FY") stay owned by _GENERIC_ALLCAPS_RE below.
_GENERIC_CAMEL_WORD = r"[A-Z][A-Za-z]*[a-z][A-Za-z]*"
_GENERIC_CAP_RE = re.compile(
    rf"\b({_GENERIC_CAMEL_WORD}(?:\s+{_GENERIC_CAMEL_WORD}){{0,2}})\b"
)
_GENERIC_ALLCAPS_RE = re.compile(r"\b([A-Z]{2,}(?:\s+[A-Z]{2,})?)\b")
_GENERIC_ENTITY_STOPWORDS = frozenset({
    "compare", "comparison", "versus", "between", "and", "or",
    "the", "a", "an", "over", "last", "month", "week", "year", "quarter",
    "years", "weekly", "monthly", "daily", "average", "averages",
    "price", "prices", "performance", "percentage", "change", "chart",
    "line", "request", "revenue", "revenues", "growth", "profit",
    "profits", "profitability", "income", "incomes", "margin", "margins",
    "earnings", "sales", "loss", "losses", "dividend", "dividends",
    "return", "returns", "statistics", "statistic", "figures", "figure",
    "fiscal", "specify", "exact", "date", "range", "show", "give", "what",
    "which", "how", "is", "are", "for", "with", "from", "today", "latest",
    "current", "company", "companies", "highest", "strongest", "best",
    "better", "stronger", "explain", "main", "reasons", "behind",
    "difference", "differences", "calculator", "calculate", "percentage",
    "changes", "year", "statistics", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "jan", "feb", "mar", "apr", "jun", "jul",
    "aug", "sep", "sept", "oct", "nov", "dec", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
})


# Function words apply to generic detection too (single source of truth
# for the ghost-entity floor; web_search reuses ENTITY_FUNCTION_STOPWORDS).
_GENERIC_ENTITY_STOPWORDS = _GENERIC_ENTITY_STOPWORDS | ENTITY_FUNCTION_STOPWORDS


# Indicator/topic words that are never entities on their own: macro topics,
# metric nouns, and result-presentation language. A structural/capitalized
# candidate composed ENTIRELY of these ("Inflation", "Mortgage Rates",
# "Projected Results Visually") is a topic phrase, not a comparable entity.
# Exact-token match only (never substring: "MarketAxess" != "market").
_INDICATOR_TOKENS = frozenset({
    "inflation", "cpi", "mortgage", "rate", "rates", "housing",
    "house", "houses", "home", "homes",
    "affordability", "gdp", "unemployment", "interest", "fed",
    "treasury", "yield", "recession", "economy", "economic",
    "wage", "wages", "employment",
    "price", "prices", "pricing", "cost", "costs",
    "revenue", "revenues", "profit", "profits", "profitability",
    "stock", "stocks", "share", "shares", "market", "markets",
    "sale", "sales", "margin", "margins", "income", "incomes",
    "earnings", "loss", "losses", "growth",
    "projected", "projection", "baseline", "results", "result",
    "visually", "visual", "visualization", "chart", "charts",
    "table", "tables", "graph", "graphs", "estimated", "estimate",
    "impact", "outlook", "trend", "trends", "figure", "figures",
    # Comparison-aspect / capability vocabulary (generic across domains,
    # not just AI models): the label text a judge/LLM generates for
    # clarification options ("Benchmark scores and performance", "Context
    # window size and latency") is a list of ASPECTS to compare, never a
    # list of entities -- its capitalized lead words were leaking through
    # as ghost entities purely because they happened to be real English
    # nouns, not stopwords (observed: detect_entities returned ['Benchmark',
    # 'Context'] for a real clarification-answer merge; 'Pricing' was
    # already caught since it predates this addition).
    "benchmark", "benchmarks", "context", "latency", "capability",
    "capabilities", "feature", "features", "performance", "suitability",
    "quality", "speed", "accuracy", "reliability", "scalability",
    "security", "usability", "token", "tokens", "score", "scores",
    "overall", "window", "windows",
    # Generic descriptor nouns (never entities on their own, any domain).
    "size", "length", "level", "levels", "type", "types", "range",
    "count", "amount", "value", "values", "duration", "limit", "limits",
    # Generic comparison scaffolding (never entities on their own, any
    # domain): "compare cost of top models ... output prize" ghosted
    # "Cost Of Top" and "Output Prize Both" as comparable entities.
    "top", "tops", "input", "inputs", "output", "outputs",
    "prize", "prizes", "both", "either", "neither",
})


def _is_indicator_word(word: str) -> bool:
    """True when every alpha-token of the word is an indicator token OR a
    function-word stopword. The stopword allowance matters for multi-word
    candidates: "Cost Per Token" has "cost"/"token" as indicators but "per"
    as neither -- requiring literally every word to be a topic-indicator
    let a preposition-glued phrase like that slip through as a fake entity
    (observed by execution, not assumed).

    Hyphenated compounds split ("house-price" -> price); punctuation is
    ignored ("visually." -> visually). Pure.
    """
    parts = [p for p in re.split(r"[^a-z]+", (word or "").lower()) if p]
    return bool(parts) and all(
        p in _INDICATOR_TOKENS or p in _GENERIC_ENTITY_STOPWORDS for p in parts
    )


def _is_indicator_phrase(candidate: str) -> bool:
    """True when ALL words of a candidate are indicator words."""
    words = (candidate or "").split()
    return bool(words) and all(_is_indicator_word(w) for w in words)


def _first_mention_index(text_lower: str, entity: str) -> int:
    """Word-boundary appearance index (str.find matches substrings:

    "ai".find inside "openai" sorted the ghost entity first -- this does
    not). 10**9 when absent so unknown items sort last. Pure."""
    try:
        match = re.search(rf"\b{re.escape((entity or '').lower())}\b", text_lower or "")
        return match.start() if match else 10**9
    except re.error:
        return 10**9


def _single_token_occurrences(text: str, candidate: str) -> int:
    """Case-insensitive whole-word occurrence count for one candidate."""
    try:
        return len(re.findall(rf"\b{re.escape(candidate)}\b", text or "", re.IGNORECASE))
    except re.error:
        return 0


def _generic_capitalized_candidates(text: str) -> List[str]:
    """Arbitrary CamelCase / ALL-CAPS entity candidates in appearance order."""
    out: List[str] = []
    seen: set[str] = set()
    for pattern in (_GENERIC_ALLCAPS_RE, _GENERIC_CAP_RE):
        for match in pattern.finditer(text or ""):
            candidate = " ".join(match.group(1).split())
            words = candidate.split()
            while words and words[0].lower() in _GENERIC_ENTITY_STOPWORDS:
                words.pop(0)
            if not words:
                continue
            candidate = " ".join(words)
            key = candidate.lower()
            if key in seen or key in _GENERIC_ENTITY_STOPWORDS:
                continue
            if len(candidate) < 2:
                continue
            # Topic phrases are never entities ("Inflation" at a sentence
            # start, "Mortgage Rates" in a heading).
            if _is_indicator_phrase(candidate):
                continue
            # Structural backstop (not list-dependent): a single bare
            # capitalized word seen only once is a sentence-start artifact
            # ("My", "If") unless it is long enough to be a real name.
            # Multi-word candidates are exempt (never sentence-start junk).
            if len(words) == 1 and len(candidate) < 3 and (
                _single_token_occurrences(text, candidate) < 2
            ):
                continue
            seen.add(key)
            out.append(candidate)
    # Order by first whole-word appearance for determinism.
    lowered = (text or "").lower()
    out.sort(key=lambda e: _first_mention_index(lowered, e))
    return out


def _structural_comparison_candidates(text: str) -> List[str]:
    """Fallback for lowercase phrasing: slice the 'Compare A, B and C' span.

    Takes the text between "compare" and the first query-scope word
    (over/on/for/in/during/from/with/show/which) and splits on commas /
    and / vs / versus / or. Each non-empty piece that is not a metric or
    period word becomes a Title-cased entity candidate. Pure heuristic,
    only used when capitalized extraction yields fewer than 2.
    """
    raw = text or ""
    lowered = raw.lower()
    start = lowered.find("compare")
    if start < 0:
        for token in (" vs ", " versus ", " between "):
            start = lowered.find(token)
            if start >= 0:
                break
        if start < 0:
            return []
        segment = raw[start:]
    else:
        segment = raw[start + len("compare"):]
    # Cut at the first scope/metric/period cue, OR a standalone dash --
    # "fable vs astra comparison - Benchmark scores..." is a clarification-
    # merge continuation (or a natural qualifying clause), never more
    # comparison entities; without this cut the whole aspect-list survives
    # as one overlong piece and gets truncated into garbage like "Astra
    # Comparison -" (observed by execution).
    cut = re.search(
        r"\b(over|on|for|in|during|from|with|show|which|explain|calculate|between)\b"
        r"|\s+-\s+",
        segment, re.IGNORECASE,
    )
    if cut:
        segment = segment[:cut.start()]
    # ", and " must split as ONE delimiter: comma-first alternation order
    # stranded "and BigCommerce" as a piece (the Oxford-comma ghost).
    parts = re.split(
        r"\s*,\s+and\s+|\s*,\s*|\s+and\s+|\s+vs\.?\s+|\s+versus\s+|\s+or\s+",
        segment,
    )
    out: List[str] = []
    seen: set[str] = set()
    _NON_ENTITY = _GENERIC_ENTITY_STOPWORDS | {
        "stock", "share", "revenue", "profit", "growth", "performance",
    }
    for part in parts:
        piece = " ".join(part.strip().split())
        if not piece:
            continue
        # Strip leading determiners and stray conjunctions (belt over the
        # split fix above: "and BigCommerce" must never survive).
        piece = re.sub(r"^(the|a|an|and|or)\s+", "", piece, flags=re.IGNORECASE)
        if not piece or piece.lower() in _NON_ENTITY:
            continue
        # Keep first 1-3 words as the entity name.
        words = piece.split()[:3]
        # Drop trailing metric/period words.
        while words and words[-1].lower() in _NON_ENTITY:
            words.pop()
        if not words:
            continue
        candidate = " ".join(words)
        # Topic/metric spans are not entities ("inflation",
        # "house-price growth" -> "house-price", "mortgage rates ...").
        if _is_indicator_phrase(candidate):
            continue
        # Display Title-cased for determinism ("acme" -> "Acme").
        display = " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words)
        key = display.lower()
        if key in seen or key in _NON_ENTITY:
            continue
        seen.add(key)
        out.append(display)
    return out[:4]


def detect_entities(query: str) -> List[str]:
    """Detect entities generically (arbitrary names, not a fixed list).

    Known-company aliases resolve display names; "X startup" categories and
    generic capitalized / structural candidates cover arbitrary queries.
    Returns display names in query order, deduped, capped at 4.
    """
    text = query or ""
    lowered = text.lower()
    found: List[str] = []
    seen: set[str] = set()

    # Known companies first (longest names first so "advanced micro devices"
    # wins over a bare "amd" overlap, "toyota motor" over "toyota").
    # This is alias resolution only, not detection gating.
    for name in sorted(KNOWN_COMPANIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            display = _DISPLAY_NAMES.get(
                name, name.upper() if len(name) <= 4 else name.title()
            )
            key = display.lower()
            if key not in seen:
                seen.add(key)
                found.append(display)

    # Generic "X startup" entities for the robotics-vs-tech failure:
    # "tech startup or robotics startup" -> ["tech startup", "robotics startup"].
    for match in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9&.\-]*\s+startup)\b", text, re.IGNORECASE):
        candidate = " ".join(match.group(1).split())
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            found.append(candidate)

    # Generic arbitrary candidates (Title-case / ALL-CAPS) fill the rest.
    # No per-pass truncation here: the final maximal-munch + cap stage
    # below must see every candidate ("Google DeepMind" must be visible
    # to subsume the "Google" fragment at its position).
    for candidate in _generic_capitalized_candidates(text):
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    # Structural lowercase fallback when capitalization yields too few.
    if len(found) < 2 and is_comparison_query(text):
        for candidate in _structural_comparison_candidates(text):
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                found.append(candidate)

    # Order by first whole-word appearance; same-start longest wins
    # ("Google DeepMind" subsumes the "Google" fragment at its position --
    # maximal munch, so one mention never yields two overlapping entities).
    by_start: Dict[int, str] = {}
    for entity in found:
        pos = _first_mention_index(lowered, entity)
        if pos not in by_start or len(entity) > len(by_start[pos]):
            by_start[pos] = entity
    ordered = [by_start[pos] for pos in sorted(by_start)]
    return ordered[:4]

__all__ = [
    "ENTITY_FUNCTION_STOPWORDS",
    "_GENERIC_ALLCAPS_RE",
    "_GENERIC_CAMEL_WORD",
    "_GENERIC_CAP_RE",
    "_GENERIC_ENTITY_STOPWORDS",
    "_INDICATOR_TOKENS",
    "_first_mention_index",
    "_generic_capitalized_candidates",
    "_is_indicator_phrase",
    "_is_indicator_word",
    "_single_token_occurrences",
    "_structural_comparison_candidates",
    "detect_entities",
]
