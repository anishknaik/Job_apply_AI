"""SQLite persistence. This is the source of truth for the whole pipeline.

Two rules hold everywhere in this module:

  * Nothing is ever deleted. Full posting history is what makes repost
    detection work.
  * A posting seen again only has `last_seen_at` touched. Everything else
    about the original row is left alone.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "jobpipe.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id                INTEGER PRIMARY KEY,
    ats_type          TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    company           TEXT NOT NULL,
    title             TEXT NOT NULL,
    normalized_title  TEXT NOT NULL,
    location          TEXT,
    remote_flag       INTEGER,
    description       TEXT,
    url               TEXT NOT NULL,
    posted_at         TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    stage_a_passed    INTEGER,
    stage_a_reason    TEXT,
    UNIQUE (ats_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_repost ON postings (content_hash);
CREATE INDEX IF NOT EXISTS idx_stage_a ON postings (stage_a_passed);

CREATE TABLE IF NOT EXISTS scores (
    posting_id        INTEGER PRIMARY KEY REFERENCES postings(id),
    skills_match      INTEGER,
    seniority_fit     INTEGER,
    domain_relevance  INTEGER,
    overall           INTEGER,
    dealbreakers      TEXT,
    reasoning         TEXT,
    model             TEXT,
    scored_at         TEXT,
    -- Addendum 3.1: the lane a posting belongs to, decided before scoring so
    -- the rubric can branch. One rubric spanning AI/ML engineering and data
    -- science is a known cause of scores drifting toward 6.
    archetype         TEXT,
    -- Addendum 3.2: legitimacy is tracked on its own axis and never feeds
    -- `overall`. A legitimate poor-fit role and a fake perfect-fit role need
    -- different handling, which collapsing them into one number prevents.
    legitimacy        TEXT,
    legitimacy_reason TEXT,
    -- Same principle applied to fit. `overall` is a strict paper-fit
    -- assessment; `apply_score` is whether it is worth an application anyway.
    -- The two come apart for the most important shape in this search: right
    -- skills, not enough years. Collapsing them into one number means either
    -- losing those roles or inflating every score to keep them.
    apply_score       INTEGER,
    apply_reason      TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT,
    finished_at   TEXT,
    fetched       INTEGER,
    new_postings  INTEGER,
    stage_a_pass  INTEGER,
    scored        INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    errors        TEXT
);

-- Status history (addendum 4.3). An append-only event log rather than a
-- `status` column on postings, for one reason: 4.3 asks for per-channel
-- advance rates, and an advance rate is a question about TRANSITIONS. A
-- single column answers "how many are at interview now" but can never answer
-- "what fraction of Greenhouse applications reached interview", because
-- overwriting the value destroys the evidence. Rows here are never updated.
--
-- The sheet stays the source of truth for status; this mirrors it, read-only.
-- Generated documents (Phase 2 spec §6). Never overwritten: a new generation
-- for the same posting inserts a new row and the old one is superseded, the
-- same rule the sheet follows for evicted rows. `sent_path` is populated after
-- the human edits and sends, so that when a role reaches interview you know
-- exactly what they read.
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    posting_id      INTEGER NOT NULL REFERENCES postings (id),
    doc_type        TEXT NOT NULL,      -- resume | cover_letter | short_answer
    archetype       TEXT,
    bullet_ids      TEXT,               -- JSON array, resume only
    generated_path  TEXT,
    sent_path       TEXT,               -- populated after the human edits
    model           TEXT,
    generated_at    TEXT NOT NULL,
    edit_distance   REAL,               -- computed when sent_path appears
    superseded_at   TEXT                -- set when a newer doc replaces this
);

CREATE TABLE IF NOT EXISTS status_events (
    id          INTEGER PRIMARY KEY,
    posting_id  INTEGER NOT NULL REFERENCES postings (id),
    status      TEXT NOT NULL,
    previous    TEXT,
    noted_at    TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'sheet'
);

-- The last value read from the sheet's status column, per posting. Not
-- history - status_events is the history - but the one fact needed to tell a
-- human edit from a cell nobody touched. See status_mirror.py.
CREATE TABLE IF NOT EXISTS sheet_status (
    posting_id  INTEGER PRIMARY KEY REFERENCES postings (id),
    status      TEXT NOT NULL,
    read_at     TEXT NOT NULL
);
"""


