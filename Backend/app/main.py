from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.config import get_settings
from app.db.database import check_db_connection
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

# Registers every SQLAlchemy model up front - see app/db/models/__init__.py
# for why this has to happen before any request can run a query.
import app.db.models  # noqa: F401

from app.routes.auth import router as auth_router
from app.routes.contact import router as contact_router
from app.routes.files import router as files_router
from app.routes.chat import router as chat_router
from app.middlewares.rate_limiter import QuotaLimitExceeded

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"________Starting {settings.APP_NAME}......__________")
    yield
    logger.info(f"Shutting down {settings.APP_NAME}......")


app = FastAPI(title=settings.APP_NAME, version=settings.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGIN,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline browser-hardening headers for every response.

    The API only returns JSON/SSE, but these also cover /docs, /redoc and
    error pages. A CSP is deliberately left to the edge/proxy because the
    Swagger UI ships inline scripts and would need nonces.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if settings.ENVIRONMENT.lower() == "production":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response

# Quota 429s carry a top-level body of {"detail": ..., "contact_form"?} per the
# specs/02 §3 contract, so they can't ride the default HTTPException(detail=...)
# shape; route them through this handler instead (see rate_limiter.py).
app.add_exception_handler(
    QuotaLimitExceeded,
    lambda request, exc: JSONResponse(status_code=429, content=exc.payload),
)

app.include_router(auth_router)
app.include_router(contact_router)
app.include_router(files_router)
app.include_router(chat_router)


@app.get("/")
def root():
    logger.info("Root endpoint hit")
    return {"message": f"{settings.APP_NAME} is running"}


@app.get("/health")
async def health_check():
    """Liveness + DB readiness probe.

    The old check returned 200 without touching the database, so a deploy
    with a broken DATABASE_URL stayed "healthy" and kept serving 500s. A
    failed probe now returns 503 so the load balancer can pull the instance.
    """
    try:
        await check_db_connection()
    except Exception:
        logger.exception("Health check database probe failed")
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "version": settings.VERSION},
        )
    return {"status": "ok", "version": settings.VERSION}
