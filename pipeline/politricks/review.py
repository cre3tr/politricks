"""The human gate: nothing ships without your review (devpost/spec.md > Review).

`politricks review <bill-id>` walks you through every DRAFT text in a bill, one
item at a time — the summary, each group entry, each clause explanation — and
you accept, edit or skip each one. Accepting sets `review {status: "reviewed",
at: <today>}`, which is what flips the page badge from "AI draft" to "Reviewed".
This is the Sheng gate: Claude writes the draft, you decide it sounds right.

Order: anything lint flags (a banned phrase, or Claude's own neutral flag)
comes first, then the rest in bill order. Hand-written samples, already
reviewed items and clauses with no explanation are skipped — there is nothing
for a human to judge in them.

Two rules the walk enforces:
- You cannot ACCEPT an item that still carries a banned phrase. Edit it first.
- The walk is not "done" while `lint` still fails anywhere in the bill.

Everything that touches the outside world — asking, showing, saving, opening an
editor — is passed in, so `review --self-test` can drive the whole walk with
scripted answers, in memory, offline, writing nothing.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Union

from .hashing import sha256_file
from .lint import (
    FIXTURE_DRAFT_IDS,
    LANGS,
    SUMMARY_ID,
    Finding,
    LintError,
    fixture_bill,
    group_item_id,
    lint_bill,
    load_phrases,
)
from .models import Bill, Clause, GroupEntry, NeutralCheck, Review, Summary, Tri, bill_path, load_bill, save_bill

LEGAL_PREVIEW_CHARS = 300

Ask = Callable[[str], str]
Show = Callable[[str], None]
Save = Callable[[Bill], None]


@dataclass
class Item:
    """One thing a human can review: the summary, a group entry or a clause."""

    id: str
    owner: Union[Summary, GroupEntry, Clause]
    heading: Optional[str] = None  # clauses only
    legal_text: Optional[str] = None  # clauses only: shown for context, never edited

    @property
    def tri(self) -> Optional[Tri]:
        return self.owner.explanation if isinstance(self.owner, Clause) else self.owner.text


@dataclass
class ReviewResult:
    visited: list[str] = field(default_factory=list)  # item ids, in the order they were shown
    accepted: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)  # accept attempts blocked by a banned phrase
    not_drafts: dict[str, int] = field(default_factory=dict)  # what was never offered, and why
    remaining_drafts: int = 0
    findings: list[Finding] = field(default_factory=list)  # lint failures left at the end
    quit: bool = False

    @property
    def ok(self) -> bool:
        return not self.findings


# --------------------------------------------------------------------------
# What gets walked
# --------------------------------------------------------------------------


def _collect(bill: Bill) -> tuple[list[Item], dict[str, int]]:
    """Split every reviewable thing into drafts (walked) and the rest (counted)."""
    candidates: list[Item] = []
    if bill.summary is not None:
        candidates.append(Item(SUMMARY_ID, bill.summary))
    for entry in bill.groups:
        candidates.append(Item(group_item_id(entry.group), entry))
    for clause in bill.clauses:
        candidates.append(
            Item(clause.id, clause, heading=f"Section {clause.number}: {clause.heading}", legal_text=clause.text)
        )

    drafts: list[Item] = []
    not_drafts = {"sample": 0, "reviewed": 0, "no explanation": 0}
    for item in candidates:
        status = item.owner.review.status
        if item.tri is None:
            not_drafts["no explanation"] += 1
        elif status == "draft":
            drafts.append(item)
        elif status == "sample":
            not_drafts["sample"] += 1
        else:
            not_drafts["reviewed"] += 1
    return drafts, not_drafts


def _describe(item: Item, findings: list[Finding]) -> str:
    lines = ["", "=" * 64, item.id if item.heading is None else f"{item.id}  {item.heading}"]
    if item.legal_text:
        text = item.legal_text
        if len(text) > LEGAL_PREVIEW_CHARS:
            text = text[:LEGAL_PREVIEW_CHARS].rstrip() + " [...]"
        lines += ["", "LEGAL TEXT (first 300 characters):", text]
    tri = item.tri
    lines += ["", f"EN:    {tri.en}", f"SW:    {tri.sw}", f"SHENG: {tri.sheng}"]
    if findings:
        lines.append("")
        lines += [f"  ! {finding.lang + ': ' if finding.lang else ''}{finding.describe()}" for finding in findings]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Editing a text
# --------------------------------------------------------------------------


def find_editor() -> Optional[str]:
    """$EDITOR if set, else Notepad on Windows, else None (type it inline)."""
    editor = os.environ.get("EDITOR")
    if editor:
        return editor
    return "notepad" if os.name == "nt" else None


def _launch(command: str, path: Path) -> int:
    if os.name == "nt":
        return subprocess.run(f'{command} "{path}"', shell=True).returncode
    return subprocess.run([*shlex.split(command), str(path)]).returncode


def _edit_text(current: str, lang: str, ask: Ask, show: Show, editor: Optional[str]) -> str:
    """Return the replacement text ("" means no change).

    With an editor: the text goes into a temp .txt file, the editor opens it,
    and we read it back. Windows 11 Notepad can return before you close it, so
    we always stop and ask you to confirm before reading the file. With no
    editor (or one that fails to start): you type the replacement inline.
    """
    command = find_editor() if editor == "auto" else editor
    if command is not None:
        path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
                handle.write(current + "\n")
                path = Path(handle.name)
            code = _launch(command, path)
            if code == 0:
                ask("Save and close the editor, then press Enter > ")
                return path.read_text(encoding="utf-8-sig").rstrip()
            show(f"the editor exited with code {code}; type the new text here instead")
        except OSError as error:
            show(f"could not start the editor ({error}); type the new text here instead")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
    return ask(f"New {lang} text (empty = keep the old one) > ").rstrip()


# --------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------


def walk(bill: Bill, phrases: list[str], ask: Ask, show: Show, save: Save, editor: Optional[str] = "auto") -> ReviewResult:
    """Walk every draft in `bill`. `editor`: "auto" = $EDITOR/Notepad, None =
    always type inline, or an explicit command. Mutates `bill` in place."""
    drafts, not_drafts = _collect(bill)
    result = ReviewResult(not_drafts=not_drafts)
    show(
        f"{len(drafts)} draft text(s) to review. Skipping {sum(not_drafts.values())} that aren't drafts: "
        f"{not_drafts['sample']} sample (hand-written placeholders), {not_drafts['reviewed']} already reviewed, "
        f"{not_drafts['no explanation']} clause(s) with no explanation yet."
    )

    def findings_for(item: Item) -> list[Finding]:
        return [finding for finding in lint_bill(bill, phrases) if finding.item == item.id]

    # Flagged items first (a stable sort keeps bill order inside each half).
    flagged = {finding.item for finding in lint_bill(bill, phrases)}
    drafts.sort(key=lambda item: item.id not in flagged)

    dirty = False  # edits made since the last save
    try:
        for item in drafts:
            result.visited.append(item.id)
            while True:
                show(_describe(item, findings_for(item)))
                choice = ask("[a]ccept  [e]dit  [s]kip  [q]uit > ").strip().lower()[:1]

                if choice == "a":
                    findings = findings_for(item)
                    banned = [finding for finding in findings if finding.kind == "banned"]
                    if banned:
                        result.refused.append(item.id)
                        show("cannot accept: this text still has a banned phrase. Edit it first.")
                        continue
                    for finding in findings:  # neutral flags: allowed, but say so first
                        show(f"accepting clears this flag — {finding.describe()}")
                    item.owner.review = Review(status="reviewed", at=date.today().isoformat())
                    result.accepted.append(item.id)
                    save(bill)  # right now, so quitting later loses nothing
                    dirty = False
                    break

                if choice == "e":
                    lang = ask("Which language? [en/sw/sheng] > ").strip().lower()
                    if lang not in LANGS:
                        show(f"unknown language {lang!r}")
                        continue
                    new_text = _edit_text(getattr(item.tri, lang), lang, ask, show, editor)
                    if not new_text or new_text == getattr(item.tri, lang):
                        show("no change")
                        continue
                    setattr(item.tri, lang, new_text)
                    if item.id not in result.edited:
                        result.edited.append(item.id)
                    dirty = True
                    continue

                if choice == "s":
                    result.skipped.append(item.id)
                    break

                if choice == "q":
                    result.quit = True
                    break

                show("choose a, e, s or q")

            if result.quit:
                break
    finally:
        # An edit you made but didn't accept (then skipped, quit or Ctrl-C'd) is
        # still your work: keep it, as a draft.
        if dirty:
            save(bill)

    result.remaining_drafts = sum(1 for item in drafts if item.owner.review.status == "draft")
    result.findings = lint_bill(bill, phrases)
    if result.findings:
        show("")
        for finding in result.findings:
            show(finding.line())
        show(f"not done: {len(result.findings)} lint failure(s)")
    else:
        show(
            f"accepted {len(result.accepted)}, edited {len(result.edited)}, "
            f"skipped {len(result.skipped)}, remaining drafts {result.remaining_drafts}"
        )
    return result


# --------------------------------------------------------------------------
# `politricks review <bill-id>`
# --------------------------------------------------------------------------


def _ask_from_terminal(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        # No terminal attached (piped input ran out): treat it as "quit".
        return "q"


def run(repo_root: Path, bill_id: str) -> int:
    """Returns the exit code: 0 done, 1 lint failures remain or the bill wouldn't load."""
    try:
        phrases = load_phrases(repo_root)
        path = bill_path(repo_root, bill_id)
        bill = load_bill(path)
    except (LintError, ValueError, OSError) as error:
        print(f"FAILED: {error}")
        return 1
    result = walk(bill, phrases, _ask_from_terminal, print, lambda saved: save_bill(saved, path))
    return 0 if result.ok else 1