def utcnow() -> str:
    """Single source of 'now' for the whole pipeline, as an ISO-8601 string.

    Stored as text rather than a datetime because Python 3.12 deprecated the
    implicit sqlite3 datetime adapters.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_days_ago(days: int) -> str:
    """A cutoff in exactly the format utcnow() writes.

    Deliberately not SQLite's `datetime('now', '-N days')`: that renders as
    "2026-08-06 14:23:05" with a space and no offset, while utcnow() writes
    "2026-08-06T14:23:05+00:00". Comparing the two as text compares ' ' against
    'T' and silently gives the wrong answer.
    """
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.isoformat(timespec="seconds")


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after the first schema shipped. CREATE TABLE IF NOT EXISTS
# does nothing to a table that already exists, so new columns have to be
# applied explicitly or an existing database silently keeps the old shape.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("scores", "archetype", "TEXT"),
    ("scores", "legitimacy", "TEXT"),
    ("scores", "legitimacy_reason", "TEXT"),
    ("scores", "apply_score", "INTEGER"),
    ("scores", "apply_reason", "TEXT"),
    ("scores", "engagement", "TEXT"),
    # The generated text itself, not just a path to it. A path is not a
    # record: the first human edit of a real letter renamed the file rather
    # than copying it, and the generated original — the baseline the whole of
    # §7 measures against — was simply gone. Content in the database survives
    # renames, moves, overwrites and deletions.
    ("documents", "content", "TEXT"),

    # --- Phase 5 Slice 0 -------------------------------------------------
    # Which draft this is, per posting and doc_type. PHASE5_PLAN §5.4 wants
    # two distances, not one: first save -> final save says how good the
    # first draft was, final save -> .sent.md says what is still changed
    # afterwards. One column answers both, and §8.3 has zero rows either way,
    # so there is no historical continuity to protect.
    ("documents", "revision", "INTEGER"),

    # The grounding verdict, stored against the draft it was run on. NULL is
    # meaningful: it is "never checked", which copy_for_editing refuses. A
    # regenerated letter gets a NEW row via insert_document's supersede, so a
    # fresh draft cannot inherit its predecessor's pass — the fail-closed
    # property comes from the schema, not from remembering to invalidate.
    ("documents", "grounding_ok", "INTEGER"),
    ("documents", "grounding_accuracy", "REAL"),
    # Which model checked it. The independence claim in §5.1 is only worth
    # anything if it is recorded: writer and checker must not be the same.
    ("documents", "grounding_model", "TEXT"),
    # The full claim list, not just the flag — a failure has to be readable
    # after the fact or the gate is unarguable.
    ("documents", "grounding_claims", "TEXT"),
    ("documents", "grounding_checked_at", "TEXT"),

    # Whether the form asked for a letter. §5.3 accepts that `measure`'s
    # population is letter-required applications only; this is what makes
    # that population inspectable rather than implicit.
    ("status_events", "letter_required", "INTEGER"),

    # Score provenance. `model` already records the model id and `scored_at`
    # the time; these add the backend (dev API vs Vertex) and the rubric the
    # score was produced under, so a corpus mixing 3.1 and 2.5 Flash scores
    # stays inspectable rather than silently comparable. `domain` is the
    # company's field, which selects the resume variant downstream.
    ("scores", "scorer", "TEXT"),
    ("scores", "rubric_version", "TEXT"),
    ("scores", "domain", "TEXT"),
    # Why the seniority bar sits above him: none | years | scope | both. The
    # scope ceiling in scoring._apply_scope_ceiling fires on this, so without
    # the column a capped score cannot be told from one the model simply
    # scored low - and the cap becomes unauditable the moment it matters.
    ("scores", "seniority_gap", "TEXT"),

    # Which form question a short answer answers (PHASE5_PLAN §6.1). Part of
    # the supersede key: a form asks several questions, and saving the answer
    # to one must not retire the answer to another. NULL for resumes and
    # letters, which keep one live document per posting as before.
    ("documents", "question", "TEXT"),
]


def _apply_migrations(conn: sqlite3.Connection) -> list[str]:
    applied = []
    for table, column, coltype in MIGRATIONS:
        existing = {row["name"] for row in
                    conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue                      # table not created yet
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            applied.append(f"{table}.{column}")
    return applied


# Indexes on columns that may only exist after a migration. Kept out of
# SCHEMA because executescript runs before migrations, and CREATE INDEX on a
# not-yet-added column fails on any pre-existing database.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_scores_overall ON scores (overall);
CREATE INDEX IF NOT EXISTS idx_scores_apply ON scores (apply_score);
CREATE INDEX IF NOT EXISTS idx_status_posting
    ON status_events (posting_id, noted_at);
CREATE INDEX IF NOT EXISTS idx_documents_posting
    ON documents (posting_id, doc_type, generated_at);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    applied = _apply_migrations(conn)
    conn.executescript(INDEXES)
    conn.commit()
    if applied:
        import logging
        logging.getLogger(__name__).info(
            "applied schema migrations: %s", ", ".join(applied))


# ---------------------------------------------------------------------------
# postings
# ---------------------------------------------------------------------------

def upsert_posting(conn: sqlite3.Connection, posting, content_hash: str,
                   seen_at: str | None = None) -> tuple[int, bool]:
    """Insert a posting, or touch `last_seen_at` if we have seen it before.

    Returns (posting_id, is_new). `is_new` drives everything downstream —
    only new postings are filtered and scored.

    Deliberately a SELECT-then-write rather than an upsert with RETURNING:
    the two-step version makes "was this new?" unambiguous, and this table is
    small enough that the extra query costs nothing.
    """
    seen_at = seen_at or utcnow()
    cur = conn.execute(
        "SELECT id FROM postings WHERE ats_type = ? AND source_id = ?",
        (posting.ats_type, posting.source_id),
    )
    row = cur.fetchone()

    if row is not None:
        conn.execute(
            "UPDATE postings SET last_seen_at = ? WHERE id = ?",
            (seen_at, row["id"]),
        )
        return row["id"], False

    cur = conn.execute(
        """
        INSERT INTO postings (
            ats_type, source_id, company, title, normalized_title,
            location, remote_flag, description, url, posted_at,
            first_seen_at, last_seen_at, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            posting.ats_type,
            posting.source_id,
            posting.company,
            posting.title,
            posting.normalized_title,
            posting.location,
            int(posting.remote_flag),
            posting.description,
            posting.url,
            posting.posted_at,
            seen_at,
            seen_at,
            content_hash,
        ),
    )
    return cur.lastrowid, True


