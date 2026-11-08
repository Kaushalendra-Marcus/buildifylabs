"""Generate a demo dataset for BuildifyLabs uploads and Loom demos.

Writes `data/demo/demo_retail_sales.csv`: 56 days of a small retail
business (Aug 6 - Oct 30, 2026), daily rows across 4 product categories
and 2 sales channels, revenues in rupee strings ("₹48,290") exactly as a
real POS export would look - which exercises the ingestion cleaning
pass (₹/comma stripping, date coercion, blank + duplicate-row drops).

The baked-in story is deterministic (seeded) so demos are repeatable:
  - weeks 1-7        steady growth
  - Sep 28 - Oct 4   promo week: revenue jumps ~+35%
  - Oct 24 - Oct 30  a competitor opens and Bakery collapses (~-40%),
    so "why did revenue change last week?" has a real, findable answer.

Run:  cd Backend && python scripts/demo_csv.py
Optional:  --end 2026-11-20 (regenerates the whole window ending there)
Optional:  --out data/demo/demo_retail_sales.csv
"""
import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

# --- data shape -------------------------------------------------------------

CATEGORIES = [
    # (name, natural share of daily revenue, avg price per unit)
    ("Beverages", 0.42, 180),
    ("Bakery", 0.28, 120),
    ("Dairy", 0.18, 90),
    ("Merch", 0.12, 350),
]
CHANNELS = [("Store", 0.62), ("Online", 0.38)]
WEEKEND_FACTOR = {4: 1.12, 5: 1.24, 6: 1.18}  # Fri, Sat, Sun (Mon=0)

PROMO_START, PROMO_END = date(2026, 9, 28), date(2026, 10, 4)
BAKERY_DROP_START = date(2026, 10, 24)  # competitor opens; the last-week dip
SEED = 7


def generate_rows(end_date: date, days: int) -> list[list[str]]:
    """One row per (day, category, channel), with the baked-in storyline."""
    rng = random.Random(SEED)
    rows: list[list[str]] = []
    for day_offset in range(days):
        d = end_date - timedelta(days=days - 1 - day_offset)
        weekday = d.weekday()
        base = 26000 * (1 + 0.004 * day_offset)
        if PROMO_START <= d <= PROMO_END:
            base *= 1.35
        for name, share, price in CATEGORIES:
            cat_rev = base * share * (0.82 + 0.36 * rng.random())
            bakery_dropping = name == "Bakery" and d >= BAKERY_DROP_START
            for channel_name, channel_share in CHANNELS:
                factor = WEEKEND_FACTOR.get(weekday, 1.0) if channel_name == "Store" else 1.0
                rev = cat_rev * channel_share * factor
                if bakery_dropping:
                    rev *= 0.60
                rev = int(rev // 10) * 10  # round to tens
                units = max(1, int(rev / price))
                rows.append(
                    [
                        d.isoformat(),
                        name,
                        channel_name,
                        f"{units:,}",      # "1,240"  -> cleaned to an int
                        f"₹{rev:,}",       # "₹48,290" -> cleaned to a float
                    ]
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the BuildifyLabs demo CSV.")
    parser.add_argument("--end", default=date(2026, 10, 30).isoformat(), help="last date (ISO)")
    parser.add_argument("--days", type=int, default=56, help="number of days of history")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "demo" / "demo_retail_sales.csv"))
    args = parser.parse_args()

    raw = generate_rows(date.fromisoformat(args.end), args.days)

    # Deliberately include classic dirty rows for the cleaning pass to fix:
    mid_duplicate = list(raw[len(raw) // 2])  # exact duplicate of a mid row
    dataset = raw[: len(raw) // 2] + [mid_duplicate, []] + raw[len(raw) // 2 :]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "category", "channel", "units_sold", "revenue"])
        writer.writerows(dataset)  # the [] entry writes a blank line

    print(f"wrote {out_path}")
    print(f"  clean data rows : {len(raw)}")
    print(f"  dirty rows added: 1 blank, 1 duplicate (cleaner should drop both)")
    print(f"  date range      : {raw[0][0]} .. {raw[-1][0]} ({args.days} days)")
    print(f"  dims            : {len(CATEGORIES)} categories x {len(CHANNELS)} channels")


if __name__ == "__main__":
    main()
