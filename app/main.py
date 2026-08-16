"""
FastAPI application - the HTTP interface to the placement agent.

Endpoints:
  POST /profile              -> create/update a user's search profile
  POST /resume/upload        -> upload + parse a resume (PDF/DOCX)
  POST /resume/ats-score     -> score a resume against a job description
  POST /jobs/search          -> filtered + ranked job search
  POST /jobs/ingest          -> manually trigger a fetch cycle (also runs on schedule)
  POST /agent/chat           -> natural-language entrypoint to the full agent
"""
import os
import secrets
import logging
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

from app.db import init_db, get_session, Job, UserProfile
from app.core.resume_parser import parse_resume
from app.core.ats_scorer import compute_ats_score
from app.core.matcher import find_matches
from app.core.ingest import run_ingestion_cycle, seed_sample_jobs
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

    token = create_access_token(profile.id, profile.email)
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

    token = create_access_token(profile.id, profile.email)
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
    parsed = parse_resume(file_bytes, file.filename)

    current_user.resume_text = parsed["raw_text"]
    current_user.resume_skills = ",".join(parsed["skills"])
    db.commit()

    return {
        "message": "Resume parsed and saved.",
        "skills_found": parsed["skills"],
        "estimated_experience_years": parsed["experience_years"],
        "text_length": len(parsed["raw_text"]),
    }


@app.post("/resume/ats-score")
async def ats_score(
    job_description: str = Form(...),
    resume_text: str = Form(None),
    current_user: UserProfile = Depends(get_current_user),
):
    if not resume_text:
        resume_text = current_user.resume_text
    if not resume_text:
        raise HTTPException(400, "Provide resume_text directly, or upload a resume first via /resume/upload.")

    return compute_ats_score(resume_text, job_description)


@app.post("/jobs/search")
def search_jobs(
    job_titles: str = Form(..., description="Comma separated"),
    locations: str = Form(..., description="Comma separated"),
    top_k: int = Form(20),
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    all_jobs = db.query(Job).filter(Job.is_active == True).all()  # noqa: E712
    job_dicts = [
        {"title": j.title, "company": j.company, "location": j.location,
         "description": j.description, "apply_url": j.apply_url, "source": j.source}
        for j in all_jobs
    ]

    titles_list = [t.strip() for t in job_titles.split(",")]
    locations_list = [l.strip() for l in locations.split(",")]

    matches = find_matches(job_dicts, titles_list, locations_list, current_user.resume_text or "", top_k=top_k)
    return {"count": len(matches), "jobs": matches}


@app.post("/jobs/ingest")
def trigger_ingest(
    search_query: str = Form(""),
    search_location: str = Form(""),
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually triggers one fetch-and-store cycle immediately, instead
    of waiting for the next scheduled run. Requires login so this can't
    be spammed anonymously."""
    result = run_ingestion_cycle(db, search_query, search_location)
    return result


@app.post("/agent/chat")
def agent_chat(
    message: str = Form(...),
    current_user: UserProfile = Depends(get_current_user),
):
    """Natural language entrypoint - e.g. 'Find me remote data analyst
    internships in India and check my resume against the top one.'"""
    reply = run_agent(message, resume_text=current_user.resume_text or "")
    return {"reply": reply}


@app.post("/jobs/seed-sample")
def seed_sample(
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Loads a small set of sample jobs from app/data/sample_jobs.json
    into the pool. Use this to test search/matching/notifications right
    after setup, before configuring real GREENHOUSE_BOARDS/LEVER_BOARDS/
    ADZUNA keys - no external calls, no API keys required."""
    return seed_sample_jobs(db)


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve the static frontend (login/dashboard) at the root. Mounted last
# so it doesn't shadow the API routes above.
if os.path.isdir("app/static"):
    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
