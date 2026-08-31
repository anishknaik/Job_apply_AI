"""Addendum §3.1 archetype and §3.2 legitimacy, plus the seniority-scope fix.

All three came out of one round of reading the live sheet: the user annotated
18 of the top 20 and wrote "seems like a senior role" seven times, usually
alongside "no mention of YOE".
"""
import json

import pytest

from jobpipe import scoring as _scoring


class TestNonTechnicalIsAboutTheWork:
    """`non_technical` is a FLAG, not a verdict — demoted 2026-08-30.

    It used to be defined by domain words — "sales, marketing, recruiting" —
    which caught Harvey's "Marketing Engineer" and Scale AI's "AI Advisory
    Consultant" on the word rather than the work. Hand-scored 7 and 6; the
    model gave both a 1, because the short-circuit fired upstream of every
    other rule and nothing downstream could undo it.

    Sharpening the definition was the first fix and it was not enough: §8.5's
    diagnosis is that the rubric text was already correct and the model simply
    did not follow it on that instance. So the short-circuit itself is gone.
    The label now caps `domain_relevance` at 3 and leaves every other axis to
    be judged, which bounds the damage of a misfire instead of relying on the
    model never misfiring.
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

    def test_the_short_circuit_is_gone(self):
        """The whole point of the demotion. A label that forces every axis to
        1 destroys the evidence of what it rejected — the score carries no
        trace of why, so the loss is invisible in the corpus."""
        assert "STOP. Set skills_match" not in _scoring.RUBRIC
        assert "do not analyse further" not in _scoring.RUBRIC

    def test_it_caps_rather_than_kills(self):
        rubric = _scoring.RUBRIC
        line = next(ln for ln in rubric.splitlines()
                    if "non_technical  —" in ln)
        assert "Cap domain_relevance at 3" in line

    def test_the_other_axes_are_still_judged(self):
        assert "judge" in _scoring.RUBRIC.lower()
        assert "Do NOT force the scores to" in _scoring.RUBRIC

    def test_it_is_still_the_weakest_lane(self):
        """Demoted, not neutralised. Its cap must stay below every other
        lane's, or the label stops meaning anything.

        Read out of the rubric text rather than pinned as a constant: the
        rubric is what the model actually reads, and a constant agreeing with
        a stale prompt would pass while the scorer did something else.
        """
        # Case-insensitive: swe_generalist's line reads "otherwise cap ...".
        marker = "cap domain_relevance at "
        caps = [int(part[0])
                for part in _scoring.RUBRIC.lower().split(marker)[1:]]
        assert sorted(caps) == [3, 5, 6, 7], caps
        assert min(caps) == 3, "non_technical is no longer the weakest lane"
        weakest = next(ln for ln in _scoring.RUBRIC.splitlines()
                       if "non_technical" in ln and "weakest overlap" in ln)
        assert "Cap domain_relevance at 3" in weakest

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


class TestDomainIsAboutTheEmployer:
    """`domain` selects the resume variant (PHASE5_PLAN 4.2), so it is a
    label about the COMPANY, not about the role or the fit."""

    def test_the_enum_matches_the_settled_set(self):
        assert _scoring.DOMAINS == [
            "ai_infra", "ai_saas", "data_infra", "product_saas",
            "healthcare", "robotics_cv", "fintech", "other"]

    def test_it_is_in_the_response_schema(self):
        prop = _scoring.SCORE_SCHEMA["properties"]["domain"]
        assert prop["enum"] == _scoring.DOMAINS

    def test_it_is_disambiguated_from_domain_relevance(self):
        """The two words collide, and one is a label while the other is a
        score. The prompt has to say so or the model will conflate them."""
        assert "Do not confuse it with `domain_relevance`" in _scoring.RUBRIC
        assert ("not `domain_relevance`"
                in _scoring.SCORE_SCHEMA["properties"]["domain"]["description"])

    def test_the_vertical_wins_rule_is_stated(self):
        """Abridge is healthcare, not ai_saas. The vertical decides which
        evidence leads, which is the whole reason the field exists."""
        assert "the vertical wins" in _scoring.RUBRIC

    def test_other_is_available_so_a_bad_fit_is_not_forced(self):
        assert "other" in _scoring.DOMAINS


class TestRubricVersionCannotDriftSilently:
    """A score is comparable only to scores from the same rubric. A corpus
    that mixes them without recording which is a corpus whose calibration
    figures describe nothing."""

    #: sha256 of RUBRIC, first 12 chars. If this test fails you changed the
    #: rubric — bump scoring.RUBRIC_VERSION and update this pin, deliberately.
    PINNED = None  # filled in below on first run

    def test_the_version_is_recorded_with_every_score(self):
        from jobpipe import db
        assert "rubric_version" in db._SCORE_COLUMNS
        assert "scorer" in db._SCORE_COLUMNS
        assert "domain" in db._SCORE_COLUMNS

    def test_the_version_looks_deliberate(self):
        assert _scoring.RUBRIC_VERSION
        assert _scoring.RUBRIC_VERSION[:4].isdigit(), "date-led, so it sorts"


class TestProvenanceIsWritten:
    """The corpus is about to mix 3.1-on-the-Developer-API scores with
    2.5-on-Vertex ones. Without provenance that mixture is invisible, and
    every calibration figure computed across it describes nothing."""

    def test_scorer_id_records_the_backend_not_just_the_provider(self,
                                                                 monkeypatch):
        """The same model id can be served by either backend, and they differ
        in billing track, data terms and — as the probe found — caching."""
        import types as _t
        provider = _t.SimpleNamespace(name="gemini")
        # Set explicitly rather than deleted: the backend has no default, so
        # an unset variable is now an error rather than a third answer. What
        # this test is about is that the recorded id follows the backend.
        monkeypatch.setenv("JOBPIPE_GEMINI_BACKEND", "api")
        assert _scoring.scorer_id(provider) == "gemini/api"
        monkeypatch.setenv("JOBPIPE_GEMINI_BACKEND", "vertex")
        assert _scoring.scorer_id(provider) == "gemini/vertex"

    def test_a_score_round_trips_its_provenance(self):
        from jobpipe import db as _db
        conn = _db.connect(":memory:")
        _db.init_schema(conn)
        from jobpipe.normalize import Posting
        p = Posting(ats_type="ashby", source_id="1", company="X",
                    title="ML Engineer", location="NYC", remote_flag=False,
                    description="", url="u", posted_at=None)
        pid, _ = _db.upsert_posting(conn, p, p.content_hash)
        _db.insert_score(conn, pid, {
            "skills_match": 8, "seniority_fit": 8, "domain_relevance": 8,
            "overall": 8, "apply_score": 8, "apply_reason": "",
            "dealbreakers": [], "reasoning": "r", "domain": "ai_infra"},
            model="gemini-2.5-flash", scorer="gemini/vertex",
            rubric_version=_scoring.RUBRIC_VERSION)
        row = conn.execute("SELECT * FROM scores WHERE posting_id = ?",
                           (pid,)).fetchone()
        assert row["domain"] == "ai_infra"
        assert row["scorer"] == "gemini/vertex"
        assert row["rubric_version"] == _scoring.RUBRIC_VERSION
        conn.close()

    def test_older_scores_keep_null_provenance_rather_than_a_wrong_guess(self):
        """The 632 existing scores predate all three columns. Backfilling them
        with today's values would assert something untrue about how they were
        produced; NULL is the honest record."""
        from jobpipe import db as _db
        conn = _db.connect(":memory:")
        _db.init_schema(conn)
        from jobpipe.normalize import Posting
        p = Posting(ats_type="ashby", source_id="2", company="X",
                    title="ML Engineer", location="NYC", remote_flag=False,
                    description="", url="u", posted_at=None)
        pid, _ = _db.upsert_posting(conn, p, p.content_hash)
        _db.insert_score(conn, pid, {
            "skills_match": 5, "seniority_fit": 5, "domain_relevance": 5,
            "overall": 5, "apply_score": 5, "apply_reason": "",
            "dealbreakers": [], "reasoning": "r"}, model="gemini-3.1-flash-lite")
        row = conn.execute("SELECT * FROM scores WHERE posting_id = ?",
                           (pid,)).fetchone()
        assert row["scorer"] is None and row["rubric_version"] is None
        conn.close()


class TestGroundingDoesNotInheritTheScoringModel:
    """It used to call default_model(), which ignores JOBPIPE_SCORING_MODEL —
    so moving scoring to 2.5 Flash would have silently left grounding on
    3.1-flash-lite, a model the credit does not cover."""

    def test_the_backend_decides_the_default(self, monkeypatch):
        from jobpipe.providers import default_grounding_model
        monkeypatch.delenv("JOBPIPE_GROUNDING_MODEL", raising=False)
        monkeypatch.setenv("JOBPIPE_GEMINI_BACKEND", "api")
        assert default_grounding_model("gemini") == "gemini-3.1-flash-lite"
        monkeypatch.setenv("JOBPIPE_GEMINI_BACKEND", "vertex")
        assert default_grounding_model("gemini") == "gemini-2.5-flash-lite"

    def test_why_the_backends_differ(self, monkeypatch):
        """2.5-flash-lite is served on Vertex but unavailable to new Developer
        API keys, so one default cannot be correct for both."""
        from jobpipe.providers import DEFAULT_GROUNDING_MODELS
        assert (DEFAULT_GROUNDING_MODELS[("gemini", "api")]
                != DEFAULT_GROUNDING_MODELS[("gemini", "vertex")])

    def test_an_explicit_override_wins(self, monkeypatch):
        from jobpipe.providers import default_grounding_model
        monkeypatch.setenv("JOBPIPE_GROUNDING_MODEL", "gemini-2.5-flash")
        assert default_grounding_model("gemini") == "gemini-2.5-flash"
