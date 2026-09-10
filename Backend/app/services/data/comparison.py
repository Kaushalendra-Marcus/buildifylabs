"""Deterministic multi-entity, multi-metric historical comparison support.

Root cause of the NVIDIA-vs-AMD failure (and the earlier tech-startup vs
robotics-startup failure): the pipeline had

- no query decomposition (entities / metrics / historical period),
- only a 1-month Yahoo price series + a current-snapshot quoteSummary, which
  were charted as if they answered a 3-year historical question,
- no evidence model (entity / metric / value / unit / period / source /
  historical-vs-current),
- no period or comparison validation, and
- an unconditional visual guarantee that charted whatever arrived even at
  0% confidence.

This module is the deterministic core that fixes all of those without any
LLM arithmetic (specs/11 S2: the LLM narrates, code computes):

- :func:`decompose_comparison_query` -- entities, metrics, historical period.
- :func:`validate_historical_coverage` -- a 1-month / snapshot series can
  never satisfy a ~3-year request.
- :func:`validate_comparison` -- same metric / unit / period / frequency /
  definition, both entities present.
- :func:`compute_pct_change` / :func:`compute_comparison_stats` -- stock and
  revenue % changes plus a single shared profitability metric, with the
  formula and assumptions stated.
- :func:`comparison_confidence` / :func:`insufficient_reason` -- evidence
  completeness drives confidence; missing evidence blocks visualization.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known companies -> Yahoo symbols. The web-search STOCK_ALIASES map is the
# live lookup table; this mirror exists so decomposition never depends on the
# network and so tests can assert the exact failing query decomposes.
# ---------------------------------------------------------------------------
KNOWN_COMPANIES: Dict[str, str] = {
    "nvidia": "NVDA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "tesla": "TSLA",
    "byd": "BYDDY",
    "toyota": "TM",
    "toyota motor": "TM",
    "apple": "AAPL",
    "amazon": "AMZN",
    "nike": "NKE",
    "reliance": "RELIANCE.NS",
    "microsoft": "MSFT",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "intel": "INTC",
    "samsung": "005930.KS",
}

# Well-known private/unlisted companies: no public ticker exists, so they
# must NEVER enter Yahoo symbol search (which would fuzzy-match them onto
# an unrelated listed vehicle, e.g. "OpenAI" -> C3.ai's "AI"). Seed list,
# not exhaustive: the _resolve_symbol() name-overlap check is the general
# backstop for private names missing here. All keys lowercase.
KNOWN_PRIVATE_COMPANIES = frozenset({
    "openai", "open ai",
    "anthropic",
    "xai", "x ai",
    "mistral", "mistral ai",
    "spacex", "space x",
    "stripe",
    "bytedance", "byte dance",
    "databricks",
    "discord",
    "canva",
    "shein",
    "plaid",
    "epic games",
    "valve",
    "anduril",
    "rippling",
    "chime",
    "perplexity",
    "scale ai",
})

# Canonical display names (query-order output of detect_entities).
_DISPLAY_NAMES: Dict[str, str] = {
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "tesla": "Tesla",
    "byd": "BYD",
    "toyota": "Toyota",
    "toyota motor": "Toyota",
    "samsung": "Samsung",
}

# Canonical metric keys required by the failing query.
METRIC_STOCK = "stock_performance"
METRIC_REVENUE = "revenue_growth"
METRIC_PROFIT = "profitability"

_STOCK_RE = re.compile(
    r"\b(stock|share|price|market\s*(performance|cap)?|ticker|nasdaq|nyse|"
    r"price\s*change|returns?)\b",
    re.IGNORECASE,
)
_REVENUE_RE = re.compile(
    r"\b(revenue|revenues|sales|turnover|revenue\s*growth|"
    r"top[-\s]?lines?)\b",
    re.IGNORECASE,
)
_PROFIT_RE = re.compile(
    r"\b(profit\w*|profitability|net\s*income|operating\s*margin|net\s*margin|"
    r"earnings|eps|ebitda|gross\s*margin|bottom[-\s]?lines?)\b",
    re.IGNORECASE,
)

# "last 3 years" / "past three years" / "3-year" / "over 3 years" ...
# Word forms cover one through ten so "last eight/nine years" parses.
_PERIOD_RE = re.compile(
    r"\b(?:last|past|previous|over(?:\s+the)?|trailing)\s+"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]?\s*"
    r"(years?|yrs?|y)\b"
    r"|\b(\d+)\s*[- ]?years?\s*(comparison|history|trend|performance)?\b"
    r"|\b(\d+)\s*y\b",
    re.IGNORECASE,
)
# Explicit calendar ranges: "2022 to 2025", "2022-2025", "from 2022 to 2025",
# "between 2022 and 2025". Relative forms stay in _PERIOD_RE above.
_EXPLICIT_RANGE_RE = re.compile(
    r"\b(?:from\s+)?(19\d{2}|20\d{2})\s*(?:to|through|thru|until|\-|\u2013|\u2014)\s*(19\d{2}|20\d{2})\b"
    r"|\bbetween\s+(19\d{2}|20\d{2})\s+and\s+(19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
# A bare fiscal-year anchor ("FY2023", "fiscal 2023", "fiscal year 2024").
_FISCAL_YEAR_RE = re.compile(
    r"\bF\.?\s*Y\.?\s*(20\d{2})\b|\bfiscal(?:\s+year)?\s+(20\d{2})\b",
    re.IGNORECASE,
)
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_COMPARISON_RE = re.compile(
    r"\b(vs\.?|versus|compare|comparison|contrast|between|"
    r"which\s+(company|one)|stronger|better|best)\b",
    re.IGNORECASE,
)


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


# ---------------------------------------------------------------------------
# Generic entity typing: semantic type resolves BEFORE tool selection.
# Companies / financial instruments may enter market adapters; industries,
# categories, products, geographies, and concepts must not be conflated
# with them. Pure heuristic, no network, no question-specific lists.
# ---------------------------------------------------------------------------
_GEOGRAPHY_TOKENS = frozenset({
    "usa", "america", "united states", "europe", "asia", "china", "india",
    "japan", "germany", "france", "uk", "britain", "canada", "australia",
    "africa", "brazil", "mexico", "russia", "korea", "spain", "italy",
    "netherlands", "sweden", "switzerland", "singapore", "europe",
    "latin america", "middle east", "california", "texas", "london",
    "paris", "tokyo", "beijing",
})
_CATEGORY_CUE_RE = re.compile(
    r"\b(startups?|industr\w*|sectors?|markets?|categor\w*)\b", re.IGNORECASE
)
_TICKER_RE = re.compile(r"^[A-Z]{2,5}(\.[A-Z]{1,3})?$")


_INDUSTRY_CUE_RE = re.compile(
    r"\b(industr\w*|sectors?|verticals?)\b", re.IGNORECASE
)
_PRODUCT_CUE_RE = re.compile(
    r"\b(phone|phones|smartphone|laptop|software|app|apps|device|devices|"
    r"car|cars|vehicle|shoe|shoes|drink|beverage|food|drug|vaccine|chip|chips)\b",
    re.IGNORECASE,
)
_COUNTRY_CUE_RE = re.compile(
    r"\b(countr\w*|nation\s*)\b", re.IGNORECASE
)
_REGION_CUE_RE = re.compile(
    r"\b(region|regions|continent|territor\w*|province|state\s+of)\b",
    re.IGNORECASE,
)
_PRIVATE_CUE_RE = re.compile(
    r"\b(private|startup|startups|llc|incubated|unlisted)\b", re.IGNORECASE
)


def classify_entity_type(entity: str) -> str:
    """Semantic type for one entity string (generic, no question lists).

    Classification happens BEFORE financial-market routing; only financial
    types may enter Yahoo adapters. Returns one of PUBLIC_COMPANY,
    PRIVATE_COMPANY, FINANCIAL_INSTRUMENT, INDUSTRY, CATEGORY, PRODUCT,
    COUNTRY, REGION, GEOGRAPHY, CONCEPT, UNKNOWN. Capitalization alone
    never creates a company: an unrecognized capitalized phrase is UNKNOWN
    (callers treat UNKNOWN as non-financial unless Yahoo symbol search
    positively resolves it at retrieval time).
    """
    name = (entity or "").strip()
    if not name:
        return "UNKNOWN"
    lowered = name.lower()
    if lowered in KNOWN_COMPANIES:
        return "PUBLIC_COMPANY"
    # Explicit private recognition BEFORE the ticker/UNKNOWN fallthrough:
    # a known-unlisted name is PRIVATE_COMPANY (never Yahoo-routable),
    # never UNKNOWN (which would invite a fuzzy symbol search).
    _private_key = re.sub(r"'s$", "", lowered).strip()
    if _private_key in KNOWN_PRIVATE_COMPANIES:
        return "PRIVATE_COMPANY"
    if _TICKER_RE.match(name.strip()) and len(name.strip()) <= 6:
        # Ticker-like ALL-CAPS short tokens are instruments, but a generic
        # category word in caps ("ETF" aside) is not: require Yahoo-style
        # shape AND no category/geo cue.
        if not _CATEGORY_CUE_RE.search(name) and lowered not in _GEOGRAPHY_TOKENS:
            return "FINANCIAL_INSTRUMENT"
    # A named startup ("Acme startup") is a private company, not a generic
    # category. Private companies carry no market series (non-financial).
    if _PRIVATE_CUE_RE.search(name):
        return "PRIVATE_COMPANY"
    if _INDUSTRY_CUE_RE.search(name):
        return "INDUSTRY"
    if _CATEGORY_CUE_RE.search(name):
        return "CATEGORY"
    if _COUNTRY_CUE_RE.search(name) or lowered in _GEOGRAPHY_TOKENS:
        # Bare geography tokens are COUNTRY/GEOGRAPHY, not companies.
        return "COUNTRY" if _COUNTRY_CUE_RE.search(name) else "GEOGRAPHY"
    if _REGION_CUE_RE.search(name):
        return "REGION"
    if lowered in _GEOGRAPHY_TOKENS:
        return "GEOGRAPHY"
    if _PRODUCT_CUE_RE.search(name):
        return "PRODUCT"
    # Lowercase common-noun phrases are concepts, never companies.
    if name == lowered and not _TICKER_RE.match(name.strip()):
        return "CONCEPT"
    # Capitalized but unrecognized: UNKNOWN (not a company by heuristic).
    # Legacy alias "company" is preserved as PUBLIC_COMPANY equivalent by
    # callers via FINANCIAL_ENTITY_TYPES.
    if re.match(r"^[A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,2}$", name):
        return "UNKNOWN"
    return "UNKNOWN"


# Only types with market series may enter Yahoo adapters. PRIVATE_COMPANY
# is typed (semantics preserved) but non-financial: no symbol, no market
# history -- it resolves via snippets/filings, never Yahoo.
FINANCIAL_ENTITY_TYPES = frozenset({
    "PUBLIC_COMPANY", "FINANCIAL_INSTRUMENT",
    "company", "financial_instrument",  # legacy aliases
})


def is_financial_entity(entity: str) -> bool:
    """True only for entity types that may enter market adapters."""
    try:
        return classify_entity_type(entity) in FINANCIAL_ENTITY_TYPES
    except Exception:
        return False


def financial_entities(entities: Sequence[str]) -> List[str]:
    """Subset of entities eligible for market/financial adapters."""
    return [e for e in (entities or []) if is_financial_entity(e)]


def may_attempt_market_resolution(entity: str) -> bool:
    """True when Yahoo symbol search may be attempted for one entity.

    Strictly financial types always; UNKNOWN (capitalized but unrecognized)
    may attempt resolution because the symbol search itself is the ground
    truth -- a positive hit types it, None skips it with an explicit reason
    (never silent, never zero-filled). Known non-financial types (CONCEPT,
    CATEGORY, INDUSTRY, PRODUCT, GEOGRAPHY, COUNTRY, REGION, PRIVATE_COMPANY)
    never enter market adapters.
    """
    try:
        typed = classify_entity_type(entity)
    except Exception:
        return False
    if typed in FINANCIAL_ENTITY_TYPES:
        return True
    return typed == "UNKNOWN"


def market_candidate_entities(entities: Sequence[str]) -> List[str]:
    """Entities that may enter market-adapter resolution (verified, not assumed)."""
    return [e for e in (entities or []) if may_attempt_market_resolution(e)]


def detect_metrics(query: str) -> List[str]:
    """Detect which of the required comparison metrics the query asks for."""
    text = query or ""
    metrics: List[str] = []
    if _STOCK_RE.search(text):
        metrics.append(METRIC_STOCK)
    if _REVENUE_RE.search(text):
        metrics.append(METRIC_REVENUE)
    if _PROFIT_RE.search(text):
        metrics.append(METRIC_PROFIT)
    return metrics


def detect_generic_metric_phrases(query: str) -> List[str]:
    """Raw metric-like phrases for arbitrary metrics (generic fallback).

    Captures the span after "on"/"in terms of"/"by"/"for" up to the next
    scope cue, split on commas/and. Used only to decide whether the user
    already supplied a metric (clarification discipline), never for Yahoo
    routing. Pure.
    """
    text = query or ""
    match = re.search(
        r"\b(?:on|in\s+terms\s+of|by|for|in|regarding|about)\s+(.+)",
        text, re.IGNORECASE,
    )
    if not match:
        return []
    segment = match.group(1)
    cut = re.search(
        r"\b(over|during|from|between|in\s+\d{4}|last|past|trailing|show|which|explain|with|and\s+explain)\b",
        segment, re.IGNORECASE,
    )
    if cut:
        segment = segment[:cut.start()]
    parts = re.split(r"\s*,\s*|\s+and\s+", segment)
    out: List[str] = []
    for part in parts:
        phrase = " ".join(part.strip().split())[:80]
        if phrase and len(phrase) >= 3:
            out.append(phrase)
    return out[:4]


def detect_period_years(query: str) -> Optional[int]:
    """Detect an explicit historical window in years ("last 3 years" -> 3)."""
    parsed = parse_time_range(query or "")
    return parsed.years


@dataclass
class TimeRange:
    """Normalized time-range representation (structural, never a loose string).

    kind is one of "relative_years" ("last 3 years"), "calendar_year_range"
    ("2022 to 2025" / "FY2023"), or "none" (no explicit window detected).
    """

    kind: str = "none"
    years: Optional[int] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "years": self.years,
            "start_year": self.start_year,
            "end_year": self.end_year,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "TimeRange":
        if not isinstance(payload, dict):
            return cls()
        kind = str(payload.get("kind") or "none")
        if kind not in ("relative_years", "calendar_year_range", "none"):
            kind = "none"
        try:
            years = int(payload["years"]) if payload.get("years") is not None else None
        except (TypeError, ValueError):
            years = None
        try:
            start = int(payload["start_year"]) if payload.get("start_year") is not None else None
        except (TypeError, ValueError):
            start = None
        try:
            end = int(payload["end_year"]) if payload.get("end_year") is not None else None
        except (TypeError, ValueError):
            end = None
        return cls(
            kind=kind, years=years, start_year=start, end_year=end,
            label=payload.get("label"),
        )


def parse_time_range(query: str) -> TimeRange:
    """Parse a query's time window into a structural TimeRange.

    Supports relative windows ("last N years", "past N years", "N-year",
    "trailing N years", "3Y") and explicit ranges ("2022 to 2025",
    "2022-2025", "from 2022 to 2025", "between 2022 and 2025", "FY2023" /
    "fiscal 2023"). Explicit ranges win over relative ones when both appear.
    Pure and deterministic -- no LLM, no network.
    """
    text = query or ""
    explicit = _EXPLICIT_RANGE_RE.search(text)
    if explicit:
        groups = [g for g in explicit.groups() if g]
        try:
            start_year, end_year = int(groups[0]), int(groups[1])
        except (IndexError, TypeError, ValueError):
            start_year = end_year = None
        if start_year and end_year:
            if start_year > end_year:
                start_year, end_year = end_year, start_year
            span = end_year - start_year + 1
            return TimeRange(
                kind="calendar_year_range",
                years=span,
                start_year=start_year,
                end_year=end_year,
                label=f"{start_year}-{end_year}",
            )
    match = _PERIOD_RE.search(text)
    if match:
        for group in (match.group(1), match.group(3)):
            if group:
                raw = group.strip().lower()
                years: Optional[int] = None
                if raw.isdigit():
                    years = int(raw)
                elif raw in _WORD_NUM:
                    years = int(_WORD_NUM[raw])
                if years:
                    return TimeRange(
                        kind="relative_years", years=years, label=f"{years}Y"
                    )
        # NOTE: the short "3y/5y/10y" form is NOT match.group(4) (that slot
        # is the optional comparison-word group) -- match it directly so the
        # form actually works (previously dead).
        short_match = re.search(r"\b(\d+)\s*y\b", text, re.IGNORECASE)
        if short_match:
            years = int(short_match.group(1))
            return TimeRange(kind="relative_years", years=years, label=f"{years}Y")
    fiscal = _FISCAL_YEAR_RE.search(text)
    if fiscal:
        year_text = fiscal.group(1) or fiscal.group(2)
        try:
            year = int(year_text)
        except (TypeError, ValueError):
            year = None
        if year:
            return TimeRange(
                kind="calendar_year_range", years=1,
                start_year=year, end_year=year, label=f"FY{year}",
            )
    return TimeRange()


def is_comparison_query(query: str) -> bool:
    return bool(_COMPARISON_RE.search(query or ""))


def symbol_for_entity(entity: str) -> Optional[str]:
    """Canonical Yahoo symbol for a known entity display name (None if unknown)."""
    if not entity:
        return None
    return KNOWN_COMPANIES.get(str(entity).strip().lower())


def _legacy_build_research_plan_removed() -> None:
    """Legacy dict-only build_research_plan was removed (Phase H1).

    The canonical builder is build_research_plan() defined below (ResearchPlan
    dict subclass with required_tools). This stub exists only to document the
    removal: there is exactly ONE definition of "required tools" --
    required_tools_for_query() -- read via ResearchPlan.required_tools.
    """
    raise NotImplementedError("use build_research_plan()")


def is_researchable_comparison(query: str) -> bool:
    """True when the query is a multi-entity comparison whose entities and
    metrics are detected -- i.e. public data the agent must research itself
    rather than ask the user to supply.

    Generic: works for arbitrary entities/metrics, not a fixed list. The
    period is safely defaultable (latest available window), so it is NOT
    required here; historical intent only strengthens the verdict via the
    plan's requires_history flag when present.
    """
    try:
        plan = build_research_plan(query or "")
    except Exception:
        return False
    entities = list(plan.get("entities", []) or [])
    metrics = list(plan.get("metrics", []) or [])
    if not (plan.get("is_comparison") and len(entities) >= 2):
        return False
    if len(metrics) >= 1:
        return True
    # Arbitrary metrics: a generic metric phrase also counts as supplied
    # (never ask the user for a metric they already named).
    try:
        if detect_generic_metric_phrases(query or ""):
            return True
    except Exception:
        pass
    return False


# A clarification that asks the USER to supply researchable company data
# ("Please provide annual revenue / net income / profit margin figures ...").
# Such questions must never be asked for public-company statistics: the
# agent has web/data tools and must use them instead.
_RESEARCHABLE_DATA_REQUEST_RE = re.compile(
    r"\b(provide|supply|share|upload|enter|give|paste|tell\s+me)\b"
    r".{0,80}?\b(annual\s+)?(revenue|net\s*income|profit\s*margin|"
    r"profitability|financials?|figures?)\b"
    r"|\b(annual\s+)?(revenue|net\s*income|profit\s*margin)\s+"
    r"(figures?|numbers?|data)\s+for\b",
    re.IGNORECASE,
)


def clarification_asks_for_researchable_data(
    question: str, query: str
) -> bool:
    """True when a clarification question asks the user to hand over data
    the agent should research itself (and the original query is a
    researchable public-company comparison)."""
    if not question or not is_researchable_comparison(query or ""):
        return False
    return bool(_RESEARCHABLE_DATA_REQUEST_RE.search(question or ""))


def decompose_comparison_query(query: str) -> Dict[str, Any]:
    """Decompose a comparison query into entities / metrics / period.

    Pure and deterministic -- no LLM, no network.
    """
    entities = detect_entities(query or "")
    metrics = detect_metrics(query or "")
    time_range = parse_time_range(query or "")
    years = time_range.years
    return {
        "entities": entities,
        "metrics": metrics,
        "period_years": years,
        "period_label": time_range.label or (f"{years}Y" if years else None),
        "time_range": time_range.to_dict(),
        "is_comparison": is_comparison_query(query or "")
        or (len(entities) >= 2 and len(metrics) >= 1),
        "requires_history": years is not None and years >= 2,
    }


# ---------------------------------------------------------------------------
# Currency normalization: "currency" is never a usable unit for comparison.
# Every financial figure must resolve to an explicit ISO code (USD, CNY, JPY,
# EUR, INR, GBP, ...) or stay explicitly unknown (None). Unknown is honest;
# generic is not.
# ---------------------------------------------------------------------------
_CURRENCY_SYMBOL_MAP: Dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "\u20ac": "EUR",
    "EUR": "EUR",
    "\u20b9": "INR",
    "INR": "INR",
    "\u00a3": "GBP",
    "GBP": "GBP",
    "\u00a5": "JPY",  # bare yen sign defaults to JPY; yuan words override to CNY
    "CN\u00a5": "CNY",
    "CNY": "CNY",
    "RMB": "CNY",
    "JPY": "JPY",
}

_CURRENCY_WORD_MAP: Dict[str, str] = {
    "dollar": "USD", "dollars": "USD",
    "euro": "EUR", "euros": "EUR",
    "rupee": "INR", "rupees": "INR",
    "pound": "GBP", "pounds": "GBP",
    "yen": "JPY",
    "yuan": "CNY",
}

_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def normalize_currency(
    symbol: Optional[str] = None, word: Optional[str] = None
) -> Optional[str]:
    """Resolve a currency symbol/word to an explicit ISO code (None = unknown).

    Yuan-family words ("yuan", "CNY", "RMB", "CN¥") always win over a bare
    "¥" sign, which otherwise defaults to JPY. Never guesses beyond this
    table: anything unrecognized stays None (explicitly unknown).
    """
    word_code: Optional[str] = None
    if word:
        word_code = _CURRENCY_WORD_MAP.get(str(word).strip().lower())
        if word_code is not None:
            return word_code
        upper = str(word).strip().upper()
        if upper in _CURRENCY_SYMBOL_MAP:
            return _CURRENCY_SYMBOL_MAP[upper]
    if symbol:
        token = str(symbol).strip()
        if token in _CURRENCY_SYMBOL_MAP:
            return _CURRENCY_SYMBOL_MAP[token]
        upper = token.upper()
        if upper in _CURRENCY_SYMBOL_MAP:
            return _CURRENCY_SYMBOL_MAP[upper]
    return None


def evidence_currency(item: "ComparisonEvidence") -> Optional[str]:
    """Explicit ISO currency for an evidence item (None = unknown).

    Derives from the `currency` field first, then from an ISO-looking `unit`
    ("USD" as a unit means USD). A generic unit ("currency", "money",
    "price") yields None -- explicitly unknown, never a silent default.
    """
    code = (getattr(item, "currency", None) or "").strip().upper()
    if _ISO_CURRENCY_RE.match(code or ""):
        return code
    unit = (getattr(item, "unit", None) or "").strip().upper()
    if _ISO_CURRENCY_RE.match(unit or ""):
        return unit
    return None


def currencies_compatible(first: Optional[str], second: Optional[str]) -> bool:
    """True when two currencies may appear in one comparison.

    Same code always passes; either side unknown passes with an assumption
    note (callers must record it). Only two KNOWN-DIFFERENT codes fail --
    CNY 100B next to USD 100B must never compare silently.
    """
    if not first or not second:
        return True
    return str(first).strip().upper() == str(second).strip().upper()


# ---------------------------------------------------------------------------
# Canonical research plan: the ONE source of truth for research execution.
# ---------------------------------------------------------------------------
_MACRO_INTENT_RE = re.compile(
    r"\b(inflation|cpi|consumer\s*price|interest\s*rate|fed\s*funds?|"
    r"federal\s*funds|unemployment|jobless|jobs\s*report|gdp|"
    r"recession|treasury\s*yield|mortgage\s*rate)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_WHO_WHAT_RE = re.compile(
    r"\b(who\s+is|who\s+are|what\s+is|what\s+are|tell\s+me\s+about|"
    r"explain|overview\s+of)\b",
    re.IGNORECASE,
)

# Deterministic tool requirements per evidence kind. The LLM planner may
# RECOMMEND tools but can never remove these: resolve_tool_plan() unions
# them back in. Tool names match the TOOL_CATALOG keys in langchain_pipeline.
HISTORY_TOOLS = ("market_history", "financial_history")
SNAPSHOT_TOOLS = ("market", "fundamentals")
_TOOL_CATALOG_KEYS = frozenset({
    "snippets", "market", "fundamentals", "market_history",
    "financial_history", "wikipedia", "macro", "extract",
})


def required_tools_for_query(
    query: str, decomposed: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Deterministic tool requirements for a query (no LLM, no network).

    This is the MINIMAL correctness set -- tools without which the answer
    would be wrong or missing -- not a maximal wish list. The LLM planner
    stays advisory for everything else (snippets on simple asks, wikipedia,
    macro, extract): it may ADD tools via resolve_tool_plan(), but it can
    never remove these:

    - Historical stock intent -> market_history (never the 1-month snapshot).
    - Historical revenue/profitability intent -> financial_history.
    - Non-historical stock intent -> market; scale/valuation intent ->
      fundamentals.
    - Named multi-entity historical comparisons ALWAYS need market_history +
      financial_history + fundamentals (current scale context) + snippets
      (the explanatory "why"), for EVERY named company.
    """
    text = query or ""
    decomposed = decomposed or decompose_comparison_query(text)
    entities = list(decomposed.get("entities", []) or [])
    metrics = list(decomposed.get("metrics", []) or [])
    requires_history = bool(decomposed.get("requires_history"))
    is_comp = bool(decomposed.get("is_comparison"))
    required: List[str] = []

    def _add(tool: str) -> None:
        if tool not in required:
            required.append(tool)

    # Entity typing gate: financial entities plus UNKNOWN names (whose
    # Yahoo symbol search is the ground-truth typing step) may require
    # market adapters. Known non-financial entities (geographies, concepts,
    # categories, products, private companies) resolve via
    # snippets/wikipedia/macro only.
    try:
        fin_entities = market_candidate_entities(entities)
    except Exception:
        fin_entities = financial_entities(entities)
    has_financial = bool(fin_entities)
    needs_stock = METRIC_STOCK in metrics
    needs_financial = METRIC_REVENUE in metrics or METRIC_PROFIT in metrics
    if is_comp and len(entities) >= 2 and requires_history:
        # Canonical multi-entity historical comparison requirement.
        # History tools only when at least one financial entity exists;
        # otherwise snippets carry the comparison (never Yahoo for concepts).
        if has_financial and (needs_stock or not metrics):
            _add("market_history")
        if has_financial and (needs_financial or not metrics):
            _add("financial_history")
        if has_financial:
            _add("fundamentals")
        _add("snippets")
    else:
        if needs_stock and has_financial:
            _add("market_history" if requires_history else "market")
        elif needs_stock and not has_financial:
            _add("snippets")
        if needs_financial and has_financial:
            _add("financial_history" if requires_history else "fundamentals")
        elif needs_financial and not has_financial:
            _add("snippets")
        if entities and not (needs_stock or needs_financial) and not is_comp:
            if has_financial:
                _add("fundamentals")
            else:
                _add("snippets")
        if is_comp and len(entities) >= 2:
            _add("snippets")
    return required


