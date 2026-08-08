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

## Upload — `specs/04-file-upload-ingestion.md` (validator exists, route doesn't yet)

```ts
interface FileResponse {
  id: string;
  file_name: string;
  file_type: string;
  file_size: number | null;
  status: "processing" | "completed" | "failed";   // completed/failed transition not implemented server-side yet
  pinecone_namespace: string | null;
  created_at: string;                               // ISO 8601
}
```

Accepted types: `.csv`, `.pdf`, `.xlsx` only, checked by both extension and MIME type server-side —
send the real file, don't rely on spoofing `content_type`. Size caps: `free` ≤ 3 MB, `pro` ≤ 10 MB.
Guest users are rejected entirely (403) — don't show the upload UI to guests at all.

## Chat / insight pipeline — `specs/06-ai-insight-pipeline.md` (shape stable, no route yet)

```ts
type VisualType =
  | "line_chart" | "bar_chart" | "pie_chart" | "kpi_card"
  | "heatmap" | "funnel_chart" | "india_map" | "anomaly_chart" | "ai_summary";

interface VisualOutput {
  visual_type: VisualType;              // ⚠️ not enum-enforced server-side yet — handle an
                                         // unrecognized value defensively, don't assume it's
                                         // always one of the 9
  chart_data: {
    labels: unknown[];
    datasets: unknown[];
    meta: Record<string, unknown>;
  };
  title: string;
}

interface PipelineOutput {
  answer: string;
  visuals: VisualOutput[];
  insights: string[];
  summary: string;
  root_causes: string[];
  recommendations: string[];
  news_context: string[];
  anomalies: string[];
  confidence: number;                   // ⚠️ not bounded server-side yet (spec says should be 0..1)
}
```

Build one renderer per `visual_type` (9 total) plus a fallback for unrecognized values.
`chart_data`'s exact `labels`/`datasets` shape isn't pinned down further in the spec — treat it as
loosely structured until a real `/chat` route exists to observe real payloads against.

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