def find_prior_content_hash(conn: sqlite3.Connection, content_hash: str,
                            exclude_id: int, within_days: int = 60):
    """Return an earlier posting with the same content_hash, if one is recent.

    Companies close and relist the same req constantly. Without this you
    re-score the same job every three weeks and pay for it every time.
    """
    cur = conn.execute(
        """
        SELECT id, first_seen_at
        FROM postings
        WHERE content_hash = ?
          AND id != ?
          AND julianday('now') - julianday(first_seen_at) <= ?
        ORDER BY first_seen_at ASC
        LIMIT 1
        """,
        (content_hash, exclude_id, within_days),
    )
    return cur.fetchone()


def merge_posting_locations(conn: sqlite3.Connection, posting_id: int,
                            incoming_location: str | None) -> bool:
    """Fold another listing's location into the canonical row's location list.

    Returns True if the list actually grew.

    When it grows, the row's Stage A verdict is cleared so it is re-evaluated.
    Without that reset a req first seen as "San Francisco" would keep its
    location_allow rejection even after its New York twin arrived — which is
    the exact failure that excluding location from content_hash is meant to
    prevent.
    """
    from .normalize import merge_locations

    row = conn.execute(
        "SELECT location FROM postings WHERE id = ?", (posting_id,)
    ).fetchone()
    if row is None:
        return False

    merged = merge_locations(row["location"], incoming_location)
    if merged == (row["location"] or ""):
        return False

    conn.execute(
        """
        UPDATE postings
        SET location = ?, stage_a_passed = NULL, stage_a_reason = NULL
        WHERE id = ?
        """,
        (merged, posting_id),
    )
    return True


