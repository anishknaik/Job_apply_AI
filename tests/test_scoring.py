"""Stage B tests. No network, no spend — the provider is faked throughout."""
import json

import pytest

from jobpipe import db, scoring
from jobpipe.providers import ProviderResponse, ScoringProvider


def score_payload(overall=8):
    return {"reasoning": "Strong overlap on the core stack.",
            "skills_match": 8, "seniority_fit": 7, "domain_relevance": 9,
            "dealbreakers": [], "overall": overall}


class FakeProvider(ScoringProvider):
    """Records calls and replays queued results."""

    name = "gemini"
    known_models = ("gemini-3.1-flash-lite",)

    def __init__(self, results, prefix_tokens=6290, floor=4096):
        self._results = list(results)
        self._prefix_tokens = prefix_tokens
        self._floor = floor
        self.calls = []

    def count_tokens(self, model, text):
        return self._prefix_tokens

    def cache_minimum_tokens(self, model):
        return self._floor

    def score(self, *, model, system_prompt, user_text, schema):
        self.calls.append({"model": model, "system_prompt": system_prompt,
                           "user_text": user_text, "schema": schema})
        if not self._results:
            raise AssertionError("more calls than queued results")
        nxt = self._results.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def ok(payload, **kw):
    return ProviderResponse(text=json.dumps(payload), input_tokens=100,
                            output_tokens=50, **kw)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def row():
    return {
        "id": 1, "company": "Anthropic", "title": "Software Engineer",
        "location": "New York, NY", "remote_flag": 0,
        "posted_at": "2026-08-01T00:00:00+00:00",
        "description": "Build things with Python.",
    }


SYSTEM = "rubric+profile"


class TestRequestShape:
    def test_passes_the_schema_through(self, row):
        provider = FakeProvider([ok(score_payload(9))])
        scoring.score_posting(provider, row, SYSTEM, scoring.ScoreRun())
        schema = provider.calls[0]["schema"]
        assert set(schema["required"]) == {
            "archetype", "engagement", "legitimacy", "legitimacy_reason",
            "reasoning", "skills_match", "seniority_fit",
            "domain_relevance", "dealbreakers", "overall",
            "apply_score", "apply_reason"}

    def test_system_prompt_is_a_plain_stable_string(self, row):
        """Provider-shaped blocks are the provider's business, not scoring's."""
        provider = FakeProvider([ok(score_payload(9))])
        scoring.score_posting(provider, row, SYSTEM, scoring.ScoreRun())
        assert isinstance(provider.calls[0]["system_prompt"], str)

    def test_the_prefix_is_identical_across_calls(self, row):
        """Byte-stability is what makes caching fire on either vendor."""
        provider = FakeProvider([ok(score_payload(9)), ok(score_payload(2))])
        run = scoring.ScoreRun()
        scoring.score_posting(provider, row, SYSTEM, run)
        scoring.score_posting(provider, dict(row, id=2), SYSTEM, run)
        assert provider.calls[0]["system_prompt"] == provider.calls[1]["system_prompt"]

    def test_reasoning_is_required_before_the_number(self):
        required = scoring.SCORE_SCHEMA["required"]
        assert required.index("reasoning") < required.index("overall")

    def test_apply_score_is_a_separate_axis(self):
        """Fit and worth-applying answer different questions (addendum 3.2's
        principle: keep separate axes separate)."""
        props = scoring.SCORE_SCHEMA["properties"]
        assert "apply_score" in props and "overall" in props
        assert "apply_reason" in props

    def test_build_system_prompt_puts_the_profile_after_the_rubric(self):
        prompt = scoring.build_system_prompt("MY PROFILE TEXT")
        assert prompt.endswith("MY PROFILE TEXT")
        assert "CANDIDATE MASTER PROFILE" in prompt


