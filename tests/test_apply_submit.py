"""Phase 6, Slice 6: submit.py against a fake ATS: confirm, error, challenge,
timeout - and the one thing every case proves: exactly one click, never a
retry, and no click at all when a challenge is already on the page."""
import pytest

from apply_helpers import needs_chromium, open_browser, serve
from jobpipe.apply import signals, submit

pytestmark = needs_chromium


@pytest.fixture(scope="module")
def site():
    with serve() as base:
        yield base


def run(site, tmp_path, ats, outcome, timeout=3.0, **kw):
    with open_browser(tmp_path) as b:
        page = b.new_page()
        page.goto(f"{site}/fake_ats.html?ats={ats}&outcome={outcome}")
        result = submit.submit_once(page, ats, confirmation_timeout=timeout, **kw)
        clicks = page.evaluate("window.__submits")
        return result, clicks


class TestOneClick:
    @pytest.mark.parametrize("ats", ["greenhouse", "ashby"])
    def test_a_confirmation_is_confirmed_after_one_click(self, site, tmp_path, ats):
        result, clicks = run(site, tmp_path, ats, "confirm")
        assert result.outcome == "confirmed" and result.clicked
        assert clicks == 1

    @pytest.mark.parametrize("ats", ["greenhouse", "ashby"])
    def test_an_error_is_recorded_and_not_retried(self, site, tmp_path, ats):
        result, clicks = run(site, tmp_path, ats, "error")
        assert result.outcome == "error" and clicks == 1
        assert "not clicking again" in result.detail

    @pytest.mark.parametrize("ats", ["greenhouse", "ashby"])
    def test_a_challenge_after_the_click_stops_everything(self, site, tmp_path, ats):
        result, clicks = run(site, tmp_path, ats, "challenge")
        assert result.outcome == "challenge" and clicks == 1
        assert "verify_human_text" in result.detail or "challenge_iframe" in result.detail

    @pytest.mark.parametrize("ats", ["greenhouse", "ashby"])
    def test_a_timeout_is_unconfirmed_and_not_retried(self, site, tmp_path, ats):
        result, clicks = run(site, tmp_path, ats, "timeout", timeout=1.5)
        assert result.outcome == "unconfirmed" and clicks == 1
        assert "no confirmation within" in result.detail
        assert result.elapsed >= 1.4

    def test_a_challenge_already_on_the_page_means_no_click(self, site, tmp_path):
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/fake_ats_precaptcha.html")
            result = submit.submit_once(page, "greenhouse", confirmation_timeout=1)
            assert result.outcome == "not_clicked" and not result.clicked
            assert "recaptcha" in result.signals_at_submit
            assert page.evaluate("window.__submits") == 0

    def test_a_signal_seen_at_load_means_no_click_even_if_gone_now(self, site, tmp_path):
        result, clicks = run(site, tmp_path, "greenhouse", "confirm",
                             signals_before=("recaptcha",))
        assert result.outcome == "not_clicked" and clicks == 0

    def test_screenshots_are_taken_after_the_click(self, site, tmp_path):
        result, _ = run(site, tmp_path, "ashby", "confirm", screenshot_dir=tmp_path / "shots")
        assert result.screenshots and result.screenshots[0].endswith("after-submit-confirmed.png")

    def test_no_submit_button_means_no_click(self, site, tmp_path):
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/greenhouse_form.html")
            page.evaluate("document.querySelector('button[type=submit]').remove()")
            result = submit.submit_once(page, "greenhouse", confirmation_timeout=1)
            assert result.outcome == "not_clicked" and "no submit button" in result.detail


class TestClassify:
    """The pure part, with the verified Ashby markup and the UNVERIFIED
    Greenhouse wording."""
    clean = signals.Verdict()

    def test_ashby_success_container_is_the_primary_signal(self):
        html = '<div class="ashby-application-form-success-container"><p>Custom org text.</p></div>'
        assert submit.classify("ashby", html, "u", "Custom org text.", self.clean) == "confirmed"

    def test_ashby_default_text_counts_too(self):
        assert submit.classify("ashby", "", "u", "Your application was successfully submitted.", self.clean) == "confirmed"

    def test_ashby_error_text(self):
        assert submit.classify("ashby", "", "u", "We couldn't submit your form.", self.clean) == "error"

    def test_greenhouse_thank_you_or_confirmation_url(self):
        assert submit.classify("greenhouse", "", "u", "Thank you for applying", self.clean) == "confirmed"
        assert submit.classify("greenhouse", "", "https://x/alloy/jobs/1/confirmation", "", self.clean) == "confirmed"

    def test_a_challenge_outranks_nothing(self):
        v = signals.detect("<iframe src='https://x/recaptcha/api2/bframe'></iframe>")
        assert submit.classify("greenhouse", "", "u", "", v) == "challenge"

    def test_nothing_yet_is_none(self):
        assert submit.classify("greenhouse", "<form></form>", "u", "Submit application", self.clean) is None
