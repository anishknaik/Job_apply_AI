"""Phase 6, Slice 4: fill.py against local fixture pages, offline.

The pages are test doubles of the two real forms (fixtures/apply/pages);
the assertions are about what the filler does and never does: it types and
reads back, it chooses options by exact text from the widget's own list, it
uploads through the field's own input, it leaves hidden fields and
unmatched options alone, and it never submits.
"""
from pathlib import Path

import pytest

from apply_helpers import needs_chromium, open_browser, serve
from jobpipe.apply import fill, resolve, spec
from jobpipe.apply.spec import Field, FormSpec, Option

pytestmark = needs_chromium

YES_NO = (Option("Yes", "1"), Option("No", "0"))


def gh_form():
    fields = (
        Field("first_name", "First Name", "text", True),
        Field("last_name", "Last Name", "text", False),
        Field("email", "Email", "text", True),
        Field("candidate-location", "Location (City)", "text", True),
        Field("resume", "Resume/CV", "file", True, text_key="resume_text"),
        Field("cover_letter", "Cover Letter", "file", False),
        Field("question_36029293002", "LinkedIn Profile", "text", True),
        Field("question_36029298002", "Are you authorized to work in the US?", "select", True, options=YES_NO),
        Field("question_why", "Why do you want to work here?", "textarea", True),
        Field("question_oneline", "In one line, your proudest project", "text", False),
        Field("longitude", "Longitude", "hidden", True, section="location"),
        Field("gender", "Gender", "select", False, section="eeoc",
              options=(Option("Decline To Self Identify", "3"), Option("Female", "2"), Option("Male", "1"))),
    )
    return FormSpec(ats="greenhouse", job_id="1", company="Alloy", title="x", fields=fields)


def items(**values):
    """Resolved items by key, with sensible actions for the test form."""
    form = gh_form()
    out = []
    for f in form.fields:
        if f.key not in values:
            out.append(resolve.Resolved(f, resolve.SKIP if f.kind == "hidden" else resolve.BLANK))
            continue
        action, value = values[f.key]
        out.append(resolve.Resolved(f, action, value, source="test"))
    return resolve.Resolution(tuple(out))


@pytest.fixture(scope="module")
def site():
    with serve() as base:
        yield base


@pytest.fixture
def page(site, tmp_path):
    with open_browser(tmp_path) as b:
        p = b.new_page()
        p.goto(f"{site}/greenhouse_form.html")
        yield p


