"""Status taxonomy (addendum 4.2) and the transition log (4.3).

The status column is the one cell only the human writes, which makes tolerant
reading the whole job. Someone typing "Interviewing" at 9am means `interview`;
a taxonomy that only accepts canonical spelling drops that row out of every
downstream number without saying so.
"""
import pytest

from jobpipe import db, states


class TestNormalise:
    @pytest.mark.parametrize("raw,expected", [
        ("Applied", "applied"),
        ("applied", "applied"),
        ("  APPLIED  ", "applied"),
        ("Interviewing", "interview"),
        ("phone screen", "interview"),
        ("phone-screen", "interview"),
        ("Phone_Screen", "interview"),
        ("sent", "applied"),
        ("Do not apply", "skip"),
        ("don't apply", "skip"),
        ("withdrawn", "discarded"),
    ])
    def test_resolves_hand_typed_values(self, raw, expected):
        assert states.normalise(raw) == expected

    @pytest.mark.parametrize("blank", ["", "   ", None, "-"])
    def test_an_empty_cell_is_the_resting_state(self, blank):
        """By far the most common value: rows arrive on their own."""
        assert states.normalise(blank) == "not_applied"

    def test_an_unknown_value_raises_rather_than_guessing(self):
        """Silently mapping to the nearest state would corrupt advance rates
        months later, when nobody remembers the cell was a typo."""
        with pytest.raises(states.UnknownStatus) as exc:
            states.normalise("spoke to a recruiter maybe?")
        assert "not a known status" in str(exc.value)

    def test_every_state_resolves_to_itself(self):
        for state in states.load_states():
            assert states.normalise(state.id) == state.id
            assert states.normalise(state.label) == state.id


class TestDocuments:
    """Phase 2 §6 storage. Supersede, never overwrite (§8)."""

    def test_a_document_round_trips(self, conn):
        pid = seed(conn)
        db.insert_document(conn, pid, "resume", archetype="ai_ml_engineer",
                           bullet_ids=["a", "b"], generated_path="/tmp/r.pdf",
                           model="m")
        conn.commit()
        doc = db.current_document(conn, pid, "resume")
        assert doc["archetype"] == "ai_ml_engineer"
        assert doc["bullet_ids"] == '["a", "b"]'
        assert doc["generated_path"] == "/tmp/r.pdf"

    def test_regenerating_supersedes_rather_than_deletes(self, conn):
        """The old document may already have been sent. Destroying the record
        of what an employer read is the `--rescore` mistake in new clothes."""
        pid = seed(conn)
        db.insert_document(conn, pid, "resume", generated_path="/tmp/v1.pdf")
        db.insert_document(conn, pid, "resume", generated_path="/tmp/v2.pdf")
        conn.commit()
        rows = conn.execute(
            "SELECT generated_path, superseded_at FROM documents "
            "ORDER BY id").fetchall()
        assert len(rows) == 2, "the first document must still exist"
        assert rows[0]["superseded_at"] is not None
        assert rows[1]["superseded_at"] is None
        assert db.current_document(conn, pid,
                                   "resume")["generated_path"] == "/tmp/v2.pdf"

    def test_doc_types_supersede_independently(self, conn):
        """Regenerating a resume must not retire the cover letter."""
        pid = seed(conn)
        db.insert_document(conn, pid, "cover_letter", generated_path="/tmp/c")
        db.insert_document(conn, pid, "resume", generated_path="/tmp/r1")
        db.insert_document(conn, pid, "resume", generated_path="/tmp/r2")
        conn.commit()
        assert db.current_document(conn, pid,
                                   "cover_letter")["generated_path"] == "/tmp/c"

    def test_postings_supersede_independently(self, conn):
        a, b = seed(conn, source_id="1"), seed(conn, source_id="2")
        db.insert_document(conn, a, "resume", generated_path="/tmp/a")
        db.insert_document(conn, b, "resume", generated_path="/tmp/b")
        conn.commit()
        assert db.current_document(conn, a, "resume")["generated_path"] \
            == "/tmp/a"

    def test_no_document_returns_none(self, conn):
        assert db.current_document(conn, seed(conn), "resume") is None


class TestPhase2Handshake:
    """Addendum 2 §3: Phase 2 keys off three states as generation triggers."""

    @pytest.mark.parametrize("state", ["queued", "drafted", "applying"])
    def test_the_trigger_states_exist(self, state):
        assert state in {s.id for s in states.load_states()}

    def test_they_sit_between_surfaced_and_applied(self):
        """Order is load-bearing: `is_regression` uses list position, and a
        status moving backwards through these would re-fire generation."""
        rank = states.rank
        assert rank("surfaced") < rank("queued") < rank("drafted") \
            < rank("applying") < rank("applied")

    def test_applying_and_applied_do_not_collide(self):
        """Adjacent, similar, and one triggers generation while the other
        means the work is done."""
        assert states.normalise("Applying") == "applying"
        assert states.normalise("Applied") == "applied"

    @pytest.mark.parametrize("raw,expected", [
        ("Selected", "queued"), ("generate", "queued"),
        ("Draft", "drafted"), ("resume ready", "drafted"),
        ("In progress", "applying"), ("committing", "applying"),
    ])
    def test_aliases_resolve(self, raw, expected):
        assert states.normalise(raw) == expected

    def test_none_are_terminal(self):
        for state in ("queued", "drafted", "applying"):
            assert not states.is_terminal(state)


