"""Remembered answers: the key, what may be learned, the file, reuse in the
resolver, and the snapshot of choices the runner reads for capture."""
import json
from pathlib import Path

import pytest

from apply_helpers import needs_chromium, open_browser, serve
from jobpipe.apply import fill, learned, resolve, spec
from jobpipe.apply.spec import Field, FormSpec, Option

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "apply"
YES_NO = (Option("Yes", "1"), Option("No", "0"))


def fld(key, label, kind="select", required=True, options=YES_NO, section="application"):
    return Field(key=key, label=label, kind=kind, required=required, section=section, options=options)


class TestTheKey:
    def test_whitespace_case_and_company(self):
        assert learned.key_for("How did you  first learn about\nAffirm as an employer?", "Affirm") == \
            "how did you first learn about {company} as an employer?"

    def test_every_way_of_naming_the_company(self):
        k = learned.key_for("Have you applied to Cursor before? Anysphere asks.", "Cursor (Anysphere)")
        assert k == "have you applied to {company} before? {company} asks."

    def test_no_company_means_no_substitution(self):
        assert learned.key_for("Anything?", None) == "anything?"

    def test_a_word_containing_the_name_is_left_alone(self):
        assert learned.key_for("Answer affirmatively", "Affirm") == "answer affirmatively"

    def test_the_answer_is_templated_and_expanded(self):
        t = learned.template_answer("Yes, I am based in one of Sierra's office locations.", "Sierra")
        assert t == "Yes, I am based in one of {company}'s office locations."
        assert learned.candidate_answers(t, "Cursor (Anysphere)")[:2] == (
            "Yes, I am based in one of Cursor's office locations.",
            "Yes, I am based in one of Cursor (Anysphere)'s office locations.")
        assert learned.candidate_answers("No", "Sierra") == ("No",)


class TestWhatMayBeLearned:
    @pytest.mark.parametrize("kind,ok", [("select", True), ("radio", True), ("yesno", True),
                                         ("text", False), ("textarea", False), ("file", False),
                                         ("multiselect", False), ("checkbox", False)])
    def test_choice_widgets_only(self, kind, ok):
        assert learned.learnable(fld("q", "Office?", kind), None) is ok

    @pytest.mark.parametrize("meaning", ["salary", "attestation", "signature", "government_id",
                                         "gender", "veteran_status", "work_authorization",
                                         "sponsorship", "prior_employment", "resume", "cover_letter"])
    def test_never_these_meanings(self, meaning):
        assert not learned.learnable(fld("q", "x"), meaning)

    @pytest.mark.parametrize("meaning", [None, "how_heard", "state_of_residence", "start_date"])
    def test_these_may_be(self, meaning):
        assert learned.learnable(fld("q", "x"), meaning)

    def test_never_a_self_id_section(self):
        assert not learned.learnable(fld("gender", "Gender", section="eeoc"), None)
        assert not learned.learnable(fld("q", "Identity", section="demographic"), None)


class TestTheFile:
    def test_missing_is_empty(self, tmp_path):
        store = learned.load(tmp_path / "absent.yaml")
        assert store.entries == {} and store.path == tmp_path / "absent.yaml"

    def test_the_example_loads(self):
        store = learned.load(REPO / "config" / "learned_answers.example.yaml")
        assert "how did you first learn about {company} as an employer?" in store.entries
        e = store.entries["are you able to work from a {company} office listed for this role?"]
        assert e.kind == "radio" and e.latest.company == "Example Co"

    def test_it_is_gitignored(self):
        assert "config/learned_answers.yaml" in (REPO / ".gitignore").read_text(encoding="utf-8")

    def test_free_text_kinds_are_refused(self, tmp_path):
        path = tmp_path / "l.yaml"
        path.write_text('answers:\n  "why us?": {answer: "Because", kind: textarea}\n')
        with pytest.raises(learned.LearnedError, match="only"):
            learned.load(path)

    def test_record_save_load_round_trip(self, tmp_path):
        store = learned.Store(path=tmp_path / "l.yaml")
        learned.record(store, "How did you hear about Sierra?", "Company website", "radio",
                       "Sierra", 27481, when="2026-11-04")
        learned.record(store, "How did you hear about Affirm?", "Company website", "select",
                       "Affirm", 24670, when="2026-11-05")
        learned.save(store)
        again = learned.load(tmp_path / "l.yaml")
        e = again.get("How did you hear about Cohere?", "Cohere")
        assert e.answer == "Company website" and [s.company for s in e.seen_on] == ["Sierra", "Affirm"]
        assert e.kind == "select"

    def test_a_changed_answer_supersedes_the_old_one(self):
        store = learned.Store()
        learned.record(store, "Relocate?", "Yes", "select", "A", 1, "2026-11-04")
        learned.record(store, "Relocate?", "No", "select", "B", 2, "2026-11-05")
        e = store.get("Relocate?", None)
        assert e.answer == "No" and e.superseded == ["Yes"]