def resolve_tool_plan(llm_plan: Any, query: str) -> List[str]:
    """The ONE authoritative tool plan for research execution.

    Union of the deterministic requirements (which always win) plus any
    valid extra tools the LLM recommended. The LLM may ADD tools; it must
    NEVER silently remove a deterministically required one (e.g. proposing
    only ["market_history"] for a query that also needs financial_history).
    An empty/invalid LLM plan means "no opinion" and resolves to [] so the
    caller falls back to the deterministic intent predicates -- unknown
    names are dropped, never executed. Pure -- no LLM, no network.
    """
    if not isinstance(llm_plan, list) or not llm_plan:
        return []
    required = required_tools_for_query(query or "")
    extras: List[str] = []
    for item in llm_plan:
        name = str(item or "").strip().lower()
        if name in _TOOL_CATALOG_KEYS and name not in required and name not in extras:
            extras.append(name)
    return required + extras


class ResearchPlan(dict):
    """Canonical internal research plan: the single source of truth that
    research execution, gating, confidence, and follow-ups all read from.

    A dict subclass (not a second competing model): all legacy dict access
    (plan["entities"], plan.get("tasks"), JSON serialization) keeps working,
    while typed attribute access (plan.entities, plan.required_tools, ...)
    is available for new code. Built only by build_research_plan(), which
    reuses decompose_comparison_query() -- never construct a competing
    planner, extend this one.
    """

    query: str = ""
    entities: List[str] = []
    metrics: List[str] = []
    source_scope: str = "live_web"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Typed attribute views over the same canonical mapping.
        self.query = str(self.get("query", "") or "")
        self.entities = list(self.get("entities", []) or [])
        self.metrics = list(self.get("metrics", []) or [])
        self.source_scope = str(self.get("source_scope", "live_web") or "live_web")

    @property
    def time_range(self) -> TimeRange:
        return TimeRange.from_dict(self.get("time_range"))

    @property
    def comparison_requested(self) -> bool:
        return bool(self.get("comparison_requested", self.get("is_comparison", False)))

    @property
    def requires_history(self) -> bool:
        return bool(self.get("requires_history", False))

    @property
    def required_tools(self) -> List[str]:
        return list(self.get("required_tools", []) or [])

    @property
    def research_tasks(self) -> List[Dict[str, Any]]:
        tasks = self.get("research_tasks", self.get("tasks", []))
        return list(tasks or [])

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


