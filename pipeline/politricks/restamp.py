"""One-time migration: re-stamp AI drafts after the clause fingerprint moved to v2.

Why this file exists (slice 10, 2026-10-23). A clause's review fingerprint now
covers its `suggested_articles` (`models.current_sha256`, clause version v2).
Every AI draft already in the bills is stamped with the v1 fingerprint, and
`models.is_hand_edited` reads "stamp does not match the content" as "a human
edited this", which protects the draft from `generate` for good. Left alone,
the move to v2 would silently turn every untouched AI draft into a
"hand-edited" one.

What it does, per clause that has an explanation, under the bill lock:

- an AI draft whose stamp equals its v1 fingerprint (so it is exactly what
  `generate` wrote, untouched since) is re-stamped with its v2 fingerprint
  and stays an AI draft;
- a draft stamped "hand-edited", or whose stamp matches neither version (a
  human changed the JSON), is left alone: still hand-edited;
- a REVIEWED clause is left alone. Its stamp is v1, so it now reads "changed
  since review" and comes back through `politricks review <id> --flagged`.
  This command never makes anything reviewed and never keeps a review alive;
- a draft with no stamp (null) and a sample are left alone: neither is
  protected, so there is nothing to restore.

Summaries, group entries and civic blocks are not touched: their fingerprints
did not change (hashing.py keeps them on v1), so nothing of theirs needs it.

Safe to run twice: the second run finds nothing to do and does not write the
file at all. `--dry-run` prints the same report and writes nothing.

    uv run --project pipeline politricks restamp <bill-id> [--dry-run]
    uv run --project pipeline politricks restamp --self-test
"""

from __future__ import annotations

import contextlib
import io
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .hashing import sha256_file
from .models import (
    HAND_EDITED,
    Bill,
    Clause,
    Review,
    Tri,
    bill_path,
    changed_since_review,
    current_sha256,
    draft_review,
    is_hand_edited,
    is_protected,
    is_reviewed,
    is_v1_stamped,
    legacy_clause_sha256_v1,
    load_bill,
    save_bill,
)
from .reviewlock import BillLocked, bill_lock
from .selftest import FIXTURE_BILL_ID, Checker, reset_bill


@dataclass
class Plan:
    """Where every clause with an explanation stands, by what restamp does."""

    restamp: list[str] = field(default_factory=list)  # AI drafts on a v1 stamp: re-stamped
    already_current: list[str] = field(default_factory=list)  # draft, stamp matches v2 already
    hand_edited: list[str] = field(default_factory=list)  # draft, "hand-edited" literal
    mismatched: list[str] = field(default_factory=list)  # draft, stamp matches neither version
    reviewed_stale: list[str] = field(default_factory=list)  # reviewed, v1 stamp: now "changed"
    reviewed_current: list[str] = field(default_factory=list)  # reviewed, matches v2
    reviewed_other: list[str] = field(default_factory=list)  # reviewed, stamp matches neither
    unstamped: list[str] = field(default_factory=list)  # draft with no stamp
    other: list[str] = field(default_factory=list)  # samples


def plan(bill: Bill) -> Plan:
    """Classify every clause with an explanation. Pure: changes nothing."""
    result = Plan()
    for clause in bill.clauses:
        if clause.explanation is None:
            continue
        review = clause.review
        if review.status == "reviewed":
            if is_reviewed(clause):
                result.reviewed_current.append(clause.id)
            elif is_v1_stamped(clause):
                result.reviewed_stale.append(clause.id)
            else:
                result.reviewed_other.append(clause.id)
        elif review.status == "draft":
            if review.sha256 is None:
                result.unstamped.append(clause.id)
            elif review.sha256 == current_sha256(clause):
                result.already_current.append(clause.id)
            elif is_v1_stamped(clause):
                result.restamp.append(clause.id)
            elif review.sha256 == HAND_EDITED:
                result.hand_edited.append(clause.id)
            else:
                result.mismatched.append(clause.id)
        else:
            result.other.append(clause.id)
    return result


def apply_plan(bill: Bill, todo: Plan) -> int:
    """Re-stamp exactly the clauses in `todo.restamp`. Nothing else changes."""
    by_id = {clause.id: clause for clause in bill.clauses}
    for clause_id in todo.restamp:
        clause = by_id[clause_id]
        clause.review = draft_review(clause)
    return len(todo.restamp)


