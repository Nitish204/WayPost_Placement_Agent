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
import difflib
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FUZZY_MATCH_THRESHOLD = 0.82  # 0-1 similarity ratio; tuned to catch typos/near-misses without over-matching


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


def rank_jobs(jobs: list[dict], profile_text: str, top_k: int = 30) -> list[dict]:
    """Ranks a pre-filtered job list by semantic similarity to the
    user's resume/profile text. Returns jobs with an added 'match_score'
    field (0-100), sorted descending."""
    if not jobs:
        return []

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
) -> list[dict]:
    """Full pipeline: hard filters -> semantic ranking."""
    filtered = [
        j for j in jobs
        if title_prefilter(j.get("title", ""), job_titles)
        and location_matches(j.get("location", ""), locations)
    ]

    # If filters wiped out everything (e.g. niche title), fall back to
    # ranking the full unfiltered pool rather than returning nothing.
    pool = filtered if filtered else jobs

    profile_text = resume_text or " ".join(job_titles)
    return rank_jobs(pool, profile_text, top_k=top_k)