def set_stage_a(conn: sqlite3.Connection, posting_id: int,
                passed: bool, reason: str) -> None:
    conn.execute(
        "UPDATE postings SET stage_a_passed = ?, stage_a_reason = ? WHERE id = ?",
        (int(passed), reason, posting_id),
    )


def get_posting(conn: sqlite3.Connection, posting_id: int):
    return conn.execute(
        "SELECT * FROM postings WHERE id = ?", (posting_id,)
    ).fetchone()


def get_unfiltered(conn: sqlite3.Connection):
    """Postings that have never been through Stage A."""
    return conn.execute(
        "SELECT * FROM postings WHERE stage_a_passed IS NULL ORDER BY id"
    ).fetchall()


def get_unscored(conn: sqlite3.Connection, limit: int | None = None):
    """Stage A survivors with no score yet. This is the Stage B work queue."""
    sql = """
        SELECT p.*
        FROM postings p
        LEFT JOIN scores s ON s.posting_id = p.id
        WHERE p.stage_a_passed = 1 AND s.posting_id IS NULL
        ORDER BY p.id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def get_queue_off_rubric(conn: sqlite3.Connection, rubric_version: str,
                         limit: int | None = None):
    """Postings in the application queue not scored by the current rubric.

    The queue is the set `list_queue` surfaces: everything carrying a status
    event. Those are the postings a session actually works on, and the ones
    `render_resume` needs `domain` for.

    Exists because `score` had exactly two modes and neither fits a rubric
    change. Scoring only the unscored skips every row already carrying a
    stale score, and `--rescore` deletes the whole table to reach them -
    which would discard the 3.1-era corpus PHASE5_PLAN 3.5 keeps deliberately
    as historical record, and bill for 625 postings to refresh 67.

    Ordered oldest-rubric-first so an interrupted run resumes usefully, and
    idempotent: a posting already on `rubric_version` is not returned, so
    re-running after a partial failure costs only what is left.
    """
    # EXISTS, not a join on the latest event: which status a posting is at
    # does not matter here, only that it is in the queue at all. A join on
    # MAX(id) would read as though it did, and `current_statuses` keys off
    # MAX(id) while a bare rowid would not necessarily agree with it.
    sql = """
        SELECT p.*
        FROM postings p
        LEFT JOIN scores s ON s.posting_id = p.id
        WHERE EXISTS (SELECT 1 FROM status_events e WHERE e.posting_id = p.id)
          AND (s.posting_id IS NULL
               OR s.rubric_version IS NULL
               OR s.rubric_version != ?)
        ORDER BY p.id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (rubric_version,)).fetchall()


# ---------------------------------------------------------------------------
# scores
# ---------------------------------------------------------------------------

# Written once so insert and copy cannot drift. They did: `engagement` was
# added to the schema, the migration and the sheet query, but missed in both
# INSERT statements — so every row scored with it came back NULL, silently,
# with no error anywhere. A duplicated column list is the reason that was
# possible, so the list now has exactly one home.
_SCORE_COLUMNS = (
    "skills_match", "seniority_fit", "domain_relevance", "overall",
    "dealbreakers", "reasoning", "model", "scored_at", "archetype",
    "engagement", "legitimacy", "legitimacy_reason", "apply_score",
    "apply_reason",
    # Provenance (PHASE5_PLAN 3.5). `model` alone does not say which backend
    # served it or which rubric produced it, and a corpus that mixes rubrics
    # silently is a corpus whose calibration numbers describe nothing.
    "domain", "seniority_gap", "scorer", "rubric_version",
)

