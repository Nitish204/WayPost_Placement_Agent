"""
FastAPI application - the HTTP interface to the placement agent.

Endpoints:
  POST /profile              -> create/update a user's search profile
  POST /resume/upload        -> upload + parse a resume (PDF/DOCX)
  POST /resume/ats-score     -> score a resume against a job description
  POST /jobs/search          -> filtered + ranked job search
  POST /jobs/ingest          -> manually trigger a fetch cycle (requires login, also runs on schedule)
  POST /cron/ingest          -> trigger a fetch cycle via external cron, secret-key protected, no login
  POST /agent/chat           -> natural-language entrypoint to the full agent
  GET  /admin/board-health   -> checks each configured Greenhouse/Lever/Ashby token is still live
  POST /apply/prepare        -> fills a job's real apply form via browser agent, returns a preview (does NOT submit)
  POST /apply/{id}/confirm   -> submits a previously previewed + human-approved application
  POST /apply/{id}/reject    -> discards a previewed application without submitting
  GET  /applications         -> lists the current user's application attempts + statuses
"""
import os
import json
import hashlib
import secrets
import logging
import datetime as dt
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

from app.db import init_db, get_session, Job, UserProfile, Application
from app.core.resume_parser import parse_resume
from app.core.ats_scorer import compute_ats_score
from app.core.matcher import find_matches
from app.core.ingest import run_ingestion_cycle, seed_sample_jobs, resolve_boards
from app.core.board_validator import validate_boards
from app.core.apply_agent import prepare_application, confirm_submit
from app.core.auth import (
    hash_password, verify_password, create_access_token, get_current_user,
    hash_security_answer, verify_security_answer,
)
from app.agent import run_agent
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Placement Finder Agent", version="0.2.0")

# ---------------------------------------------------------------------
# Rate limiting - keyed by client IP. Applied per-endpoint below (see
# @limiter.limit(...) decorators on auth routes) since those are the
# realistic brute-force targets (login guessing, security-answer
# guessing, account enumeration via forgot-password, spam registration)
# - not blanket-applied to every endpoint, since job search/resume
# upload etc. don't carry the same abuse risk and blanket limits just
# degrade normal usage without adding real protection there.
# ---------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ---------------------------------------------------------------------
# CORS - defaults to permissive for local dev, but reads
# ALLOWED_ORIGINS from the environment so a production deploy can (and
# should) lock this to its real domain instead of allowing any site to
# call this API from a browser. Comma-separated, e.g.:
#   ALLOWED_ORIGINS=https://waypost-placement-agent.onrender.com
# ---------------------------------------------------------------------
_allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "").strip()
allowed_origins = [o.strip() for o in _allowed_origins_env.split(",") if o.strip()] or ["*"]
if allowed_origins == ["*"]:
    logger.warning(
        "[security] ALLOWED_ORIGINS not set - CORS is wide open (allow_origins=['*']). "
        "Set ALLOWED_ORIGINS to your real deployed URL before treating this as production."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Baseline security headers on every response. None of these are
    exotic - they're the standard low-cost hardening any API should
    ship with, closing off classes of attack (clickjacking, MIME
    sniffing, referrer leakage) that cost nothing to prevent."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.on_event("startup")
def on_startup():
    init_db()
    interval = int(os.getenv("INGEST_INTERVAL_MINUTES", "60"))
    start_scheduler(interval_minutes=interval)
    logger.info("App started, DB initialized, scheduler running.")


@app.on_event("shutdown")
def on_shutdown():
    stop_scheduler()


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------

@app.post("/auth/register")
@limiter.limit("5/minute")
def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(..., min_length=8),
    security_question: str = Form(..., description="e.g. 'What was your first pet's name?'"),
    security_answer: str = Form(..., min_length=2),
    job_titles: str = Form(..., description="Comma separated, e.g. 'Software Engineer,Data Analyst'"),
    locations: str = Form(..., description="Comma separated, e.g. 'Bangalore,Remote'"),
    experience_level: str = Form("fresher"),
    db: Session = Depends(get_session),
):
    existing = db.query(UserProfile).filter(UserProfile.email == email).first()
    if existing:
        raise HTTPException(409, "An account with this email already exists. Try logging in instead.")

    profile = UserProfile(
        name=name, email=email, hashed_password=hash_password(password),
        security_question=security_question, security_answer_hash=hash_security_answer(security_answer),
        job_titles=job_titles, locations=locations, experience_level=experience_level,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    token = create_access_token(profile)
    return {
        "access_token": token, "token_type": "bearer",
        "user": {"id": profile.id, "name": profile.name, "email": profile.email},
    }


@app.post("/auth/login")
@limiter.limit("10/minute")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_session),
):
    profile = db.query(UserProfile).filter(UserProfile.email == email).first()
    if not profile or not profile.hashed_password or not verify_password(password, profile.hashed_password):
        raise HTTPException(401, "Incorrect email or password.")

    token = create_access_token(profile)
    return {
        "access_token": token, "token_type": "bearer",
        "user": {"id": profile.id, "name": profile.name, "email": profile.email},
    }


