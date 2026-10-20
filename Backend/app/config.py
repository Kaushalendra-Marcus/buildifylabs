from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic import Field, field_validator
from functools import lru_cache
from typing import Optional
from typing_extensions import Annotated


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, env_file=".env")

    APP_NAME: str = "BACKEND"
    VERSION: str = "1.2.0"
    # NoDecode: pydantic-settings JSON-decodes complex fields before any
    # field_validator runs, so a comma-separated value used to crash the app
    # at boot with SettingsError. NoDecode passes the raw string through to
    # the validator below, which accepts both JSON arrays and plain
    # comma-separated strings.
    ALLOWED_ORIGIN: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("ALLOWED_ORIGIN", mode="before")
    @classmethod
    def parse_allowed_origin(cls, v):
        # Accept a JSON array ('["https://a","https://b"]') or a plain
        # comma-separated string ("https://a, https://b") for the same list.
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json

                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass
            return [x.strip().strip("\"'") for x in s.split(",") if x.strip()]
        return v

    DATABASE_URL: str = Field(...)
    # Neon requires TLS; a local Postgres in Docker usually does not. Set
    # DATABASE_SSL=false for local/dev databases instead of editing code.
    DATABASE_SSL: bool = True
    SQL_ECHO: bool = False

    # These back features that are planned but not wired into any route yet
    # (LLM pipeline, payments). Making them required
    # meant the app couldn't boot at all without dummy values for keys nothing
    # currently uses. They become required again as each feature actually
    # ships.
    GROQ_API_KEY: Optional[str] = Field(None)
    # Extra Groq keys: rotated round-robin per call and failed over on
    # 401/429/transport errors, so one exhausted or revoked key never takes
    # the pipeline down. Add GROQ_API_KEY2/3/4 in .env to use them.
    GROQ_API_KEY2: Optional[str] = Field(None)
    GROQ_API_KEY3: Optional[str] = Field(None)
    GROQ_API_KEY4: Optional[str] = Field(None)
    GROQ_MODEL: str = Field(...)
    # Optional override for small structured calls. When omitted, use GROQ_MODEL.
    GROQ_FAST_MODEL: Optional[str] = Field(None)

    @property
    def groq_api_keys(self) -> list[str]:
        """Configured Groq keys in priority order, empties dropped."""
        return [
            key
            for key in (
                self.GROQ_API_KEY,
                self.GROQ_API_KEY2,
                self.GROQ_API_KEY3,
                self.GROQ_API_KEY4,
            )
            if key
        ]

    @property
    def groq_fast_model(self) -> str:
        return self.GROQ_FAST_MODEL or self.GROQ_MODEL

    @property
    def groq_strong_model(self) -> str:
        """Model for trust-sensitive live-web synthesis. Falls back to the
        default model when no stronger override is configured."""
        return self.GROQ_STRONG_MODEL or self.GROQ_MODEL

    WEB_SEARCH_API_KEY: Optional[str] = Field(None)
    # Firecrawl API key for managed web search/extraction (empty -> adapter
    # disabled).
    FIRECRAWL_API_KEY: Optional[str] = Field(None)
    # Firecrawl returns full markdown per result (more useful than a one-line
    # snippet, but also more credits/latency), so keep its fan-out tighter than
    # Tavily's.
    FIRECRAWL_MAX_RESULTS: int = 3
    WEB_SEARCH_MAX_RESULTS: int = 5
    # Live-web cache TTL (specs/07 FR5: ~6h so repeat external questions
    # share evidence instead of re-scraping at full latency/cost).
    WEB_SEARCH_CACHE_TTL_SECONDS: int = 21600

    # FRED (macro series) needs an API key for the observations endpoint
    # (free at api.stlouisfed.org). Absent -> macro adapter skips gracefully.
    FRED_API_KEY: Optional[str] = Field(None)

    # Stronger model for live-web narration (specs/12 synthesis touchpoint):
    # when set, source_scope in ("live_web", "both") narrates with this
    # model instead of GROQ_MODEL. Unset -> GROQ_MODEL (no behavior change).
    GROQ_STRONG_MODEL: Optional[str] = Field(None)

    # --- Live-web evidence budgeting (specs/07 hardening) ---

    # Hard character budget for the ranked snippet pool that enters the
    # synthesis prompt. A cheap proxy for a token budget (no tokenizer
    # dependency) — see app/services/llm/context_budget.py::estimate_tokens
    # for the exact heuristic. Multiple search queries x multiple providers
    # can produce more snippets than any single call should see; this is the
    # ceiling that turns "first N by arrival order" into "best-fitting subset
    # by relevance."
    MAX_EVIDENCE_CONTEXT_CHARS: int = 12000

    # A single retrieved source (e.g. one full Tavily-Extract page) larger
    # than this triggers map-reduce summarization (Phase 3) before it is
    # added to the evidence pool, instead of being hard-truncated mid-sentence.
    SUMMARIZE_TRIGGER_CHARS: int = 6000

    # How many top result URLs the recommendation deep-read may fetch in full
    # (Tavily Extract) for list/ranking/comparison questions. Bounded so page
    # bodies improve answers without runaway latency/cost; each page still
    # goes through map-reduce summarization above when oversized.
    MAX_DEEP_READ_URLS: int = 5

    # --- Visual guarantee (specs/06 FR9) ---

    # Ceiling on how many deterministically-synthesized visuals
    # ensure_visuals() may attach to one answer. Single source of truth —
    # replaces three separate literal slices inside that function.
    MAX_SYNTHESIZED_VISUALS: int = 7

    # --- source_scope rollout kill switch (specs/07, Phase 6 of this plan) ---

    # Independent of any code change: flip to false (env var, no redeploy of
    # logic) if e.g. the Tavily free-tier monthly quota is at risk. When
    # false, live_web/both requests answer honestly from own_data only (never
    # a silent full failure) — see Phase 6.
    ENABLE_LIVE_WEB_SCOPE: bool = Field(True)

    # --- Document QA (Part C): PDF text → chunks → keyword evidence ---

    # Kill switch, same shape as ENABLE_LIVE_WEB_SCOPE above: when false,
    # PDF uploads still succeed (parsed and chunked) but retrieval at query
    # time is skipped.
    ENABLE_DOCUMENT_QA: bool = Field(True)

    DOCUMENT_CHUNK_CHARS: int = 1000
    DOCUMENT_CHUNK_OVERLAP_CHARS: int = 150
    MAX_DOCUMENT_CHARS: int = 200_000  # safety cap on extracted PDF text before chunking
    MAX_CHUNKS_PER_FILE: int = 400  # independent backstop

    MAX_DOCUMENT_CHUNKS_PER_QUERY: int = 6  # top-K retrieval
    MAX_DOCUMENT_CONTEXT_CHARS: int = 4000  # reserved sub-budget for documents

    HF_API_KEY: Optional[str] = Field(None)
    HF_MODEL: str = "mistralai/Mixtral-8x7B-Instruct-v0.1"

    PINECONE_API_KEY: Optional[str] = Field(None)
    PINECONE_ENVIRONMENT: Optional[str] = Field(None)

    REDIS_URL: Optional[str] = Field(None)

    JWT_SECRET: str = Field(...)
    JWT_ALGORITHM: str = "HS256"

    @field_validator("JWT_ALGORITHM")
    @classmethod
    def only_hmac_algorithms(cls, v: str) -> str:
        # Never allow "none" or an asymmetric algorithm here: every token in
        # this app is signed and verified with the same shared secret, and an
        # env typo must fail at boot instead of silently weakening auth.
        allowed = {"HS256", "HS384", "HS512"}
        if v not in allowed:
            raise ValueError(f"JWT_ALGORITHM must be one of {sorted(allowed)}")
        return v

    # "production" enables production-only response headers (HSTS) and is the
    # signal for deployment checks. Defaults to development so local runs and
    # tests need no extra env.
    ENVIRONMENT: str = "development"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    FRONTEND_URL: str = Field(...)

    SMTP_HOST: str = Field(...)
    SMTP_PORT: int = Field(...)
    SMTP_USER: str = Field(...)
    SMTP_PASS: str = Field(...)
    EMAIL_FROM: str = Field(...)

    # Recipient for the POST /contact lead-capture form (specs/02 §2 FR5).
    # A config value rather than a hardcoded address, so it can be changed
    # without a code edit. Required because /contact ships with this phase.
    CONTACT_FORM_RECIPIENT_EMAIL: str = Field(...)

    # Google login is live and uses GOOGLE_CLIENT_ID to verify ID tokens.
    # GOOGLE_CLIENT_SECRET isn't referenced anywhere yet (only needed for a
    # server-side auth-code exchange flow, which isn't implemented), so it
    # stays optional until that's built.
    GOOGLE_CLIENT_ID: str = Field(...)
    GOOGLE_CLIENT_SECRET: Optional[str] = Field(None)

    HUGGINGFACE_MODEL_PATH: str = "sentence-transformers/all-MiniLM-L6-v2"

    UPI_ID: Optional[str] = Field(None)
    PAYMENT_AMOUNT: int = 299

    LOGIN_RATE_LIMIT: int = 5
    VERIFY_EMAIL_RATE_LIMIT: int = 3
    # Unauthenticated account-creating / email-sending endpoints. Without
    # these, /auth/guest can mint unlimited accounts and /contact can be used
    # to relay spam through the app's SMTP credentials.
    GUEST_RATE_LIMIT: int = 10
    CONTACT_RATE_LIMIT: int = 5

    # Set to true only when the app is behind a trusted reverse proxy / load
    # balancer that overwrites X-Forwarded-For. Otherwise a client could spoof
    # the header and dodge per-IP rate limits.
    TRUST_PROXY_HEADERS: bool = False

    # Where uploaded raw files are persisted (Phase B3 storage backend, gap #4):
    # local disk for dev; swap storage.py for an object-store backend in prod.
    # Optional with a sane default, so upload works without extra env setup.
    UPLOAD_DIR: str = "data/uploads"

    REQUESTS_PER_MINUTE: int = 60


@lru_cache()
def get_settings() -> Settings:
    return Settings()
