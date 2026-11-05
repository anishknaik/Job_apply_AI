"""Edit-distance tracking. No network.

The point of §7 is "measure what they change". These tests mostly guard the
ways that measurement can quietly become meaningless: comparing against the
wrong baseline, counting cosmetic differences as edits, or losing the
baseline entirely.
"""
import pytest

from jobpipe import db, measure


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def seed_posting(conn, company="Abridge"):
    from jobpipe.normalize import Posting
    p = Posting(ats_type="ashby", source_id="1", company=company,
                title="Software Engineer, GenAI", location="NYC",
                remote_flag=False, description="", url="u1", posted_at=None)
    pid, _ = db.upsert_posting(conn, p, p.content_hash)
    conn.commit()
    return pid


class TestLevenshtein:
    def test_identical_is_zero(self):
        assert measure.levenshtein("abc", "abc") == 0

    def test_empty_against_text(self):
        assert measure.levenshtein("", "abc") == 3
        assert measure.levenshtein("abc", "") == 3

    @pytest.mark.parametrize("a,b,expected", [
        ("kitten", "sitting", 3),
        ("flaw", "lawn", 2),
        ("abc", "abd", 1),
    ])
    def test_known_distances(self, a, b, expected):
        assert measure.levenshtein(a, b) == expected

    def test_it_is_symmetric(self):
        assert measure.levenshtein("hello", "world") == \
            measure.levenshtein("world", "hello")


class TestNormalisation:
    """Cosmetic differences must not read as edits, or every measurement
    carries a floor and the trend §7 wants is invisible."""

    def test_smart_quotes_fold(self):
        assert measure.distance("I'm here", "I’m here") == 0.0

    def test_dash_variants_fold(self):
        assert measure.distance("a — b", "a -- b") == 0.0

    def test_repeated_spaces_fold(self):
        assert measure.distance("a  b", "a b") == 0.0

    def test_a_real_edit_still_registers(self):
        assert measure.distance("the quick brown fox",
                                "the slow brown fox") > 0


class TestDistance:
    def test_unchanged_is_zero(self):
        assert measure.distance("same text", "same text") == 0.0

    def test_it_is_normalised_to_one(self):
        assert measure.distance("abcdef", "zyxwvu") == 1.0

    def test_both_empty_is_zero_not_a_crash(self):
        assert measure.distance("", "") == 0.0


class TestParagraphBreakdown:
    """The aggregate says the draft was close; the per-slot number says which
    instruction is wrong. That is the actionable half."""

    def test_unchanged_paragraphs_are_identified(self):
        text = "Para one.\n\nPara two.\n\nPara three."
        result = measure.compare(text, text)
        assert all(c.verdict == "unchanged" for c in result["paragraphs"])

    def test_one_rewritten_paragraph_is_isolated(self):
        gen = "Para one.\n\nPara two.\n\nPara three."
        sent = "Para one.\n\nCompletely different words here.\n\nPara three."
        result = measure.compare(gen, sent)
        verdicts = [c.verdict for c in result["paragraphs"]]
        assert verdicts[0] == "unchanged"
        assert verdicts[1] != "unchanged"
        assert verdicts[2] == "unchanged"

    def test_a_deleted_paragraph_is_counted(self):
        result = measure.compare("A.\n\nB.\n\nC.", "A.\n\nB.")
        assert len(result["paragraphs"]) == 3
        assert result["paragraphs"][2].sent == ""

    def test_word_counts_are_reported(self):
        result = measure.compare("one two three", "one two")
        assert result["words_generated"] == 3
        assert result["words_sent"] == 2

    @pytest.mark.parametrize("distance,verdict", [
        (0.0, "unchanged"), (0.05, "light"), (0.20, "moderate"),
        (0.60, "rewritten"),
    ])
    def test_verdict_bands(self, distance, verdict):
        change = measure.ParagraphChange(0, distance, "a", "b")
        assert change.verdict == verdict


