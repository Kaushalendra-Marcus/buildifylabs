from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.config import get_settings
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

# Registers every SQLAlchemy model up front - see app/db/models/__init__.py
# for why this has to happen before any request can run a query.
import app.db.models  # noqa: F401

from app.routes.auth import router as auth_router
from app.routes.contact import router as contact_router
from app.routes.files import router as files_router
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


@app.get("/")
def root():
    logger.info("Root endpoint hit")
    return {"message": f"{settings.APP_NAME} is running"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