_SCORE_INSERT = (
    f"INSERT OR REPLACE INTO scores (posting_id, {', '.join(_SCORE_COLUMNS)}) "
    f"VALUES ({', '.join('?' * (len(_SCORE_COLUMNS) + 1))})"
)


def insert_score(conn: sqlite3.Connection, posting_id: int, score: dict,
                 model: str, scorer: str | None = None,
                 rubric_version: str | None = None) -> None:
    import json

    values = dict(score)
    values["dealbreakers"] = json.dumps(score.get("dealbreakers", []))
    values["model"] = model
    values["scored_at"] = utcnow()
    if scorer is not None:
        values["scorer"] = scorer
    if rubric_version is not None:
        values["rubric_version"] = rubric_version
    conn.execute(_SCORE_INSERT,
                 (posting_id, *(values.get(c) for c in _SCORE_COLUMNS)))


def copy_score(conn: sqlite3.Connection, from_posting_id: int,
               to_posting_id: int) -> bool:
    """Carry an existing score forward onto a detected repost."""
    row = conn.execute(
        "SELECT * FROM scores WHERE posting_id = ?", (from_posting_id,)
    ).fetchone()
    if row is None:
        return False
    conn.execute(_SCORE_INSERT,
                 (to_posting_id, *(row[c] for c in _SCORE_COLUMNS)))
    return True


# Shared so the top-20 query and the refresh query can never drift apart —
# a refreshed row must be the same shape as a surfaced one or the sync would
# write different columns depending on how a row got there.
_SHEET_SELECT = """
    SELECT p.id, p.first_seen_at, p.company, p.title, p.location, p.url,
           s.overall, s.reasoning, s.dealbreakers,
           COALESCE(s.apply_score, s.overall) AS apply_score,
           s.apply_reason, s.archetype, s.engagement, s.legitimacy,
           s.legitimacy_reason
    FROM postings p
    JOIN scores s ON s.posting_id = p.id
"""


def get_rows_by_url(conn: sqlite3.Connection, urls) -> list:
    """Current scores for specific postings, regardless of their rank.

    The sheet is append-and-update only — it must never delete a row, because
    a row may carry hand-typed notes. The consequence is that a posting which
    falls out of the top N stops being written to and its score freezes at
    whatever it was last sync. That is worse than a missing row: a stale 10
    reads exactly like a current 10. This is how the sync refreshes those.
    """
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return []
    out = []
    for start in range(0, len(urls), 500):      # stay under SQLite's var cap
        chunk = urls[start:start + 500]
        placeholders = ",".join("?" * len(chunk))
        out.extend(conn.execute(
            f"{_SHEET_SELECT} WHERE p.url IN ({placeholders})", chunk
        ).fetchall())
    return out


