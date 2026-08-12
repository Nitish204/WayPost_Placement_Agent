"""
ATS (Applicant Tracking System) scoring.

IMPORTANT HONESTY NOTE (surface this to users in the UI too):
Real ATS platforms (Workday, Taleo, iCIMS) use proprietary parsing logic
we can't replicate exactly. This module gives a reasonable ESTIMATE based
on the same signals most ATS systems actually use: keyword/skill overlap
with the job description, and resume formatting hygiene. It should be
framed to users as "an ATS-style estimate," not a guarantee.
"""
import os
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.core.llm import simple_completion


def keyword_overlap_score(resume_text: str, jd_text: str) -> dict:
    """TF-IDF cosine similarity between resume and job description, plus
    an explicit list of JD keywords missing from the resume. This is the
    core signal most real ATS keyword-matchers use."""
    vectorizer = TfidfVectorizer(stop_words="english", max_features=500)
    tfidf = vectorizer.fit_transform([resume_text, jd_text])
    similarity = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]

    # Extract top JD keywords (by tfidf weight in the JD alone) not present in resume
    feature_names = vectorizer.get_feature_names_out()
    jd_vector = tfidf[1].toarray()[0]
    resume_lower = resume_text.lower()

    jd_keywords_ranked = sorted(
        zip(feature_names, jd_vector), key=lambda x: x[1], reverse=True
    )
    missing_keywords = []
    for kw, weight in jd_keywords_ranked:
        if weight <= 0:
            continue
        if kw.lower() not in resume_lower:
            missing_keywords.append(kw)
        if len(missing_keywords) >= 15:
            break

    return {
        "similarity_score": round(float(similarity) * 100, 1),  # 0-100
        "missing_keywords": missing_keywords,
    }


def formatting_checks(resume_text: str) -> dict:
    """Heuristic checks for things that commonly break real ATS parsers.
    Since we only have extracted text (not the original file structure),
    these are best-effort proxies."""
    issues = []
    warnings = []

    if len(resume_text.strip()) < 200:
        issues.append("Resume text is very short - parsing may have failed, or content is too sparse.")

    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", resume_text):
        issues.append("No email address detected - ATS systems may reject applications without contact info.")

    if not re.search(r"(\+?\d[\d\-\s()]{7,}\d)", resume_text):
        warnings.append("No phone number detected.")

    bullet_count = resume_text.count("•") + len(re.findall(r"\n\s*-\s+", resume_text))
    if bullet_count < 3:
        warnings.append("Few or no bullet points detected - ATS and recruiters both favor bulleted, scannable experience sections.")

    word_count = len(resume_text.split())
    if word_count > 1200:
        warnings.append("Resume text is quite long - consider trimming to 1-2 pages for better scannability.")

    return {"issues": issues, "warnings": warnings}


def llm_qualitative_feedback(resume_text: str, jd_text: str) -> str:
    """Uses whichever LLM provider is configured (Gemini by default,
    Anthropic if that's set instead) to give human-readable, actionable
    feedback: weak bullet points, missing quantification, alignment gaps.
    Falls back gracefully if no provider is configured."""
    prompt = f"""You are an expert technical recruiter and resume coach.
Compare the RESUME to the JOB DESCRIPTION below and give concise, actionable feedback.

Return your feedback as 4-6 short bullet points covering:
- Key skill/requirement gaps between resume and JD
- Weak or vague bullet points that should be quantified
- Any structural/clarity issues
- One concrete rewrite suggestion for the weakest bullet point

RESUME:
{resume_text[:6000]}

JOB DESCRIPTION:
{jd_text[:4000]}
"""
    return simple_completion(prompt, max_tokens=800)


def compute_ats_score(resume_text: str, jd_text: str, use_llm: bool = True) -> dict:
    """Full ATS report: numeric score + missing keywords + formatting
    issues + optional LLM qualitative feedback."""
    kw = keyword_overlap_score(resume_text, jd_text)
    fmt = formatting_checks(resume_text)

    # Composite score: 70% keyword match, 30% formatting hygiene
    formatting_penalty = len(fmt["issues"]) * 10 + len(fmt["warnings"]) * 3
    formatting_score = max(0, 100 - formatting_penalty)
    composite = round(kw["similarity_score"] * 0.7 + formatting_score * 0.3, 1)

    result = {
        "ats_score_estimate": composite,
        "keyword_similarity": kw["similarity_score"],
        "missing_keywords": kw["missing_keywords"],
        "formatting_issues": fmt["issues"],
        "formatting_warnings": fmt["warnings"],
        "disclaimer": (
            "This is an estimate based on keyword overlap and formatting "
            "heuristics. Real ATS platforms (Workday, Taleo, iCIMS) use "
            "proprietary logic and results may vary."
        ),
    }

    if use_llm:
        result["qualitative_feedback"] = llm_qualitative_feedback(resume_text, jd_text)

    return result
