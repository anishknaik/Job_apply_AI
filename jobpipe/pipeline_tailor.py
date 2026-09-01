"""The tailoring run (Phase 2 spec §1, §9.10).

Two stages, deliberately separated by a human decision:

  queued  -> resume generated -> drafted      (cheap, batched, speculative)
  applying -> cover letter generated          (expensive, one at a time)

§1's reasoning: a resume is bullet selection from a fixed bank — one
constrained Flash-Lite call, no prose, no grounding check — so generating one
for every queued role costs a fraction of a cent and can be wrong without
consequence. A cover letter is generated prose on the expensive model plus a
verification pass, and it is worthless until the human has actually committed
to applying. Generating letters eagerly would roughly triple the phase's cost
for documents that mostly never get sent.

Batching matters (§1.1): the queue will regularly exceed what gets applied to,
so one failure must not cost the run the rest of the queue — the same rule
Phase 1 applies to a failing company in a fetch.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import bullets as bullets_mod
from . import db, grounding, letters, resume, scoring, tailor

log = logging.getLogger(__name__)

OUTPUT_ROOT = Path("output/applications")


@dataclass
class TailorRun:
    generated: int = 0
    skipped: int = 0
    failed: int = 0
    blocked: int = 0
    errors: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0,
                                                 "cached": 0})

    def add_usage(self, usage: dict) -> None:
        for key in self.usage:
            self.usage[key] += usage.get(key, 0)


def resume_version(variant: str, bullet_ids: list[str], when: str) -> str:
    """A short, stable identifier written back to the sheet (§6).

    Includes a hash of the selected bullets so two applications to the same
    company are distinguishable, and so a regenerated resume with different
    content gets a different version even on the same day.
    """
    import hashlib

    digest = hashlib.sha256("|".join(bullet_ids).encode()).hexdigest()[:6]
    return f"{variant}-{when[:10]}-{digest}"


def application_dir(row, root: Path = OUTPUT_ROOT) -> Path:
    return Path(root) / letters.slug(row)


def render_and_record(bank, row, conn, selected, variant, *,
                      skill_order=None, posting_skills=None, model=None,
                      root: Path = OUTPUT_ROOT) -> dict:
    """Render chosen bullets to a one-page PDF and record the document row.

    Split out of generate_resume because the two halves have different
    callers. Selection is a model call; this half is not, and this half is the
    whole of what `render_resume` needs over MCP (PHASE5_PLAN §4.2) — there
    the client has already decided, so a provider in this path would mean a
    second model choosing after the first one already chose.

    The fabrication firewall is unaffected by the split: `Bank.select` gates
    the ids upstream of here and raises on an unknown one, which is the check
    §9.5.1 says carries the guarantee on its own once the schema enum is gone.
    """
    folder = application_dir(row, root)
    rendered = resume.render(bank, selected, folder / "resume.pdf",
                             variant=variant, skill_order=skill_order,
                             emphasise=posting_skills)

    when = db.utcnow()
    version = resume_version(variant, rendered.bullet_ids, when)
    db.insert_document(conn, row["id"], "resume",
                       archetype=variant,
                       bullet_ids=rendered.bullet_ids,
                       generated_path=str(rendered.path),
                       model=model, generated_at=when)
    return {"rendered": rendered, "version": version, "folder": folder}


def generate_resume(provider, bank, row, sel_system, conn,
                    root: Path = OUTPUT_ROOT) -> dict:
    """Select bullets, render, verify, record. Raises on failure."""
    selection = tailor.select_bullets(provider, bank, row, sel_system)
    result = render_and_record(bank, row, conn, selection["bullets"],
                               selection["variant"],
                               skill_order=selection["skill_order"],
                               posting_skills=selection["posting_skills"],
                               model=selection["model"], root=root)
    return {"selection": selection, **result}


def generate_cover_letter(provider, bank, row, letter_system, ground_system,
                          conn, prose_model, root: Path = OUTPUT_ROOT,
                          selected=None) -> dict:
    """Generate a letter and verify it. A failed check BLOCKS (§2.2).

    The letter is still written to disk when grounding fails, and the failure
    is reported alongside it. Withholding the file would hide what went wrong;
    §2.2 says surface and stop, not surface and delete.
    """
    result = letters.generate_letter(provider, bank, row, letter_system,
                                     prose_model, selected=selected)
    folder = application_dir(row, root)
    path = letters.save_letter(result["text"], folder / "cover_letter.md")

    # The row is written and committed BEFORE the check runs. `grounding.check`
    # raises on a refusal and on zero extracted claims — both deliberately
    # treated as failures — and this function's caller commits only on the way
    # out, so under the old order a raised check discarded the document row
    # entirely. A missing row and a row with a NULL verdict both refuse at
    # copy_for_editing, but only the second says a draft exists and was never
    # cleared. It is also the order the MCP tools use, where save_cover_letter
    # and check_grounding are separate calls.
    document_id = db.insert_document(conn, row["id"], "cover_letter",
                                     generated_path=str(path),
                                     model=prose_model,
                                     content=result["text"])
    conn.commit()

    check = grounding.check(provider, bank, result["text"],
                            _description(row), ground_system)
    db.record_grounding(conn, document_id, check)

    return {"letter": result, "grounding": check, "path": path,
            "folder": folder, "document_id": document_id}


def _description(row) -> str:
    try:
        return str(row["description"] or "")
    except (KeyError, IndexError):
        return ""


def build_prompts(bank):
    """The three cacheable prefixes, built once per run rather than per item."""
    profile = scoring.load_profile()
    return {
        "selection": tailor.build_system_prompt(bank, profile),
        "letter": letters.build_system_prompt(bank, profile,
                                              letters.load_exemplars()),
        "grounding": grounding.build_system_prompt(bank, profile),
    }


def load_bank():
    return bullets_mod.load_bank()
