"""Google Sheets sync — the entire human interface for Phase 1.

Two rules govern this module, both from the spec:

  * **The sheet is a rendered view, never authoritative state.** SQLite is the
    source of truth. Nothing is ever read back from the sheet to drive the
    pipeline.
  * **The `status` column is human-owned and must never be overwritten.** This
    is why the sync is an upsert keyed on `url` rather than a rewrite: a
    full-sheet write is one bug away from erasing a week of hand-tracking.

Auth is a service account, chosen over OAuth because this runs unattended
under cron: no browser, no consent screen, no refresh token to expire at 6am.
Share the sheet with the service account's `client_email` as an Editor.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Columns 1-12 are the spec's schema, fixed up front so later phases never
# force a migration. Phase 1 writes seven of them; the five human-owned ones
# exist from day one and stay empty.
#
# `fit` and `apply_reason` are APPENDED rather than inserted, so the spec's
# column positions are untouched.
COLUMNS = [
    "date_found", "company", "role", "score", "reasoning", "dealbreakers",
    "url",
    # human-owned from here — the sync never writes these
    "status", "applied_date", "resume_version", "contact", "followup_due",
    # appended extras, so the spec's column positions above are untouched
    "fit", "apply_reason", "archetype", "legitimacy",
    # free-text, yours. Appended rather than inserted for the same reason.
    "notes",
]

HUMAN_OWNED = {"status", "applied_date", "resume_version", "contact",
               "followup_due", "notes"}

PIPELINE_OWNED = [c for c in COLUMNS if c not in HUMAN_OWNED]

URL_COLUMN = COLUMNS.index("url")


class SheetError(RuntimeError):
    """The sheet could not be reached or configured."""


@dataclass
class SyncResult:
    appended: int = 0
    updated: int = 0
    refreshed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)


def open_worksheet(sheet_id: str | None = None, tab: str | None = None,
                   credentials_path: str | None = None):
    """Open (or create) the target tab. Raises SheetError with what to fix."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:  # pragma: no cover - import guard
        raise SheetError(
            "gspread and google-auth are required: "
            "pip install gspread google-auth") from exc

    sheet_id = sheet_id or os.environ.get("GOOGLE_SHEET_ID")
    tab = tab or os.environ.get("GOOGLE_SHEET_TAB", "roles")
    credentials_path = credentials_path or os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")

    if not sheet_id:
        raise SheetError(
            "GOOGLE_SHEET_ID is not set in .env. Create a Google Sheet and "
            "copy the id out of its URL: "
            "docs.google.com/spreadsheets/d/<THIS PART>/edit")

    path = Path(credentials_path)
    if not path.exists():
        raise SheetError(
            f"service account key not found at {path}. In Google Cloud "
            f"Console: create a service account, add a JSON key, save it "
            f"there, and enable the Google Sheets API for the project.")

    try:
        creds = Credentials.from_service_account_file(str(path), scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001 - surfaced with the fix attached
        email = _service_account_email(path)
        raise SheetError(
            f"could not open sheet {sheet_id}\n"
            # Some google-auth and gspread errors carry an empty str(), so the
            # type and repr are included — a blank error message is worse than
            # no error message.
            f"  {type(exc).__name__}: {exc or repr(exc)}\n"
            f"  If this is a permission error, the usual cause is that the "
            f"sheet is not shared with the service account. Share it as an "
            f"Editor with: {email}\n"
            f"  If it mentions an API being disabled, enable the Google "
            f"Sheets API for the service account's project.") from exc

    try:
        return spreadsheet.worksheet(tab)
    except Exception:
        log.info("tab %r not found, creating it", tab)
        try:
            return spreadsheet.add_worksheet(title=tab, rows=1000,
                                             cols=len(COLUMNS))
        except Exception as exc:  # noqa: BLE001
            raise SheetError(
                f"could not create tab {tab!r} in the sheet\n"
                f"  {type(exc).__name__}: {exc or repr(exc)}\n"
                f"  The service account may have Viewer rather than Editor "
                f"access. Existing tabs: "
                f"{[w.title for w in spreadsheet.worksheets()]}") from exc


def _service_account_email(path: Path) -> str:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(
            "client_email", "(client_email not found in key file)")
    except Exception:  # noqa: BLE001
        return "(could not read the key file)"


def _field(posting, name, default=""):
    """Read a column from a sqlite3.Row or a plain dict, tolerating absence.

    sqlite3.Row raises IndexError for an unknown key and dict raises KeyError;
    a query that forgets a column should produce a blank cell rather than
    taking the whole sync down.
    """
    try:
        value = posting[name]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def build_row(posting) -> dict:
    """One sheet row from one scored posting."""
    dealbreakers = _field(posting, "dealbreakers", "[]")
    if isinstance(dealbreakers, str):
        try:
            dealbreakers = json.loads(dealbreakers)
        except (TypeError, ValueError):
            dealbreakers = []
    legitimacy = _field(posting, "legitimacy", "ok") or "ok"
    return {
        "date_found": str(_field(posting, "first_seen_at"))[:10],
        "company": _field(posting, "company"),
        "role": _field(posting, "title"),
        "score": _field(posting, "apply_score"),
        "reasoning": _field(posting, "reasoning"),
        "dealbreakers": "; ".join(dealbreakers or []),
        "url": _field(posting, "url"),
        "fit": _field(posting, "overall"),
        "apply_reason": _field(posting, "apply_reason"),
        # The lane, with the engagement type appended when it is not a direct
        # hire. Kept in one column because they answer the same question when
        # you are scanning the sheet: what kind of role is this. Only
        # `consulting` is shown — annotating every direct role would make the
        # marker invisible, which is the point of a marker.
        "archetype": (
            f"{_field(posting, 'archetype')} (consulting)".strip()
            if _field(posting, "engagement") == "consulting"
            else _field(posting, "archetype")),
        # Only surfaced when it is a problem — an "ok" in every row is noise
        # that trains you to stop reading the column.
        "legitimacy": ("" if legitimacy == "ok" else
                       f"{legitimacy}: {_field(posting, 'legitimacy_reason')}"
                       .strip().rstrip(":").strip()),
    }


def ensure_header(worksheet) -> list[str]:
    """Write the header if the sheet is empty; verify and extend it if not.

    Missing columns are appended rather than treated as fatal, so adding a
    field in a later version does not strand a sheet full of hand-typed notes.
    """
    existing = worksheet.row_values(1)
    if not existing:
        worksheet.update([COLUMNS], "A1")
        _freeze_header(worksheet)
        return list(COLUMNS)

    missing = [c for c in COLUMNS if c not in existing]
    if missing:
        log.info("adding column(s) %s to the sheet", missing)
        extended = list(existing) + missing
        worksheet.update([extended], "A1")
        existing = extended

    _freeze_header(worksheet)
    return existing


def _freeze_header(worksheet) -> None:
    """Freeze row 1.

    Cosmetic, but also load-bearing: gspread's sort() with no explicit range
    starts at `frozenRowCount + 1`, so on a sheet with nothing frozen it would
    sort the header row into the data. The sort below passes an explicit range
    as well; this is belt and braces.
    """
    try:
        if worksheet.frozen_row_count < 1:
            worksheet.freeze(rows=1)
    except Exception as exc:  # noqa: BLE001 - never fatal
        log.debug("could not freeze the header row: %s", exc)


def sync(worksheet, rows: list[dict], refresh_by_url=None) -> SyncResult:
    """Upsert `rows` into the worksheet, keyed on url.

    Human-owned columns are never written, on either an insert or an update.
    On an update only the pipeline's own cells are touched, so a status typed
    into the sheet at 9am survives the next morning's run.

    `refresh_by_url` is called with the urls already in the sheet that are not
    in `rows` — the ones that have dropped out of the top N — and returns their
    current rows. Rows are never deleted (they may carry hand-typed notes), so
    without this their scores freeze at whatever they were the last time they
    ranked. A stale 10 is indistinguishable from a current 10 on the page, and
    is read as the best lead in the sheet. Measured drift on a real sheet
    before this existed: 10 -> 4, 9 -> 5, 9 -> 7, 9 -> 7.
    """
    result = SyncResult()
    header = ensure_header(worksheet)
    index = {name: i for i, name in enumerate(header)}

    existing = worksheet.get_all_values()[1:]          # skip header
    url_at = index["url"]
    row_number_by_url = {
        row[url_at]: n
        for n, row in enumerate(existing, start=2)
        if len(row) > url_at and row[url_at]
    }

    rows = list(rows)
    refreshed_urls: set[str] = set()
    if refresh_by_url is not None:
        surfaced = {row.get("url") for row in rows}
        evicted = [url for url in row_number_by_url if url not in surfaced]
        if evicted:
            extra = list(refresh_by_url(evicted))
            refreshed_urls = {row.get("url") for row in extra}
            rows.extend(extra)

    updates, appends = [], []
    touched_urls: set[str] = set()
    for row in rows:
        target = row_number_by_url.get(row["url"])
        if target is None:
            line = [""] * len(header)
            for name, value in row.items():
                if name in index:
                    line[index[name]] = value
            appends.append(line)
            continue

        current = existing[target - 2]
        for name, value in row.items():
            if name not in index or name in HUMAN_OWNED:
                continue
            column = index[name]
            was = current[column] if len(current) > column else ""
            if str(was) != str(value):
                updates.append({
                    "range": _a1(target, column + 1),
                    "values": [[value]],
                })
                touched_urls.add(row.get("url"))

    if updates:
        worksheet.batch_update(updates)
    if appends:
        worksheet.append_rows(appends, value_input_option="RAW")
        result.appended = len(appends)

    # Reported apart so a run that only corrects dropped-out rows is visible
    # rather than looking like ordinary churn in the top N.
    result.refreshed = len(touched_urls & refreshed_urls)
    result.updated = len(touched_urls) - result.refreshed
    result.unchanged = (len(rows) - result.appended
                        - result.updated - result.refreshed)
    return result


def set_cells(worksheet, url: str, values: dict[str, str],
              expect: dict[str, str] | None = None) -> bool:
    """Write specific cells on the row with this url. Compare-and-set.

    The ONLY place the pipeline writes a human-owned column, and it exists
    because Phase 2 §1 needs `tailor` to advance queued -> drafted and to
    record `resume_version`. That does not weaken Phase 1's rule: `sync` still
    never touches these columns. The distinction is that sync is a bulk
    rewrite of a rendered view, where a bug erases a week of hand-tracking,
    while this is one row, named columns, and conditional.

    `expect` makes it conditional. If the sheet no longer holds the value we
    read a moment ago — because the human retyped it while a batch of fifteen
    resumes was generating — the write is skipped and False returned. Writing
    unconditionally would silently revert their edit.
    """
    header = worksheet.row_values(1)
    index = {name: i for i, name in enumerate(header)}
    if "url" not in index:
        log.warning("sheet has no url column; refusing to write")
        return False

    url_at = index["url"]
    target = None
    for number, row in enumerate(worksheet.get_all_values()[1:], start=2):
        if len(row) > url_at and row[url_at] == url:
            target, current = number, row
            break
    if target is None:
        log.warning("no sheet row for %s; nothing written", url)
        return False

    for column, wanted in (expect or {}).items():
        position = index.get(column)
        actual = (current[position] if position is not None
                  and len(current) > position else "")
        if actual.strip().lower() != wanted.strip().lower():
            log.info("skipping write for %s: %s is %r, expected %r",
                     url, column, actual, wanted)
            return False

    updates = [{"range": _a1(target, index[c] + 1), "values": [[v]]}
               for c, v in values.items() if c in index]
    if not updates:
        return False
    worksheet.batch_update(updates)
    return True


def read_statuses(worksheet) -> dict[str, str]:
    """The sheet's status column, keyed on url. Read-only.

    The pipeline never writes this column — it is the one cell in the sheet
    that only the human owns. Reading it back for analytics (addendum 4.3) is
    not the same as letting the sheet drive the pipeline: nothing here changes
    what gets fetched, filtered or scored.
    """
    header = worksheet.row_values(1)
    try:
        url_at = header.index("url")
        status_at = header.index("status")
    except ValueError:
        log.warning("sheet has no url/status column; skipping status mirror")
        return {}

    out = {}
    for row in worksheet.get_all_values()[1:]:
        if len(row) <= url_at or not row[url_at]:
            continue
        out[row[url_at]] = row[status_at] if len(row) > status_at else ""
    return out


def ensure_status_dropdown(worksheet, path=None) -> None:
    """Constrain the status column to the taxonomy (addendum 4.2).

    A dropdown rather than a note, because a free-text column drifts: "Phone
    screen", "phone-screen" and "PhoneScreen" all mean one thing and none of
    them match. Aliases in states.yml catch that after the fact; this stops it
    happening. Cosmetic if it fails — never fatal.
    """
    from . import states

    header = worksheet.row_values(1)
    if "status" not in header:
        return
    column = header.index("status")

    try:
        values = states.labels() if path is None else states.labels(path)
        worksheet.spreadsheet.batch_update({"requests": [{
            "setDataValidation": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 1,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in values],
                    },
                    # Deliberately not strict: a value outside the list warns
                    # rather than being rejected. Losing a hand-typed note to
                    # a validation error is worse than an unrecognised status,
                    # which the mirror reports anyway.
                    "strict": False,
                    "showCustomUi": True,
                },
            }
        }]})
    except Exception as exc:  # noqa: BLE001 - cosmetic, never fatal
        log.warning("could not set the status dropdown: %s", exc)


