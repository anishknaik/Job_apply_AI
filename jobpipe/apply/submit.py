"""The one click, then confirmation detection. No retries, ever.

`submit_once` checks for a bot challenge immediately before clicking - a
hit means no click at all - then clicks the form's own submit button once
and watches the page until the ATS confirms, errors, shows a challenge, or
the clock runs out. Every outcome but `confirmed` is `submit_unconfirmed`
territory: the runner records it, screenshots it, and never clicks again,
because a duplicate application is worse than a missed one. Anish checks
by hand.

What counts as confirmation:

* Ashby - VERIFIED from its frontend bundle (2026-11-04): a success box with
  class `ashby-application-form-success-container` and role="status",
  default text "Your application was successfully submitted. We'll contact
  you if there are next steps." (an organisation may replace the text, so
  the class is the primary signal); the error box reads "We couldn't
  submit your form.".
* Greenhouse - UNVERIFIED. The job-boards app's bundle was not reachable
  from the environment this was built in, so the patterns below are the
  thank-you wording Greenhouse boards have used and a `confirmation` URL
  segment. A miss on a real confirmation is `unconfirmed`, which is the
  safe outcome; the first live run (HANDOFF.md, Phase 6) should capture the
  real confirmation page and replace these.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import fill as _fill, signals as _signals, spec as _spec

CONFIRMED, UNCONFIRMED, ERROR, CHALLENGE, NOT_CLICKED = (
    "confirmed", "unconfirmed", "error", "challenge", "not_clicked")
# Written to apply_runs, committed, *before* the click. If anything after
# the click raises or the process dies, the row still says a click happened
# and decide.py's first_attempt condition refuses a second one.
CLICKED = "clicked"

SUBMIT_BUTTONS = {
    _spec.GREENHOUSE: '#application-form button[type="submit"]',
    _spec.ASHBY: "button.ashby-application-form-submit-button",
}

_ASHBY_SUCCESS_CLASS = "ashby-application-form-success-container"
_ASHBY_SUCCESS_TEXT = re.compile(r"successfully submitted", re.I)
_ASHBY_ERROR_TEXT = re.compile(r"we couldn'?t submit your form", re.I)

# Greenhouse: UNVERIFIED, see the module docstring.
_GH_SUCCESS_TEXT = re.compile(
    r"thank you for applying|application (has been|was) (submitted|received)|"
    r"we(?:'ve| have) received your application|your application has been received", re.I)
_GH_SUCCESS_URL = re.compile(r"/confirmation\b|confirmation=", re.I)
_GH_ERROR_TEXT = re.compile(
    r"there was an error|could not be submitted|unable to submit|"
    r"please (correct|fix) the (errors|highlighted)|something went wrong", re.I)


@dataclass
class SubmitResult:
    outcome: str
    clicked: bool
    detail: str = ""
    signals_at_submit: tuple[str, ...] = ()
    screenshots: list[str] = field(default_factory=list)
    elapsed: float = 0.0


_SCRIPT = re.compile(r"<script\b.*?</script>", re.I | re.S)


def classify(ats: str, html: str, url: str, text: str,
             verdict: _signals.Verdict) -> str | None:
    """What the page says after the click: confirmed, error, challenge, or
    None for "nothing yet". Pure, so the fixtures can exercise it. Markup is
    judged with scripts removed: a bundle that names the success class is
    not a success box on screen."""
    html = _SCRIPT.sub(" ", html or "")
    if ats == _spec.ASHBY:
        if _ASHBY_SUCCESS_CLASS in html or _ASHBY_SUCCESS_TEXT.search(text):
            return CONFIRMED
        if _ASHBY_ERROR_TEXT.search(text):
            return ERROR
    elif ats == _spec.GREENHOUSE:
        if _GH_SUCCESS_TEXT.search(text) or _GH_SUCCESS_URL.search(url):
            return CONFIRMED
        if _GH_ERROR_TEXT.search(text):
            return ERROR
    if verdict.bot_check:
        return CHALLENGE
    return None


def _read(page) -> tuple[str, str, str, _signals.Verdict]:
    html = page.content()
    try:
        text = page.inner_text("body")
    except Exception:                                        # noqa: BLE001
        text = ""
    return html, page.url, text, _fill.collect_signals(page)


def submit_once(page, ats: str, confirmation_timeout: float = 45.0,
                screenshot_dir: Path | None = None,
                signals_before: tuple[str, ...] = (),
                before_click=None) -> SubmitResult:
    """Click submit once and wait for the ATS to say what happened.

    `signals_before` is what fill saw at load; a challenge seen now, or
    then, means no click. There is deliberately no loop around the click.
    `before_click` runs immediately before it - the runner records the
    attempt there, durably, so a crash after the click cannot hide it. If
    it raises, there is no click.
    """
    html, url, text, verdict = _read(page)
    at_submit = tuple(verdict.kinds)
    if verdict.bot_check or signals_before:
        result = SubmitResult(NOT_CLICKED, False,
                              "bot check present; not clicking: " + verdict.describe()
                              if verdict.bot_check else
                              "bot check seen at load; not clicking: " + ", ".join(signals_before),
                              at_submit)
        _shoot(page, screenshot_dir, "not-clicked", result)
        return result

    frame, _root = _fill.form_root(page, ats)
    if frame is None:
        return SubmitResult(NOT_CLICKED, False, "no form on the page", at_submit)
    button = frame.locator(SUBMIT_BUTTONS[ats])
    if not button.count():
        return SubmitResult(NOT_CLICKED, False, "no submit button on the form", at_submit)

    if before_click is not None:
        before_click()
    started = time.monotonic()
    button.first.click()                       # the one click
    outcome, detail = UNCONFIRMED, f"no confirmation within {confirmation_timeout:.0f}s"
    while time.monotonic() - started < confirmation_timeout:
        try:
            html, url, text, verdict = _read(page)
        except Exception as exc:                             # noqa: BLE001
            # The page may be navigating; that is not an answer yet.
            page.wait_for_timeout(250)
            detail = f"page unreadable while waiting: {exc}"[:200]
            continue
        seen = classify(ats, html, url, text, verdict)
        if seen == CONFIRMED:
            outcome, detail = CONFIRMED, "confirmation shown"
            break
        if seen == ERROR:
            outcome, detail = ERROR, "the ATS showed an error after the click; not clicking again"
            break
        if seen == CHALLENGE:
            outcome, detail = CHALLENGE, "a challenge appeared after the click: " + verdict.describe()
            break
        page.wait_for_timeout(300)
    result = SubmitResult(outcome, True, detail, at_submit,
                          elapsed=time.monotonic() - started)
    _shoot(page, screenshot_dir, f"after-submit-{outcome}", result)
    return result


def _shoot(page, directory: Path | None, name: str, result: SubmitResult) -> None:
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        result.screenshots.append(str(path))
    except Exception:                                        # noqa: BLE001
        pass