def get_sheet_rows(conn: sqlite3.Connection, min_score: int = 4,
                   tier_thresholds: dict[str, int] | None = None,
                   limit: int = 25, max_stale_days: int | None = 7,
                   per_company: int | None = 3):
    """The top `limit` postings by apply_score, above a low hard floor.

    **Rank, not threshold.** The spec asks for "5-20 new roles" each morning,
    and calibration showed a fixed cutoff cannot deliver that reliably: the
    model's positives pile onto exactly 6, so one point of drift swings the
    sheet between three rows and twelve. Ranking gives a stable daily volume
    and removes a parameter that could not be tuned on the available sample.

    `min_score` stays as a floor so a thin day surfaces four good roles rather
    than padding to twenty with rejects.

    `tier_thresholds` maps company to the minimum apply_score that company
    needs to appear at all, so the `tier` column in targets.csv does real
    work: tier 3 ("would consider") can be made to require an 8 while tier 1
    still surfaces at the floor.
    """
    # Surfacing is driven by apply_score, not overall: the sheet answers
    # "what should I apply to today". `overall` rides along as a column so the
    # honest paper-fit assessment is still visible next to it.
    #
    # COALESCE keeps rows scored before apply_score existed from vanishing.
    # Liveness (addendum 4.1) without a browser. ATS feeds retain closed
    # postings, but a genuinely dead req eventually drops out of the JSON, and
    # `last_seen_at` already records that for free. The spec suggests a
    # Playwright pass; do not. Ashby's SPA returns HTTP 200 for dead postings,
    # so a browser check reports them live — wrong on roughly half the sources.
    #
    # The window is deliberately days, not one run: a single failed fetch must
    # never bury a whole company's board. At 7 days a board has to fail seven
    # consecutive runs to expire anything, by which point it is genuinely broken.
    where, params = ["COALESCE(s.apply_score, s.overall) >= ?"], [min_score]
    if max_stale_days is not None:
        where.append("p.last_seen_at >= ?")
        params.append(utc_days_ago(max_stale_days))

    rows = conn.execute(
        f"""
        {_SHEET_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(s.apply_score, s.overall) DESC,
                 p.first_seen_at DESC
        """,
        params,
    ).fetchall()

    if tier_thresholds:
        rows = [row for row in rows
                if row["apply_score"] >= tier_thresholds.get(row["company"],
                                                             min_score)]

    if per_company is None:
        return rows[:limit]

    # Addendum 2 §1: the cap is applied DURING selection, not after truncation.
    # Truncating to `limit` first and then dropping over-cap rows leaves the
    # sheet short — the slots freed by a dropped row are never refilled.
    #
    # The cap is the substantive half of this change. Without it one employer
    # with forty open reqs crowds out every mid-size company, and an actively
    # hiring 30-person startup sinks below a large company's back catalogue.
    taken: list = []
    seen: dict[str, int] = {}
    for row in rows:
        company = row["company"]
        if seen.get(company, 0) >= per_company:
            continue
        seen[company] = seen.get(company, 0) + 1
        taken.append(row)
        if len(taken) >= limit:
            break
    return taken


# ---------------------------------------------------------------------------
# documents (Phase 2 §6)
# ---------------------------------------------------------------------------

# One home for the column list, and the round-trip test in the suite is driven
# from it. The `engagement` bug came from a column defined in three places and
# written in one (Phase 2 spec §8).
_DOCUMENT_COLUMNS = (
    "posting_id", "doc_type", "archetype", "bullet_ids", "generated_path",
    "sent_path", "model", "generated_at", "edit_distance", "content",
    "revision", "grounding_ok", "grounding_accuracy", "grounding_model",
    "grounding_claims", "grounding_checked_at", "question",
)

_DOCUMENT_INSERT = (
    f"INSERT INTO documents ({', '.join(_DOCUMENT_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_DOCUMENT_COLUMNS))})"
)


def insert_document(conn: sqlite3.Connection, posting_id: int, doc_type: str,
                    **fields) -> int:
    """Record a generated document, superseding any earlier one of its type.

    Supersede rather than overwrite (§8): the earlier document may already
    have been sent, and destroying the record of what an employer read to save
    a row is the same mistake as letting an evicted sheet row go stale.

    Ordered so the new row is written BEFORE the old is marked superseded — a
    crash between the two leaves two live documents, which is recoverable. The
    reverse leaves none, which is the `--rescore` failure.
    """
    import json

    values = dict(fields)
    values["posting_id"] = posting_id
    values["doc_type"] = doc_type
    values.setdefault("generated_at", utcnow())
    if values.get("revision") is None:
        # Monotonic per posting+doc_type, counting superseded rows too — the
        # supersede below hides the previous draft from `current_document`
        # but does not delete it, which is what makes revision 1 -> N a real
        # sequence rather than a count of what happens to be live.
        # COALESCE(MAX, COUNT), not MAX alone: the 52 documents that predate
        # this column have revision NULL, so MAX returns NULL and the next
        # draft for one of those postings would restart at 1 and collide with
        # its own history. Counting them instead numbers the new draft after
        # the drafts that actually exist. Safe because nothing is ever deleted
        # from this table (§6), so the count only grows.
        # Scoped by question with IS rather than =, so the NULL every resume
        # and letter carries still matches itself and their numbering is
        # unchanged, while each short answer counts its own drafts.
        values["revision"] = 1 + (conn.execute(
            "SELECT COALESCE(MAX(revision), COUNT(*)) FROM documents "
            "WHERE posting_id = ? AND doc_type = ? AND question IS ?",
            (posting_id, doc_type, values.get("question"))).fetchone()[0]
            or 0)
    if isinstance(values.get("bullet_ids"), (list, tuple)):
        values["bullet_ids"] = json.dumps(list(values["bullet_ids"]))

    cur = conn.execute(_DOCUMENT_INSERT,
                       tuple(values.get(c) for c in _DOCUMENT_COLUMNS))
    new_id = cur.lastrowid
    conn.execute(
        "UPDATE documents SET superseded_at = ? "
        "WHERE posting_id = ? AND doc_type = ? AND question IS ? "
        "AND id != ? AND superseded_at IS NULL",
        (utcnow(), posting_id, doc_type, values.get("question"), new_id))
    return new_id


