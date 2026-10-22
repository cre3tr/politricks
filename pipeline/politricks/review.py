"""The human gate: nothing ships without your review (devpost/spec.md > Review).

`politricks review <bill-id>` walks you through every DRAFT text in a bill, one
item at a time — the summary, each group entry, each clause explanation — and
you accept, edit or skip each one. Accepting sets `review {status: "reviewed",
at: <today>, sha256: <fingerprint>}`, which is what flips the page badge from
"AI draft" to "Reviewed". The fingerprint (`models.current_sha256`) covers the
three texts and the tags or section list shown with them, so the badge stays
"Reviewed" only while the content is exactly what you accepted. Change it
afterwards, by hand or by a tags pass, and it reads "Changed since review"
until you accept it again.
This is the Sheng gate: Claude writes the draft, you decide it sounds right.

Order: anything lint flags (a banned phrase, a neutral or Sheng flag, or a
change since review) comes first, then the rest: clauses in bill order, then
the summary, then the group notes (a group's sections come from the clause
tags, so settle those first). Clean hand-written samples, items still exactly
as reviewed and clauses with no explanation are skipped: there is nothing for
a human to judge in them.

A text that lint flags is walked whatever its status, otherwise the walk would
dead-end on it ("not done" with no way to fix it):
- draft or reviewed -> accepting sets it to reviewed, dated today. Editing
  turns it into a hand-edited draft until you accept it (a reviewed text
  too, because the badge must never say "Reviewed" about words nobody
  reviewed). A hand-edited draft is yours: `generate` never overwrites it.
- sample -> accepting keeps it a sample. Only `generate` replaces a sample, and
  a sample never renders as "AI draft" or "Reviewed" (spec > Clause Card).

Narrowing the walk (2026-10-22). The full walk offers every draft after the
flagged ones, and each accept saves at once, so re-accepting one flagged item
by answering "a" to everything accepted 59 items. Two options walk less:
- `--flagged`: only what lint flags. The drafts are left out (and counted).
- `--only ID [ID ...]`: only the named items (c12, summary, group:trader),
  whatever their status, so a reviewed text can be reopened to fix a typo. An
  id that doesn't exist, or has no explanation yet, refuses the whole run
  before anything is shown.
The "not done while lint fails" rule below still looks at the whole bill.

Rules the walk enforces:
- You cannot ACCEPT an item that still carries a banned phrase, at any status.
  Edit it first.
- The walk is not "done" while `lint` still fails anywhere in the bill.
- Answers must be exact: a/accept, e/edit, s/skip, q/quit. "all" or "abort" is
  not "a". End of input (a closed pipe) and Ctrl-C both mean QUIT, never an
  answer: they can't be mistaken for text you typed. Anything you edited is
  still saved, as a draft.

Everything that touches the outside world — asking, showing, saving, opening an
editor — is passed in, so `review --self-test` can drive the whole walk with
scripted answers, in memory, offline, writing nothing.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Union

from .hashing import sha256_file
from .lint import (
    LANGS,
    SUMMARY_ID,
    Finding,
    LintError,
    group_item_id,
    lint_bill,
    load_phrases,
)
from .models import (
    HAND_EDITED,
    Bill,
    Clause,
    GroupEntry,
    NeutralCheck,
    Review,
    Summary,
    Tri,
    bill_path,
    changed_since_review,
    current_sha256,
    is_hand_edited,
    is_reviewed,
    load_bill,
    save_bill,
)
from .reviewlock import BillLocked, bill_lock
from .selftest import FIXTURE_DRAFT_IDS, FIXTURE_SAMPLE_ID, Checker, fixture_bill, mark_reviewed

LEGAL_PREVIEW_CHARS = 300

Ask = Callable[[str], str]
Show = Callable[[str], None]
Save = Callable[[Bill], None]

# The only answers the walk understands (after strip + lowercase). Matching the
# whole answer, not its first letter, is what keeps "abort" from meaning "a".
CHOICES = {
    "a": "a",
    "accept": "a",
    "e": "e",
    "edit": "e",
    "s": "s",
    "skip": "s",
    "q": "q",
    "quit": "q",
}


class QuitReview(Exception):
    """The reviewer is gone: input ended (EOF) or the script ran out.

    Raised by `ask` instead of returning an answer, so "no more input" can never
    be mistaken for text the human typed. `walk` treats it as quit.
    """


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


def _candidates(bill: Bill) -> list[Item]:
    """Every reviewable thing in bill order: clauses, then the summary, then groups."""
    candidates: list[Item] = []
    for clause in bill.clauses:
        candidates.append(
            Item(clause.id, clause, heading=f"Section {clause.number}: {clause.heading}", legal_text=clause.text)
        )
    if bill.summary is not None:
        candidates.append(Item(SUMMARY_ID, bill.summary))
    for entry in bill.groups:
        candidates.append(Item(group_item_id(entry.group), entry))
    return candidates


def selection_errors(bill: Bill, only: list[str]) -> list[str]:
    """Why each id given to `--only` can't be walked (an empty list = all fine).

    A typo must refuse the run, never quietly walk nothing and report "done".
    """
    texts = {item.id: item.tri for item in _candidates(bill)}
    errors = []
    for item_id in only:
        if item_id not in texts:
            errors.append(f"{item_id}: no such item in this bill (ids look like c12, summary, group:trader)")
        elif texts[item_id] is None:
            errors.append(f"{item_id}: has no explanation yet, so there is nothing to review")
    return errors


def _collect(
    bill: Bill, phrases: list[str], flagged_only: bool = False, only: Optional[list[str]] = None
) -> tuple[list[Item], dict[str, int]]:
    """Split every reviewable thing into the ones to walk and the rest (counted).

    Walked: every draft, plus anything lint flags whatever its status (a
    reviewed item changed since is flagged), flagged ones first. A stable sort
    keeps the order inside each half: clauses, then the summary, then groups.

    Narrowed: `flagged_only` walks just the flagged items, `only` just the named
    ones (whatever their status). Whatever the full walk would have offered
    but the filter kept back is counted as "left out", so the walk can say so.
    """
    flagged = {finding.item for finding in lint_bill(bill, phrases)}
    to_walk: list[Item] = []
    not_walked = {"sample": 0, "reviewed": 0, "no explanation": 0, "left out": 0}
    for item in _candidates(bill):
        status = item.owner.review.status
        if item.tri is None:
            not_walked["no explanation"] += 1
            continue
        offered = status == "draft" or item.id in flagged  # what the full walk offers
        if only is not None:
            chosen = item.id in only
        elif flagged_only:
            chosen = item.id in flagged
        else:
            chosen = offered
        if chosen:
            to_walk.append(item)
        elif offered:
            not_walked["left out"] += 1
        elif status == "sample":
            not_walked["sample"] += 1
        else:
            not_walked["reviewed"] += 1
    to_walk.sort(key=lambda item: item.id not in flagged)
    return to_walk, not_walked


def _describe(item: Item, findings: list[Finding]) -> str:
    lines = ["", "=" * 64, item.id if item.heading is None else f"{item.id}  {item.heading}"]
    if item.legal_text:
        text = item.legal_text
        if len(text) > LEGAL_PREVIEW_CHARS:
            text = text[:LEGAL_PREVIEW_CHARS].rstrip() + " [...]"
        lines += ["", "LEGAL TEXT (first 300 characters):", text]
    tri = item.tri
    lines += ["", f"EN:    {tri.en}", f"SW:    {tri.sw}", f"SHENG: {tri.sheng}"]
    # Accepting also accepts these (they're in the fingerprint), so show them.
    if isinstance(item.owner, Clause):
        lines.append(f"GROUPS: {', '.join(item.owner.groups) or 'none'}")
    elif isinstance(item.owner, GroupEntry):
        lines.append(f"SECTIONS: {', '.join(item.owner.clause_ids) or 'none'}")
    status = item.owner.review.status
    if changed_since_review(item.owner):
        lines += ["", "status: reviewed, but changed since"]
    elif status != "draft":
        lines += ["", f"status: {status}"]
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
    editor (or one that fails to start, or saves something we can't read): you
    type the replacement inline. If the input ends here, `ask` raises
    QuitReview and nothing is changed.
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
        except UnicodeDecodeError:
            # e.g. Notepad saved as UTF-16 or ANSI. Never guess at the bytes.
            show("the editor saved the file in an encoding I can't read (use UTF-8); type the new text here instead")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
    return ask(f"New {lang} text (empty = keep the old one) > ").rstrip()


# --------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------


def walk(
    bill: Bill,
    phrases: list[str],
    ask: Ask,
    show: Show,
    save: Save,
    editor: Optional[str] = "auto",
    flagged_only: bool = False,
    only: Optional[list[str]] = None,
) -> ReviewResult:
    """Walk every item that needs a human. `editor`: "auto" = $EDITOR/Notepad,
    None = always type inline, or an explicit command. `flagged_only` / `only`
    narrow the walk (see the module docstring). Mutates `bill` in place."""
    if flagged_only and only is not None:
        raise ValueError("choose --flagged or --only, not both")
    if only is not None and selection_errors(bill, only):
        raise ValueError("; ".join(selection_errors(bill, only)))
    to_walk, not_walked = _collect(bill, phrases, flagged_only, only)
    result = ReviewResult(not_drafts=not_walked)
    start_findings = lint_bill(bill, phrases)
    flagged = {finding.item for finding in start_findings}
    had_banned = {finding.item for finding in start_findings if finding.kind == "banned"}
    n_flagged = sum(1 for item in to_walk if item.id in flagged)
    left_out = not_walked["left out"]
    show(
        f"{len(to_walk)} text(s) to review ({n_flagged} flagged by lint, shown first). "
        f"Skipping {sum(not_walked.values()) - left_out} that need nothing: "
        f"{not_walked['sample']} sample (hand-written placeholders), {not_walked['reviewed']} already reviewed, "
        f"{not_walked['no explanation']} clause(s) with no explanation yet."
    )
    if left_out:
        show(f"{left_out} other draft(s) left out by --flagged / --only: run without it to see them.")

    def findings_for(item: Item) -> list[Finding]:
        return [finding for finding in lint_bill(bill, phrases) if finding.item == item.id]

    dirty = False  # edits made since the last save
    try:
        try:
            for item in to_walk:
                result.visited.append(item.id)
                while True:
                    show(_describe(item, findings_for(item)))
                    choice = CHOICES.get(ask("[a]ccept  [e]dit  [s]kip  [q]uit > ").strip().lower())

                    if choice == "a":
                        findings = findings_for(item)
                        banned = [finding for finding in findings if finding.kind == "banned"]
                        if banned:
                            result.refused.append(item.id)
                            show("cannot accept: this text still has a banned phrase. Edit it first.")
                            continue
                        if item.owner.review.status == "sample":
                            # A sample is replaced by `generate`, never promoted by a human.
                            note = "samples stay placeholders until generate replaces them"
                            if item.id in had_banned:
                                note += "; banned phrase removed"
                            show(note)
                        else:
                            for finding in findings:  # neutral flags: allowed, but say so first
                                show(f"accepting clears this flag — {finding.describe()}")
                            item.owner.review = Review(
                                status="reviewed", at=date.today().isoformat(), sha256=current_sha256(item.owner)
                            )
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
                        if item.owner.review.status != "sample":
                            if item.owner.review.status == "reviewed":
                                # These are no longer the words a human accepted.
                                show("edited: this text is a draft again until you accept it")
                            # A human's draft now: generate never overwrites it,
                            # even if a later edit types the old words back.
                            item.owner.review = Review(status="draft", sha256=HAND_EDITED)
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
        except (QuitReview, KeyboardInterrupt):
            # End of input or Ctrl-C, at any prompt (even half-way through an
            # edit): the same as typing "q". Nothing half-typed is kept.
            result.quit = True
            show("")
            show("input ended or interrupted: quitting the review")
    finally:
        # An edit you made but didn't accept (then skipped, quit or Ctrl-C'd) is
        # still your work: keep it, as a draft.
        if dirty:
            save(bill)

    result.remaining_drafts = sum(1 for item in to_walk if item.owner.review.status == "draft")
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
    except EOFError as error:
        # No terminal attached (piped input ran out). Never turn that into an
        # answer: as "q" it would be typed into an edit prompt as new text.
        raise QuitReview("end of input") from error


def run(repo_root: Path, bill_id: str, flagged_only: bool = False, only: Optional[list[str]] = None) -> int:
    """Returns the exit code: 0 done, 1 lint failures remain, the bill wouldn't
    load, or an `--only` id can't be walked."""
    try:
        phrases = load_phrases(repo_root)
        path = bill_path(repo_root, bill_id)
    except (LintError, ValueError) as error:
        print(f"FAILED: {error}")
        return 1
    if not path.exists():
        print(f"FAILED: no bill file at {path}")
        return 1
    # Held for the whole walk: it saves the whole file on every accept, so
    # anything else writing the bill meanwhile would be overwritten.
    try:
        with bill_lock(path):
            try:
                bill = load_bill(path)
            except (ValueError, OSError) as error:
                print(f"FAILED: {error}")
                return 1
            errors = selection_errors(bill, only) if only is not None else []
            if errors:
                for error in errors:
                    print(f"FAILED: {error}")
                return 1
            try:
                result = walk(
                    bill,
                    phrases,
                    _ask_from_terminal,
                    print,
                    lambda saved: save_bill(saved, path),
                    flagged_only=flagged_only,
                    only=only,
                )
            except KeyboardInterrupt:
                # Ctrl-C outside the walk's own prompts (e.g. while the final lint ran).
                # Accepted items were already saved as they were accepted.
                print("\ninterrupted")
                return 1
    except BillLocked as error:
        print(f"FAILED: {error}")
        return 1
    return 0 if result.ok else 1


