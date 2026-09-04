"""
Database layer. Uses SQLite by default (zero setup) but works with any
SQLAlchemy-supported DB (Postgres, MySQL) by changing DATABASE_URL in .env
"""
import os
import hashlib
import logging
import datetime as dt
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, Float, Boolean, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./placements.db")
IS_SQLITE = "sqlite" in DATABASE_URL
logger = logging.getLogger(__name__)

# pool_pre_ping: tests each connection with a lightweight query before
# handing it to a request, transparently reconnecting if it's dead -
# this is the fix for "SSL connection has been closed unexpectedly"
# errors, which happen because managed Postgres providers (Neon, RDS,
# Supabase, etc.) silently close idle connections after a timeout, but
# SQLAlchemy's pool doesn't know that and tries to reuse the stale one.
# pool_recycle: proactively drops+reopens connections older than this
# many seconds, so we recycle before the provider's own timeout hits
# rather than only reacting after a request already failed once.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    pool_pre_ping=True,
    pool_recycle=180 if not IS_SQLITE else -1,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Job(Base):
    """A single job/internship posting pulled from any source."""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_hash = Column(String, unique=True, index=True)  # dedup key
    title = Column(String, index=True)
    company = Column(String, index=True)
    location = Column(String, index=True)
    description = Column(Text)
    apply_url = Column(String)
    source = Column(String)          # greenhouse / lever / adzuna / manual
    experience_level = Column(String, nullable=True)  # intern/entry/mid/senior (best-effort)
    posted_date = Column(DateTime, default=dt.datetime.utcnow)
    fetched_at = Column(DateTime, default=dt.datetime.utcnow)
    is_active = Column(Boolean, default=True)


class UserProfile(Base):
    """Stores what the user is looking for + their parsed resume."""
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String, nullable=True)
    job_titles = Column(String)      # comma separated
    locations = Column(String)       # comma separated
    experience_level = Column(String)
    resume_text = Column(Text, nullable=True)
    resume_skills = Column(Text, nullable=True)  # comma separated, extracted
    telegram_chat_id = Column(String, nullable=True)  # set once user links their Telegram
    notify_email = Column(Boolean, default=True)
    notify_telegram = Column(Boolean, default=True)
    match_score_threshold = Column(Float, default=40.0)  # min match_score to trigger a notification
    reset_token_hash = Column(String, nullable=True)  # sha256 of the raw token emailed to the user, never store the raw token
    reset_token_expires = Column(DateTime, nullable=True)
    security_question = Column(String, nullable=True)
    security_answer_hash = Column(String, nullable=True)  # bcrypt hash, same treatment as the password itself
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class MatchResult(Base):
    """Cached match score between a user and a job, so we don't recompute
    every time and so we can detect + notify about NEW high matches."""
    __tablename__ = "match_results"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    job_id = Column(Integer, index=True)
    score = Column(Float)
    matched_at = Column(DateTime, default=dt.datetime.utcnow)
    notified = Column(Boolean, default=False)


def make_job_hash(title: str, company: str, location: str) -> str:
    """Stable hash so the same job from the same company/location isn't
    stored twice even if the scraper runs repeatedly or job appears on
    multiple boards with slightly different descriptions."""
    key = f"{title.strip().lower()}|{company.strip().lower()}|{location.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()


def _column_ddl_type(column: Column) -> str:
    """Maps a SQLAlchemy column's type to the DDL fragment needed for a
    raw ALTER TABLE ... ADD COLUMN statement. Only needs to cover the
    types actually used in our models."""
    return column.type.compile(dialect=engine.dialect)


def _sync_schema():
    """Auto-migration: compares each model's expected columns against
    what actually exists in the live DB and ADD COLUMNs anything
    missing, with a safe default so existing rows don't break.

    Why this exists: Base.metadata.create_all() (used in init_db) only
    creates tables that don't exist yet - it silently does nothing to
    a table that's already there, even if the model gained new columns
    since that table was first created. That's exactly what caused the
    'UndefinedColumn' error for reset_token_hash/reset_token_expires:
    the live Postgres table predated those fields.

    This isn't a substitute for a real migration tool (Alembic) if the
    project grows - it only handles the additive case (new nullable
    column), not renames/drops/type changes. But it means future
    column additions deploy cleanly without a manual SQL step on Neon,
    which is the actual recurring failure mode we want to eliminate."""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # brand new table - create_all already handles this case
        existing_cols = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_cols:
                continue
            ddl_type = _column_ddl_type(column)
            stmt = f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl_type}'
            logger.warning(f"[db] auto-migrating: {stmt}")
            with engine.begin() as conn:
                conn.execute(text(stmt))


def init_db():
    Base.metadata.create_all(bind=engine)
    _sync_schema()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
