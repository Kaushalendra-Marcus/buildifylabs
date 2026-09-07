"""Review recent model failures: flagged answers + generic fallbacks.

Reads QueryLogs directly (needs a real DATABASE_URL) and prints what to fix
next: user-flagged answers first, then turns that died in a fallback. Run
weekly; every entry is a prompt/guarantee gap with a real user behind it.

Usage:
    DATABASE_URL=... python scripts/review_feedback.py [limit]
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET", "review-secret")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PORT", "1025")
os.environ.setdefault("SMTP_USER", "review")
os.environ.setdefault("SMTP_PASS", "review")
os.environ.setdefault("EMAIL_FROM", "review@example.com")
os.environ.setdefault("CONTACT_FORM_RECIPIENT_EMAIL", "review@example.com")
os.environ.setdefault("GOOGLE_CLIENT_ID", "review")

from sqlalchemy import desc, select  # noqa: E402

from app.db.database import get_session_maker  # noqa: E402
from app.db.models.query_logs import QueryLogs  # noqa: E402

FALLBACK_MARKERS = (
    "Couldn't produce a reliable answer",
    "Sorry, I could not process",
    "I had trouble understanding",
    "Something went wrong",
    "haven't uploaded any data",
)


def _summarize(log) -> dict:
    try:
        response = json.loads(log.response or "{}")
    except (ValueError, TypeError):
        response = {}
    answer = str(response.get("answer", ""))[:160]
    clarification = (response.get("clarification") or {}).get("question", "")
    visuals = [v.get("visual_type") for v in response.get("visuals", [])]
    return {
        "query": log.query,
        "answer": answer,
        "clarification": clarification,
        "visuals": visuals,
        "flagged": log.flagged,
    }


async def main(limit: int = 20) -> int:
    maker = get_session_maker()
    async with maker() as session:
        rows = (
            await session.execute(
                select(QueryLogs).order_by(desc(QueryLogs.created_at)).limit(500)
            )
        ).scalars()
        logs = list(rows)

    flagged = [log for log in logs if log.flagged][:limit]
    fallbacks = [
        log
        for log in logs
        if not log.flagged
        and any(m in str((json.loads(log.response or "{}") or {}).get("answer", "")) for m in FALLBACK_MARKERS)
    ][:limit]

    print(f"== Flagged by users ({len(flagged)}) ==")
    for log in flagged:
        item = _summarize(log)
        print(f"- Q: {item['query']}\n  A: {item['answer']}\n  visuals={item['visuals']}")
    print(f"\n== Generic fallbacks ({len(fallbacks)}) ==")
    for log in fallbacks:
        item = _summarize(log)
        print(f"- Q: {item['query']}\n  A: {item['answer']}")
    if not flagged and not fallbacks:
        print("Nothing to review - no flags or fallbacks in the window.")
    return 0


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    raise SystemExit(asyncio.run(main(limit)))
