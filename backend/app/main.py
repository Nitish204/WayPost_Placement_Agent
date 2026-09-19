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
    if os.getenv("DISABLE_SCHEDULER") == "1":
        # Tests import this module via FastAPI's TestClient context manager,
        # which fires this exact startup event - without this guard, every
        # single test run would kick off the real scheduler (real network
        # calls to Greenhouse/Lever/Ashby, real board-health checks) in a
        # background thread that outlives the test and races against
        # conftest.py's per-test Base.metadata.drop_all/create_all, which
        # is what the "no such table" background errors during the test
        # run were. Set by tests/conftest.py.
        logger.info("App started, DB initialized, scheduler DISABLED (DISABLE_SCHEDULER=1).")
        return
    interval = int(os.getenv("INGEST_INTERVAL_MINUTES", "60"))
    board_health_interval = int(os.getenv("BOARD_HEALTH_INTERVAL_MINUTES", "360"))
    start_scheduler(interval_minutes=interval, board_health_interval_minutes=board_health_interval)
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
    # `id` is included so the frontend can call /apply/prepare for a
    # specific result without a second lookup - it wasn't needed before
    # this endpoint's only consumer was "open apply_url in a new tab".
    job_dicts = [
        {"id": j.id, "title": j.title, "company": j.company, "location": j.location,
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


@app.post("/cron/ingest")
def cron_trigger_ingest(
    request: Request,
    secret: str = Form(None),
    db: Session = Depends(get_session),
):
    """Unauthenticated (no user login) ingest trigger for external cron
    services (cron-job.org, GitHub Actions scheduled workflow, etc.)
    that can't hold a user's Bearer token. Protected instead by a
    shared secret (CRON_SECRET env var) so random requests on the
    internet can't spam free-tier API quotas or spin up the service
    unnecessarily.

    Accepts the secret as a form field OR an X-Cron-Secret header, since
    different cron services differ in what's easiest for them to send.

    Set CRON_SECRET in Render's environment variables to any long
    random string, then have your external cron service call:
      POST https://<your-app>.onrender.com/cron/ingest
      Header: X-Cron-Secret: <same value>
    This request also serves to wake the service from sleep on
    Render's free tier, since incoming traffic resets the idle timer.
    """
    expected = os.getenv("CRON_SECRET", "")
    provided = secret or request.headers.get("X-Cron-Secret", "")

    if not expected:
        raise HTTPException(
            503,
            "CRON_SECRET is not configured on the server. Set it in Render's "
            "environment variables before using this endpoint.",
        )
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(401, "Invalid or missing cron secret.")

    result = run_ingestion_cycle(db)
    logger.info(f"[cron] ingest triggered externally: {result}")
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


@app.get("/admin/board-health")
def board_health(current_user: UserProfile = Depends(get_current_user)):
    """Checks every currently-configured Greenhouse/Lever/Ashby board
    token against its real endpoint and reports which ones are dead.

    Requires login (not a public/cron-style secret-key endpoint like
    /cron/ingest) since this is a diagnostic for whoever's running the
    app, not something external automation needs to call - and it's
    cheap enough (a handful of short-timeout GETs) that ordinary login
    protection is enough, no separate rate limit needed."""
    boards = resolve_boards()
    return validate_boards(boards)


@app.post("/apply/prepare")
def apply_prepare(
    job_id: int = Form(...),
    phone: str = Form(None),
    cover_note: str = Form(None),
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Stage 1 of auto-apply: loads the job's REAL apply_url in a
    headless browser, fills whatever fields it recognizes (name/email/
    phone/cover letter) using the current user's profile, and returns a
    screenshot for review. Does NOT submit anything - see apply_agent.py.

    Idempotent per (user, job): re-calling this for a job that already
    has an application row returns the existing row's current state
    instead of launching a second browser session and creating a
    duplicate pending_approval entry."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    if not job.apply_url:
        raise HTTPException(400, "This job posting has no apply link to fill.")

    idempotency_key = hashlib.sha256(f"{current_user.id}:{job_id}".encode()).hexdigest()
    existing = db.query(Application).filter(Application.idempotency_key == idempotency_key).first()
    if existing:
        return _serialize_application(existing, job)

    candidate = {
        "name": current_user.name,
        "email": current_user.email,
        "phone": phone or "",
        "cover_note": cover_note or "",
    }
    result = prepare_application(job.apply_url, candidate)

    application = Application(
        user_id=current_user.id,
        job_id=job.id,
        idempotency_key=idempotency_key,
        status="pending_approval" if result["ok"] else "failed",
        phone=phone,
        cover_note=cover_note,
        filled_fields=json.dumps(result.get("filled_fields", [])),
        preview_screenshot_b64=result.get("screenshot_b64"),
        error_message=None if result["ok"] else result.get("reason"),
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    return _serialize_application(application, job)


@app.post("/apply/{application_id}/confirm")
def apply_confirm(
    application_id: int,
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Stage 2 of auto-apply - the human approval gate. Only an
    application currently in 'pending_approval' (i.e. successfully
    previewed, not yet acted on) can be confirmed. This is the only
    code path in the whole app that leads to apply_agent.confirm_submit()
    - there is no way to reach a real form submission without a human
    having called this endpoint on a specific, already-previewed row."""
    application = db.query(Application).filter(
        Application.id == application_id, Application.user_id == current_user.id
    ).first()
    if not application:
        raise HTTPException(404, "Application not found.")
    if application.status != "pending_approval":
        raise HTTPException(400, f"Application is '{application.status}', not awaiting approval.")

    job = db.query(Job).filter(Job.id == application.job_id).first()
    if not job:
        raise HTTPException(404, "The job for this application no longer exists.")

    candidate = {
        "name": current_user.name,
        "email": current_user.email,
        "phone": application.phone or "",
        "cover_note": application.cover_note or "",
    }
    result = confirm_submit(job.apply_url, candidate)
    application.decided_at = dt.datetime.utcnow()
    if result["ok"]:
        application.status = "submitted"
        application.submitted_at = dt.datetime.utcnow()
        application.confirmation_screenshot_b64 = result.get("confirmation_b64")
    else:
        application.status = "failed"
        application.error_message = result.get("reason")
    db.commit()
    db.refresh(application)

    return _serialize_application(application, job)


@app.post("/apply/{application_id}/reject")
def apply_reject(
    application_id: int,
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Human declines a previewed application. Nothing was ever
    submitted for a rejected row - the browser agent only filled and
    screenshotted the form during /apply/prepare."""
    application = db.query(Application).filter(
        Application.id == application_id, Application.user_id == current_user.id
    ).first()
    if not application:
        raise HTTPException(404, "Application not found.")
    if application.status != "pending_approval":
        raise HTTPException(400, f"Application is '{application.status}', not awaiting approval.")

    application.status = "rejected"
    application.decided_at = dt.datetime.utcnow()
    db.commit()
    return {"message": "Application discarded. Nothing was submitted."}


@app.get("/applications")
def list_applications(
    current_user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    applications = db.query(Application).filter(
        Application.user_id == current_user.id
    ).order_by(Application.created_at.desc()).all()

    results = []
    for a in applications:
        job = db.query(Job).filter(Job.id == a.job_id).first()
        results.append(_serialize_application(a, job))
    return {"count": len(results), "applications": results}


def _serialize_application(a: Application, job: Job | None) -> dict:
    """Shared shape for apply_prepare/apply_confirm/list_applications
    responses, so the frontend has one consistent structure to render
    regardless of which endpoint returned it."""
    return {
        "id": a.id,
        "status": a.status,
        "job": {
            "id": job.id, "title": job.title, "company": job.company,
            "location": job.location, "apply_url": job.apply_url,
        } if job else None,
        "filled_fields": json.loads(a.filled_fields or "[]"),
        "preview_screenshot_b64": a.preview_screenshot_b64,
        "confirmation_screenshot_b64": a.confirmation_screenshot_b64,
        "error_message": a.error_message,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
    }


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
