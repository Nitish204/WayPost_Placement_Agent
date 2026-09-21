"""
Regression coverage for a likely cause of "auto-apply throws an error
and asks to manually apply" on otherwise-normal-looking jobs: many real
ATS integrations (a Greenhouse form embedded on a company's own custom
careers page, Workday, others) render the actual application form
inside an <iframe>. Playwright's page.locator() only searches the
top-level document - before this fix, _fill_form would find zero
fields on any such page, report an empty filled_fields list, and
downstream _locate_submit_button would then either find no matching
button or (worse, pre-disambiguation-fix) grab something unrelated on
the main page.

_fill_form must now search every frame on the page and use whichever
frame actually has matching fields.
"""
from app.core.apply_agent import _fill_form


class FakeInputElement:
    def __init__(self, aria_label=None, fill_should_fail=False):
        self._aria_label = aria_label
        self._fill_should_fail = fill_should_fail
        self.filled_value = None

    def get_attribute(self, attr):
        if attr == "aria-label":
            return self._aria_label
        return None

    def fill(self, value):
        if self._fill_should_fail:
            raise RuntimeError("element not fillable")
        self.filled_value = value


class FakeInputLocatorSet:
    def __init__(self, elements):
        self._elements = elements

    def count(self):
        return len(self._elements)

    def nth(self, i):
        return self._elements[i]


class FakeFrame:
    """Stands in for a Playwright Frame (or Page, which shares the
    same relevant API surface) - only implements what _fill_form and
    _resolve_label actually call."""
    def __init__(self, inputs=None):
        self._inputs = inputs or []

    def locator(self, selector):
        # _resolve_label may also call frame.locator('label[for="..."]')
        # - not needed for these tests since inputs use aria-label directly.
        return FakeInputLocatorSet(self._inputs)


class FakePageWithFrames:
    def __init__(self, frames):
        self._frames = frames

    @property
    def frames(self):
        return self._frames

    @property
    def main_frame(self):
        return self._frames[0]


CANDIDATE = {"name": "Nitish", "email": "nitish@example.com", "phone": "9999999999", "cover_note": "Hello"}


def test_fill_form_finds_fields_in_main_frame_when_present():
    """Regression: the common case (no iframe involved) must keep
    working exactly as before."""
    email_input = FakeInputElement(aria_label="Email Address")
    main_frame = FakeFrame(inputs=[email_input])
    page = FakePageWithFrames(frames=[main_frame])

    filled, filled_elements, target_frame = _fill_form(page, CANDIDATE)

    assert len(filled) == 1
    assert filled[0]["field"] == "email"
    assert email_input.filled_value == "nitish@example.com"
    assert target_frame is main_frame


def test_fill_form_finds_fields_inside_an_iframe():
    """The actual fix: main frame has nothing, but a second frame
    (representing an embedded ATS iframe) has the real fields - these
    must now be found and filled, not silently missed."""
    main_frame = FakeFrame(inputs=[])  # nothing in the top-level document
    email_input = FakeInputElement(aria_label="Email")
    name_input = FakeInputElement(aria_label="Full Name")
    iframe = FakeFrame(inputs=[email_input, name_input])

    page = FakePageWithFrames(frames=[main_frame, iframe])

    filled, filled_elements, target_frame = _fill_form(page, CANDIDATE)

    assert len(filled) == 2
    assert email_input.filled_value == "nitish@example.com"
    assert name_input.filled_value == "Nitish"
    assert target_frame is iframe  # NOT main_frame - this is the key assertion


def test_fill_form_returns_empty_when_no_frame_has_matching_fields():
    """No fields found anywhere -> empty result, main_frame as a safe
    fallback for the caller to search for a submit button in (rather
    than raising or returning None)."""
    main_frame = FakeFrame(inputs=[])
    other_frame = FakeFrame(inputs=[FakeInputElement(aria_label="Unrelated field, e.g. LinkedIn URL")])
    page = FakePageWithFrames(frames=[main_frame, other_frame])

    filled, filled_elements, target_frame = _fill_form(page, CANDIDATE)

    assert filled == []
    assert filled_elements == []
    assert target_frame is main_frame


def test_fill_form_skips_a_frame_that_raises_and_keeps_checking_others():
    """A frame that's cross-origin-restricted or mid-navigation can
    raise when queried - one bad frame must not abort the whole search,
    since the real form might be in a different frame entirely."""
    class ExplodingFrame(FakeFrame):
        def locator(self, selector):
            raise RuntimeError("frame was detached")

    broken_frame = ExplodingFrame()
    email_input = FakeInputElement(aria_label="Email")
    good_frame = FakeFrame(inputs=[email_input])
    page = FakePageWithFrames(frames=[broken_frame, good_frame])

    filled, filled_elements, target_frame = _fill_form(page, CANDIDATE)

    assert len(filled) == 1
    assert target_frame is good_frame
