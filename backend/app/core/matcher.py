"""
Matching engine: given a user profile (job titles wanted, locations,
experience level, resume text) and a pool of Job rows from the DB,
returns a ranked list of best-fit jobs.

Approach: hard filters first (location, basic experience gate) to cut
the search space, then TF-IDF cosine similarity between resume/profile
text and job description for semantic ranking. This avoids needing an
external embeddings API for the MVP - swap in real embeddings + a
vector DB (pgvector/Pinecone) later for better semantic matching.
"""
import json
import re
import difflib
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FUZZY_MATCH_THRESHOLD = 0.82  # 0-1 similarity ratio; tuned to catch typos/near-misses without over-matching

# Same canonical levels UserProfile.experience_level is stored as
# (see app/main.py's /auth/register). Ordinal, low to high.
_LEVEL_ORDER = {"fresher": 0, "0-2y": 1, "2-5y": 2, "5y+": 3}

_SENIOR_PATTERNS = [
    r"\bsenior\b", r"\bsr\.?\b", r"\bstaff\b", r"\bprincipal\b", r"\blead\b",
    r"\bmanager\b", r"\bhead of\b", r"\bdirector\b",
]
_ENTRY_PATTERNS = [
    r"\bentry.?level\b", r"\bjunior\b", r"\bjr\.?\b", r"\bnew grad(uate)?s?\b",
    r"\bgraduate\b", r"\bfresher\b", r"\bintern(ship)?\b", r"\b0.?(-|to)?.?2\s*years?\b",
]
_YEARS_PATTERN = re.compile(r"(\d+)\+?\s*years?")


def _load_city_aliases() -> dict:
    """Loads app/data/city_aliases.json: canonical city -> list of
    alternate names (e.g. 'bangalore' -> ['bengaluru', 'blr'])."""
    try:
        with open(DATA_DIR / "city_aliases.json") as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


_CITY_ALIASES = _load_city_aliases()


def _expand_with_aliases(location: str) -> set[str]:
    """Given one location string the user typed, returns the set of all
    names it could reasonably also appear as in a job listing - itself
    plus any known aliases in either direction. E.g. 'bangalore' expands
    to {'bangalore', 'bengaluru', 'blr'}, and typing 'bengaluru' expands
    to the same set (alias lookup works both ways)."""
    loc = location.strip().lower()
    if not loc:
        return set()
    expanded = {loc}
    for canonical, aliases in _CITY_ALIASES.items():
        names = {canonical, *aliases}
        if loc in names:
            expanded |= names
    return expanded


def _fuzzy_contains(job_loc_lower: str, candidate: str) -> bool:
    """Catches near-misses that aren't exact substrings - typos, minor
    spelling variants ('hydrabad' vs 'hyderabad'), etc. Compares the
    candidate against each word/phrase chunk in the job's location
    string rather than the whole string at once, since a short city name
    compared against a long 'City, State, Country' string would always
    score low on raw ratio."""
    if candidate in job_loc_lower:
        return True
    chunks = [c.strip() for c in job_loc_lower.replace("/", ",").split(",") if c.strip()]
    chunks.append(job_loc_lower)  # also try the whole string, cheap and occasionally useful
    for chunk in chunks:
        ratio = difflib.SequenceMatcher(None, candidate, chunk).ratio()
        if ratio >= FUZZY_MATCH_THRESHOLD:
            return True
    return False


def location_matches(job_location: str, wanted_locations: list[str]) -> bool:
    """Remote jobs always pass. Otherwise: for each location the user
    wants, expand it to known aliases (e.g. Bangalore <-> Bengaluru),
    then check for a substring match, falling back to fuzzy matching to
    catch typos/spelling variants the alias list doesn't cover."""
    if not wanted_locations:
        return True
    job_loc_lower = (job_location or "").lower()
    if "remote" in job_loc_lower or "work from home" in job_loc_lower or "wfh" in job_loc_lower:
        return True

    for loc in wanted_locations:
        if not loc.strip():
            continue
        for candidate in _expand_with_aliases(loc):
            if _fuzzy_contains(job_loc_lower, candidate):
                return True
    return False


def title_prefilter(job_title: str, wanted_titles: list[str]) -> bool:
    """Loose keyword match on title before doing the more expensive
    similarity ranking - keeps totally irrelevant jobs out."""
    if not wanted_titles:
        return True
    title_lower = (job_title or "").lower()
    return any(
        any(word in title_lower for word in wt.lower().split())
        for wt in wanted_titles if wt.strip()
    )


