"""Edit-distance tracking (Phase 2 spec §7, §9.11).

§7 is blunt about what to measure: "Do not measure 'does the human like it'
in the abstract. Measure what they change."

So the headline is a normalised Levenshtein distance between the generated
letter and the one actually sent, and the useful part is the per-paragraph
breakdown underneath it. An aggregate of 0.08 says the draft was close; it
does not say that paragraph three gets rewritten every single time, which is
the thing that would tell you which instruction is wrong.

The resume is deliberately not measured this way. It is assembled from a
fixed bank, so it should be sent as generated — hand-editing it discards the
traceability that makes §2.1 a guarantee rather than a hope. If a resume is
wrong the fix is upstream, in the bank or the selection.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import letters

log = logging.getLogger(__name__)

SENT_SUFFIX = ".sent.md"


def levenshtein(a: str, b: str) -> int:
    """Classic edit distance, two rows rather than a full matrix.

    A letter is ~2,300 characters, so the full matrix would be 5M cells for
    no benefit. This is O(len(a) * len(b)) time and O(len(b)) space.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,            # deletion
                current[j - 1] + 1,         # insertion
                previous[j - 1] + (ca != cb),  # substitution
            ))
        previous = current
    return previous[-1]


def normalise(text: str) -> str:
    """Fold differences that are not edits.

    Whitespace and smart quotes change when text moves through an editor, and
    counting those as edits would put a floor under every measurement and hide
    the trend §7 asks for.
    """
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("—", "--").replace("–", "-"))
    return re.sub(r"[ \t]+", " ", text).strip()


def distance(generated: str, sent: str) -> float:
    """Normalised to 0..1. 0 = sent as written, 1 = nothing survived."""
    a, b = normalise(generated), normalise(sent)
    if not a and not b:
        return 0.0
    return levenshtein(a, b) / max(len(a), len(b))


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


@dataclass
class ParagraphChange:
    index: int
    distance: float
    generated: str
    sent: str

    @property
    def verdict(self) -> str:
        if self.distance == 0:
            return "unchanged"
        if self.distance < 0.10:
            return "light"
        if self.distance < 0.35:
            return "moderate"
        return "rewritten"


def compare(generated: str, sent: str) -> dict:
    """Whole-document distance plus a per-paragraph breakdown.

    Paragraphs are matched by position rather than by content. That is the
    honest choice for a four-paragraph letter with a fixed structure: if the
    human swapped two paragraphs, position-matching reports both as rewritten,
    which is true — the letter no longer says what it said in that slot.
    """
    gen_paras = paragraphs(generated)
    sent_paras = paragraphs(sent)
    changes = []
    for index in range(max(len(gen_paras), len(sent_paras))):
        g = gen_paras[index] if index < len(gen_paras) else ""
        s = sent_paras[index] if index < len(sent_paras) else ""
        changes.append(ParagraphChange(index=index, distance=distance(g, s),
                                       generated=g, sent=s))
    return {
        "distance": distance(generated, sent),
        "paragraphs": changes,
        "words_generated": len(generated.split()),
        "words_sent": len(sent.split()),
    }


def sent_path_for(generated_path: str | Path) -> Path:
    """Where the edited copy lives: cover_letter.md -> cover_letter.sent.md."""
    path = Path(generated_path)
    return path.parent / (path.stem + SENT_SUFFIX)


def _is_stale(sent_file: Path, generated_at: str | None) -> bool:
    """True if the edited file predates the draft it is paired with.

    Compared against the document's own `generated_at` rather than the file's
    mtime, because the .md on disk gets rewritten by every regeneration while
    the database row records when THIS draft was produced.
    """
    if not generated_at:
        return False
    import datetime

    try:
        generated = datetime.datetime.fromisoformat(generated_at)
    except ValueError:
        return False
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=datetime.timezone.utc)

    edited = datetime.datetime.fromtimestamp(sent_file.stat().st_mtime,
                                             tz=datetime.timezone.utc)
    return edited < generated


