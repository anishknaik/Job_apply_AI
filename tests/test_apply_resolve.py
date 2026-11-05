"""Phase 6, Slice 2: resolve.py, a row at a time.

One test per row of PHASE6_PLAN §3's resolver table, each failing on its
own, plus every blocker. The sources are built by hand in the shape
form_answers() returns; the two real forms close the file.
"""
import json
from pathlib import Path

import pytest

from jobpipe.apply import resolve, spec
from jobpipe.apply.spec import Field, FormSpec, Option

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "apply"

YES_NO = (Option("Yes", "1"), Option("No", "0"))


def answers(**over):
    base = {
        "identity": {"name": "Jane Example", "email": "jane@example.com",
                     "phone": "(555) 010-0000", "linkedin": "linkedin.com/in/jane",
                     "github": "github.com/jane", "location": "Springfield, IL"},
        "education": {"school": "Example State University",
                      "degree": "B.S. Computer Science", "date": "May 2025"},
        "work_authorization": {"authorized_to_work_in_us": True,
                               "requires_sponsorship": False},
        "self_identification": {"gender": ["Male"], "race_ethnicity": ["Asian"],
                                "veteran_status": ["I am not a protected veteran"],
                                "disability_status": [], "hispanic_or_latino": ["No"],
                                "pronouns": []},
        "logistics": {"preferred_name": "", "earliest_start_date": "Immediately",
                      "how_did_you_hear": ""},
        "reusable_answers": {},
    }
    for key, value in over.items():
        base[key] = value
    return base


def sources(**kw):
    kw.setdefault("answers", answers())
    return resolve.Sources(**kw)


def fld(key, label, kind="text", required=True, options=None, section="application", text_key=None):
    return Field(key=key, label=label, kind=kind, required=required,
                 section=section, options=options, text_key=text_key)


def one(field, src=None):
    return resolve.resolve_field(field, src or sources())


# -- row 1: identity from the profile ----------------------------------------

