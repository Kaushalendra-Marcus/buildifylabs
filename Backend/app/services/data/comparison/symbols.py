"""Company symbols, entity typing, market candidacy. Split from comparison.py; behavior unchanged."""

from __future__ import annotations

import logging
import re

from typing import Dict, List, Optional, Sequence

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


def symbol_for_entity(entity: str) -> Optional[str]:
    """Canonical Yahoo symbol for a known entity display name (None if unknown)."""
    if not entity:
        return None
    return KNOWN_COMPANIES.get(str(entity).strip().lower())

__all__ = [
    "FINANCIAL_ENTITY_TYPES",
    "KNOWN_COMPANIES",
    "KNOWN_PRIVATE_COMPANIES",
    "METRIC_PROFIT",
    "METRIC_REVENUE",
    "METRIC_STOCK",
    "_CATEGORY_CUE_RE",
    "_COUNTRY_CUE_RE",
    "_DISPLAY_NAMES",
    "_GEOGRAPHY_TOKENS",
    "_INDUSTRY_CUE_RE",
    "_PRIVATE_CUE_RE",
    "_PRODUCT_CUE_RE",
    "_PROFIT_RE",
    "_REGION_CUE_RE",
    "_REVENUE_RE",
    "_STOCK_RE",
    "_TICKER_RE",
    "classify_entity_type",
    "financial_entities",
    "is_financial_entity",
    "market_candidate_entities",
    "may_attempt_market_resolution",
    "symbol_for_entity",
]
