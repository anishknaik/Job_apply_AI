"""Phase 6, Slice 1: the parsers, against real captured forms.

Every fixture under tests/fixtures/apply/ is a real public posting captured
2026-11-04 (README there). The assertions are about things that matter
downstream: which fields are required, which are files, which are selects
with exact option text, which are hidden, and - for Ashby - which keys the
filler will locate by.
"""
import json
from pathlib import Path

import pytest

from jobpipe.apply import spec

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "apply"


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def alloy():
    return spec.parse_greenhouse(load_json("greenhouse_alloy_8495229002_questions.json"))


@pytest.fixture(scope="module")
def datadog():
    return spec.parse_greenhouse(load_json("greenhouse_datadog_8053714_questions.json"))


@pytest.fixture(scope="module")
def langchain():
    html = (FIXTURES / "ashby_langchain_74e5f9f4_application.html").read_text(encoding="utf-8")
    return spec.parse_ashby_form(html, spec.extract_app_data(html),
                                 apply_url="https://jobs.ashbyhq.com/langchain/74e5f9f4/application")


class TestGreenhouse:
    def test_identity(self, alloy):
        assert alloy.ats == "greenhouse"
        assert alloy.job_id == "8495229002"
        assert alloy.company == "Alloy"
        assert alloy.title == "Senior Machine Learning Engineer"
        assert alloy.apply_url.startswith("https://www.alloy.com/")

    def test_every_question_block_is_read(self, alloy):
        # 17 questions + 3 location + 3 EEOC (veteran, race, gender)
        assert len(alloy.fields) == 23
        assert [f.section for f in alloy.fields].count("location") == 3
        assert [f.section for f in alloy.fields].count("eeoc") == 3

    def test_identity_fields_keep_greenhouse_names(self, alloy):
        first = alloy.field_by_key("first_name")
        assert first.kind == "text" and first.required and first.label == "First Name"
        assert alloy.field_by_key("email").required
        assert alloy.field_by_key("phone").required

    def test_a_file_field_carries_its_enter_manually_twin(self, alloy):
        resume = alloy.field_by_key("resume")
        assert resume.kind == "file" and resume.required
        assert resume.text_key == "resume_text"
        letter = alloy.field_by_key("cover_letter")
        assert letter.kind == "file" and not letter.required
        assert letter.text_key == "cover_letter_text"

    def test_the_salary_question_is_a_required_text_field(self, alloy):
        """The real reason this fixture is here: resolve.py must make this a
        blocker, so the posting goes to review."""
        salary = alloy.field_by_key("question_36029295002")
        assert salary.label.startswith("What are your salary requirements")
        assert salary.required and salary.kind == "text"

    def test_a_select_carries_exact_option_text(self, alloy):
        sponsorship = alloy.field_by_key("question_36029299002")
        assert sponsorship.kind == "select" and sponsorship.required
        assert [o.label for o in sponsorship.options] == ["Yes", "No"]
        assert [o.value for o in sponsorship.options] == ["1", "0"]

    def test_hidden_location_fields_are_marked_hidden(self, alloy):
        for key in ("longitude", "latitude"):
            f = alloy.field_by_key(key)
            assert f.kind == "hidden" and f.section == "location"

    def test_location_maps_to_the_rendered_id(self, alloy):
        """The JSON calls it `location`; the page renders
        `candidate-location`. The key is what the filler locates by."""
        loc = alloy.field_by_key("candidate-location")
        assert loc is not None and loc.kind == "text" and loc.required

    def test_eeoc_options_are_verbatim(self, alloy):
        gender = alloy.field_by_key("gender")
        assert gender.section == "eeoc" and not gender.required
        assert [o.label for o in gender.options] == \
            ["Decline To Self Identify", "Female", "Male"]
        race = alloy.field_by_key("race")
        assert "Asian" in [o.label for o in race.options]

    def test_datadog_attestations_and_multiselects(self, datadog):
        certify = datadog.field_by_key("question_68014209")
        assert certify.label.startswith("I certify") and certify.required
        assert certify.kind == "select"
        cities = datadog.field_by_key("question_68014212")
        assert cities.kind == "multiselect" and cities.required
        assert len(cities.options) == 45
        assert datadog.field_by_key("preferred_name").required
        assert datadog.field_by_key("question_68014214").label.startswith("How did you hear")

    def test_an_unknown_type_is_unknown_not_text(self):
        payload = {"id": 1, "questions": [{"label": "Video", "required": True,
                   "fields": [{"name": "question_1", "type": "input_video", "values": []}]}]}
        out = spec.parse_greenhouse(payload)
        assert out.fields[0].kind == "unknown"

    def test_demographic_questions_shape(self):
        """UNVERIFIED against a live board - documented shape only."""
        payload = {"id": 1, "questions": [], "demographic_questions": {
            "header": "x", "description": "y", "questions": [
                {"id": 5, "label": "Gender identity", "required": False,
                 "type": "multi_value_single_select",
                 "answer_options": [{"id": 9, "label": "Non-binary"}]}]}}
        out = spec.parse_greenhouse(payload)
        f = out.fields[0]
        assert f.section == "demographic" and f.key == "demographic_question_5"
        assert f.options == (spec.Option("Non-binary", "9"),)

    def test_not_a_questions_payload(self):
        with pytest.raises(spec.SpecError):
            spec.parse_greenhouse({"id": 1, "title": "x"})

    def test_urls(self):
        assert spec.greenhouse_questions_url("alloy", 8495229002) == \
            "https://boards-api.greenhouse.io/v1/boards/alloy/jobs/8495229002?questions=true"
        assert spec.greenhouse_form_url("alloy", 8495229002) == \
            "https://job-boards.greenhouse.io/alloy/jobs/8495229002"