def build_research_plan(query: str, source_scope: str = "live_web") -> ResearchPlan:
    """Canonical plan builder (single source of truth for research execution).

    Returns a ResearchPlan (a dict subclass): legacy dict access keeps
    working and typed attribute access is available for new code.
    """
    return _build_research_plan(query or "", source_scope=source_scope)


def _build_research_plan(query: str, source_scope: str = "live_web") -> ResearchPlan:
    """Deterministic internal research plan for a comparison query.

    Decomposes the request into entities / metrics / period plus one task
    per entity per evidence kind, so the research stage can verify
    entity-completeness (never silently continue with 2 of 3 companies)
    and metric-completeness (stock history alone never satisfies revenue
    or profitability). Pure -- no LLM, no network.
    """
    decomposed = decompose_comparison_query(query or "")
    entities = list(decomposed.get("entities", []) or [])
    metrics = list(decomposed.get("metrics", []) or [])
    time_range = parse_time_range(query or "")
    years = time_range.years
    tasks: List[Dict[str, Any]] = []
    for entity in entities:
        symbol = symbol_for_entity(entity)
        if METRIC_STOCK in metrics:
            tasks.append({
                "id": f"stock:{entity}", "kind": "stock_history",
                "entity": entity, "symbol": symbol, "years": years,
            })
        if METRIC_REVENUE in metrics:
            tasks.append({
                "id": f"revenue:{entity}", "kind": "financial_history",
                "entity": entity, "symbol": symbol, "years": years,
                "metric": "annualTotalRevenue",
            })
        if METRIC_PROFIT in metrics:
            tasks.append({
                "id": f"profit:{entity}", "kind": "financial_history",
                "entity": entity, "symbol": symbol, "years": years,
                "metric": "annualNetIncome",
            })
    if metrics:
        tasks.append({
            "id": "explain", "kind": "web_explanation",
            "entities": entities, "metrics": metrics,
        })
    tasks.append({"id": "normalize", "kind": "normalize"})
    tasks.append({"id": "validate", "kind": "validate"})
    tasks.append({"id": "calculate", "kind": "calculate"})
    tasks.append({"id": "answer", "kind": "answer"})
    return ResearchPlan({
        "query": query,
        "entities": entities,
        "metrics": metrics,
        "time_range": time_range.to_dict(),
        "period_years": years,
        "period_label": time_range.label or (f"{years}Y" if years else None),
        "is_comparison": bool(decomposed.get("is_comparison", False)),
        "comparison_requested": bool(decomposed.get("is_comparison", False)),
        "requires_history": bool(decomposed.get("requires_history", False)),
        "required_entities": entities,
        "required_metrics": metrics,
        "required_tools": required_tools_for_query(query, decomposed),
        "tasks": tasks,
        "research_tasks": tasks,
        "source_scope": source_scope,
    })


def plan_to_dict(plan: Any) -> Dict[str, Any]:
    """Coerce a ResearchPlan (or legacy dict) to the canonical dict shape."""
    if isinstance(plan, ResearchPlan):
        return plan.to_dict()
    if isinstance(plan, dict):
        return dict(plan)
    return {}


def reconcile_judge_tools(
    judge_tools: Any, evidence: Dict[str, Any]
) -> Dict[str, Any]:
    """Post-hoc role of Decision.tools_needed (it arrives AFTER research).

    The judge's tool list cannot drive dispatch (research already ran), so
    its only honest use is a completeness check: which requested tools have
    no evidence on hand. Returns {"missing": [...], "note": str}. Callers
    must cap confidence / state assumptions when "missing" is non-empty --
    never silently ignore it (that was the dead-field bug).
    """
    wanted = [
        str(item or "").strip().lower() for item in (judge_tools or [])
        if str(item or "").strip()
    ]
    evidence = evidence or {}
    has_map = {
        "snippets": bool(evidence.get("web_snippet_count")),
        "market": bool(evidence.get("market_entities")),
        "market_history": bool(evidence.get("price_history_entities")),
        "financial_history": bool(evidence.get("financial_history_entities")),
        "fundamentals": bool(evidence.get("fundamentals_entities")),
        "wikipedia": bool(evidence.get("web_snippet_count")),
        "macro": bool(evidence.get("macro_entities")),
        "extract": bool(evidence.get("web_snippet_count")),
    }
    missing = [tool for tool in wanted if not has_map.get(tool, True)]
    note = (
        f"Judge-requested tools lacking evidence: {missing}."
        if missing else ""
    )
    return {"missing": missing, "note": note}


def merge_clarification_context(
    original_query: str,
    prior_clarification: Optional[str],
    followup_query: str,
) -> str:
    """Merge a clarification reply into the ORIGINAL research plan context.

    A clarification answer ("revenue", "Tesla") is a fragment, not a
    standalone query: decomposing it alone loses the original entities and
    period. Returns the combined text to decompose (original query first so
    its entities/metrics/period win on conflicts, then the user's answer).
    Pure -- no LLM, no network.
    """
    try:
        from app.services.data.canonical import build_canonical_query as _build
        return _build(original_query or "", prior_clarification, followup_query or "")
    except Exception:
        pass
    original = (original_query or "").strip()
    followup = (followup_query or "").strip()
    if not original:
        return followup
    if not followup:
        return original
    # The follow-up refines the original; keep both so entity/metric/period
    # detection sees the full intent instead of a bare fragment.
    return f"{original} [clarification answer: {followup}]"


def must_not_clarify(query: str) -> bool:
    """Deterministic clarification ban for researchable public comparisons.

    True when the query names >=2 entities plus >=1 metric (canonical or
    generic phrase) -- the data is theoretically retrievable, so asking the
    user to supply it is forbidden. The period is safely defaultable and
    never justifies a clarification on its own. Genuine ambiguity
    ("Compare Apple and Samsung" with no metric) returns False and may
    still clarify.
    """
    return is_researchable_comparison(query or "")


# ---------------------------------------------------------------------------
# Generic clarification discipline (arbitrary entities / metrics / timeframes
# / geographies / currencies / source preferences).
# ---------------------------------------------------------------------------
_METRIC_WORD_RE = re.compile(
    r"\b(revenue|revenues|sales|profit\w*|margin|margins|earnings|stock|"
    r"share|price|market|growth|headcount|churn|units?|users?|customers?|"
    r"orders?|returns?|top[-\s]?line|bottom[-\s]?line)\b",
    re.IGNORECASE,
)
_TIMEFRAME_WORD_RE = re.compile(
    r"\b(last|past|previous|trailing|over|years?|yrs?|months?|weeks?|"
    r"quarters?|20\d{2}|FY\s?20\d{2}|fiscal)\b",
    re.IGNORECASE,
)
_GEO_WORD_RE = re.compile(
    r"\b(usa|america|europe|asia|china|india|japan|germany|france|uk|"
    r"region|regions|country|countr\w*|geograph\w*|global|local|us|eu)\b",
    re.IGNORECASE,
)
_CURRENCY_WORD_RE_CLAR = re.compile(
    r"\b(usd|eur|cny|jpy|inr|gbp|currency|currencies|dollars?|yuan|yen|"
    r"euros?|rupees?|pounds?)\b",
    re.IGNORECASE,
)
_SOURCE_WORD_RE = re.compile(
    r"\b(source|sources|provider|yahoo|tavily|fred|wikipedia|web|live)\b",
    re.IGNORECASE,
)


def clarification_is_redundant(question: str, query: str) -> Tuple[bool, str]:
    """True when a clarification asks for info already in the query.

    Generic across entities, metrics, timeframes, geographies, currencies,
    and source preferences. Timeframe / geography / currency / source are
    safely defaultable and must never trigger a clarification when
    entities+metrics are present. Pure.
    """
    q = (question or "").strip()
    orig = (query or "").strip()
    if not q or not orig:
        return False, ""
    ql, ol = q.lower(), orig.lower()
    # Entities already supplied: question names an entity span already present.
    try:
        for entity in detect_entities(orig):
            if entity and entity.lower() in ql and entity.lower() in ol:
                # Asking "which company" when companies are named is redundant.
                if re.search(r"\b(which|what)\b.*\b(compan\w*|entit\w*|firm\w*)\b", ql):
                    return True, f"entities already supplied ({entity})"
    except Exception:
        pass
    # Metrics already supplied: canonical metric present. A bare generic
    # phrase ("by users") does NOT make "which user metric?" redundant --
    # that clarification disambiguates sub-metrics (total vs active) and
    # must be allowed. Only a canonical metric (revenue/stock/profit) plus
    # a question asking for that same dimension is redundant.
    try:
        supplied = set(detect_metrics(orig))
        if supplied and (
            _METRIC_WORD_RE.search(q)
            or re.search(r"\b(metric|measure|kpi|dimension)\b", ql)
        ):
            # Question asks for a metric dimension already named.
            for token in re.findall(r"[a-z\-]+", ol):
                if token in ql and _METRIC_WORD_RE.search(token):
                    return True, f"metric already supplied ({token})"
            if re.search(r"\b(metric|measure|kpi|dimension)\b", ql):
                # Only when the query names a canonical metric does a bare
                # "which metric?" repeat it. Generic-only queries may still
                # need disambiguation.
                return True, "metric already supplied"
    except Exception:
        pass
    # Safely defaultable dimensions never justify clarification when the
    # core comparison (entities+metrics or entities+intent) is present.
    try:
        has_core = len(detect_entities(orig)) >= 1
        if has_core:
            if _TIMEFRAME_WORD_RE.search(q) and parse_time_range(orig).years is not None:
                return True, "timeframe already supplied"
            if _TIMEFRAME_WORD_RE.search(q) and (
                detect_metrics(orig) or detect_generic_metric_phrases(orig)
            ):
                return True, "timeframe safely defaultable"
            if _GEO_WORD_RE.search(q):
                return True, "geography safely defaultable"
            if _CURRENCY_WORD_RE_CLAR.search(q):
                return True, "currency safely defaultable"
            if _SOURCE_WORD_RE.search(q):
                return True, "source safely defaultable"
    except Exception:
        pass
    return False, ""


def should_clarify_generic(
    query: str,
    *,
    entities: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[str]] = None,
    evidence_sufficient: bool = False,
    prior_clarification: Optional[str] = None,
    proposed_question: Optional[str] = None,
) -> Tuple[bool, str]:
    """Generic clarification gate: only when missing blocks execution.

    Returns (allowed, reason). Clarification is forbidden when evidence is
    sufficient for (partial) execution, when the proposed question repeats
    prior clarification, or when it asks for already-supplied / defaultable
    info. Pure.
    """
    if evidence_sufficient:
        return False, "evidence sufficient for execution"
    if prior_clarification and proposed_question:
        try:
            import difflib as _difflib

            left = "".join(c for c in prior_clarification.lower() if c.isalnum())
            right = "".join(c for c in proposed_question.lower() if c.isalnum())
            if left and right and (
                left == right
                or _difflib.SequenceMatcher(None, left, right).ratio() >= 0.82
            ):
                return False, "repeat clarification forbidden"
        except Exception:
            pass
    if proposed_question and query:
        try:
            redundant, why = clarification_is_redundant(proposed_question, query)
            if redundant:
                return False, why
        except Exception:
            pass
    # No entities at all and no clear intent blocks meaningful execution.
    try:
        ents = list(entities) if entities is not None else detect_entities(query or "")
    except Exception:
        ents = []
    if not ents:
        return True, "no entities detected"
    return True, "missing blocks execution"


# ---------------------------------------------------------------------------
# Evidence model
# ---------------------------------------------------------------------------
@dataclass
class ComparisonEvidence:
    entity: str
    metric: str  # one of METRIC_* or a generic label
    value: float
    unit: str  # e.g. "USD", "USD_millions", "percent", "ratio"
    period_start: Optional[str] = None  # ISO date or label
    period_end: Optional[str] = None
    frequency: Optional[str] = None  # "daily" | "weekly" | "annual" | ...
    definition: Optional[str] = None  # e.g. "close", "annualTotalRevenue"
    geography: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    is_historical: bool = False
    # --- semantic binding (Phases 4/11): every numerical evidence item meant
    # for comparison should carry these. currency is an explicit ISO code
    # ("USD", "CNY", "JPY", ...) or None (explicitly unknown -- never the
    # generic string "currency"). scale is the magnitude multiplier the value
    # is expressed in (1 = single units). population/scope is the measured
    # cohort ("all stores", "US retail"). source_type is one of
    # "yahoo" | "fred" | "snippet" | "snapshot" | "web_research".
    # provenance is the free-text origin trail. comparison_eligible False
    # means "untyped / unattributed -- must never enter numerical comparison".
    currency: Optional[str] = None
    scale: Optional[float] = None
    population: Optional[str] = None
    source_type: Optional[str] = None
    provenance: Optional[str] = None
    comparison_eligible: bool = True


