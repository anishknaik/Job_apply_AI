"""The generated resume header (PHASE5_PLAN §4.1, Track B slice 3).

`test_bullets.TestHeaderMatchesTheProfile` asserts the bank and the profile
agree. These test the thing that makes them agree, and the one that matters
most is `test_the_generated_file_agrees_with_the_profile`: a §12 edit that is
never regenerated is otherwise invisible until it has shipped on a resume.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jobpipe import header

PROFILE = """\
# Profile

## 11. Calibration examples

```yaml
# A decoy fence, earlier in the file, exactly where the real one is.
examples: [{title: Nope}]
```

## 12. Structured identity

```yaml
identity:
  name: Ada Lovelace
  email: ada@example.com
  phone: (555) 010-0000
  linkedin: linkedin.com/in/ada
  location: London, UK

education:
  school: A University
  degree: B.S. Mathematics
  date: May 1843
  coursework: >-
    Analytical Engines, Difference Engines,
    Bernoulli Numbers

experience:
  - id: babbage
    company: Babbage & Co
    title: Analyst
    dates: Jan 1842 - Dec 1843
  - id: notes
    company: Taylor's Scientific Memoirs
    title: Translator
    dates: 1843
```
"""


class TestExtract:
    def test_it_reads_the_identity(self):
        got = header.extract(PROFILE)
        assert got["meta"]["name"] == "Ada Lovelace"
        assert got["meta"]["phone"] == "(555) 010-0000"

    def test_it_reads_every_role(self):
        roles = header.extract(PROFILE)["roles"]
        assert set(roles) == {"babbage", "notes"}
        assert roles["babbage"]["title"] == "Analyst"

    def test_it_ignores_an_earlier_yaml_fence(self):
        """§11's calibration examples are a real yaml block earlier in the
        file. Anchoring on the fence alone would pick it up and generate a
        header out of scoring examples."""
        assert "examples" not in header.extract(PROFILE)

    def test_coursework_is_folded_to_one_line(self):
        """The profile wraps it for legibility; a newline reaching the PDF
        renders as a gap in the education block."""
        course = header.extract(PROFILE)["education"]["coursework"]
        assert "\n" not in course
        assert course.startswith("Analytical Engines, Difference Engines,")

    def test_a_missing_section_is_an_error_not_a_partial_header(self):
        """Returning what it found would leave the bank's hand-written value
        in place for the rest — the exact drift this module removes."""
        with pytest.raises(header.HeaderError):
            header.extract("# Profile\n\nNo section twelve here.\n")

    def test_a_missing_field_names_itself(self):
        broken = PROFILE.replace("  phone: (555) 010-0000\n", "")
        with pytest.raises(header.HeaderError, match="phone"):
            header.extract(broken)

    def test_a_role_without_an_id_is_an_error(self):
        broken = PROFILE.replace("  - id: babbage\n", "  - \n")
        with pytest.raises(header.HeaderError):
            header.extract(broken)


class TestMerge:
    def _bank(self):
        return {
            "meta": {"name": "STALE", "email": "stale@example.com",
                     "headline": {"ai_ml": "kept"}},
            "education": {"school": "STALE", "degree": "kept-if-absent"},
            "roles": [
                {"id": "babbage", "kind": "experience", "company": "STALE",
                 "title": "STALE", "dates": "STALE", "location": "kept",
                 "groups": [{"id": "g1"}]},
                {"id": "a-project", "kind": "project", "company": "kept"},
            ],
        }

    def test_generated_fields_win(self):
        raw = header.merge_into(self._bank(), header.extract(PROFILE))
        assert raw["meta"]["name"] == "Ada Lovelace"
        assert raw["roles"][0]["company"] == "Babbage & Co"
        assert raw["education"]["school"] == "A University"

    def test_hand_edited_fields_survive(self):
        """§12 declines to own headline, kind, location and the group stack
        lines: they are keyword tuning and layout, not facts about him."""
        raw = header.merge_into(self._bank(), header.extract(PROFILE))
        assert raw["meta"]["headline"] == {"ai_ml": "kept"}
        assert raw["roles"][0]["kind"] == "experience"
        assert raw["roles"][0]["location"] == "kept"
        assert raw["roles"][0]["groups"] == [{"id": "g1"}]

    def test_a_role_the_profile_does_not_mention_is_untouched(self):
        """Projects are roles in the bank's shape and carry no employer or
        tenure, so §12 lists none of them."""
        raw = header.merge_into(self._bank(), header.extract(PROFILE))
        assert raw["roles"][1] == {"id": "a-project", "kind": "project",
                                   "company": "kept"}

    def test_no_header_is_a_no_op(self):
        """A fresh checkout has no gitignored profile to generate from. The
        bank's own hand-written header still has to render."""
        before = self._bank()
        assert header.merge_into(before, None) is before
        assert before["meta"]["name"] == "STALE"


