"""Currency normalization + compatibility. Split from comparison.py; behavior unchanged."""

from __future__ import annotations

import logging
import re

from typing import Dict, Optional

logger = logging.getLogger(__name__)



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

__all__ = [
    "_CURRENCY_SYMBOL_MAP",
    "_CURRENCY_WORD_MAP",
    "_ISO_CURRENCY_RE",
    "currencies_compatible",
    "evidence_currency",
    "normalize_currency",
]