def _ids(ids: list[str]) -> str:
    return f" ({', '.join(ids)})" if ids and len(ids) <= 12 else ""


def report(bill_id: str, todo: Plan) -> None:
    print(f"{bill_id}:")
    print(f"  re-stamped as AI drafts (v1 stamp, untouched since): {len(todo.restamp)}{_ids(todo.restamp)}")
    print(f"  AI drafts already on the v2 stamp: {len(todo.already_current)}")
    print(f"  left hand-edited ('hand-edited' stamp): {len(todo.hand_edited)}{_ids(todo.hand_edited)}")
    print(f"  left hand-edited (stamp matches neither version, edited in the JSON): {len(todo.mismatched)}{_ids(todo.mismatched)}")
    print(f"  left reviewed, now 'changed since review' (re-accept with `review --flagged`): "
          f"{len(todo.reviewed_stale)}{_ids(todo.reviewed_stale)}")
    if todo.reviewed_current:
        print(f"  reviewed and current on v2: {len(todo.reviewed_current)}{_ids(todo.reviewed_current)}")
    if todo.reviewed_other:
        print(f"  reviewed, changed since review for another reason: {len(todo.reviewed_other)}{_ids(todo.reviewed_other)}")
    if todo.unstamped or todo.other:
        print(f"  untouched, nothing to restore: {len(todo.unstamped)} unstamped draft(s), {len(todo.other)} sample(s)")


def run(repo_root: Path, bill_id: str, dry_run: bool = False) -> int:
    """Re-stamp one bill's v1 AI drafts. Returns the exit code."""
    path = bill_path(repo_root, bill_id)
    try:
        with bill_lock(path):
            bill = load_bill(path)
            todo = plan(bill)
            report(bill_id, todo)
            if not todo.restamp:
                print("  nothing to re-stamp: file not written")
                return 0
            if dry_run:
                print(f"  --dry-run: would re-stamp {len(todo.restamp)} draft(s); file not written")
                return 0
            apply_plan(bill, todo)
            save_bill(bill, path)
            print(f"  wrote {path.name}")
            return 0
    except BillLocked as error:
        print(f"FAILED: {error} Nothing was changed.")
        return 1


# --------------------------------------------------------------------------
# Self-test: a temp copy of the test bill, never a real one.
# --------------------------------------------------------------------------


def _give_words(clause: Clause, words: str) -> None:
    clause.explanation = Tri(en=words, sw=words, sheng=words)


