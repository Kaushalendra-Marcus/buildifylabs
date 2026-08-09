# Type contracts — translated from the backend's Pydantic schemas

Open only the section for the feature you're building. These mirror `Backend/app/schemas/*.py` and
`Backend/app/services/llm/langchain_pipeline.py`. Full field-level detail and every error case live
in the linked spec — this is the shape to type your API layer against, not the full contract.

## Auth — `specs/01-authentication.md` (live today)

```ts
interface AuthUserResponse {
  id: string;                          // UUID
  email: string | null;
  name: string | null;
  plan: "guest" | "free" | "pro";
}

interface AuthResponse {
  user: AuthUserResponse;
  access_token: string;
  refresh_token: string | null;
  token_type: "bearer";
}

interface TokenResponse {              // POST /auth/refresh response — no `user` field
  access_token: string;
  refresh_token: string | null;
  token_type: "bearer";
}

interface SignupRequest { email?: string; name?: string; password?: string; }
interface SigninRequest { email: string; password: string; }
interface GuestRequest { device_id?: string; }
interface GoogleRequest { token: string; }         // Google ID token from Google Identity Services
interface RefreshRequest { refresh_token: string; }
interface ForgotPasswordRequest { email: string; }
interface ResetPasswordRequest { token: string; new_password: string; }   // min 8 chars
```

Auth is fully stateless JWT — access token TTL 60 min, refresh TTL 7 days, no server session to fall
back on. Decide deliberately between `localStorage` and a cookie-backed approach for token storage
(the backend currently expects a Bearer header, not a cookie, so cookie storage would need a backend
change too) — don't default to `localStorage` without weighing the tradeoff for a product handling
business data.

## Upload — `specs/04-file-upload-ingestion.md` (live: `POST /files/upload`, `GET /files*`, B3)

```ts
interface FileResponse {
  id: string;
  file_name: string;
  file_type: string;
  file_size: number | null;
  status: "processing" | "completed" | "failed";
  pinecone_namespace: string | null;
  error: string | null;                         // stored failure reason when status === "failed"
  created_at: string;                           // ISO 8601
}
```

Accepted types: `.csv`, `.pdf`, `.xlsx` only, checked by both extension and MIME type server-side —
send the real file, don't rely on spoofing `content_type`. Size caps: `free` ≤ 3 MB, `pro` ≤ 10 MB.
Guest users are rejected entirely (403) — don't show the upload UI to guests at all.

## Chat / insight pipeline — `specs/06-ai-insight-pipeline.md` (route live: `POST /chat`, B4)

```ts
type VisualType =
  | "metric" | "graph" | "table" | "comparison"
  | "insight" | "alert" | "status";

interface VisualOutput {
  visual_type: VisualType;              // server-enforced Literal since B4, but keep a defensive
                                         // fallback for unrecognized values anyway
  props: Record<string, unknown>;        // shape depends on visual_type — see src/lib/schemas/visuals.ts
  title: string;
}

interface ClarificationRequest {         // alternate response mode (specs/10 §2 "ask, don't guess")
  question: string;
  options: string[];                     // quick-pick choices; empty if none fit
}

interface PipelineOutput {               // POST /chat returns this directly
  answer: string;
  visuals: VisualOutput[];
  insights: string[];
  summary: string;
  root_causes: string[];                 // hedged causal language by design (specs/10 §2)
  recommendations: string[];
  news_context: string[];                // empty for now — news lands with specs/07
  anomalies: string[];
  confidence: number;                    // Field(ge=0.0, le=1.0), bounded server-side
  clarification: ClarificationRequest | null;  // non-null ⇒ the other answer fields are empty:
                                         // render as a quick-pick prompt, not a chat answer
  sql_query: string | null;              // exact SQL behind this answer (traceability)
  data_preview: Array<Record<string, unknown>> | null;  // raw row slice the SQL ran on
  query_log_id: string | null;           // UUID — drives "show the query" + flagging
}

interface ChatRequest {
  query: string;
  source_scope?: "own_data" | "live_web" | "both";   // only "own_data" is fully supported today
  company_name?: string | null;          // reserved for benchmarking (specs/11), unused in B4
}

interface FlagRequest { query_log_id: string; }          // POST /chat/flag
interface FlagResponse { query_log_id: string; flagged: boolean; }
```

Per-type `props` shape (authoritative detail is `src/lib/schemas/visuals.ts`; these are what the
backend's prompt tells the model to produce):

- `metric` → `{ label: string, value: number, change_pct: number | null, direction: "up" | "down" | "flat" }`
- `graph` → `{ chart_type: "line" | "bar" | "pie" | "area", labels: string[], datasets: [{ name: string, values: number[] }] }`
- `table` → `{ columns: string[], values: Array<Array<string | number>> }`
- `comparison` → `{ value: number, baseline: number, groups: [{ label: string, value: number }] }`
- `insight` → `{ text: string, context: string }`
- `alert` → `{ level: "info" | "warning" | "critical", summary: string, reason: string }`
- `status` → `{ state: "on_track" | "at_risk" | "off_track", detail: string }`

Build one renderer per `visual_type` (7 total) plus the fallback for unrecognized values. When
`clarification` is non-null, don't render charts — render the quick-pick prompt. The per-type prop
shapes are authoritative in `src/lib/schemas/visuals.ts` — the single source of truth landed in F0
and is what the renderers (F4) are built against; this section only summarizes them.

## Payment — `specs/03-payment-verification.md` (designed, not implemented)

```ts
interface CreateOrderResponse {         // POST /payments/create-order (proposed)
  order_id: string;
  amount: number;                        // paise, not rupees — ₹299 = 29900
  currency: "INR";
  key_id: string;                        // Razorpay public key, safe to expose client-side
}

interface PaymentResponse {             // GET /payments/me (proposed)
  id: string;
  razorpay_order_id: string;
  razorpay_payment_id: string | null;
  amount: number;                        // rupees (decimal) here, unlike the paise value above
  status: "created" | "paid" | "failed";
  created_at: string;
  verified_at: string | null;
}
```

Flow: backend creates a Razorpay order → frontend opens Checkout with `key_id` + `order_id` → on
completion, frontend posts `razorpay_payment_id`/`razorpay_signature` to `/payments/verify` as a
**UX-speed optimization only** — the backend's webhook is the actual source of truth for the
upgrade. Don't treat the client-side verify response as final confirmation; refetch
`GET /payments/me` to confirm the plan actually changed.
