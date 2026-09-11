"""Short answers on application forms (PHASE5_PLAN §6.1, Track C).

An open-ended box on a form — "describe your experience working with
customers" — is free prose making claims about him, exactly like a cover
letter, so it gets the same discipline: Claude drafts, the server stores every
save as its own revision, and Gemini checks it before Anish sees it. §6.1:
"Do not let a 200-character box become the one place fabrication is
unchecked."

A form asks several questions, and `documents` supersedes per posting and
doc_type. So the question is part of the key, stored in `documents.question`,
rather than something a session has to remember: answering one question must
never retire the answer to another.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from . import header

DOC_TYPE = "short_answer"

ANSWERS_PATH = Path("config/answers.yaml")
EXAMPLE_PATH = Path("config/answers.example.yaml")
PROFILE_PATH = header.PROFILE_PATH

# Filled by hand on every form, whatever any file says (§6.1 "leave for
# Anish"). Returned with every view so the filler never has to remember it.
NEVER_FILL = (
    "salary or compensation expectations",
    "SSN, passport, or any other government identifier",
    "payment or bank details",
    "attestation, certification or signature checkboxes",
)


class AnswersError(RuntimeError):
    pass


def normalise_question(text) -> str:
    """The slot key. Whitespace-insensitive and nothing more.

    The same question pasted with a different line break has to land in the
    same slot, or a re-save leaves two live answers to one question. Two
    differently worded questions stay two slots: deciding that they "mean the
    same thing" is how one answer silently replaces another.
    """
    return " ".join(str(text or "").split())


def _option(key: str, item) -> str:
    """One self-ID entry: the form's exact option text, and nothing else."""
    if isinstance(item, bool):
        # YAML 1.1 reads an unquoted Yes or No as true/false. The form's
        # option says "No"; a boolean cannot say which spelling it was.
        raise AnswersError(
            f"self_identification.{key} holds a true/false value, not text. "
            f"YAML reads an unquoted Yes or No as a boolean - quote it, "
            f'e.g. ["No"].')
    if not isinstance(item, str) or not item.strip():
        raise AnswersError(
            f"self_identification.{key} must list the form's exact option "
            f"text; found {type(item).__name__}.")
    return item


def load(path: Path | str = ANSWERS_PATH) -> dict:
    """Read and validate the answers file. Raises rather than guessing.

    Self-ID values must be lists of text so the filler can SELECT an option by
    exact match (§6.1.1 constraint 4). An empty list, like an empty logistics
    value, means "leave it for Anish".
    """
    import yaml

    path = Path(path)
    if not path.exists():
        raise AnswersError(
            f"{path} does not exist. Copy {EXAMPLE_PATH} to it and fill it "
            f"in; it is gitignored.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise AnswersError(f"{path} did not parse to a mapping")

    self_id = {}
    for key, value in (raw.get("self_identification") or {}).items():
        if value is None:
            value = []
        if not isinstance(value, list):
            raise AnswersError(
                f"self_identification.{key} must be a list of the form's "
                f"exact option text, even for one answer: [\"...\"].")
        self_id[key] = [_option(key, item) for item in value]

    logistics = {}
    for key, value in (raw.get("logistics") or {}).items():
        if isinstance(value, bool):
            raise AnswersError(
                f"logistics.{key} is true/false, not text; quote it.")
        if isinstance(value, datetime.date):
            value = value.isoformat()   # a bare 2027-06-30 parses as a date
        logistics[key] = "" if value is None else str(value)

    return {"self_identification": self_id, "logistics": logistics}


def form_view(profile_text: str, answers: dict) -> dict:
    """Everything a form is filled FROM, in one mapping.

    §12 is read live rather than copied, so there is no third hand-kept copy
    of his contact details to drift (§6.1.1 constraint 1). Self-ID comes only
    from the answers file, never from the profile. This is content, not
    conduct: how to use it lives in CLAUDE.md and the server instructions.
    """
    block = header.structured_block(profile_text)
    education = {k: " ".join(str(v).split())
                 for k, v in (block.get("education") or {}).items()}
    work = block.get("work_authorization") or {}

    missing = [f"self_identification.{k}"
               for k, v in answers["self_identification"].items() if not v]
    missing += [f"logistics.{k}"
                for k, v in answers["logistics"].items() if not v]
    if not work:
        missing.append("work_authorization (not in master_profile.md §12)")

    return {
        "identity": dict(block.get("identity") or {}),
        "education": education,
        "work_authorization": work,
        "experience": list(block.get("experience") or []),
        "self_identification": answers["self_identification"],
        "logistics": answers["logistics"],
        "leave_for_anish": missing,
        "never_fill": list(NEVER_FILL),
    }
