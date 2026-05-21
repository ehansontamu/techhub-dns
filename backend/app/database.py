import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from app.config import settings

database_url = settings.database_url

# SQLAlchemy QueuePool settings are process-local. Keep defaults conservative,
# but allow a little headroom for a small internal team on one web worker.
MYSQL_POOL_DEFAULTS = {
    "pool_size": 8,
    "max_overflow": 4,
    "pool_timeout": 5,
    "pool_recycle": 3600,
}

MYSQL_POOL_LIMITS = {
    "pool_size": (1, 12),
    "max_overflow": (0, 12),
    "pool_timeout": (2, 30),
    "pool_recycle": (300, 7200),
}


def _get_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = _get_env_int(name, default)
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


runtime_db_pool_settings = {
    "database_backend": "sqlite"
    if str(database_url).strip().lower().startswith("sqlite")
    else "mysql",
    "pool_size": None,
    "max_overflow": None,
    "pool_timeout": None,
    "pool_recycle": None,
}

# SQLite does not support the MYSQL QueuePool arguments used in production.
if str(database_url).strip().lower().startswith("sqlite"):
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
else:
    pool_size = _bounded_env_int(
        "DB_POOL_SIZE",
        MYSQL_POOL_DEFAULTS["pool_size"],
        MYSQL_POOL_LIMITS["pool_size"][0],
        MYSQL_POOL_LIMITS["pool_size"][1],
    )
    max_overflow = _bounded_env_int(
        "DB_MAX_OVERFLOW",
        MYSQL_POOL_DEFAULTS["max_overflow"],
        MYSQL_POOL_LIMITS["max_overflow"][0],
        MYSQL_POOL_LIMITS["max_overflow"][1],
    )
    pool_recycle = _bounded_env_int(
        "DB_POOL_RECYCLE",
        MYSQL_POOL_DEFAULTS["pool_recycle"],
        MYSQL_POOL_LIMITS["pool_recycle"][0],
        MYSQL_POOL_LIMITS["pool_recycle"][1],
    )
    pool_timeout = _bounded_env_int(
        "DB_POOL_TIMEOUT",
        MYSQL_POOL_DEFAULTS["pool_timeout"],
        MYSQL_POOL_LIMITS["pool_timeout"][0],
        MYSQL_POOL_LIMITS["pool_timeout"][1],
    )

    runtime_db_pool_settings.update(
        {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
        }
    )

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,  # MySQL connection timeout handling
        pool_timeout=pool_timeout,
    )

    # Issue #42: auto-tune pool_recycle to stay under MySQL's wait_timeout
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text("SHOW VARIABLES LIKE 'wait_timeout'"))
            row = result.fetchone()
            if row:
                wait_timeout = int(row[1])
                # Set pool_recycle to 80% of wait_timeout, minimum 60s
                safe_recycle = max(60, int(wait_timeout * 0.8))
                if safe_recycle < pool_recycle:
                    engine.pool._recycle = safe_recycle
                    runtime_db_pool_settings["pool_recycle"] = safe_recycle
                    import logging
                    logging.getLogger(__name__).info(
                        "Adjusted pool_recycle to %s (wait_timeout=%s)", safe_recycle, wait_timeout
                    )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Could not query wait_timeout: %s", e)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@contextmanager
def get_db():
    """Context manager for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session():
    """Get a database session directly (caller responsible for closing)"""
    return SessionLocal()


def get_runtime_db_pool_settings() -> dict:
    return dict(runtime_db_pool_settings)
