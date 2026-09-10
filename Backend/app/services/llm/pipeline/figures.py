"""Snippet-figure extraction, binding, attribution. Split from langchain_pipeline.py; behavior unchanged."""

import logging
import re

from typing import Dict, Optional

from .deps import decompose_comparison_query, figure_entity_label, figure_metric_label, is_figure_comparison_eligible, normalize_currency

logger = logging.getLogger(__name__)


_SYNTH_TABLE_MAX_ROWS = 12
_SYNTH_TABLE_MAX_COLS = 6
_SYNTH_SERIES_MAX_POINTS = 30
_SYNTH_SOURCES_MAX_ROWS = 8
_SYNTH_FIGURES_MAX = 8
# Financial tables must never silently drop a whole company to a row cap
# (the live Toyota omission): budget holds 4 companies x 6 annual points.
_SYNTH_FINANCIAL_MAX_ROWS = 24


def _interleave_entities(rows: list[list], max_rows: int) -> list[list]:
    """Round-robin interleave table rows by entity (first column) so a row
    cap degrades to fewer years per company, never to a missing company.
    Deterministic: entities alphabetical, rows stable within entity."""
    by_entity: Dict[str, list] = {}
    for row in rows:
        by_entity.setdefault(str(row[0]) if row else "", []).append(row)
    ordered_entities = sorted(by_entity)
    out: list[list] = []
    index = 0
    while len(out) < max_rows:
        progressed = False
        for entity in ordered_entities:
            bucket = by_entity[entity]
            if index < len(bucket):
                out.append(bucket[index])
                progressed = True
                if len(out) >= max_rows:
                    break
        if not progressed:
            break
        index += 1
    return out

# Verbatim figures with explicit units only (money, percent, or counts with
# a count noun). Bare numbers ("30 ideas", "8 months", "Top 10", years like
# "2024") never qualify, so dates and ranks cannot leak into charts — but a
# number attached to a count noun ("16 billion views", "12,655 open jobs")
# IS chartable evidence and must become visuals, never prose-only.
_FIGURE_MONEY_RE = re.compile(
    r"([$€₹£])\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|B|million|billion|thousand)?\b"
)
_FIGURE_PERCENT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(%(?!\w)|percent\b)")
_FIGURE_COUNT_NOUNS = (
    r"views?|subscribers?|followers?|jobs?|openings?|vacancies|"
    r"users?|downloads?|installs?|employees?|headcount|staff|"
    r"customers?|clients?|members?|orders?|listeners?|students?"
)
_FIGURE_COUNT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|B|million|billion|thousand)?\b\s?"
    r"(?:open\s+)?(" + _FIGURE_COUNT_NOUNS + r")\b",
    re.IGNORECASE,
)
# Scale-bearing bare numbers ("about 350 billion", "around 315 million"):
# the scale word proves it is a quantity, not a rank/year. The WHAT comes
# from the nearest count noun in a small window ("lifetime views (about
# 350 billion)" -> views). Unit-less decimals ("58.3") never qualify.
_FIGURE_SCALED_BARE_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|B|million|billion|thousand)\b",
    re.IGNORECASE,
)
_FIGURE_NOUN_WINDOW = 80
# Count nouns normalised to their canonical metric label (matches the
# count cues in comparison.figure_metric_label).
_FIGURE_COUNT_NOUN_TO_METRIC = {
    "view": "views", "views": "views",
    "subscriber": "subscribers", "subscribers": "subscribers",
    "follower": "followers", "followers": "followers",
    "job": "jobs", "jobs": "jobs",
    "opening": "jobs", "openings": "jobs", "vacancy": "jobs", "vacancies": "jobs",
    "user": "users", "users": "users",
    "download": "downloads", "downloads": "downloads",
    "install": "downloads", "installs": "downloads",
    "employee": "employees", "employees": "employees",
    "headcount": "employees", "staff": "employees",
    "customer": "customers", "customers": "customers",
    "client": "customers", "clients": "customers",
    "member": "customers", "members": "customers",
    "order": "orders", "orders": "orders",
    "listener": "listeners", "listeners": "listeners",
    "student": "students", "students": "students",
}
_FIGURE_SCALE = {
    "k": 1e3, "K": 1e3, "thousand": 1e3,
    "M": 1e6, "million": 1e6,
    "B": 1e9, "billion": 1e9,
}