def collect(conn) -> list[dict]:
    """Every letter with an edited copy beside it, distance computed.

    Writes `sent_path` and `edit_distance` back to the row (§6). Skips
    documents already measured against the same file, so this is cheap to
    re-run and does not churn the table.
    """
    # Only the live document. Superseded rows point at the same file on disk,
    # so including them counted one edited letter four times — inflating n and
    # dragging the mean toward a number nobody produced.
    rows = conn.execute(
        "SELECT * FROM documents WHERE doc_type = 'cover_letter' "
        "AND generated_path IS NOT NULL AND superseded_at IS NULL "
        "ORDER BY generated_at").fetchall()

    out = []
    for row in rows:
        generated_file = Path(row["generated_path"])
        sent_file = sent_path_for(generated_file)
        if not sent_file.exists():
            continue

        # A sent file older than the draft it supposedly came from is an edit
        # of an EARLIER generation. Regenerating leaves exactly this: a stale
        # .sent.md beside a fresh .md. Measuring it reports generation
        # variance as if it were human editing — and prose regenerates at
        # ~0.53 distance, so the number would be pure noise dressed as signal.
        if _is_stale(sent_file, row["generated_at"]):
            log.warning(
                "%s predates the draft it sits beside (generated %s); it is "
                "an edit of an earlier version and cannot be measured. "
                "Re-edit the current draft, or delete the stale file.",
                sent_file, row["generated_at"])
            continue

        # The database first, the file only as a fallback. The generated file
        # is the one the human works on, so it gets renamed, overwritten and
        # deleted — the first real edit renamed it, destroying the baseline
        # §7 measures against. A row cannot be renamed.
        generated = row["content"]
        if not generated:
            if not generated_file.exists():
                log.warning("document %s has neither stored content nor a "
                            "file at %s; cannot measure",
                            row["id"], generated_file)
                continue
            generated = generated_file.read_text(encoding="utf-8")
        sent = sent_file.read_text(encoding="utf-8")
        result = compare(generated, sent)

        if (row["sent_path"] != str(sent_file)
                or row["edit_distance"] is None
                or abs(row["edit_distance"] - result["distance"]) > 1e-9):
            conn.execute(
                "UPDATE documents SET sent_path = ?, edit_distance = ? "
                "WHERE id = ?",
                (str(sent_file), result["distance"], row["id"]))

        posting = conn.execute(
            "SELECT company, title FROM postings WHERE id = ?",
            (row["posting_id"],)).fetchone()
        out.append({
            "document_id": row["id"], "posting_id": row["posting_id"],
            "company": posting["company"] if posting else "?",
            "title": posting["title"] if posting else "?",
            "archetype": row["archetype"], "generated_at": row["generated_at"],
            **result,
        })
    conn.commit()
    return out


def summarise(measured: list[dict]) -> dict:
    """The three numbers §7 asks for, plus the per-slot one that is useful."""
    if not measured:
        return {"n": 0}

    distances = [m["distance"] for m in measured]
    by_slot: dict[int, list[float]] = {}
    for entry in measured:
        for change in entry["paragraphs"]:
            by_slot.setdefault(change.index, []).append(change.distance)

    by_archetype: dict[str, list[float]] = {}
    for entry in measured:
        by_archetype.setdefault(entry["archetype"] or "?", []).append(
            entry["distance"])

    return {
        "n": len(measured),
        "mean": sum(distances) / len(distances),
        "worst": max(distances),
        "best": min(distances),
        "by_slot": {k: sum(v) / len(v) for k, v in sorted(by_slot.items())},
        "by_archetype": {k: sum(v) / len(v)
                         for k, v in sorted(by_archetype.items())},
    }


# Derived, never restated — see letters.BLOCK_NAMES for why. These label an
# ASSEMBLED letter, which is what collect() ever measures: both sides come from
# disk or documents.content, and both are assembled.
SLOT_NAMES = letters.BLOCK_NAMES