@app.get("/auth/me")
def me(current_user: UserProfile = Depends(get_current_user)):
    return {
        "id": current_user.id, "name": current_user.name, "email": current_user.email,
        "job_titles": current_user.job_titles, "locations": current_user.locations,
        "experience_level": current_user.experience_level,
        "has_resume": bool(current_user.resume_text),
        "resume_skills": (current_user.resume_skills or "").split(",") if current_user.resume_skills else [],
        "notify_email": current_user.notify_email,
        "notify_telegram": current_user.notify_telegram,
        "telegram_linked": bool(current_user.telegram_chat_id),
        "match_score_threshold": current_user.match_score_threshold,
    }


@app.post("/auth/logout_everywhere")
def logout_everywhere(current_user: UserProfile = Depends(get_current_user), db: Session = Depends(get_session)):
    """Invalidates every token issued for this account, including the
    one used to call this endpoint. Useful if you suspect a token
    leaked, or just want to force a clean re-login on every device.

    `or 0` guards against existing rows that predate this column: the
    generic auto-migration (_sync_schema in db.py) only ADD COLUMNs the
    type, not a default, so a pre-existing user's token_version starts
    as NULL/None rather than 0 - `None + 1` would otherwise crash here."""
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    return {"message": "Logged out on all devices. Please log in again."}


@app.post("/auth/security-question")
@limiter.limit("5/minute")
def get_security_question(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_session),
):
    """First step of on-site password recovery: returns the account's
    security question so the frontend can display it. This does leak
    whether an email is registered (unlike the old email-based flow,
    which could stay silent) - an accepted tradeoff for going
    email-free, since the alternative (always returning some question)
    would let anyone probe for the real one anyway once they submit
    a wrong answer. Rate limited to slow down enumeration attempts."""
    user = db.query(UserProfile).filter(UserProfile.email == email).first()
    if not user or not user.security_question:
        raise HTTPException(404, "No account found with that email, or no security question was set for it.")
    return {"security_question": user.security_question}


@app.post("/auth/reset-with-security-answer")
@limiter.limit("5/minute")
def reset_with_security_answer(
    request: Request,
    email: str = Form(...),
    security_answer: str = Form(...),
    new_password: str = Form(..., min_length=8),
    db: Session = Depends(get_session),
):
    """Tightly rate limited on purpose: this is the actual
    account-takeover path if a security answer is guessable, so it
    gets the strictest limit of any auth endpoint - 5 attempts/minute
    per IP makes brute-forcing a short/common answer impractical."""
    user = db.query(UserProfile).filter(UserProfile.email == email).first()
    if not user or not verify_security_answer(security_answer, user.security_answer_hash):
        raise HTTPException(400, "That answer doesn't match our records. Please try again.")

    user.hashed_password = hash_password(new_password)
    # A password reset is exactly the moment a stolen/old session should
    # stop working. `or 0` guards the same NULL-on-existing-rows case as
    # logout_everywhere above.
    user.token_version = (user.token_version or 0) + 1
    db.commit()

    return {"message": "Password updated. You can now log in with your new password."}


# ---------------------------------------------------------------------
# Profile / preferences (all require a valid Bearer token now)
# ---------------------------------------------------------------------

@app.post("/profile/update")
def update_profile(
    job_titles: str = Form(None),
    locations: str = Form(None),
    experience_level: str = Form(None),
    notify_email: bool = Form(None),
    notify_telegram: bool = Form(None),
    match_score_threshold: float = Form(None),
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    for field, value in [
        ("job_titles", job_titles), ("locations", locations),
        ("experience_level", experience_level), ("notify_email", notify_email),
        ("notify_telegram", notify_telegram), ("match_score_threshold", match_score_threshold),
    ]:
        if value is not None:
            setattr(current_user, field, value)
    db.commit()
    return {"message": "Profile updated."}


@app.post("/notifications/telegram/link")
def link_telegram(
    telegram_chat_id: str = Form(..., description="Get this from @userinfobot on Telegram, or your bot's /start handler"),
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    current_user.telegram_chat_id = telegram_chat_id
    db.commit()
    return {"message": "Telegram linked. You'll now receive job alerts there too."}


@app.post("/resume/upload")
async def upload_resume(
    file: UploadFile = File(...),
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    file_bytes = await file.read()
    try:
        parsed = parse_resume(file_bytes, file.filename)
    except ValueError as e:
        # parse_resume raises ValueError for unsupported file extensions -
        # without this catch, that became an unhandled 500 instead of a
        # clean message the frontend could actually show the user.
        raise HTTPException(400, str(e))

    current_user.resume_text = parsed["raw_text"]
    current_user.resume_skills = ",".join(parsed["skills"])
    db.commit()