class TestBaselineSurvival:
    """The generated file is the one the human works on, so it gets renamed,
    overwritten and deleted. The first real edit renamed it and destroyed the
    baseline. Measured: the prose model's generation-to-generation distance is
    0.53 at temperature 0, so a regeneration is NOT a usable substitute —
    the baseline has to be stored, not re-derivable."""

    def test_content_is_stored_on_write(self, conn):
        pid = seed_posting(conn)
        db.insert_document(conn, pid, "cover_letter",
                           generated_path="output/x/cover_letter.md",
                           content="the generated letter")
        conn.commit()
        doc = db.current_document(conn, pid, "cover_letter")
        assert doc["content"] == "the generated letter"

    def test_measuring_uses_stored_content_not_the_file(self, conn, tmp_path):
        pid = seed_posting(conn)
        generated = tmp_path / "cover_letter.md"
        sent = tmp_path / "cover_letter.sent.md"
        sent.write_text("edited version of the letter", encoding="utf-8")
        # The generated file deliberately does NOT exist — renamed away.
        db.insert_document(conn, pid, "cover_letter",
                           generated_path=str(generated),
                           content="original version of the letter")
        conn.commit()

        measured = measure.collect(conn)
        assert len(measured) == 1
        assert 0 < measured[0]["distance"] < 1

    def test_a_lost_baseline_is_skipped_not_guessed(self, conn, tmp_path):
        """No content and no file means the measurement is impossible. It
        must not silently fall back to something else."""
        pid = seed_posting(conn)
        sent = tmp_path / "cover_letter.sent.md"
        sent.write_text("edited", encoding="utf-8")
        db.insert_document(conn, pid, "cover_letter",
                           generated_path=str(tmp_path / "cover_letter.md"))
        conn.commit()
        assert measure.collect(conn) == []

    def test_the_distance_is_written_back(self, conn, tmp_path):
        pid = seed_posting(conn)
        (tmp_path / "cover_letter.sent.md").write_text("edited",
                                                       encoding="utf-8")
        db.insert_document(conn, pid, "cover_letter",
                           generated_path=str(tmp_path / "cover_letter.md"),
                           content="original")
        conn.commit()
        measure.collect(conn)
        doc = db.current_document(conn, pid, "cover_letter")
        assert doc["edit_distance"] is not None
        assert doc["sent_path"].endswith("cover_letter.sent.md")

    def test_resumes_are_never_measured(self, conn, tmp_path):
        """A resume is assembled from the bank and should be sent as
        generated; hand-editing it discards §2.1's traceability."""
        pid = seed_posting(conn)
        (tmp_path / "resume.sent.md").write_text("edited", encoding="utf-8")
        db.insert_document(conn, pid, "resume",
                           generated_path=str(tmp_path / "resume.md"),
                           content="original")
        conn.commit()
        assert measure.collect(conn) == []


class TestOnlyRealPairsAreMeasured:
    """Both of these produced confident, wrong numbers on live data."""

    def _pair(self, conn, tmp_path, folder, generated_at, sent_mtime=None):
        import os
        pid = seed_posting(conn, company=folder)
        directory = tmp_path / folder
        directory.mkdir()
        sent = directory / "cover_letter.sent.md"
        sent.write_text("the edited letter", encoding="utf-8")
        if sent_mtime is not None:
            os.utime(sent, (sent_mtime, sent_mtime))
        db.insert_document(conn, pid, "cover_letter",
                           generated_path=str(directory / "cover_letter.md"),
                           content="the generated letter",
                           generated_at=generated_at)
        conn.commit()
        return pid, sent

    def test_superseded_documents_are_excluded(self, conn, tmp_path):
        """Three superseded rows plus the live one all pointed at the same
        file, so one edited letter was counted four times."""
        pid, _ = self._pair(conn, tmp_path, "acme",
                            "2020-01-01T00:00:00+00:00")
        for _ in range(3):
            db.insert_document(
                conn, pid, "cover_letter", content="another draft",
                generated_path=str(tmp_path / "acme" / "cover_letter.md"),
                generated_at="2020-01-01T00:00:00+00:00")
        conn.commit()
        assert len(measure.collect(conn)) == 1

    def test_a_sent_file_older_than_the_draft_is_refused(self, conn,
                                                         tmp_path):
        """Regenerating leaves a stale .sent.md beside a fresh draft.
        Measuring it reports generation variance as human editing — and prose
        regenerates at ~0.53, so the number would be noise dressed as signal.
        """
        import datetime
        old = datetime.datetime(2020, 1, 1,
                                tzinfo=datetime.timezone.utc).timestamp()
        self._pair(conn, tmp_path, "stale", "2026-08-14T19:07:58+00:00",
                   sent_mtime=old)
        assert measure.collect(conn) == []

    def test_a_sent_file_newer_than_the_draft_is_measured(self, conn,
                                                          tmp_path):
        self._pair(conn, tmp_path, "fresh", "2020-01-01T00:00:00+00:00")
        assert len(measure.collect(conn)) == 1

    def test_a_missing_generated_at_does_not_block_measurement(self, conn,
                                                               tmp_path):
        assert not measure._is_stale(tmp_path, None)


