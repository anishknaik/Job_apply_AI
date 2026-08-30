"""Google Sheets sync. No network — the worksheet is faked.

The load-bearing test is that a human-entered `status` survives a sync. The
sheet is the only human interface in Phase 1, and a full-sheet rewrite is one
bug away from erasing a week of hand-tracking.
"""
import pytest

from jobpipe import sheets


class FakeWorksheet:
    """Minimal stand-in for a gspread worksheet."""

    def __init__(self, values=None):
        self.values = [list(r) for r in (values or [])]
        self.appended = []
        self.sorted_by = None

    def row_values(self, n):
        return list(self.values[n - 1]) if len(self.values) >= n else []

    def get_all_values(self):
        return [list(r) for r in self.values]

    def update(self, values, rng):
        while len(self.values) < 1:
            self.values.append([])
        self.values[0] = list(values[0])

    def batch_update(self, updates):
        for u in updates:
            cell = u["range"]
            col = 0
            i = 0
            while i < len(cell) and cell[i].isalpha():
                col = col * 26 + (ord(cell[i].upper()) - 64)
                i += 1
            row = int(cell[i:])
            while len(self.values) < row:
                self.values.append([""] * len(self.values[0]))
            line = self.values[row - 1]
            while len(line) < col:
                line.append("")
            line[col - 1] = u["values"][0][0]

    def append_rows(self, rows, value_input_option=None):
        self.appended.extend(rows)
        self.values.extend([list(r) for r in rows])

    def sort(self, *specs, range=None):
        self.sorted_by = (specs, range)

    @property
    def frozen_row_count(self):
        return getattr(self, "_frozen", 0)

    def freeze(self, rows=0):
        self._frozen = rows

    def col_values(self, n):
        return [r[n - 1] if len(r) >= n else "" for r in self.values]


def header_row():
    return list(sheets.COLUMNS)


def posting_row(url="https://x/1", company="Anthropic", score=8, fit=6):
    return {
        "date_found": "2026-08-13", "company": company, "role": "ML Engineer",
        "score": score, "reasoning": "good overlap", "dealbreakers": "",
        "url": url, "fit": fit, "apply_reason": "skills match",
    }


class TestEngagementMarker:
    """The consulting marker rides in the archetype column, per request."""

    def test_consulting_is_marked(self):
        row = sheets.build_row({"archetype": "ai_ml_engineer",
                                "engagement": "consulting"})
        assert row["archetype"] == "ai_ml_engineer (consulting)"

    def test_direct_roles_are_not_annotated(self):
        """Annotating every row would make the marker invisible."""
        row = sheets.build_row({"archetype": "ai_ml_engineer",
                                "engagement": "direct"})
        assert row["archetype"] == "ai_ml_engineer"

    def test_a_missing_engagement_does_not_break_the_row(self):
        """Scores written before the column existed."""
        assert sheets.build_row({"archetype": "analytics"})["archetype"] \
            == "analytics"


class TestTierColouring:
    """Rows shaded by their company's tier: 1 blue, 2 yellow, 3 red."""

    class _WS(FakeWorksheet):
        id = 0

        def __init__(self, values):
            super().__init__(values)
            self.spreadsheet = self
            self.requests = []

        def batch_update(self, body):
            # gspread's worksheet.batch_update takes a list; the spreadsheet
            # one takes {"requests": [...]}. Only the latter reaches here.
            if isinstance(body, dict):
                self.requests.extend(body["requests"])
            else:
                super().batch_update(body)

    def _sheet(self, companies):
        header = list(sheets.COLUMNS)
        rows = [header]
        at = header.index("company")
        for name in companies:
            row = [""] * len(header)
            row[at] = name
            rows.append(row)
        return self._WS(rows)

    def _colour_of(self, request):
        return request["repeatCell"]["cell"]["userEnteredFormat"][
            "backgroundColor"]

    def test_each_tier_gets_its_colour(self):
        ws = self._sheet(["A", "B", "C"])
        n = sheets.color_by_tier(ws, {"A": 1, "B": 2, "C": 3})
        assert n == 3
        colours = [self._colour_of(r) for r in ws.requests]
        assert colours == [sheets.TIER_COLORS[1], sheets.TIER_COLORS[2],
                           sheets.TIER_COLORS[3]]

    def test_the_header_is_never_shaded(self):
        ws = self._sheet(["A"])
        sheets.color_by_tier(ws, {"A": 1})
        assert all(r["repeatCell"]["range"]["startRowIndex"] >= 1
                   for r in ws.requests)

    def test_contiguous_runs_collapse_into_one_request(self):
        ws = self._sheet(["A", "B", "C", "D"])
        sheets.color_by_tier(ws, {"A": 1, "B": 1, "C": 1, "D": 2})
        assert len(ws.requests) == 2
        first = ws.requests[0]["repeatCell"]["range"]
        assert (first["startRowIndex"], first["endRowIndex"]) == (1, 4)

    def test_an_unknown_company_is_left_alone(self):
        """A row whose company left targets.csv keeps whatever it had rather
        than being painted a misleading colour."""
        ws = self._sheet(["A", "Ghost"])
        n = sheets.color_by_tier(ws, {"A": 1})
        assert n == 1
        assert len(ws.requests) == 1

    def test_company_matching_ignores_case_and_padding(self):
        ws = self._sheet(["  langchain "])
        assert sheets.color_by_tier(ws, {"LangChain": 1}) == 1

    def test_a_failure_is_never_fatal(self):
        ws = self._sheet(["A"])

        def boom(_body):
            raise RuntimeError("quota")

        ws.batch_update = boom
        assert sheets.color_by_tier(ws, {"A": 1}) == 0