def infer_job_experience_level(job_title: str, job_description: str) -> str:
    """Heuristic classification from free text - Greenhouse/Lever/Adzuna
    don't return structured experience-level metadata, only a title and
    a description, so this is a keyword/pattern read of those, not a
    guaranteed-accurate signal. Returns one of _LEVEL_ORDER's keys, or
    'unknown' when the text gives no real signal either way.

    Order of checks matters: senior-role keywords are checked first
    since a posting can simultaneously mention a low year-count AND a
    senior title (e.g. "Senior Engineer - own a team of 2+ years..."),
    and title-level signals like 'Senior'/'Lead' are more reliable than
    a stray number pulled from anywhere in the text."""
    text = f"{job_title} {job_description}".lower()

    if any(re.search(p, text) for p in _SENIOR_PATTERNS):
        return "5y+"

    years_mentioned = [int(n) for n in _YEARS_PATTERN.findall(text)]
    if years_mentioned:
        max_years = max(years_mentioned)
        if max_years >= 5:
            return "5y+"
        if max_years >= 2:
            return "2-5y"
        return "0-2y"

    if any(re.search(p, text) for p in _ENTRY_PATTERNS):
        return "fresher"

    return "unknown"


def experience_matches(job_title: str, job_description: str, wanted_level: str | None) -> bool:
    """True unless the job clearly requires meaningfully MORE
    experience than wanted_level. Deliberately conservative in two
    ways, matching this file's existing fallback philosophy (see
    find_matches: filters that would wipe out the whole pool fall back
    to no filtering rather than returning nothing):

      - An 'unknown' classification never excludes a job. Most real
        postings won't cleanly state a number, and treating ambiguous
        text as disqualifying would hide plenty of genuinely-relevant
        roles for the sake of a heuristic that was never going to be
        perfect.
      - One tier of slack is allowed either direction (wanted_rank + 1),
        since level boundaries are fuzzy in practice - a 0-2y candidate
        can reasonably apply to a posting that leans '2-5y', and rigid
        exact-tier matching would be more restrictive than any real
        recruiter's screening actually is.

    No wanted_level (None/empty/not a known tier) means no preference
    was set - don't filter at all."""
    if not wanted_level or wanted_level not in _LEVEL_ORDER:
        return True

    inferred = infer_job_experience_level(job_title, job_description)
    if inferred == "unknown":
        return True

    return _LEVEL_ORDER[inferred] <= _LEVEL_ORDER[wanted_level] + 1


def rank_jobs(jobs: list[dict], profile_text: str, top_k: int = 30) -> list[dict]:
    """Ranks a pre-filtered job list by semantic similarity to the
    user's resume/profile text. Returns jobs with an added 'match_score'
    field (0-100), sorted descending.

    sklearn is imported here, not at module level, deliberately:
    scikit-learn pulls in numpy + scipy, which measured at ~10s of
    cumulative import time (see `python -X importtime -c "import
    app.main"`) - over half of this app's total module-import cost.
    Since app.main imports this module directly, that cost used to be
    paid on EVERY cold start (Render free tier spins the instance down
    after ~15min idle), before Uvicorn could even bind a port to answer
    a health check - which is what was causing UptimeRobot to catch the
    app mid-boot and report false 503s. Deferring the import to here
    means it's only paid on the first actual job-search request, not
    on every cold start, and /health responds immediately regardless."""
    if not jobs:
        return []

    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    corpus = [profile_text] + [j.get("description", "") or j.get("title", "") for j in jobs]
    vectorizer = TfidfVectorizer(stop_words="english", max_features=1000)
    tfidf = vectorizer.fit_transform(corpus)

    profile_vec = tfidf[0:1]
    job_vecs = tfidf[1:]
    scores = cosine_similarity(profile_vec, job_vecs)[0]

    for job, score in zip(jobs, scores):
        job["match_score"] = round(float(score) * 100, 1)

    ranked = sorted(jobs, key=lambda j: j["match_score"], reverse=True)
    return ranked[:top_k]


def find_matches(
    jobs: list[dict],
    job_titles: list[str],
    locations: list[str],
    resume_text: str = "",
    top_k: int = 30,
    experience_level: str | None = None,
) -> list[dict]:
    """Full pipeline: hard filters -> semantic ranking."""
    filtered = [
        j for j in jobs
        if title_prefilter(j.get("title", ""), job_titles)
        and location_matches(j.get("location", ""), locations)
        and experience_matches(j.get("title", ""), j.get("description", ""), experience_level)
    ]

    # If filters wiped out everything (e.g. niche title), fall back to
    # ranking the full unfiltered pool rather than returning nothing.
    pool = filtered if filtered else jobs

    profile_text = resume_text or " ".join(job_titles)
    return rank_jobs(pool, profile_text, top_k=top_k)
