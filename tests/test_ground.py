"""`cli ground` — re-check an existing draft and record the verdict.

No network: the provider and the grounding call are both stubbed.

This command exists because a grounding verdict is about a draft AND a
profile, and both move. The 52 documents already in jobpipe.db predate the
grounding columns, so every one carries a NULL verdict that copy_for_editing
correctly refuses; and Phase 5 slice 0 edited master_profile.md, so any
verdict recorded before that describes a comparison against a profile that no
longer exists.
"""
import types

import pytest

from jobpipe import cli, db, grounding
from jobpipe.normalize import Posting


def args(db_path, posting_id, yes=True):
    return types.SimpleNamespace(db=db_path, posting_id=str(posting_id),
                                 yes=yes, provider=None)


@pytest.fixture
def dbfile(tmp_path):
    path = str(tmp_path / "ground.db")
    conn = db.connect(path)
    db.init_schema(conn)
    conn.close()
    return path


def seed(db_path, with_letter=True, content="Dear Team,\n\nA claim.\n"):
    conn = db.connect(db_path)
    p = Posting(ats_type="greenhouse", source_id="1", company="PostHog",
                title="Context Engineer", location="NYC", remote_flag=False,
                description="Build context pipelines.", url="u",
                posted_at=None)
    pid, _ = db.upsert_posting(conn, p, p.content_hash)
    doc = None
    if with_letter:
        doc = db.insert_document(conn, pid, "cover_letter", content=content,
                                 generated_path="x/cover_letter.md",
                                 model="gemini-3.5-flash")
    conn.commit()
    conn.close()
    return pid, doc


@pytest.fixture
def stub(monkeypatch):
    """Stub the provider and the prompt build; the check is set per-test.

    build_prompts is stubbed because it reads master_profile.md, which is
    gitignored — a fresh checkout has no copy and the test must still run.
    """
    from jobpipe import pipeline_tailor as pt
    monkeypatch.setattr(cli, "_build_provider",
                        lambda _p: types.SimpleNamespace(name="gemini"))
    monkeypatch.setattr(pt, "build_prompts", lambda _bank: {"grounding": "sys"})

    def verdict(*supported):
        return grounding.GroundingResult(
            claims=[{"claim": f"c{i}", "supported": s}
                    for i, s in enumerate(supported)],
            model="gemini-2.5-flash-lite")
    return verdict


class TestRefusals:
    def test_unknown_posting(self, dbfile, capsys):
        assert cli.cmd_ground(args(dbfile, 99999)) == 1
        assert "no posting with id" in capsys.readouterr().out

    def test_posting_with_no_letter_says_how_to_make_one(self, dbfile, capsys):
        pid, _ = seed(dbfile, with_letter=False)
        assert cli.cmd_ground(args(dbfile, pid)) == 1
        out = capsys.readouterr().out
        assert "has no cover letter" in out
        assert "tailor --cover-letter" in out

    def test_without_yes_nothing_is_spent_or_written(self, dbfile, stub,
                                                     monkeypatch, capsys):
        pid, doc = seed(dbfile)

        def boom(*a, **k):
            raise AssertionError("grounding.check called without --yes")
        monkeypatch.setattr(grounding, "check", boom)

        assert cli.cmd_ground(args(dbfile, pid, yes=False)) == 1
        assert "Add --yes" in capsys.readouterr().out
        conn = db.connect(dbfile)
        assert conn.execute("SELECT grounding_ok FROM documents WHERE id = ?",
                            (doc,)).fetchone()[0] is None


class TestRecording:
    def test_a_pass_is_recorded_and_releases_the_draft(self, dbfile, stub,
                                                       monkeypatch, capsys):
        pid, doc = seed(dbfile)
        monkeypatch.setattr(grounding, "check",
                            lambda *a, **k: stub("supported", "supported"))

        assert cli.cmd_ground(args(dbfile, pid)) == 0
        conn = db.connect(dbfile)
        row = conn.execute("SELECT * FROM documents WHERE id = ?",
                           (doc,)).fetchone()
        assert row["grounding_ok"] == 1
        assert row["grounding_accuracy"] == 1.0
        assert row["grounding_model"] == "gemini-2.5-flash-lite"
        assert "copy_for_editing will now release" in capsys.readouterr().out

    def test_a_failure_is_recorded_and_exits_nonzero(self, dbfile, stub,
                                                     monkeypatch, capsys):
        """Recorded, not withheld. §2.2 says surface and stop, not delete —
        and a failed verdict is what makes copy_for_editing refuse."""
        pid, doc = seed(dbfile)
        monkeypatch.setattr(grounding, "check",
                            lambda *a, **k: stub("supported", "unsupported"))

        assert cli.cmd_ground(args(dbfile, pid)) == 1
        conn = db.connect(dbfile)
        assert conn.execute("SELECT grounding_ok FROM documents WHERE id = ?",
                            (doc,)).fetchone()[0] == 0
        assert "will refuse it" in capsys.readouterr().out

    def test_it_checks_the_stored_content_not_the_file(self, dbfile, stub,
                                                       monkeypatch):
        """generated_path points nowhere here. The row still has the text, and
        a row cannot be renamed — which is the whole reason content is stored
        rather than pointed at."""
        pid, doc = seed(dbfile, content="the stored draft")
        seen = {}

        def capture(_provider, _bank, letter_text, *a, **k):
            seen["text"] = letter_text
            return stub("supported")
        monkeypatch.setattr(grounding, "check", capture)

        assert cli.cmd_ground(args(dbfile, pid)) == 0
        assert seen["text"] == "the stored draft"

    def test_re_checking_overwrites_the_previous_verdict(self, dbfile, stub,
                                                         monkeypatch):
        """A verdict describes a draft against a profile. When the profile
        changes the old verdict is not merely stale, it is wrong."""
        pid, doc = seed(dbfile)
        monkeypatch.setattr(grounding, "check",
                            lambda *a, **k: stub("unsupported"))
        cli.cmd_ground(args(dbfile, pid))

        monkeypatch.setattr(grounding, "check",
                            lambda *a, **k: stub("supported"))
        assert cli.cmd_ground(args(dbfile, pid)) == 0
        conn = db.connect(dbfile)
        assert conn.execute("SELECT grounding_ok FROM documents WHERE id = ?",
                            (doc,)).fetchone()[0] == 1
