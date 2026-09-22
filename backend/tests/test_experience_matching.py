"""
Tests for experience-level filtering. This closes a real gap: the
module docstring in matcher.py claimed "location, basic experience
gate" as a hard filter, but no such filter existed anywhere in the
code - UserProfile.experience_level was collected and stored at
registration but never used to influence search results at all.
"""
from app.core.matcher import infer_job_experience_level, experience_matches, find_matches


# ------------------------- infer_job_experience_level -------------------------

def test_infers_senior_from_title_keywords():
    assert infer_job_experience_level("Senior Software Engineer", "") == "5y+"
    assert infer_job_experience_level("Staff Engineer", "") == "5y+"
    assert infer_job_experience_level("Engineering Lead", "") == "5y+"


def test_infers_fresher_from_entry_level_keywords():
    assert infer_job_experience_level("Software Engineer", "Great opportunity for new graduates.") == "fresher"
    assert infer_job_experience_level("Junior Developer", "") == "fresher"
    assert infer_job_experience_level("Software Engineering Intern", "") == "fresher"


def test_infers_from_years_mentioned_in_description():
    assert infer_job_experience_level("Engineer", "Requires 1 year of experience.") == "0-2y"
    assert infer_job_experience_level("Engineer", "3+ years of experience required.") == "2-5y"
    assert infer_job_experience_level("Engineer", "7+ years of experience required.") == "5y+"


def test_returns_unknown_when_no_signal_present():
    assert infer_job_experience_level("Software Engineer", "Build great products with our team.") == "unknown"


def test_senior_title_takes_precedence_over_a_stray_low_number():
    """A senior-titled role that happens to mention '2 years' somewhere
    (e.g. 'own a roadmap spanning 2 years') should still classify as
    senior, not entry-level - title signal is checked first."""
    assert infer_job_experience_level("Senior Engineer", "Own a 2 year product roadmap.") == "5y+"


# ------------------------- experience_matches -------------------------

def test_no_preference_never_filters():
    assert experience_matches("Senior Staff Engineer", "", None) is True
    assert experience_matches("Senior Staff Engineer", "", "") is True
    assert experience_matches("Senior Staff Engineer", "", "not-a-real-level") is True


def test_unknown_classification_never_excludes():
    """Conservative-by-design: most real postings won't cleanly state a
    number, so ambiguous text must never be treated as disqualifying."""
    assert experience_matches("Software Engineer", "Great team, great mission.", "fresher") is True


def test_fresher_user_sees_fresher_and_adjacent_but_not_far_senior():
    assert experience_matches("Junior Developer", "", "fresher") is True
    assert experience_matches("Engineer", "1 year of experience", "fresher") is True  # one tier of slack (0-2y)
    assert experience_matches("Engineer", "3+ years required", "fresher") is False  # two tiers away (2-5y) - correctly excluded
    assert experience_matches("Senior Staff Engineer", "", "fresher") is False  # 5y+ is far too senior


def test_5y_plus_user_is_never_excluded_by_a_lower_tier_posting():
    """A senior candidate is never blocked from a less-senior-leaning
    posting - the filter only ever excludes jobs that need MORE
    experience than the user has, never less."""
    assert experience_matches("Junior Developer", "", "5y+") is True
    assert experience_matches("Senior Engineer", "", "5y+") is True


# ------------------------- find_matches integration -------------------------

def test_find_matches_filters_out_clearly_senior_roles_for_fresher():
    jobs = [
        {"title": "Junior Software Engineer", "description": "", "location": "Remote"},
        {"title": "Senior Staff Software Engineer", "description": "", "location": "Remote"},
    ]
    results = find_matches(jobs, ["Software Engineer"], ["Remote"], experience_level="fresher")
    titles = [j["title"] for j in results]
    assert "Junior Software Engineer" in titles
    assert "Senior Staff Software Engineer" not in titles


def test_find_matches_with_no_experience_level_behaves_as_before():
    """Backward compatibility: omitting experience_level entirely must
    not change existing behavior for any caller that doesn't pass it."""
    jobs = [
        {"title": "Junior Software Engineer", "description": "", "location": "Remote"},
        {"title": "Senior Staff Software Engineer", "description": "", "location": "Remote"},
    ]
    results = find_matches(jobs, ["Software Engineer"], ["Remote"])
    assert len(results) == 2