def self_test(repo_root: Path) -> bool:
    check = Checker()
    real_files = sorted((repo_root / "src" / "data" / "bills").glob("*.json"))
    real_before = {file.name: sha256_file(file) for file in real_files}

    # A bill with one clause in every state the migration has to tell apart.
    # Each stamp is built the way the real data got it: the AI draft by
    # `generate` under v1 (legacy_clause_sha256_v1), the others as named.
    bill = reset_bill(repo_root)
    wanted = ["c3", "c4", "c5", "c6", "c7", "c8", "c9"]
    clauses = {clause.id: clause for clause in bill.clauses if clause.id in wanted}
    bill.clauses = [clause for clause in bill.clauses if clause.id in wanted or clause.id == "c1"]
    for clause_id, clause in clauses.items():
        _give_words(clause, f"words for {clause_id}")
    ai, edited, reviewed, forged, current, unstamped, ai_articles = (clauses[c] for c in wanted)
    ai_articles.suggested_articles = [201, 10]  # content a v1 stamp could never see

    for clause in (ai, reviewed, forged, edited, ai_articles):
        clause.review = Review(status="draft", sha256=legacy_clause_sha256_v1(clause))
    reviewed.review = Review(status="reviewed", at="2026-01-01", sha256=legacy_clause_sha256_v1(reviewed))
    edited.review = Review(status="draft", sha256=HAND_EDITED)
    forged.review = Review(status="draft", sha256="f" * 64)  # a JSON hand-edit: matches neither version
    current.review = draft_review(current)  # already on v2
    unstamped.review = Review(status="draft", sha256=None)

    # Positive control: the hazard is real. Before the migration the untouched
    # AI draft reads as a human's, which would protect it from generate for good.
    check("control: before the migration, an untouched v1 AI draft already reads as hand-edited (the hazard is real)",
          is_hand_edited(ai) and is_protected(ai) and legacy_clause_sha256_v1(ai) != current_sha256(ai))
    without_articles = ai_articles.model_copy(deep=True)
    without_articles.suggested_articles = []
    check("control: the v1 fingerprint can't see articles, the v2 one can",
          legacy_clause_sha256_v1(ai_articles) == legacy_clause_sha256_v1(without_articles)
          and current_sha256(ai_articles) != current_sha256(without_articles))

    todo = plan(bill)
    check("plan: exactly the untouched v1 AI drafts are marked for re-stamping", todo.restamp == ["c3", "c9"], str(todo.restamp))
    check("plan: hand-edited, forged, reviewed, current and unstamped each land in their own bucket",
          todo.hand_edited == ["c4"] and todo.mismatched == ["c6"] and todo.reviewed_stale == ["c5"]
          and todo.already_current == ["c7"] and todo.unstamped == ["c8"] and todo.other == ["c1"], str(todo))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = bill_path(root, FIXTURE_BILL_ID)
        save_bill(bill, path)
        stamps_before = {c.id: (c.review.status, c.review.sha256) for c in bill.clauses}

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(root, FIXTURE_BILL_ID, dry_run=True)
        check("--dry-run reports and writes nothing", code == 0 and "would re-stamp 2" in out.getvalue()
              and {c.id: (c.review.status, c.review.sha256) for c in load_bill(path).clauses} == stamps_before, out.getvalue())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(root, FIXTURE_BILL_ID)
        after = {c.id: c for c in load_bill(path).clauses}
        check("run exits 0 and says how many it re-stamped", code == 0 and "re-stamped as AI drafts (v1 stamp, untouched since): 2" in out.getvalue(), out.getvalue())
        for clause_id in ("c3", "c9"):
            clause = after[clause_id]
            check(f"{clause_id}: an untouched AI draft is re-stamped, still a draft, and no longer hand-edited",
                  clause.review.status == "draft" and clause.review.sha256 == current_sha256(clause)
                  and not is_hand_edited(clause) and not is_protected(clause), str(clause.review))
        check("c9: its articles are untouched by the migration", after["c9"].suggested_articles == [201, 10])
        check("c4: a 'hand-edited' draft stays hand-edited, stamp unchanged",
              after["c4"].review.sha256 == HAND_EDITED and is_hand_edited(after["c4"]))
        check("c6: a draft edited in the JSON (matches neither version) stays hand-edited, stamp unchanged",
              after["c6"].review.sha256 == "f" * 64 and is_hand_edited(after["c6"]))
        check("c5: a reviewed clause is not re-stamped and not kept reviewed: it reads changed since review",
              after["c5"].review.status == "reviewed" and after["c5"].review.sha256 == stamps_before["c5"][1]
              and changed_since_review(after["c5"]) and not is_reviewed(after["c5"]))
        check("c7, c8: an already-current draft and an unstamped draft are left exactly as they were",
              (after["c7"].review.status, after["c7"].review.sha256) == stamps_before["c7"]
              and (after["c8"].review.status, after["c8"].review.sha256) == stamps_before["c8"])
        check("nothing anywhere became reviewed",
              [c.id for c in after.values() if c.review.status == "reviewed"] == ["c5"])
        check("everything outside the stamps is byte-for-byte the same (text, explanation, groups, articles)",
              all(after[c.id].model_dump(exclude={"review"}) == c.model_dump(exclude={"review"}) for c in bill.clauses))

        bytes_after_first = sha256_file(path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(root, FIXTURE_BILL_ID)
        check("running it again is a no-op: exit 0, 'file not written', byte-identical",
              code == 0 and "nothing to re-stamp" in out.getvalue() and sha256_file(path) == bytes_after_first, out.getvalue())

        # The lock: a second writer is refused before anything changes.
        before_lock = sha256_file(path)
        with bill_lock(path):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run(root, FIXTURE_BILL_ID)
        check("a bill another command holds is refused (exit 1) and left unchanged",
              code == 1 and "is open in another" in out.getvalue() and sha256_file(path) == before_lock, out.getvalue())

    real_after = {file.name: sha256_file(file) for file in real_files}
    check("the real bill files were not touched", len(real_files) > 0 and real_after == real_before, f"{len(real_files)} files")
    return check.finish()
