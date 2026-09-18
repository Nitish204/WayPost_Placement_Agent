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


def _fill_form(page, candidate: dict) -> list[dict]:
    values = {
        "name": candidate.get("name", ""),
        "email": candidate.get("email", ""),
        "phone": candidate.get("phone", ""),
        "cover_letter": candidate.get("cover_note", ""),
    }
    filled = []
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
        except Exception as e:
            logger.info(f"[apply_agent] could not fill field '{label}': {e}")
    return filled


def prepare_application(apply_url: str, candidate: dict, timeout_ms: int = 20000) -> dict:
    """Returns:
        {"ok": True, "filled_fields": [...], "screenshot_b64": "..."}
      or
        {"ok": False, "reason": "..."}
    Never raises - callers (routes) treat a failed prepare as a normal,
    displayable outcome, not a 500."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=timeout_ms)
                filled = _fill_form(page, candidate)
                screenshot_bytes = page.screenshot(full_page=True)
            finally:
                browser.close()
        return {
            "ok": True,
            "filled_fields": filled,
            "screenshot_b64": base64.b64encode(screenshot_bytes).decode("ascii"),
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
    the module docstring and routes/POST /apply/{id}/confirm in main.py."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=timeout_ms)
                _fill_form(page, candidate)

                submit_btn = page.locator(_SUBMIT_SELECTOR).first
                if submit_btn.count() == 0:
                    return {"ok": False, "reason": "No submit button found on the apply page - manual submission required."}
                submit_btn.click(timeout=10000)
                page.wait_for_load_state("networkidle", timeout=15000)

                confirmation_bytes = page.screenshot(full_page=True)
            finally:
                browser.close()
        return {"ok": True, "confirmation_b64": base64.b64encode(confirmation_bytes).decode("ascii")}
    except Exception as e:
        logger.warning(f"[apply_agent] submit failed for {apply_url}: {e}")
        return {"ok": False, "reason": str(e)}
