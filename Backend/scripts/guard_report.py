"""Hallucination-guard fire-rate report, from production QueryLogs.

Every answer's ``research_state["guards"]`` lists the guards that fired for it
(untraceable prose numbers, unsupported citations, stripped sentences, fail-closed
caps, neutralized injection text, suspicious SQL results, ...). This aggregates
them over a window and compares against the user flag rate, answering:

* how often does each guard fire (a rising rate is an early warning);
* are flagged answers the ones the guards fired on (do the guards catch what
  users catch?), and
* is the confidence score calibrated (does flagged-rate fall as confidence rises?).

Usage (from Backend/, with the production DATABASE_URL in .env):
    python scripts/guard_report.py             # last 7 days
    python scripts/guard_report.py --days 30
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.db.database import get_session_maker  # noqa: E402
from app.db.models.query_logs import QueryLogs  # noqa: E402

_BUCKETS = ((0.0, 0.35, "low  (<0.35)"), (0.35, 0.65, "mid  (0.35-0.65)"), (0.65, 1.01, "high (>=0.65)"))


def _guard_name(event):
    return str(event).split(":", 1)[0].strip()


def summarize(records):
    """records: iterable of (response_json_str, flagged_bool). Pure, testable."""
    total = flagged_total = with_guard = flagged_with_guard = 0
    fired = Counter()
    bucket_counts = {label: [0, 0] for _, _, label in _BUCKETS}  # [answers, flagged]
    for response, flagged in records:
        try:
            payload = json.loads(response or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        total += 1
        flagged = bool(flagged)
        flagged_total += int(flagged)
        events = []
        state = payload.get("research_state")
        if isinstance(state, dict):
            events = [e for e in (state.get("guards") or []) if e]
        names = {_guard_name(e) for e in events}
        for name in names:
            fired[name] += 1
        if names:
            with_guard += 1
            flagged_with_guard += int(flagged)
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            for low, high, label in _BUCKETS:
                if low <= confidence < high:
                    bucket_counts[label][0] += 1
                    bucket_counts[label][1] += int(flagged)
                    break
    return {
        "total": total,
        "flagged": flagged_total,
        "answers_with_guard": with_guard,
        "flagged_with_guard": flagged_with_guard,
        "fired": fired,
        "buckets": bucket_counts,
    }


def _pct(part, whole):
    return f"{(part / whole * 100):.1f}%" if whole else "n/a"


def render(summary, days):
    total = summary["total"]
    lines = [f"Guard report - last {days} day(s), {total} answer(s)", ""]
    lines.append("Guard fire rates (share of answers where the guard fired):")
    if not summary["fired"]:
        lines.append("  (no guard events recorded)")
    for name, count in summary["fired"].most_common():
        lines.append(f"  {name:<34} {count:>6}  {_pct(count, total):>7}")
    lines += ["", "Do the guards catch what users flag?"]
    flagged, with_guard = summary["flagged"], summary["answers_with_guard"]
    fwg = summary["flagged_with_guard"]
    lines.append(f"  flag rate overall:                {_pct(flagged, total)}")
    lines.append(f"  flag rate when a guard fired:     {_pct(fwg, with_guard)}")
    lines.append(f"  flag rate when none fired:        {_pct(flagged - fwg, total - with_guard)}")
    lines += ["", "Confidence calibration (flag rate should fall as confidence rises):"]
    for _, _, label in _BUCKETS:
        count, flg = summary["buckets"][label]
        lines.append(f"  {label:<18} {count:>6} answers  flagged {_pct(flg, count):>7}")
    return "\n".join(lines)


async def main(days):
    maker = get_session_maker()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with maker() as db:
        result = await db.execute(
            select(QueryLogs.response, QueryLogs.flagged).where(QueryLogs.created_at >= since)
        )
        records = [(row[0], row[1]) for row in result.all()]
    print(render(summarize(records), days))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hallucination-guard fire-rate report.")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(main(args.days))
