"""
Browser application agent (Playwright).

Design mirrors the two-stage pattern used throughout this codebase for
anything irreversible (see auth.py's reset-token flow): nothing is
submitted in one step.

  prepare_application()  - loads the job's REAL apply_url, matches and
    fills form fields it recognizes (name/email/phone/cover letter),
    takes a full-page screenshot, and returns. It never clicks submit.

  confirm_submit()       - only ever called from POST /apply/{id}/confirm,
    which itself only runs after a human has seen the prepare_application
    screenshot and approved it. Re-runs the same fill sequence (most ATS
    forms don't persist state between page loads, so the safest way to
    guarantee the submitted form matches what was previewed is to fill
    it fresh rather than trying to keep one browser session alive across
    an HTTP round-trip) and clicks the actual submit control.

Both are synchronous (Playwright sync API) since this project's request
handlers are plain sync FastAPI routes, not async - consistent with the
existing source adapters (greenhouse.py etc.) using `requests`, not
`httpx.AsyncClient`.
"""
import re
import base64
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_FIELD_PATTERNS = {
    "name": [r"full.?name", r"^name$", r"your name", r"applicant.?name"],
    "email": [r"email"],
    "phone": [r"phone", r"mobile", r"contact number"],
    "cover_letter": [r"cover.?letter", r"why.*interest", r"message", r"additional information"],
}
_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in _FIELD_PATTERNS.items()}

_TEXT_INPUT_SELECTOR = "input[type='text'], input[type='email'], input[type='tel'], textarea"
_SUBMIT_SELECTOR = "button[type='submit'], input[type='submit']"


def _match_field(label: str) -> str | None:
    for key, patterns in _COMPILED.items():
        if any(p.search(label or "") for p in patterns):
            return key
    return None


def _resolve_label(page, el) -> str:
    """Best-effort label resolution: explicit ARIA label, then
    placeholder text, then a <label for=id>. Real ATS forms vary a lot
    here, so this is intentionally a chain of fallbacks rather than one
    lookup - matches the same defensive pattern the frontend uses when
    resolving figures from external APIs (Adzuna/Greenhouse) that don't
    guarantee every field is present."""
    for attr in ("aria-label", "placeholder"):
        val = el.get_attribute(attr)
        if val:
            return val
    el_id = el.get_attribute("id")
    if el_id:
        label_loc = page.locator(f'label[for="{el_id}"]')
        if label_loc.count():
            try:
                return label_loc.first.inner_text()
            except Exception:
                pass
    name_attr = el.get_attribute("name")
    return name_attr or ""


def _fill_form(page, candidate: dict) -> tuple[list[dict], list]:
    """Returns (filled_field_descriptions, filled_element_handles) -
    the handles are needed by _locate_submit_button to scope its search
    to the actual form that got filled, not just any submit-looking
    control on the page."""
    values = {
        "name": candidate.get("name", ""),
        "email": candidate.get("email", ""),
        "phone": candidate.get("phone", ""),
        "cover_letter": candidate.get("cover_note", ""),
    }
    filled = []
    filled_elements = []
    inputs = page.locator(_TEXT_INPUT_SELECTOR)
    count = inputs.count()
    for i in range(count):
        el = inputs.nth(i)
        label = _resolve_label(page, el)
        key = _match_field(label)
        if not key or not values.get(key):
            continue
        try:
            el.fill(values[key])
            filled.append({"field": key, "label": label})
            filled_elements.append(el)
        except Exception as e:
            logger.info(f"[apply_agent] could not fill field '{label}': {e}")
    return filled, filled_elements


