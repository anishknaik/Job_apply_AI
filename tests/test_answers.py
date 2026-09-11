"""The answers file and the view a form is filled from (PHASE5_PLAN §6.1.1).

Two properties matter. Self-ID answers must be the form's EXACT option text,
so a filler selects rather than interprets - which means a YAML boolean has
to be refused, not coerced. And the identity fields come from §12 live, never
from a second hand-kept copy.
"""
import textwrap
from pathlib import Path

import pytest
import yaml

from jobpipe import answers, header

PROFILE = textwrap.dedent("""\
    # Profile

    ## 12. Structured identity

    ```yaml
    identity:
      name: Jane Q. Example
      email: jane@example.com
      phone: "(555) 010-0000"
      linkedin: linkedin.com/in/example
      location: Springfield, IL
      github: github.com/example
    education:
      school: Example State
      degree: B.S. Computer Science
      date: May 2025
      coursework: >-
        Machine Learning,
        Databases
    experience:
      - id: acme
        company: Acme
        title: Intern
        dates: Jun 2024 - Aug 2024
    work_authorization:
      authorized_to_work_in_us: true
      requires_sponsorship: false
      status: US citizen
    ```
    """)


def write(tmp_path, text):
    path = tmp_path / "answers.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


class TestLoad:
    def test_a_missing_file_says_how_to_create_it(self, tmp_path):
        with pytest.raises(answers.AnswersError, match="answers.example.yaml"):
            answers.load(tmp_path / "nope.yaml")

    def test_exact_option_text_loads(self, tmp_path):
        path = write(tmp_path, """\
            self_identification:
              gender: ["Decline To Self Identify", "I don't wish to answer"]
              veteran_status: []
            logistics:
              earliest_start_date: Immediately
            """)
        out = answers.load(path)
        assert out["self_identification"]["gender"] == [
            "Decline To Self Identify", "I don't wish to answer"]
        assert out["self_identification"]["veteran_status"] == []
        assert out["logistics"]["earliest_start_date"] == "Immediately"

    @pytest.mark.parametrize("entry", ["[No]", "[yes]", "[off]", "- No"])
    def test_a_yaml_boolean_is_refused_not_coerced(self, tmp_path, entry):
        """An unquoted No is False to YAML. Coercing it back cannot recover
        whether the form said "No", "no" or "NO", so it must be refused."""
        body = (f"  transgender: {entry}" if entry.startswith("[")
                else f"  transgender:\n    {entry}")
        path = tmp_path / "answers.yaml"
        path.write_text("self_identification:\n" + body + "\n",
                        encoding="utf-8")
        with pytest.raises(answers.AnswersError, match="quote it"):
            answers.load(path)

    def test_a_bare_string_instead_of_a_list_is_refused(self, tmp_path):
        path = write(tmp_path, """\
            self_identification:
              gender: Decline To Self Identify
            """)
        with pytest.raises(answers.AnswersError, match="list"):
            answers.load(path)

    def test_an_empty_entry_is_refused(self, tmp_path):
        path = write(tmp_path, """\
            self_identification:
              gender: ["  "]
            """)
        with pytest.raises(answers.AnswersError, match="exact option text"):
            answers.load(path)

    def test_a_null_list_means_leave_it(self, tmp_path):
        path = write(tmp_path, """\
            self_identification:
              pronouns:
            """)
        assert answers.load(path)["self_identification"]["pronouns"] == []

    def test_a_bare_date_comes_back_as_text(self, tmp_path):
        path = write(tmp_path, """\
            logistics:
              work_authorization_expires: 2027-06-30
            """)
        assert answers.load(path)["logistics"][
            "work_authorization_expires"] == "2027-06-30"


class TestTheTemplate:
    """config/answers.example.yaml is tracked. It must load, and it must never
    hold an answer - the real file is the gitignored copy."""

    def test_it_loads(self):
        answers.load(answers.EXAMPLE_PATH)

    def test_it_holds_no_answers(self):
        out = answers.load(answers.EXAMPLE_PATH)
        assert all(v == [] for v in out["self_identification"].values())
        assert all(v == "" for v in out["logistics"].values())

    def test_the_real_file_is_gitignored(self):
        text = Path(".gitignore").read_text(encoding="utf-8")
        assert "config/answers*" in text
        assert "!config/answers.example.yaml" in text


class TestFormView:
    def _view(self, tmp_path, body="self_identification:\n  gender: []\n"):
        return answers.form_view(PROFILE, answers.load(write(tmp_path, body)))

    def test_identity_comes_from_section_12_including_extra_keys(self,
                                                                 tmp_path):
        """github is not a header key; the form view still carries it."""
        view = self._view(tmp_path)
        assert view["identity"]["github"] == "github.com/example"
        assert view["identity"]["email"] == "jane@example.com"

    def test_work_authorization_passes_through(self, tmp_path):
        view = self._view(tmp_path)
        assert view["work_authorization"]["requires_sponsorship"] is False

    def test_folded_education_is_one_line(self, tmp_path):
        assert self._view(tmp_path)["education"]["coursework"] == (
            "Machine Learning, Databases")

    def test_self_id_comes_only_from_the_answers_file(self, tmp_path):
        view = self._view(tmp_path, "self_identification:\n"
                                    "  gender: [\"Decline To Self Identify\"]\n")
        assert view["self_identification"] == {
            "gender": ["Decline To Self Identify"]}

    def test_empty_answers_are_listed_for_anish(self, tmp_path):
        view = self._view(tmp_path, "self_identification:\n  gender: []\n"
                                    "logistics:\n  preferred_name: ''\n")
        assert "self_identification.gender" in view["leave_for_anish"]
        assert "logistics.preferred_name" in view["leave_for_anish"]

    def test_the_never_fill_list_always_comes_along(self, tmp_path):
        view = self._view(tmp_path)
        assert any("salary" in item for item in view["never_fill"])
        assert any("government" in item for item in view["never_fill"])

    def test_missing_work_authorization_is_flagged(self, tmp_path):
        profile = PROFILE.split("work_authorization:")[0] + "```\n"
        view = answers.form_view(profile, answers.load(
            write(tmp_path, "self_identification: {}\n")))
        assert view["work_authorization"] == {}
        assert any("work_authorization" in m for m in view["leave_for_anish"])

    def test_a_profile_without_section_12_is_refused(self, tmp_path):
        with pytest.raises(header.HeaderError):
            answers.form_view("# no section 12 here\n", answers.load(
                write(tmp_path, "self_identification: {}\n")))
