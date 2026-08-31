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
            "archetype", "domain", "engagement", "seniority_gap", "legitimacy",
            "legitimacy_reason", "reasoning", "skills_match", "seniority_fit",
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
        prompt = scoring.build_system_prompt("MY PROFILE TEXT", examples="")
        assert prompt.endswith("MY PROFILE TEXT")
        assert "CANDIDATE MASTER PROFILE" in prompt

    def test_calibrated_examples_go_last_when_present(self):
        """Few-shot anchors on the judgement the rules only approximate. The
        closer they sit to the posting being scored, the more weight they
        carry — so after the profile, not before it."""
        prompt = scoring.build_system_prompt("MY PROFILE TEXT",
                                             examples="EXAMPLE BLOCK")
        assert prompt.index("MY PROFILE TEXT") < prompt.index("EXAMPLE BLOCK")
        assert prompt.rstrip().endswith("EXAMPLE BLOCK")

    def test_absent_examples_degrade_rather_than_break(self, tmp_path):
        """Generated from gitignored CSVs, so a fresh checkout has none.
        Scoring must still run — degraded, not broken."""
        missing = tmp_path / "nope.md"
        assert scoring.load_calibration_examples(missing) == ""
        prompt = scoring.build_system_prompt("P", examples="")
        assert prompt.endswith("P")


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
        base = dict(db=":memory:", rescore=True, yes=True, queue=False,
                    provider=None, model=None, escalation_model=None,
                    targets=None, filters=None, verbose=False)
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


class TestScopeCeilingIsEnforced:
    """The rubric has asked for this since Phase 1 and the model does not do
    it: on 2026-08-31 it scored a "Founding Data Scientist" apply 6 while its
    own reasoning read "the seniority bar is high due to the founding nature
    and strategic scope". Naming the rule and not applying it.

    That is §8.5's finding for the second time — the text is right, the model
    does not follow it. So the model reports which KIND of gap it is, a fact
    it gets right, and the consequence is enforced here. Same shape as
    banned_used() and Bank.select: checked, never instructed.
    """

    def payload(self, gap, apply_score, overall=6):
        return {"reasoning": "r", "skills_match": 8, "seniority_fit": 4,
                "domain_relevance": 9, "dealbreakers": [], "overall": overall,
                "apply_score": apply_score, "apply_reason": "worth a shot",
                "seniority_gap": gap, "archetype": "ai_ml_engineer",
                "domain": "ai_infra", "engagement": "direct",
                "legitimacy": "ok", "legitimacy_reason": ""}

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        """Opt-in since 2026-08-31 — it collapsed 11 of 19 holdout postings
        onto 5 and cost more rank agreement than it bought. The mechanism is
        kept and tested; only the default changed."""
        monkeypatch.setenv("JOBPIPE_SCOPE_CEILING", "1")

    def score(self, row, gap, apply_score):
        provider = FakeProvider([ok(self.payload(gap, apply_score))])
        run = scoring.ScoreRun()
        # overall 6 sits in the escalation band, so queue a second identical
        # reply rather than let the fake run dry.
        provider._results.append(ok(self.payload(gap, apply_score)))
        return scoring.score_posting(provider, row, SYSTEM, run), run

    def test_a_scope_gap_caps_apply_at_five(self, row):
        score, run = self.score(row, "scope", 8)
        assert score["apply_score"] == 5
        assert run.scope_capped == 1

    def test_both_counts_as_scope(self, row):
        """A role with a years bar AND a scope bar is not rescued by the
        years half being negotiable."""
        assert self.score(row, "both", 9)[0]["apply_score"] == 5

    def test_a_years_gap_is_untouched(self, row):
        """Rule 3's canonical shape: right skills, not enough years. Capping
        it would be the costliest mistake this system can make."""
        score, run = self.score(row, "years", 8)
        assert score["apply_score"] == 8
        assert run.scope_capped == 0

    def test_no_gap_is_untouched(self, row):
        assert self.score(row, "none", 9)[0]["apply_score"] == 9

    def test_it_only_ever_lowers(self, row):
        """A scope gap cannot make a posting more attractive."""
        score, run = self.score(row, "scope", 3)
        assert score["apply_score"] == 3
        assert run.scope_capped == 0, "nothing was capped, so nothing to count"

    def test_the_cap_is_recorded_not_silent(self, row):
        """A score nobody can explain later is a score nobody can calibrate
        against."""
        score, _ = self.score(row, "scope", 8)
        assert "scope cap: 8 -> 5" in score["apply_reason"]
        assert "worth a shot" in score["apply_reason"], "original reason kept"

    def test_the_field_is_required_so_it_cannot_be_omitted(self):
        assert "seniority_gap" in scoring.SCORE_SCHEMA["required"]
        assert scoring.SCORE_SCHEMA["properties"]["seniority_gap"]["enum"] \
            == scoring.SENIORITY_GAP

    def test_it_is_off_unless_asked_for(self, row, monkeypatch):
        monkeypatch.delenv("JOBPIPE_SCOPE_CEILING", raising=False)
        score, run = self.score(row, "scope", 8)
        assert score["apply_score"] == 8, "the default must not cap"
        assert run.scope_capped == 0

    def test_the_ceiling_matches_what_the_rubric_promises(self):
        """The rubric says "let apply_score fall to 3-5". The ceiling is the
        top of that range, not a new policy invented in code."""
        assert scoring.SCOPE_GAP_APPLY_CEILING == 5
        assert "fall to 3-5" in scoring.RUBRIC