class TestIdentityFromTheProfile:
    @pytest.mark.parametrize("key,label,expected,source", [
        ("email", "Email", "jane@example.com", "profile:identity.email"),
        ("phone", "Phone", "(555) 010-0000", "profile:identity.phone"),
        ("question_1", "LinkedIn Profile", "linkedin.com/in/jane", "profile:identity.linkedin"),
        ("question_2", "Github", "github.com/jane", "profile:identity.github"),
        ("question_3", "School", "Example State University", "profile:education.school"),
        ("question_4", "Degree", "B.S. Computer Science", "profile:education.degree"),
        ("question_5", "Graduation date", "May 2025", "profile:education.date"),
        ("_systemfield_name", "Name", "Jane Example", "profile:identity.name"),
    ])
    def test_each_identity_field_is_copied_exactly(self, key, label, expected, source):
        out = one(fld(key, label))
        assert (out.action, out.value, out.source) == ("fill", expected, source)

    def test_work_authorization_and_sponsorship_are_yes_no_from_the_profile(self):
        auth = one(fld("q", "Are you authorized to work in the US?", "select", options=YES_NO))
        assert (auth.action, auth.value) == ("select", "Yes")
        spons = one(fld("q", "Will you now, or in the future, require employer visa sponsorship?",
                        "select", options=YES_NO))
        assert (spons.action, spons.value) == ("select", "No")
        assert spons.source == "profile:work_authorization.requires_sponsorship"

    STEM_OPT = {"authorized_to_work_in_us": True, "requires_sponsorship": True,
                "requires_sponsorship_now": False, "requires_sponsorship_future": True}

    @pytest.mark.parametrize("label,expected,key", [
        # Affirm's two questions, 2026-11-04.
        ("Do you require immigration sponsorship to work for Affirm in the United States?",
         "No", "requires_sponsorship_now"),
        ("Will you require immigration sponsorship at any point in the future to maintain "
         "authorization to work in the U.S.?", "Yes", "requires_sponsorship_future"),
        ("Will you now, or in the future, require employer visa sponsorship?",
         "Yes", "requires_sponsorship_future"),
        ("Do you now or will you ever require sponsorship?", "Yes", "requires_sponsorship_future"),
        ("Will you need sponsorship to work in the US?", "Yes", "requires_sponsorship_future"),
        ("Do you currently require visa sponsorship?", "No", "requires_sponsorship_now"),
    ])
    def test_sponsorship_now_and_future_are_separate_answers(self, label, expected, key):
        src = sources(answers=answers(work_authorization=self.STEM_OPT))
        out = one(fld("q", label, "select", options=YES_NO), src)
        assert (out.action, out.value) == ("select", expected)
        assert out.source == f"profile:work_authorization.{key}"

    def test_a_combined_authorization_question_with_other_options_is_a_blocker(self):
        """Datadog's wording: three options, none exactly "Yes". Exact match
        only, so the posting goes to review with the options listed."""
        f = fld("q", "Are you legally authorised to work full-time in the country where this job is based?",
                "select", options=(Option("Yes, no restriction.", "1"),
                                   Option("Yes, but I will need sponsorship in the future.", "2"),
                                   Option("No, I need sponsorship now.", "3")))
        out = one(f)
        assert out.action == "blocker" and "Yes, no restriction." in out.note

    def test_location_is_typed_into_an_autocomplete(self):
        out = one(fld("_systemfield_location", "Location", "location", required=False))
        assert (out.action, out.value) == ("autocomplete", "Springfield, IL")

    def test_location_in_an_ashby_custom_autocomplete_is_matched_live(self):
        # Seen on Sierra's form 2026-11-04: a UUID-keyed Location question
        # parsed as a select with no options until opened.
        out = one(fld("0e82aefd", "Location", "select", options=None))
        assert (out.action, out.value) == ("select", "Springfield, IL")
        assert "live widget" in out.note

    def test_a_select_with_known_options_still_needs_an_exact_match(self):
        out = one(fld("q", "Location", "select", options=(Option("Chicago, IL", "1"),)))
        assert out.action == "blocker"

    def test_a_missing_identity_value_is_a_blocker_when_required(self):
        src = sources(answers=answers(identity={"name": "Jane Example"}))
        out = one(fld("email", "Email"), src)
        assert out.action == "blocker" and "identity.email" in out.note

    def test_a_missing_identity_value_is_blank_when_optional(self):
        src = sources(answers=answers(identity={"name": "Jane Example"}))
        out = one(fld("question_2", "Github", required=False), src)
        assert out.action == "blank"