class TestFileRoundTrip:
    def test_render_then_load_returns_what_went_in(self, tmp_path):
        source = header.extract(PROFILE)
        path = tmp_path / "header.generated.yaml"
        path.write_text(header.render(source), encoding="utf-8")
        assert header.load(path) == source

    def test_the_banner_says_not_to_edit_it(self):
        assert "do not edit" in header.render(header.extract(PROFILE)).lower()

    def test_a_missing_file_loads_as_none(self, tmp_path):
        assert header.load(tmp_path / "absent.yaml") is None

    def test_write_reports_whether_anything_changed(self, tmp_path):
        profile = tmp_path / "profile.md"
        profile.write_text(PROFILE, encoding="utf-8")
        out = tmp_path / "header.generated.yaml"

        assert header.write(profile, out)[1] is True
        assert header.write(profile, out)[1] is False, "rewrote an identical file"

    def test_a_missing_profile_explains_itself(self, tmp_path):
        with pytest.raises(header.HeaderError, match="gitignored"):
            header.write(tmp_path / "absent.md", tmp_path / "out.yaml")

    def test_diff_names_the_drifted_field(self, tmp_path):
        profile = tmp_path / "profile.md"
        profile.write_text(PROFILE, encoding="utf-8")
        out = tmp_path / "header.generated.yaml"
        header.write(profile, out)

        profile.write_text(PROFILE.replace("Ada Lovelace", "Ada B. Lovelace"),
                           encoding="utf-8")
        assert header.diff(profile, out) == ["meta.name"]

    def test_an_ungenerated_header_is_reported_as_drift(self, tmp_path):
        profile = tmp_path / "profile.md"
        profile.write_text(PROFILE, encoding="utf-8")
        assert header.diff(profile, tmp_path / "absent.yaml")


class TestAgainstTheRealProfile:
    """The check that replaces keeping two files in sync by hand."""

    def test_the_generated_file_agrees_with_the_profile(self):
        if not Path(header.PROFILE_PATH).exists():
            pytest.skip("master_profile.md not present (gitignored)")
        drift = header.diff()
        assert drift == [], (
            "config/header.generated.yaml has drifted from master_profile.md "
            "§12. Run `python -m jobpipe.cli header`. Until you do, every "
            f"resume renders the stale value. Drifted: {drift}")

    def test_the_bank_actually_picks_the_generated_values_up(self):
        """Merging at load_bank() is what makes the generation matter; the
        two files agreeing proves nothing if the overlay never runs."""
        if not Path(header.HEADER_PATH).exists():
            pytest.skip("header not generated (needs the gitignored profile)")
        from jobpipe import bullets

        generated = header.load()
        bank = bullets.load_bank()
        assert bank.meta["name"] == generated["meta"]["name"]
        for rid, fields in generated["roles"].items():
            assert bank.roles[rid]["company"] == fields["company"]
            assert bank.roles[rid]["dates"] == fields["dates"]