class TestLanguageDealbreaker:
    """A language requirement is a dealbreaker only under a *requirements*
    heading.

    Sierra posts both shapes: "Software Engineer, Agent (Brazilian Portuguese
    speaking)" lists fluency under WHAT YOU'LL BRING, while "Agent - Travel &
    Hospitality" lists Spanish under EVEN BETTER. Hand-scored 1 and 9
    respectively, so a blanket language filter would be as wrong as none.
    """

    def test_the_rubric_names_language_as_a_hard_dealbreaker(self):
        assert "fluency in a language the candidate does not" \
            in scoring.SCORE_SCHEMA["properties"]["dealbreakers"]["description"]

    def test_the_rubric_distinguishes_required_from_preferred(self):
        rubric = scoring.RUBRIC.lower()
        for heading in ("what you'll bring", "requirements", "qualifications"):
            assert heading in rubric, f"{heading!r} missing from the rubric"
        for heading in ("even better", "nice to have", "preferred"):
            assert heading in rubric, f"{heading!r} missing from the rubric"

    def test_a_preference_is_explicitly_not_a_dealbreaker(self):
        """Without this the rule over-fires and costs a top-20 slot."""
        assert "do not treat it as a" in scoring.RUBRIC.lower()


class TestUnstatedSeniorityBar:
    """An absent years requirement must not buy a 9-10 on seniority_fit.

    The band table used to read "no bar stated, or the bar is at or below the
    candidate's experience -> 9-10", which contradicted the scope paragraph
    below it. The table won: four Cohere roles that state no years anywhere
    scored 9-10 on seniority, and the human's own note on each was "seems like
    a senior role".
    """

    def test_the_top_band_no_longer_rewards_a_missing_bar(self):
        rubric = scoring.RUBRIC
        top_band = rubric.split("9-10", 1)[1].split("6-8", 1)[0]
        assert "no bar stated" not in top_band

    def test_a_missing_bar_routes_to_scope(self):
        assert "has NOT thereby earned" in scoring.RUBRIC

    def test_the_scope_signals_are_enumerated(self):
        for signal in ("end to end", "strategy", "mentoring",
                       "externally", "first hire"):
            assert signal in scoring.RUBRIC, f"scope signal {signal!r} missing"

    def test_a_worked_example_anchors_the_unstated_bar_case(self):
        """Prose lost to the band table once; an anchor is what fixed it."""
        assert "names no years requirement anywhere" in scoring.RUBRIC


class TestRule3IsConditional:
    """Rule 3's floor must not rescue a role whose seniority gap is scope.

    Measured on 22 hand-scored postings: apply_score spanned 6-10 while the
    human's spanned 2-10, bias +0.68. The floor was firing unconditionally
    because the carve-out sat in a paragraph *after* the numbered rule, the
    same placement bug that let the band table beat the scope prose.
    """

    def test_the_carve_out_lives_inside_the_rule(self):
        rubric = scoring.RUBRIC
        rule3 = rubric.split("  3. ", 1)[1].split("apply_score is usually", 1)[0]
        assert "rule 3 does not apply" in rule3.lower()
        assert "scope" in rule3.lower()

    def test_no_floor_is_stated_explicitly(self):
        assert "no floor at all" in scoring.RUBRIC

    def test_the_rubric_says_low_scores_are_expected(self):
        """Without this the model treats 6 as the polite minimum."""
        assert "not a failure of nerve" in scoring.RUBRIC

    def test_apply_can_fall_below_overall_for_scope(self):
        assert "scope rather than years" in scoring.RUBRIC


