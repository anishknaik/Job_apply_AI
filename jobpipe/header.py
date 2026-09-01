"""The resume header, generated from `master_profile.md` §12.

The fabrication firewall covers bullet TEXT and stops there. Ids are
enum-constrained in the selection schema and `Bank.select` re-validates, so no
model can put a sentence in the experience section that a human did not write.
`meta:`, `education:` and each role's company, title and dates are not
protected at all — they are trusted YAML, hard-required by `load_bank()` and
read straight into the PDF. What linked them to a source document was a
convention in the bank's own header, addressed to a human editor.

So the gap was never that a model could reach them. It is that a stale or
mistyped hand edit reaches every generated resume with nothing in between, and
the two files agreed only because somebody kept them in sync.

This closes it by generation rather than by assertion. §12 is the source; the
header is emitted from it into its own file and merged at `load_bank()`.
**Nothing is ever written into `bullets.yaml`**, which stays entirely
hand-edited — the generated file is separate precisely so a regeneration can
never touch a human's bullet text.

What §12 deliberately does NOT carry, and this therefore does not generate:
`headline`, a role's `kind` and `location`, and the per-group `stack:` lines.
Those are ATS keyword tuning and layout decisions rather than facts about him,
and they change for reasons `master_profile.md` knows nothing about.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

PROFILE_PATH = Path("config/master_profile.md")
HEADER_PATH = Path("config/header.generated.yaml")

# The §12 fenced block. Anchored on the heading so a `yaml` fence added
# earlier in the file — §11's calibration examples are the live risk — cannot
# be picked up instead.
_BLOCK = re.compile(r"##\s*12\.\s*Structured identity.*?```yaml\n(.*?)```",
                    re.S)

IDENTITY_KEYS = ("name", "email", "phone", "linkedin", "location")
EDUCATION_KEYS = ("school", "degree", "date", "coursework")
ROLE_KEYS = ("company", "title", "dates")

BANNER = """\
# GENERATED — do not edit. Regenerate with `python -m jobpipe.cli header`.
#
# Source: config/master_profile.md §12. Edit there, regenerate here.
# `pytest tests/test_header.py` fails when this file and §12 disagree, so a
# profile edit that is never regenerated is caught rather than shipped.
#
# Merged into the bank at load_bank(). Carries only facts about him: name,
# contact, degree and each role's company/title/dates. `headline`, a role's
# `kind` and `location`, and the per-group `stack:` lines are NOT here and
# stay hand-edited in bullets.yaml — they are keyword tuning and layout, not
# facts, and §12 says so explicitly.
"""


class HeaderError(RuntimeError):
    pass


def extract(profile_text: str) -> dict:
    """Pull §12's YAML block out of the profile and shape it for merging.

    Raises rather than returning a partial header: a missing key here would
    silently leave the bank's hand-written value in place, which is exactly
    the drift this module exists to remove.
    """
    import yaml

    match = _BLOCK.search(profile_text)
    if not match:
        raise HeaderError(
            "master_profile.md has no '## 12. Structured identity' section "
            "with a ```yaml block. That block is the source of truth for the "
            "resume header; without it there is nothing to generate from.")

    block = yaml.safe_load(match.group(1))
    if not isinstance(block, dict):
        raise HeaderError("§12's yaml block did not parse to a mapping")

    for section in ("identity", "education", "experience"):
        if section not in block:
            raise HeaderError(f"§12 is missing the {section!r} key")

    meta = {}
    for key in IDENTITY_KEYS:
        if key not in block["identity"]:
            raise HeaderError(f"§12 identity is missing {key!r}")
        meta[key] = block["identity"][key]

    education = {}
    for key in EDUCATION_KEYS:
        if key not in block["education"]:
            raise HeaderError(f"§12 education is missing {key!r}")
        value = block["education"][key]
        # The coursework list is folded across lines in the profile for
        # legibility. The bank stores it as one line, and a stray newline
        # would render as a gap in the PDF.
        education[key] = " ".join(str(value).split())

    roles = {}
    for entry in block["experience"]:
        rid = entry.get("id")
        if not rid:
            raise HeaderError("a §12 experience entry has no id")
        if rid in roles:
            raise HeaderError(f"duplicate §12 experience id {rid!r}")
        for key in ROLE_KEYS:
            if key not in entry:
                raise HeaderError(f"§12 experience {rid!r} is missing {key!r}")
        roles[rid] = {key: entry[key] for key in ROLE_KEYS}

    return {"meta": meta, "education": education, "roles": roles}


def render(header: dict) -> str:
    """The generated file's text, banner included."""
    import yaml

    body = yaml.safe_dump(header, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=100)
    return BANNER + "\n" + body


def write(profile_path: Path | str = PROFILE_PATH,
          out_path: Path | str = HEADER_PATH) -> tuple[Path, bool]:
    """Regenerate the header file. Returns (path, changed)."""
    profile_path, out_path = Path(profile_path), Path(out_path)
    if not profile_path.exists():
        raise HeaderError(
            f"{profile_path} not found. It is gitignored — it carries contact "
            f"details and visa status — so a fresh checkout has no copy and "
            f"the header cannot be generated until it is restored.")

    text = render(extract(profile_path.read_text(encoding="utf-8")))
    before = (out_path.read_text(encoding="utf-8")
              if out_path.exists() else None)
    if before == text:
        return out_path, False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path, True


def load(path: Path | str = HEADER_PATH) -> dict | None:
    """The generated header, or None if it has not been generated.

    None is not an error. `master_profile.md` is gitignored, so a fresh
    checkout can neither generate this file nor be expected to have it, and
    the bank's own hand-written header still renders. Absence means "no
    generated source available", which is where the project stood before this
    module existed.
    """
    import yaml

    path = Path(path)
    if not path.exists():
        return None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def merge_into(raw: dict, header: dict | None) -> dict:
    """Overlay the generated header onto a parsed `bullets.yaml`.

    Mutates and returns `raw`. Only the generated keys are touched — anything
    §12 declines to own is left exactly as the human wrote it, and a role that
    §12 does not mention (there are none today, but projects are roles too in
    the bank's shape) passes through untouched.
    """
    if not header:
        return raw

    raw.setdefault("meta", {}).update(header.get("meta") or {})
    raw.setdefault("education", {}).update(header.get("education") or {})

    by_id = header.get("roles") or {}
    for role in raw.get("roles") or ():
        fields = by_id.get(role.get("id"))
        if fields:
            role.update(fields)
    return raw


def diff(profile_path: Path | str = PROFILE_PATH,
         header_path: Path | str = HEADER_PATH) -> list[str]:
    """Every field where the generated file disagrees with §12.

    The regenerate-and-compare that replaces keeping two files in sync by
    hand. Empty list means they agree.
    """
    fresh = extract(Path(profile_path).read_text(encoding="utf-8"))
    stored = load(header_path)
    if stored is None:
        return [f"{header_path} has not been generated"]

    out = []
    for section in ("meta", "education"):
        for key, value in fresh[section].items():
            if (stored.get(section) or {}).get(key) != value:
                out.append(f"{section}.{key}")
    for rid, fields in fresh["roles"].items():
        for key, value in fields.items():
            if ((stored.get("roles") or {}).get(rid) or {}).get(key) != value:
                out.append(f"roles.{rid}.{key}")
    for rid in (stored.get("roles") or {}):
        if rid not in fresh["roles"]:
            out.append(f"roles.{rid} is not in §12")
    return out