class TestGreenhouseDouble:
    def test_text_is_typed_and_read_back_equal(self, page):
        res = items(first_name=("fill", "Jane"), email=("fill", "jane@example.com"),
                    question_why=("fill", "Because.\n\nTwo paragraphs."))
        report = fill.fill_form(page, gh_form(), res)
        by = {f.key: f for f in report.fields}
        assert by["first_name"].status == "ok" and by["first_name"].verified
        assert by["question_why"].verified
        assert page.input_value("#question_why") == "Because.\n\nTwo paragraphs."
        assert report.unverified == []

    def test_a_box_that_reshapes_the_value_is_a_mismatch(self, page):
        res = items(question_oneline=("fill", "One.\nTwo."))
        report = fill.fill_form(page, gh_form(), res)
        f = report.fields[[x.key for x in report.fields].index("question_oneline")]
        assert f.status == "mismatch" and not f.verified
        assert f.value_sha256 != f.readback_sha256
        assert report.unverified and report.unverified[0].key == "question_oneline"

    def test_an_option_is_chosen_by_exact_text_from_the_listbox(self, page):
        res = items(question_36029298002=("select", "Yes"), gender=("select", "Male"))
        report = fill.fill_form(page, gh_form(), res)
        by = {f.key: f for f in report.fields}
        assert by["question_36029298002"].verified
        assert by["gender"].verified
        assert page.get_attribute("[data-combobox]:has(#gender)", "data-selected") == "Male"
        assert page.get_attribute("[data-combobox]:has(#question_36029298002)", "data-selected") == "Yes"

    def test_no_exact_option_leaves_the_widget_unset(self, page):
        res = items(gender=("select", "Man"))
        report = fill.fill_form(page, gh_form(), res)
        f = {x.key: x for x in report.fields}["gender"]
        assert f.status == "unmatched_option"
        assert page.get_attribute("[data-combobox]:has(#gender)", "data-selected") is None

    def test_a_partial_match_is_not_a_match(self, page):
        """'Female' contains 'male'; typing 'Male' must choose Male only."""
        res = items(gender=("select", "Male"))
        fill.fill_form(page, gh_form(), res)
        assert page.get_attribute("[data-combobox]:has(#gender)", "data-selected") == "Male"

    def test_location_autocomplete_picks_the_exact_suggestion(self, page):
        res = items(**{"candidate-location": ("autocomplete", "Springfield, IL")})
        report = fill.fill_form(page, gh_form(), res)
        f = {x.key: x for x in report.fields}["candidate-location"]
        assert f.verified
        assert page.get_attribute("[data-combobox]:has(#candidate-location)", "data-selected") == "Springfield, IL"

    def test_the_resume_goes_through_its_own_file_input(self, page, tmp_path):
        pdf = tmp_path / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        res = items(resume=("upload", str(pdf)))
        report = fill.fill_form(page, gh_form(), res)
        f = {x.key: x for x in report.fields}["resume"]
        assert f.verified and f.status == "ok"
        assert page.evaluate("document.querySelector('#resume').files[0].name") == "resume.pdf"
        assert page.evaluate("document.querySelector('#autofill-upload').files.length") == 0

    def test_a_missing_file_is_an_error_not_an_upload(self, page, tmp_path):
        res = items(resume=("upload", str(tmp_path / "nope.pdf")))
        report = fill.fill_form(page, gh_form(), res)
        f = {x.key: x for x in report.fields}["resume"]
        assert f.status == "error"

    def test_hidden_fields_are_never_touched(self, page):
        report = fill.fill_form(page, gh_form(), items(first_name=("fill", "Jane")))
        assert {x.key: x for x in report.fields}["longitude"].status == "skipped"
        assert page.input_value("#longitude") == "-72.5"

    def test_blanks_and_blockers_touch_nothing(self, page):
        form = gh_form()
        res = resolve.Resolution((resolve.Resolved(form.fields[0], resolve.BLOCKER, note="x"),
                                  resolve.Resolved(form.fields[1], resolve.BLANK)))
        report = fill.fill_form(page, form, res)
        assert [f.status for f in report.fields] == ["blocked", "blank"]
        assert page.input_value("#first_name") == "" and page.input_value("#last_name") == ""

    def test_a_field_missing_from_the_page_is_reported(self, page):
        f = Field("question_ghost", "Ghost", "text", True)
        res = resolve.Resolution((resolve.Resolved(f, resolve.FILL, "x", "test"),))
        report = fill.fill_form(page, gh_form(), res)
        assert report.fields[0].status == "not_found"

    def test_the_form_is_never_submitted(self, page):
        res = items(first_name=("fill", "Jane"), question_36029298002=("select", "Yes"))
        fill.fill_form(page, gh_form(), res)
        assert page.evaluate("window.__submits") == 0

    def test_a_screenshot_is_written_when_asked(self, page, tmp_path):
        report = fill.fill_form(page, gh_form(), items(first_name=("fill", "Jane")),
                                screenshot_dir=tmp_path / "shots", tag="t")
        assert report.screenshots == [str(tmp_path / "shots" / "t.png")]
        assert (tmp_path / "shots" / "t.png").stat().st_size > 0

    def test_a_clean_page_has_no_signals(self, page):
        report = fill.fill_form(page, gh_form(), items())
        assert report.signals_before == []

    def test_the_report_serialises(self, page):
        report = fill.fill_form(page, gh_form(), items(first_name=("fill", "Jane")))
        data = report.to_dict()
        assert data["ats"] == "greenhouse" and data["fields"][0]["key"] == "first_name"
        assert "Jane" not in report.to_json(), "the report carries hashes, not values"


