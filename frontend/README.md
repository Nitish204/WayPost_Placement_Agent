# Waypost — Next.js frontend

Rebuild of the original vanilla-HTML frontend (previously `app/static/index.html`
in the FastAPI repo) as a proper Next.js 14 (App Router) + TypeScript + Tailwind
+ Framer Motion app.

## What changed vs the original
- Same visual identity (yellow/black/cream, hard offset shadows) but consolidated
  into one token system (`tailwind.config.ts`) instead of drifting per-component.
- Real component reuse: `Panel`, `Button`, `Input`, `Badge` used everywhere,
  instead of one-off inline-styled HTML blocks.
- One orchestrated entrance animation on the landing page (staggered rise),
  purposeful hover/active feedback on interactive elements — not generic
  fade-up-everything.
- Every API call in `lib/api.ts` maps 1:1 to a real FastAPI route — nothing
  invented. See the map at the bottom of this file.

## Backend is untouched
This only replaces `app/static/`. Your FastAPI backend (`app/main.py` and
everything under `app/core`, `app/sources`, etc.) stays exactly as-is — it's
just an API now instead of also serving the frontend.

## Setup
```bash
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_BASE at your running FastAPI backend
npm run dev
```

Font note: `app/layout.tsx` currently falls back to system fonts because this
was built in a sandboxed environment without access to fonts.googleapis.com.
On your machine, uncomment the `next/font/google` block at the top of that
file (Space Grotesk + JetBrains Mono, matching the original design) — it'll
self-host the fonts at build time with zero extra setup.

## Deploying
- Deploy this Next.js app to Vercel (or anywhere) separately from the FastAPI backend.
- Set `NEXT_PUBLIC_API_BASE` to your deployed FastAPI URL (e.g. the Render URL).
- On the FastAPI side, set `ALLOWED_ORIGINS` to this app's deployed URL so CORS allows it.
- You can then delete `app/static/` from the FastAPI repo entirely, or leave it
  as a fallback — your call.

## Route map (frontend → backend)
| Frontend action | Backend route |
|---|---|
| Login | `POST /auth/login` |
| Register | `POST /auth/register` |
| Load profile | `GET /auth/me` |
| Upload resume | `POST /resume/upload` |
| ATS score | `POST /resume/ats-score` |
| Search jobs | `POST /jobs/search` |
| Load sample jobs | `POST /jobs/seed-sample` |
| Update profile | `POST /profile/update` |
| Link Telegram | `POST /notifications/telegram/link` |
| Agent chat | `POST /agent/chat` |

## Still to wire up (present in backend, not yet in UI)
- `/auth/security-question` + `/auth/reset-with-security-answer` (forgot-password flow) — API client method exists in `lib/api.ts`, just needs a UI form.
- `/resume/ats-score` — client method exists, needs a UI panel (paste a JD, see score).
- `/agent/chat` — client method exists, needs a chat UI.
- Telegram linking — client method exists, needs a settings UI.

These weren't cut for a reason — I focused on the core loop (auth → resume →
search) first so you have something real to look at. Say the word and I'll
wire the rest in the same pass.
