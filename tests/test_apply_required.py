"""The ninth condition's eyes: required markers read from the rendered page,
compared with the spec. One test per outcome, the Sierra regression (the
old parser's blind spot must fail the check), and the allowlist."""
from pathlib import Path

import pytest

from apply_helpers import needs_chromium, open_browser, serve
from jobpipe.apply import required, spec
from jobpipe.apply.spec import Field, FormSpec

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "apply"


class TestMissingFromSpec:
    def form(self, *keys, ats="ashby"):
        return FormSpec(ats, "1", "x", "x", tuple(Field(k, k, "text", True) for k in keys))

    def test_not_read_is_none(self):
        assert required.missing_from_spec(None, self.form("a")) is None

    def test_every_marker_known_is_empty(self):
        assert required.missing_from_spec(["a", "b"], self.form("a", "b")) == []

    def test_an_unknown_marker_is_listed(self):
        assert required.missing_from_spec(["a", "zzz"], self.form("a")) == ["zzz"]

    def test_the_greenhouse_country_field_is_allowed_by_name(self):
        assert required.missing_from_spec(["country", "phone"], self.form("phone", ats="greenhouse")) == []
        assert required.missing_from_spec(["country"], self.form("phone", ats="ashby")) == ["country"]
        assert "phone" in required.ALLOWED["greenhouse"]["country"]

    def test_a_multiselect_key_matches_with_or_without_brackets(self):
        assert required.missing_from_spec(["question_1"], self.form("question_1[]", ats="greenhouse")) == []


@pytest.fixture(scope="module")
def site():
    with serve() as base:
        yield base


@pytest.fixture(scope="module")
def fixtures_site():
    with serve(FIXTURES) as base:
        yield base


@needs_chromium
class TestOnThePage:
    def test_ashby_double(self, site, tmp_path):
        html = (FIXTURES / "pages" / "ashby_form.html").read_text(encoding="utf-8")
        form = spec.parse_ashby_form(html)
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/ashby_form.html")
            markers, missing = required.check(page, form)
        assert set(markers) == {"_systemfield_name", "_systemfield_email", "_systemfield_resume",
                                "5137f84a-82fa-442d-9fff-8503a0818baf",
                                "de771a19-ae1f-42ca-885d-dfc8861fa2ad"}
        assert missing == []

    def test_greenhouse_double_with_the_country_sub_field(self, site, tmp_path):
        form = FormSpec("greenhouse", "1", "Alloy", "x", (
            Field("first_name", "First Name", "text", True), Field("email", "Email", "text", True),
            Field("phone", "Phone", "text", True), Field("resume", "Resume/CV", "file", True)))
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/greenhouse_form.html")
            markers, missing = required.check(page, form)
        assert set(markers) == {"first_name", "email", "country", "phone", "resume"}
        assert missing == []

    def test_no_form_means_not_read(self, site, tmp_path):
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/widgets.js")
            assert required.check(page, FormSpec("ashby", "1", "x", "x", ())) == (None, None)

    def test_sierras_real_page_with_the_old_parsers_blind_spot_fails(self, fixtures_site, tmp_path):
        """The regression that motivated the check: drop the radio groups
        from the spec, as the old parser did, and the page disagrees."""
        html = (FIXTURES / "ashby_sierra_0dfe7805_application.html").read_text(encoding="utf-8")
        form = spec.parse_ashby_form(html, spec.extract_app_data(html))
        with open_browser(tmp_path) as b:
            page = b.new_page()
            # The captured page still carries Ashby's script tags. With a
            # network, they load the live app, which re-renders the page away
            # from the captured form; tests stay offline, so block them.
            page.route("**/*", lambda route: route.continue_()
                       if route.request.url.startswith(fixtures_site) else route.abort())
            page.goto(f"{fixtures_site}/ashby_sierra_0dfe7805_application.html")
            markers, missing = required.check(page, form)
            assert len(markers) == 13 == len(form.required) and missing == []
            old_parser = FormSpec(form.ats, form.job_id, form.company, form.title,
                                  tuple(f for f in form.fields if f.kind != "radio"))
            _, missing_old = required.check(page, old_parser)
        assert len(missing_old) == 5
        assert "3d33a44a-7eeb-465b-8299-84788e69a3bc" in missing_old
        assert "afdc2ef9-5c9a-44c1-aca1-864c5402e5a2" in missing_old

    def test_alloys_real_form_excerpt(self, fixtures_site, tmp_path):
        """The captured Alloy form: every aria-required control is a spec key
        or the allowed country sub-field."""
        import json
        form = spec.parse_greenhouse(json.loads(
            (FIXTURES / "greenhouse_alloy_8495229002_questions.json").read_text(encoding="utf-8")))
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{fixtures_site}/greenhouse_alloy_8495229002_form.html")
            markers, missing = required.check(page, form)
        assert "resume" in markers and "country" in markers and "candidate-location" in markers
        assert missing == []


class TestGreenhouseDemographicIds:
    """Chime, 2026-11-05: the page marks a required demographic question
    by its bare id; the spec keys it demographic_question_<id>."""

    def test_a_bare_demographic_id_is_the_same_question(self):
        form = FormSpec("greenhouse", "1", "x", "x", (
            spec.Field("demographic_question_4024620002", "Gender", "select", True,
                       section="demographic"),))
        assert required.missing_from_spec(["4024620002"], form) == []

    def test_a_marker_with_no_field_is_still_missing(self):
        form = FormSpec("greenhouse", "1", "x", "x", (
            spec.Field("demographic_question_4024620002", "Gender", "select", True,
                       section="demographic"),))
        assert required.missing_from_spec(
            ["4024620002", "gdpr_demographic_data_consent_given_1"], form) == [
            "gdpr_demographic_data_consent_given_1"]

    def test_page_id(self):
        assert spec.page_id("demographic_question_42") == "42"
        assert spec.page_id("question_42") == "question_42"
