#!/usr/bin/env python3
"""Generate few-shot calibration examples from Anish's own hand scores.

`master_profile.md` §11 has asked for this since Phase 1: "Draft set — revise
after hand-scoring 20 real postings, then paste into the scoring prompt as
few-shot examples. These exist to stop scores clustering at 6-8." 65 postings
have been hand-scored since; the two were never connected.

WHY HIS SCORES AND NOT THE MODEL'S REASONING. Calibration means agreement with
Anish. Feeding the model its own previous reasoning would teach it to
reproduce its previous errors - and the 3.1 run scored Scale AI 1 where he
said 6, and missed both positives in the holdout. The target is his judgement,
not any model's.

THE HOLDOUT IS EXCLUDED BY CONSTRUCTION. calibration_holdout.csv overlaps
neither source set, and tuning on the set you measure on turns a holdout into
a training set. calibrate.py already states the rule; this enforces it.

Stratified across the apply range rather than taking everything, because §11's
stated purpose is coverage of the scale, not volume. The low band matters most
right now: demoting the non_technical short-circuit removed the only floor the
rubric had, and the 2.5 run over-scored 12 of 19 postings by a mean of +2.3.
Real examples of what a 1 and a 3 look like are what put a floor back.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobpipe import calibrate  # noqa: E402

SOURCES = [Path("config/calibration_notes.csv"), Path("config/calibration.csv")]
HOLDOUT = Path("config/calibration_holdout.csv")
OUT = Path("config/calibration_examples.md")

#: How many per apply-score band. Two at the extremes because those are the
#: bands the model drifts away from; one in the crowded middle.
PER_BAND = {0: 2, 1: 2, 2: 2, 3: 2, 4: 2, 5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1}


def main() -> int:
    holdout_ids = {str(r.get("posting_id", "")).strip()
                   for r in calibrate.read_rows(HOLDOUT)}

    rows, seen = [], set()
    for source in SOURCES:
        if not source.exists():
            print(f"  ! missing {source}, skipping")
            continue
        for row in calibrate.load_hand_scores(source):
            pid = str(row.get("posting_id", "")).strip()
            if pid in holdout_ids:
                print(f"  - excluded (holdout): {row.get('company')}")
                continue
            if pid in seen:
                continue
            seen.add(pid)
            row["_source"] = source.name
            rows.append(row)

    # Prefer entries that carry both axes and a longer note: more reasoning per
    # token, and a fit/apply pair teaches the gap between them.
    rows.sort(key=lambda r: (r["my_fit"] is None,
                             -len((r.get("notes") or "").split())))

    chosen, taken = [], {}
    for row in rows:
        band = row["my_apply"]
        if band is None:
            continue
        if taken.get(band, 0) >= PER_BAND.get(band, 1):
            continue
        taken[band] = taken.get(band, 0) + 1
        chosen.append(row)
    chosen.sort(key=lambda r: (r["my_apply"], r["my_fit"] or 0))

    lines = [
        "## Calibrated examples — real postings, scored by hand",
        "",
        "These are the candidate's OWN scores on real postings, with his own",
        "reasoning. They are the calibration target. Where one of these",
        "conflicts with a general rule above, **these win** — the rules are an",
        "approximation of this judgement, not the other way round.",
        "",
        "`fit` is fit on paper. `apply` is whether it is worth applying anyway.",
        "A missing `fit` means it was not recorded separately; those entries",
        "were scored on a should-I-apply basis.",
        "",
    ]
    for row in chosen:
        fit = row["my_fit"]
        note = " ".join((row.get("notes") or "").split())
        head = f"apply {row['my_apply']}"
        if fit is not None:
            head = f"fit {fit}, {head}"
        lines.append(f"**{row['company']} — {row['title']}**  ({head})")
        lines.append(f"> {note}")
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    span = sorted({r["my_apply"] for r in chosen})
    print(f"\nwrote {len(chosen)} examples to {OUT}")
    print(f"apply scores covered: {span}")
    print(f"words: {len(OUT.read_text(encoding='utf-8').split()):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
