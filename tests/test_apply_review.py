"""The review wait: ends on a stdin line or a closed browser, never on EOF;
keeps the last snapshot of choices; and when the form disappears after
the (simulated) submit, saves the confirmation evidence and records what
Anish chose on the questions the runner left. Status is never touched."""
import io
import json
import threading
from pathlib import Path

import pytest

from apply_helpers import needs_chromium, open_browser, serve
from jobpipe.apply import learned, resolve, review, spec

pytestmark = needs_chromium


class Lines:
    """A stand-in for StdinLines: push a line from a timer, or never."""

    def __init__(self):
        self._lines = []
        self.eof = False

    def push(self, line="\n"):
        self._lines.append(line)

    def get(self):
        return self._lines.pop(0) if self._lines else None


@pytest.fixture(scope="module")
def site():
    with serve() as base:
        yield base


def context(page, tmp_path, ats="ashby", company="Fake", store=None):
    html = page.content()
    form = spec.parse_ashby_form(html) if ats == "ashby" else spec.FormSpec(
        "greenhouse", "1", company, "x", (spec.Field("first_name", "First Name", "text", True),))
    sources = resolve.Sources(answers={"identity": {"name": "Jane"}}, company=company, employers=())
    resolution = resolve.resolve(form, sources)
    store = store if store is not None else learned.Store()
    return review.ReviewContext(page=page, ats=ats, form=form, resolution=resolution,
                                company=company, posting_id=7, run_dir=tmp_path / "run",
                                learned_store=store, learned_path=tmp_path / "learned.yaml")


class TestEndings:
    def test_a_line_on_stdin_ends_it(self, site, tmp_path):
        lines = Lines()
        threading.Timer(0.8, lines.push).start()
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats.html?ats=ashby&outcome=timeout")
            result = review.wait(context(page, tmp_path), lines, poll=0.2, out=io.StringIO())
        assert result.ended_by == "stdin" and not result.form_gone
        assert result.evidence is None and result.learned_recorded == []

    def test_eof_on_stdin_does_not_end_it(self, site, tmp_path):
        """The non-interactive bug: input() saw EOF and closed the browser.
        A reader whose stream is already at EOF must leave the wait running."""
        lines = review.StdinLines(io.StringIO(""))
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats.html?ats=ashby&outcome=timeout")
            result = review.wait(context(page, tmp_path), lines, poll=0.2, max_wait=1.2,
                                 out=io.StringIO())
        assert lines.eof
        assert result.ended_by == "max_wait" and result.waited >= 1.1

    def test_a_closed_page_ends_it(self, site, tmp_path):
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats.html?ats=ashby&outcome=timeout")
            ctx = context(page, tmp_path)
            page.close()
            result = review.wait(ctx, Lines(), poll=0.2, out=io.StringIO())
        assert result.ended_by == "browser_closed"

    def test_stdin_lines_reads_a_real_stream(self):
        lines = review.StdinLines(io.StringIO("go\n"))
        import time
        for _ in range(50):
            got = lines.get()
            if got:
                break
            time.sleep(0.02)
        assert got == "go\n"


class TestWhenTheFormGoes:
    def test_ashby_confirmation_is_captured_and_choices_remembered(self, site, tmp_path):
        lines = Lines()
        threading.Timer(2.5, lines.push).start()
        out = io.StringIO()
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats.html?ats=ashby&outcome=confirm&auto_after=900")
            ctx = context(page, tmp_path)
            # Anish's own choice on the question the runner had no source for.
            page.click('label[for="r-heard-1"]')
            result = review.wait(ctx, lines, poll=0.2, out=out)
            assert page.evaluate("window.__submits") == 1, "the page submitted itself, not the runner"
        assert result.ended_by == "stdin" and result.form_gone
        ev = result.evidence
        assert ev["verdict"] == "confirmed"
        assert "successfully submitted" in ev["text_excerpt"]
        assert Path(ev["screenshot"]).exists() and Path(ev["html"]).exists()
        saved = json.loads((tmp_path / "run" / "confirmation.json").read_text(encoding="utf-8"))
        assert saved["verdict"] == "confirmed" and saved["url"].startswith(site)
        assert result.learned_recorded == ["r-heard"]
        store = learned.load(tmp_path / "learned.yaml")
        entry = store.get("How did you hear about Other Co?", "Other Co")
        assert entry.answer == "Company website" and entry.kind == "radio"
        assert entry.latest.company == "Fake" and entry.latest.posting_id == 7
        text = out.getvalue()
        assert "verdict: confirmed" in text and "remembered 1 answer(s)" in text
        assert "status not changed" in text

    def test_an_abandoned_run_teaches_nothing(self, site, tmp_path):
        lines = Lines()
        threading.Timer(0.8, lines.push).start()
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats.html?ats=ashby&outcome=timeout")
            page.click('label[for="r-heard-0"]')
            result = review.wait(context(page, tmp_path), lines, poll=0.2, out=io.StringIO())
        assert result.choices.get("r-heard") == "Blog or news article", "seen, but"
        assert not result.form_gone and result.learned_recorded == []
        assert not (tmp_path / "learned.yaml").exists()

    def test_greenhouse_thank_you_page_is_captured_with_its_unverified_verdict(self, site, tmp_path):
        lines = Lines()
        threading.Timer(2.2, lines.push).start()
        out = io.StringIO()
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats.html?ats=greenhouse&outcome=confirm&auto_after=700")
            result = review.wait(context(page, tmp_path, ats="greenhouse"), lines, poll=0.2, out=out)
        assert result.form_gone and result.evidence["verdict"] == "confirmed"
        assert "#confirmation" in result.evidence["url"]

    def test_a_vanished_form_with_nothing_recognised_says_so(self, site, tmp_path):
        lines = Lines()
        threading.Timer(2.2, lines.push).start()
        out = io.StringIO()
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats.html?ats=greenhouse&outcome=gone&auto_after=700")
            result = review.wait(context(page, tmp_path, ats="greenhouse"), lines, poll=0.2, out=out)
        assert result.form_gone and result.evidence["verdict"] is None
        assert "no confirmation recognised" in out.getvalue()
        assert "UNVERIFIED" in out.getvalue()
