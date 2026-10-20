from app.config import get_settings
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from functools import lru_cache
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base = declarative_base()


@lru_cache()
def get_engine():
    # lru_cache() with no args memoizes on the (empty) argument list, so this
    # always returns the same engine/pool for the process lifetime instead of
    # opening a brand new connection pool on every single request.
    settings = get_settings()
    return create_async_engine(
        settings.DATABASE_URL,
        echo=settings.SQL_ECHO,
        pool_size=10,
        max_overflow=20,
        # Neon (and most managed Postgres) closes idle connections server-side.
        # Without pre-ping the first request after an idle period gets a
        # "connection is closed" 500; recycling keeps the pool fresh.
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
        connect_args={
            "ssl": settings.DATABASE_SSL,
        },
    )


@lru_cache()
def get_session_maker():
    return async_sessionmaker(bind=get_engine(), class_=AsyncSession, expire_on_commit=False)


async def check_db_connection():
    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connected successfully")


async def get_db():
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