def _nearest_count_noun(text: str, start: int, end: int, radius: int = 80) -> Optional[str]:
    """Closest count noun to a bare scaled number, canonicalised to its
    metric label. Either side counts ("lifetime views (about 350
    billion)" -> views); nearest wins so mixed answers resolve per number.
    None when no count noun is near — the figure stays metric-less."""
    window_start = max(0, start - radius)
    window = text[window_start:min(len(text), end + radius)]
    best: Optional[str] = None
    best_dist: Optional[int] = None
    for hit in re.finditer(_FIGURE_COUNT_NOUNS, window, re.IGNORECASE):
        pos = window_start + hit.start()
        hit_end = window_start + hit.end()
        if pos < end and start < hit_end:
            dist = 0
        else:
            dist = min(abs(pos - start), abs(hit_end - end))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = hit.group(0).strip().lower()
    if best is None:
        return None
    return _FIGURE_COUNT_NOUN_TO_METRIC.get(best)


def _figures_from_snippets(
    snippets: Optional[list], query: Optional[str] = None
) -> list:
    """Extract cited money/percent/count figures verbatim from web snippets.

    Counts cover adjacent nouns ("50M subscribers") AND scale-bearing bare
    numbers ("about 350 billion", WHAT resolved from the nearest count
    noun). Unit-less decimals, ranks ("Top 10") and years never qualify.

    Each figure keeps its exact text, a normalized value for bar heights, a
    unit class (money vs percent vs count, never mixed on one chart), the FULL
    snippet scope for semantic binding (H3: never truncated before
    WHO/WHAT/WHEN/UNIT/CURRENCY/DEFINITION resolution), the snippet it
    came from, and the citation number. Semantic binding (entity / metric
    / period / currency / frequency / definition / scale) is attached
    deterministically against the query's entities when `query` is given;
    a figure is comparison_eligible ONLY under the strict H2 contract
    (entity + specific metric + explicit ISO currency for money + ...;
    counts need entity + specific metric, no currency). Untyped "$202B"
    (no known meaning) stays citable in the figures table but must never
    enter numerical comparison. No NLP, no invention.
    """
    figures: list = []
    seen: set[str] = set()
    queried_entities: list = []
    try:
        if decompose_comparison_query is not None and query:
            queried_entities = (
                decompose_comparison_query(query) or {}
            ).get("entities", []) or []
    except Exception:
        queried_entities = []
    for index, snippet in enumerate(snippets or [], start=1):
        text = str(snippet)
        claimed_spans: list = []
        for match in _FIGURE_MONEY_RE.finditer(text):
            claimed_spans.append((match.start(), match.end()))
            amount = float(match.group(2).replace(",", ""))
            scale = _FIGURE_SCALE.get(match.group(3) or "", 1)
            figures.append(
                _bind_figure(
                    text=text,
                    match_text=match.group(0).strip(),
                    value=amount * scale,
                    unit="money",
                    symbol=match.group(1) or "",
                    match_start=match.start(),
                    match_end=match.end(),
                    ref=index,
                    queried_entities=queried_entities,
                    scale=scale,
                )
            )
        for match in _FIGURE_PERCENT_RE.finditer(text):
            claimed_spans.append((match.start(), match.end()))
            figures.append(
                _bind_figure(
                    text=text,
                    match_text=match.group(0).strip(),
                    value=float(match.group(1).replace(",", "")),
                    unit="percent",
                    symbol="",
                    match_start=match.start(),
                    match_end=match.end(),
                    ref=index,
                    queried_entities=queried_entities,
                    scale=1.0,
                )
            )
        for match in _FIGURE_COUNT_RE.finditer(text):
            # A count overlapping a money/percent match belongs to that
            # figure (e.g. "$50M" inside "$50M subscribers") — skip it.
            if any(
                match.start() < end and start < match.end()
                for start, end in claimed_spans
            ):
                continue
            claimed_spans.append((match.start(), match.end()))
            amount = float(match.group(1).replace(",", ""))
            scale = _FIGURE_SCALE.get(match.group(2) or "", 1)
            noun = str(match.group(3) or "").strip().lower()
            figures.append(
                _bind_figure(
                    text=text,
                    match_text=match.group(0).strip(),
                    value=amount * scale,
                    unit="count",
                    symbol="",
                    match_start=match.start(),
                    match_end=match.end(),
                    ref=index,
                    queried_entities=queried_entities,
                    scale=scale,
                    count_noun=_FIGURE_COUNT_NOUN_TO_METRIC.get(noun),
                )
            )
        for match in _FIGURE_SCALED_BARE_RE.finditer(text):
            # Already claimed by a money/percent/adjacent-noun match (e.g.
            # "16 billion" inside "16 billion views") — skip the double count.
            if any(
                match.start() < end and start < match.end()
                for start, end in claimed_spans
            ):
                continue
            claimed_spans.append((match.start(), match.end()))
            amount = float(match.group(1).replace(",", ""))
            scale = _FIGURE_SCALE.get(match.group(2) or "", 1)
            figures.append(
                _bind_figure(
                    text=text,
                    match_text=match.group(0).strip(),
                    value=amount * scale,
                    unit="count",
                    symbol="",
                    match_start=match.start(),
                    match_end=match.end(),
                    ref=index,
                    queried_entities=queried_entities,
                    scale=scale,
                    count_noun=_nearest_count_noun(text, match.start(), match.end()),
                )
            )
    ordered: list = []
    for figure in figures:
        if figure["text"] not in seen:
            seen.add(figure["text"])
            ordered.append(figure)
    return ordered[:_SYNTH_FIGURES_MAX]


