"""Apply hand-written SAMPLE explanations (slice 2a), before an API key exists.

`politricks samples <bill-id>` reads `pipeline/samples/<bill-id>.yaml` — plain
English/Kiswahili/Sheng text a human typed in — and writes it onto the bill's
summary and a few named clauses, exactly as `generate.py` will later write
Claude's output, except the review status is "sample" instead of "draft".

Why this file exists at all: slice 2a proves the whole site (cards, language
toggle, badges) works end to end *before* the Anthropic pipeline is wired up,
using text a human already trusts. Slice 2b's `generate` overwrites "sample"
items with real Claude drafts; this command never touches a clause a human has
already reviewed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Bill, NeutralCheck, Review, Tri, bill_path, load_bill, save_bill

SAMPLE_NOTE = "Hand-written sample"


class SamplesError(Exception):
    """Raised when the sample YAML names a clause that doesn't exist."""


def load_samples(repo_root: Path, bill_id: str) -> dict:
    path = repo_root / "pipeline" / "samples" / f"{bill_id}.yaml"
    if not path.exists():
        raise SamplesError(f"no sample file at {path.relative_to(repo_root).as_posix()}")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def apply_samples(bill: Bill, data: dict) -> int:
    """Write sample text onto `bill` in place. Returns the number of clauses applied.

    Never overwrites an item already marked "reviewed" — a human's edit always
    wins over a placeholder, even a fresh one. Raises SamplesError if the YAML
    names a clause id that isn't in the bill (a typo we want to catch loudly,
    not silently skip).
    """
    applied = 0

    # --- summary ---------------------------------------------------------
    summary_data = data.get("summary")
    if summary_data:
        if bill.summary is not None and bill.summary.review.status == "reviewed":
            print("skip summary: already reviewed")
        else:
            bill.summary = _summary_from(summary_data)

    # --- clauses -----------------------------------------------------------
    clauses_data = data.get("clauses") or {}
    by_id = {clause.id: clause for clause in bill.clauses}
    for clause_id, tri_data in clauses_data.items():
        clause = by_id.get(clause_id)
        if clause is None:
            raise SamplesError(f"sample file names clause '{clause_id}', which is not in the bill")
        if clause.review.status == "reviewed":
            print(f"skip {clause_id}: already reviewed")
            continue
        clause.explanation = _tri_from(tri_data)
        clause.review = Review(status="sample")
        clause.neutral_check = NeutralCheck(ok=True, note=SAMPLE_NOTE)
        applied += 1

    return applied


def _tri_from(data: dict) -> Tri:
    return Tri(en=data["en"].strip(), sw=data["sw"].strip(), sheng=data["sheng"].strip())


def _summary_from(data: dict):
    from .models import Summary

    return Summary(text=_tri_from(data), review=Review(status="sample"))


def run(repo_root: Path, bill_id: str) -> None:
    data = load_samples(repo_root, bill_id)
    path = bill_path(repo_root, bill_id)
    bill = load_bill(path)
    applied = apply_samples(bill, data)
    save_bill(bill, path)
    print(f"applied {applied} clauses + summary (status: sample)")
