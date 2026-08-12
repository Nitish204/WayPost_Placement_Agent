"""
Agent orchestration layer.

This is the "AI agent" part in the product sense: an LLM is given a set
of tools (search jobs, analyze resume, compute ATS score) and a user's
natural-language request, and it decides which tools to call and in
what order, then synthesizes a final answer.

Provider-agnostic: works with Gemini (free tier, default) or Anthropic
(paid) via app/core/llm.py - see that file to switch providers.
"""
import logging

from app.db import SessionLocal, Job
from app.core.matcher import find_matches
from app.core.ats_scorer import compute_ats_score
from app.core.ingest import run_ingestion_cycle
from app.core.llm import agent_loop

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Tool implementations (these are the "hands" of the agent - plain
# Python functions the LLM can choose to invoke)
# ---------------------------------------------------------------------

def tool_search_jobs(job_titles: list[str], locations: list[str], resume_text: str = "", top_k: int = 15) -> dict:
    """Searches the current job DB pool for matches. Triggers a fresh
    ingestion cycle first if the pool looks stale/empty for this query,
    so the user always gets a real attempt at fresh results."""
    db = SessionLocal()
    try:
        existing = db.query(Job).filter(Job.is_active == True).all()  # noqa: E712
        if len(existing) < 5:
            # pool too thin - trigger a live fetch for the first title/location given
            query = job_titles[0] if job_titles else ""
            loc = locations[0] if locations else ""
            run_ingestion_cycle(db, search_query=query, search_location=loc)
            existing = db.query(Job).filter(Job.is_active == True).all()  # noqa: E712

        job_dicts = [
            {
                "title": j.title, "company": j.company, "location": j.location,
                "description": j.description, "apply_url": j.apply_url, "source": j.source,
            }
            for j in existing
        ]
        matches = find_matches(job_dicts, job_titles, locations, resume_text, top_k=top_k)
        # Trim descriptions for the LLM context - it doesn't need the full JD
        for m in matches:
            m["description"] = (m.get("description") or "")[:300]
        return {"count": len(matches), "jobs": matches}
    finally:
        db.close()


def tool_ats_score(resume_text: str, job_description: str) -> dict:
    """Computes an ATS-style estimate score comparing resume to a JD."""
    return compute_ats_score(resume_text, job_description, use_llm=True)


TOOL_DEFINITIONS = [
    {
        "name": "search_jobs",
        "description": (
            "Search for job/internship openings matching desired titles and "
            "locations. Optionally boost ranking using resume text for "
            "personalized relevance. Use this whenever the user wants to "
            "find opportunities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_titles": {"type": "array", "items": {"type": "string"}, "description": "Desired job titles/roles, e.g. ['Software Engineer Intern']"},
                "locations": {"type": "array", "items": {"type": "string"}, "description": "Desired locations, e.g. ['Bangalore', 'Remote']"},
                "resume_text": {"type": "string", "description": "Plain text of the user's resume, if available, to personalize ranking"},
                "top_k": {"type": "integer", "description": "Max number of results to return", "default": 15},
            },
            "required": ["job_titles", "locations"],
        },
    },
    {
        "name": "ats_score",
        "description": (
            "Compute an ATS-style match score and feedback comparing a "
            "resume against a specific job description. Use this when the "
            "user provides both a resume and a target job description."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resume_text": {"type": "string"},
                "job_description": {"type": "string"},
            },
            "required": ["resume_text", "job_description"],
        },
    },
]

TOOL_IMPL = {
    "search_jobs": lambda **kwargs: tool_search_jobs(**kwargs),
    "ats_score": lambda **kwargs: tool_ats_score(**kwargs),
}

SYSTEM_PROMPT = """You are a placement/job-search assistant agent for students \
and early-career job seekers, especially those without campus placement \
support who need help discovering off-campus opportunities.

You have tools to search live job postings and to score a resume against \
a job description (ATS-style estimate). Use tools whenever the user's \
request needs current data - don't guess at job listings from memory.

Be direct and practical. When presenting jobs, include company, title, \
location, and apply link. When giving ATS/resume feedback, be specific \
and actionable, not generic. If the user hasn't given enough info (e.g. \
no job title or location for a search), ask ONE clarifying question \
before calling a tool with incomplete/guessed input."""


def run_agent(user_message: str, resume_text: str = "", max_turns: int = 5) -> str:
    """Runs the tool-use agent loop for one user turn (Gemini by default,
    Anthropic if that's the configured provider instead). Returns the
    final text response after any tool calls are resolved."""
    context_note = f"\n\n[User's resume text is available - {len(resume_text)} chars]" if resume_text else ""

    # Wrap tool impls so resume_text gets auto-injected when the model
    # omits it (keeps behavior identical to the previous implementation).
    def search_jobs_wrapped(**kwargs):
        if resume_text and "resume_text" not in kwargs:
            kwargs["resume_text"] = resume_text
        return tool_search_jobs(**kwargs)

    def ats_score_wrapped(**kwargs):
        if resume_text and not kwargs.get("resume_text"):
            kwargs["resume_text"] = resume_text
        return tool_ats_score(**kwargs)

    tool_impl = {
        "search_jobs": search_jobs_wrapped,
        "ats_score": ats_score_wrapped,
    }

    return agent_loop(
        system_prompt=SYSTEM_PROMPT,
        user_message=user_message + context_note,
        tool_defs=TOOL_DEFINITIONS,
        tool_impl=tool_impl,
        max_turns=max_turns,
    )