class TestTheNameSplit:
    def test_first_and_last_from_section_12_keys_when_present(self):
        src = sources(answers=answers(identity={"name": "Anish Kumar Naik",
                                                "first_name": "Anish", "last_name": "Naik"}))
        assert one(fld("first_name", "First Name"), src).value == "Anish"
        assert one(fld("last_name", "Last Name"), src).value == "Naik"

    def test_legal_first_and_last_name_labels(self):
        # Sierra's Ashby form, 2026-11-04: UUID keys, "Legal First Name".
        src = sources(answers=answers(identity={"name": "Anish Kumar Naik",
                                                "first_name": "Anish Kumar", "last_name": "Naik"}))
        assert one(fld("180a0dae", "Legal First Name"), src).value == "Anish Kumar"
        assert one(fld("b369c90d", "Legal Last Name"), src).value == "Naik"

    def test_how_did_you_first_learn_is_how_heard(self):
        # Affirm's Greenhouse form, 2026-11-04.
        f = fld("q", "How did you first learn about Affirm as an employer?", "select",
                options=(Option("LinkedIn", "1"), Option("Other", "2")))
        assert resolve.resolve_field(f, sources()).meaning == "how_heard"

    @pytest.mark.parametrize("options,expected,key", [
        ((Option("Illinois", "1"), Option("Indiana", "2")), "Illinois", "state_of_residence"),
        ((Option("IL", "1"), Option("IN", "2")), "IL", "state_abbreviation"),
    ])
    def test_state_of_residence_takes_whichever_spelling_the_dropdown_has(self, options, expected, key):
        # Affirm's Greenhouse form, 2026-11-04.
        lg = {"state_of_residence": "Illinois", "state_abbreviation": "IL"}
        src = sources(answers=answers(logistics=lg))
        f = fld("q", "Which U.S. State or Canadian Province do you reside in?", "select", options=options)
        out = one(f, src)
        assert (out.meaning, out.action, out.value) == ("state_of_residence", "select", expected)
        assert out.source == f"answers:logistics.{key}"

    def test_state_of_residence_with_neither_spelling_is_a_blocker(self):
        src = sources(answers=answers(logistics={"state_of_residence": "Illinois",
                                                 "state_abbreviation": "IL"}))
        f = fld("q", "State of residence", "select", options=(Option("Ill.", "1"),))
        assert one(f, src).action == "blocker"

    def test_city_state_is_still_location(self):
        assert resolve.meaning_of(fld("q", "City, State")) == "location"

    def test_a_two_word_name_splits_unambiguously(self):
        assert one(fld("first_name", "First Name")).value == "Jane"
        assert one(fld("last_name", "Last Name")).value == "Example"

    def test_a_three_word_name_is_not_guessed(self):
        src = sources(answers=answers(identity={"name": "Anish Kumar Naik"}))
        out = one(fld("last_name", "Last Name"), src)
        assert out.action == "blocker"
        assert "first_name and last_name" in out.note


# -- row 2: self-identification -----------------------------------------------

class TestSelfIdentification:
    GENDER = (Option("Decline To Self Identify", "3"), Option("Female", "2"), Option("Male", "1"))

    def test_exact_option_text_is_selected(self):
        out = one(fld("gender", "Gender", "select", required=False, options=self.GENDER, section="eeoc"))
        assert (out.action, out.value, out.source) == ("select", "Male", "answers:self_identification.gender")

    def test_no_exact_match_leaves_it_blank_and_says_so(self):
        src = sources(answers=answers(self_identification={"gender": ["Man"]}))
        out = one(fld("gender", "Gender", "select", required=False, options=self.GENDER, section="eeoc"), src)
        assert out.action == "blank" and "never interpreted" in out.note

    def test_an_empty_list_leaves_it_blank(self):
        out = one(fld("disability_status", "Disability Status", "select", required=False,
                      options=(Option("Yes", "1"), Option("No", "2")), section="eeoc"))
        assert out.action == "blank"

    def test_a_required_self_id_with_no_match_is_a_blocker(self):
        src = sources(answers=answers(self_identification={"gender": []}))
        out = one(fld("gender", "Gender", "select", required=True, options=self.GENDER, section="eeoc"), src)
        assert out.action == "blocker"

    def test_an_eeoc_question_with_an_unreadable_label_is_still_never_guessed(self):
        out = one(fld("q9", "Please tell us about yourself", "select", required=False,
                      options=self.GENDER, section="eeoc"))
        assert out.action == "blank" and out.meaning == "self_identification"

    def test_greenhouse_keys_route_to_the_answers_keys(self):
        f = fld("hispanic_ethnicity", "Are you Hispanic/Latino?", "select", required=False,
                options=(Option("Yes", "1"), Option("No", "0"), Option("Decline To Self Identify", "2")),
                section="eeoc")
        out = one(f)
        assert (out.value, out.source) == ("No", "answers:self_identification.hispanic_or_latino")


# -- row 3: dropdowns generally -------------------------------------------------