class TestAshby:
    def test_identity_from_app_data(self, langchain):
        assert langchain.ats == "ashby"
        assert langchain.company == "LangChain"
        assert langchain.title == "Python OSS Engineer"
        assert langchain.job_id == "74e5f9f4-e44a-4594-ba26-abdf71bf287d"
        assert langchain.bot_check_site_key == "6LezdY0tAAAAAEnollNLCAI0z1VDGwM7AzGrU4XZ"

    def test_every_field_entry_is_read_by_its_path(self, langchain):
        assert [f.key for f in langchain.fields] == [
            "_systemfield_name", "_systemfield_email", "_systemfield_resume",
            "_systemfield_location",
            "5137f84a-82fa-442d-9fff-8503a0818baf",
            "82baf130-729c-47f7-a795-be9cdee14150",
            "29edfacc-23e6-403a-acbc-ad7e31c875c8"]

    def test_kinds_and_required(self, langchain):
        by = {f.key: f for f in langchain.fields}
        assert (by["_systemfield_name"].kind, by["_systemfield_name"].required) == ("text", True)
        assert (by["_systemfield_email"].kind, by["_systemfield_email"].required) == ("email", True)
        assert (by["_systemfield_resume"].kind, by["_systemfield_resume"].required) == ("file", True)
        assert (by["_systemfield_location"].kind, by["_systemfield_location"].required) == ("location", False)
        assert by["82baf130-729c-47f7-a795-be9cdee14150"].label == "GitHub Link (if applicable)"
        assert not by["82baf130-729c-47f7-a795-be9cdee14150"].required

    def test_a_yes_no_question_has_two_options(self, langchain):
        q = langchain.field_by_key("5137f84a-82fa-442d-9fff-8503a0818baf")
        assert q.kind == "yesno" and q.required
        assert q.label.startswith("Will you now, or in the future, require sponsorship")
        assert [o.label for o in q.options] == ["Yes", "No"]

    def test_an_autocomplete_has_no_options_until_opened(self, langchain):
        assert langchain.field_by_key("_systemfield_location").options is None
        assert any("listbox" in n for n in langchain.notes)

    def test_an_unseen_widget_is_unknown(self):
        html = ('<div class="x ashby-application-form-field-entry" data-field-path="abc">'
                '<label class="_required_1 ashby-application-form-question-title" for="abc">Video</label>'
                '<div><video></video></div></div>'
                '<button class="ashby-application-form-submit-button">Go</button>')
        out = spec.parse_ashby_form(html)
        assert out.fields[0].kind == "unknown" and out.fields[0].required

    def test_a_page_without_the_form_is_refused(self):
        with pytest.raises(spec.SpecError):
            spec.parse_ashby_form("<html><body>Loading…</body></html>")

    def test_extract_app_data_stops_at_the_object(self):
        html = (FIXTURES / "ashby_langchain_74e5f9f4_application.html").read_text(encoding="utf-8")
        data = spec.extract_app_data(html)
        assert data["organization"]["name"] == "LangChain"
        assert spec.extract_app_data("<html></html>") is None

    def test_application_url(self):
        assert spec.ashby_application_url("https://jobs.ashbyhq.com/langchain/abc") == \
            "https://jobs.ashbyhq.com/langchain/abc/application"
        assert spec.ashby_application_url("https://jobs.ashbyhq.com/langchain/abc/application") == \
            "https://jobs.ashbyhq.com/langchain/abc/application"
