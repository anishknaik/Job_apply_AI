"""Application status vocabulary (addendum 4.2).

The `status` column is human-owned: it is typed into the sheet by hand and the
pipeline never writes it. That makes tolerant reading the whole job of this
module. Someone typing "Interviewing" or "phone screen" at 9am means
`interview`, and a taxonomy that only accepts the canonical spelling would
quietly drop those rows out of every downstream number.

So: match case-insensitively, collapse whitespace and punctuation, and accept
a documented alias list. Anything still unrecognised is reported rather than
guessed at — a silent mismatch here shows up months later as an advance rate
computed over the wrong denominator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path("config/states.yml")


class UnknownStatus(ValueError):
    """A sheet cell that does not resolve to any known state."""


@dataclass(frozen=True)
class State:
    id: str
    label: str
    description: str
    terminal: bool
    order: int


def _key(text: str) -> str:
    """Normalise for matching: case, whitespace, and separators."""
    text = (text or "").strip().lower()
    text = re.sub(r"[_\-]+", " ", text)
    return re.sub(r"\s+", " ", text)


@lru_cache(maxsize=4)
def load_states(path: str | Path = DEFAULT_PATH) -> tuple[State, ...]:
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return tuple(
        State(id=entry["id"], label=entry["label"],
              description=entry.get("description", ""),
              terminal=bool(entry.get("terminal", False)), order=position)
        for position, entry in enumerate(raw["states"])
    )


@lru_cache(maxsize=4)
def _alias_map(path: str | Path = DEFAULT_PATH) -> dict[str, str]:
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for entry in raw["states"]:
        names = [entry["id"], entry["label"], *(entry.get("aliases") or [])]
        for name in names:
            key = _key(str(name))
            # An alias claimed by two states is a config bug, not a runtime
            # one: fail loudly at load rather than resolve by list order.
            if key in mapping and mapping[key] != entry["id"]:
                raise ValueError(
                    f"alias {name!r} is claimed by both {mapping[key]!r} and "
                    f"{entry['id']!r} in {path}")
            mapping[key] = entry["id"]
    return mapping


def normalise(text: str | None, path: str | Path = DEFAULT_PATH) -> str:
    """Resolve a hand-typed status to a canonical id.

    An empty cell is `not_applied` — the resting state, and by far the most
    common value in the sheet.
    """
    key = _key(text or "")
    mapping = _alias_map(path)
    if key in mapping:
        return mapping[key]
    if not key:
        return "not_applied"
    raise UnknownStatus(
        f"{text!r} is not a known status. Known: "
        f"{', '.join(s.id for s in load_states(path))}")


def is_terminal(status_id: str, path: str | Path = DEFAULT_PATH) -> bool:
    return next(s.terminal for s in load_states(path) if s.id == status_id)


def rank(status_id: str, path: str | Path = DEFAULT_PATH) -> int:
    return next(s.order for s in load_states(path) if s.id == status_id)


def is_regression(old: str, new: str, path: str | Path = DEFAULT_PATH) -> bool:
    """Would this transition move a row backwards through the lifecycle?

    Not an error — a genuine re-open happens, and the human owns the column.
    It is worth reporting, because much more often it is a typo that would
    otherwise corrupt the advance-rate numbers months from now.
    """
    if old == new:
        return False
    if is_terminal(old, path) and not is_terminal(new, path):
        return True
    return rank(new, path) < rank(old, path)


def labels(path: str | Path = DEFAULT_PATH) -> list[str]:
    """Canonical labels, in lifecycle order — the sheet's dropdown."""
    return [s.label for s in load_states(path)]