def with_store(**entries):
    store = learned.Store()
    for question, (answer, kind) in entries.items():
        learned.record(store, question, answer, kind, "Example Co", 1, "2026-11-05")
    return store


class TestReuseInTheResolver:
    def src(self, store, company="Affirm"):
        return resolve.Sources(answers={"identity": {}, "logistics": {}}, company=company,
                               employers=(), learned=store)

    def test_a_blocker_select_takes_the_remembered_option(self):
        store = with_store(**{"How did you first learn about Example Co as an employer?": ("Company website", "select")})
        f = fld("q", "How did you first learn about Affirm as an employer?",
                options=(Option("LinkedIn", "1"), Option("Company website", "2")))
        out = resolve.resolve(FormSpec("greenhouse", "1", "Affirm", "x", (f,)), self.src(store)).items[0]
        assert (out.action, out.value) == ("select", "Company website")
        assert out.source == "learned:Example Co #1 2026-11-05"

    def test_an_optional_blank_takes_it_too(self):
        store = with_store(**{"Willing to relocate?": ("N/A", "select")})
        f = fld("q", "Willing to relocate?", required=False,
                options=(Option("Yes", "1"), Option("No", "2"), Option("N/A", "3")))
        out = resolve.resolve(FormSpec("greenhouse", "1", "Affirm", "x", (f,)), self.src(store)).items[0]
        assert (out.action, out.value) == ("select", "N/A")

    def test_an_ashby_select_without_options_defers_to_the_live_listbox(self):
        store = with_store(**{"How did you hear about us?": ("Company website", "select")})
        f = fld("q", "How did you hear about us?", options=None)
        out = resolve.resolve(FormSpec("ashby", "1", "Affirm", "x", (f,)), self.src(store)).items[0]
        assert (out.action, out.value) == ("select", "Company website")
        assert "live widget" in out.note

    def test_not_an_option_here_stays_a_blocker(self):
        store = with_store(**{"Willing to relocate?": ("Maybe", "select")})
        f = fld("q", "Willing to relocate?", options=YES_NO)
        out = resolve.resolve(FormSpec("greenhouse", "1", "Affirm", "x", (f,)), self.src(store)).items[0]
        assert out.action == "blocker" and "not an option here" in out.note

    def test_a_profile_fact_is_never_taken_from_memory(self):
        store = with_store(**{"Are you authorized to work in the US?": ("Yes", "select")})
        f = fld("q", "Are you authorized to work in the US?", options=YES_NO)
        out = resolve.resolve(FormSpec("greenhouse", "1", "Affirm", "x", (f,)), self.src(store)).items[0]
        assert out.action == "blocker" and out.source is None

    def test_a_text_question_is_never_taken_from_memory(self):
        store = with_store(**{"Favourite editor?": ("vim", "select")})
        f = fld("q", "Favourite editor?", kind="text", options=None)
        out = resolve.resolve(FormSpec("greenhouse", "1", "Affirm", "x", (f,)), self.src(store)).items[0]
        assert out.action == "blocker"

    def test_a_sourced_answer_is_not_overridden(self):
        store = with_store(**{"How did you hear about us?": ("Other", "select")})
        f = fld("q", "How did you hear about us?", options=(Option("Other", "1"), Option("Friend", "2")))
        sources = resolve.Sources(answers={"identity": {}, "logistics": {"how_did_you_hear": "Friend"}},
                                  company="Affirm", employers=(), learned=store)
        out = resolve.resolve(FormSpec("greenhouse", "1", "Affirm", "x", (f,)), sources).items[0]
        assert (out.value, out.source) == ("Friend", "answers:logistics.how_did_you_hear")

    def test_the_example_store_answers_sierras_office_question(self):
        """The example entry was seen at Example Co; its answer names the
        company. On Sierra's real form the key and the option both expand."""
        html = (FIXTURES / "ashby_sierra_0dfe7805_application.html").read_text(encoding="utf-8")
        form = spec.parse_ashby_form(html, spec.extract_app_data(html))
        store = learned.load(REPO / "config" / "learned_answers.example.yaml")
        res = resolve.resolve(form, self.src(store, company="Sierra"))
        office = res.by_key("3d33a44a-7eeb-465b-8299-84788e69a3bc")
        assert office.action == "select"
        assert office.value == "Yes, I am based in one of Sierra's office locations."
        assert office.source.startswith("learned:")


