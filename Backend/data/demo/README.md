# BuildifyLabs demo dataset

`demo_retail_sales.csv` - 56 days (Sep 5 - Oct 30, 2026) of a small retail
business: 4 product categories x 2 sales channels, `₹`-formatted revenue,
comma-formatted units. One duplicate row and one blank row are included on
purpose; the ingestion cleaner drops both silently (that's part of the demo).

## Baked-in story (deterministic, seed 7)

| Period | What's in the data |
|---|---|
| Weeks 1-7 | steady growth, weekend bumps in Store channel |
| Sep 28 - Oct 4 | promo week, revenue ~+35% |
| Oct 24 - Oct 30 | a competitor opens; Bakery collapses ~-34% while the rest holds - so "why did revenue change last week?" has a findable answer |

## Verified (run it yourself)

```bash
cd Backend && python scripts/verify_demo_csv.py    # real parse -> ingest -> execute_sql, 23 checks
python scripts/demo_csv.py                         # regenerate (optional: --end YYYY-MM-DD)
```

Ground truth baked into the file (checked by the verifier):

- total revenue ₹17,17,570 across 448 clean rows
- last week ₹2,07,190 vs the week before ₹2,25,560  →  **−8.1% week over week**
- driver: Bakery ₹60,790 → ₹40,310  →  **−33.7%**, everything else ~flat
- top category overall: Beverages (₹7,22,870); worst day last week: Oct 29

## Using it in a demo

1. Sign in (or continue as guest - and note guests can ask but not upload).
2. `/app/data` → upload `demo_retail_sales.csv` (processing → completed).
3. In `/app/chat` ask, with source scope "Your data":
   - "Why did revenue change last week?" (the Loom money shot)
   - "Show my top categories" / "Compare Store vs Online last week"
   - "What's my trend over the last 8 weeks?" then pin the answer to Reports