@dataclass
class TypedFigure:
    """A snippet figure with semantic binding (Phase 4/5, hardened H2).

    Raw extraction preserves the verbatim figure; semantic fields are filled
    by context analysis. comparison_eligible is False unless the FULL
    H2 contract holds (entity + specific metric + numeric value + unit +
    explicit ISO currency for money + definition + source_type) -- untyped
    "$202B" must never enter numerical comparison. The raw figure is always
    preserved for prose/context; eligibility only gates math/tables/bars.
    """

    text: str
    value: float
    unit: str  # "money" | "percent"
    context: str
    ref: int
    entity: Optional[str] = None
    metric: Optional[str] = None
    currency: Optional[str] = None
    scale: Optional[float] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    frequency: Optional[str] = None
    definition: Optional[str] = None
    geography: Optional[str] = None
    population: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    source_type: str = "snippet"
    comparison_eligible: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text, "value": self.value, "unit": self.unit,
            "context": self.context, "ref": self.ref,
            "entity": self.entity, "metric": self.metric,
            "currency": self.currency, "scale": self.scale,
            "period_start": self.period_start, "period_end": self.period_end,
            "frequency": self.frequency, "definition": self.definition,
            "geography": self.geography, "population": self.population,
            "source": self.source, "source_url": self.source_url,
            "source_type": self.source_type,
            "comparison_eligible": self.comparison_eligible,
        }

    def to_evidence(self, default_metric: str = "") -> Optional[ComparisonEvidence]:
        """Convert to ComparisonEvidence; None when not comparison-eligible.

        Hardened (H2/H4): re-validates the FULL eligibility contract here
        (not just the stored flag) so a hand-constructed eligible flag with
        generic metric / missing currency can never slip into math.
        """
        if not self.comparison_eligible:
            return None
        try:
            eligible, _ = is_figure_comparison_eligible(self)
        except Exception:
            eligible = self.comparison_eligible
        if not eligible:
            return None
        return ComparisonEvidence(
            entity=self.entity or "unknown",
            metric=self.metric or default_metric or "unknown",
            value=self.value,
            unit=self.currency or self.unit,
            period_start=self.period_start,
            period_end=self.period_end,
            frequency=self.frequency,
            definition=self.definition or self.metric,
            geography=self.geography,
            source=self.source,
            source_url=self.source_url,
            is_historical=bool(self.period_start and self.period_end),
            currency=self.currency,
            scale=self.scale,
            population=self.population,
            source_type=self.source_type,
            provenance=f"snippet [{self.ref}]: {self.context[:120]}",
            comparison_eligible=True,
        )


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    # Fiscal labels ("FY2023", "fiscal 2023", "FY 2022") carry a real year:
    # map to Jan 1 of that year so fiscal-year evidence stays comparable
    # (previously these parsed as None -> "unparseable time periods" and
    # blocked every valid fiscal-year comparison).
    fiscal = _FISCAL_YEAR_RE.search(text)
    if fiscal:
        try:
            return date(int(fiscal.group(1) or fiscal.group(2)), 1, 1)
        except (TypeError, ValueError):
            pass
    bare_year = re.fullmatch(r"(19\d{2}|20\d{2})", text)
    if bare_year:
        try:
            return date(int(bare_year.group(1)), 1, 1)
        except (TypeError, ValueError):
            pass
    # Try ISO first, then common Yahoo label formats.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%b %d", "%b %d, %Y", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text[: len(fmt)] if fmt.startswith("%b") else text[:10], fmt)
            if fmt == "%b %d":
                # Month-day labels from the 1-month series carry no year:
                # treat as current-year dates (span stays ~30 days).
                return parsed.replace(year=date.today().year).date()
            return parsed.date()
        except (ValueError, OverflowError):
            continue
    try:
        return datetime.fromisoformat(text[:10]).date()
    except (ValueError, TypeError):
        return None


def _span_days(start: Any, end: Any) -> Optional[int]:
    s, e = _parse_date(start), _parse_date(end)
    if s is None or e is None:
        return None
    return abs((e - s).days)


def series_span_days(labels: Sequence[Any]) -> Optional[int]:
    """Day span covered by a chart label axis (None when unparseable)."""
    parsed = [_parse_date(label) for label in (labels or [])]
    parsed = [d for d in parsed if d is not None]
    if len(parsed) < 2:
        return None
    return abs((max(parsed) - min(parsed)).days)


def _distinct_years(labels: Optional[Sequence[Any]]) -> int:
    """Distinct calendar years across labels (annual observation coverage)."""
    years: set[int] = set()
    for label in list(labels or []):
        parsed = _parse_date(label)
        if parsed is not None:
            years.add(parsed.year)
    return len(years)


def validate_historical_coverage(
    *,
    labels: Optional[Sequence[Any]] = None,
    period_start: Any = None,
    period_end: Any = None,
    requested_years: Optional[int] = None,
    values: Optional[Sequence[Any]] = None,
    frequency: Optional[str] = None,
) -> Tuple[bool, str]:
    """True only when TEMPORAL SPAN *and* OBSERVATION COVERAGE satisfy the ask.

    Span alone never suffices: two snapshots ~3 years apart are a ~3-year
    span with only 2 observations, not a multi-year series. For annual
    requests the series must carry distinct calendar-year observations
    (min(requested_years, 3) distinct years for requests >= 2Y); sparse
    snapshots are reported as snapshots, never as complete series. Fiscal
    vs calendar and annual vs monthly mixes are flagged by callers via
    frequency/definition checks; here a stated monthly frequency can never
    satisfy a multi-year annual request with only 1-2 points.
    """
    if not requested_years:
        return True, "no historical window requested"
    if int(requested_years) < 2:
        # Sub-2Y asks need only a dated span; observation rule applies >= 2Y.
        required_days = int(int(requested_years) * 365 * 0.8)
        span: Optional[int] = None
        if labels:
            span = series_span_days(labels)
        if span is None and (period_start or period_end):
            span = _span_days(period_start, period_end)
        if span is None:
            return False, (
                f"insufficient evidence: no dated coverage for a "
                f"{requested_years}-year request"
            )
        if span < required_days:
            return False, (
                f"insufficient evidence: data spans ~{span} days, "
                f"need ~{int(requested_years) * 365} days ({requested_years} years)"
            )
        return True, f"covers ~{span} days for a {requested_years}-year request"
    try:
        n_values = len(list(values or [])) if values is not None else 0
    except Exception:
        n_values = 0
    n_labels = len(list(labels or [])) if labels else 0
    n_obs = min(n_values or n_labels, n_labels or n_values) if (n_values and n_labels) else (n_values or n_labels)
    distinct = _distinct_years(labels) if labels else 0
    required_obs = min(int(requested_years), 3)
    freq = str(frequency or "").strip().lower()
    sub_annual = freq in ("monthly", "month", "weekly", "week", "daily", "day")
    if sub_annual:
        # Sub-annual frequency needs proportionally more points, not 2.
        required_obs = max(required_obs, min(int(requested_years) * 2, 6))
    annual_series_ok = bool(labels and not sub_annual and distinct >= required_obs)
    # Annual observations cover calendar years: N annual points span N-1
    # elapsed years but represent N periods (2022+2023+2024 answers "3Y").
    # Two snapshots spanning enough days are still a snapshot pair, never
    # a series -- the observation rule below blocks them regardless.
    if annual_series_ok:
        required_days = int(max(1, requested_years - 1) * 365 * 0.7)
    else:
        required_days = int(requested_years * 365 * 0.7)
    span = None
    if labels:
        span = series_span_days(labels)
    if span is None and (period_start or period_end):
        span = _span_days(period_start, period_end)
    if span is None:
        # No dates at all -> current snapshot, never historical coverage.
        if values is not None and len(list(values or [])) >= 1 and not labels and not period_start and not period_end:
            return False, (
                "current snapshot has no historical dates; "
                f"cannot satisfy a {requested_years}-year request"
            )
        return False, (
            f"insufficient evidence: no dated coverage for a "
            f"{requested_years}-year request"
        )
    if span < required_days:
        return False, (
            f"insufficient evidence: data spans ~{span} days, "
            f"need ~{requested_years * 365} days ({requested_years} years)"
        )
    # Observation coverage: distinct dated observations must represent the
    # requested periods. Two endpoints spanning enough days are still a
    # snapshot pair, not a series.
    if labels and distinct < required_obs:
        return False, (
            f"insufficient evidence: only {distinct} distinct annual "
            f"observation(s) for a {requested_years}-year request "
            f"(need {required_obs}); sparse snapshots cannot satisfy history"
        )
    if n_obs and n_obs < 2:
        return False, (
            f"insufficient evidence: only {n_obs} observation(s) for a "
            f"{requested_years}-year request"
        )
    return True, f"covers ~{span} days with {distinct or n_obs} observation(s) for a {requested_years}-year request"


def validate_comparison(
    evidence: Sequence[ComparisonEvidence],
    *,
    expected_entities: Optional[Sequence[str]] = None,
    expected_metric: Optional[str] = None,
    fx_rates: Optional[Dict[str, float]] = None,
    enforce_currency: bool = True,
) -> Tuple[bool, str]:
    """Validate that a comparison is like-for-like before visualizing.

    Checks: >=2 distinct entities, same metric, same unit, same frequency,
    same definition, overlapping/same period, compatible currencies (same
    explicit code, or at least one side unknown, or an explicit deterministic
    FX conversion supplied), same geography/population when stated, and same
    historical/current status. Anything else (total funding vs startup
    cost, current vs historical, USD vs JPY without FX, one company's metric
    vs another company's unrelated metric, monthly vs 3-year) fails and must
    block the chart -- never label a mismatched visual as a comparison.

    enforce_currency=False skips ONLY the currency check, for the
    currency-invariant growth path (per-entity pct_change and net margins
    are unitless and valid across reporting currencies). Absolute-value
    outputs must always enforce it.
    """
    items = [item for item in (evidence or []) if getattr(item, "comparison_eligible", True)]
    if len(items) < 2:
        return False, "insufficient evidence: need both companies, got fewer than 2 series"
    entities = {str(item.entity).strip().lower() for item in items if str(item.entity).strip()}
    if expected_entities:
        want = {str(e).strip().lower() for e in expected_entities if str(e).strip()}
        missing = want - entities
        if missing:
            return False, (
                f"insufficient evidence: missing entity {sorted(missing)}; "
                f"have {sorted(entities)}"
            )
    if len(entities) < 2:
        return False, "insufficient evidence: only one entity series for a two-company comparison"
    metrics = {str(item.metric).strip().lower() for item in items}
    if len(metrics) != 1:
        return False, f"definition mismatch: mixed metrics {sorted(metrics)}"
    if expected_metric and next(iter(metrics)) != str(expected_metric).strip().lower():
        return False, (
            f"metric mismatch: need {expected_metric}, have {sorted(metrics)}"
        )
    units = {str(item.unit).strip().lower() for item in items}
    # H5: explicit ISO units (USD/JPY/...) must agree for ABSOLUTE reads;
    # the legacy generic "currency"/"money" unit is a wildcard that defers
    # to the currency check below (unknown never blocks growth math, only
    # absolute reads with two KNOWN-different codes fail there). On the
    # currency-invariant growth path (enforce_currency=False) different
    # ISO codes are EXPECTED (USD vs CNY vs JPY growth % still compares)
    # and must not block here either.
    _GENERIC_UNITS = {"currency", "money", "currency:unknown"}
    _specific_units = {u for u in units if u not in _GENERIC_UNITS}
    _all_specific_are_iso = bool(_specific_units) and all(
        bool(_ISO_CURRENCY_RE.match(u.upper())) for u in _specific_units
    )
    if len(_specific_units) > 1 and not (not enforce_currency and _all_specific_are_iso):
        return False, f"unit mismatch: {sorted(units)}"
    if len(units) != 1 and len(_specific_units) == 1 and len(units) > 1:
        # Mixed generic + one specific code (e.g. fixture "currency" next
        # to live "usd"): allow here; the currency check decides absolutes.
        pass
    elif len(units) != 1 and not (not enforce_currency and _all_specific_are_iso):
        return False, f"unit mismatch: {sorted(units)}"
    # Currency: explicit codes must agree unless a deterministic FX
    # conversion is supplied. Unknown (None) on either side passes with an
    # assumption (callers record it) -- but never silently equates CNY/JPY.
    # Skipped only for the currency-invariant growth path (see docstring).
    fx_rates = fx_rates or {}
    known_currencies = {evidence_currency(item) for item in items}
    known_currencies.discard(None)
    if enforce_currency and len(known_currencies) > 1:
        codes = sorted(known_currencies)
        convertible = all(
            str(code).upper() in {str(k).upper() for k in fx_rates}
            or str(code).upper() == codes[0]
            for code in codes
        )
        if not convertible:
            return False, (
                f"currency mismatch: {codes} with no explicit FX conversion; "
                "absolute values in different currencies must not compare"
            )
    freqs = {str(item.frequency or '').strip().lower() for item in items}
    freqs.discard("")
    if len(freqs) > 1:
        return False, f"frequency mismatch: {sorted(freqs)}"
    defs = {str(item.definition or '').strip().lower() for item in items}
    defs.discard("")
    if len(defs) > 1:
        return False, f"definition mismatch: {sorted(defs)}"
    # Aggregation semantics: total vs average vs median vs snapshot are
    # different statistics and must never compare as like-for-like.
    stats = {
        str(
            getattr(item, "statistic", getattr(item, "aggregation", None)) or ""
        ).strip().lower()
        for item in items
    }
    stats.discard("")
    stats.discard("unknown")
    if len(stats) > 1:
        return False, f"aggregation mismatch: {sorted(stats)} (total/average/median must not compare)"
    # Geography / population-scope: stated values must agree across sides.
    geos = {str(item.geography or '').strip().lower() for item in items}
    geos.discard("")
    if len(geos) > 1:
        return False, f"geography mismatch: {sorted(geos)}"
    pops = {
        str(getattr(item, "population", None) or '').strip().lower()
        for item in items
    }
    pops.discard("")
    if len(pops) > 1:
        return False, f"population/scope mismatch: {sorted(pops)}"
    # Historical/current status: a current snapshot next to dated history is
    # never like-for-like (the exact "snapshot used as historical growth" bug).
    statuses = {bool(item.is_historical) for item in items}
    if len(statuses) > 1:
        return False, (
            "historical/current status mismatch: current snapshot values must "
            "not compare against historical series"
        )
    # Period overlap: both must carry dates and they must overlap.
    dated = [item for item in items if item.period_start and item.period_end]
    if len(dated) < 2:
        return False, "insufficient evidence: missing comparable time periods"
    starts = [_parse_date(item.period_start) for item in dated]
    ends = [_parse_date(item.period_end) for item in dated]
    if any(d is None for d in starts + ends):
        return False, "insufficient evidence: unparseable time periods"
    latest_start = max(d for d in starts if d is not None)
    earliest_end = min(d for d in ends if d is not None)
    if latest_start > earliest_end:  # type: ignore[operator]
        return False, "period mismatch: non-overlapping time periods"
    return True, "comparison valid: same metric/unit/frequency/definition, overlapping period"