class TestSummary:
    def _entry(self, distance, archetype="ai_ml", slots=(0.0, 0.0),
               letter_required=True, writer="claude-mcp"):
        # Padded to the assembled seven-block shape. summarise() aggregates
        # by_slot only over letters with that shape, so a bare two-slot
        # fixture would be skipped and these assertions would read an empty
        # dict rather than the means they are checking. Letter-required by
        # default, since the headline is over those only.
        slots = tuple(slots) + (0.0,) * (len(measure.SLOT_NAMES) - len(slots))
        return {"distance": distance, "archetype": archetype,
                "letter_required": letter_required, "writer": writer,
                "paragraphs": [measure.ParagraphChange(i, d, "a", "b")
                               for i, d in enumerate(slots)]}

    def test_empty_is_not_a_crash(self):
        assert measure.summarise([])["n"] == 0

    def test_mean_best_worst(self):
        summary = measure.summarise([self._entry(0.1), self._entry(0.3)])
        assert summary["mean"] == pytest.approx(0.2)
        assert summary["best"] == 0.1 and summary["worst"] == 0.3

    def test_per_slot_isolates_the_problem_paragraph(self):
        summary = measure.summarise([
            self._entry(0.2, slots=(0.0, 0.8)),
            self._entry(0.2, slots=(0.0, 0.7)),
        ])
        assert summary["by_slot"][0] == 0.0
        assert summary["by_slot"][1] == pytest.approx(0.75)

    def test_per_variant_is_reported(self):
        summary = measure.summarise([self._entry(0.1, "ai_ml"),
                                     self._entry(0.5, "data_science")])
        assert summary["by_archetype"]["ai_ml"] == 0.1
        assert summary["by_archetype"]["data_science"] == 0.5


class TestLetterRequiredAndWriter:
    """CONTEXT §8.10. advance_status promised that measure counted
    letter-required applications only, and measure never read the flag:
    five letters sent as drafted into optional boxes had pulled the `why
    this role` slot from 0.110 to 0.018."""

    _entry = TestSummary._entry

    def test_the_headline_is_letter_required_only(self):
        s = measure.summarise([
            self._entry(0.10, slots=(0, 0, 0.11)),
            self._entry(0.0, letter_required=False),
            self._entry(0.0, letter_required=False),
            self._entry(0.03, letter_required=None, writer="cowork-handwritten")])
        assert s["n"] == 1 and s["mean"] == pytest.approx(0.10)
        assert s["by_slot"][2] == pytest.approx(0.11), "the optional zeros do not dilute it"
        assert s["measured"] == 4 and s["population"] == "required"

    def test_what_is_left_out_is_counted_beside_it(self):
        s = measure.summarise([self._entry(0.1),
                               self._entry(0.0, letter_required=False),
                               self._entry(0.0, letter_required=False),
                               self._entry(0.04, letter_required=None)])
        assert s["excluded"]["not_required"] == {"n": 2, "mean": 0.0, "zeros": 2}
        assert s["excluded"]["unrecorded"]["n"] == 1
        assert s["excluded"]["unrecorded"]["mean"] == pytest.approx(0.04)

    def test_nothing_required_is_a_headline_of_zero_not_a_crash(self):
        s = measure.summarise([self._entry(0.0, letter_required=False)])
        assert s["n"] == 0 and "mean" not in s
        assert s["excluded"]["not_required"]["n"] == 1
        assert s["by_writer"]["claude-mcp"]["required_n"] == 0

    def test_split_by_writer_over_every_letter(self):
        s = measure.summarise([
            self._entry(0.0, writer="claude-mcp", letter_required=False),
            self._entry(0.2, writer="claude-mcp"),
            self._entry(0.1, writer="gemini-2.5-pro"),
            self._entry(0.03, writer="cowork-handwritten", letter_required=None)])
        w = s["by_writer"]
        assert list(w) == ["claude-mcp", "cowork-handwritten", "gemini-2.5-pro"]
        assert w["claude-mcp"] == {"n": 2, "mean": pytest.approx(0.1),
                                   "required_n": 1, "required_mean": pytest.approx(0.2)}
        assert w["cowork-handwritten"]["required_mean"] is None
        assert w["gemini-2.5-pro"]["required_n"] == 1

    def _letter(self, conn, tmp_path, source_id, model):
        from jobpipe.normalize import Posting
        p = Posting(ats_type="ashby", source_id=source_id, company=f"Co{source_id}",
                    title="Engineer", location="NYC", remote_flag=False,
                    description="", url=f"u{source_id}", posted_at=None)
        pid, _ = db.upsert_posting(conn, p, p.content_hash)
        folder = tmp_path / source_id
        folder.mkdir()
        (folder / "cover_letter.sent.md").write_text("the letter", encoding="utf-8")
        db.insert_document(conn, pid, "cover_letter", model=model,
                           generated_path=str(folder / "cover_letter.md"),
                           content="the letter", generated_at="2020-01-01T00:00:00+00:00")
        conn.commit()
        return pid

    def test_collect_reads_the_flag_and_the_writer(self, conn, tmp_path):
        required = self._letter(conn, tmp_path, "1", "claude-mcp")
        optional = self._letter(conn, tmp_path, "2", "gemini-2.5-pro")
        never = self._letter(conn, tmp_path, "3", "cowork-handwritten")
        db.record_status(conn, required, "applied", letter_required=True)
        db.record_status(conn, optional, "applied", letter_required=False)
        conn.commit()
        got = {m["posting_id"]: m for m in measure.collect(conn)}
        assert got[required]["letter_required"] is True
        assert got[optional]["letter_required"] is False
        assert got[never]["letter_required"] is None
        assert got[optional]["writer"] == "gemini-2.5-pro"

    def test_the_latest_applied_event_that_carries_the_flag_wins(self, conn, tmp_path):
        pid = self._letter(conn, tmp_path, "1", "claude-mcp")
        db.record_status(conn, pid, "applied", letter_required=False)
        db.record_status(conn, pid, "interview")
        db.record_status(conn, pid, "applied", letter_required=True)
        db.record_status(conn, pid, "rejected")
        conn.commit()
        assert measure.letter_required(conn, pid) is True
        other = self._letter(conn, tmp_path, "2", "claude-mcp")
        db.record_status(conn, other, "applied")          # recorded without the flag
        conn.commit()
        assert measure.letter_required(conn, other) is None