class TestRecordingChoices:
    def test_only_unfilled_learnable_options(self):
        store = learned.Store()
        heard = fld("heard", "How did you hear about Affirm?", "radio",
                    options=(Option("Blog", "Blog"), Option("Company website", "Company website")))
        auth = fld("auth", "Are you authorized to work in the US?", "radio")
        gender = fld("gender", "Gender", section="eeoc", options=(Option("Male", "1"),))
        filled = fld("relocate", "Relocate?", options=YES_NO)
        form = FormSpec("ashby", "1", "Affirm", "x", (heard, auth, gender, filled))
        res = resolve.Resolution((
            resolve.Resolved(heard, resolve.BLOCKER), resolve.Resolved(auth, resolve.BLOCKER),
            resolve.Resolved(gender, resolve.BLANK),
            resolve.Resolved(filled, resolve.SELECT, "No", "learned:x")))
        recorded = learned.record_choices(store, form, res, {
            "heard": "Company website", "auth": "Yes", "gender": "Male", "relocate": "Yes"},
            "Affirm", 24670, "2026-11-05")
        assert recorded == ["heard"]
        e = store.get("How did you hear about Cohere?", "Cohere")
        assert e.answer == "Company website" and e.kind == "radio"

    def test_a_choice_that_is_not_one_of_the_options_is_ignored(self):
        store = learned.Store()
        f = fld("q", "Relocate?", options=YES_NO)
        res = resolve.Resolution((resolve.Resolved(f, resolve.BLOCKER),))
        assert learned.record_choices(store, FormSpec("greenhouse", "1", "A", "x", (f,)), res,
                                      {"q": "Maybe"}, "A", 1) == []


@pytest.fixture(scope="module")
def site():
    with serve() as base:
        yield base


@needs_chromium
class TestReadingChoicesFromThePage:
    def test_ashby_radio_yes_no_and_unset(self, site, tmp_path):
        html = (FIXTURES / "pages" / "ashby_form.html").read_text(encoding="utf-8")
        form = spec.parse_ashby_form(html)
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/ashby_form.html")
            before = fill.read_choices(page, form)
            assert before["5137f84a-82fa-442d-9fff-8503a0818baf"] is None
            assert before["de771a19-ae1f-42ca-885d-dfc8861fa2ad"] is None
            page.click('button[data-option="no"]')
            page.click('label[for="r-auth-0"]')
            after = fill.read_choices(page, form)
            assert after["5137f84a-82fa-442d-9fff-8503a0818baf"] == "No"
            assert after["de771a19-ae1f-42ca-885d-dfc8861fa2ad"] == "Yes"

    def test_greenhouse_react_select(self, site, tmp_path):
        gender = Field("gender", "Gender", "select", False, section="eeoc",
                       options=(Option("Decline To Self Identify", "3"), Option("Female", "2"), Option("Male", "1")))
        auth = Field("question_36029298002", "Authorized?", "select", True, options=YES_NO)
        form = FormSpec("greenhouse", "1", "Alloy", "x", (gender, auth))
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/greenhouse_form.html")
            assert fill.read_choices(page, form) == {"gender": None, "question_36029298002": None}
            page.click("#question_36029298002")
            page.click('#react-select-question_36029298002-option-1')
            assert fill.read_choices(page, form)["question_36029298002"] == "No"

    def test_no_form_means_no_snapshot(self, site, tmp_path):
        form = FormSpec("ashby", "1", "x", "x", ())
        with open_browser(tmp_path) as b:
            page = b.new_page()
            page.goto(f"{site}/greenhouse_form.html")
            assert fill.read_choices(page, form) == {}