def comparability_unknowns(evidence: Sequence["ComparisonEvidence"]) -> Dict[str, List[str]]:
    """Unknown metadata dimensions per entity (UNKNOWN stays UNKNOWN).

    Returns {dimension: [entity, ...]} for frequency/currency/definition/
    geography/population/period unknown on historical evidence. Callers must
    record these as assumptions and must not treat them as compatible when
    the dimension is necessary for the comparison (frequency/period for
    historical, currency for absolutes).
    """
    out: Dict[str, List[str]] = {
        "frequency": [], "currency": [], "definition": [],
        "geography": [], "population": [], "period": [],
    }
    for item in evidence or []:
        name = str(getattr(item, "entity", "") or "")
        if not str(getattr(item, "frequency", "") or "").strip():
            out["frequency"].append(name)
        if evidence_currency(item) is None and str(getattr(item, "unit", "") or "").strip().lower() in (
            "currency", "money", "currency:unknown", "price",
        ):
            out["currency"].append(name)
        if not str(getattr(item, "definition", "") or "").strip():
            out["definition"].append(name)
        if not str(getattr(item, "geography", "") or "").strip():
            out["geography"].append(name)
        if not str(getattr(item, "population", "") or "").strip():
            out["population"].append(name)
        if not getattr(item, "period_start", None) or not getattr(item, "period_end", None):
            out["period"].append(name)
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# Deterministic statistics (specs/11 S2: code computes, the LLM narrates)
# ---------------------------------------------------------------------------
def compute_pct_change(start: Any, end: Any) -> Optional[float]:
    """Simple end-to-end percentage change, rounded to 2dp (None if undefined).

    Explicitly NOT a CAGR: callers must label it as simple percentage
    change. None vs 0 vs NaN are distinguished (0 is valid, None/NaN
    missing, zero-base undefined -> None, never inf).
    """
    try:
        from app.services.data.canonical import simple_pct_change as _simple

        return _simple(start, end)
    except Exception:
        pass
    try:
        import math as _math

        if start is None or end is None:
            return None
        start_f, end_f = float(start), float(end)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (_math.isfinite(start_f) and _math.isfinite(end_f)):
        return None
    if start_f == 0:
        return None
    return round((end_f - start_f) / abs(start_f) * 100, 2)


def compute_cagr(start: Any, end: Any, periods: Any) -> Optional[float]:
    """Deterministic CAGR with explicit period semantics (percent, 2dp)."""
    try:
        from app.services.data.canonical import compute_cagr as _cagr

        return _cagr(start, end, periods)
    except Exception:
        return None


STATS_FORMULA = (
    "pct_change = (end - start) / abs(start) * 100 "
    "(simple end-to-end percentage change only; not CAGR)"
)
CAGR_FORMULA = "CAGR = (end/start)^(1/periods) - 1, in percent"
STATS_ASSUMPTIONS = (
    "Start/end are the first/last points of the same validated historical "
    "series for each company (same metric, unit, frequency, definition, "
    "overlapping period). Profitability uses ONE shared metric for every "
    "company: net profit margin = net income / revenue * 100, computed in "
    "code per fiscal year. Fiscal-year labels are preserved as reported; "
    "when fiscal years differ between companies the comparison basis is the "
    "latest comparable completed annual periods as labeled."
)


def compute_net_margins(
    revenues: Sequence[Any],
    incomes: Sequence[Any],
    revenue_labels: Optional[Sequence[Any]] = None,
    income_labels: Optional[Sequence[Any]] = None,
) -> List[Optional[float]]:
    """Per-year net profit margin % = net income / revenue * 100 (2dp).

    Joined by normalized period label when both label series are given
    (never pure position alignment across mismatched series); falls back
    to position alignment only when labels are absent or identical.
    Returns None where undefined (zero/missing revenue) -- never 0-filled,
    never silently truncated: length mismatches without labels yield None
    for the unmatched tail instead of shifted values. Deterministic.
    """
    rev = list(revenues or [])
    inc = list(incomes or [])
    if revenue_labels is not None or income_labels is not None:
        try:
            from app.services.data.canonical import strip_clarification_fragments as _s

            def _norm(label: Any) -> str:
                return " ".join(str(label or "").strip().split()).lower()

            if revenue_labels is not None and income_labels is not None:
                rev_labels = [_norm(l) for l in list(revenue_labels or [])]
                inc_labels = [_norm(l) for l in list(income_labels or [])]
                if rev_labels == inc_labels:
                    pass  # identical order: position path below is exact
                else:
                    # Join by period: explicit None gap where a period is
                    # missing on either side (never shift values).
                    rev_map = {l: v for l, v in zip(rev_labels, rev)}
                    inc_map = {l: v for l, v in zip(inc_labels, inc)}
                    margins: List[Optional[float]] = []
                    for label in rev_labels:
                        margins.append(
                            _margin_pair(
                                rev_map.get(label), inc_map.get(label)
                            )
                        )
                    return margins
            _ = _s
        except Exception:
            pass
    margins = []
    for revenue, income in zip(rev, inc):
        margins.append(_margin_pair(revenue, income))
    # Length mismatch without labels: pad the shorter side with None so no
    # value ever shifts position.
    if len(rev) != len(inc):
        margins.extend([None] * abs(len(rev) - len(inc)))
    return margins


def _margin_pair(revenue: Any, income: Any) -> Optional[float]:
    try:
        import math as _math

        if revenue is None or income is None:
            return None
        revenue_f = float(revenue)  # type: ignore[arg-type]
        income_f = float(income)  # type: ignore[arg-type]
        if not (_math.isfinite(revenue_f) and _math.isfinite(income_f)):
            return None
    except (TypeError, ValueError):
        return None
    if revenue_f == 0:
        return None
    return round(income_f / revenue_f * 100, 2)


def compute_yearly_stats(
    labels: Sequence[Any], values: Sequence[Any]
) -> List[Dict[str, Any]]:
    """Year-by-year values with deterministic year-over-year % changes.

    Returns [{label, value, yoy_pct_change}] where the first year's change
    is None (no prior year). Labels are preserved verbatim (fiscal-year
    metadata survives into the answer and visuals).
    """
    out: List[Dict[str, Any]] = []
    paired = list(zip(list(labels or []), list(values or [])))
    for index, (label, value) in enumerate(paired):
        try:
            value_f = float(value) if value is not None else None
        except (TypeError, ValueError):
            value_f = None
        yoy: Optional[float] = None
        if index > 0:
            try:
                prior = float(paired[index - 1][1])
                yoy = compute_pct_change(prior, value_f) if value_f is not None else None
            except (TypeError, ValueError):
                yoy = None
        out.append({"label": label, "value": value_f, "yoy_pct_change": yoy})
    return out