class TestQueueBackfill:
    """`--queue` exists because neither existing mode fits a rubric change.

    Scoring only the unscored skips every row carrying a stale score, and
    `--rescore` deletes the whole table to reach them - discarding the 3.1-era
    corpus PHASE5_PLAN 3.5 keeps as historical record, and billing for 625
    postings to refresh 67.
    """

    def _cli_args(self, **kw):
        import types
        base = dict(db=":memory:", rescore=False, yes=True, queue=True,
                    provider=None, model=None, escalation_model=None,
                    targets=None, filters=None, verbose=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _seed(self, conn, n):
        from jobpipe.normalize import Posting
        ids = []
        for i in range(n):
            posting = Posting(ats_type="ashby", source_id=f"q{i}",
                              company=f"Co{i}", title="ML Engineer",
                              location="NY", remote_flag=False,
                              description="Build things.", url=f"https://q/{i}",
                              posted_at=None)
            pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
            db.set_stage_a(conn, pid, True, "passed")
            ids.append(pid)
        return ids

    def _score(self, conn, pid, rubric):
        db.insert_score(conn, pid, {"overall": 6, "apply_score": 6},
                        "gemini-3.1-flash-lite", scorer="gemini/api",
                        rubric_version=rubric)
        conn.commit()

    def test_only_queued_postings_are_selected(self, conn):
        a, b, c = self._seed(conn, 3)
        db.record_status(conn, a, "not_applied")
        db.record_status(conn, b, "applied")
        conn.commit()
        got = {r["id"] for r in db.get_queue_off_rubric(conn, "2026-08-31.3")}
        assert got == {a, b}, "a posting with no status is not in the queue"
        assert c not in got

    def test_rows_already_on_the_current_rubric_are_skipped(self, conn):
        a, b = self._seed(conn, 2)
        for pid in (a, b):
            db.record_status(conn, pid, "not_applied")
        self._score(conn, a, "2026-08-31.3")
        self._score(conn, b, "2026-08-30.1")
        conn.commit()
        got = {r["id"] for r in db.get_queue_off_rubric(conn, "2026-08-31.3")}
        assert got == {b}, "re-running must only cost what is left"

    def test_a_null_rubric_version_counts_as_stale(self, conn):
        """The 3.1-era corpus predates the provenance columns entirely."""
        a, = self._seed(conn, 1)
        db.record_status(conn, a, "not_applied")
        db.insert_score(conn, a, {"overall": 6, "apply_score": 6},
                        "gemini-3.1-flash-lite")
        conn.commit()
        assert [r["id"] for r in
                db.get_queue_off_rubric(conn, "2026-08-31.3")] == [a]

    def test_it_refuses_without_yes_and_scores_nothing(self, conn, monkeypatch):
        from jobpipe import cli
        a, = self._seed(conn, 1)
        db.record_status(conn, a, "not_applied")
        conn.commit()
        monkeypatch.setattr(cli.db, "connect", lambda _: conn)
        assert cli.cmd_score(self._cli_args(yes=False)) == 1
        assert conn.execute("SELECT COUNT(*) n FROM scores").fetchone()["n"] == 0

    def test_queue_and_rescore_together_are_refused(self, conn, monkeypatch):
        """One targets the queue and keeps everything else; the other discards
        the whole table. Silently picking either would be wrong."""
        from jobpipe import cli
        a, = self._seed(conn, 1)
        db.record_status(conn, a, "not_applied")
        self._score(conn, a, "2026-08-30.1")
        monkeypatch.setattr(cli.db, "connect", lambda _: conn)
        assert cli.cmd_score(self._cli_args(rescore=True)) == 1
        assert conn.execute("SELECT COUNT(*) n FROM scores").fetchone()["n"] == 1


class TestLocationIsNeverADealbreaker:
    """Third instance of one defect: a rule qualified everywhere except on the
    field the model fills.

    master_profile.md 10 says he relocates anywhere in the US without
    qualification, and the rubric's rule 1 says "relocation the candidate has
    ruled out" - but the `dealbreakers` field description said "mandatory
    relocation", unqualified. Verkada's CV posting came back overall 8 /
    apply 2 on a five-days-onsite line, below the sheet's floor of 4.
    """

    def test_the_field_description_rules_location_out(self):
        desc = scoring.SCORE_SCHEMA["properties"]["dealbreakers"]["description"]
        assert "LOCATION IS NEVER ONE" in desc
        assert "mandatory relocation," not in desc, \
            "the unqualified phrase is what taught the model to cap on onsite"
        for term in ("on-site", "hybrid", "relocat"):
            assert term in desc.lower(), f"{term} must be named as in scope"

    def test_the_rubric_still_keeps_its_qualifier(self):
        """The prose was never wrong; only the field was. It must stay right."""
        assert "relocation the candidate \\nhas ruled out" in scoring.RUBRIC \
            or "relocation the candidate has ruled out" in scoring.RUBRIC.replace("\\n", "")