# Semantic-binding patterns for snippet figures (all generic, topic-free).
_FIGURE_CONTEXT_CHARS = 200
_FIGURE_FREQUENCY_RE = re.compile(
    r"\b(annual\w*|yearly|quarterly|monthly|weekly|daily|"
    r"per\s+(year|quarter|month|week|day))\b",
    re.IGNORECASE,
)
_FIGURE_YEAR_RE = re.compile(
    r"\b((?:F\.?\s*Y\.?\s*)?20\d{2}|fiscal\s+(?:year\s+)?20\d{2})\b",
    re.IGNORECASE,
)
_FIGURE_CURRENCY_WORD_RE = re.compile(
    r"\b(USD|US\s*dollars?|CNY|RMB|yuan|JPY|yen|EUR|euros?|INR|rupees?|"
    r"GBP|pounds?|dollars?)\b",
    re.IGNORECASE,
)
_FIGURE_SUBJECT_RE = re.compile(r"^\s*([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,2})")


def _fallback_subject(window: str) -> Optional[str]:
    """Leading capitalized subject of a snippet window ("Acme raised ..." ->
    "Acme"). Used ONLY when the query names no known entities; otherwise an
    unattributed figure stays unattributed (strict path). Generic leading
    words ("This", "Buy", "Price") are never subjects."""
    match = _FIGURE_SUBJECT_RE.search(window or "")
    if not match:
        return None
    candidate = " ".join(match.group(1).split())
    if len(candidate) < 2 or _is_generic_entity(candidate):
        return None
    return candidate


def _fallback_subject(window: str) -> Optional[str]:
    """Leading capitalized subject of a snippet window ("Acme raised ..." ->
    "Acme"). Used ONLY when the query names no known entities; otherwise an
    unattributed figure stays unattributed (strict path). Generic leading
    words ("This", "Buy", "Price") are never subjects."""
    match = _FIGURE_SUBJECT_RE.search(window or "")
    if not match:
        return None
    candidate = " ".join(match.group(1).split())
    if len(candidate) < 2 or _is_generic_entity(candidate):
        return None
    return candidate


# ---------------------------------------------------------------------------
# Attributed chart data: every bar/comparison datum must name its WHO.
# A chart label is ALWAYS a resolved entity (product/company/channel) from
# the evidence — never a sentence fragment ("Buy", "This"). Figures without
# a resolvable entity stay citable in the figures table but never chart.
# ---------------------------------------------------------------------------
# Generic leading words that are never entities (pronouns, verbs, commerce
# boilerplate, currency words, sentence starters, months/weekdays — dates
# never name chart data). A candidate whose FIRST word is one of these is
# rejected.
_CHART_ENTITY_STOPWORDS = frozenset({
    "this", "that", "these", "those", "it", "they", "them", "here", "there",
    "buy", "buys", "buying", "shop", "shopping", "check", "click", "read",
    "see", "find", "get", "top", "best", "price", "prices", "deal", "deals",
    "offer", "offers", "sale", "list", "lists", "review", "reviews", "guide",
    "guides", "under", "with", "from", "about", "new", "latest", "full",
    "overall", "more", "most", "such", "each", "other", "many", "some",
    "all", "the", "a", "an", "and", "or", "vs", "per", "rs", "inr", "usd",
    "eur", "gbp", "rupee", "rupees", "dollar", "dollars", "euro", "euros",
    "pound", "pounds", "for", "as", "at", "by", "in", "of", "on", "to",
    "is", "are", "was", "were", "be", "no", "not", "so", "if", "than",
    "then", "into", "over", "after", "before",
    "someone", "somebody", "something", "anyone", "anybody", "anything",
    "everyone", "everybody", "everything", "nobody", "nothing",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
})
# Trailing descriptors stripped from resolved phrases ("... Headphones
# Price" -> "... Headphones"; "... H707 HD" -> "... H707").
_CHART_TRAILING_LABELS = frozenset({"price", "prices", "mrp", "cost", "deal"})
_CHART_TRAILING_SPECS = frozenset({"hd", "rgb", "led", "wireless", "bluetooth", "wired"})
# Leading retailer tokens stripped when more words follow ("Amazon Echo
# Dot" -> "Echo Dot").
_CHART_RETAILER_PREFIXES = frozenset({"amazon", "flipkart", "myntra", "croma"})
# Leading listing-boilerplate stripped the same way ("Headphone And
# Earphone Price Available From Zebronics ..." -> "Zebronics ..."):
# category nouns and filler words describe listings, never items.
_CHART_CATEGORY_WORDS = frozenset({
    "headphone", "headphones", "earphone", "earphones", "earbud", "earbuds",
    "headset", "headsets", "speaker", "speakers", "laptop", "laptops",
    "phone", "phones", "smartphone", "smartphones", "watch", "watches",
    "television", "televisions", "camera", "cameras", "tablet", "tablets",
    "neckband", "neckbands", "keyboard", "keyboards", "monitor", "monitors",
    "printer", "printers", "router", "routers", "charger", "chargers",
    "console", "consoles", "available", "online", "store", "official",
    "genuine",
})
# Colors are never standalone entities ("(Black) Price" -> look further).
_CHART_COLORS = frozenset({
    "black", "white", "blue", "red", "green", "yellow", "pink", "grey",
    "gray", "silver", "gold", "brown", "orange", "purple", "beige", "navy",
})
_CHART_WORD_RE = re.compile(r"[A-Za-z0-9][\w&.\-]*")