# Deliberately pale. These are backgrounds behind black body text and a notes
# column you actually read, so saturation costs legibility for no information
# — the tier is a three-way flag, not a gradient. Tier 3 is a muted rose
# rather than a true red: "would consider" is not an error state, and a strong
# red reads as one.
TIER_COLORS = {
    1: {"red": 0.82, "green": 0.89, "blue": 0.98},   # blue
    2: {"red": 1.00, "green": 0.95, "blue": 0.80},   # yellow
    3: {"red": 0.97, "green": 0.86, "blue": 0.86},   # red
}


def color_by_tier(worksheet, tier_by_company: dict[str, int]) -> int:
    """Shade each row by its company's tier. Cosmetic, never fatal.

    Runs AFTER the sort so the colours match what is on screen. Sorting does
    carry formatting with the row, but re-deriving from the sorted contents
    means a colour can never drift out of step with the company beside it.

    Contiguous runs of the same tier are collapsed into one request — after
    sorting by score the rows are well shuffled, so this is a modest win, but
    it keeps a 200-row sheet from becoming 200 API requests.
    """
    header = worksheet.row_values(1)
    if "company" not in header:
        return 0
    company_at = header.index("company")
    lookup = {k.strip().lower(): v for k, v in tier_by_company.items()}

    tiers: list[int | None] = []
    for row in worksheet.get_all_values()[1:]:
        name = row[company_at] if len(row) > company_at else ""
        tiers.append(lookup.get(name.strip().lower()))

    requests, start = [], 0
    while start < len(tiers):
        end = start
        while end + 1 < len(tiers) and tiers[end + 1] == tiers[start]:
            end += 1
        color = TIER_COLORS.get(tiers[start])
        if color is not None:
            requests.append({"repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": start + 1,       # skip the header
                    "endRowIndex": end + 2,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(header),
                },
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }})
        start = end + 1

    if not requests:
        return 0
    try:
        worksheet.spreadsheet.batch_update({"requests": requests})
    except Exception as exc:  # noqa: BLE001 - cosmetic, never fatal
        log.warning("could not colour rows by tier: %s", exc)
        return 0
    return sum(1 for t in tiers if t in TIER_COLORS)


def sort_by_score(worksheet) -> None:
    """Sort whole rows by score desc, then date_found desc.

    Two things this gets right that are easy to get wrong:

      * The range starts at row 2. gspread's default range begins at
        `frozenRowCount + 1`, which is row 1 on an unfrozen sheet — that would
        sort the header into the data.
      * Column positions come from the live header, not from COLUMNS. The sync
        is keyed on header names, so you can reorder columns in the sheet
        freely; this must follow suit or it would sort on the wrong column.
    """
    header = worksheet.row_values(1)
    try:
        score = header.index("score") + 1
        found = header.index("date_found") + 1
    except ValueError:
        log.warning("cannot sort: the sheet has no score/date_found column")
        return

    last_row = len(worksheet.col_values(header.index("url") + 1))
    if last_row < 3:
        return                          # nothing to order

    rng = f"{_a1(2, 1)}:{_a1(last_row, len(header))}"
    try:
        worksheet.sort((score, "des"), (found, "des"), range=rng)
    except Exception as exc:  # noqa: BLE001 - cosmetic, never fatal
        log.warning("could not sort the sheet: %s", exc)


def _a1(row: int, column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"