# --------------------------------------------------------------------------
# Self-test: drives the whole walk with scripted answers, in memory, offline.
# --------------------------------------------------------------------------


def self_test(repo_root: Path) -> bool:
    import contextlib
    import io

    check = Checker()

    real_path = bill_path(repo_root, "finance-act-2026")
    sha_before = sha256_file(real_path)
    phrases = load_phrases(repo_root)
    # Today from the OS clock directly, not from `date.today()`: the code under
    # test uses that, and a check that reads the same source can't disagree.
    today = time.strftime("%Y-%m-%d")

    @dataclass
    class Driven:
        result: ReviewResult
        left: list  # script entries nobody asked for
        lines: list[str]  # everything shown, one entry per show() call
        saves: list[Bill]  # a deep copy of the bill at each save()
        escaped: bool = False  # a Ctrl-C that walk() let through (it must never)

        @property
        def shown(self) -> str:
            return "\n".join(self.lines)

    def drive(bill: Bill, answers: list, editor: Optional[str] = None, **narrow) -> Driven:
        """Run walk() with scripted answers. An entry that is an exception is
        RAISED when asked for; when the script runs out the reviewer is gone
        (QuitReview), exactly like a closed pipe. It never answers "q".
        `narrow` passes flagged_only= / only= through to walk()."""
        queue = list(answers)
        lines: list[str] = []
        saves: list[Bill] = []

        def ask(prompt: str) -> str:
            if not queue:
                raise QuitReview("the script ran out")
            answer = queue.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        try:
            result = walk(
                bill, phrases, ask, lines.append, lambda b: saves.append(b.model_copy(deep=True)), editor=editor, **narrow
            )
        except KeyboardInterrupt:
            return Driven(ReviewResult(), queue, lines, saves, escaped=True)
        return Driven(result, queue, lines, saves)

    def clause_of(bill: Bill, clause_id: str) -> Clause:
        return next(clause for clause in bill.clauses if clause.id == clause_id)

    BANNED = "The officer cannot vote."

    # === Scenario A: the main walk ==============================================
    # c4 carries a banned phrase, c3 and the group entry are clean drafts, the
    # summary and c1 are samples. Full words, odd case and spacing are accepted.
    bill = fixture_bill(repo_root)
    clause_of(bill, "c4").explanation.sheng += " Hii ni kinyume cha katiba."
    others_before = [c.model_dump() for c in bill.clauses if c.id not in FIXTURE_DRAFT_IDS]
    summary_before = bill.summary.model_dump()
    # Counted straight from the copy, not from review's own _collect(), so the
    # check below compares two independent counts.
    samples_before = 1 + sum(1 for c in bill.clauses if c.review.status == "sample")
    new_sheng = "Hii inaongeza section mpya ya 6B kwa Income Tax Act."
    run_a = drive(
        bill,
        [
            "accept",  # c4: accept -> refused (banned phrase)
            "Edit", "sheng", new_sheng,  # c4: edit sheng inline (no editor in the test)
            " ACCEPT ",  # c4: accept -> allowed now
            "skip",  # c3: skip
            "s",  # group:trader: skip (groups come after clauses)
        ],
    )
    result, shown = run_a.result, run_a.shown
    c3, c4 = clause_of(bill, "c3"), clause_of(bill, "c4")
    check(
        "A. the flagged clause (c4) comes first, then clause c3, then the group entry",
        result.visited == ["c4", "c3", "group:trader"],
        str(result.visited),
    )
    check("A. accepting c4 with a banned phrase was refused", result.refused == ["c4"], str(result.refused))
    check(
        "A. the accept stores the fingerprint of what was accepted, so c4 counts as reviewed",
        c4.review.sha256 == current_sha256(c4) and is_reviewed(c4),
        str(c4.review),
    )
    check(
        "A. c4 is reviewed, dated today, with the edited sheng",
        c4.review.status == "reviewed" and c4.review.at == today and c4.explanation.sheng == new_sheng,
        f"{c4.review} / {c4.explanation.sheng!r}",
    )
    check(
        "A. c3 and the group entry (skipped) are still drafts",
        c3.review.status == "draft" and c3.review.at is None and bill.groups[0].review.status == "draft",
    )
    check(
        "A. the sample summary and every other clause (samples included) are untouched",
        bill.summary.model_dump() == summary_before
        and [c.model_dump() for c in bill.clauses if c.id not in FIXTURE_DRAFT_IDS] == others_before,
    )
    check(
        f"A. all {samples_before} sample items were counted as skipped, and none was walked",
        result.not_drafts["sample"] == samples_before and not (set(result.visited) & {"summary", FIXTURE_SAMPLE_ID}),
        str(result.not_drafts),
    )
    check(
        "A. save() ran once, after the accept, and what it saved already had c4 reviewed",
        len(run_a.saves) == 1 and clause_of(run_a.saves[0], "c4").review.status == "reviewed",
        f"{len(run_a.saves)} save(s)",
    )
    check("A. the script was fully consumed (the walk asked exactly what we answered)", run_a.left == [], str(run_a.left))
    check(
        "A. the walk finished clean: no lint failures, counts printed",
        result.ok and "accepted 1, edited 1, skipped 2, remaining drafts 2" in shown,
        run_a.lines[-1],
    )
    try:
        Bill.model_validate(bill.model_dump(mode="json"))
        validates = True
    except ValueError:
        validates = False
    check("A. the result still validates as models.Bill", validates)

    # === Scenario B: an edit you made is kept even if you quit =================
    # The first item is draft clause c3. After an explicit "q" the walk must
    # STOP: c4 and the group entry are never shown.
    bill = fixture_bill(repo_root)
    run_b = drive(bill, ["e", "en", "Edited by hand.", "q"])
    saved_group = clause_of(run_b.saves[0], "c3") if run_b.saves else None
    check(
        "B. edit then quit: the edit was saved, still as a draft",
        run_b.result.quit
        and len(run_b.saves) == 1
        and saved_group.explanation.en == "Edited by hand."
        and saved_group.review.status == "draft",
        f"{len(run_b.saves)} save(s)",
    )
    check(
        "B. after an explicit quit the later items are NOT visited",
        run_b.result.visited == ["c3"] and run_b.left == [],
        str(run_b.result.visited),
    )

    # === Scenario C: a neutral flag is allowed to be cleared by a human ========
    bill = fixture_bill(repo_root)
    clause_of(bill, "c4").neutral_check = NeutralCheck(ok=False, note="Described a value judgement")
    run_c = drive(bill, ["a", "s", "s"])
    check(
        "C. flagged c4 comes first; accepting it is allowed and clears the neutral flag",
        run_c.result.visited[0] == "c4"
        and run_c.result.refused == []
        and clause_of(bill, "c4").review.status == "reviewed"
        and run_c.result.ok,
        str(run_c.result.visited),
    )

    # === Scenario A2: a banned phrase in the draft GROUP entry ==================
    # Group entries render to readers like any other text, so lint flags them and
    # the walk must offer them first and let a human fix them.
    bill = fixture_bill(repo_root)
    bill.groups[0].text.sw += " Hii ni kinyume cha katiba."
    run_a2 = drive(bill, ["a", "e", "sw", "Hii inabadilisha maelezo ya mapato ya biashara.", "a", "s", "s"])
    check(
        "A2. a banned phrase in the draft group entry: walked FIRST, accepting refused, then edited and accepted",
        run_a2.result.visited[0] == "group:trader"
        and run_a2.result.refused == ["group:trader"]
        and bill.groups[0].review.status == "reviewed"
        and bill.groups[0].review.at == today
        and run_a2.result.ok,
        f"{run_a2.result.visited}, refused {run_a2.result.refused}",
    )

    # === Scenario D: a flagged SAMPLE is walked, and stays a sample ============
    # (The old walk dead-ended here: "not done" with no way to fix the text.)
    bill = fixture_bill(repo_root)
    clause_of(bill, FIXTURE_SAMPLE_ID).explanation.en = BANNED
    run_d = drive(bill, ["a", "e", "en", "Clean placeholder.", "a", "s", "s", "s"])
    c1 = clause_of(bill, FIXTURE_SAMPLE_ID)
    check(
        "D. a banned phrase planted in a sample is walked FIRST, and accepting it is refused",
        run_d.result.visited[0] == FIXTURE_SAMPLE_ID and run_d.result.refused == [FIXTURE_SAMPLE_ID],
        str(run_d.result.visited),
    )
    check(
        "D. edited clean and accepted: it STAYS a sample (never reviewed, no date)",
        c1.review.status == "sample" and c1.review.at is None and c1.explanation.en == "Clean placeholder.",
        str(c1.review),
    )
    check(
        "D. the accept said why it stayed a sample",
        "samples stay placeholders until generate replaces them; banned phrase removed" in run_d.shown,
    )
    check(
        "D. the walk ends done (ok), and the unflagged sample summary was not walked",
        run_d.result.ok and "not done" not in run_d.shown and "summary" not in run_d.result.visited,
        run_d.lines[-1],
    )
    check(
        "D. the cleaned sample was saved when accepted",
        len(run_d.saves) == 1 and clause_of(run_d.saves[0], FIXTURE_SAMPLE_ID).explanation.en == "Clean placeholder.",
    )

    # === Scenario E: a flagged REVIEWED text is walked, edited, re-accepted ====
    bill = fixture_bill(repo_root)
    c3 = clause_of(bill, "c3")
    c3.review = Review(status="reviewed", at="2026-01-01")
    c3.explanation.en = BANNED
    run_e = drive(bill, ["a", "e", "en", "The officer may not take part.", "a", "s", "s"])
    check(
        "E. a reviewed text that trips a banned phrase is walked first, and accepting it is refused",
        run_e.result.visited[0] == "c3" and run_e.result.refused == ["c3"],
        str(run_e.result.visited),
    )
    check(
        "E. edited and re-accepted: reviewed again, dated today, with the new words",
        c3.review.status == "reviewed" and c3.review.at == today and c3.explanation.en == "The officer may not take part.",
        str(c3.review),
    )
    check("E. the walk ends done (ok)", run_e.result.ok, run_e.lines[-1])

    # An edited reviewed text that is NOT re-accepted must not stay "reviewed".
    bill = fixture_bill(repo_root)
    c3 = clause_of(bill, "c3")
    c3.review = Review(status="reviewed", at="2026-01-01")
    c3.explanation.en = BANNED
    run_e2 = drive(bill, ["e", "en", "The officer may not take part.", "q"])
    saved_c3 = clause_of(run_e2.saves[-1], "c3") if run_e2.saves else None
    check(
        "E. edit a reviewed text then quit: it is saved as a draft, not as 'Reviewed' words nobody reviewed",
        saved_c3 is not None and saved_c3.review.status == "draft" and saved_c3.review.at is None,
        str(saved_c3.review if saved_c3 else "no save"),
    )

    # === Scenario F: only exact answers count ===================================
    # "abort", "all" and "again" all start with "a". None may accept.
    bill = fixture_bill(repo_root)
    run_f = drive(bill, ["abort", "all", "again", "q"])
    check(
        'F. "abort", "all" and "again" do not accept: nothing accepted, the item stays a draft',
        run_f.result.accepted == [] and clause_of(bill, "c3").review.status == "draft" and run_f.saves == [],
        str(run_f.result.accepted),
    )
    check(
        "F. each of them was refused with the prompt help, and the same item was asked again",
        run_f.lines.count("choose a, e, s or q") == 3 and run_f.result.visited == ["c3"],
        str(run_f.lines.count("choose a, e, s or q")),
    )
    bill = fixture_bill(repo_root)
    run_f2 = drive(bill, ["Quit"])
    check('F. the full word "Quit" (any case) quits', run_f2.result.quit and run_f2.result.accepted == [])

    # === Scenario G: end of input and Ctrl-C mean quit, never data =============
    # G1. The input ends at the inline "New en text" prompt. The old walk read
    # that as the answer "q" and PUT IT IN THE BILL as the new text.
    bill = fixture_bill(repo_root)
    text_before = clause_of(bill, "c3").explanation.en
    run_g1 = drive(bill, ["e", "en"])  # script ends -> the reviewer is gone
    check(
        "G. end of input at the inline text prompt leaves the text unchanged, and save() is never called",
        clause_of(bill, "c3").explanation.en == text_before and run_g1.saves == [] and run_g1.result.quit,
        f"{clause_of(bill, "c3").explanation.en!r}, {len(run_g1.saves)} save(s)",
    )
    # G2. Ctrl-C at the accept prompt, after a real edit: the edit is kept.
    bill = fixture_bill(repo_root)
    run_g2 = drive(bill, ["e", "en", "Edited by hand.", KeyboardInterrupt()])
    check(
        "G. Ctrl-C mid-walk ends the walk cleanly (no exception), with the edit saved as a draft",
        not run_g2.escaped
        and run_g2.result.quit
        and len(run_g2.saves) == 1
        and clause_of(run_g2.saves[0], "c3").explanation.en == "Edited by hand."
        and clause_of(run_g2.saves[0], "c3").review.status == "draft"
        and "accepted 0, edited 1" in run_g2.shown,
        f"{len(run_g2.saves)} save(s)",
    )
    # G3. Ctrl-C half-way through a SECOND edit: the first edit is still kept,
    # and the half-typed one is not.
    bill = fixture_bill(repo_root)
    sw_before = clause_of(bill, "c3").explanation.sw
    run_g3 = drive(bill, ["e", "en", "First edit.", "e", "sw", KeyboardInterrupt()])
    check(
        "G. Ctrl-C mid-edit keeps the earlier edit, and the interrupted one changes nothing",
        not run_g3.escaped
        and run_g3.result.quit
        and len(run_g3.saves) == 1
        and clause_of(run_g3.saves[0], "c3").explanation.en == "First edit."
        and clause_of(run_g3.saves[0], "c3").explanation.sw == sw_before,
        f"{len(run_g3.saves)} save(s)",
    )

    # G4. The same two endings through the REAL terminal reader (`input()` on a
    # fake stdin), because the scripted `ask` above never touches
    # `_ask_from_terminal`, where the EOF handling actually lives.
    class FakeStdin:
        """Feeds `lines`, then either ends (EOF) or raises Ctrl-C."""

        def __init__(self, lines: list[str], then: str) -> None:
            self.lines, self.then = list(lines), then

        def readline(self) -> str:
            if self.lines:
                return self.lines.pop(0) + "\n"
            if self.then == "eof":
                return ""
            raise KeyboardInterrupt

    def drive_terminal(bill: Bill, stdin: FakeStdin) -> Driven:
        lines: list[str] = []
        saves: list[Bill] = []
        real_stdin = sys.stdin
        sys.stdin = stdin  # type: ignore[assignment]
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # input() prints its prompt
                result = walk(
                    bill, phrases, _ask_from_terminal, lines.append, lambda b: saves.append(b.model_copy(deep=True)), editor=None
                )
        except KeyboardInterrupt:
            return Driven(ReviewResult(), [], lines, saves, escaped=True)
        finally:
            sys.stdin = real_stdin
        return Driven(result, [], lines, saves)

    bill = fixture_bill(repo_root)
    text_before = clause_of(bill, "c3").explanation.en
    run_g4 = drive_terminal(bill, FakeStdin(["e", "en"], then="eof"))  # the pipe closes at "New en text"
    check(
        "G. a closed pipe at the inline text prompt (real terminal reader) quits: text unchanged, nothing saved, nothing accepted",
        run_g4.result.quit
        and clause_of(bill, "c3").explanation.en == text_before
        and run_g4.saves == []
        and run_g4.result.accepted == [],
        f"{clause_of(bill, "c3").explanation.en!r}, accepted {run_g4.result.accepted}",
    )
    bill = fixture_bill(repo_root)
    run_g5 = drive_terminal(bill, FakeStdin(["e", "en", "Edited by hand."], then="ctrl-c"))
    check(
        "G. Ctrl-C at the terminal prompt (real terminal reader) quits cleanly, with the edit saved",
        not run_g5.escaped
        and run_g5.result.quit
        and len(run_g5.saves) == 1
        and clause_of(run_g5.saves[0], "c3").explanation.en == "Edited by hand.",
        f"{len(run_g5.saves)} save(s)",
    )

    # === Scenario H: an editor that saves unreadable bytes =====================
    # The temp "editor" is a tiny script that writes bytes that are not UTF-8
    # (as Notepad does when it saves UTF-16). Falls back to typing inline.
    bill = fixture_bill(repo_root)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "bad_editor.py"
        script.write_bytes(b"import sys\nopen(sys.argv[1], 'wb').write(b'\\xff\\xfe not utf-8')\n")
        run_h = drive(bill, ["e", "en", "", "Typed instead.", "q"], editor=f'"{sys.executable}" "{script}"')
    check(
        "H. an editor that saves unreadable bytes falls back to the inline prompt, with a message",
        clause_of(bill, "c3").explanation.en == "Typed instead." and "encoding" in run_h.shown,
        f"{clause_of(bill, "c3").explanation.en!r}",
    )

    # === Scenario I: fingerprints (what "Reviewed" is tied to) =================
    # I1. An edit you skip stays yours: a hand-edited draft generate won't touch.
    bill = fixture_bill(repo_root)
    drive(bill, ["e", "en", "Edited by hand.", "s"])
    c3 = clause_of(bill, "c3")
    check("I. an edited-then-skipped draft is marked hand-edited (generate keeps it)",
          c3.review.status == "draft" and c3.review.sha256 == HAND_EDITED and is_hand_edited(c3), str(c3.review))

    # I2. Edit-back: a reviewed text edited, then typed back to its old words,
    # is still a hand-edited draft, never "reviewed" again without an accept.
    bill = fixture_bill(repo_root)
    c3 = clause_of(bill, "c3")
    original = c3.explanation.en
    c3.explanation.sw += " " + BANNED  # a banned phrase gets even a reviewed clause walked
    mark_reviewed(c3)
    drive(bill, ["e", "en", "Changed words.", "e", "en", original, "s"])
    check("I. a reviewed text edited and typed back is a hand-edited draft, not reviewed",
          c3.explanation.en == original and c3.review.status == "draft" and is_hand_edited(c3) and not is_reviewed(c3),
          str(c3.review))

    # I3. Changed since review (here: a tag added in the JSON) is walked, shown
    # as such with its tags, and an accept makes it reviewed again.
    bill = fixture_bill(repo_root)
    c3 = clause_of(bill, "c3")
    mark_reviewed(c3)
    untouched = clause_of(bill, "c4")
    mark_reviewed(untouched)
    c3.groups = [*c3.groups, "student"]
    run_i3 = drive(bill, ["a", "s"])
    check("I. a reviewed clause changed since is walked first, shown as changed, with its groups",
          run_i3.result.visited[:1] == ["c3"] and "reviewed, but changed since" in run_i3.shown
          and "GROUPS: " in run_i3.shown and "student" in run_i3.shown, str(run_i3.result.visited))
    check("I. accepting it fingerprints the new content: reviewed again, today",
          is_reviewed(c3) and c3.review.at == today, str(c3.review))
    check("I. a reviewed clause still as accepted is not walked, and is counted as already reviewed",
          "c4" not in run_i3.result.visited and run_i3.result.not_drafts["reviewed"] == 1, str(run_i3.result.not_drafts))

    # I4. The shared vectors: this side's fingerprints must equal the file the
    # site's twin is checked against (scripts/check-build.mjs), or the two
    # could drift apart while each passes its own tests.
    from . import hashvectors

    recorded = json.loads(repo_root.joinpath(*hashvectors.FIXTURE).read_text(encoding="utf-8"))["vectors"]
    check("I. models.current_sha256 matches every shared vector (regenerate: python -m politricks.hashvectors)",
          len(recorded) > 0 and recorded == hashvectors.build(), f"{len(recorded)} recorded")

    # === Scenario J: one writer at a time (reviewlock.py) ======================
    # A temp repo with a copy of the test bill. stdin is emptied for the run()
    # call, so if the lock ever stops working the walk ends at its first prompt
    # (and the check fails) instead of waiting for someone to type.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pipeline" / "prompts").mkdir(parents=True)
        shutil.copy(repo_root / "pipeline" / "prompts" / "banned.txt", root / "pipeline" / "prompts")
        path = bill_path(root, "finance-act-2026")
        save_bill(fixture_bill(repo_root), path)
        before = sha256_file(path)
        real_stdin = sys.stdin
        with bill_lock(path):
            try:
                with bill_lock(path):
                    second = "taken twice"
            except BillLocked:
                second = "refused"
            sys.stdin = io.StringIO("")
            try:
                with contextlib.redirect_stdout(io.StringIO()) as said:
                    code = run(root, "finance-act-2026")
            finally:
                sys.stdin = real_stdin
        check("J. a second lock on the same bill is refused at once", second == "refused", second)
        check("J. a walk on a bill another writer holds refuses before asking anything (exit 1), bill unchanged",
              code == 1 and "is open in another" in said.getvalue() and sha256_file(path) == before, said.getvalue())
        try:
            with bill_lock(path):
                again = "taken again"
        except BillLocked:
            again = "still held"
        check("J. once let go, the lock can be taken again", again == "taken again", again)

    # === Scenario K: narrowing the walk (--flagged, --only) ====================
    # The trap this closes (2026-10-22): re-accepting ONE flagged item walked on
    # into every draft, and answering "a" each time accepted 59 items. The trap
    # bill rebuilds that shape: one reviewed clause changed since (c3, a tag
    # added), plus the fixture's drafts (c4, group:trader).
    def trap_bill() -> Bill:
        bill = fixture_bill(repo_root)
        c3 = clause_of(bill, "c3")
        mark_reviewed(c3)
        c3.groups = [*c3.groups, "student"]
        return bill

    # Control: without a filter, the same answers really do take the drafts too,
    # so the checks after it test the filter and not a bill with nothing to trap.
    bill = trap_bill()
    run_k0 = drive(bill, ["a", "a", "a"])
    check("K. control: the full walk accepts the drafts after the flagged item too (the trap)",
          run_k0.result.accepted == ["c3", "c4", "group:trader"], str(run_k0.result.accepted))

    bill = trap_bill()
    run_k1 = drive(bill, ["a", "a", "a"], flagged_only=True)
    check("K. --flagged: the same answers accept the flagged c3 alone, and the rest of the script is never asked",
          run_k1.result.visited == ["c3"] and run_k1.result.accepted == ["c3"] and run_k1.left == ["a", "a"],
          f"visited {run_k1.result.visited}, left {run_k1.left}")
    check("K. --flagged: c3 is reviewed again, the drafts are untouched, and the walk ended by itself (no quit)",
          is_reviewed(clause_of(bill, "c3"))
          and clause_of(bill, "c4").review.status == "draft"
          and bill.groups[0].review.status == "draft"
          and not run_k1.result.quit,
          str(clause_of(bill, "c4").review))
    check("K. --flagged says how many drafts it left out",
          "2 other draft(s) left out" in run_k1.shown and run_k1.result.not_drafts["left out"] == 2,
          str(run_k1.result.not_drafts))

    bill = fixture_bill(repo_root)
    run_k2 = drive(bill, [], flagged_only=True)
    check("K. --flagged with nothing flagged walks nothing and finishes clean",
          run_k2.result.visited == [] and not run_k2.result.quit and run_k2.result.ok and "0 text(s) to review" in run_k2.shown,
          run_k2.lines[0] if run_k2.lines else "nothing shown")

    bill = trap_bill()
    run_k3 = drive(bill, ["a", "a"], only=["c4"])
    check("K. --only c4 walks c4 alone, even with a flagged item in the bill",
          run_k3.result.visited == ["c4"] and run_k3.result.accepted == ["c4"] and run_k3.left == ["a"]
          and not is_reviewed(clause_of(bill, "c3")),
          f"visited {run_k3.result.visited}")

    bill = fixture_bill(repo_root)
    run_k4 = drive(bill, ["s", "s"], only=["group:trader", "c3"])
    check("K. --only with two ids walks both, in bill order (clauses before groups)",
          run_k4.result.visited == ["c3", "group:trader"] and run_k4.left == [], str(run_k4.result.visited))

    # A named item is walked even when it is reviewed and unchanged: that is how
    # a typo spotted on the site gets fixed.
    bill = fixture_bill(repo_root)
    c4 = clause_of(bill, "c4")
    mark_reviewed(c4)
    run_k5 = drive(bill, ["s"], only=["c4"])
    check("K. --only walks a named item that is already reviewed (skipping it leaves it reviewed)",
          run_k5.result.visited == ["c4"] and is_reviewed(c4), str(run_k5.result.visited))

    bill = fixture_bill(repo_root)
    errors = selection_errors(bill, ["c3", "c999", "c2", "summary", "group:trader", "group:nobody"])
    check("K. selection_errors refuses an unknown id and a clause with no explanation, and nothing else",
          [error.partition(": ")[0] for error in errors] == ["c999", "c2", "group:nobody"], str(errors))
    try:
        drive(fixture_bill(repo_root), ["a"], only=["c999"])
        bad_id = "walked"
    except ValueError:
        bad_id = "refused"
    try:
        drive(fixture_bill(repo_root), ["a"], flagged_only=True, only=["c3"])
        both = "walked"
    except ValueError:
        both = "refused"
    check("K. walk() itself refuses an unknown id, and --flagged with --only together",
          bad_id == "refused" and both == "refused", f"{bad_id}, {both}")

    # run(): a typo refuses the whole run before anything is shown, bill
    # unchanged. stdin is empty, so a walk that started would end at its first
    # prompt with exit 0, not 1.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pipeline" / "prompts").mkdir(parents=True)
        shutil.copy(repo_root / "pipeline" / "prompts" / "banned.txt", root / "pipeline" / "prompts")
        path = bill_path(root, "finance-act-2026")
        save_bill(fixture_bill(repo_root), path)
        before = sha256_file(path)
        real_stdin = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            with contextlib.redirect_stdout(io.StringIO()) as said:
                code = run(root, "finance-act-2026", only=["c3", "c999"])
        finally:
            sys.stdin = real_stdin
        check("K. run --only with an unknown id exits 1 before showing any item, bill unchanged",
              code == 1 and "c999: no such item" in said.getvalue() and "text(s) to review" not in said.getvalue()
              and sha256_file(path) == before,
              said.getvalue())

    # === Nothing written ========================================================
    check("the real bill file's SHA-256 is unchanged", sha256_file(real_path) == sha_before)

    return check.finish()