def _is_generic_entity(text: str) -> bool:
    """True when a candidate cannot be a chart entity (generic leading word,
    all-generic words, too short, or a bare number)."""
    words = str(text or "").split()
    if not words:
        return True
    first = words[0].strip(".,;:!?()\"'").lower()
    if first in _CHART_ENTITY_STOPWORDS or first in _CHART_RETAILER_PREFIXES:
        # Retailer alone ("Amazon") is never a chart entity; retailer-led
        # phrases are handled (stripped) by the phrase cleaner, not here.
        return True
    if all(w.strip(".,;:!?()\"'").lower() in _CHART_ENTITY_STOPWORDS for w in words):
        return True
    joined = " ".join(words)
    if len(joined) < 2:
        return True
    if re.fullmatch(r"[\d,.\s]+", joined):
        return True
    return False


def _clean_chart_phrase(words: list, full_text: str = "") -> Optional[str]:
    """Brand-led product name from run words: strip leading stopwords /
    retailers and trailing price labels, keep the first 4 words (brand and
    model lead; trailing descriptors repeat across products). The phrase
    must start with a capital and must not be a bare number."""
    cleaned = list(words)
    while len(cleaned) > 1 and (
        cleaned[0].lower() in _CHART_ENTITY_STOPWORDS
        or cleaned[0].lower() in _CHART_RETAILER_PREFIXES
        or cleaned[0].lower() in _CHART_CATEGORY_WORDS
    ):
        cleaned.pop(0)
    cleaned = cleaned[:4]
    # A product name heads with a capital ("Boeing 747", not "2025
    # iPhone"): drop leading non-capitals left by list numbering.
    while len(cleaned) > 1 and not re.match(r"[A-Z]", cleaned[0]):
        cleaned.pop(0)
    while len(cleaned) > 1 and cleaned[-1].lower() in _CHART_TRAILING_LABELS:
        cleaned.pop()
    while len(cleaned) > 1 and cleaned[-1].lower() in _CHART_TRAILING_SPECS:
        cleaned.pop()
    while len(cleaned) > 1 and cleaned[-1].lower() in _CHART_ENTITY_STOPWORDS:
        cleaned.pop()
    if not cleaned:
        return None
    if not re.match(r"[A-Z]", cleaned[0]):
        return None
    if len(cleaned) == 1 and cleaned[0].lower() in _CHART_COLORS:
        return None
    if len(cleaned) == 1:
        # Title-case common nouns ("Neckbands", "Headphones") are categories,
        # not items: they occur lowercase somewhere in the snippet, while
        # proper names ("Echo", "YRF") never do. ALLCAPS/digital names
        # ("YRF", "H707") are always kept.
        word = cleaned[0]
        if (
            word.upper() != word
            and not re.search(r"\d", word)
            and full_text
            and re.search(
                r"(?<![A-Za-z])" + re.escape(word.lower()) + r"(?![A-Za-z])",
                full_text.lower(),
            )
        ):
            return None
    phrase = " ".join(cleaned)
    if _is_generic_entity(phrase):
        return None
    return phrase


