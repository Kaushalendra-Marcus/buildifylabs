from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "BACKEND"
    VERSION: str = "1.0.0"
    ALLOWED_ORIGIN: list[str] = ["http://localhost:5173"]

    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    SQL_ECHO: bool = False

    # These back features that are planned but not wired into any route yet
    # (LLM pipeline, embeddings/vector store, payments). Making them required
    # meant the app couldn't boot at all without dummy values for keys nothing
    # currently uses. They become required again as each feature actually
    # ships.
    GROQ_API_KEY: Optional[str] = Field(None, env="GROQ_API_KEY")
    # Extra Groq keys: rotated round-robin per call and failed over on
    # 401/429/transport errors, so one exhausted or revoked key never takes
    # the pipeline down. Add GROQ_API_KEY2/3/4 in .env to use them.
    GROQ_API_KEY2: Optional[str] = Field(None, env="GROQ_API_KEY2")
    GROQ_API_KEY3: Optional[str] = Field(None, env="GROQ_API_KEY3")
    GROQ_API_KEY4: Optional[str] = Field(None, env="GROQ_API_KEY4")
    GROQ_MODEL: str = Field(..., env="GROQ_MODEL")
    # Optional override for small structured calls. When omitted, use GROQ_MODEL.
    GROQ_FAST_MODEL: Optional[str] = Field(None, env="GROQ_FAST_MODEL")

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

    WEB_SEARCH_API_KEY: Optional[str] = Field(None, env="WEB_SEARCH_API_KEY")
    WEB_SEARCH_MAX_RESULTS: int = 5
    # Live-web cache TTL (specs/07 FR5: ~6h so repeat external questions
    # share evidence instead of re-scraping at full latency/cost).
    WEB_SEARCH_CACHE_TTL_SECONDS: int = 21600

    # FRED (macro series) needs an API key for the observations endpoint
    # (free at api.stlouisfed.org). Absent -> macro adapter skips gracefully.
    FRED_API_KEY: Optional[str] = Field(None, env="FRED_API_KEY")

    # Stronger model for live-web narration (specs/12 synthesis touchpoint):
    # when set, source_scope in ("live_web", "both") narrates with this
    # model instead of GROQ_MODEL. Unset -> GROQ_MODEL (no behavior change).
    GROQ_STRONG_MODEL: Optional[str] = Field(None, env="GROQ_STRONG_MODEL")

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
    ENABLE_LIVE_WEB_SCOPE: bool = Field(True, env="ENABLE_LIVE_WEB_SCOPE")

    HF_API_KEY: Optional[str] = Field(None, env="HF_API_KEY")
    HF_MODEL: str = "mistralai/Mixtral-8x7B-Instruct-v0.1"

    PINECONE_API_KEY: Optional[str] = Field(None, env="PINECONE_API_KEY")
    PINECONE_ENVIRONMENT: Optional[str] = Field(None, env="PINECONE_ENVIRONMENT")

    REDIS_URL: Optional[str] = Field(None, env="REDIS_URL")

    JWT_SECRET: str = Field(..., env="JWT_SECRET")
    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    FRONTEND_URL: str = Field(..., env="FRONTEND_URL")

    SMTP_HOST: str = Field(..., env="SMTP_HOST")
    SMTP_PORT: int = Field(..., env="SMTP_PORT")
    SMTP_USER: str = Field(..., env="SMTP_USER")
    SMTP_PASS: str = Field(..., env="SMTP_PASS")
    EMAIL_FROM: str = Field(..., env="EMAIL_FROM")

    # Recipient for the POST /contact lead-capture form (specs/02 §2 FR5).
    # A config value rather than a hardcoded address, so it can be changed
    # without a code edit. Required because /contact ships with this phase.
    CONTACT_FORM_RECIPIENT_EMAIL: str = Field(..., env="CONTACT_FORM_RECIPIENT_EMAIL")

    # Google login is live and uses GOOGLE_CLIENT_ID to verify ID tokens.
    # GOOGLE_CLIENT_SECRET isn't referenced anywhere yet (only needed for a
    # server-side auth-code exchange flow, which isn't implemented), so it
    # stays optional until that's built.
    GOOGLE_CLIENT_ID: str = Field(..., env="GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = Field(None, env="GOOGLE_CLIENT_SECRET")

    HUGGINGFACE_MODEL_PATH: str = "sentence-transformers/all-MiniLM-L6-v2"

    UPI_ID: Optional[str] = Field(None, env="UPI_ID")
    PAYMENT_AMOUNT: int = 299

    LOGIN_RATE_LIMIT: int = 5
    VERIFY_EMAIL_RATE_LIMIT: int = 3

    # Where uploaded raw files are persisted (Phase B3 storage backend, gap #4):
    # local disk for dev; swap storage.py for an object-store backend in prod.
    # Optional with a sane default, so upload works without extra env setup.
    UPLOAD_DIR: str = "data/uploads"

    REQUESTS_PER_MINUTE: int = 60

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
