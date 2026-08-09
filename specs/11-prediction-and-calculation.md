# 11 — Prediction, Calculation & Benchmarking

**Status:** ⚠️ §3.1 done (B4), §3.2–3.4 not started. §3.1's deterministic statistical calculations
(§3.1 + §5's no-new-infrastructure constraint) are implemented in `app/services/data/stats.py` and
consumed by B4's `POST /chat` — the LLM narrates precomputed numbers, never computes them. §3.4's
"ask, don't guess" interaction pattern (§4) is implemented in the pipeline's SYSTEM_PROMPT; its
first concrete benchmarking use lands with B6. §3.2 (forecasting) and §3.3 (what-if) are the next
build steps and extend the same `/chat` route.
**Source files:** `app/services/data/stats.py`, `app/services/llm/langchain_pipeline.py`,
`app/routes/chat.py`

---

## 1. Problem Statement

The original scope covered *descriptive* analysis only — what happened, and why. The product's
actual scope is broader: **forecast future trends, model what-if scenarios, compute statistics, and
benchmark against similar companies.** These are fundamentally different from descriptive insight
generation in one critical way: they involve real arithmetic, and arithmetic done by an LLM is a
guess, not a calculation. This spec exists specifically to draw that line clearly before any of this
gets built.

## 2. Core design principle — compute in code, narrate with the LLM

**The LLM never does the arithmetic.** For every capability below, the actual number comes from a
deterministic calculation in Python (pandas/numpy on the fetched data), and the LLM's only job is to
explain what that already-computed number means in plain language. This isn't a style preference —
it's the difference between a forecast a business owner can trust and one that's a plausible-sounding
guess. Concretely: the backend computes `next_month_revenue = trend_fn(historical_data)` first, then
passes that number *into* the prompt as a fact to narrate, never asks the LLM to produce the number
itself.

## 3. The four capabilities

### 3.1 Statistical calculations
Averages, growth rates (period-over-period %), ratios (margin %, etc.) computed directly from the
user's own data via pandas. Lowest risk, lowest complexity — build this first, since forecasting
(3.2) is layered on top of it.

### 3.2 Trend forecasting
Project a metric forward (e.g. "next month's revenue") from historical data. Start simple: linear
regression or moving-average extrapolation over the existing time series — this doesn't require a
heavy forecasting library to be useful, and a simple method that's honestly labeled as simple is
better than a complex one that overstates its own confidence. Always surface the method used and a
confidence range, never a bare single number presented as certain.

### 3.3 What-if scenarios
Parameterized recalculation — "what happens to revenue if I raise price 10%." Requires identifying
which columns in the user's data a scenario parameter maps to (price, quantity, cost) and
recomputing deterministically. Simplest honest v1: assume quantity is unaffected by the price change
(clearly state this assumption in the answer) rather than modeling price elasticity, which needs
data this product doesn't have. Get the simple version right and visibly labeled before attempting
anything more sophisticated.

### 3.4 Competitor / company benchmarking
Comparing the user's business against a similar company. **This is the highest-risk capability
here**, because real financial data for private companies usually isn't public — a confident-sounding
comparison built on guessed numbers is exactly the failure mode `10-trust-safety-compliance.md`
exists to prevent. This is the first real, concrete use of the interaction pattern in §4 below.

## 4. Core interaction pattern: ask, don't guess

This is a general pipeline behavior, not a one-off rule for benchmarking — apply it anywhere the
pipeline doesn't have enough information to answer well:

**When the pipeline can't answer confidently, it asks the user a clarifying question instead of
guessing.** Concretely for benchmarking: if a user asks "how do I compare to [Competitor]," and no
reliable data exists for that competitor, the response is not a fabricated number — it's a question
back to the user, offered as quick-pick options where possible (mirroring the pattern used
throughout this whole design conversation): "I don't have reliable public data for [Competitor].
Want me to (a) search for whatever public information exists, clearly marked as incomplete, (b)
compare against industry-average benchmarks instead, or (c) you provide their numbers directly?"

This same pattern should extend to other ambiguous pipeline moments — an ambiguous date range
("this quarter" — which one?), a column name that could map to two different business concepts, a
what-if parameter that isn't clearly present in the data. Anywhere the alternative is a guess dressed
up as an answer, ask instead. Implementation-wise, this means `PipelineOutput` needs a mode for "this
is a clarifying question, not a final answer" — a new optional field (e.g. `clarification: { question:
str, options: list[str] } | None`) that the frontend renders as a quick-pick prompt rather than a
chat answer, closing the loop back into the same request once answered.

## 5. Constraints

- No new infrastructure required for 3.1–3.3 (pandas on already-fetched data). 3.4 (benchmarking)
  may eventually want a web-search tool call for the "best-effort public data" option in §4 — treat
  that as a follow-on, not a prerequisite to ship the rest of this spec.
- Forecasting and what-ifs both depend on having enough historical data to be meaningful (a
  time-series forecast from three data points isn't one) — the pipeline should recognize insufficient
  data and say so, not force an answer.
- These live in the **same chat interface** as descriptive questions — no separate "predictions" UI
  section. A user shouldn't need to learn two places to ask questions of one product.

## 6. Acceptance Criteria

- [x] Every numeric answer this module produces is traceable to a deterministic calculation, never
      an LLM-generated number — verifiable by inspecting the prompt sent to the LLM (the number is
      an input to the prompt, not something asked of it). *(`stats.py` computes
      averages/totals/growth/ratios; `run_pipeline` receives `computed_numbers` as data to narrate,
      and the SYSTEM_PROMPT forbids calculating. §3.2–3.3 will extend this to forecasts/what-ifs.)*
- [x] The clarifying-question pattern (§4) is implemented as a first-class `PipelineOutput` mode,
      not a special case bolted onto benchmarking alone. *(`clarification: ClarificationRequest | None`
      is a real pipeline output mode driven by the ask-don't-guess prompt, B4; benchmarking's first
      concrete use lands with B6.)*
- [ ] A forecast always states its method and a confidence range, never a bare number.
      *(§3.2, pending.)*
- [ ] A what-if scenario always states its assumptions (e.g. "assumes quantity is unaffected")
      alongside the result.
- [ ] Benchmarking never presents a guessed competitor number as fact — it either clearly labels
      data as best-effort/incomplete, falls back to industry averages, or asks the user for the
      competitor's numbers directly.