class TestSchema:
    def test_spec_columns_come_first_and_in_order(self):
        """Later phases depend on these positions; extras are appended."""
        assert sheets.COLUMNS[:12] == [
            "date_found", "company", "role", "score", "reasoning",
            "dealbreakers", "url", "status", "applied_date",
            "resume_version", "contact", "followup_due"]

    def test_human_owned_columns_exist_from_day_one(self):
        """Created now so there is no migration in three weeks."""
        for column in ("status", "applied_date", "resume_version", "contact",
                       "followup_due"):
            assert column in sheets.COLUMNS

    def test_pipeline_never_claims_a_human_column(self):
        assert not (set(sheets.PIPELINE_OWNED) & sheets.HUMAN_OWNED)


class TestRefreshEvictedRows:
    """A row that falls out of the top N must not keep a stale score.

    Rows are never deleted, so before this the sync simply stopped writing to
    them and the last score they held stayed on the page forever. Observed on
    a real sheet: 10 -> 4, 9 -> 5, 9 -> 7, 9 -> 7 — and the 10 was the row
    that read as the strongest lead in the list.
    """

    def _sheet_with(self, *rows):
        ws = FakeWorksheet([header_row()])
        sheets.sync(ws, list(rows))
        return ws

    def test_updates_a_row_that_is_no_longer_surfaced(self):
        ws = self._sheet_with(posting_row("https://x/1", score=10))
        result = sheets.sync(
            ws, [posting_row("https://x/2", score=9)],
            refresh_by_url=lambda urls: [posting_row(u, score=4)
                                         for u in urls])
        score_at = sheets.COLUMNS.index("score")
        by_url = {r[sheets.URL_COLUMN]: r for r in ws.values[1:]}
        assert by_url["https://x/1"][score_at] == 4
        assert result.refreshed == 1

    def test_a_refreshed_row_keeps_its_notes(self):
        """The whole reason evicted rows are not simply deleted."""
        ws = self._sheet_with(posting_row("https://x/1", score=10))
        notes_at = sheets.COLUMNS.index("notes")
        status_at = sheets.COLUMNS.index("status")
        ws.values[1][notes_at] = "worth a referral"
        ws.values[1][status_at] = "applied"

        sheets.sync(ws, [posting_row("https://x/2", score=9)],
                    refresh_by_url=lambda urls: [posting_row(u, score=4)
                                                 for u in urls])
        assert ws.values[1][notes_at] == "worth a referral"
        assert ws.values[1][status_at] == "applied"

    def test_surfaced_rows_are_never_passed_to_the_refresher(self):
        ws = self._sheet_with(posting_row("https://x/1", score=10))
        seen = []

        def refresher(urls):
            seen.extend(urls)
            return []

        sheets.sync(ws, [posting_row("https://x/1", score=7)],
                    refresh_by_url=refresher)
        assert seen == [], "a row still in the top N is not evicted"

    def test_no_refresher_leaves_behaviour_unchanged(self):
        """Callers that do not pass one must not start losing rows."""
        ws = self._sheet_with(posting_row("https://x/1", score=10))
        sheets.sync(ws, [posting_row("https://x/2", score=9)])
        score_at = sheets.COLUMNS.index("score")
        by_url = {r[sheets.URL_COLUMN]: r for r in ws.values[1:]}
        assert by_url["https://x/1"][score_at] == 10


class TestFirstSync:
    def test_writes_the_header_into_an_empty_sheet(self):
        ws = FakeWorksheet()
        sheets.sync(ws, [posting_row()])
        assert ws.values[0] == sheets.COLUMNS

    def test_appends_new_postings(self):
        ws = FakeWorksheet([header_row()])
        result = sheets.sync(ws, [posting_row("https://x/1"),
                                  posting_row("https://x/2")])
        assert result.appended == 2
        assert len(ws.appended) == 2

    def test_leaves_human_columns_blank_on_insert(self):
        ws = FakeWorksheet([header_row()])
        sheets.sync(ws, [posting_row()])
        row = ws.appended[0]
        for column in sheets.HUMAN_OWNED:
            assert row[sheets.COLUMNS.index(column)] == ""