class TestEngagement:
    """Consulting is an arrangement, not a lane, and not a wrong function.

    The candidate is on a consulting firm's roster between client placements
    and has delivered into client environments including a federal one. Rule 2
    used to cap these at 3, which is why Scale AI's AI Advisory Consultant
    scored 1 against his own 6.
    """

    def test_engagement_is_its_own_axis(self):
        """Folding it into ARCHETYPES would force a choice between recording
        the lane and recording the arrangement."""
        assert "consulting" not in scoring.ARCHETYPES
        assert scoring.ENGAGEMENT == ["direct", "consulting"]
        assert "engagement" in scoring.SCORE_SCHEMA["properties"]

    def test_engagement_is_decided_with_the_archetype(self):
        required = scoring.SCORE_SCHEMA["required"]
        assert required.index("engagement") < required.index("overall")

    def test_rule_2_no_longer_swallows_consulting(self):
        assert "Consulting is NOT a wrong function" in scoring.RUBRIC

    def test_the_discount_has_a_floor(self):
        """A preference must not act as a barrier."""
        assert "never take apply_score below 5" in scoring.RUBRIC.lower()

    def test_engagement_turns_on_what_the_employer_sells(self):
        """Not on customer contact. LangChain's Deployed Engineer means you
        work FOR LangChain on LangChain's product; it was being tagged
        consulting and taking a discount on roles hand-scored 9 and 10."""
        assert "what the employer sells" in scoring.RUBRIC
        assert "Customer-facing is NOT consulting" in scoring.RUBRIC

    def test_forward_deployed_titles_are_named_as_direct(self):
        rubric = scoring.RUBRIC
        for title in ("Deployed Engineer", "Applied AI Engineer",
                      "Forward Deployed Engineer"):
            assert f"\"{title}\"" in rubric or title in rubric

    def test_the_discount_is_scoped_to_agency_and_staffing(self):
        assert "takes no discount at all" in scoring.RUBRIC


class TestCacheFloor:
    def test_refuses_a_prefix_below_the_floor(self):
        provider = FakeProvider([], prefix_tokens=3000, floor=4096)
        with pytest.raises(scoring.ProfileTooShort) as exc:
            scoring.check_cacheable(provider, SYSTEM, "gemini-3.1-flash-lite")
        assert "4096" in str(exc.value)
        assert "words" in str(exc.value), "should say how much to add"

    def test_accepts_a_prefix_above_the_floor(self):
        provider = FakeProvider([], prefix_tokens=6290, floor=4096)
        assert scoring.check_cacheable(
            provider, SYSTEM, "gemini-3.1-flash-lite") == 6290


class TestEscalation:
    def test_borderline_scores_escalate(self, row):
        provider = FakeProvider([ok(score_payload(6)), ok(score_payload(7))])
        run = scoring.ScoreRun()
        score = scoring.score_posting(provider, row, SYSTEM, run,
                                      model="gemini-3.1-flash-lite",
                                      escalation_model="gemini-3.5-flash-lite")
        assert len(provider.calls) == 2
        assert provider.calls[1]["model"] == "gemini-3.5-flash-lite"
        assert score["model"] == "gemini-3.5-flash-lite"
        assert run.escalated == 1

    def test_confident_scores_do_not_escalate(self, row):
        for overall in (2, 9):
            provider = FakeProvider([ok(score_payload(overall))])
            run = scoring.ScoreRun()
            scoring.score_posting(provider, row, SYSTEM, run)
            assert len(provider.calls) == 1, f"overall={overall} escalated"
            assert run.escalated == 0

    def test_a_failed_escalation_keeps_the_first_score(self, row):
        provider = FakeProvider([ok(score_payload(6)),
                                 RuntimeError("overloaded"),
                                 RuntimeError("overloaded")])
        run = scoring.ScoreRun()
        score = scoring.score_posting(provider, row, SYSTEM, run,
                                      model="gemini-3.1-flash-lite")
        assert score["overall"] == 6
        assert score["model"] == "gemini-3.1-flash-lite"


