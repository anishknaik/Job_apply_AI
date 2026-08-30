"""Addendum §3.1 archetype and §3.2 legitimacy, plus the seniority-scope fix.

All three came out of one round of reading the live sheet: the user annotated
18 of the top 20 and wrote "seems like a senior role" seven times, usually
alongside "no mention of YOE".
"""
import json

import pytest

from jobpipe import scoring as _scoring


class TestNonTechnicalIsAboutTheWork:
    """`non_technical` STOPs scoring dead, so a misfire is unrecoverable.

    It used to be defined by domain words — "sales, marketing, recruiting" —
    which caught Harvey's "Marketing Engineer" and Scale AI's "AI Advisory
    Consultant" on the word rather than the work. Hand-scored 7 and 6; the
    model gave both a 1, because the short-circuit fires upstream of every
    other rule and nothing downstream can undo it.
    """

    def test_the_definition_leads_with_the_work(self):
        rubric = _scoring.RUBRIC
        line = next(ln for ln in rubric.splitlines()
                    if ln.strip().startswith("non_technical"))
        assert "the work itself" in line

    def test_a_domain_word_is_explicitly_insufficient(self):
        assert "never on a domain word in the title" in _scoring.RUBRIC

    def test_both_directions_are_covered(self):
        """A technical title with no technical work must still be caught."""
        rubric = _scoring.RUBRIC
        assert "Marketing Engineer" in rubric      # business word, real work
        assert "Solutions Architect" in rubric     # technical word, no work

    def test_the_stop_is_still_a_stop(self):
        """Loosening the definition must not weaken the short-circuit."""
        assert "STOP" in _scoring.RUBRIC

from jobpipe import db, scoring
from jobpipe.providers import ProviderResponse, ScoringProvider


class FakeProvider(ScoringProvider):
    name = "gemini"
    known_models = ("gemini-3.1-flash-lite",)

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def count_tokens(self, model, text):
        return 6929

    def cache_minimum_tokens(self, model):
        return 4096

    def score(self, *, model, system_prompt, user_text, schema):
        self.calls.append({"model": model, "schema": schema,
                           "system_prompt": system_prompt})
        return self._results.pop(0)


def payload(**overrides):
    base = {"archetype": "ai_ml_engineer", "legitimacy": "ok",
            "legitimacy_reason": "", "reasoning": "r", "skills_match": 8,
            "seniority_fit": 7, "domain_relevance": 9, "dealbreakers": [],
            "overall": 8, "apply_score": 8, "apply_reason": "a"}
    base.update(overrides)
    return ProviderResponse(text=json.dumps(base), input_tokens=10,
                            output_tokens=5)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_schema(c)
    yield c
    c.close()


ROW = {"id": 1, "company": "OpenAI", "title": "Demo Experience Engineer",
       "location": "San Francisco", "remote_flag": 0,
       "posted_at": "2026-08-01T00:00:00+00:00", "description": "d"}


class TestArchetypeSchema:
    def test_archetype_is_required_and_enumerated(self):
        props = scoring.SCORE_SCHEMA["properties"]
        assert props["archetype"]["enum"] == scoring.ARCHETYPES
        assert "archetype" in scoring.SCORE_SCHEMA["required"]

    def test_archetype_is_decided_before_the_scores(self):
        """The rubric branches on it, so it must be filled in first."""
        required = scoring.SCORE_SCHEMA["required"]
        assert required.index("archetype") < required.index("skills_match")
        assert required.index("archetype") < required.index("overall")

    def test_covers_both_primary_lanes_and_the_short_circuit(self):
        for lane in ("ai_ml_engineer", "data_scientist", "non_technical"):
            assert lane in scoring.ARCHETYPES

    def test_rubric_names_every_archetype(self):
        for lane in scoring.ARCHETYPES:
            assert lane in scoring.RUBRIC, lane

    def test_rubric_declares_the_co_primary_lanes(self):
        assert "co-primary" in scoring.RUBRIC


class TestLegitimacySchema:
    def test_enumerated_and_required(self):
        props = scoring.SCORE_SCHEMA["properties"]
        assert props["legitimacy"]["enum"] == scoring.LEGITIMACY
        assert "legitimacy" in scoring.SCORE_SCHEMA["required"]
        assert "legitimacy_reason" in scoring.SCORE_SCHEMA["required"]

    def test_rubric_states_it_never_affects_the_scores(self):
        """§3.2's whole point — a fake perfect-fit role and a real poor-fit
        role need different handling."""
        assert "never affects any score" in scoring.RUBRIC or \
               "never affect" in scoring.RUBRIC.lower()