class TestDropdowns:
    def test_an_unrecognised_select_is_a_blocker_when_required(self):
        out = one(fld("q", "If you are not based in the NYC metro area, are you willing to relocate?",
                      "select", options=(Option("Yes", "1"), Option("No", "2"), Option("N/A", "3"))))
        assert out.action == "blocker" and "unrecognised" in out.note

    def test_an_unrecognised_select_is_blank_when_optional(self):
        out = one(fld("q", "Favourite editor?", "select", required=False,
                      options=(Option("vim", "1"),)))
        assert out.action == "blank"

    def test_a_known_value_with_no_exact_option_is_a_blocker(self):
        f = fld("q", "How did you hear about this position?", "select",
                options=(Option("LinkedIn (Job Posting)", "1"), Option("Other", "2")))
        src = sources(answers=answers(logistics={"how_did_you_hear": "Company careers page"}))
        out = one(f, src)
        assert out.action == "blocker" and "no option exactly equals" in out.note

    def test_a_known_value_with_an_exact_option_is_selected(self):
        f = fld("q", "How did you hear about this position?", "select",
                options=(Option("LinkedIn (Job Posting)", "1"), Option("Other", "2")))
        src = sources(answers=answers(logistics={"how_did_you_hear": "Other"}))
        assert one(f, src).value == "Other"


# -- row 4: resume ------------------------------------------------------------

class TestResume:
    def test_this_postings_render_is_uploaded(self, tmp_path):
        pdf = tmp_path / "resume.pdf"
        out = one(fld("resume", "Resume/CV", "file", text_key="resume_text"),
                  sources(resume_path=pdf))
        assert (out.action, out.value, out.source) == ("upload", str(pdf), "render_resume")

    def test_no_render_is_a_blocker(self):
        out = one(fld("resume", "Resume/CV", "file"))
        assert out.action == "blocker" and "render_resume" in out.note

    def test_ashby_system_resume(self, tmp_path):
        out = one(fld("_systemfield_resume", "Resume", "file"), sources(resume_path=tmp_path / "r.pdf"))
        assert out.action == "upload"


# -- row 5: cover letter ------------------------------------------------------------

class TestCoverLetter:
    def test_a_file_field_takes_the_reviewed_pdf(self, tmp_path):
        out = one(fld("cover_letter", "Cover Letter", "file", required=False),
                  sources(letter_pdf=tmp_path / "cover_letter.pdf"))
        assert (out.action, out.source) == ("upload", "prepare_letter(pdf=True)")

    def test_a_text_box_takes_the_reviewed_text(self):
        out = one(fld("cover_letter_text", "Cover Letter", "textarea", required=False),
                  sources(letter_text="Dear Team,\n\nBody.\n"))
        assert (out.action, out.value, out.source) == ("fill", "Dear Team,\n\nBody.\n", "prepare_letter")

    def test_required_and_absent_is_a_blocker(self):
        out = one(fld("cover_letter", "Cover Letter", "file", required=True))
        assert out.action == "blocker" and "copy_for_editing" in out.note

    def test_optional_and_absent_is_left_empty(self):
        out = one(fld("cover_letter", "Cover Letter", "file", required=False))
        assert out.action == "blank"


# -- row 6: open-ended questions --------------------------------------------------

QUESTION = "Why do you want to work at Alloy?"


def stored(content="Because of the fraud work.", ok=1, doc_id=7):
    return {spec_norm(QUESTION): {"id": doc_id, "content": content,
                                  "grounding_ok": ok, "grounding_model": "gemini-2.5-flash-lite"}}


def spec_norm(q):
    from jobpipe import answers as a
    return a.normalise_question(q)