def compute_comparison_stats(
    evidence_by_entity_metric: Dict[str, Dict[str, Tuple[Any, Any]]],
    latest_margins: Optional[Dict[str, Any]] = None,
    yearly: Optional[Dict[str, Any]] = None,
    period_basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute start/end/%-change per entity+metric deterministically.

    Input: {entity: {metric: (start_value, end_value)}}.
    Output: {entity: {metric: {start, end, pct_change}}, "winners": {...},
    "formula": ..., "assumptions": ...}. Winners are decided by highest
    %-change for stock performance and revenue growth; the profitability
    winner is the highest LATEST net profit margin when `latest_margins`
    is supplied (one comparable margin per company), else highest net-income
    %-change. Optional `yearly` carries deterministic year-by-year series
    (labels preserved) and `period_basis` documents fiscal-year alignment.
    Always stated, never LLM-computed.
    """
    out: Dict[str, Any] = {"entities": {}, "winners": {}, "formula": STATS_FORMULA, "assumptions": STATS_ASSUMPTIONS}
    for entity, metrics in (evidence_by_entity_metric or {}).items():
        out["entities"][entity] = {}
        for metric, pair in (metrics or {}).items():
            start, end = pair
            try:
                start_f = float(start) if start is not None else None
                end_f = float(end) if end is not None else None
            except (TypeError, ValueError):
                start_f, end_f = None, None
            pct = compute_pct_change(start_f, end_f) if start_f is not None and end_f is not None else None
            out["entities"][entity][metric] = {"start": start_f, "end": end_f, "pct_change": pct}

    def _winner(metric: str) -> Optional[str]:
        if metric == METRIC_PROFIT and latest_margins:
            scored_margins = [
                (entity, margin) for entity, margin in latest_margins.items()
                if isinstance(margin, (int, float))
            ]
            if scored_margins:
                return max(scored_margins, key=lambda kv: kv[1])[0]
        scored = [
            (entity, vals.get(metric, {}).get("pct_change"))
            for entity, vals in out["entities"].items()
            if isinstance(vals.get(metric), dict)
        ]
        scored = [(e, s) for e, s in scored if isinstance(s, (int, float))]
        if not scored:
            return None
        return max(scored, key=lambda kv: kv[1])[0]

    for metric in (METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT):
        winner = _winner(metric)
        if winner is not None:
            out["winners"][metric] = winner
    if latest_margins:
        out["latest_margins"] = dict(latest_margins)
    if yearly:
        out["yearly"] = yearly
    out["profitability_definition"] = "net profit margin = net income / revenue * 100"
    if period_basis:
        out["period_basis"] = period_basis
    return out


# ---------------------------------------------------------------------------
# Evidence coverage: explicit requested x metrics x time-range requirements
# with retrieved / validated / unavailable states + partial-result policy.
# Single source of truth for answer scope, stats, confidence, visuals.
# ---------------------------------------------------------------------------
@dataclass
class EvidenceRequirement:
    entity: str
    metric: str
    time_range_label: Optional[str] = None


@dataclass
class EvidenceCoverage:
    """Canonical validated evidence state (one per request).

    requested: every entity x metric requirement from the plan.
    retrieved: requirements with raw evidence on hand.
    validated: requirements passing history + like-for-like.
    unavailable: requested minus validated, each with a reason.
    validated_entities / validated_metrics: subsets sufficient for partial.
    excluded_*: requested items dropped from the answer, with reasons.
    sufficient: >=2 validated entities sharing >=1 validated metric.
    partial: sufficient but not complete (some requested excluded).
    """

    requested: List[EvidenceRequirement] = field(default_factory=list)
    retrieved: List[EvidenceRequirement] = field(default_factory=list)
    validated: List[EvidenceRequirement] = field(default_factory=list)
    unavailable: List[Dict[str, Any]] = field(default_factory=list)
    validated_entities: List[str] = field(default_factory=list)
    validated_metrics: List[str] = field(default_factory=list)
    excluded_entities: List[Dict[str, Any]] = field(default_factory=list)
    excluded_metrics: List[Dict[str, Any]] = field(default_factory=list)
    sufficient: bool = False
    partial: bool = False
    complete: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": [r.__dict__ for r in self.requested],
            "retrieved_count": len(self.retrieved),
            "validated_count": len(self.validated),
            "unavailable": list(self.unavailable),
            "validated_entities": list(self.validated_entities),
            "validated_metrics": list(self.validated_metrics),
            "excluded_entities": list(self.excluded_entities),
            "excluded_metrics": list(self.excluded_metrics),
            "sufficient": self.sufficient,
            "partial": self.partial,
            "complete": self.complete,
        }


def build_evidence_coverage(
    *,
    entities: Sequence[str],
    metrics: Sequence[str],
    time_range_label: Optional[str] = None,
    per_entity_metric_ok: Optional[Dict[str, Dict[str, bool]]] = None,
    reasons: Optional[Dict[str, str]] = None,
) -> EvidenceCoverage:
    """Build the canonical coverage matrix from per-entity x metric validity.

    per_entity_metric_ok[entity][metric] True means retrieved AND validated.
    Missing entries mean unavailable (never zero-filled, never inferred).
    Partial policy: sufficient when >=2 entities share >=1 validated metric;
    otherwise insufficient and the affected comparison must BLOCK.
    """
    entities = [str(e) for e in (entities or []) if str(e).strip()]
    metrics = [str(m) for m in (metrics or []) if str(m).strip()]
    per_entity_metric_ok = per_entity_metric_ok or {}
    reasons = reasons or {}
    coverage = EvidenceCoverage()
    for entity in entities:
        for metric in metrics:
            req = EvidenceRequirement(
                entity=entity, metric=metric, time_range_label=time_range_label
            )
            coverage.requested.append(req)
            ok = bool((per_entity_metric_ok.get(entity) or {}).get(metric))
            if ok:
                coverage.retrieved.append(req)
                coverage.validated.append(req)
            else:
                coverage.unavailable.append({
                    "entity": entity, "metric": metric,
                    "reason": reasons.get(f"{entity}::{metric}")
                    or reasons.get(entity)
                    or f"no validated evidence for {entity} / {metric}",
                })
    # Validated subsets: entities/metrics with at least one validated cell.
    ent_ok: Dict[str, int] = {}
    met_ok: Dict[str, int] = {}
    for req in coverage.validated:
        ent_ok[req.entity] = ent_ok.get(req.entity, 0) + 1
        met_ok[req.metric] = met_ok.get(req.metric, 0) + 1
    # An entity counts as validated only when it has ALL requested metrics?
    # No -- partial metric policy: an entity is validated when it has at
    # least one validated metric; metric-level exclusion is tracked
    # separately so a missing profitability column does not drop revenue.
    coverage.validated_entities = sorted(ent_ok.keys())
    coverage.validated_metrics = sorted(met_ok.keys())
    # Common validated metrics across >=2 entities drive sufficiency.
    metric_entity_count: Dict[str, int] = {}
    for metric in metrics:
        ents = {
            req.entity for req in coverage.validated if req.metric == metric
        }
        metric_entity_count[metric] = len(ents)
    common = [m for m, c in metric_entity_count.items() if c >= 2]
    # Sufficiency: at least 2 validated entities sharing at least 1 metric,
    # or (non-comparison / single-metric degenerate) at least 1 validated
    # requirement when fewer than 2 entities were requested.
    if len(entities) >= 2:
        coverage.sufficient = len(common) >= 1 and len(coverage.validated_entities) >= 2
    elif len(entities) == 1:
        coverage.sufficient = len(coverage.validated) >= 1
    else:
        coverage.sufficient = False
    coverage.complete = (
        len(coverage.unavailable) == 0 and bool(coverage.requested)
    )
    coverage.partial = bool(coverage.sufficient and not coverage.complete)
    # Excluded: requested entities/metrics with zero validated cells.
    for entity in entities:
        if entity not in coverage.validated_entities:
            coverage.excluded_entities.append({
                "entity": entity,
                "reason": reasons.get(entity) or f"no validated evidence for {entity}",
            })
    for metric in metrics:
        if metric not in coverage.validated_metrics:
            coverage.excluded_metrics.append({
                "metric": metric,
                "reason": reasons.get(metric) or f"no validated evidence for {metric}",
            })
    return coverage


def coverage_exclusion_note(coverage: EvidenceCoverage) -> str:
    """Human-readable exclusion statement for partial answers.

    LEGACY: no longer on the user-facing path (check_research_completeness
    renders through canonical.exclusion_note_for instead, per the
    single-renderer rule). Kept for backward-compatible imports only.
    """
    bits: List[str] = []
    for item in coverage.excluded_entities:
        bits.append(f"{item.get('entity')} ({item.get('reason')})")
    for item in coverage.excluded_metrics:
        bits.append(f"{item.get('metric')} ({item.get('reason')})")
    if not bits:
        return ""
    return "Excluded from this comparison (insufficient validated evidence): " + "; ".join(bits) + "."


def comparison_confidence(
    *,
    entities_found: Sequence[str],
    entities_required: Sequence[str],
    metrics_found: Sequence[str],
    metrics_required: Sequence[str],
    historical_ok: bool,
    comparison_ok: bool,
    source_count: int = 0,
) -> float:
    """Evidence-driven confidence for multi-entity historical comparisons.

    Partial-result policy: a validated subset with >=2 comparable entities
    and >=1 common metric earns partial confidence (capped at 0.65), never
    0.0. Only an insufficient subset (<2 entities, no common metric, or
    failed validation for the subset) forces 0.0 -- and 0.0 must block
    visualization (never chart at 0%).
    """
    want_e = {str(e).strip().lower() for e in (entities_required or []) if str(e).strip()}
    have_e = {str(e).strip().lower() for e in (entities_found or []) if str(e).strip()}
    want_m = {str(m).strip().lower() for m in (metrics_required or []) if str(m).strip()}
    have_m = {str(m).strip().lower() for m in (metrics_found or []) if str(m).strip()}
    # Insufficient for ANY comparison: fewer than 2 validated entities or
    # no validated metric at all.
    if len(have_e) < 2 and len(want_e) >= 2:
        return 0.0
    if want_m and not have_m:
        return 0.0
    if not historical_ok or not comparison_ok:
        return 0.0
    full_e = (not want_e) or want_e.issubset(have_e)
    full_m = (not want_m) or want_m.issubset(have_m)
    if full_e and full_m:
        if source_count >= 4:
            return 0.85
        if source_count >= 2:
            return 0.65
        return 0.45
    # Partial subset: useful but explicitly incomplete -- cap below full.
    if source_count >= 2:
        return 0.65
    if source_count >= 1:
        return 0.45
    return 0.35


def assert_compatible_inputs(
    first: ComparisonEvidence,
    second: ComparisonEvidence,
    *,
    fx_rates: Optional[Dict[str, float]] = None,
) -> Tuple[bool, str]:
    """Pre-calculation guard: two evidence items may enter pct_change /
    margin math only when semantically compatible (same metric, definition,
    unit, frequency, compatible currency, same geography/population scope,
    same historical status, overlapping period, finite numerics). A
    calculator must NEVER discover meaning -- this assertion runs BEFORE
    any arithmetic. Returns (ok, reason); the full per-dimension verdict
    lives in validate_calculation_inputs(). Pure.
    """
    try:
        verdict = validate_calculation_inputs(first, second, fx_rates=fx_rates)
        if not verdict.get("ok"):
            return False, str(verdict.get("reason", "incompatible"))
    except Exception as exc:
        return False, f"incompatible calculation inputs: {exc}"
    ok, detail = validate_comparison(
        [first, second], fx_rates=fx_rates,
    )
    if not ok:
        return False, f"incompatible calculation inputs: {detail}"
    return True, "calculation inputs compatible"


def evidence_driven_confidence(
    model_confidence: Any,
    *,
    plan: Optional[Any] = None,
    entities_found: Optional[Sequence[str]] = None,
    metrics_found: Optional[Sequence[str]] = None,
    historical_ok: bool = True,
    comparison_ok: bool = True,
    row_count: int = 0,
    source_count: int = 0,
    snippet_count: int = 0,
    provider_count: int = 0,
    has_structured: bool = False,
) -> float:
    """Deterministic evidence-quality cap over the LLM's subjective signal.

    For comparison queries the comparison_confidence() verdict is the cap
    (missing entities/metrics, failed history, or failed like-for-like all
    force 0.0); otherwise a generic evidence cap (strong >= 0.90, thin >=
    0.65, nothing >= 0.35). The model's number survives below the cap and is
    NEVER lifted above it -- many snippets with incompatible numbers cannot
    produce high confidence. A BLOCKED historical comparison always yields
    0.0 here. Pure.
    """
    try:
        model_value = float(model_confidence)
    except (TypeError, ValueError):
        model_value = 0.0
    required_entities: Sequence[str] = []
    required_metrics: Sequence[str] = []
    is_comparison_plan = False
    requires_history_plan = False
    if isinstance(plan, dict):
        required_entities = list(
            plan.get("required_entities", plan.get("entities", [])) or []
        )
        required_metrics = list(
            plan.get("required_metrics", plan.get("metrics", [])) or []
        )
        is_comparison_plan = bool(
            plan.get("is_comparison", plan.get("comparison_requested", False))
        )
        requires_history_plan = bool(plan.get("requires_history", False))
    # The strict entity/metric-exact verdict governs HISTORICAL comparisons
    # (structured multi-year evidence is mandatory there). Qualitative
    # snippet comparisons use the generic evidence cap instead: cited prose
    # is their evidence, and a missing Yahoo series must not zero them.
    if is_comparison_plan and requires_history_plan and required_entities:
        cap = comparison_confidence(
            entities_found=list(entities_found or []),
            entities_required=list(required_entities),
            metrics_found=list(metrics_found or []),
            metrics_required=list(required_metrics),
            historical_ok=historical_ok,
            comparison_ok=comparison_ok,
            source_count=source_count,
        )
    else:
        if (
            row_count >= 3
            or snippet_count >= 3
            or provider_count >= 2
            or has_structured
        ):
            cap = 0.90
        elif row_count >= 1 or snippet_count >= 1:
            cap = 0.65
        else:
            cap = 0.35
    try:
        return round(min(model_value, cap), 2)
    except (TypeError, ValueError):
        return cap


def check_entity_completeness(
    expected_entities: Sequence[str],
    found_entities: Sequence[str],
) -> Tuple[bool, List[str]]:
    """True + [] when every expected entity is present; else False + missing.

    Used by every research stage so a 3-company ask never silently continues
    with two. Pure.
    """
    want = {str(e).strip().lower() for e in (expected_entities or []) if str(e).strip()}
    have = {str(e).strip().lower() for e in (found_entities or []) if str(e).strip()}
    missing = sorted(want - have)
    return (len(missing) == 0, missing)


def build_trace(
    *,
    query: str,
    plan: Optional[Any] = None,
    tools_requested: Optional[Sequence[str]] = None,
    tools_executed: Optional[Sequence[str]] = None,
    tool_results: Optional[Dict[str, Any]] = None,
    missing_evidence: Optional[Sequence[str]] = None,
    comparison_gate: Optional[Dict[str, Any]] = None,
    calculated_stats: Optional[Any] = None,
    visual_decision: Optional[Any] = None,
    final_confidence: Optional[Any] = None,
    # --- runtime-trace hardening (Phase H15): explicit contract fields ---
    planned_tools: Optional[Sequence[str]] = None,
    actually_executed_tools: Optional[Sequence[str]] = None,
    missing_entities: Optional[Sequence[str]] = None,
    missing_metrics: Optional[Sequence[str]] = None,
    completeness: Optional[Dict[str, Any]] = None,
    entities: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[str]] = None,
    time_range: Optional[Any] = None,
) -> Dict[str, Any]:
    """Structured per-request trace for observability (no secrets/PII beyond
    the query text itself; never log API keys, tokens, or user data rows).

    Runtime-trace contract (Phase H15): every production diagnosis must be
    possible from this dict alone -- QUERY, PLAN, ENTITIES, METRICS,
    TIME_RANGE, REQUIRED_TOOLS, PLANNED_TOOLS, ACTUALLY_EXECUTED_TOOLS,
    TOOL_RESULTS, MISSING_ENTITIES, MISSING_METRICS, COMPLETENESS,
    COMPARISON_GATE, BLOCKED_REASON, CALCULATED_STATS, VISUAL_DECISION,
    FINAL_CONFIDENCE. All values are JSON-safe scalars/lists/dicts; raw
    rows, API keys, tokens, and passwords are never included (callers must
    pass counts, never payloads). Pure.
    """
    gate = comparison_gate or {}
    plan_dict = plan if isinstance(plan, dict) else {}
    plan_entities = list(entities if entities is not None else plan_dict.get("entities", []) or [])
    plan_metrics = list(metrics if metrics is not None else plan_dict.get("metrics", []) or [])
    plan_time_range = time_range if time_range is not None else plan_dict.get("time_range")
    if hasattr(plan_time_range, "to_dict"):
        try:
            plan_time_range = plan_time_range.to_dict()  # type: ignore[union-attr]
        except Exception:
            plan_time_range = str(plan_time_range)
    required_tools = list(plan_dict.get("required_tools", []) or [])
    # PLANNED_TOOLS defaults to the explicit arg, then tools_requested (legacy).
    planned = list(planned_tools if planned_tools is not None else (tools_requested or []))
    executed = list(
        actually_executed_tools if actually_executed_tools is not None else (tools_executed or [])
    )
    trace = {
        "query": str(query or "")[:300],
        "plan": {
            "entities": plan_entities,
            "metrics": plan_metrics,
            "time_range": plan_time_range,
            "requires_history": bool(plan_dict.get("requires_history", False)),
            "required_tools": required_tools,
        },
        # Explicit top-level aliases so `trace["entities"]` works without
        # digging into `trace["plan"]`.
        "entities": plan_entities,
        "metrics": plan_metrics,
        "time_range": plan_time_range,
        "required_tools": required_tools,
        "planned_tools": planned,
        "tools_requested": list(tools_requested or planned),
        "actually_executed_tools": executed,
        "tools_executed": executed,
        "tool_results": dict(tool_results or {}),
        "missing_entities": list(missing_entities or []),
        "missing_metrics": list(missing_metrics or []),
        "missing_evidence": list(missing_evidence or []),
        "completeness": dict(completeness or {}),
        "comparison_gate": {
            "applies": bool(gate.get("applies")),
            "blocked": bool(gate.get("blocked")),
            "blocked_reason": str(gate.get("blocked_reason", "") or "")[:500],
            "historical_ok": gate.get("historical_ok"),
            "comparison_ok": gate.get("comparison_ok"),
        },
        "blocked_reason": str(gate.get("blocked_reason", "") or "")[:500],
        "calculated_stats": bool(calculated_stats),
        "visual_decision": visual_decision,
        "final_confidence": final_confidence,
    }
    return trace


def format_runtime_trace(trace: Dict[str, Any]) -> str:
    """One-line safe log rendering of a build_trace() dict.

    Contains only the trace contract fields (no payloads, no secrets).
    Use for `logger.info("RUNTIME TRACE %s", format_runtime_trace(trace))`.
    Pure.
    """
    try:
        import json as _json

        safe = {
            "query": str((trace or {}).get("query", ""))[:120],
            "entities": (trace or {}).get("entities"),
            "metrics": (trace or {}).get("metrics"),
            "time_range": (trace or {}).get("time_range"),
            "required_tools": (trace or {}).get("required_tools"),
            "planned_tools": (trace or {}).get("planned_tools"),
            "actually_executed_tools": (trace or {}).get("actually_executed_tools"),
            "tool_results": (trace or {}).get("tool_results"),
            "missing_entities": (trace or {}).get("missing_entities"),
            "missing_metrics": (trace or {}).get("missing_metrics"),
            "completeness": (trace or {}).get("completeness"),
            "comparison_gate": (trace or {}).get("comparison_gate"),
            "blocked_reason": str((trace or {}).get("blocked_reason", ""))[:200],
            "calculated_stats": (trace or {}).get("calculated_stats"),
            "visual_decision": (trace or {}).get("visual_decision"),
            "final_confidence": (trace or {}).get("final_confidence"),
        }
        return _json.dumps(safe, default=str)
    except Exception:
        return "runtime trace unavailable"


def insufficient_reason(
    *,
    query: str,
    entities: Sequence[str],
    metrics: Sequence[str],
    requested_years: Optional[int],
    historical_ok: bool,
    historical_detail: str = "",
    comparison_ok: bool = False,
    comparison_detail: str = "",
) -> str:
    """Human-readable missing-data statement for blocked comparisons."""
    bits = [f"Could not build the requested comparison for {query.strip()[:120]!r}."]
    if requested_years:
        bits.append(f"Requested window: last {requested_years} years.")
    bits.append(f"Entities detected: {list(entities) or 'none'}.")
    bits.append(f"Metrics detected: {list(metrics) or 'none'}.")
    if not historical_ok:
        bits.append(f"Historical data missing/insufficient. {historical_detail}".strip())
    if not comparison_ok:
        bits.append(f"Comparison not like-for-like. {comparison_detail}".strip())
    count = len(list(entities or []))
    scope = f"all {count} companies" if count > 2 else "BOTH companies"
    bits.append(
        "What is missing: validated multi-year price history AND annual "
        f"revenue/profitability history for {scope} over the same "
        "period (same metric, unit, frequency, definition)."
    )
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Figure metric labelling: prevents funding-vs-cost style false comparisons
# (the shared root cause with the tech-startup vs robotics-startup failure).
# ---------------------------------------------------------------------------
# NOTE: "startup_cost" is deliberately FIRST and distinct from generic
# "cost" and "funding": "$40K startup cost" must bind metric=startup_cost
# (never generic money/cost) so funding != startup_cost and the two can
# never enter one comparison. Order matters -- figure_metric_label returns
# the first matching pattern.
_FIGURE_METRIC_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("startup_cost", re.compile(r"\bstartup\s*costs?\b", re.IGNORECASE)),
    ("revenue", re.compile(r"\b(revenue|revenues|sales|turnover)\b", re.IGNORECASE)),
    ("funding", re.compile(r"\b(funding|funded|raised|raising|investment|valuation|financing)\b", re.IGNORECASE)),
    ("deal", re.compile(r"\b(sold\s+for|sold|acqui(red|sition)|deal)\b", re.IGNORECASE)),
    ("cost", re.compile(r"\b(cost|costs|expense|expenses|spend|spending)\b", re.IGNORECASE)),
    ("price", re.compile(r"\b(price|prices|share\s*price|close|closing)\b", re.IGNORECASE)),
    ("profit", re.compile(r"\b(profit\w*|margin|net\s*income|earnings|ebitda)\b", re.IGNORECASE)),
    ("market_cap", re.compile(r"\b(market\s*cap|marketcap)\b", re.IGNORECASE)),
    # "Market size" phrasing ("the market was valued at $X billion",
    # "market size, share & forecast", "industry is projected to reach...")
    # is the single most common metric cue in industry/sector research
    # reports, yet was entirely absent from this list -- every such figure
    # fell through with metric=None ("missing metric (WHAT unknown)" in
    # is_figure_comparison_eligible), so market-size comparisons could
    # never produce a comparison/bar chart even with clean, cited,
    # same-currency numbers on both sides. Ordered after market_cap so a
    # literal "market cap" mention still wins that more specific label.
    (
        "market_size",
        re.compile(
            r"\bmarket\s*(size|sizing|value|valued|worth|share)\b"
            r"|\b(market|industry)\s+(was|is|reached|stood\s+at|"
            r"projected|expected|estimated|forecast(ed)?)\b",
            re.IGNORECASE,
        ),
    ),
    # Count nouns ("16 billion views", "50M subscribers", "12,655 open
    # jobs"): the WHAT for count-unit figures. Ordered LAST so an explicit
    # money cue in the same window ("$50M funding for 1000 users") still
    # wins the more specific financial label.
    ("views", re.compile(r"\bviews?\b", re.IGNORECASE)),
    ("subscribers", re.compile(r"\bsubscribers?\b", re.IGNORECASE)),
    ("followers", re.compile(r"\bfollowers?\b", re.IGNORECASE)),
    ("jobs", re.compile(r"\b(jobs?|job\s*openings?|openings?|vacanc\w+)\b", re.IGNORECASE)),
    ("users", re.compile(r"\busers?\b", re.IGNORECASE)),
    ("downloads", re.compile(r"\b(downloads?|installs?)\b", re.IGNORECASE)),
    ("employees", re.compile(r"\b(employees?|headcount|staff|workers?)\b", re.IGNORECASE)),
    ("customers", re.compile(r"\b(customers?|clients?|members?)\b", re.IGNORECASE)),
]


def figure_metric_label(context: str) -> Optional[str]:
    """Metric cue for a snippet context (None when no cue present)."""
    for label, pattern in _FIGURE_METRIC_PATTERNS:
        if pattern.search(context or ""):
            return label
    return None


def figures_share_metric(figures: Sequence[Dict[str, Any]]) -> Tuple[bool, str]:
    """True when cited figures are comparable (same metric cue or uncured).

    Only blocks when two figures carry EXPLICITLY different cues (e.g.
    funding vs cost) -- uncured figures stay comparable for backward
    compatibility with existing qualitative answers.
    """
    labels = [figure_metric_label(str(fig.get("context", ""))) for fig in (figures or [])]
    # "deal" (sold for) and "funding" (raised) are both transaction values:
    # normalise to one bucket so "raised $300k vs sold for $1M" still compares.
    normalised = [{"deal": "funding"}.get(label, label) for label in labels]
    present = [label for label in normalised if label]
    if len(set(present)) <= 1:
        return True, "same or uncured metric"
    return False, f"metric mismatch across figures: {sorted(set(present))}"


def figure_entity_label(context: str, entities: Sequence[str]) -> Optional[str]:
    """Which queried entity (if any) a figure context mentions."""
    lowered = (context or "").lower()
    for entity in entities or []:
        if not entity:
            continue
        name = str(entity).lower()
        if name in lowered:
            return str(entity)
    # Second pass, generic-noun-stripped: a broad sector/industry entity
    # ("AI industry", "artificial industry", "the medical market") almost
    # never appears verbatim in a source snippet -- sources name the
    # specific thing ("AI in Healthcare Market", "medical devices market"),
    # not the user's generic phrasing. Stripping trailing generic nouns and
    # retrying lets that core word still bind instead of the entity being
    # dropped as "insufficient validated evidence" on every industry-level
    # comparison. Guarded to >=3 chars so this never falls back to a bare
    # 1-2 letter acronym ("ai", "it") matching as a substring of unrelated
    # words.
    for entity in entities or []:
        if not entity:
            continue
        core = re.sub(
            r"\b(industry|industries|sector|sectors|market|markets|the|global)\b",
            "",
            str(entity).lower(),
        )
        core = " ".join(core.split())
        if len(core) >= 3 and core in lowered:
            return str(entity)
    return None


# ---------------------------------------------------------------------------
# Hardening contracts (Phase H): type-safe figures, calculation guards,
# research completeness, and currency inference. All pure, all reuse the
# existing evidence model -- no new planner, no new engine.
# ---------------------------------------------------------------------------

def _infer_currency_for_symbol(symbol: Optional[str]) -> Optional[str]:
    """Deterministic reporting-currency inference from a Yahoo symbol suffix.

    Exchange suffix decides: .NS/.BO -> INR, .KS/.KQ -> KRW, .T -> JPY,
    .SS/.SZ -> CNY, .L -> GBP, .PA/.AS/.DE etc -> EUR, otherwise USD
    (US-listed common stock / ADR). Returns None for empty input. This is
    an explicitly documented heuristic for evidence objects that would
    otherwise carry currency=None (unknown); callers record it as inferred.
    Pure.
    """
    if not symbol:
        return None
    upper = str(symbol).strip().upper()
    if "." not in upper:
        return "USD"
    suffix = upper.rsplit(".", 1)[-1]
    if suffix in ("NS", "BO"):
        return "INR"
    if suffix in ("KS", "KQ"):
        return "KRW"
    if suffix == "T":
        return "JPY"
    if suffix in ("SS", "SZ", "HK"):
        return "CNY" if suffix in ("SS", "SZ") else "HKD"
    if suffix in ("L", "IL"):
        return "GBP"
    if suffix in ("PA", "AS", "DE", "MI", "MC", "BR"):
        return "EUR"
    if suffix in ("TO", "V", "CN"):
        return "CAD"
    if suffix in ("AX", "SI"):
        return "AUD" if suffix == "AX" else "SGD"
    return "USD"


def is_figure_comparison_eligible(figure: Any) -> Tuple[bool, str]:
    """Strict comparison-eligibility for one figure (dict or TypedFigure).

    A comparison-eligible figure must carry enough semantic metadata to
    safely enter comparison math/tables/bars/winners:

    - entity (WHO) and metric (WHAT) bound -- never generic "money"
    - numeric value (WHAT amount) and unit class (money vs percent vs count)
    - explicit ISO currency when unit is money (USD/JPY/CNY/...; generic
      "currency"/"money" is explicitly unknown and FAILS; counts need none)
    - scale (magnitude multiplier; 1 when already in single units)
    - definition (what the number MEANS; defaults to metric when absent
      only if metric itself is a specific cue like funding/startup_cost)
    - source_type (snippet/yahoo/...) provenance marker

    Period/frequency/source_url/historical-status are recorded when
    available and REQUIRED for historical comparison math (callers check
    them via validate_comparison), but a snapshot figure with full
    entity/metric/currency binding may still be eligible for snapshot
    prose tables -- hence they gate the calculation path, not this flag.

    Raw figures are NEVER discarded here: False means "prose/context only",
    never "drop". Pure.
    """
    get = (
        (lambda key, default=None: figure.get(key, default))
        if isinstance(figure, dict)
        else (lambda key, default=None: getattr(figure, key, default))
    )
    entity = get("entity")
    metric = get("metric")
    value = get("value")
    unit = get("unit")
    currency = get("currency")
    scale = get("scale")
    definition = get("definition")
    source_type = get("source_type", "snippet")

    if not entity or not str(entity).strip():
        return False, "missing entity (WHO unknown)"
    if not metric or not str(metric).strip():
        return False, "missing metric (WHAT unknown)"
    if str(metric).strip().lower() in ("money", "currency", "value", "amount", "unknown", ""):
        return False, f"generic metric {metric!r} cannot compare"
    try:
        value_f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False, "missing/invalid numeric value"
    import math as _math

    if not _math.isfinite(value_f):
        return False, "non-finite numeric value"
    if str(unit or "").strip().lower() not in ("money", "percent", "count"):
        return False, f"missing/unknown unit {unit!r}"
    if str(unit or "").strip().lower() == "money":
        code = str(currency or "").strip().upper()
        if not _ISO_CURRENCY_RE.match(code or ""):
            return False, f"money figure lacks explicit ISO currency (got {currency!r})"
    # Scale: None means "unstated" -> treat as 1 only when the value was
    # already extracted scaled (pipeline multiplies k/M/B at extraction).
    # An explicit scale is preferred but its absence must not silently
    # re-scale; require presence OR a money/percent unit with finite value.
    # (Lenient here by design: scale mismatch is normalized, not blocked.)
    if scale is not None:
        try:
            scale_f = float(scale)  # type: ignore[arg-type]
            if not _math.isfinite(scale_f) or scale_f <= 0:
                return False, f"invalid scale {scale!r}"
        except (TypeError, ValueError):
            return False, f"invalid scale {scale!r}"
    # Definition: must be specific. A bare metric IS the definition when
    # the metric cue itself is specific (funding/startup_cost/revenue/...).
    effective_definition = str(definition or metric or "").strip().lower()
    if not effective_definition or effective_definition in (
        "money", "currency", "value", "amount", "unknown",
    ):
        return False, "missing definition (WHAT-measured-as unknown)"
    if not source_type or not str(source_type).strip():
        return False, "missing source_type/provenance"
    return True, "comparison-eligible: entity+metric+value+unit+currency+definition bound"


def validate_calculation_inputs(
    first: "ComparisonEvidence",
    second: "ComparisonEvidence",
    *,
    fx_rates: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Structured pre-calculation compatibility verdict (Phase H4).

    The calculator must NEVER decide meaning: this runs BEFORE any
    arithmetic and returns {"ok": bool, "reason": str, "checks": {...}}
    with one flag per contract dimension:

    - compatible_entity (two distinct entities)
    - compatible_metric, compatible_definition, compatible_unit
    - compatible_currency (same ISO or explicit deterministic FX supplied;
      unknown on either side passes with an assumption note recorded by the
      caller -- only two KNOWN-DIFFERENT codes fail)
    - compatible_period (overlapping dated periods), compatible_frequency
    - compatible_scope (geography/population), compatible_status
      (historical vs current), valid_numeric_value (finite numbers)

    Never raises; never converts currencies or scales silently. Callers
    must refuse to calculate when ok is False. Pure.
    """
    checks: Dict[str, Any] = {}
    try:
        entities = [str(getattr(first, "entity", "") or "").strip().lower(),
                    str(getattr(second, "entity", "") or "").strip().lower()]
        checks["compatible_entity"] = (
            bool(entities[0] and entities[1]) and entities[0] != entities[1]
        )
        checks["compatible_metric"] = (
            str(getattr(first, "metric", "") or "").strip().lower()
            == str(getattr(second, "metric", "") or "").strip().lower()
            and bool(str(getattr(first, "metric", "") or "").strip())
        )
        first_def = str(getattr(first, "definition", "") or "").strip().lower()
        second_def = str(getattr(second, "definition", "") or "").strip().lower()
        checks["compatible_definition"] = (
            (not first_def or not second_def or first_def == second_def)
            and checks["compatible_metric"]
        )
        # Unit: explicit currency codes compare as units too (USD vs JPY
        # fails here AND in the currency check -- defense in depth). The
        # legacy generic "currency" unit passes here and defers to the
        # currency check below.
        first_unit = str(getattr(first, "unit", "") or "").strip().lower()
        second_unit = str(getattr(second, "unit", "") or "").strip().lower()
        checks["compatible_unit"] = bool(first_unit and first_unit == second_unit)
        first_cur = evidence_currency(first)
        second_cur = evidence_currency(second)
        if first_cur and second_cur and first_cur != second_cur:
            rates = {str(k).upper(): float(v) for k, v in (fx_rates or {}).items()}
            checks["compatible_currency"] = (
                first_cur.upper() in rates and second_cur.upper() in rates
            )
        else:
            checks["compatible_currency"] = True
        first_freq = str(getattr(first, "frequency", "") or "").strip().lower()
        second_freq = str(getattr(second, "frequency", "") or "").strip().lower()
        checks["compatible_frequency"] = (
            (not first_freq or not second_freq or first_freq == second_freq)
        )
        # Period overlap.
        try:
            start_a, end_a = _parse_date(getattr(first, "period_start", None)), _parse_date(
                getattr(first, "period_end", None)
            )
            start_b, end_b = _parse_date(getattr(second, "period_start", None)), _parse_date(
                getattr(second, "period_end", None)
            )
            if start_a and end_a and start_b and end_b:
                checks["compatible_period"] = max(start_a, start_b) <= min(end_a, end_b)
            else:
                checks["compatible_period"] = False
        except Exception:
            checks["compatible_period"] = False
        first_geo = str(getattr(first, "geography", "") or "").strip().lower()
        second_geo = str(getattr(second, "geography", "") or "").strip().lower()
        first_pop = str(getattr(first, "population", "") or "").strip().lower()
        second_pop = str(getattr(second, "population", "") or "").strip().lower()
        checks["compatible_scope"] = (
            (not first_geo or not second_geo or first_geo == second_geo)
            and (not first_pop or not second_pop or first_pop == second_pop)
        )
        checks["compatible_status"] = (
            bool(getattr(first, "is_historical", False))
            == bool(getattr(second, "is_historical", False))
        )
        import math as _math

        try:
            first_v = float(getattr(first, "value", None))  # type: ignore[arg-type]
            second_v = float(getattr(second, "value", None))  # type: ignore[arg-type]
            checks["valid_numeric_value"] = bool(
                _math.isfinite(first_v) and _math.isfinite(second_v)
            )
        except (TypeError, ValueError):
            checks["valid_numeric_value"] = False
        ok = all(bool(v) for v in checks.values())
        failing = sorted(k for k, v in checks.items() if not v)
        reason = (
            "calculation inputs compatible"
            if ok else f"incompatible calculation inputs: {', '.join(failing)}"
        )
        return {"ok": ok, "reason": reason, "checks": checks}
    except Exception as exc:
        return {"ok": False, "reason": f"validation failed: {exc}", "checks": checks}


def check_research_completeness(
    plan: Any,
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    """Hard deterministic completeness verdict (Phase H6).

    Verifies the 9-point contract for the canonical ResearchPlan:

    1. every required entity exists in evidence
    2. every required metric exists per entity
    3. required tools actually produced evidence
    4. historical coverage is sufficient for the requested window
    5. evidence is comparison-eligible (like-for-like validation)
    6. periods overlap across entities
    7. units/currencies are compatible (or explicit FX supplied)
    8. definitions match across entities
    9. enough observations exist (>=2 dated points per entity+metric)

    `evidence` shape (all optional): {
      "price_history": [...], "financial_history": [...],
      "market_data": [...], "fundamentals": [...],
      "required_tools_present": [...],  # tools that actually yielded evidence
      "fx_rates": {...},
    }

    Returns {
      "comparison_complete": bool,
      "per_entity": {entity: {metric: bool, ...}},
      "missing_entities": [...], "missing_metrics": [...],
      "missing": [...human-readable...],
      "historical_ok": bool, "historical_detail": str,
      "comparison_ok": bool, "comparison_detail": str,
    }

    Never silently drops a company: a 3-company ask with 2 present yields
    comparison_complete False with the missing company named. Pure.
    """
    plan_dict = dict(plan) if isinstance(plan, dict) else {}
    required_entities: List[str] = list(
        plan_dict.get("required_entities", plan_dict.get("entities", [])) or []
    )
    required_metrics: List[str] = list(
        plan_dict.get("required_metrics", plan_dict.get("metrics", [])) or []
    )
    required_tools: List[str] = list(plan_dict.get("required_tools", []) or [])
    requested_years = plan_dict.get("period_years")
    evidence = dict(evidence or {})
    price_history = list(evidence.get("price_history") or [])
    financial_history = list(evidence.get("financial_history") or [])
    fx_rates = dict(evidence.get("fx_rates") or {})

    per_entity: Dict[str, Dict[str, bool]] = {}
    missing: List[str] = []
    missing_entities: List[str] = []
    missing_metrics: List[str] = []

    def _has_price(entity: str) -> bool:
        key = str(entity).strip().lower()
        return any(
            isinstance(item, dict)
            and str(item.get("entity", "")).strip().lower() == key
            and (item.get("values") or (item.get("revenue", {}) or {}).get("values"))
            or (
                isinstance(item, dict)
                and str(item.get("entity", "")).strip().lower() == key
                and item.get("values") and item.get("labels")
            )
            for item in price_history
        )

    def _block_values(entity: str, block: str) -> list:
        key = str(entity).strip().lower()
        for item in financial_history:
            if not isinstance(item, dict):
                continue
            if str(item.get("entity", "")).strip().lower() != key:
                continue
            chunk = (item.get(block, {}) or {})
            if isinstance(chunk, dict) and chunk.get("values"):
                return list(chunk.get("values") or [])
        return []

    def _block_labels(entity: str, block: str) -> list:
        key = str(entity).strip().lower()
        for item in financial_history:
            if not isinstance(item, dict):
                continue
            if str(item.get("entity", "")).strip().lower() != key:
                continue
            chunk = (item.get(block, {}) or {})
            if isinstance(chunk, dict) and chunk.get("values"):
                return list(chunk.get("labels") or [])
        return []

    # 1+2: per-entity x per-metric presence (>=2 dated points where history
    # is required; >=1 point otherwise).
    needs_history = bool(plan_dict.get("requires_history"))
    for entity in required_entities:
        per_entity.setdefault(entity, {})
        for metric in required_metrics:
            ok = False
            if metric == METRIC_STOCK:
                items = [
                    item for item in price_history
                    if isinstance(item, dict)
                    and str(item.get("entity", "")).strip().lower()
                    == str(entity).strip().lower()
                    and item.get("values") and item.get("labels")
                ]
                ok = any(len(list(item.get("values") or [])) >= (2 if needs_history else 1) for item in items)
            elif metric == METRIC_REVENUE:
                ok = len(_block_values(entity, "revenue")) >= (2 if needs_history else 1)
            elif metric == METRIC_PROFIT:
                ok = len(_block_values(entity, "net_income")) >= (2 if needs_history else 1)
            else:
                ok = False
            per_entity[entity][metric] = bool(ok)
            if not ok:
                missing.append(f"{entity}: {metric} missing")
                if metric not in missing_metrics:
                    missing_metrics.append(metric)

    # Entities with nothing at all are missing entities.
    for entity in required_entities:
        row = per_entity.get(entity, {})
        if row and not any(row.values()):
            if entity not in missing_entities:
                missing_entities.append(entity)
        elif not row and required_metrics:
            if entity not in missing_entities:
                missing_entities.append(entity)
    # Also: any required entity absent from every evidence list.
    have_entities = set()
    for lst in (price_history, financial_history):
        for item in lst:
            if isinstance(item, dict) and item.get("entity"):
                name = str(item.get("entity", "")).strip()
                if name:
                    have_entities.add(name.lower())
    for entity in required_entities:
        if str(entity).strip().lower() not in have_entities and entity not in missing_entities:
            missing_entities.append(entity)

    # 3: required tools actually produced evidence. Only CORE history
    # tools block the comparison (market_history for stock, financial_history
    # for revenue/profitability, market for non-historical stock):
    # fundamentals (current scale context) and snippets (explanation) are
    # recorded as missing context but never block the chart on their own --
    # otherwise a valid 3Y Yahoo comparison without fundamentals text would
    # wrongly stay chartless.
    _CORE_TOOLS = {"market_history", "financial_history", "market"}
    present_tools: List[str] = list(evidence.get("required_tools_present") or [])
    if not present_tools:
        # Derive from payload presence when the caller did not state it.
        if price_history:
            present_tools.append("market_history")
        if financial_history:
            present_tools.append("financial_history")
        if evidence.get("market_data"):
            present_tools.append("market")
        if evidence.get("fundamentals"):
            present_tools.append("fundamentals")
        if evidence.get("snippets") or evidence.get("web_snippet_count"):
            present_tools.append("snippets")
        if evidence.get("news_context"):
            present_tools.append("snippets")
    core_required = [t for t in required_tools if t in _CORE_TOOLS]
    # Historical stock/revenue intent implies its history tool even when
    # the plan's required_tools list predates the call (defense in depth).
    if needs_history:
        if METRIC_STOCK in required_metrics and "market_history" not in core_required:
            core_required.append("market_history")
        if (METRIC_REVENUE in required_metrics or METRIC_PROFIT in required_metrics) and "financial_history" not in core_required:
            core_required.append("financial_history")
    for tool in required_tools:
        if tool not in present_tools:
            if tool in _CORE_TOOLS:
                missing.append(f"required tool produced no evidence: {tool}")
            else:
                # Context-only gap: visible in trace, not a completeness fail.
                missing.append(f"context tool produced no evidence: {tool} (non-blocking)")

    # 4: historical coverage per series.
    historical_ok = True
    historical_bits: List[str] = []
    if requested_years and needs_history:
        if METRIC_STOCK in required_metrics:
            for item in price_history:
                ok, detail = validate_historical_coverage(
                    labels=list(item.get("labels") or []),
                    period_start=item.get("period_start"),
                    period_end=item.get("period_end"),
                    requested_years=requested_years,
                    values=item.get("values"),
                )
                if not ok:
                    historical_ok = False
                    historical_bits.append(f"{item.get('entity')}: {detail}")
        for block, metric in (("revenue", METRIC_REVENUE), ("net_income", METRIC_PROFIT)):
            if metric in required_metrics:
                for entity in required_entities:
                    labels = _block_labels(entity, block)
                    values = _block_values(entity, block)
                    if len(values) < 2:
                        historical_ok = False
                        historical_bits.append(f"{entity}: need 2+ annual {block} points")
    historical_detail = " ".join(historical_bits)

    # 5-9: like-for-like validation across entities (metric/unit/period/
    # frequency/definition/currency/scope/status + overlap + observations).
    comparison_ok = True
    comparison_bits: List[str] = []
    if required_entities and required_metrics:
        # Build one ComparisonEvidence per entity per metric from the raw
        # series tails (same construction the gate uses) and validate.
        for metric in required_metrics:
            group: List[ComparisonEvidence] = []
            for entity in required_entities:
                if metric == METRIC_STOCK:
                    series = next(
                        (item for item in price_history
                         if isinstance(item, dict)
                         and str(item.get("entity", "")).strip().lower()
                         == str(entity).strip().lower()
                         and item.get("values") and item.get("labels")),
                        None,
                    )
                    if series is None:
                        continue
                    vals = list(series.get("values") or [])
                    labs = list(series.get("labels") or [])
                    try:
                        group.append(ComparisonEvidence(
                            entity=str(entity), metric=metric, value=float(vals[-1]),
                            unit=str(series.get("currency", "price") or "price"),
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency=str(series.get("frequency", "weekly") or "weekly"),
                            definition=str(series.get("metric", "close") or "close"),
                            source="evidence", is_historical=True,
                        ))
                    except (TypeError, ValueError):
                        continue
                else:
                    block = "revenue" if metric == METRIC_REVENUE else "net_income"
                    want_metric = "annualTotalRevenue" if metric == METRIC_REVENUE else "annualNetIncome"
                    series = next(
                        (item for item in financial_history
                         if isinstance(item, dict)
                         and str(item.get("entity", "")).strip().lower()
                         == str(entity).strip().lower()
                         and isinstance(item.get(block, {}), dict)
                         and (item.get(block, {}) or {}).get("values")),
                        None,
                    )
                    if series is None:
                        continue
                    chunk = (series.get(block, {}) or {})
                    vals = list(chunk.get("values") or [])
                    labs = list(chunk.get("labels") or [])
                    if len(vals) < 2:
                        continue
                    currency = str(
                        chunk.get("currency", "") or series.get("currency", "") or ""
                    ).strip().upper() or None
                    try:
                        group.append(ComparisonEvidence(
                            entity=str(entity), metric=metric, value=float(vals[-1]),
                            unit=currency or "currency",
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency="annual", definition=str(chunk.get("metric", want_metric)),
                            source="evidence", is_historical=True, currency=currency,
                            source_type="evidence",
                        ))
                    except (TypeError, ValueError):
                        continue
            if len(group) >= 2:
                ok, detail = validate_comparison(
                    group, expected_entities=required_entities,
                    expected_metric=metric, fx_rates=fx_rates,
                    enforce_currency=False,
                )
                if not ok:
                    comparison_ok = False
                    comparison_bits.append(f"{metric}: {detail}")
                # Absolute-currency transparency: known-different codes fail
                # absolute reads (growth % still valid); record it.
                known = {evidence_currency(item) for item in group}
                known.discard(None)
                if len(known) > 1:
                    comparison_bits.append(
                        f"{metric}: absolute values in {sorted(known)} do not "
                        "compare without explicit FX (growth % does)"
                    )
            elif len(required_entities) >= 2:
                comparison_ok = False
                comparison_bits.append(f"{metric}: fewer than 2 entity series")
    comparison_detail = " ".join(comparison_bits)

    # Any per-entity gap fails the strict whole comparison (never silent
    # 2-of-3 as complete). Partial-result policy lives alongside: a
    # validated subset with >=2 comparable entities sharing >=1 metric is
    # sufficient for a partial answer with explicit exclusions.
    any_gap = any(
        not present for row in per_entity.values() for present in row.values()
    )
    missing_tool = any(
        tool not in present_tools for tool in core_required
    ) if core_required else False
    comparison_complete = bool(
        required_entities
        and required_metrics
        and not any_gap
        and not missing_tool
        and historical_ok
        and comparison_ok
    )
    # Canonical coverage matrix (single source of truth for partial scope).
    try:
        _reasons: Dict[str, str] = {}
        for item in (missing or []):
            _reasons.setdefault(str(item)[:120], str(item)[:200])
        # Per-metric reasons from the detail strings.
        _time_label = None
        try:
            _time_label = plan_dict.get("period_label")
        except Exception:
            _time_label = None
        coverage = build_evidence_coverage(
            entities=required_entities,
            metrics=required_metrics,
            time_range_label=_time_label,
            per_entity_metric_ok=per_entity,
            reasons={e: f"no validated evidence for {e}" for e in missing_entities},
        )
    except Exception:
        coverage = EvidenceCoverage()
    # Single-renderer rule: the user-facing exclusion note always renders
    # through canonical.exclusion_note_for (plain names, never raw list
    # repr, never a second ad-hoc format). coverage_exclusion_note() stays
    # defined as legacy but is no longer on the user-facing path.
    try:
        from app.services.data.canonical import exclusion_note_for as _excl_for

        _exclusion_note = _excl_for(
            {
                "excluded_entities": [
                    {"entity": item.get("entity")}
                    for item in coverage.excluded_entities
                ],
                "excluded_metrics": [
                    {"metric": item.get("metric")}
                    for item in coverage.excluded_metrics
                ],
            }
        )
    except Exception:
        _exclusion_note = coverage_exclusion_note(coverage)
    return {
        "comparison_complete": comparison_complete,
        "per_entity": per_entity,
        "missing_entities": missing_entities,
        "missing_metrics": missing_metrics,
        "missing": missing,
        "historical_ok": historical_ok,
        "historical_detail": historical_detail,
        "comparison_ok": comparison_ok,
        "comparison_detail": comparison_detail,
        # Partial-result contract (generic, additive fields only).
        "coverage": coverage.to_dict(),
        "validated_entities": list(coverage.validated_entities),
        "validated_metrics": list(coverage.validated_metrics),
        "excluded_entities": list(coverage.excluded_entities),
        "excluded_metrics": list(coverage.excluded_metrics),
        "sufficient_for_partial": bool(coverage.sufficient),
        "partial": bool(coverage.partial),
        "exclusion_note": _exclusion_note,
    }