class TestStatusIsNeverOverwritten:
    """The spec's one inviolable rule about the sheet."""

    def _sheet_with_status(self, status="Applied"):
        header = header_row()
        row = [""] * len(header)
        row[sheets.COLUMNS.index("url")] = "https://x/1"
        row[sheets.COLUMNS.index("company")] = "Anthropic"
        row[sheets.COLUMNS.index("score")] = "8"
        row[sheets.COLUMNS.index("status")] = status
        row[sheets.COLUMNS.index("applied_date")] = "2026-08-01"
        row[sheets.COLUMNS.index("contact")] = "jane@example.com"
        return FakeWorksheet([header, row])

    def test_status_survives_a_resync(self):
        ws = self._sheet_with_status("Applied")
        sheets.sync(ws, [posting_row(url="https://x/1", score=9)])
        assert ws.values[1][sheets.COLUMNS.index("status")] == "Applied"

    def test_every_human_column_survives(self):
        ws = self._sheet_with_status()
        sheets.sync(ws, [posting_row(url="https://x/1", score=9)])
        row = ws.values[1]
        assert row[sheets.COLUMNS.index("applied_date")] == "2026-08-01"
        assert row[sheets.COLUMNS.index("contact")] == "jane@example.com"

    def test_the_pipeline_columns_do_update(self):
        ws = self._sheet_with_status()
        sheets.sync(ws, [posting_row(url="https://x/1", score=9)])
        assert ws.values[1][sheets.COLUMNS.index("score")] == 9

    def test_a_known_url_is_never_duplicated(self):
        ws = self._sheet_with_status()
        result = sheets.sync(ws, [posting_row(url="https://x/1")])
        assert result.appended == 0
        assert ws.appended == []


class TestIdempotence:
    def test_a_second_identical_sync_changes_nothing(self):
        ws = FakeWorksheet([header_row()])
        sheets.sync(ws, [posting_row()])
        before = [list(r) for r in ws.values]

        result = sheets.sync(ws, [posting_row()])
        assert result.appended == 0
        assert result.updated == 0
        assert ws.values == before


class TestHeaderSafety:
    def test_adds_a_missing_column_instead_of_failing(self):
        """A sheet full of hand-typed notes must not be stranded by a new
        column in a later version."""
        older = [c for c in sheets.COLUMNS if c != "notes"]
        ws = FakeWorksheet([older])
        sheets.sync(ws, [posting_row()])
        assert "notes" in ws.values[0]

    def test_tolerates_extra_columns_the_user_added(self):
        ws = FakeWorksheet([header_row() + ["my own column"]])
        sheets.sync(ws, [posting_row()])
        assert ws.appended

    def test_writes_by_header_name_not_position(self):
        """So columns can be reordered in the sheet without breaking sync."""
        shuffled = list(reversed(sheets.COLUMNS))
        ws = FakeWorksheet([shuffled])
        sheets.sync(ws, [posting_row(company="Anthropic")])
        row = ws.appended[0]
        assert row[shuffled.index("company")] == "Anthropic"
        assert row[shuffled.index("url")] == "https://x/1"


class TestNotesColumn:
    def test_notes_is_human_owned(self):
        assert "notes" in sheets.HUMAN_OWNED
        assert "notes" not in sheets.PIPELINE_OWNED

    def test_notes_survive_a_resync(self):
        header = header_row()
        row = [""] * len(header)
        row[sheets.COLUMNS.index("url")] = "https://x/1"
        row[sheets.COLUMNS.index("score")] = "7"
        row[sheets.COLUMNS.index("notes")] = "referred by Sam; ask about team"
        ws = FakeWorksheet([header, row])

        sheets.sync(ws, [posting_row(url="https://x/1", score=9)])
        assert ws.values[1][sheets.COLUMNS.index("notes")] == \
            "referred by Sam; ask about team"
        assert ws.values[1][sheets.COLUMNS.index("score")] == 9

    def test_notes_are_left_blank_on_insert(self):
        ws = FakeWorksheet([header_row()])
        sheets.sync(ws, [posting_row()])
        assert ws.appended[0][sheets.COLUMNS.index("notes")] == ""


class TestRowBuilding:
    def test_parses_dealbreakers_from_stored_json(self):
        row = sheets.build_row({
            "first_seen_at": "2026-08-13T04:00:00+00:00", "company": "A",
            "title": "T", "apply_score": 8, "overall": 6, "reasoning": "r",
            "dealbreakers": '["security clearance", "relocation"]',
            "url": "u", "apply_reason": "ar",
        })
        assert row["dealbreakers"] == "security clearance; relocation"

    def test_date_found_is_a_plain_date(self):
        row = sheets.build_row({
            "first_seen_at": "2026-08-13T04:00:00+00:00", "company": "A",
            "title": "T", "apply_score": 8, "overall": 6, "reasoning": "",
            "dealbreakers": "[]", "url": "u", "apply_reason": "",
        })
        assert row["date_found"] == "2026-08-13"

    def test_score_is_apply_and_fit_is_carried_separately(self):
        row = sheets.build_row({
            "first_seen_at": "2026-08-13", "company": "A", "title": "T",
            "apply_score": 9, "overall": 5, "reasoning": "",
            "dealbreakers": "[]", "url": "u", "apply_reason": "",
        })
        assert row["score"] == 9 and row["fit"] == 5


class TestA1Notation:
    def test_converts_columns_past_z(self):
        assert sheets._a1(1, 1) == "A1"
        assert sheets._a1(2, 26) == "Z2"
        assert sheets._a1(3, 27) == "AA3"