class TestFailureHandling:
    def test_retries_bad_json_once_then_gives_up(self, row):
        provider = FakeProvider([ProviderResponse(text="not json"),
                                 ProviderResponse(text="not json")])
        run = scoring.ScoreRun()
        assert scoring.score_posting(provider, row, SYSTEM, run) is None
        assert len(provider.calls) == 2
        assert run.failed == 1

    def test_recovers_when_the_retry_succeeds(self, row):
        provider = FakeProvider([ProviderResponse(text="{oops"),
                                 ok(score_payload(9))])
        run = scoring.ScoreRun()
        assert scoring.score_posting(provider, row, SYSTEM, run)["overall"] == 9
        assert run.failed == 0

    def test_a_refusal_is_recorded_not_raised(self, row):
        provider = FakeProvider([ProviderResponse(
            text=None, refused=True, refusal_reason="policy")])
        run = scoring.ScoreRun()
        assert scoring.score_posting(provider, row, SYSTEM, run) is None
        assert run.failed == 1

    def test_one_bad_posting_never_aborts_the_run(self, row):
        provider = FakeProvider([RuntimeError("boom"), RuntimeError("boom")])
        run = scoring.ScoreRun()
        scoring.score_posting(provider, row, SYSTEM, run)
        assert run.failed == 1 and run.scored == 0


class TestTokenAccounting:
    def test_accumulates_usage_including_cached_tokens(self, row):
        provider = FakeProvider([ok(score_payload(9), cached_tokens=5000)])
        run = scoring.ScoreRun()
        scoring.score_posting(provider, row, SYSTEM, run)
        assert run.input_tokens == 100
        assert run.output_tokens == 50
        assert run.cache_read_tokens == 5000


class TestRescoreSafety:
    """--rescore must never leave the database empty.

    It once deleted the scores and *then* hit the cap, so the guardrail
    produced exactly the outage it exists to prevent.
    """

    def _cli_args(self, **kw):
        import types
        base = dict(db=":memory:", rescore=True, yes=True, provider=None,
                    model=None, escalation_model=None, targets=None,
                    filters=None, verbose=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _seed(self, conn, n):
        from jobpipe.normalize import Posting
        for i in range(n):
            p = Posting(ats_type="ashby", source_id=str(i), company="X",
                        title=f"ML Engineer {i}", location="New York",
                        remote_flag=False, description="", url=f"u{i}",
                        posted_at=None)
            pid, _ = db.upsert_posting(conn, p, p.content_hash)
            db.set_stage_a(conn, pid, True, "passed")
            db.insert_score(conn, pid, {
                "overall": 7, "apply_score": 7, "skills_match": 7,
                "seniority_fit": 7, "domain_relevance": 7, "dealbreakers": [],
                "reasoning": "r", "apply_reason": ""}, model="m")
        conn.commit()

    def test_scores_survive_when_the_cap_refuses(self, conn, monkeypatch):
        from jobpipe import cli
        self._seed(conn, 5)
        monkeypatch.setenv("JOBPIPE_MAX_SCORES_PER_RUN", "3")
        monkeypatch.setattr(db, "connect", lambda *a, **k: conn)

        assert cli.cmd_score(self._cli_args()) == 1
        remaining = conn.execute("SELECT COUNT(*) n FROM scores").fetchone()["n"]
        assert remaining == 5, "refusing the run must not delete the scores"

    def test_requires_yes_before_discarding(self, conn, monkeypatch):
        from jobpipe import cli
        self._seed(conn, 2)
        monkeypatch.setenv("JOBPIPE_MAX_SCORES_PER_RUN", "200")
        monkeypatch.setattr(db, "connect", lambda *a, **k: conn)

        assert cli.cmd_score(self._cli_args(yes=False)) == 1
        assert conn.execute(
            "SELECT COUNT(*) n FROM scores").fetchone()["n"] == 2


class TestScoringGate:
    def test_disabled_by_default(self, conn):
        from jobpipe import pipeline
        assert not pipeline.check_scoring_gate(conn, cap=200,
                                               enabled=False).allowed

    def test_refuses_a_queue_above_the_cap(self, conn):
        from jobpipe import pipeline
        from jobpipe.normalize import Posting
        for i in range(5):
            posting = Posting(ats_type="ashby", source_id=str(i), company="X",
                              title=f"Engineer {i}", location="New York",
                              remote_flag=False, description="", url="u",
                              posted_at=None)
            pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
            db.set_stage_a(conn, pid, True, "passed")
        gate = pipeline.check_scoring_gate(conn, cap=3, enabled=True)
        assert not gate.allowed and gate.queued == 5