def _nearest_product_phrase(window: str, pos: int, full_text: str = "") -> Optional[str]:
    """Closest capitalized product-like phrase to a figure position.

    Searches BACKWARD first: listicles name the product before its price
    ("... Ant Esports H707 ... Price: ₹1,499"). Forward only as fallback.
    Returns a brand-led phrase of at most 4 words, stopword-cleaned, or
    None when nothing entity-like is near.
    """
    tokens: list = []
    for match in _CHART_WORD_RE.finditer(window or ""):
        # Trailing dots are sentence/list punctuation ("1. OneOdio"),
        # never part of the name; stripping them also splits glued runs.
        word = match.group(0).rstrip(".")
        if not word:
            continue
        if re.match(r"[a-z]", word):
            continue
        tokens.append((match.start(), match.start() + len(word), word))
    if not tokens:
        return None
    # Maximal runs of single-space separated tokens.
    runs: list = []
    current = [tokens[0]]
    for start, end, word in tokens[1:]:
        if start == current[-1][1] + 1:
            current.append((start, end, word))
        else:
            runs.append(current)
            current = [(start, end, word)]
    runs.append(current)
    backward = [run for run in runs if run[-1][1] <= pos]
    forward = [run for run in runs if run[0][0] >= pos]
    if backward:
        # Nearest run first.
        for run in sorted(backward, key=lambda run: pos - run[-1][1]):
            phrase = _clean_chart_phrase(
                [word for _, _, word in run], full_text or window
            )
            if phrase:
                return phrase
    if forward:
        for run in sorted(forward, key=lambda run: run[0][0] - pos):
            phrase = _clean_chart_phrase(
                [word for _, _, word in run], full_text or window
            )
            if phrase:
                return phrase
    return None


def _resolve_chart_entity(figure: dict, queried_entities: list) -> Optional[str]:
    """The WHO for one figure, best signal first: query-bound entity, then
    the nearest product phrase (a window-leading fallback like "Headphones
    Under" describes the article heading, not the priced item — proximity
    to the figure wins), then any valid bound entity. None means
    unchartable (stays in the figures table only)."""
    entity = (figure or {}).get("entity")
    if (
        entity
        and not _is_generic_entity(str(entity))
        and (figure or {}).get("entity_source") == "queried"
    ):
        return str(entity).strip()
    window = str((figure or {}).get("context", "") or "")
    if queried_entities and figure_entity_label is not None:
        try:
            hit = figure_entity_label(window, queried_entities or [])
        except Exception:
            hit = None
        if hit and not _is_generic_entity(str(hit)):
            return str(hit).strip()
    match_text = str((figure or {}).get("text", "") or "")
    pos = window.find(match_text) if match_text else -1
    if pos < 0:
        pos = len(window)
    phrase = _nearest_product_phrase(
        window, pos, str((figure or {}).get("full_context", "") or "")
    )
    if phrase:
        return phrase
    if entity and not _is_generic_entity(str(entity)):
        return str(entity).strip()
    return None