class TestAshbyDouble:
    def test_the_real_langchain_spec_fills_its_double(self, site, tmp_path):
        html = (Path(__file__).parent / "fixtures" / "apply" /
                "ashby_langchain_74e5f9f4_application.html").read_text(encoding="utf-8")
        form = spec.parse_ashby_form(html, spec.extract_app_data(html))
        pdf = tmp_path / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        answers = {"identity": {"name": "Jane Example", "email": "jane@example.com",
                                "github": "github.com/jane", "location": "Springfield, IL",
                                "linkedin": "linkedin.com/in/jane"},
                   "work_authorization": {"authorized_to_work_in_us": True, "requires_sponsorship": False}}
        res = resolve.resolve(form, resolve.Sources(answers=answers, resume_path=pdf))
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/ashby_form.html")
            report = fill.fill_form(page, form, res)
            by = {f.key: f for f in report.fields}
            assert by["_systemfield_name"].verified
            assert by["_systemfield_email"].verified
            assert by["_systemfield_resume"].verified
            assert by["_systemfield_location"].verified
            assert by["5137f84a-82fa-442d-9fff-8503a0818baf"].verified
            assert page.get_attribute('button[data-option="no"]', "aria-pressed") == "true"
            assert page.get_attribute('button[data-option="yes"]', "aria-pressed") == "false"
            assert page.evaluate("document.querySelector('#autofill').files.length") == 0
            assert page.evaluate("window.__submits") == 0
            assert report.unverified == []


    RADIO_KEY = "de771a19-ae1f-42ca-885d-dfc8861fa2ad"

    def radio_item(self, value):
        f = Field(self.RADIO_KEY, "Are you legally authorized to work in the United States?",
                  spec.RADIO, True, options=YES_NO)
        form = FormSpec(ats="ashby", job_id="1", company="x", title="x", fields=(f,))
        return form, resolve.Resolution((resolve.Resolved(f, resolve.SELECT, value, source="test"),))

    def test_a_radio_option_is_clicked_by_exact_text_and_read_back(self, site, tmp_path):
        form, res = self.radio_item("Yes")
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/ashby_form.html")
            report = fill.fill_form(page, form, res)
            assert report.fields[0].verified
            assert page.is_checked("#r-auth-0") and not page.is_checked("#r-auth-1")
            assert page.evaluate("window.__submits") == 0

    def test_no_exact_radio_option_leaves_the_group_unset(self, site, tmp_path):
        form, res = self.radio_item("yes")
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/ashby_form.html")
            report = fill.fill_form(page, form, res)
            assert report.fields[0].status == "unmatched_option"
            assert report.unverified
            assert not page.is_checked("#r-auth-0") and not page.is_checked("#r-auth-1")


class TestSignalsOnThePage:
    def test_recaptcha_is_seen_before_anything_is_typed(self, site, tmp_path):
        form = FormSpec(ats="greenhouse", job_id="1", company="x", title="x",
                        fields=(Field("first_name", "First Name", "text", True),))
        res = resolve.Resolution((resolve.Resolved(form.fields[0], resolve.FILL, "Jane", "t"),))
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/recaptcha_form.html")
            report = fill.fill_form(page, form, res)
            assert "recaptcha" in report.signals_before
            verdict = fill.collect_signals(page)
            assert {s.where for s in verdict.signals} >= {"html", "global"}

    def test_no_form_on_the_page_is_reported_not_guessed(self, site, tmp_path):
        form = FormSpec(ats="ashby", job_id="1", company="x", title="x", fields=())
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/greenhouse_form.html")
            report = fill.fill_form(page, form, resolve.Resolution(()))
            assert not report.form_found and "ashby-application-form-container" in report.error
