# Waypost

Full project: FastAPI backend (unchanged business logic) + new Next.js frontend.

```
waypost-final/
├── backend/     ← your original FastAPI app, unchanged except see note below
└── frontend/    ← new Next.js + Tailwind + Framer Motion app
```

## Run locally

**Backend:**
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in values
uvicorn app.main:app --reload --port 8000
```

**Frontend** (separate terminal):
```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE=http://localhost:8000" > .env.local
npm run dev
```
Open http://localhost:3000

## What changed in `backend/`
Nothing in the Python code. Two manual steps left for you:
1. `backend/app/static/` (the old HTML frontend) is no longer used by the new
   frontend — safe to delete once you've confirmed `frontend/` works, or leave
   it as a fallback.
2. When you deploy, set `ALLOWED_ORIGINS` in your host's environment variables
   to your deployed frontend URL, e.g. `https://waypost.vercel.app` — the
   backend already reads this env var, no code change needed.

## What's in `frontend/`
- `app/page.tsx` — landing + login/register/forgot-password
- `app/dashboard/page.tsx` — job search, resume upload, ATS score, agent chat, Telegram linking
- `components/` — shared UI (Panel, Button, Input, Badge) + feature panels (ForgotPassword, AtsScorePanel, AgentChat, TelegramLink)
- `lib/api.ts` — typed client, one function per backend route
- `public/` — favicons copied over from the old `app/static/`

Every API call in `lib/api.ts` maps 1:1 to a real route in `backend/app/main.py`.

Font note: `frontend/app/layout.tsx` uses system fonts as a fallback. To get
the intended Space Grotesk + JetBrains Mono, uncomment the `next/font/google`
block at the top of that file — commented out only because this was built in
a sandbox without internet access to fonts.googleapis.com. Works normally on
your machine.

## Deploying
- Backend → Render (as before)
- Frontend → Vercel: set `NEXT_PUBLIC_API_BASE` to your Render backend URL in Vercel's environment variables
