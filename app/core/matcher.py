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
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def location_matches(job_location: str, wanted_locations: list[str]) -> bool:
    """Loose match: remote jobs always pass; otherwise substring match
    against any of the user's preferred locations."""
    if not wanted_locations:
        return True
    job_loc_lower = (job_location or "").lower()
    if "remote" in job_loc_lower:
        return True
    return any(loc.strip().lower() in job_loc_lower for loc in wanted_locations if loc.strip())


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
