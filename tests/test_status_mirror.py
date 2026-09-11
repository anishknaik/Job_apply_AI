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
        assert result.behind and result.behind[0].database == "applied"
        assert "database says applied" in str(result.behind[0])

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


class FakeWorksheet:
    """A url and a status column - all set_cells needs to find and write."""

    def __init__(self, status=""):
        self.values = [["url", "status"], [URL, status]]

    def row_values(self, n):
        return list(self.values[n - 1])

    def get_all_values(self):
        return [list(r) for r in self.values]

    def batch_update(self, updates):
        for u in updates:
            cell = u["range"]
            column, row = ord(cell[0]) - 64, int(cell[1:])
            self.values[row - 1][column - 1] = u["values"][0][0]

    @property
    def cell(self):
        return self.values[1][1]


class TestWriteBack:
    """Anish's decision (2026-09-10): sync writes the status column, but only
    to bring a cell nobody edited up to a status recorded through a tool."""

    def _behind(self, conn, pid, ws):
        status_mirror.mirror(conn, {URL: ws.cell})
        advance(conn, pid, "applied")
        return status_mirror.mirror(conn, {URL: ws.cell}).behind

    def test_an_untouched_cell_gets_the_databases_status(self, conn, pid):
        ws = FakeWorksheet("")
        written, skipped = status_mirror.write_back(
            conn, ws, self._behind(conn, pid, ws))
        assert (written, skipped) == (1, [])
        assert ws.cell == "Applied", "the dropdown's label, not the id"

    def test_the_written_cell_reads_as_unchanged_next_sync(self, conn, pid):
        """Otherwise the next sync would take its own write for an edit."""
        ws = FakeWorksheet("")
        status_mirror.write_back(conn, ws, self._behind(conn, pid, ws))
        events = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()
        result = status_mirror.mirror(conn, {URL: ws.cell})
        assert result.changed == 0 and result.behind == []
        assert conn.execute(
            "SELECT COUNT(*) FROM status_events").fetchone()[0] == events[0]

    def test_a_cell_edited_mid_sync_keeps_the_edit(self, conn, pid):
        """Read blank, then Anish types Interview before the write lands."""
        ws = FakeWorksheet("")
        behind = self._behind(conn, pid, ws)
        ws.values[1][1] = "Interview"
        written, skipped = status_mirror.write_back(conn, ws, behind)
        assert (written, len(skipped)) == (0, 1)
        assert ws.cell == "Interview"
        status_mirror.mirror(conn, {URL: ws.cell})
        assert status(conn, pid) == "interview", "his edit wins next run"

    def test_an_edited_cell_is_never_written(self, conn, pid):
        """A changed cell is an edit and flows INTO the database; there is
        nothing behind to write back."""
        ws = FakeWorksheet("")
        status_mirror.mirror(conn, {URL: ""})
        advance(conn, pid, "applied")
        ws.values[1][1] = "Responded"
        result = status_mirror.mirror(conn, {URL: ws.cell})
        assert result.behind == []
        assert status(conn, pid) == "responded"
