"""
Resume parsing: extracts raw text from PDF/DOCX, then pulls out
structured signals (skills, years of experience, education) using a
lightweight keyword approach + optional LLM enrichment.
"""
import re
import io
import pdfplumber
import docx

# A starter skills taxonomy. In production you'd pull this from a larger
# curated list (e.g. LinkedIn skills dataset / O*NET) or generate it
# dynamically via the LLM enrichment step below.
SKILL_KEYWORDS = [
    "python", "java", "c++", "c#", "javascript", "typescript", "react",
    "node.js", "django", "flask", "fastapi", "sql", "mysql", "postgresql",
    "mongodb", "aws", "azure", "gcp", "docker", "kubernetes", "git",
    "machine learning", "deep learning", "nlp", "computer vision",
    "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy",
    "data analysis", "data structures", "algorithms", "rest api",
    "html", "css", "spring boot", "excel", "tableau", "power bi",
    "linux", "ci/cd", "agile", "scrum", "figma", "product management",
]


def extract_text_from_pdf(file_bytes: bytes) -> str:
    text_parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_from_docx(file_bytes: bytes) -> str:
    document = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in document.paragraphs)


def extract_resume_text(file_bytes: bytes, filename: str) -> str:
    """Detects file type by extension and extracts plain text."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    elif lower.endswith(".docx"):
        return extract_text_from_docx(file_bytes)
    else:
        raise ValueError("Unsupported resume format. Please upload a PDF or DOCX.")


def extract_skills(resume_text: str) -> list[str]:
    """Simple case-insensitive keyword match against the skills taxonomy.
    Fast and free - good first pass before LLM-based enrichment."""
    text_lower = resume_text.lower()
    found = []
    for skill in SKILL_KEYWORDS:
        # word-boundary-ish match to avoid partial matches (e.g. "r" inside "react")
        pattern = r"(?<![a-zA-Z0-9])" + re.escape(skill) + r"(?![a-zA-Z0-9])"
        if re.search(pattern, text_lower):
            found.append(skill)
    return found


def estimate_experience_years(resume_text: str) -> float | None:
    """Best-effort regex scan for explicit '<N> years of experience'
    mentions. Not perfect - real systems compute this from date ranges
    in the work-history section, which is a further enhancement."""
    matches = re.findall(r"(\d+(?:\.\d+)?)\+?\s*years?\s+(?:of\s+)?experience", resume_text.lower())
    if matches:
        return max(float(m) for m in matches)
    return None


def parse_resume(file_bytes: bytes, filename: str) -> dict:
    """Full parse pipeline: text -> skills + experience estimate."""
    text = extract_resume_text(file_bytes, filename)
    skills = extract_skills(text)
    experience_years = estimate_experience_years(text)
    return {
        "raw_text": text,
        "skills": skills,
        "experience_years": experience_years,
    }
