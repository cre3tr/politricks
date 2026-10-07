"""Shared scaffolding for every `--self-test` (lint, review, intake, generate).

Why this file exists: three self-tests each carried their own copy of the
PASS/FAIL printer, and two of them (lint, review) built their test bill by
loading the REAL finance-act-2026 and patching only a few clauses. That made
the tests depend on the state of the real data: the day a human reviewed c3
or the real summary stopped being a sample, "exactly c3 and c4 are drafts"
would fail on a perfectly healthy repo. Two helpers fix both:

- `Checker` is the one PASS/FAIL printer.
- `fixture_bill` builds the one test bill, and resets EVERYTHING a human or
  Claude could have changed, so it comes out identical today and after every
  real clause has been explained and reviewed.

Nothing here writes to disk. The bill is a deep copy held in memory.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Bill, GroupEntry, Review, Summary, Tri, bill_path, load_bill

# The two clauses the recorded fixture (pipeline/tests/fixtures/batch_results
# .jsonl) turns into drafts. Every self-test that uses the fixture leans on them.
FIXTURE_DRAFT_IDS = ("c3", "c4")

# The clause that stands in for a hand-written sample in the test bill.
FIXTURE_SAMPLE_ID = "c1"

# The test bill is a copy of this real bill (its clause text is real, everything
# generated or reviewed on top of it is replaced).
FIXTURE_BILL_ID = "finance-act-2026"

# Fixed placeholder texts. They carry no banned phrase, on purpose: a lint run
# over the untouched fixture must find nothing.
SAMPLE_TEXT = Tri(en="Sample text.", sw="Maandishi ya mfano.", sheng="Sample text bro.")
GROUP_TEXT = Tri(
    en="Traders: this bill changes how trade income is described.",
    sw="Wafanyabiashara: mswada huu unabadilisha maelezo ya mapato ya biashara.",
    sheng="Wafanyibiashara: bill hii inabadilisha maelezo ya mapato ya biz.",
)


class Checker:
    """One PASS/FAIL printer for all self-tests.

    Use it like a function: `check("label", condition, "optional detail")`.
    `check.ok` stays True until the first failure; `check.finish()` prints a
    count line made from the checks actually run (so the number comes from the
    run, not from anyone counting PASS lines by eye) and returns `ok`.
    """

    def __init__(self) -> None:
        self.ok = True
        self.passed = 0
        self.failed = 0

    def __call__(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"PASS: {label}")
        else:
            self.failed += 1
            self.ok = False
            # One line per check, even when the detail is multi-line program output.
            detail = " / ".join(detail.splitlines())
            print(f"FAIL: {label}" + (f" ({detail})" if detail else ""))

    def finish(self) -> bool:
        print(f"checks: {self.passed} passed, {self.failed} failed")
        return self.ok


def reset_bill(repo_root: Path) -> Bill:
    """finance-act-2026 in memory with every generated/reviewed field reset.

    After this the bill has: real clause text, NO explanations, every clause a
    plain draft, one sample clause (c1), a sample summary, and one draft group
    entry (trader, c3). The recorded fixture is NOT applied yet.
    """
    bill = load_bill(bill_path(repo_root, FIXTURE_BILL_ID)).model_copy(deep=True)
    for clause in bill.clauses:
        clause.explanation = None
        clause.review = Review(status="draft")
        clause.neutral_check = None
        clause.groups = []
        clause.suggested_articles = []

    sample = next(clause for clause in bill.clauses if clause.id == FIXTURE_SAMPLE_ID)
    sample.explanation = SAMPLE_TEXT.model_copy()
    sample.review = Review(status="sample")

    bill.summary = Summary(text=SAMPLE_TEXT.model_copy(), review=Review(status="sample"))
    bill.groups = [
        GroupEntry(group="trader", text=GROUP_TEXT.model_copy(), clause_ids=["c3"], review=Review(status="draft"))
    ]
    return bill


def fixture_bill(repo_root: Path) -> Bill:
    """The test bill: `reset_bill` plus the recorded batch fixture applied.

    The fixture holds clause results only (c3 and c4 applied; c5 refused, c6
    errored, c7 invalid), no summary result, so `generate.apply` leaves the
    sample summary and the draft group alone. Result, every time:

        c1  sample      c3, c4  draft (from the fixture)      everything else  no explanation
        summary  sample      groups  one draft entry (trader)
    """
    from . import generate  # imported here: generate itself imports this module

    bill = reset_bill(repo_root)
    fixture = repo_root / "pipeline" / "tests" / "fixtures" / "batch_results.jsonl"
    lines = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
    generate.apply(bill, [generate.parse_batch_line(line) for line in lines])
    return bill
