"""
Regression test for a production incident: on an Adzuna landing page,
the submit-button selector's naive `.first` match grabbed the page's
own search-bar button instead of the real application form's submit
control (the real form was inside a modal popup, later in the DOM but
not the "first" match). Playwright then spent 10 seconds retrying a
click that a popup overlay kept intercepting, and the application
correctly failed - but only after burning the full timeout, and only
because the wrong button happened to be unclickable. A page where the
wrong button WAS clickable would have silently clicked the wrong thing.

_locate_submit_button() must now refuse to guess whenever there's
genuine ambiguity (multiple visible candidates it can't resolve to the
filled form), rather than defaulting to .first.

These tests use lightweight fakes that mimic the small slice of the
Playwright Locator API _locate_submit_button actually calls
(.count(), .nth(), .is_visible(), .locator()) rather than a real
browser, since Chromium isn't available in every test environment
(see test_apply.py's docstring for the same constraint) - this lets
the decision logic itself be verified deterministically and instantly.
"""
from app.core.apply_agent import _locate_submit_button


class FakeElement:
    """Stands in for a single Playwright element handle."""
    def __init__(self, visible=True, form_id=None):
        self._visible = visible
        self.form_id = form_id  # which fake <form> this element "belongs to"

    def is_visible(self):
        return self._visible

    def locator(self, selector):
        # Used by _locate_submit_button for `filled_elements[0].locator("xpath=ancestor::form[1]")`
        if selector.startswith("xpath=ancestor::form"):
            return FakeLocatorSet([FakeElement(visible=True, form_id=self.form_id)]) if self.form_id else FakeLocatorSet([])
        raise NotImplementedError(selector)


class FakeLocatorSet:
    """Stands in for the result of page.locator(...) - an ordered,
    indexable set of elements."""
    def __init__(self, elements):
        self._elements = elements

    def count(self):
        return len(self._elements)

    def nth(self, i):
        return self._elements[i]

    def locator(self, selector):
        # Mimics Playwright: calling .locator() on a locator scopes the
        # search to that locator's subtree. When this set wraps exactly
        # one element that itself knows how to resolve a sub-locator
        # (a FakeFormElement), delegate to it.
        if len(self._elements) == 1 and hasattr(self._elements[0], "locator"):
            return self._elements[0].locator(selector)
        raise NotImplementedError(selector)


class FakePage:
    """Stands in for a Playwright Page - only implements .locator()
    since that's all _locate_submit_button calls on `page` directly."""
    def __init__(self, submit_buttons):
        self._submit_buttons = submit_buttons

    def locator(self, selector):
        return FakeLocatorSet(self._submit_buttons)


class FakeFormElement(FakeElement):
    """A fake <form> ancestor whose .locator() returns its own scoped submit buttons."""
    def __init__(self, form_id, scoped_submits):
        super().__init__(visible=True, form_id=form_id)
        self._scoped_submits = scoped_submits

    def locator(self, selector):
        if selector.startswith("xpath=ancestor::form"):
            return FakeLocatorSet([self])
        return FakeLocatorSet(self._scoped_submits)


def test_single_visible_submit_button_is_unambiguous():
    """The common case - a normal single-form ATS page - must keep
    working exactly as before: one visible submit button, use it."""
    page = FakePage(submit_buttons=[FakeElement(visible=True)])
    btn, reason = _locate_submit_button(page, filled_elements=[])
    assert btn is not None
    assert reason is None


def test_zero_visible_submit_buttons_refuses_clearly():
    page = FakePage(submit_buttons=[FakeElement(visible=False)])
    btn, reason = _locate_submit_button(page, filled_elements=[])
    assert btn is None
    assert "No visible submit button" in reason


def test_multiple_submit_buttons_with_no_filled_fields_refuses_rather_than_guessing():
    """This is the exact Adzuna scenario's shape: multiple visible
    submit-like buttons on the page, and nothing was filled to anchor
    a decision on (e.g. the real form is behind a popup our field
    matcher never found). Old behavior: silently pick .first. New
    behavior: refuse."""
    page = FakePage(submit_buttons=[FakeElement(visible=True), FakeElement(visible=True)])
    btn, reason = _locate_submit_button(page, filled_elements=[])
    assert btn is None
    assert "2 possible submit buttons" in reason
    assert "refusing to guess" in reason


def test_multiple_submit_buttons_resolves_via_filled_forms_ancestor():
    """When we DID fill fields, and exactly one of the visible submit
    buttons lives inside that same form, that's unambiguous enough to
    use - this is the case that should still work for legitimate
    multi-button pages (e.g. a page with an unrelated newsletter-signup
    button elsewhere, plus the real apply form)."""
    real_submit = FakeElement(visible=True, form_id="apply-form")
    unrelated_submit = FakeElement(visible=True, form_id="newsletter-form")
    filled_field = FakeFormElement(form_id="apply-form", scoped_submits=[real_submit])

    page = FakePage(submit_buttons=[unrelated_submit, real_submit])
    btn, reason = _locate_submit_button(page, filled_elements=[filled_field])
    assert btn is real_submit
    assert reason is None


def test_multiple_submit_buttons_still_refuses_if_scoped_search_stays_ambiguous():
    """Even with filled fields to anchor on, if the scoped form search
    itself finds more than one submit button, still refuse rather than
    guessing among those."""
    submit_a = FakeElement(visible=True)
    submit_b = FakeElement(visible=True)
    filled_field = FakeFormElement(form_id="apply-form", scoped_submits=[submit_a, submit_b])

    page = FakePage(submit_buttons=[submit_a, submit_b])
    btn, reason = _locate_submit_button(page, filled_elements=[filled_field])
    assert btn is None
    assert "refusing to guess" in reason
