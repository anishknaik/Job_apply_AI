"""Phase 6, Slice 3: bot-check detection. Fixture pages with and without
each challenge type, plus the two real pages, which both carry reCAPTCHA
Enterprise - the measurement that makes review the main path."""
from pathlib import Path

import pytest

from jobpipe.apply import signals

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "apply"

CLEAN = """<!doctype html><html><head><title>Apply</title>
<script src="/static/app.js"></script></head>
<body><form id="application-form"><label for="first_name">First Name</label>
<input id="first_name"><button>Send application</button></form></body></html>"""


def page(extra_head="", extra_body=""):
    return CLEAN.replace("</head>", extra_head + "</head>").replace("</body>", extra_body + "</body>")


class TestEachChallengeType:
    def test_a_clean_page_is_clean(self):
        v = signals.detect(CLEAN)
        assert not v.bot_check and v.describe() == "no bot check seen"

    @pytest.mark.parametrize("kind,head", [
        ("recaptcha", '<script src="https://www.google.com/recaptcha/api.js" async></script>'),
        ("recaptcha", '<script src="https://www.recaptcha.net/recaptcha/enterprise.js?render=6Lxx"></script>'),
        ("hcaptcha", '<script src="https://js.hcaptcha.com/1/api.js"></script>'),
        ("turnstile", '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'),
        ("arkose", '<script src="https://client-api.arkoselabs.com/v2/api.js"></script>'),
    ])
    def test_a_script_tag_is_a_hit(self, kind, head):
        v = signals.detect(page(extra_head=head))
        assert kind in v.kinds

    @pytest.mark.parametrize("kind,body", [
        ("recaptcha", '<textarea id="g-recaptcha-response" name="g-recaptcha-response"></textarea>'),
        ("recaptcha", '<div class="g-recaptcha" data-sitekey="6Lxx"></div>'),
        ("hcaptcha", '<div class="h-captcha" data-sitekey="x"></div>'),
        ("turnstile", '<div class="cf-turnstile" data-sitekey="x"></div>'),
    ])
    def test_a_widget_element_is_a_hit(self, kind, body):
        assert kind in signals.detect(page(extra_body=body)).kinds

    def test_a_challenge_iframe_in_the_html_is_a_hit(self):
        body = '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=x"></iframe>'
        v = signals.detect(page(extra_body=body))
        assert "challenge_iframe" in v.kinds

    def test_a_challenge_frame_url_is_a_hit(self):
        v = signals.detect(CLEAN, frame_urls=["https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html"])
        assert v.kinds == ("challenge_iframe",)

    def test_a_defined_global_is_a_hit(self):
        v = signals.detect(CLEAN, window_globals=["grecaptcha"])
        assert v.kinds == ("recaptcha",) and v.signals[0].where == "global"

    def test_verify_you_are_human_text_is_a_hit(self):
        v = signals.detect(page(extra_body="<p>Please verify you are human to continue.</p>"))
        assert "verify_human_text" in v.kinds

    def test_the_text_check_reads_visible_text_not_scripts(self):
        """Script source mentioning robots is not a challenge on screen."""
        v = signals.detect(page(extra_head="<script>// are you a robot</script>"))
        assert not v.bot_check

    def test_visible_text_can_be_passed_from_the_browser(self):
        v = signals.detect(CLEAN, visible_text="Are you a robot? Complete the check")
        assert "verify_human_text" in v.kinds

    def test_an_unrelated_frame_is_not_a_hit(self):
        v = signals.detect(CLEAN, frame_urls=["https://www.youtube.com/embed/x"])
        assert not v.bot_check


class TestTheRealPages:
    def test_the_ashby_application_page_loads_recaptcha_enterprise(self):
        html = (FIXTURES / "ashby_langchain_74e5f9f4_application.html").read_text(encoding="utf-8")
        v = signals.detect(html)
        assert "recaptcha" in v.kinds
        assert any("enterprise.js" in s.detail or "g-recaptcha" in s.detail for s in v.signals)

    def test_the_greenhouse_alloy_page_loads_recaptcha_enterprise(self):
        """PHASE6_PLAN §1 sampled static HTML and found no reCAPTCHA on
        Alloy's board. The rendered page loads recaptcha/enterprise.js."""
        # The fixture is the form excerpt, which carries no script tag; the
        # page it came from loaded recaptcha/enterprise.js and defined
        # window.grecaptcha, which is what the runner probes for.
        html = (FIXTURES / "greenhouse_alloy_8495229002_form.html").read_text(encoding="utf-8")
        assert not signals.detect(html).bot_check
        v = signals.detect(html, window_globals=["grecaptcha"])
        assert v.kinds == ("recaptcha",)

    def test_describe_names_the_check(self):
        v = signals.detect(page(extra_head='<script src="https://www.google.com/recaptcha/api.js"></script>'))
        assert v.describe().startswith("recaptcha (html:")