def _locate_submit_button(page, filled_elements: list):
    """Picks the submit control to click - or refuses, rather than
    guessing, when it can't be confident.

    Production incident that motivated this: on an Adzuna landing page,
    the naive `.first` match on all submit-like elements grabbed the
    page's own search-bar button (id="search-btn"), not the real
    application form's submit - because DOM order, not relevance, was
    the only signal being used. The real apply form was inside a modal
    popup that happened to come later in the page.

    Strategy, in order:
      1. Collect only VISIBLE candidates (a hidden duplicate template
         button shouldn't count).
      2. Exactly one visible candidate -> unambiguous, use it. This is
         the common case (a normal single-form ATS page).
      3. Multiple visible candidates -> try to narrow to the one whose
         nearest <form> ancestor is the same form we actually filled
         fields in. If that narrows it to exactly one, use it.
      4. Still ambiguous (multiple candidates, no filled fields to
         anchor on, or the scoped search still finds >1) -> refuse.
         Returning a clear reason and no button is the correct outcome
         here, not a best-effort guess - clicking the wrong control on
         a real application page is worse than not submitting at all.

    Returns (locator_or_None, reason_string_or_None).
    """
    all_submits = page.locator(_SUBMIT_SELECTOR)
    total = all_submits.count()
    visible_indices = [i for i in range(total) if all_submits.nth(i).is_visible()]

    if not visible_indices:
        return None, "No visible submit button found on the apply page - manual submission required."

    if len(visible_indices) == 1:
        return all_submits.nth(visible_indices[0]), None

    if filled_elements:
        try:
            target_form = filled_elements[0].locator("xpath=ancestor::form[1]")
            if target_form.count() > 0:
                scoped = target_form.locator(_SUBMIT_SELECTOR)
                scoped_visible = [i for i in range(scoped.count()) if scoped.nth(i).is_visible()]
                if len(scoped_visible) == 1:
                    return scoped.nth(scoped_visible[0]), None
        except Exception as e:
            logger.info(f"[apply_agent] form-scoped submit search failed: {e}")

    return None, (
        f"{len(visible_indices)} possible submit buttons found on this page and none could be "
        "confidently matched to the form that was filled - refusing to guess which one is the "
        "real application submit. Manual submission required."
    )


def prepare_application(apply_url: str, candidate: dict, timeout_ms: int = 20000) -> dict:
    """Returns:
        {"ok": True, "filled_fields": [...], "screenshot_b64": "...",
         "submit_target_found": bool, "submit_warning": str|None}
      or
        {"ok": False, "reason": "..."}
    Never raises - callers (routes) treat a failed prepare as a normal,
    displayable outcome, not a 500.

    submit_target_found/submit_warning let the approval preview flag
    "this page has an ambiguous submit target" BEFORE a human approves
    it, rather than only discovering that at confirm time - the same
    _locate_submit_button check confirm_submit will run is run here
    too, just without ever clicking."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=timeout_ms)
                filled, filled_elements = _fill_form(page, candidate)
                submit_btn, submit_warning = _locate_submit_button(page, filled_elements)
                screenshot_bytes = page.screenshot(full_page=True)
            finally:
                browser.close()
        return {
            "ok": True,
            "filled_fields": filled,
            "screenshot_b64": base64.b64encode(screenshot_bytes).decode("ascii"),
            "submit_target_found": submit_btn is not None,
            "submit_warning": submit_warning,
        }
    except Exception as e:
        logger.warning(f"[apply_agent] prepare failed for {apply_url}: {e}")
        return {"ok": False, "reason": str(e)}


def confirm_submit(apply_url: str, candidate: dict, timeout_ms: int = 20000) -> dict:
    """Returns:
        {"ok": True, "confirmation_b64": "..."}
      or
        {"ok": False, "reason": "..."}
    This is the ONLY function in this module that clicks a submit
    control. It must only ever be called after a human approval - see
    the module docstring and routes/POST /apply/{id}/confirm in main.py.

    Uses _locate_submit_button rather than a blind .first match - see
    that function's docstring for the incident that made this necessary."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=timeout_ms)
                _, filled_elements = _fill_form(page, candidate)

                submit_btn, reason = _locate_submit_button(page, filled_elements)
                if submit_btn is None:
                    return {"ok": False, "reason": reason}

                submit_btn.click(timeout=10000)
                page.wait_for_load_state("networkidle", timeout=15000)

                confirmation_bytes = page.screenshot(full_page=True)
            finally:
                browser.close()
        return {"ok": True, "confirmation_b64": base64.b64encode(confirmation_bytes).decode("ascii")}
    except Exception as e:
        logger.warning(f"[apply_agent] submit failed for {apply_url}: {e}")
        return {"ok": False, "reason": str(e)}
