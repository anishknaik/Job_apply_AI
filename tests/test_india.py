"""The india tab: a second view over fetched postings. No network.

What is pinned here is that the view is separate - it applies every Stage A
rule except location, and it never touches Stage A's own verdict - and that
the tab's own columns survive the shared sync.
"""
import pytest

from jobpipe import db, filters, india, sheets
from test_sheets import FakeWorksheet


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def rules():
    return filters.load_rules()


def add(conn, n, title="Software Engineer", location="Bengaluru, India",
        company="MongoDB", seen=None, stage_a=0):
    now = db.utcnow()
    conn.execute(
        """INSERT INTO postings (ats_type, source_id, company, title,
               normalized_title, location, remote_flag, description, url,
               posted_at, first_seen_at, last_seen_at, content_hash,
               stage_a_passed, stage_a_reason)
           VALUES ('greenhouse', ?, ?, ?, ?, ?, 0, 'd', ?, NULL, ?, ?, ?, ?,
                   'location_exclude: ''india''')""",
        (str(n), company, title, title.lower(), location, f"https://x/{n}",
         now, seen or now, f"h{n}", stage_a))
    conn.commit()


class TestIsIndia:
    @pytest.mark.parametrize("location", [
        "Bengaluru, India", "Gurugram", "IN-Pune", "Remote - India",
        "Bengaluru - BLR1", "Seattle, WA; IN - Bengaluru",
        "United Kingdom; Spain; India",
    ])
    def test_india_locations(self, location):
        assert india.is_india(location)

    @pytest.mark.parametrize("location", [
        "Indianapolis, IN", "San Francisco, CA", "Remote", "", None,
    ])
    def test_everything_else(self, location):
        assert not india.is_india(location)


class TestSelect:
    def test_keeps_a_role_stage_a_rejected_on_location_alone(self, conn, rules):
        add(conn, 1)
        assert [r["url"] for r in india.select(conn, rules)] == ["https://x/1"]

    def test_the_title_rules_still_apply(self, conn, rules):
        add(conn, 1, title="Senior Software Engineer")
        add(conn, 2, title="Account Executive")
        assert india.select(conn, rules) == []

    def test_a_us_role_is_not_swept_in(self, conn, rules):
        add(conn, 1, location="New York, NY")
        add(conn, 2, location="Remote")
        assert india.select(conn, rules) == []

    def test_a_role_gone_from_its_board_is_left_out(self, conn, rules):
        add(conn, 1, seen=db.utc_days_ago(8))
        assert india.select(conn, rules) == []

    def test_stage_a_verdict_is_not_written(self, conn, rules):
        add(conn, 1)
        india.select(conn, rules)
        row = conn.execute("SELECT stage_a_passed, stage_a_reason "
                           "FROM postings").fetchone()
        assert row["stage_a_passed"] == 0
        assert row["stage_a_reason"] == "location_exclude: 'india'"

    def test_ordered_by_tier(self, conn, rules):
        add(conn, 1, company="MongoDB")
        add(conn, 2, company="Notion")
        rows = india.select(conn, rules,
                            tier_by_company={"MongoDB": 2, "Notion": 1})
        assert [r["company"] for r in rows] == ["Notion", "MongoDB"]


class TestTab:
    def test_the_tab_gets_its_own_header(self, conn, rules):
        add(conn, 1)
        ws = FakeWorksheet()
        sheets.sync(ws, [india.build_row(r) for r in india.select(conn, rules)],
                    columns=india.COLUMNS)
        assert ws.values[0] == india.COLUMNS
        row = dict(zip(ws.values[0], ws.values[1]))
        assert row["location"] == "Bengaluru, India"
        assert row["status"] == "" and row["notes"] == ""

    def test_a_closed_role_keeps_its_row_and_its_last_seen(self, conn, rules):
        add(conn, 1)
        ws = FakeWorksheet()
        sheets.sync(ws, [india.build_row(r) for r in india.select(conn, rules)],
                    columns=india.COLUMNS)
        ws.values[1][india.COLUMNS.index("status")] = "Applied"

        gone = db.utc_days_ago(9)
        conn.execute("UPDATE postings SET last_seen_at = ?", (gone,))
        conn.commit()
        sheets.sync(
            ws, [india.build_row(r) for r in india.select(conn, rules)],
            refresh_by_url=lambda urls: [
                india.build_row(r) for r in india.rows_by_url(conn, urls)],
            columns=india.COLUMNS)
        row = dict(zip(ws.values[0], ws.values[1]))
        assert row["last_seen"] == gone[:10]
        assert row["status"] == "Applied"

    def test_the_main_tab_header_is_unchanged_by_default(self):
        ws = FakeWorksheet()
        sheets.sync(ws, [])
        assert ws.values[0] == sheets.COLUMNS


def score(conn, n, apply_score=7):
    conn.execute(
        "INSERT INTO scores (posting_id, overall, apply_score, reasoning) "
        "SELECT id, 6, ?, 'r' FROM postings WHERE source_id = ?",
        (apply_score, str(n)))
    conn.commit()


class TestScoringLane:
    def test_the_queue_is_the_unscored_india_roles(self, conn, rules):
        add(conn, 1)
        add(conn, 2)
        add(conn, 3, location="New York, NY", stage_a=1)
        score(conn, 2)
        assert [r["source_id"] for r in india.unscored(conn, rules)] == ["1"]

    def test_scored_roles_lead_the_tab(self, conn, rules):
        add(conn, 1)
        add(conn, 2)
        score(conn, 2, apply_score=8)
        rows = india.select(conn, rules)
        assert [r["url"] for r in rows] == ["https://x/2", "https://x/1"]
        assert india.build_row(rows[0])["score"] == 8
        assert india.build_row(rows[1])["score"] == ""

    def test_a_scored_india_role_stays_off_the_main_tab(self, conn):
        add(conn, 1)
        add(conn, 2, location="New York, NY", stage_a=1)
        add(conn, 3, location="United States; Hyderabad, India", stage_a=1)
        for n in (1, 2, 3):
            score(conn, n)
        assert len(db.get_sheet_rows(conn)) == 3
        urls = {r["url"] for r in db.get_sheet_rows(conn,
                                                    exclude=india.lane_only)}
        assert urls == {"https://x/2", "https://x/3"}


class TestClosedRolesAreNotScored:
    def test_the_default_queue_skips_a_role_gone_from_its_board(self, conn):
        add(conn, 1, location="New York, NY", stage_a=1)
        add(conn, 2, location="New York, NY", stage_a=1,
            seen=db.utc_days_ago(8))
        assert [r["source_id"] for r in db.get_unscored(conn)] == ["1"]
        assert len(db.get_unscored(conn, max_stale_days=None)) == 2
