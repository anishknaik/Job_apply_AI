"""The runner's settings: `config/apply.yaml`, gitignored, default off.

Auto-submit lives in a file rather than a flag or a route on purpose. A CLI
flag is one typo from on; a web route can be POSTed by any site (see
`web.py`'s origin checks); a file that is not in the repository has to be
written by Anish, on his machine, by hand. Everything else in here is a
default that the example file restates, so a missing file is a usable file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("config/apply.yaml")
EXAMPLE_PATH = Path("config/apply.example.yaml")


class ApplyConfigError(ValueError):
    """apply.yaml says something the runner must not guess at."""


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path = Path(".jobpipe-browser")
    headed: bool = True
    page_timeout: float = 30.0
    confirmation_timeout: float = 45.0


@dataclass(frozen=True)
class ApplyConfig:
    auto_submit: bool = False
    max_auto_submits_per_day: int = 5
    min_minutes_between: int = 10
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    output_dir: Path = Path("output/apply")
    source: str = "defaults"


def _flag(raw: dict, key: str, default: bool) -> bool:
    """A real boolean or nothing. YAML's `yes`/`on` parse as booleans too,
    but a quoted "true" is a string, and a string must not read as on."""
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ApplyConfigError(
            f"{key} must be true or false, not {value!r}")
    return value


def _count(raw: dict, key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApplyConfigError(f"{key} must be a whole number, not {value!r}")
    return value


def load(path: str | Path | None = None) -> ApplyConfig:
    """Read apply.yaml. Absent -> defaults, auto-submit off."""
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        return ApplyConfig()
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ApplyConfigError(f"{path} must be a mapping")
    browser_raw = raw.get("browser") or {}
    if not isinstance(browser_raw, dict):
        raise ApplyConfigError("browser must be a mapping")
    browser = BrowserConfig(
        profile_dir=Path(browser_raw.get("profile_dir", ".jobpipe-browser")),
        headed=_flag(browser_raw, "headed", True),
        page_timeout=float(browser_raw.get("page_timeout", 30)),
        confirmation_timeout=float(browser_raw.get("confirmation_timeout", 45)),
    )
    return ApplyConfig(
        auto_submit=_flag(raw, "auto_submit", False),
        max_auto_submits_per_day=_count(raw, "max_auto_submits_per_day", 5),
        min_minutes_between=_count(raw, "min_minutes_between", 10),
        browser=browser,
        output_dir=Path(raw.get("output_dir", "output/apply")),
        source=str(path),
    )