class TestLifecycle:
    def test_forward_moves_are_not_regressions(self):
        assert not states.is_regression("applied", "interview")
        assert not states.is_regression("not_applied", "applied")

    def test_backward_moves_are_flagged(self):
        assert states.is_regression("interview", "applied")
        assert states.is_regression("offer", "applied")

    def test_leaving_a_terminal_state_is_flagged(self):
        assert states.is_regression("rejected", "interview")

    def test_no_change_is_not_a_regression(self):
        assert not states.is_regression("applied", "applied")

    def test_terminal_states_are_marked(self):
        assert states.is_terminal("hired")
        assert states.is_terminal("skip")
        assert not states.is_terminal("applied")

    def test_dropdown_labels_are_in_lifecycle_order(self):
        labels = states.labels()
        assert labels[0] == "Not applied"
        assert labels.index("Applied") < labels.index("Interview")


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def seed(conn, ats="greenhouse", source_id="1"):
    from jobpipe.normalize import Posting
    p = Posting(ats_type=ats, source_id=source_id, company="X",
                title=f"ML Engineer {source_id}", location="NYC",
                remote_flag=False, description="", url=f"u{source_id}",
                posted_at=None)
    pid, _ = db.upsert_posting(conn, p, p.content_hash)
    return pid


class TestStatusLog:
    def test_records_a_transition(self, conn):
        pid = seed(conn)
        assert db.record_status(conn, pid, "applied", None)
        conn.commit()
        assert db.current_statuses(conn) == {pid: "applied"}

    def test_a_repeated_reading_is_not_stored(self, conn):
        """The sheet is re-read every morning; storing each unchanged reading
        would bury real transitions under thousands of duplicates."""
        pid = seed(conn)
        db.record_status(conn, pid, "applied", None)
        assert not db.record_status(conn, pid, "applied", "applied")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) c FROM status_events").fetchone()["c"]
        assert n == 1

    def test_history_survives_later_transitions(self, conn):
        """The reason this is a log and not a column."""
        pid = seed(conn)
        db.record_status(conn, pid, "applied", None)
        db.record_status(conn, pid, "interview", "applied")
        db.record_status(conn, pid, "rejected", "interview")
        conn.commit()
        seen = [r["status"] for r in conn.execute(
            "SELECT status FROM status_events ORDER BY id")]
        assert seen == ["applied", "interview", "rejected"]
        assert db.current_statuses(conn) == {pid: "rejected"}


class TestAdvanceRates:
    def test_a_rejected_candidate_still_counts_as_having_interviewed(self,
                                                                     conn):
        """A current-status column could never answer this."""
        pid = seed(conn, "greenhouse", "1")
        db.record_status(conn, pid, "applied", None)
        db.record_status(conn, pid, "interview", "applied")
        db.record_status(conn, pid, "rejected", "interview")
        conn.commit()
        row = db.advance_rates(conn, reached="interview")[0]
        assert row["ats_type"] == "greenhouse"
        assert row["applied"] == 1 and row["reached"] == 1

    def test_splits_by_ats(self, conn):
        a = seed(conn, "greenhouse", "1")
        b = seed(conn, "ashby", "2")
        db.record_status(conn, a, "interview", None)
        db.record_status(conn, b, "applied", None)
        conn.commit()
        by_ats = {r["ats_type"]: r for r in db.advance_rates(conn)}
        assert by_ats["greenhouse"]["reached"] == 1
        assert by_ats["ashby"]["reached"] == 0

    def test_untracked_postings_are_excluded(self, conn):
        """Postings nobody has touched must not dilute the denominator."""
        seed(conn, "greenhouse", "1")
        tracked = seed(conn, "greenhouse", "2")
        db.record_status(conn, tracked, "applied", None)
        conn.commit()
        assert db.advance_rates(conn)[0]["surfaced"] == 1