def _query_price_constraint(query: str) -> Optional[tuple]:
    """Budget stated in the query ("under 2000 rs", "above $50"):
    (direction, value, ISO-currency-or-None). None when unstated."""
    if not query:
        return None
    match = re.search(
        r"(under|below|less\s+than|up\s*to|upto|within|budget(?: of| is)?|"
        r"max(?:imum)?|over|above|more\s+than|minimum|min(?:imum)?|"
        r"at\s+least|starting\s+(?:at|from))\s?"
        r"([₹$€£])?\s?([\d,]+(?:\.\d+)?)\s?"
        r"(rs|inr|usd|dollars?|eur|euros?|gbp|pounds?|₹|\$)?",
        query,
        re.IGNORECASE,
    )
    if not match:
        return None
    keyword = re.sub(r"\s+", " ", (match.group(1) or "").lower())
    direction = (
        "min"
        if keyword.split()[0] in ("over", "above", "more", "minimum", "min", "at", "starting")
        else "max"
    )
    try:
        value = float((match.group(3) or "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    symbol, word = match.group(2), match.group(4)
    currency: Optional[str] = None
    try:
        if normalize_currency is not None and (symbol or word):
            currency = normalize_currency(symbol or None, word or None)
    except Exception:
        currency = None
    if currency is None and (symbol or word):
        manual = {
            "₹": "INR", "rs": "INR", "inr": "INR",
            "$": "USD", "usd": "USD", "dollars": "USD", "dollar": "USD",
            "€": "EUR", "eur": "EUR", "euros": "EUR", "euro": "EUR",
            "£": "GBP", "gbp": "GBP", "pounds": "GBP", "pound": "GBP",
        }
        currency = manual.get(str(symbol or word or "").strip().lower())
    return (direction, value, (currency or "").upper() or None)


_BUDGET_KEYWORDS = (
    "under", "below", "less than", "up to", "upto", "within", "budget",
    "maximum", "max", "over", "above", "more than", "minimum", "min",
    "at least", "starting",
)


def _window_restates_constraint(window: str, cap: float) -> bool:
    """True when a figure's own context restates the query's budget ("the
    best headphones under ₹2000 ... ₹2000 ..."): the figure IS the
    constraint, not a product price, so it must not chart as an answer."""
    lowered = f" {(window or '').lower()} ".replace(",", "")
    for symbol in ("₹", "$", "€", "£"):
        lowered = lowered.replace(symbol, " ")
    if not any(keyword in lowered for keyword in _BUDGET_KEYWORDS):
        return False
    try:
        digits = str(int(cap)) if float(cap).is_integer() else str(cap)
    except (TypeError, ValueError):
        return False
    return f" {digits} " in lowered or f" {digits}." in lowered


def _apply_budget_constraint(figures: list, query: str) -> list:
    """Drop money figures that violate the query's stated budget from CHART
    pools (over-budget prices never bar as answers to "under X"). The
    figures table keeps everything; figures whose currency cannot be
    verified are kept (not dropped on uncertain grounds)."""
    constraint = _query_price_constraint(query or "")
    if constraint is None:
        return list(figures or [])
    direction, cap, currency = constraint
    kept = []
    for figure in figures or []:
        try:
            if (figure or {}).get("unit") != "money":
                kept.append(figure)
                continue
            code = str((figure or {}).get("currency", "") or "").strip().upper()
            if not code or not currency or code != currency:
                kept.append(figure)
                continue
            value = float((figure or {}).get("value"))
            if value == cap and _window_restates_constraint(
                str((figure or {}).get("context", "") or ""), cap
            ):
                logger.info(
                    "Budget filter: %s restates the query cap, bar-only drop.",
                    (figure or {}).get("text"),
                )
                continue
            if direction == "max" and value > cap:
                logger.info(
                    "Budget filter: %s above %s %s cap, bar-only drop.",
                    (figure or {}).get("text"), cap, currency,
                )
                continue
            if direction == "min" and value < cap:
                logger.info(
                    "Budget filter: %s below %s %s floor, bar-only drop.",
                    (figure or {}).get("text"), cap, currency,
                )
                continue
        except (TypeError, ValueError):
            pass
        kept.append(figure)
    return kept


def _bind_figure(
    *,
    text: str,
    match_text: str,
    value: float,
    unit: str,
    symbol: str,
    match_start: int,
    match_end: int,
    ref: int,
    queried_entities: list,
    scale: Optional[float] = None,
    count_noun: Optional[str] = None,
) -> dict:
    """Attach semantic binding to one raw figure (Phase 4/5, hardened H2/H3).

    Contract:
    - Display `context` stays a short 200-char window around the match.
    - Semantic binding (WHO/WHAT/WHEN/UNIT/CURRENCY/DEFINITION) NEVER uses
      that truncated window: entity/metric/currency/period/frequency are
      resolved against the FULL snippet text (plus a wide ±1000-char
      fallback), so a metric cue 300 chars away still binds correctly.
    - Every figure records WHO/WHAT/WHEN/UNIT/CURRENCY/DEFINITION with
      explicit fields; comparison_eligible follows the strict H2 contract
      (entity + specific metric + numeric value + unit + explicit ISO
      currency for money + definition + source_type). Raw figures are
      always preserved -- ineligible means prose-only, never dropped.
    """
    # Display window (short, human-readable citation context). Snap to word
    # boundaries so rows never start/end mid-word ("b outlook", "Informati"),
    # and strip markdown/table artifacts ("||", "####", "[...]") that leak
    # from raw web snippets into the figures table.
    before = max(0, match_start - 120)
    while before > 0 and before < len(text) and not text[before].isspace():
        before += 1
    after = min(len(text), match_end + 80)
    while after > match_end and after < len(text) and not text[after - 1].isspace():
        after -= 1
    window = " ".join(text[before:after].split())
    window = re.sub(r"\[[^\]]*\]", " ", window)  # [...] / [n] remnants
    window = window.replace("|", " ")
    window = re.sub(r"#+\s*", "", window)  # markdown headings
    window = re.sub(r"[-–—]{2,}", " ", window)  # table separators
    window = re.sub(r"\s{2,}", " ", window).strip()
    if len(window) > _FIGURE_CONTEXT_CHARS:
        cut = window[:_FIGURE_CONTEXT_CHARS]
        snap = cut.rfind(" ")
        window = (cut[:snap] if snap > _FIGURE_CONTEXT_CHARS - 40 else cut).strip()
    # Binding scope: the FULL snippet text (never truncated before binding).
    # A wide local window is checked first for precision, then the full
    # text as recall backstop so distant cues still bind.
    wide_before = max(0, match_start - 1000)
    wide_after = min(len(text), match_end + 1000)
    wide_scope = " ".join(text[wide_before:wide_after].split())
    full_scope = " ".join(str(text or "").split())
    entity: Optional[str] = None
    entity_source: Optional[str] = None
    try:
        if figure_entity_label is not None:
            entity = figure_entity_label(wide_scope, queried_entities or [])
            if entity is None:
                entity = figure_entity_label(full_scope, queried_entities or [])
    except Exception:
        entity = None
    if entity is not None and queried_entities:
        # Bound against a query-named entity: strictest provenance.
        entity_source = "queried"
    if entity is None and not queried_entities:
        # No known entities in play: nearest product phrase beats the
        # window-leading fallback (the fallback names article headings
        # like "Smartprix:", proximity names the priced item). Guarded to
        # empty-entity queries so strict attribution never weakens.
        try:
            pos = window.find(match_text) if match_text else -1
            phrase = _nearest_product_phrase(
                window, pos if pos >= 0 else len(window), full_scope
            )
        except Exception:
            phrase = None
        if phrase:
            entity, entity_source = phrase, "phrase"
    if entity is None and not queried_entities:
        # Still nothing: fall back to the window's own subject so "Acme
        # raised $X" vs "Globex sold for $Y" stay attributable.
        entity = _fallback_subject(window) or _fallback_subject(wide_scope)
        if entity is not None:
            entity_source = "fallback"
    metric: Optional[str] = None
    if count_noun:
        # The count noun adjacent to the match is the most precise WHAT
        # ("50M subscribers" -> subscribers), beating distant cues in the
        # wide scope ("revenue" three sentences away must not rebind it).
        metric = count_noun
    else:
        try:
            if figure_metric_label is not None:
                metric = figure_metric_label(wide_scope) or figure_metric_label(full_scope)
        except Exception:
            metric = None
    if metric is None and unit == "money" and (entity_source == "phrase"):
        # A bare currency amount next to a named product with no other cue
        # is its price ("Zeb-Duke Pro Wireless Headphones ₹1,449"). The
        # phrase requirement keeps untyped "$202B somewhere" ineligible.
        metric = "price"
    currency: Optional[str] = None
    if unit == "money":
        try:
            word_match = _FIGURE_CURRENCY_WORD_RE.search(wide_scope) or _FIGURE_CURRENCY_WORD_RE.search(full_scope)
            if normalize_currency is not None:
                currency = normalize_currency(
                    symbol or None,
                    word_match.group(1) if word_match else None,
                )
        except Exception:
            currency = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    try:
        years = [match.group(1) for match in _FIGURE_YEAR_RE.finditer(wide_scope)]
        if not years:
            years = [match.group(1) for match in _FIGURE_YEAR_RE.finditer(full_scope)]
        if years:
            period_start = years[0]
            period_end = years[-1]
    except Exception:
        pass
    frequency: Optional[str] = None
    try:
        freq_match = _FIGURE_FREQUENCY_RE.search(wide_scope) or _FIGURE_FREQUENCY_RE.search(full_scope)
        if freq_match:
            frequency = freq_match.group(1).lower()
    except Exception:
        pass
    # Definition: the specific metric cue IS the definition when no
    # separate definition phrase exists (funding vs startup_cost vs
    # revenue vs cost are different definitions and must never equate).
    definition: Optional[str] = str(metric) if metric else None
    resolved_scale: Optional[float] = None
    try:
        resolved_scale = float(scale) if scale is not None else 1.0
    except (TypeError, ValueError):
        resolved_scale = 1.0
    candidate = {
        "text": match_text,
        "value": value,
        "unit": unit,
        "context": window,
        "full_context": full_scope[:2000],
        "ref": ref,
        "entity": entity,
        "entity_source": entity_source,
        "metric": metric,
        "currency": currency,
        "count_noun": count_noun,
        "scale": resolved_scale,
        "period_start": period_start,
        "period_end": period_end,
        "frequency": frequency,
        "definition": definition,
        "source_type": "snippet",
        "comparison_eligible": False,
    }
    # Strict H2 eligibility (never just entity+metric): money needs an
    # explicit ISO currency; generic metric labels fail.
    try:
        if is_figure_comparison_eligible is not None:
            eligible, _ = is_figure_comparison_eligible(candidate)
        else:
            eligible = bool(entity and metric) and not (
                unit == "money" and not currency
            ) and str(metric or "").lower() not in ("money", "currency", "unknown", "")
        candidate["comparison_eligible"] = bool(eligible)
    except Exception:
        candidate["comparison_eligible"] = False
    return candidate


def _figure_label(
    figure: dict, max_len: int = 32, queried_entities: Optional[list] = None
) -> str:
    """Chart label = resolved chart entity (attributed datum).

    Prefers the bound entity name, then a queried entity mentioned near the
    figure, then the nearest product phrase — always a WHO from the
    evidence. Falls back to the figure's own cited text, never a sentence
    fragment ("Buy", "This", "vancements in ..."). Callers building bars
    filter out figures with no resolvable entity first.
    """
    resolved = _resolve_chart_entity(figure or {}, queried_entities or [])
    if resolved:
        label = " ".join(str(resolved).split())
        if len(label) > max_len:
            cut = label[:max_len]
            snap = cut.rfind(" ")
            label = (cut[:snap] if snap > max_len - 12 else cut).strip()
        return label
    return str((figure or {}).get("text", "") or "").strip()[:max_len]


def _attributed_pool(figures: list, query: str) -> tuple:
    """Chartable pool: budget-filtered figures with a resolved entity,
    deduped by entity (first occurrence wins). Returns (figures,
    {id(figure): entity}, queried_entities). Shared by the bar gate and
    the outer pre-check so both judge the same pool."""
    queried_entities: list = []
    try:
        if decompose_comparison_query is not None and query:
            queried_entities = (
                decompose_comparison_query(query) or {}
            ).get("entities", []) or []
    except Exception:
        queried_entities = []
    pool = _apply_budget_constraint(figures, query or "")
    attributed: list = []
    seen_entities: set = set()
    for fig in pool:
        try:
            ent = _resolve_chart_entity(fig, queried_entities)
        except Exception:
            ent = None
        if not ent:
            continue
        key = ent.strip().lower()
        if key in seen_entities:
            continue
        seen_entities.add(key)
        attributed.append((fig, ent))
    return (
        [fig for fig, _ in attributed],
        {id(fig): ent for fig, ent in attributed},
        queried_entities,
    )


# Recommended items (books, products, tools): verbatim title phrases from
# the evidence — double-quoted titles ("The Hundred-Page Machine Learning
# Book") and "Title by Author" mentions ("AI Engineering by Chip Huyen").
# Single quotes are skipped (apostrophes collide). A quote must start
# uppercase ("thanks for reading it!..." never qualifies).
_RECOMMENDED_QUOTE_RE = re.compile(r'"([^"<>]{8,80})"')
_RECOMMENDED_BY_RE = re.compile(
    r"\b([A-Z][\w&',\-:; ]{2,60}?)\s+by\s+([A-Z][\w.\-']+(?:\s+[A-Z][\w.\-']+){0,2})"
)
_SYNTH_ITEMS_MAX = 8


_SOURCES_TABLE_TITLES = frozenset({"sources cited", "sources", "cited sources"})
_SOURCES_TABLE_COLUMNS = frozenset(
    {"source", "sources", "title", "provider", "url", "link"}
)

__all__ = [
    "_BUDGET_KEYWORDS",
    "_CHART_CATEGORY_WORDS",
    "_CHART_COLORS",
    "_CHART_ENTITY_STOPWORDS",
    "_CHART_RETAILER_PREFIXES",
    "_CHART_TRAILING_LABELS",
    "_CHART_TRAILING_SPECS",
    "_CHART_WORD_RE",
    "_FIGURE_CONTEXT_CHARS",
    "_FIGURE_COUNT_NOUNS",
    "_FIGURE_COUNT_NOUN_TO_METRIC",
    "_FIGURE_COUNT_RE",
    "_FIGURE_CURRENCY_WORD_RE",
    "_FIGURE_FREQUENCY_RE",
    "_FIGURE_MONEY_RE",
    "_FIGURE_NOUN_WINDOW",
    "_FIGURE_PERCENT_RE",
    "_FIGURE_SCALE",
    "_FIGURE_SCALED_BARE_RE",
    "_FIGURE_SUBJECT_RE",
    "_FIGURE_YEAR_RE",
    "_RECOMMENDED_BY_RE",
    "_RECOMMENDED_QUOTE_RE",
    "_SOURCES_TABLE_COLUMNS",
    "_SOURCES_TABLE_TITLES",
    "_SYNTH_FIGURES_MAX",
    "_SYNTH_FINANCIAL_MAX_ROWS",
    "_SYNTH_ITEMS_MAX",
    "_SYNTH_SERIES_MAX_POINTS",
    "_SYNTH_SOURCES_MAX_ROWS",
    "_SYNTH_TABLE_MAX_COLS",
    "_SYNTH_TABLE_MAX_ROWS",
    "_apply_budget_constraint",
    "_attributed_pool",
    "_bind_figure",
    "_clean_chart_phrase",
    "_fallback_subject",
    "_figure_label",
    "_figures_from_snippets",
    "_interleave_entities",
    "_is_generic_entity",
    "_nearest_count_noun",
    "_nearest_product_phrase",
    "_query_price_constraint",
    "_resolve_chart_entity",
    "_window_restates_constraint",
]
