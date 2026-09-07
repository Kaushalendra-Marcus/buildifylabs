"""Live pipeline eval: real Groq keys, canned evidence, property checks.

Runs the decision loop end-to-end (judge -> narrate -> guarantee) against a
handful of generic scenarios and asserts *properties* (visuals present,
citations resolve, no repeated clarification, fallback only when evidence is
absent) rather than exact wording, so model phrasing drift never breaks it.

Usage (real keys required):
    GROQ_API_KEY=... [GROQ_API_KEY2=...] \
      python scripts/eval_pipeline.py

Needs DATABASE_URL/JWT_SECRET/etc. only because importing app.config
validates them — dummy values are filled in below; only Groq keys must be
real. Exits non-zero on any failing case.
"""
import asyncio
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "eval-secret")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PORT", "1025")
os.environ.setdefault("SMTP_USER", "eval")
os.environ.setdefault("SMTP_PASS", "eval")
os.environ.setdefault("EMAIL_FROM", "eval@example.com")
os.environ.setdefault("CONTACT_FORM_RECIPIENT_EMAIL", "eval@example.com")
os.environ.setdefault("GOOGLE_CLIENT_ID", "eval")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.llm.langchain_pipeline import run_pipeline  # noqa: E402

ROWS = [
    {"created_at": "2024-01-01", "revenue": 100, "region": "east"},
    {"created_at": "2024-01-02", "revenue": 250, "region": "west"},
    {"created_at": "2024-01-03", "revenue": 175, "region": "east"},
    {"created_at": "2024-01-04", "revenue": 300, "region": "west"},
]

COMPUTED = {
    "row_count": 4,
    "averages": {"revenue": 206.25},
    "totals": {"revenue": 825.0},
}

SNIPPETS = [
    "Acme raised $50M in 2024 to expand its AI support tooling.",
    "Globex launched a usage-based pricing tier in early 2025.",
    "Analysts expect AI support tooling to double by 2027.",
]

SOURCES = [
    {"title": "Acme funding", "url": "https://a.example", "provider": "Tavily"},
    {"title": "Globex pricing", "url": "https://b.example", "provider": "Tavily"},
    {"title": "Analyst outlook", "url": "", "provider": "DuckDuckGo"},
]


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    return condition


async def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is required (real key). Refusing to run.")
        return 1
    ok = True

    out = await run_pipeline(
        user_query="how is revenue trending?",
        db_data=ROWS,
        computed_numbers=COMPUTED,
    )
    kinds = [v.visual_type for v in out.visuals]
    ok &= check("data answer has visuals", bool(kinds), f"kinds={kinds}")
    ok &= check("data answer not a clarification", out.clarification is None)
    ok &= check("thinking trace present", bool(out.thinking))

    out = await run_pipeline(
        user_query="show revenue as a bar chart", db_data=ROWS
    )
    graphs = [v for v in out.visuals if v.visual_type == "graph"]
    ok &= check(
        "requested bar shape honored",
        any(g.props.get("chart_type") == "bar" for g in graphs),
        f"got={[g.props.get('chart_type') for g in graphs]}",
    )

    out = await run_pipeline(
        user_query="startup funding news",
        db_data=[],
        source_scope="live_web",
        news_context=SNIPPETS,
        web_sources=SOURCES,
    )
    import re

    cited = {int(n) for n in re.findall(r"\[(\d+)\]", out.answer)}
    ok &= check(
        "citations resolve to listed snippets",
        not cited or max(cited) <= len(SNIPPETS),
        f"cited={sorted(cited)} snippets={len(SNIPPETS)}",
    )
    ok &= check("web answer has visuals", bool(out.visuals))

    prior = "What metric should I use?"
    out = await run_pipeline(
        user_query="still deciding",
        db_data=[],
        prior_clarification=prior,
    )
    repeated = out.clarification is not None and prior.lower() in (
        out.clarification.question or ""
    ).lower()
    ok &= check("never repeats the prior question", not repeated)

    out = await run_pipeline(user_query="q", db_data=[])
    ok &= check(
        "empty evidence degrades honestly",
        out.clarification is not None or out.confidence <= 0.35,
    )

    print("EVAL " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
