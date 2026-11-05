"""Phase 6, Slice 5: decide.py, one test per condition, each failing alone.

A baseline where every condition holds decides auto_submit; each test
changes exactly one thing and expects review with that condition named.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobpipe.apply import decide, resolve
from jobpipe.apply.config import ApplyConfig
from jobpipe.apply.spec import Field, Option

NOW = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)
ON = ApplyConfig(auto_submit=True, source="config/apply.yaml")


def resolution(tmp_path, *extra):
    first = Field("first_name", "First Name", "text", True)
    resume = Field("resume", "Resume/CV", "file", True)
    why = Field("q1", "Why here?", "textarea", True)
    items = [
        resolve.Resolved(first, resolve.FILL, "Jane", "profile:identity.first_name", "first_name"),
        resolve.Resolved(resume, resolve.UPLOAD, str(tmp_path / "posting" / "resume.pdf"),
                         "render_resume", "resume"),
        resolve.Resolved(why, resolve.FILL, "Because.", "documents:short_answer#7", "open_ended"),
        *extra,
    ]
    return resolve.Resolution(tuple(items))


def baseline(tmp_path, **over):
    kw = dict(config=ON, mode=decide.AUTO_MODE, resolution=resolution(tmp_path),
              unverified=(), posting_dir=tmp_path / "posting",
              signals_before=(), signals_at_submit=(),
              answer_writers={"documents:short_answer#7": "claude-mcp",
                              "prepare_letter": "claude-mcp",
                              "prepare_letter(pdf=True)": "claude-mcp"},
              prior_submit_attempts=0, current_status="ready_for_review",
              recent_auto_submits=(), now=NOW, required_missing=())
    kw.update(over)
    return decide.Inputs(**kw)


def failing(decision):
    return [c.name for c in decision.checks if not c.passed]


class TestTheBaseline:
    def test_everything_holding_is_auto_submit(self, tmp_path):
        d = decide.decide(baseline(tmp_path))
        assert d.auto_submit and d.reasons == []
        assert len(d.checks) == 9


class TestEachConditionAlone:
    def test_1_config_off(self, tmp_path):
        d = decide.decide(baseline(tmp_path, config=ApplyConfig(auto_submit=False)))
        assert failing(d) == ["enabled"] and "auto_submit is off" in d.reasons[0]

    def test_1_run_not_auto(self, tmp_path):
        for mode in (decide.REVIEW_MODE, decide.DRY_RUN):
            d = decide.decide(baseline(tmp_path, mode=mode))
            assert failing(d) == ["enabled"] and mode in d.reasons[0]

    def test_2_bot_check_before_fill(self, tmp_path):
        d = decide.decide(baseline(tmp_path, signals_before=("recaptcha",)))
        assert failing(d) == ["no_bot_check"] and "recaptcha" in d.reasons[0]

    def test_2_bot_check_just_before_submit(self, tmp_path):
        d = decide.decide(baseline(tmp_path, signals_at_submit=("turnstile",)))
        assert failing(d) == ["no_bot_check"]

    def test_2_not_rechecked_before_submit_is_not_a_pass(self, tmp_path):
        d = decide.decide(baseline(tmp_path, signals_at_submit=None))
        assert failing(d) == ["no_bot_check"] and "not re-checked" in d.reasons[0]

    def test_3_a_blocker(self, tmp_path):
        salary = Field("q2", "What are your salary requirements?", "text", True)
        res = resolution(tmp_path, resolve.Resolved(salary, resolve.BLOCKER, meaning="salary",
                                                    note="salary: never filled"))
        d = decide.decide(baseline(tmp_path, resolution=res))
        # a required salary field trips 3 and 5 together; both are true
        assert failing(d) == ["no_blockers", "no_required_never_fill"]

    def test_3_a_fill_error(self, tmp_path):
        d = decide.decide(baseline(tmp_path, fill_error="no form on the page"))
        assert failing(d) == ["no_blockers"]

    def test_4_a_value_that_did_not_read_back(self, tmp_path):
        d = decide.decide(baseline(tmp_path, unverified=("first_name",)))
        assert failing(d) == ["verified"] and "first_name" in d.reasons[0]

    def test_4_an_upload_from_outside_the_postings_folder(self, tmp_path):
        other = Field("cover_letter", "Cover Letter", "file", False)
        res = resolution(tmp_path, resolve.Resolved(
            other, resolve.UPLOAD, str(tmp_path / "elsewhere" / "cover_letter.pdf"),
            "prepare_letter(pdf=True)", "cover_letter"))
        d = decide.decide(baseline(tmp_path, resolution=res))
        assert failing(d) == ["verified"] and "not in this posting's folder" in d.reasons[0]

    def test_5_a_required_never_fill_that_was_optional_elsewhere(self, tmp_path):
        """An optional attestation is a blank (condition 3 holds); a required
        one is not auto-submittable even if it were somehow filled."""
        att = Field("q3", "I certify that the above is true", "select", True, options=(Option("Yes", "1"),))
        res = resolution(tmp_path, resolve.Resolved(att, resolve.BLANK, meaning="attestation"))
        d = decide.decide(baseline(tmp_path, resolution=res))
        assert "no_required_never_fill" in failing(d)

    def test_6_an_answer_written_by_a_drafting_model(self, tmp_path):
        d = decide.decide(baseline(tmp_path, answer_writers={"documents:short_answer#7": "gemini-2.5-flash"}))
        assert failing(d) == ["answers_session_written"] and "gemini" in d.reasons[0]

    def test_6_an_answer_with_no_recorded_writer(self, tmp_path):
        d = decide.decide(baseline(tmp_path, answer_writers={}))
        assert failing(d) == ["answers_session_written"]

    # Phase 7A (PHASE7_PLAN §1): Gemini 2.5 Pro drafts answers and letters,
    # and a form carrying any of them must never auto-submit. One test per
    # shape the drafted prose reaches a form in.

    def test_6_a_gemini_drafted_answer_forces_review(self, tmp_path):
        d = decide.decide(baseline(tmp_path, answer_writers={
            "documents:short_answer#7": "gemini-2.5-pro",
            "prepare_letter": "claude-mcp", "prepare_letter(pdf=True)": "claude-mcp"}))
        assert not d.auto_submit
        assert failing(d) == ["answers_session_written"]
        assert "gemini-2.5-pro" in d.reasons[0] and "Why here?" in d.reasons[0]

    def test_6_a_gemini_drafted_letter_pasted_into_a_letter_box_forces_review(self, tmp_path):
        box = Field("cover_letter_text", "Cover Letter", "textarea", False)
        res = resolution(tmp_path, resolve.Resolved(
            box, resolve.FILL, "Dear Team,", "prepare_letter", "cover_letter"))
        d = decide.decide(baseline(tmp_path, resolution=res, answer_writers={
            "documents:short_answer#7": "claude-mcp",
            "prepare_letter": "gemini-2.5-pro", "prepare_letter(pdf=True)": "gemini-2.5-pro"}))
        assert failing(d) == ["answers_session_written"] and "gemini-2.5-pro" in d.reasons[0]

    def test_6_a_gemini_drafted_letter_uploaded_as_a_pdf_forces_review(self, tmp_path):
        upload = Field("cover_letter", "Cover Letter", "file", False)
        res = resolution(tmp_path, resolve.Resolved(
            upload, resolve.UPLOAD, str(tmp_path / "posting" / "cover_letter.pdf"),
            "prepare_letter(pdf=True)", "cover_letter"))
        d = decide.decide(baseline(tmp_path, resolution=res, answer_writers={
            "documents:short_answer#7": "claude-mcp",
            "prepare_letter": "gemini-2.5-pro", "prepare_letter(pdf=True)": "gemini-2.5-pro"}))
        assert failing(d) == ["answers_session_written"]

    def test_6_a_letter_in_an_anything_else_box_with_no_recorded_writer(self, tmp_path):
        """A `cli tailor --cover-letter` letter records the prose model; an
        older row may record nothing. Neither is a session."""
        box = Field("extra", "Anything else?", "textarea", False)
        res = resolution(tmp_path, resolve.Resolved(
            box, resolve.FILL, "Dear Team,", "prepare_letter", "additional_info"))
        d = decide.decide(baseline(tmp_path, resolution=res, answer_writers={
            "documents:short_answer#7": "claude-mcp"}))
        assert failing(d) == ["answers_session_written"] and "unrecorded" in d.reasons[0]

    def test_6_a_session_written_letter_still_passes(self, tmp_path):
        box = Field("cover_letter_text", "Cover Letter", "textarea", False)
        res = resolution(tmp_path, resolve.Resolved(
            box, resolve.FILL, "Dear Team,", "prepare_letter", "cover_letter"))
        assert decide.decide(baseline(tmp_path, resolution=res)).auto_submit

    def test_7_a_prior_submit_click(self, tmp_path):
        d = decide.decide(baseline(tmp_path, prior_submit_attempts=1))
        assert failing(d) == ["first_attempt"] and "prior submit" in d.reasons[0]

    def test_7_already_applied(self, tmp_path):
        d = decide.decide(baseline(tmp_path, current_status="applied"))
        assert failing(d) == ["first_attempt"]

    def test_8_the_daily_cap(self, tmp_path):
        stamps = tuple((NOW - timedelta(hours=h)).isoformat() for h in (1, 3, 5, 7, 9))
        d = decide.decide(baseline(tmp_path, recent_auto_submits=stamps))
        assert failing(d) == ["under_limits"] and "cap 5" in d.reasons[0]

    def test_8_the_cap_is_a_rolling_24_hours(self, tmp_path):
        stamps = tuple((NOW - timedelta(hours=h)).isoformat() for h in (1, 3, 5, 7, 25))
        d = decide.decide(baseline(tmp_path, recent_auto_submits=stamps))
        assert d.auto_submit

    def test_8_the_spacing(self, tmp_path):
        d = decide.decide(baseline(tmp_path, recent_auto_submits=((NOW - timedelta(minutes=4)).isoformat(),)))
        assert failing(d) == ["under_limits"] and "minimum 10" in d.reasons[0]

    def test_8_spacing_satisfied(self, tmp_path):
        d = decide.decide(baseline(tmp_path, recent_auto_submits=((NOW - timedelta(minutes=11)).isoformat(),)))
        assert d.auto_submit


class TestTheNinthCondition:
    def test_9_the_page_was_not_read(self, tmp_path):
        d = decide.decide(baseline(tmp_path, required_missing=None))
        assert failing(d) == ["required_match"] and "not read" in d.reasons[0]

    def test_9_a_required_question_the_spec_does_not_know(self, tmp_path):
        d = decide.decide(baseline(tmp_path, required_missing=("3d33a44a", "de771a19")))
        assert failing(d) == ["required_match"]
        assert "3d33a44a, de771a19" in d.reasons[0]

    def test_9_every_marker_known_passes(self, tmp_path):
        d = decide.decide(baseline(tmp_path, required_missing=()))
        assert d.auto_submit


class TestReasonsAreReadable:
    def test_every_failed_check_is_named_in_the_reasons(self, tmp_path):
        d = decide.decide(baseline(tmp_path, mode=decide.DRY_RUN, signals_before=("hcaptcha",),
                                   prior_submit_attempts=2))
        assert [r.split(":")[0] for r in d.reasons] == ["enabled", "no_bot_check", "first_attempt"]
        assert d.outcome == "review"