# --------------------------------------------------------------------------
# Self-test: drives the whole walk with scripted answers, in memory, offline.
# --------------------------------------------------------------------------


def _copy_for_review(repo_root: Path) -> Bill:
    """The fixture-drafted bill, guaranteed to hold sample items too.

    c3 and c4 are drafts; the summary and c1 are forced to "sample" in the copy
    (keeping their real text when they have it), so "samples are skipped" is
    proven the same way today and after slice 2b replaces the real samples.
    """
    bill = fixture_bill(repo_root)
    placeholder = Tri(en="Sample text.", sw="Maandishi ya mfano.", sheng="Sample text bro.")
    bill.summary = Summary(
        text=bill.summary.text if bill.summary else placeholder, review=Review(status="sample")
    )
    c1 = next(clause for clause in bill.clauses if clause.id == "c1")
    c1.explanation = c1.explanation or placeholder
    c1.neutral_check = c1.neutral_check or NeutralCheck(ok=True, note="Hand-written sample")
    c1.review = Review(status="sample")
    return bill


def self_test(repo_root: Path) -> bool:
    ok = True

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        if condition:
            print(f"PASS: {label}")
        else:
            print(f"FAIL: {label}" + (f" ({detail})" if detail else ""))
            ok = False

    real_path = bill_path(repo_root, "finance-act-2026")
    sha_before = sha256_file(real_path)
    phrases = load_phrases(repo_root)
    # Today from the OS clock directly, not from `date.today()`: the code under
    # test uses that, and a check that reads the same source can't disagree.
    today = time.strftime("%Y-%m-%d")

    def drive(bill: Bill, answers: list[str]):
        """Run walk() with scripted answers. Returns (result, answers left, shown text, saved snapshots)."""
        queue = list(answers)
        shown: list[str] = []
        saves: list[Bill] = []

        def ask(prompt: str) -> str:
            return queue.pop(0) if queue else "q"

        result = walk(bill, phrases, ask, shown.append, lambda b: saves.append(b.model_copy(deep=True)), editor=None)
        return result, queue, "\n".join(shown), saves

    def clause_of(bill: Bill, clause_id: str) -> Clause:
        return next(clause for clause in bill.clauses if clause.id == clause_id)

    # === Scenario A: the main walk ==============================================
    # c4 carries a banned phrase, c3 is clean, the summary and c1 are samples.
    bill = _copy_for_review(repo_root)
    clause_of(bill, "c4").explanation.sheng += " Hii ni kinyume cha katiba."
    others_before = [c.model_dump() for c in bill.clauses if c.id not in FIXTURE_DRAFT_IDS]
    summary_before = bill.summary.model_dump()
    # Counted straight from the copy, not from review's own _collect(), so the
    # check below compares two independent counts.
    samples_before = 1 + sum(1 for c in bill.clauses if c.review.status == "sample")
    new_sheng = "Hii inaongeza section mpya ya 6B kwa Income Tax Act."
    result, left, shown, saves = drive(
        bill,
        [
            "a",  # c4: accept -> refused (banned phrase)
            "e", "sheng", new_sheng,  # c4: edit sheng inline (no editor in the test)
            "a",  # c4: accept -> allowed now
            "s",  # c3: skip
        ],
    )
    c3, c4 = clause_of(bill, "c3"), clause_of(bill, "c4")
    check("A. the flagged clause (c4) comes before the clean one (c3)", result.visited == ["c4", "c3"], str(result.visited))
    check("A. accepting c4 with a banned phrase was refused", result.refused == ["c4"], str(result.refused))
    check(
        "A. c4 is reviewed, dated today, with the edited sheng",
        c4.review.status == "reviewed" and c4.review.at == today and c4.explanation.sheng == new_sheng,
        f"{c4.review} / {c4.explanation.sheng!r}",
    )
    check("A. c3 (skipped) is still a draft", c3.review.status == "draft" and c3.review.at is None)
    check(
        "A. the sample summary and every other clause (samples included) are untouched",
        bill.summary.model_dump() == summary_before
        and [c.model_dump() for c in bill.clauses if c.id not in FIXTURE_DRAFT_IDS] == others_before,
    )
    check(
        f"A. all {samples_before} sample items were counted as skipped, and none was walked",
        result.not_drafts["sample"] == samples_before and not (set(result.visited) & {"summary", "c1"}),
        str(result.not_drafts),
    )
    check(
        "A. save() ran once, after the accept, and what it saved already had c4 reviewed",
        len(saves) == 1 and clause_of(saves[0], "c4").review.status == "reviewed",
        f"{len(saves)} save(s)",
    )
    check("A. the script was fully consumed (the walk asked exactly what we answered)", left == [], str(left))
    check("A. the walk finished clean: no lint failures, counts printed", result.ok and "accepted 1, edited 1, skipped 1, remaining drafts 1" in shown)
    try:
        Bill.model_validate(bill.model_dump(mode="json"))
        validates = True
    except ValueError:
        validates = False
    check("A. the result still validates as models.Bill", validates)

    # === Scenario B: an edit you made is kept even if you quit =================
    bill = _copy_for_review(repo_root)
    result, left, shown, saves = drive(bill, ["e", "en", "Edited by hand.", "q"])
    check(
        "B. edit then quit: the edit was saved, still as a draft",
        result.quit
        and len(saves) == 1
        and clause_of(saves[0], "c3").explanation.en == "Edited by hand."
        and clause_of(saves[0], "c3").review.status == "draft",
        f"{len(saves)} save(s)",
    )

    # === Scenario C: a neutral flag is allowed to be cleared by a human ========
    bill = _copy_for_review(repo_root)
    clause_of(bill, "c4").neutral_check = NeutralCheck(ok=False, note="Described a value judgement")
    result, left, shown, saves = drive(bill, ["a", "s"])
    check(
        "C. flagged c4 comes first; accepting it is allowed and clears the neutral flag",
        result.visited[0] == "c4" and result.refused == [] and clause_of(bill, "c4").review.status == "reviewed" and result.ok,
        str(result.visited),
    )

    # === Scenario D: not "done" while lint fails anywhere ======================
    bill = _copy_for_review(repo_root)
    clause_of(bill, "c1").explanation.en = "The officer cannot vote."  # c1 is a sample: the walk can't fix it
    result, left, shown, saves = drive(bill, ["a", "a"])
    check(
        "D. a banned phrase in an item the walk can't fix leaves the walk 'not done'",
        not result.ok and "not done: 1 lint failure(s)" in shown and len(result.accepted) == 2,
        shown.splitlines()[-1] if shown else "",
    )

    # === Nothing written ========================================================
    check("the real bill file's SHA-256 is unchanged", sha256_file(real_path) == sha_before)

    return ok