class TestOpenEnded:
    def test_a_grounding_passed_answer_is_typed_exactly(self):
        out = one(fld("q", QUESTION, "textarea"), sources(short_answers=stored()))
        assert (out.action, out.value) == ("fill", "Because of the fraud work.")
        assert out.source == "documents:short_answer#7"

    def test_no_stored_answer_is_a_blocker_when_required(self):
        out = one(fld("q", QUESTION, "textarea"))
        assert out.action == "blocker" and "save_short_answer" in out.note

    def test_no_stored_answer_is_blank_when_optional(self):
        out = one(fld("q", "Anything else you'd like us to know?", "textarea", required=False))
        assert out.action == "blank"

    def test_an_unchecked_answer_is_a_blocker(self):
        out = one(fld("q", QUESTION, "textarea"), sources(short_answers=stored(ok=None)))
        assert out.action == "blocker" and "grounding" in out.note

    def test_a_failed_answer_is_a_blocker(self):
        out = one(fld("q", QUESTION, "textarea"), sources(short_answers=stored(ok=0)))
        assert out.action == "blocker"

    def test_a_multi_paragraph_answer_in_a_single_line_box_is_a_blocker(self):
        """The browser strips the breaks and runs sentences together; the
        rule is re-save as one paragraph and re-check."""
        out = one(fld("q", QUESTION, "text"),
                  sources(short_answers=stored(content="One.\n\nTwo.")))
        assert out.action == "blocker" and "one paragraph" in out.note

    def test_a_one_paragraph_answer_fits_a_single_line_box(self):
        out = one(fld("q", QUESTION, "text"), sources(short_answers=stored(content="One. Two.")))
        assert out.action == "fill"

    def test_the_question_is_matched_whitespace_insensitively(self):
        out = one(fld("q", "Why do you want to\nwork at Alloy?", "textarea"),
                  sources(short_answers=stored()))
        assert out.action == "fill"


# -- row 7: never fill ----------------------------------------------------------------

class TestNeverFill:
    @pytest.mark.parametrize("label", [
        "What are your salary requirements?",
        "Social Security Number",
        "Date of birth",
        "Bank account for payroll",
        "I certify that the information provided in this application is true and correct.",
        "Signature",
    ])
    def test_required_is_a_blocker(self, label):
        out = one(fld("q", label, "text"))
        assert out.action == "blocker" and "never filled" in out.note

    @pytest.mark.parametrize("label", ["Desired salary", "Signature"])
    def test_optional_is_left_empty(self, label):
        out = one(fld("q", label, "text", required=False))
        assert out.action == "blank" and out.value is None

    def test_an_attestation_select_is_never_answered_even_with_a_yes_option(self):
        f = fld("q", "I understand my application will be processed in accordance with the Candidate Privacy Policy.",
                "select", options=YES_NO)
        out = one(f)
        assert out.action == "blocker" and out.meaning == "attestation"


# -- row 8: logistics ----------------------------------------------------------------

class TestLogistics:
    def test_a_stored_value_is_used(self):
        out = one(fld("q", "When can you start?", "text", required=False))
        assert (out.action, out.value, out.source) == ("fill", "Immediately", "answers:logistics.earliest_start_date")

    def test_empty_and_optional_is_left_for_anish(self):
        out = one(fld("q", "How did you hear about this position?", "text", required=False))
        assert out.action == "blank" and "left for Anish" in out.note

    def test_empty_and_required_is_a_blocker(self):
        out = one(fld("preferred_name", "Preferred First Name", "text", required=True))
        assert out.action == "blocker"


# -- rows 9 and 10: unrecognised, hidden ----------------------------------------------

class TestUnrecognisedAndHidden:
    def test_an_unknown_widget_is_always_a_blocker(self):
        out = one(fld("q", "Record a video", "unknown", required=False))
        assert out.action == "blocker"

    def test_a_required_unrecognised_multiselect_is_a_blocker(self):
        out = one(fld("q", "In what cities are you available to work?", "multiselect",
                      options=(Option("Denver", "1"),)))
        assert out.action == "blocker"

    def test_a_hidden_field_is_skipped_never_filled(self):
        out = one(fld("longitude", "Longitude", "hidden", section="location"))
        assert out.action == "skip" and out.value is None


# -- the two real forms ------------------------------------------------------------------

