# Waypost — Placement Finder Agent

An AI agent that continuously discovers job/internship opportunities
(especially off-campus ones that don't get advertised well), matches
them against a student's profile/resume, scores resumes against job
descriptions ATS-style, and proactively emails/Telegrams you the
moment a strong match appears.

## What's included

```
app/
  db.py                 - SQLite/Postgres models (Job, UserProfile, MatchResult)
  main.py                - FastAPI app exposing all endpoints
  agent.py                - Claude tool-calling orchestrator (the "agent" brain)
  scheduler.py            - Background job that keeps fetching new postings continuously
  static/index.html        - Login/register + dashboard frontend (served at /)
  data/
    companies.json           - Curated fallback Greenhouse/Lever board list, used
                                automatically if GREENHOUSE_BOARDS/LEVER_BOARDS are unset
    sample_jobs.json          - 5 sample job postings for offline testing/demo,
                                seeded via POST /jobs/seed-sample - no API keys needed
  core/
    auth.py                - Password hashing (bcrypt) + JWT issue/verify
    notifier.py             - Email (SMTP) + Telegram bot message delivery
    matching_notify.py      - Detects new high-score matches after each ingest, notifies
    resume_parser.py        - PDF/DOCX -> text -> skills/experience extraction
    ats_scorer.py             - Keyword + formatting + LLM-based ATS score & feedback
    matcher.py                 - Filters + TF-IDF ranking of jobs against a profile
    ingest.py                   - Pulls from all sources, dedups, stores in DB
  sources/
    greenhouse.py            - Public Greenhouse job board API (free, no key)
    lever.py                   - Public Lever job board API (free, no key)
    adzuna.py                   - Adzuna job search aggregator (free tier, needs signup)
```

## Auth

Every endpoint except `/auth/register`, `/auth/login`, and `/health` now
requires a Bearer token. Register or log in to get a JWT
(`access_token`), then send it as `Authorization: Bearer <token>` on
every other call. Passwords are hashed with bcrypt; tokens expire after
`JWT_EXPIRE_MINUTES` (default 24h).

## Notifications

After every ingestion cycle (scheduled or manual), the app scores every
registered user's profile against the current job pool. Any job that's
new to that user *and* scores at or above their `match_score_threshold`
(default 40%) triggers a batched email and/or Telegram message,
depending on what they've enabled in their profile. Already-notified
matches are tracked in `MatchResult.notified` so nobody gets the same
alert twice.

- **Email**: set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` in `.env`.
  For Gmail, use an [app password](https://myaccount.google.com/apppasswords),
  not your real password.
- **Telegram**: create a bot via [@BotFather](https://t.me/BotFather),
  put the token in `TELEGRAM_BOT_TOKEN`, then each user links their own
  chat via `POST /notifications/telegram/link` (get your chat ID by
  messaging [@userinfobot](https://t.me/userinfobot)).

Both channels are optional — the app runs fine with neither configured,
it just logs a warning and skips sending.

## Why this source strategy

LinkedIn/Naukri scraping is against their ToS and gets IPs banned fast -
avoided entirely here. Instead:
- **Greenhouse & Lever public APIs**: many startups/scale-ups (the "off
  campus" opportunities students miss) post directly here, and these
  endpoints are genuinely public/unauthenticated by design.
- **Adzuna**: broad aggregator with a real free-tier API for wider
  coverage, including bigger/older companies not on Greenhouse/Lever.

You can add more sources later (RemoteOK, WeWorkRemotely both have
public feeds too) by dropping a new file in `app/sources/` following
the same `fetch_jobs() -> list[dict]` pattern.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: add your ANTHROPIC_API_KEY, JWT_SECRET_KEY (generate with
# `python -c "import secrets; print(secrets.token_hex(32))"`), and
# optionally ADZUNA keys, GREENHOUSE_BOARDS/LEVER_BOARDS, SMTP_*, and
# TELEGRAM_BOT_TOKEN for notifications

uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` for the login/dashboard UI, or use the API
directly as shown below.

**No API keys yet?** Register/log in, then call `POST /jobs/seed-sample`
to load 5 sample jobs with zero external calls - lets you test search,
matching, and the notification flow immediately, before setting up
Adzuna/Greenhouse/Lever/SMTP/Telegram. Also, if `GREENHOUSE_BOARDS` and
`LEVER_BOARDS` are left blank in `.env`, the app automatically falls
back to a small curated list in `app/data/companies.json` so real
ingestion still pulls something on a fresh install.

The scheduler starts automatically with the app and polls sources every
`INGEST_INTERVAL_MINUTES` (default 60) for every distinct title+location
combination any registered user has asked for - this is the "agent
continuously working" behavior.

## Using it

### 1. Register (returns a JWT)
```bash
curl -X POST http://localhost:8000/auth/register \
  -F "name=Jane Doe" -F "email=jane@example.com" -F "password=supersecret123" \
  -F "job_titles=Software Engineer Intern,Backend Developer" \
  -F "locations=Bangalore,Remote" \
  -F "experience_level=fresher"
# -> {"access_token": "...", "user": {"id": 1, ...}}
```

Save the token: `TOKEN=<access_token from above>`. Every call below
sends `-H "Authorization: Bearer $TOKEN"`.

### 2. Log in later
```bash
curl -X POST http://localhost:8000/auth/login \
  -F "email=jane@example.com" -F "password=supersecret123"
```

### 3. Upload resume
```bash
curl -X POST http://localhost:8000/resume/upload \
  -H "Authorization: Bearer $TOKEN" -F "file=@/path/to/resume.pdf"
```

### 4. Search jobs (uses your uploaded resume automatically)
```bash
curl -X POST http://localhost:8000/jobs/search \
  -H "Authorization: Bearer $TOKEN" \
  -F "job_titles=Software Engineer Intern" -F "locations=Bangalore,Remote"
```

### 5. ATS score against a specific JD
```bash
curl -X POST http://localhost:8000/resume/ats-score \
  -H "Authorization: Bearer $TOKEN" \
  -F "job_description=We need a Python developer with Django and AWS experience..."
```

### 6. Talk to the agent directly (natural language)
```bash
curl -X POST http://localhost:8000/agent/chat \
  -H "Authorization: Bearer $TOKEN" \
  -F "message=Find me remote data analyst internships in India"
```
The agent decides on its own whether to call `search_jobs`, `ats_score`,
both, or ask a clarifying question first.

### 7. Manually trigger a fetch cycle (in addition to the scheduled one)
```bash
curl -X POST http://localhost:8000/jobs/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -F "search_query=data analyst" -F "search_location=Bangalore"
```

### 8. Link Telegram for alerts
```bash
curl -X POST http://localhost:8000/notifications/telegram/link \
  -H "Authorization: Bearer $TOKEN" -F "telegram_chat_id=123456789"
```

## Honest limitations of this MVP (read before showing to users)

1. **ATS score is an estimate.** Real ATS platforms (Workday, Taleo,
   iCIMS) use proprietary parsing - communicate this to users clearly
   (the API response includes a `disclaimer` field for this reason).
2. **Matching uses TF-IDF, not real embeddings.** Good enough to start
   validating the product, but swap in an embeddings model + vector DB
   (pgvector/Pinecone) for meaningfully better semantic matching once
   you have real usage data to justify the added cost/complexity.
3. **No LinkedIn/Naukri source** by design (ToS risk) - coverage is
   currently limited to companies on Greenhouse/Lever + whatever Adzuna
   indexes. Expand via more public/legit APIs, not scraping.
4. **Single-process scheduler** (APScheduler in-process) is fine for an
   MVP/small user base. Move to Celery beat + workers or a serverless
   cron once you have real concurrent load.
5. **JWT secret must be changed before deploying.** `.env.example` ships
   a placeholder `JWT_SECRET_KEY` - generate a real one (see `.env.example`)
   or anyone can forge tokens.
6. **No password reset flow yet.** Registration/login work; "forgot
   password" would need an email-based reset token, not yet built.
7. **Telegram linking is manual** (paste your chat ID). A bot `/start`
   handler that captures it automatically would be smoother.

## Suggested next steps
- Add a `/auth/forgot-password` flow (reset token emailed via the same
  SMTP config used for job alerts)
- Add RemoteOK / WeWorkRemotely as additional free sources
- Replace TF-IDF matching with real embeddings once validated
- Add a Telegram bot `/start` handler to auto-capture chat_id instead of manual linking