def record_grounding(conn: sqlite3.Connection, document_id: int,
                     result) -> None:
    """Store a grounding verdict against the draft it was run on.

    Committed here rather than left to the caller. `grounding.check` raises on
    a refusal and on zero extracted claims — both deliberately treated as
    failures — so a verdict that never arrives must still leave the draft on
    record as uncleared, and an uncommitted row would vanish with the raise.
    """
    import json

    conn.execute(
        "UPDATE documents SET grounding_ok = ?, grounding_accuracy = ?, "
        "grounding_model = ?, grounding_claims = ?, grounding_checked_at = ? "
        "WHERE id = ?",
        (1 if result.ok else 0, result.accuracy, result.model,
         json.dumps(result.claims), utcnow(), document_id))
    conn.commit()


def current_document(conn: sqlite3.Connection, posting_id: int,
                     doc_type: str, question: str | None = None):
    """The live document of this type, or None. `question` selects which
    short answer; resumes and letters leave it None."""
    return conn.execute(
        "SELECT * FROM documents WHERE posting_id = ? AND doc_type = ? "
        "AND question IS ? AND superseded_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (posting_id, doc_type, question)).fetchone()


def current_documents(conn: sqlite3.Connection, posting_id: int,
                      doc_type: str) -> list:
    """Every live document of this type — one per question, for short
    answers — oldest first."""
    return conn.execute(
        "SELECT * FROM documents WHERE posting_id = ? AND doc_type = ? "
        "AND superseded_at IS NULL ORDER BY id",
        (posting_id, doc_type)).fetchall()


# ---------------------------------------------------------------------------
# status (addendum 4.2 / 4.3)
# ---------------------------------------------------------------------------

def current_statuses(conn: sqlite3.Connection) -> dict[int, str]:
    """Latest status per posting, from the event log."""
    rows = conn.execute("""
        SELECT posting_id, status FROM status_events
        WHERE id IN (SELECT MAX(id) FROM status_events GROUP BY posting_id)
    """).fetchall()
    return {row["posting_id"]: row["status"] for row in rows}


def sheet_readings(conn: sqlite3.Connection) -> dict[int, str]:
    """What the sheet's status cell said at the last sync, per posting."""
    return {row["posting_id"]: row["status"] for row in conn.execute(
        "SELECT posting_id, status FROM sheet_status").fetchall()}


def record_sheet_reading(conn: sqlite3.Connection, posting_id: int,
                         status: str) -> None:
    """Remember this sync's reading of one status cell.

    Updated in place: it is a cache of the sheet's last value, not a record of
    anything that happened. Transitions live in status_events and are never
    overwritten.
    """
    conn.execute(
        "INSERT INTO sheet_status (posting_id, status, read_at) "
        "VALUES (?, ?, ?) ON CONFLICT (posting_id) DO UPDATE SET "
        "status = excluded.status, read_at = excluded.read_at",
        (posting_id, status, utcnow()))


def record_status(conn: sqlite3.Connection, posting_id: int, status: str,
                  previous: str | None = None,
                  source: str = "sheet",
                  letter_required: bool | None = None) -> bool:
    """Append a status transition. Returns False if nothing changed.

    No-op transitions are dropped rather than stored: the sheet is re-read
    every morning, so recording each unchanged reading would bury the handful
    of real transitions under thousands of duplicates.

    `letter_required` records whether the form actually asked for a letter.
    Skipping a letter where there is no field for one is correct, so the
    population `measure` reports on is not random — it skews toward smaller
    companies and more deliberate applications, which are the ones edited
    hardest. Storing the flag makes that population inspectable instead of
    implicit, which is what lets the metric narrow its claim honestly rather
    than pretending to describe all applications.
    """
    if previous == status:
        return False
    conn.execute(
        "INSERT INTO status_events (posting_id, status, previous, noted_at, "
        "source, letter_required) VALUES (?, ?, ?, ?, ?, ?)",
        (posting_id, status, previous, utcnow(), source,
         None if letter_required is None else int(letter_required)))
    return True


def postings_with_status(conn: sqlite3.Connection, status: str,
                         tiers: dict[str, int] | None = None,
                         limit: int | None = None) -> list:
    """Postings currently at `status`, in Phase 2 §1.2's queue order.

    Ordered by tier ascending then apply_score descending. The ordering is not
    a nicety: §1.1 expects the queue to exceed what actually gets applied to,
    so a partial run has to be the RIGHT partial run. `apply_score` cannot
    express this on its own because it scores the role, not the employer —
    a tier 3 company's 8 should not outrank a tier 1 company's 7.

    Tier comes from targets.csv rather than the database, so it is passed in;
    an unlisted company sorts last rather than crashing the queue.
    """
    rows = conn.execute(f"""
        {_SHEET_SELECT}
        WHERE p.id IN (
            SELECT posting_id FROM status_events
            WHERE id IN (SELECT MAX(id) FROM status_events
                         GROUP BY posting_id)
              AND status = ?)
    """, (status,)).fetchall()

    tiers = tiers or {}
    ordered = sorted(
        rows,
        key=lambda r: (tiers.get(r["company"], 99),
                       -(r["apply_score"] or 0),
                       r["company"]))
    return ordered[:limit] if limit else ordered


def advance_rates(conn: sqlite3.Connection, reached: str = "interview"):
    """Per-ATS advance rates (addendum 4.3).

    Reads the event log rather than a current-status column, so a posting that
    was interviewed and then rejected still counts as having reached
    interview. That is the whole reason for storing transitions.
    """
    return conn.execute("""
        SELECT p.ats_type,
               COUNT(DISTINCT p.id) AS surfaced,
               COUNT(DISTINCT CASE WHEN e.status = 'applied'
                                   THEN p.id END) AS applied,
               COUNT(DISTINCT CASE WHEN e.status = ?
                                   THEN p.id END) AS reached
        FROM postings p
        LEFT JOIN status_events e ON e.posting_id = p.id
        WHERE p.id IN (SELECT posting_id FROM status_events)
        GROUP BY p.ats_type
        ORDER BY surfaced DESC
    """, (reached,)).fetchall()


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at) VALUES (?)", (utcnow(),)
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, *, fetched: int = 0,
               new_postings: int = 0, stage_a_pass: int = 0, scored: int = 0,
               input_tokens: int = 0, output_tokens: int = 0,
               errors: str = "") -> None:
    conn.execute(
        """
        UPDATE runs SET finished_at = ?, fetched = ?, new_postings = ?,
                        stage_a_pass = ?, scored = ?, input_tokens = ?,
                        output_tokens = ?, errors = ?
        WHERE id = ?
        """,
        (utcnow(), fetched, new_postings, stage_a_pass, scored,
         input_tokens, output_tokens, errors, run_id),
    )
    conn.commit()