class TestSeniorityScope:
    def test_rubric_now_weighs_scope_not_only_years(self):
        """The instruction it replaces told the model to IGNORE these."""
        for signal in ("owning a product area", "defining strategy",
                       "representing the company externally"):
            assert signal in scoring.RUBRIC, signal

    def test_rubric_no_longer_tells_the_model_to_ignore_scope(self):
        assert "Do not infer a high bar" not in scoring.RUBRIC

    def test_the_real_miss_is_a_worked_example(self):
        """OpenAI Demo Experience Engineer: scored 9 on stack alone while the
        user read it as senior from the ownership language."""
        assert "Demo Experience Engineer" in scoring.RUBRIC


class TestPersistence:
    def test_all_three_fields_reach_the_database(self, conn):
        provider = FakeProvider([payload(archetype="ml_platform",
                                         legitimacy="suspicious",
                                         legitimacy_reason="boilerplate")])
        score = scoring.score_posting(provider, ROW, "sys", scoring.ScoreRun())

        posting = type("P", (), {})()
        from jobpipe.normalize import Posting
        p = Posting(ats_type="ashby", source_id="1", company="OpenAI",
                    title="T", location="SF", remote_flag=False,
                    description="", url="u", posted_at=None)
        pid, _ = db.upsert_posting(conn, p, p.content_hash)
        db.insert_score(conn, pid, score, model="gemini-3.1-flash-lite")

        row = conn.execute("SELECT * FROM scores WHERE posting_id=?",
                           (pid,)).fetchone()
        assert row["archetype"] == "ml_platform"
        assert row["legitimacy"] == "suspicious"
        assert row["legitimacy_reason"] == "boilerplate"

    def test_a_repost_carries_them_forward(self, conn):
        from jobpipe.normalize import Posting
        first = Posting(ats_type="ashby", source_id="a", company="X",
                        title="ML Engineer", location="NY", remote_flag=False,
                        description="", url="u1", posted_at=None)
        fid, _ = db.upsert_posting(conn, first, first.content_hash)
        db.insert_score(conn, fid, {
            "archetype": "data_scientist", "legitimacy": "likely_ghost",
            "legitimacy_reason": "no team named", "overall": 5,
            "apply_score": 6, "apply_reason": "", "skills_match": 5,
            "seniority_fit": 5, "domain_relevance": 5, "dealbreakers": [],
            "reasoning": "r"}, model="m")

        second = Posting(ats_type="ashby", source_id="b", company="X",
                         title="ML Engineer", location="NY", remote_flag=False,
                         description="", url="u2", posted_at=None)
        sid, _ = db.upsert_posting(conn, second, second.content_hash)
        assert db.copy_score(conn, fid, sid)

        row = conn.execute("SELECT * FROM scores WHERE posting_id=?",
                           (sid,)).fetchone()
        assert row["archetype"] == "data_scientist"
        assert row["legitimacy"] == "likely_ghost"


class TestSheetSurfacing:
    def _row(self, **kw):
        base = {"first_seen_at": "2026-08-13", "company": "A", "title": "T",
                "apply_score": 8, "overall": 6, "reasoning": "",
                "dealbreakers": "[]", "url": "u", "apply_reason": "",
                "archetype": "ai_ml_engineer", "legitimacy": "ok",
                "legitimacy_reason": ""}
        base.update(kw)
        return base

    def test_archetype_is_shown(self):
        from jobpipe import sheets
        assert sheets.build_row(self._row())["archetype"] == "ai_ml_engineer"

    def test_ok_legitimacy_is_left_blank(self):
        """An 'ok' in every row trains you to stop reading the column."""
        from jobpipe import sheets
        assert sheets.build_row(self._row())["legitimacy"] == ""

    def test_a_problem_is_surfaced_with_its_reason(self):
        from jobpipe import sheets
        cell = sheets.build_row(self._row(
            legitimacy="likely_ghost",
            legitimacy_reason="no team named, generic across 9 cities",
        ))["legitimacy"]
        assert cell.startswith("likely_ghost")
        assert "no team named" in cell

    def test_legitimacy_is_not_human_owned(self):
        from jobpipe import sheets
        assert "legitimacy" in sheets.PIPELINE_OWNED
        assert "archetype" in sheets.PIPELINE_OWNED
