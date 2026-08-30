"""Loading and validating config/targets.csv."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGETS_PATH = (Path(__file__).resolve().parent.parent
                        / "config" / "targets.csv")

REQUIRED_COLUMNS = {"company", "ats_type", "board_token", "tier"}


# What each tier means for surfacing, so the column is not decorative:
#   tier 1 "would accept today" — surfaces at the normal threshold
#   tier 2 "strong yes"         — surfaces at the normal threshold
#   tier 3 "would consider"     — must score 8+ to be worth a slot in the sheet
#
# Wired here rather than left as a convention, because a column nothing reads
# is not worth spending an evening ranking.
TIER_MIN_SCORE = {1: 6, 2: 6, 3: 8}


@dataclass
class Target:
    company: str
    ats_type: str
    board_token: str
    tier: int
    notes: str = ""

    @property
    def min_score(self) -> int:
        return TIER_MIN_SCORE.get(self.tier, 6)


def tier_thresholds(targets: list["Target"]) -> dict[str, int]:
    """Company -> minimum score required to reach the sheet."""
    return {target.company: target.min_score for target in targets}


def load_targets(path: Path | str = DEFAULT_TARGETS_PATH) -> list[Target]:
    """Read targets.csv, skipping blank and malformed rows loudly.

    Validation happens here rather than at fetch time so a typo in the CSV
    surfaces before any network traffic.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"targets file not found: {path}")

    from .fetchers import SUPPORTED

    targets: list[Target] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path} is missing required column(s): {sorted(missing)}")

        for lineno, row in enumerate(reader, start=2):
            company = (row.get("company") or "").strip()
            ats_type = (row.get("ats_type") or "").strip().lower()
            board_token = (row.get("board_token") or "").strip()
            if not company and not board_token:
                continue

            if not company or not ats_type or not board_token:
                raise ValueError(
                    f"{path}:{lineno} incomplete row: {row!r}")
            if ats_type not in SUPPORTED:
                raise ValueError(
                    f"{path}:{lineno} unknown ats_type {ats_type!r} for "
                    f"{company} (supported: {SUPPORTED})")

            try:
                tier = int((row.get("tier") or "3").strip())
            except ValueError:
                raise ValueError(
                    f"{path}:{lineno} tier must be an integer, "
                    f"got {row.get('tier')!r}") from None

            targets.append(Target(
                company=company,
                ats_type=ats_type,
                board_token=board_token,
                tier=tier,
                notes=(row.get("notes") or "").strip(),
            ))

    if not targets:
        raise ValueError(f"{path} contains no usable rows")

    seen: dict[tuple[str, str], str] = {}
    for target in targets:
        key = (target.ats_type, target.board_token)
        if key in seen:
            raise ValueError(
                f"duplicate board {key} used by both {seen[key]} and "
                f"{target.company} — this would double-count every posting")
        seen[key] = target.company

    return targets