class TestRealForms:
    def test_alloy_goes_to_review_for_salary_and_two_unsourced_selects(self, tmp_path):
        form = spec.parse_greenhouse(json.loads(
            (FIXTURES / "greenhouse_alloy_8495229002_questions.json").read_text(encoding="utf-8")))
        src = sources(resume_path=tmp_path / "resume.pdf")
        res = resolve.resolve(form, src)
        notes = {r.field.key: r for r in res.blockers}
        # salary, plus two required selects nobody has a source for: the
        # hybrid-office question and the relocation question. Both are
        # Anish's to answer, so the form goes to review with them listed.
        assert set(notes) == {"question_36029295002", "question_36029296002",
                              "question_36029297002"}, \
            {k: v.note for k, v in notes.items()}
        assert notes["question_36029295002"].meaning == "salary"
        assert "unrecognised" in notes["question_36029296002"].note
        assert "unrecognised" in notes["question_36029297002"].note
        filled = {r.field.key: r.value for r in res.fills}
        assert filled["first_name"] == "Jane" and filled["question_36029293002"] == "linkedin.com/in/jane"
        assert filled["question_36029298002"] == "Yes" and filled["question_36029299002"] == "No"
        assert filled["resume"] == str(tmp_path / "resume.pdf")
        assert filled["gender"] == "Male"
        assert res.by_key("longitude").action == "skip"
        assert res.by_key("cover_letter").action == "blank"

    def test_datadog_goes_to_review_for_its_attestations(self, tmp_path):
        form = spec.parse_greenhouse(json.loads(
            (FIXTURES / "greenhouse_datadog_8053714_questions.json").read_text(encoding="utf-8")))
        res = resolve.resolve(form, sources(resume_path=tmp_path / "resume.pdf"))
        meanings = {r.field.key: r.meaning for r in res.blockers}
        assert meanings["question_68014209"] == "attestation"
        assert meanings["question_68014210"] == "attestation"
        assert meanings["question_68014211"] == "work_authorization"   # three-way options
        assert "question_68014212" in meanings and "question_68014213" in meanings
        assert meanings["preferred_name"] == "preferred_name"           # required, empty
        assert meanings["question_68014214"] == "how_heard"             # required, empty

    def test_langchain_fills_everything_it_can(self, tmp_path):
        html = (FIXTURES / "ashby_langchain_74e5f9f4_application.html").read_text(encoding="utf-8")
        form = spec.parse_ashby_form(html, spec.extract_app_data(html))
        res = resolve.resolve(form, sources(resume_path=tmp_path / "resume.pdf"))
        assert res.blockers == ()
        by = {r.field.key: r for r in res.items}
        assert by["_systemfield_name"].value == "Jane Example"
        assert by["5137f84a-82fa-442d-9fff-8503a0818baf"].value == "No"
        assert by["_systemfield_location"].action == "autocomplete"
        assert by["82baf130-729c-47f7-a795-be9cdee14150"].value == "github.com/jane"


class TestSierraRealForm:
    """The live form that exposed the radio-group gap, 2026-11-04."""

    def form(self):
        html = (FIXTURES / "ashby_sierra_0dfe7805_application.html").read_text(encoding="utf-8")
        return spec.parse_ashby_form(html)

    def test_radio_questions_resolve_or_block_never_vanish(self):
        work = {"authorized_to_work_in_us": True, "requires_sponsorship": True,
                "requires_sponsorship_now": False, "requires_sponsorship_future": True}
        ans = answers(work_authorization=work,
                      identity={"name": "Jane Q Example", "first_name": "Jane",
                                "last_name": "Example", "email": "jane@example.com",
                                "phone": "(555) 010-0000", "linkedin": "linkedin.com/in/jane",
                                "location": "Springfield, IL"},
                      logistics={"how_did_you_hear": "Company Careers Page"})
        res = resolve.resolve(self.form(), sources(answers=ans))
        def by(prefix):
            return next(r for r in res.items if r.field.label.startswith(prefix))
        auth = by("Are you legally authorized")
        assert (auth.action, auth.value) == ("select", "Yes")
        spons = by("Will you now or in the future")
        assert (spons.action, spons.value) == ("select", "Yes")
        assert spons.source == "profile:work_authorization.requires_sponsorship_future"
        for label in ("Are you able to work from a Sierra office",
                      "We use Metaview", "How did you hear about this opportunity?"):
            assert by(label).action == "blocker", label
        assert len(res.items) == 16
