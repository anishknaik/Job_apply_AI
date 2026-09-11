"""The sheet-to-database status mirror (addendum 4.3, PHASE5_PLAN §6.2).

The mirror had no tests while it was a closure inside `cli sync`, which is how
it came to revert an advance_status write on the first live use: it compared
each sheet cell with the database's current status, so an untouched cell beat
anything newer. What is pinned here is the rule that replaced it - only a
change on the sheet is an edit - and the sequences around it.
"""
import pytest

from jobpipe import db, status_mirror
from jobpipe.normalize import Posting

URL = "https://x/1"


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def pid(conn):
    posting = Posting(ats_type="ashby", source_id="1", company="LangChain",
                      title="Deployed Engineer", location="NYC",
                      remote_flag=False, description="d", url=URL,
                      posted_at=None)
    posting_id, _ = db.upsert_posting(conn, posting, posting.content_hash)
    conn.commit()
    return posting_id


def status(conn, pid):
    return db.current_statuses(conn).get(pid)


def advance(conn, pid, new):
    """What advance_status does: append from a session, not the sheet."""
    db.record_status(conn, pid, new, status(conn, pid), source="mcp")
    conn.commit()


class TestEditsAreMirrored:
    def test_a_first_reading_is_recorded(self, conn, pid):
        result = status_mirror.mirror(conn, {URL: ""})
        assert status(conn, pid) == "not_applied"
        assert result.changed == 1

    def test_a_changed_cell_is_an_edit(self, conn, pid):
        status_mirror.mirror(conn, {URL: ""})
        result = status_mirror.mirror(conn, {URL: "applied"})
        assert status(conn, pid) == "applied"
        assert result.changed == 1

    def test_an_unchanged_cell_writes_nothing(self, conn, pid):
        status_mirror.mirror(conn, {URL: "applied"})
        events = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()
        status_mirror.mirror(conn, {URL: "applied"})
        assert conn.execute(
            "SELECT COUNT(*) FROM status_events").fetchone()[0] == events[0]

    def test_a_real_backwards_edit_is_still_recorded_and_flagged(self, conn,
                                                                 pid):
        status_mirror.mirror(conn, {URL: "applied"})
        result = status_mirror.mirror(conn, {URL: ""})
        assert status(conn, pid) == "not_applied"
        assert result.backwards

    def test_an_unknown_value_is_reported_not_recorded(self, conn, pid):
        result = status_mirror.mirror(conn, {URL: "definitely not a status"})
        assert result.unknown == ["definitely not a status"]
        assert status(conn, pid) is None


class TestAnUntouchedCellDoesNotRevertTheDatabase:
    def test_the_langchain_sequence(self, conn, pid):
        """Seen on the sheet as not_applied; applied through advance_status;
        synced again with the cell untouched. It must stay applied."""
        status_mirror.mirror(conn, {URL: ""})
        advance(conn, pid, "applied")
        result = status_mirror.mirror(conn, {URL: ""})
        assert status(conn, pid) == "applied"
        assert result.changed == 0
        assert result.behind and "database says applied" in result.behind[0]

    def test_a_blank_first_reading_does_not_unapply(self, conn, pid):
        """Advanced through a session before the row ever reached the sheet:
        its first, blank cell is the resting state, not an edit."""
        advance(conn, pid, "applied")
        status_mirror.mirror(conn, {URL: ""})
        assert status(conn, pid) == "applied"

    def test_a_later_edit_on_the_sheet_still_wins(self, conn, pid):
        status_mirror.mirror(conn, {URL: ""})
        advance(conn, pid, "applied")
        status_mirror.mirror(conn, {URL: ""})
        status_mirror.mirror(conn, {URL: "interview"})
        assert status(conn, pid) == "interview"

    def test_the_reading_is_kept_even_when_it_changes_nothing(self, conn,
                                                              pid):
        """The human catches the sheet up to what the tool recorded - a no-op
        in the log. The reading must still move, or the NEXT tool write
        would be reverted by this now-stale cell."""
        status_mirror.mirror(conn, {URL: ""})
        advance(conn, pid, "applied")
        status_mirror.mirror(conn, {URL: "applied"})   # caught up, no event
        advance(conn, pid, "interview")
        status_mirror.mirror(conn, {URL: "applied"})   # untouched since
        assert status(conn, pid) == "interview"