class TestGroundingVerdictIsStoredAgainstItsDraft:
    """PHASE5_PLAN §5.2 gates `copy_for_editing` on a passing grounding check.

    The verdict used to be computed and discarded — `generate_cover_letter`
    returned it in a dict and nothing wrote it down — so there was nothing for
    a gate to read. Storing it on the document row rather than in a table of
    its own is what makes the gate fail closed by construction: a regenerated
    letter is a NEW row via insert_document's supersede, so it starts with a
    NULL verdict and cannot inherit its predecessor's pass. Nobody has to
    remember to invalidate anything.
    """

    @staticmethod
    def _verdict(*supported):
        from jobpipe.grounding import GroundingResult
        return GroundingResult(
            claims=[{"claim": f"c{i}", "supported": s}
                    for i, s in enumerate(supported)],
            model="gemini-2.5-flash-lite")

    def test_a_fresh_draft_has_no_verdict(self, conn):
        pid = seed(conn)
        doc = db.insert_document(conn, pid, "cover_letter", content="draft")
        row = conn.execute("SELECT * FROM documents WHERE id = ?",
                           (doc,)).fetchone()
        assert row["grounding_ok"] is None, "unchecked must not read as passed"
        assert row["grounding_checked_at"] is None

    def test_a_pass_is_recorded_with_its_checker(self, conn):
        pid = seed(conn)
        doc = db.insert_document(conn, pid, "cover_letter", content="draft")
        db.record_grounding(conn, doc, self._verdict("supported", "supported"))
        row = conn.execute("SELECT * FROM documents WHERE id = ?",
                           (doc,)).fetchone()
        assert row["grounding_ok"] == 1
        assert row["grounding_accuracy"] == 1.0
        # §5.1's independence claim is only worth something if it is recorded.
        assert row["grounding_model"] == "gemini-2.5-flash-lite"
        assert row["grounding_checked_at"]

    def test_a_partial_claim_fails_the_draft(self, conn):
        pid = seed(conn)
        doc = db.insert_document(conn, pid, "cover_letter", content="draft")
        db.record_grounding(conn, doc, self._verdict("supported", "partial"))
        row = conn.execute("SELECT * FROM documents WHERE id = ?",
                           (doc,)).fetchone()
        assert row["grounding_ok"] == 0
        assert row["grounding_accuracy"] == 0.5

    def test_the_claims_survive_for_inspection(self, conn):
        """A bare flag makes a failure unarguable after the fact."""
        import json
        pid = seed(conn)
        doc = db.insert_document(conn, pid, "cover_letter", content="draft")
        db.record_grounding(conn, doc, self._verdict("unsupported"))
        row = conn.execute("SELECT * FROM documents WHERE id = ?",
                           (doc,)).fetchone()
        assert json.loads(row["grounding_claims"])[0]["supported"] \
            == "unsupported"

    def test_a_regenerated_draft_does_not_inherit_the_pass(self, conn):
        """The property the whole gate rests on."""
        pid = seed(conn)
        first = db.insert_document(conn, pid, "cover_letter", content="v1")
        db.record_grounding(conn, first, self._verdict("supported"))

        second = db.insert_document(conn, pid, "cover_letter", content="v2")
        live = db.current_document(conn, pid, "cover_letter")
        assert live["id"] == second
        assert live["grounding_ok"] is None, \
            "a fresh draft inherited the previous draft's pass"
        # The superseded row keeps its verdict — nothing is ever destroyed.
        old = conn.execute("SELECT * FROM documents WHERE id = ?",
                           (first,)).fetchone()
        assert old["grounding_ok"] == 1


class TestRevisionIndex:
    """§5.4 wants two distances: first save -> final save, and final save ->
    .sent.md. One monotonic index per posting and doc_type answers both."""

    def test_it_counts_up_from_one(self, conn):
        pid = seed(conn)
        ids = [db.insert_document(conn, pid, "cover_letter", content=f"v{i}")
               for i in range(3)]
        got = [conn.execute("SELECT revision FROM documents WHERE id = ?",
                            (i,)).fetchone()[0] for i in ids]
        assert got == [1, 2, 3]

    def test_doc_types_are_numbered_independently(self, conn):
        pid = seed(conn)
        db.insert_document(conn, pid, "cover_letter", content="a")
        resume = db.insert_document(conn, pid, "resume", generated_path="r")
        assert conn.execute("SELECT revision FROM documents WHERE id = ?",
                            (resume,)).fetchone()[0] == 1

    def test_it_does_not_restart_over_rows_that_predate_the_column(self, conn):
        """The 52 documents already in jobpipe.db have revision NULL. MAX()
        alone returns NULL there, so the next draft would restart at 1 and
        collide with a posting's own history."""
        pid = seed(conn)
        for content in ("legacy1", "legacy2"):
            db.insert_document(conn, pid, "cover_letter", content=content)
        conn.execute("UPDATE documents SET revision = NULL WHERE posting_id = ?",
                     (pid,))
        conn.commit()
        fresh = db.insert_document(conn, pid, "cover_letter", content="new")
        assert conn.execute("SELECT revision FROM documents WHERE id = ?",
                            (fresh,)).fetchone()[0] == 3