class TestSentPath:
    def test_the_convention(self):
        assert measure.sent_path_for("a/b/cover_letter.md").name == \
            "cover_letter.sent.md"

    def test_it_stays_in_the_same_folder(self):
        assert measure.sent_path_for("a/b/cover_letter.md").parent.name == "b"


class TestSlotAlignmentGuard:
    """A letter that is not seven blocks must not skew the per-slot means.

    compare() matches paragraphs by position, so a free-form letter written
    outside letters.assemble() is offset against every assembled one from its
    first extra block onward. Averaging it in reports that offset as editing.
    """

    def _shaped(self, distance, n_slots, slot_value=0.9):
        return {"distance": distance, "archetype": "data_science",
                "letter_required": True, "writer": "cowork-handwritten",
                "paragraphs": [measure.ParagraphChange(i, slot_value, "a", "b")
                               for i in range(n_slots)]}

    def test_a_free_form_letter_is_excluded_from_by_slot(self):
        assembled = {"distance": 0.03, "archetype": "ai_ml", "letter_required": True,
                     "paragraphs": [measure.ParagraphChange(i, 0.1, "a", "b")
                                    for i in range(len(measure.SLOT_NAMES))]}
        summary = measure.summarise([assembled, self._shaped(0.03, 10)])
        assert summary["by_slot"][2] == pytest.approx(0.1)
        assert summary["slot_n"] == 1
        assert summary["slot_skipped"] == 1

    def test_it_still_counts_toward_the_headline(self):
        """Whole-document distance does not care where blank lines fall."""
        summary = measure.summarise([self._shaped(0.02, 10),
                                     self._shaped(0.04, 9)])
        assert summary["n"] == 2
        assert summary["mean"] == pytest.approx(0.03)
        assert summary["by_slot"] == {}
        assert summary["slot_skipped"] == 2

    def test_all_aligned_reports_nothing_skipped(self):
        entry = self._shaped(0.05, len(measure.SLOT_NAMES))
        summary = measure.summarise([entry, entry])
        assert summary["slot_skipped"] == 0
        assert summary["slot_n"] == 2
