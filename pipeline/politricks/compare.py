"""Compare two versions of a bill clause by clause (devpost/spec.md > Compare).

`politricks compare <bill-id>` sets the bill's own clauses (BEFORE: the first
published text, from a PDF text layer, fingerprinted) beside the later version
(AFTER: the National Assembly-approved text, read by `politricks ocr` from a
scan) and writes a `changes[]` entry for every clause that differs. It never
touches a clause text: clauses stay exactly as `extract` made them.

1. MATCH clauses. By number first (the spec's rule), but a bill passed with
   amendments is often renumbered, so the match is made by an order-preserving
   alignment that scores every pair by how alike the two texts are and adds a
   bonus when the numbers agree. A clause no after-clause is alike enough to is
   DROPPED (`after: null`); an after-clause no before-clause matches is ADDED
   (`before: null`).
2. DECIDE which matched pairs are real changes. Both texts are reduced to
   lowercase letters and digits (so spacing, punctuation, quotes and dashes
   can't differ) and look-alike characters are folded (l/1/i, o/0). They are
   then diffed at character level, and every differing block is judged:

       NOISE_CHARS = 2: a block of up to 2 characters with no digit 2 to 9 in
       it is what a scan does ("rn" for "m", "c" for "e", a speck). It is NOT a
       change. A block of 3 or more characters, or any block with a digit 2 to
       9, is a real difference: it is what an amendment does ("shall" to "may",
       "30 days" to "14").

   A pair whose texts are the same after squashing is `same` and produces
   nothing. A pair that differs ONLY by noise blocks is `borderline`: it is NOT
   written, and it is printed in full (every block, with context) so a human
   decides. After checking the PDF page, `--include c7` makes a borderline pair
   a change. Nothing is silently dropped and nothing is silently kept.
3. ASK Claude, in ONE direct call (generate's `_call_direct`: same model, effort,
   refusal fallback and usage log), for a short neutral note in English,
   Kiswahili and Sheng for each change. The system prompt is
   `pipeline/prompts/comparer.md` plus the rules sections of `translator.md`
   (single-sourced: the Sheng and neutrality rules live there only). A note is
   thrown out if it keeps a dash character or is empty, and the model can say a
   pair is only OCR noise, in which case it is listed and not written.
4. WRITE the changes as drafts, under the bill's lock with `save_bill`, each with
   `review.sha256` set to its fingerprint (`models.current_sha256`, which for a
   change covers kind, clause id, both texts and the three note texts).

`before` and `after` are legal text and are never linted; the notes are.
A change a human reviewed or edited is never overwritten, and a re-run with
nothing new makes no API call and writes no byte. `--dry-run` prints the
matched pairs, scores and what would be sent, with no call and no write.

`politricks compare --self-test` proves all of this offline in a temp folder,
with a fake client and fake OCR text.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from pydantic import BaseModel, ConfigDict

from . import generate
from . import ocr as ocr_mod
from .hashing import normalise, sha256_bytes, sha256_file
from .lint import LintError, iter_texts, lint_bill, load_phrases
from .models import (
    Bill,
    Change,
    Review,
    ShengSwap,
    Source,
    Tri,
    bill_path,
    current_sha256,
    is_protected,
    load_bill,
    save_bill,
)
from .reviewlock import BillLocked, bill_lock
from .selftest import Checker

_STRICT = ConfigDict(extra="forbid")

# The id this command's one call goes by in the usage log
# (`pipeline/.cache/batches/<bill-id>.direct.jsonl`, shared with generate).
CALL_ID = "compare"

# A differing block this long or shorter (characters, after squashing and
# folding) is OCR noise. See the module docstring for why 2.
NOISE_CHARS = 2
# Differing blocks with an equal run of this many characters or fewer between
# them are one block (see `classify`).
MERGE_GAP = 3
# Digits 2 to 9 are never noise: a number is exactly what a reader must not
# get wrong, and a scan rarely turns one of these into another. (0 and 1 are
# folded with o and l, which a scan does confuse; those blocks are marked
# `digit_like` in the report so the human looks at them first.)
SURE_DIGITS = "23456789"
# Alignment: a pair is a candidate match if sim + (bonus when the clause
# numbers agree) reaches MATCH_MIN.
MATCH_MIN = 0.55
NUMBER_BONUS = 0.10
# A "changed" pair this unlike is more likely a segmentation or OCR failure than
# an amendment: it is still written (the human may be right to see it) but the
# report says so.
LOW_SIMILARITY = 0.50

_FOLD = str.maketrans({"1": "l", "i": "l", "|": "l", "!": "l", "0": "o"})
_LEADING_NUMBER = re.compile(r"^\s*\d{1,3}[A-Za-z]?\.\s*")


class CompareError(Exception):
    """The comparison can't be made. The message says exactly what and where."""


# --------------------------------------------------------------------------
# Comparing two texts
# --------------------------------------------------------------------------


def squash(text: str) -> str:
    """Lowercase letters and digits only: spacing, punctuation, quotes, dashes
    and the clause's leading number are all gone."""
    return squash_with_positions(text)[0]


def squash_with_positions(text: str) -> tuple[str, list[int], str]:
    """`squash`, plus where each kept character sits in the number-stripped
    text (so a difference can be shown in the words a human can read) and that
    stripped text itself."""
    stripped = _LEADING_NUMBER.sub("", text, count=1)
    kept: list[str] = []
    where: list[int] = []
    for index, char in enumerate(stripped):
        for part in unicodedata.normalize("NFKC", char).lower():
            if part.isalnum():
                kept.append(part)
                where.append(index)
    return "".join(kept), where, stripped


def _readable(stripped: str, where: list[int], i1: int, i2: int, reach: int = 28) -> str:
    """The words around squashed characters i1..i2, as printed in the text."""
    start = where[i1] if i1 < len(where) else len(stripped)
    end = where[i2 - 1] + 1 if i2 > i1 and i2 - 1 < len(where) else start
    return normalise(stripped[max(0, start - reach) : end + reach])


def fold(squashed: str) -> str:
    """Length-preserving look-alike folding (1/l/i/|/! to l, 0 to o)."""
    return squashed.translate(_FOLD)


@dataclass
class Block:
    """One place two texts differ (in squashed characters)."""

    before: str  # the differing characters, squashed
    after: str
    before_words: str  # the same place in the words as printed, with some context
    after_words: str
    noise: bool
    digit_like: bool  # holds a 0 or a 1 (folded with o and l): look at these first

    def describe(self) -> str:
        flag = " [0/1 look-alike]" if self.digit_like else ""
        return (f"before {self.before!r} -> after {self.after!r}{flag}\n"
                f'                before: "...{self.before_words}..."\n'
                f'                after:  "...{self.after_words}..."')


@dataclass
class Verdict:
    kind: str  # "same", "borderline" or "changed"
    ratio: float
    blocks: list[Block] = field(default_factory=list)


def classify(before_text: str, after_text: str) -> Verdict:
    """Same, borderline (only noise-sized differences) or changed. Pure."""
    a, where_a, text_a = squash_with_positions(before_text)
    b, where_b, text_b = squash_with_positions(after_text)
    fa, fb = fold(a), fold(b)
    matcher = SequenceMatcher(None, fa, fb, autojunk=False)
    # The diff can split ONE real replacement into small pieces that happen to
    # share a letter ("may" to "shall" keeps the "a": two 1-to-2 character
    # blocks). Differing blocks separated by a short equal run are therefore
    # one block, judged by its whole span, or a whole replaced word would pass
    # as two harmless specks.
    spans: list[list[int]] = []  # [i1, i2, j1, j2]
    previous_equal = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            previous_equal = i2 - i1
            continue
        if spans and previous_equal <= MERGE_GAP:
            spans[-1][1], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
        previous_equal = 0
    blocks: list[Block] = []
    for i1, i2, j1, j2 in spans:
        left, right = a[i1:i2], b[j1:j2]
        weight = max(i2 - i1, j2 - j1)
        sure = any(char in SURE_DIGITS for char in left + right)
        blocks.append(
            Block(
                before=left, after=right,
                before_words=_readable(text_a, where_a, i1, i2), after_words=_readable(text_b, where_b, j1, j2),
                noise=weight <= NOISE_CHARS and not sure,
                digit_like=any(char in "01" for char in left + right),
            )
        )
    ratio = matcher.ratio()
    if not blocks:
        return Verdict("same", 1.0)
    return Verdict("borderline" if all(block.noise for block in blocks) else "changed", ratio, blocks)


# --------------------------------------------------------------------------
# Matching the two versions' clauses
# --------------------------------------------------------------------------


@dataclass
class Item:
    """One clause of either version."""

    number: str
    id: Optional[str]  # the bill's clause id for a before item; None for an after item
    text: str
    pages: list[int]
    heading: str = ""


@dataclass
class Pair:
    before: Optional[Item]
    after: Optional[Item]
    verdict: str  # same, borderline, changed, added, dropped
    ratio: float = 0.0
    blocks: list[Block] = field(default_factory=list)

    @property
    def key(self) -> str:
        """The change's clause_id: the before clause's id, or added:<number>."""
        return self.before.id if self.before is not None else f"added:{self.after.number}"  # type: ignore[union-attr]


def _similarity(a: Item, b: Item) -> float:
    fa, fb = fold(squash(a.text)), fold(squash(b.text))
    if not fa or not fb:
        return 0.0
    matcher = SequenceMatcher(None, fa, fb, autojunk=False)
    # quick_ratio is an upper bound that is cheap to compute: skip the full diff
    # when even the bound can't make this pair a candidate.
    if matcher.quick_ratio() + NUMBER_BONUS < MATCH_MIN:
        return 0.0
    return matcher.ratio()


def align(before: list[Item], after: list[Item]) -> list[tuple[Optional[int], Optional[int]]]:
    """Order-preserving matching of before to after clauses, best total score.

    Returns pairs of indexes in document order: (i, j) a match, (i, None) a
    dropped clause, (None, j) an added one. A pair is only a candidate when its
    texts are alike enough (see MATCH_MIN / NUMBER_BONUS), so a clause that was
    rewritten under the same number still matches if at least 45% alike (the number is
    worth 0.10) while two unrelated clauses never do.
    """
    n, m = len(before), len(after)
    score = [[0.0] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            sim = _similarity(before[i], after[j])
            total = sim + (NUMBER_BONUS if before[i].number == after[j].number else 0.0)
            score[i][j] = total if total >= MATCH_MIN and (sim > 0 or before[i].number == after[j].number) else -1.0
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = [best[i - 1][j], best[i][j - 1]]
            if score[i - 1][j - 1] >= 0:
                options.append(best[i - 1][j - 1] + score[i - 1][j - 1])
            best[i][j] = max(options)
    out: list[tuple[Optional[int], Optional[int]]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and score[i - 1][j - 1] >= 0 and abs(best[i][j] - (best[i - 1][j - 1] + score[i - 1][j - 1])) < 1e-12:
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif j > 0 and (i == 0 or abs(best[i][j] - best[i][j - 1]) < 1e-12):
            # Walking backwards, so taking the ADDED step first puts the
            # dropped clause before the added one in the final order.
            out.append((None, j - 1))
            j -= 1
        else:
            out.append((i - 1, None))
            i -= 1
    out.reverse()
    return out


def plan_pairs(before: list[Item], after: list[Item]) -> list[Pair]:
    pairs: list[Pair] = []
    for i, j in align(before, after):
        if i is not None and j is not None:
            verdict = classify(before[i].text, after[j].text)
            pairs.append(Pair(before[i], after[j], verdict.kind, verdict.ratio, verdict.blocks))
        elif i is not None:
            pairs.append(Pair(before[i], None, "dropped"))
        else:
            pairs.append(Pair(None, after[j], "added"))  # type: ignore[index]
    return pairs


# --------------------------------------------------------------------------
# The report (every pair, so nothing is silent)
# --------------------------------------------------------------------------


def _preview(text: str, width: int = 70) -> str:
    text = normalise(text)
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def _where(pair: Pair) -> str:
    left = pair.before.id if pair.before is not None else "-"
    right = pair.after.number if pair.after is not None else "-"
    renumbered = (
        pair.before is not None and pair.after is not None and pair.before.number != pair.after.number
    )
    return f"{left:>8} <-> {right}" + (" (renumbered)" if renumbered else "")


def report_pairs(pairs: list[Pair], include: set[str]) -> list[str]:
    lines = []
    for pair in pairs:
        head = f"  {_where(pair):<34} {pair.verdict.upper():<10}"
        if pair.verdict in ("same", "borderline", "changed"):
            head += f" sim {pair.ratio:.3f}"
        if pair.verdict == "borderline" and pair.key in include:
            head += "  (INCLUDED by --include)"
        if pair.verdict == "changed" and pair.ratio < LOW_SIMILARITY:
            head += "  LOW SIMILARITY: check the OCR page and the clause split before trusting this"
        lines.append(head)
        for block in pair.blocks:
            tag = "noise " if block.noise else "REAL  "
            lines.append(f"        {tag}{block.describe()}")
        if pair.verdict in ("added", "dropped"):
            side = pair.after if pair.verdict == "added" else pair.before
            lines.append(f"        {_preview(side.text)}")  # type: ignore[union-attr]
    return lines


# --------------------------------------------------------------------------
# What is wanted in changes[], and what is asked of Claude
# --------------------------------------------------------------------------


@dataclass
class Wanted:
    """A change without its note yet."""

    key: str
    kind: str  # "changed", "added" or "dropped" (the prompt's word)
    before: Optional[str]
    after: Optional[str]
    before_number: Optional[str]
    after_number: Optional[str]
    heading: str
    after_pages: list[int]


def wanted_from(pairs: list[Pair], include: set[str]) -> list[Wanted]:
    out = []
    for pair in pairs:
        if pair.verdict == "same" or (pair.verdict == "borderline" and pair.key not in include):
            continue
        out.append(
            Wanted(
                key=pair.key,
                kind={"borderline": "changed"}.get(pair.verdict, pair.verdict),
                before=pair.before.text if pair.before is not None else None,
                after=pair.after.text if pair.after is not None else None,
                before_number=pair.before.number if pair.before is not None else None,
                after_number=pair.after.number if pair.after is not None else None,
                heading=pair.before.heading if pair.before is not None else "",
                after_pages=list(pair.after.pages) if pair.after is not None else [],
            )
        )
    return out


class NoteOut(BaseModel):
    model_config = _STRICT
    id: str
    note: generate.TriOut
    only_ocr_noise: bool
    sheng_swaps: list[generate.SwapOut]


class NotesOut(BaseModel):
    model_config = _STRICT
    items: list[NoteOut]


# The sections of translator.md this prompt reuses. They are NOT copied here:
# the Sheng and neutrality rules live in one file (Iron Law X), and a rename
# there fails loudly (`_translator_sections`) instead of quietly dropping a rule.
REUSED_SECTIONS = (
    "What to copy exactly, never translate",
    "What never to say",
    "Punctuation",
    "The two languages",
    "Sheng words you kept in Kiswahili (`sheng_swaps`)",
)


def _translator_sections(repo_root: Path) -> str:
    text = (repo_root / "pipeline" / "prompts" / "translator.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(?=## )", text)
    by_title = {part.splitlines()[0].removeprefix("## ").strip(): part.rstrip() for part in parts if part.startswith("## ")}
    missing = [title for title in REUSED_SECTIONS if title not in by_title]
    if missing:
        raise CompareError(f"translator.md no longer has the section(s) {missing}: comparer.md reuses them, so fix one of the two files")
    return "\n\n".join(by_title[title] for title in REUSED_SECTIONS)


def system_prompt(repo_root: Path) -> str:
    own = (repo_root / "pipeline" / "prompts" / "comparer.md").read_text(encoding="utf-8").rstrip()
    return own + "\n\n" + _translator_sections(repo_root) + "\n"


def build_params(repo_root: Path, bill: Bill, wanted: list[Wanted], before_label: str, after_label: str) -> dict:
    """The Messages API params for every note in one call: generate's model and
    effort and its schema rules, with this command's own prompt and schema."""
    schema = NotesOut.model_json_schema()
    assert generate._all_objects_forbid_additional(schema), "NotesOut schema must forbid extra properties"
    blocks = []
    for item in wanted:
        for text in (item.before, item.after):
            if text is not None and re.search(r"</?(change|before|after)\b", text, re.IGNORECASE):
                raise CompareError(f"{item.key}: the clause text contains a <change>/<before>/<after> tag, which the request can't carry safely")
        parts = [f'<change id="{item.key}" kind="{item.kind}">']
        if item.before is not None:
            parts.append(f"<before>\n{item.before}\n</before>")
        if item.after is not None:
            parts.append(f"<after>\n{item.after}\n</after>")
        parts.append("</change>")
        blocks.append("\n".join(parts))
    user = (
        f"Bill: {bill.title}\n"
        f"before = {before_label}\nafter = {after_label}\n\n"
        "Write one note for every item below. Return one result for every id.\n\n" + "\n\n".join(blocks)
    )
    return {
        "model": generate.MODEL,
        "max_tokens": generate.MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [{"type": "text", "text": system_prompt(repo_root), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": generate.EFFORT},
    }


_DASHES = ("—", "–")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def note_problems(item: Wanted, out: NoteOut) -> list[str]:
    """Why a note can't be used (empty = usable)."""
    problems = []
    for lang, text in (("en", out.note.en), ("sw", out.note.sw), ("sheng", out.note.sheng)):
        if not text.strip():
            problems.append(f"{lang} is empty")
        for dash in _DASHES:
            if dash in text:
                problems.append(f"{lang} has a dash character (U+{ord(dash):04X})")
    if item.kind in ("added", "dropped") and out.only_ocr_noise:
        problems.append(f"the answer marked a clause that is {item.kind} as OCR noise")
    return problems


def number_warnings(item: Wanted, out: NoteOut) -> list[str]:
    """Numbers the English note mentions that appear in neither text (a hint of
    an invented fact; not a blocker: a text may spell a number in words)."""
    known = " ".join(filter(None, [item.before, item.after, item.before_number, item.after_number]))
    return [n for n in dict.fromkeys(_NUMBER.findall(out.note.en)) if n not in _NUMBER.findall(known)]


def collect_notes(
    wanted: list[Wanted], outcome: generate.Outcome
) -> tuple[dict[str, NoteOut], dict[str, str], list[str]]:
    """Match the model's answer to the items asked for.

    Returns (usable notes by key, the reason each other key has none, warnings).
    A missing, repeated or unusable item fails that key only."""
    if not isinstance(outcome, generate.Applied):
        reason = getattr(outcome, "reason", type(outcome).__name__)
        return {}, {item.key: reason for item in wanted}, []
    seen: dict[str, list[NoteOut]] = {}
    for item_out in outcome.parsed.items:
        seen.setdefault(item_out.id, []).append(item_out)
    good: dict[str, NoteOut] = {}
    bad: dict[str, str] = {}
    warnings: list[str] = []
    for item in wanted:
        answers = seen.get(item.key, [])
        if not answers:
            bad[item.key] = "no result came back for this clause"
        elif len(answers) > 1:
            bad[item.key] = "the answer repeated this id"
        else:
            problems = note_problems(item, answers[0])
            if problems:
                bad[item.key] = "; ".join(problems)
            else:
                good[item.key] = answers[0]
                for number in number_warnings(item, answers[0]):
                    warnings.append(f"CHECK {item.key}: the English note mentions {number}, which is in neither text as a number")
    for stray in sorted(set(seen) - {item.key for item in wanted}):
        warnings.append(f"note: the answer had an id nobody asked for ({stray!r}); ignored")
    return good, bad, warnings


# --------------------------------------------------------------------------
# Merging with what the bill already holds
# --------------------------------------------------------------------------


@dataclass
class Plan:
    """What to do with each wanted change, given the bill's existing ones."""

    keep: dict[str, Change] = field(default_factory=dict)  # a human's change, or an up-to-date draft
    ask: list[Wanted] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # printed lines about what was kept and why
    unmatched: list[Change] = field(default_factory=list)  # existing changes no longer found: human ones stay
    drop: list[str] = field(default_factory=list)  # untouched drafts that are no longer a difference


def plan_changes(existing: list[Change], wanted: list[Wanted], force: bool) -> Plan:
    plan = Plan()
    mine = {change.clause_id: change for change in existing if change.kind == "draft"}
    wanted_keys = {item.key for item in wanted}
    for item in wanted:
        old = mine.get(item.key)
        if old is None:
            plan.ask.append(item)
            continue
        same_texts = old.before == item.before and old.after == item.after
        if is_protected(old):
            why = "reviewed" if old.review.status == "reviewed" else "hand-edited"
            line = f"  {item.key}: a human's note ({why}): kept"
            if not same_texts:
                line += "; the texts being compared have changed since, and it is NOT updated (clear its review first if the new texts should win)"
            plan.notes.append(line)
            plan.keep[item.key] = old
        elif same_texts and not force and old.review.sha256 == current_sha256(old):
            plan.notes.append(f"  {item.key}: already up to date")
            plan.keep[item.key] = old
        else:
            plan.ask.append(item)
    for key, old in mine.items():
        if key in wanted_keys:
            continue
        if is_protected(old):
            plan.notes.append(f"  {key}: no longer a difference in this comparison, but a human reviewed or edited its note: kept")
            plan.unmatched.append(old)
        else:
            plan.drop.append(key)
    return plan


def build_change(item: Wanted, out: NoteOut, source_label: str, ocr_source: bool) -> Change:
    change = Change(
        kind="draft", clause_id=item.key, heading=item.heading,
        before_number=item.before_number, after_number=item.after_number,
        before=item.before, after=item.after,
        note=Tri(en=out.note.en.strip(), sw=out.note.sw.strip(), sheng=out.note.sheng.strip()),
        source_label=source_label,
        ocr=ocr_source and item.after is not None,
        after_pages=item.after_pages if item.after is not None else [],
        sheng_swaps=[ShengSwap(word=swap.word, english=swap.english) for swap in out.sheng_swaps],
    )
    # Stamped LAST, after every field is set, so the fingerprint matches what is on disk.
    change.review = Review(status="draft", sha256=current_sha256(change))
    return change


# --------------------------------------------------------------------------
# `politricks compare`
# --------------------------------------------------------------------------


@dataclass
class Prepared:
    pairs: list[Pair]
    before_label: str
    after_label: str
    ocr_text_path: Path
    log: list[str]
    after_count: int


def prepare(repo_root: Path, bill: Bill) -> Prepared:
    """Read both versions and match them. Refuses a stale OCR file."""
    ocr_sources = [s for s in bill.sources if s.ocr]
    if len(ocr_sources) != 1:
        raise CompareError(
            f"{bill.id} has {len(ocr_sources)} OCR source(s); compare needs exactly one. Run `politricks ocr {bill.id}` "
            "(its source must be marked `ocr: true` in pipeline/sources.yaml)."
        )
    source = ocr_sources[0]
    plain = [s for s in bill.sources if not s.ocr]
    if not plain or not bill.clauses:
        raise CompareError(f"{bill.id} has no text-layer source with clauses to compare against: run `politricks extract {bill.id}` first")
    pdf = repo_root / source.file
    text_path = pdf.parent / f"{pdf.stem}.ocr.txt"
    try:
        ocr = ocr_mod.read_ocr_text(text_path)
        ocr_mod.require_fresh(ocr, source.sha256, text_path, bill.id)
        if pdf.exists() and sha256_file(pdf) != source.sha256:
            raise ocr_mod.OcrError(
                f"the PDF on disk ({sha256_file(pdf)[:16]}...) is not the one the bill records ({source.sha256[:16]}...): "
                f"run `politricks ocr {bill.id} --force`"
            )
        segmentation = ocr_mod.segment_ocr(ocr, ocr_mod.load_ocr_overrides(repo_root, bill.id))
    except ocr_mod.OcrError as error:
        raise CompareError(str(error)) from error
    before = [Item(c.number, c.id, c.text, list(c.pages), c.heading) for c in bill.clauses]
    after = [Item(c.number, None, c.text, list(c.pages)) for c in segmentation.clauses]
    return Prepared(plan_pairs(before, after), plain[0].label, source.label, text_path, segmentation.log, len(after))


def print_report(repo_root: Path, bill: Bill, prep: Prepared, include: set[str]) -> None:
    counts: dict[str, int] = {}
    for pair in prep.pairs:
        counts[pair.verdict] = counts.get(pair.verdict, 0) + 1
    print(f"before: {len(bill.clauses)} clauses ({prep.before_label})")
    print(f"after:  {prep.after_count} clauses read by OCR ({prep.after_label})")
    print(f"        from {prep.ocr_text_path.relative_to(repo_root).as_posix()}")
    for line in prep.log:
        print(f"  {line}")
    print(f"noise threshold: a differing block of {NOISE_CHARS} characters or fewer, with no digit 2 to 9, is OCR noise")
    for line in report_pairs(prep.pairs, include):
        print(line)
    print("pairs: " + ", ".join(f"{count} {name}" for name, count in sorted(counts.items())))
    borderline = [pair for pair in prep.pairs if pair.verdict == "borderline"]
    for pair in borderline:
        if pair.key not in include:
            pages = f" (PDF page {', '.join(map(str, pair.after.pages))})" if pair.after is not None else ""  # type: ignore[union-attr]
            print(f"BORDERLINE {pair.key}{pages}: differs only by noise-sized blocks, so it is NOT a change yet. "
                  f"Check the PDF, then `--include {pair.key}` if it is real.")
    unknown = include - {pair.key for pair in prep.pairs if pair.verdict == "borderline"}
    if unknown:
        raise CompareError(f"--include names {sorted(unknown)}, which are not borderline pairs in this comparison")


def run(
    repo_root: Path,
    bill_id: str,
    dry_run: bool = False,
    force: bool = False,
    include: list[str] | tuple[str, ...] = (),
    client=None,
) -> int:
    """Returns the exit code: 0 done (or already up to date), 1 anything wrong.

    The OCR text is read, checked for staleness and matched BEFORE the bill's
    lock is taken or any client exists, so a bad file costs nothing. `--dry-run`
    takes no lock, makes no call and writes nothing. `client` exists so the
    self-test can pass a fake.
    """
    try:
        path = bill_path(repo_root, bill_id)
    except ValueError as error:
        print(f"FAILED: {error}")
        return 1
    if not path.exists():
        print(f"FAILED: no bill file at {path}")
        return 1
    try:
        phrases = load_phrases(repo_root)
    except LintError as error:
        print(f"FAILED: {error}")
        return 1
    try:
        if dry_run:
            return _run(repo_root, bill_id, path, phrases, True, force, set(include), client)
        with bill_lock(path):
            return _run(repo_root, bill_id, path, phrases, False, force, set(include), client)
    except BillLocked as error:
        print(f"FAILED: {error} Nothing was sent.")
        return 1
    except CompareError as error:
        print(f"FAILED: {error}")
        return 1


def _run(repo_root: Path, bill_id: str, path: Path, phrases: list[str], dry_run: bool, force: bool,
         include: set[str], client) -> int:
    bill = load_bill(path)
    prep = prepare(repo_root, bill)
    mode = " (dry run: no API call, nothing written)" if dry_run else ""
    print(f"compare {bill_id}{mode}")
    print_report(repo_root, bill, prep, include)
    wanted = wanted_from(prep.pairs, include)
    plan = plan_changes(bill.changes, wanted, force)
    for line in plan.notes:
        print(line)
    for key in plan.drop:
        print(f"  {key}: no longer a difference: the untouched draft is removed")

    params = None
    if plan.ask:
        params = build_params(repo_root, bill, plan.ask, prep.before_label, prep.after_label)
        print(f"{'would ask' if dry_run else 'asking'} Claude for {len(plan.ask)} note(s) in one direct call: "
              + ", ".join(f"{item.key} ({item.kind})" for item in plan.ask))
    elif not plan.drop:
        print("nothing to ask Claude, nothing to change")
    if dry_run:
        if params is not None:
            size = len(params["messages"][0]["content"]) + len(params["system"][0]["text"])
            print(f"request: model {params['model']}, effort {params['output_config']['effort']}, "
                  f"about {size:,} characters (system prompt {len(params['system'][0]['text']):,})")
        return 0

    answers: dict[str, NoteOut] = {}
    failures: dict[str, str] = {}
    if plan.ask:
        if client is None:
            try:
                import anthropic

                client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
            except Exception as error:
                print(f"FAILED: cannot start the Anthropic client ({type(error).__name__}: {error}). Nothing was sent.")
                return 1
        outcome = generate._call_direct(
            client, CALL_ID, params, usage_log=generate._usage_log(repo_root, bill.id), model_cls=NotesOut
        )
        answers, failures, warnings = collect_notes(plan.ask, outcome)
        for line in warnings:
            print(f"  {line}")

    source_label = f"{prep.before_label} compared with {prep.after_label}"
    noise_only = sorted(key for key, out in answers.items() if out.only_ocr_noise)
    for key in noise_only:
        print(f"  {key}: Claude read this as OCR noise, not a change, so it is NOT written. Check the PDF if you disagree.")
    built = {item.key: build_change(item, answers[item.key], source_label, True)
             for item in plan.ask if item.key in answers and not answers[item.key].only_ocr_noise}

    # The new list, in document order; human changes whose clause is gone stay at the end.
    others = [change for change in bill.changes if change.kind != "draft"]
    ordered: list[Change] = []
    for item in wanted:
        if item.key in plan.keep:
            ordered.append(plan.keep[item.key])
        elif item.key in built:
            ordered.append(built[item.key])
        else:
            # Not applied this time (a failure, or noise): an untouched earlier draft stays as it was.
            old = next((c for c in bill.changes if c.kind == "draft" and c.clause_id == item.key), None)
            if old is not None and item.key not in noise_only:
                ordered.append(old)
    ordered += plan.unmatched
    new_changes = others + ordered
    changed = [c.model_dump(mode="json") for c in new_changes] != [c.model_dump(mode="json") for c in bill.changes]
    if changed:
        bill.changes = new_changes
        save_bill(bill, path)
    wrote = [item.key for item in wanted if item.key in built]  # document order
    print(f"wrote {len(wrote)} change(s) as drafts: {', '.join(wrote)}" if wrote else "wrote no new change")

    code = 0
    for key, reason in failures.items():
        print(f"  NOT APPLIED {key}: {reason}")
        code = 1
    if failures:
        print(f"{len(failures)} note(s) not applied; run again for those.")
    findings = [f for f in lint_bill(bill, phrases) if f.item.startswith("changes[")]
    for finding in findings:
        print(finding.line())
        code = 1
    if findings:
        print(f"{len(findings)} banned phrase(s) in the change notes: edit them in `politricks review` after fixing the text")
    return code


# --------------------------------------------------------------------------
# Self-test: offline, in a temp folder, with a fake client and fake OCR text
# --------------------------------------------------------------------------

TEST_ID = "test-compare-bill"
OCR_LABEL = "Example Bill (as approved: text read by OCR)"
FIRST_LABEL = "Example Bill (as first published)"

# Invented text, never real law.
BEFORE_CLAUSES = [
    ("1", 'This Act may be cited as the "Example" Act, 2026.', "Short title."),
    ("2", "A person shall file a return within thirty days of the end of the year.", "Returns."),
    ("3", "The Minister may make rules for the better carrying out of this Act and the Minister shall publish them in the Gazette.", "Rules."),
    ("4", "A person who fails to file a return commits an offence and is liable to a fine not exceeding ten thousand shillings.", "Offence."),
    ("5", "The Council shall keep a register of every return filed under this Act and shall make the register available to the public.", "Register."),
    ("6", "This Act comes into force on the date of assent.", "Commencement."),
]
# What the scan says. Each differs from BEFORE in exactly one way (see the self-test).
AFTER_LINES = {
    2: [
        "ENACTED by the Parliament of Kenya, as follows",
        "1.  This Act may be cited as the “Example” Act,2026.",  # same after squashing: quotes, spacing
        "2. A person must file a return within fourteen days of the end of the year.",  # changed: must / fourteen
        "3. The Minister may make rules for the better carrying out of this Act and the Minister shall publish them in the Gazetle.",  # noise: t -> l
    ],
    3: [
        "4, The Council shall keep a register of every return filed under this Act and shall make the register available to the public.",  # renumbered (was 5), '4,' read through noise
        "5. The Minister shall report to Parliament every year on the working of this Act.",  # added
        "6. This Act comes into force on the date of assent.",  # same
    ],
}


def _ocr_file(root: Path, sha: str, pages: dict[int, list[str]], name: str = "approved.ocr.txt") -> Path:
    folder = root / "sources" / TEST_ID
    folder.mkdir(parents=True, exist_ok=True)
    reads = [ocr_mod.PageRead(number, lines, words=len(lines) * 5) for number, lines in sorted(pages.items())]
    path = folder / name
    path.write_text(ocr_mod.format_ocr_text(f"sources/{TEST_ID}/approved.pdf", sha, "fake", reads), encoding="utf-8", newline="\n")
    return path


def _make_bill(root: Path, pdf_bytes: bytes) -> Bill:
    from .hashing import sha256_text

    clauses = []
    for number, text, heading in BEFORE_CLAUSES:
        full = f"{number}. {text}"
        from .models import Clause

        clauses.append(Clause(id=f"c{number}", number=number, heading=heading, text=normalise(full),
                              text_sha256=sha256_text(full), pages=[3]))
    folder = root / "sources" / TEST_ID
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "approved.pdf").write_bytes(pdf_bytes)
    bill = Bill(
        id=TEST_ID, title="The Example Bill, 2026", chamber="national", kind="bill", status="active", stage="Test",
        stage_checked_at="2026-10-23", clauses=clauses,
        sources=[
            Source(label=FIRST_LABEL, url="https://www.parliament.go.ke/test/first.pdf", official=True,
                   fetched_at="2026-10-01T00:00:00+00:00", sha256="0" * 64, file=f"sources/{TEST_ID}/bill.pdf"),
            Source(label=OCR_LABEL, url="https://www.parliament.go.ke/test/approved.pdf", official=True,
                   fetched_at="2026-10-02T00:00:00+00:00", sha256=sha256_bytes(pdf_bytes), file=f"sources/{TEST_ID}/approved.pdf", ocr=True),
        ],
    )
    save_bill(bill, bill_path(root, TEST_ID))
    return bill


def _temp_root(repo_root: Path, tmp: Path) -> tuple[Path, bytes]:
    for rel in ("pipeline/prompts/comparer.md", "pipeline/prompts/translator.md", "pipeline/prompts/banned.txt"):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo_root / rel, tmp / rel)
    (tmp / "pipeline" / "overrides").mkdir(parents=True, exist_ok=True)
    (tmp / "pipeline" / "overrides" / f"{TEST_ID}.ocr.yaml").write_text("expected_clause_count: 6\n", encoding="utf-8", newline="\n")
    (tmp / "src" / "data" / "bills").mkdir(parents=True, exist_ok=True)
    pdf_bytes = b"%PDF-1.4 a stand-in whose bytes are the fingerprint\n"
    _make_bill(tmp, pdf_bytes)
    _ocr_file(tmp, sha256_bytes(pdf_bytes), AFTER_LINES)
    return tmp, pdf_bytes


class _FakeClient:
    """Just enough of `anthropic.Anthropic` for generate._call_direct: records
    each call and answers from `answer(id, kind, before, after) -> (en, sw, sheng, noise)`."""

    def __init__(self, answer=None, error: Exception | None = None, drop: tuple[str, ...] = ()) -> None:
        self.calls: list[dict] = []
        self._answer = answer or (lambda i, kind, b, a: (f"EN {kind} {i}", f"SW {kind} {i}", f"SHENG {kind} {i}", False))
        self._error = error
        self._drop = drop
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, *, output_format, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        user = kwargs["messages"][0]["content"]
        items = []
        for match in re.finditer(r'<change id="([^"]+)" kind="([^"]+)">\n(.*?)\n</change>', user, re.DOTALL):
            key, kind, body = match.groups()
            if key in self._drop:
                continue
            before = re.search(r"<before>\n(.*?)\n</before>", body, re.DOTALL)
            after = re.search(r"<after>\n(.*?)\n</after>", body, re.DOTALL)
            en, sw, sheng, noise = self._answer(key, kind, before.group(1) if before else None, after.group(1) if after else None)
            items.append({"id": key, "note": {"en": en, "sw": sw, "sheng": sheng}, "only_ocr_noise": noise, "sheng_swaps": []})
        parsed = output_format.model_validate({"items": items})
        return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed, model="fake-model", usage=None)


def _quiet(fn, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = fn(*args, **kwargs)
    return result, buffer.getvalue()


def self_test(repo_root: Path) -> bool:
    check = Checker()
    phrases = load_phrases(repo_root)
    real_bills = sorted((repo_root / "src" / "data" / "bills").glob("*.json"))
    watched = real_bills + [repo_root / "pipeline" / "sources.yaml"] + [
        repo_root / "pipeline" / "prompts" / n for n in ("comparer.md", "translator.md", "banned.txt")
    ]
    before_real = {p: sha256_file(p) for p in watched}
    logs = [generate._usage_log(repo_root, p.stem) for p in real_bills]
    logs_before = {log: (log.stat().st_size if log.exists() else None) for log in logs}

    # --- A. one pair of texts at a time ---------------------------------------
    def verdict(a: str, b: str) -> Verdict:
        return classify(a, b)

    check("identical texts: same", verdict("1. A person shall file.", "1. A person shall file.").kind == "same")
    check("spacing, quotes, dashes, case and the leading number are not differences",
          verdict('1. The "Minister"—a person—may act.', "7.  the “minister” - a person-may   act").kind == "same")
    check("words run together by a scan are not a difference ('ofthe' / 'of the')",
          verdict("1. The power of the Minister.", "1. The power ofthe Minister.").kind == "same")
    check("l / 1 / i and o / 0 look-alikes fold away",
          verdict("2. Article 11 and section 10.", "2. Article ll and section 1O.").kind == "same")
    one = verdict("3. This applies to the Gazette.", "3. This applies to the Gazetle.")
    check("one wrong letter is BORDERLINE, with the block shown",
          one.kind == "borderline" and len(one.blocks) == 1 and one.blocks[0].before == "t" and one.blocks[0].after == "l", str(one))
    two = verdict("3. The Exarnple Act.", "3. The Example Act.")
    check("'rn' read as 'm' (2 characters) is borderline", two.kind == "borderline", str(two))
    three = verdict("3. A person shall file.", "3. A person must file.")
    check("'shall' to 'must' is a real change (blocks of 3 or more)", three.kind == "changed" and not three.blocks[0].noise, str(three))
    digit = verdict("4. Within 30 days.", "4. Within 40 days.")
    check("a single changed digit (3 to 4) is a real change, never noise", digit.kind == "changed", str(digit))
    zero_one = verdict("4. Within 10 days.", "4. Within 11 days.")
    check("10 to 11 is only a look-alike block, so it is borderline AND flagged 0/1 look-alike for a human",
          zero_one.kind == "borderline" and zero_one.blocks[0].digit_like, str(zero_one))
    check("an inserted whole word ('not') is a real change", verdict("5. A person shall file.", "5. A person shall not file.").kind == "changed")
    check("a threshold of 0 would turn the borderline pair into a change (the threshold is what decides)",
          _with_noise(0, lambda: verdict("3. Gazette.", "3. Gazetle.").kind) == "changed")

    # --- B. matching: renumbering, additions, drops ---------------------------
    items_b = [Item("1", "c1", "1. Alpha clause about returns and the register of persons.", [1]),
               Item("2", "c2", "2. Beta clause about penalties and fines for offences.", [1]),
               Item("3", "c3", "3. Gamma clause about the commencement of the Act.", [1])]
    items_a = [Item("1", None, "1. Alpha clause about returns and the register of persons.", [2]),
               Item("2", None, "2. A brand new clause about annual reports to Parliament by the Minister.", [2]),
               Item("3", None, "3. Beta clause about penalties and fines for offences.", [2]),
               Item("4", None, "4. Gamma clause about the commencement of the Act.", [2])]
    moved = plan_pairs(items_b, items_a)
    check("an inserted clause renumbers what follows: Beta and Gamma still match their own text",
          [(p.before.id if p.before else None, p.after.number if p.after else None, p.verdict) for p in moved]
          == [("c1", "1", "same"), (None, "2", "added"), ("c2", "3", "same"), ("c3", "4", "same")], str([(p.before and p.before.id, p.after and p.after.number, p.verdict) for p in moved]))
    check("an added pair is keyed added:<number>; a before clause keeps its own id", moved[1].key == "added:2" and moved[0].key == "c1")
    rewritten = plan_pairs([Item("7", "c7", "7. Entirely one wording of a rule about fees and charges.", [1])],
                           [Item("7", None, "7. Another wording of a rule about fees, charges and surcharges to be paid.", [2])])
    check("a rewritten clause under the SAME number is still matched (changed), not dropped and added",
          len(rewritten) == 1 and rewritten[0].verdict == "changed", str([(p.verdict, p.ratio) for p in rewritten]))
    unrelated = plan_pairs([Item("1", "c1", "1. The Council shall keep a register.", [1])], [Item("2", None, "2. Wholly different words about fishing licences and quotas.", [2])])
    check("two unlike clauses under different numbers are never matched: dropped + added",
          [p.verdict for p in unrelated] == ["dropped", "added"], str([p.verdict for p in unrelated]))

    # --- C. the whole command on the fixture bill, dry run first --------------
    with tempfile.TemporaryDirectory(prefix="politricks-compare-") as tmp_name:
        root, pdf_bytes = _temp_root(repo_root, Path(tmp_name))
        bill_file = bill_path(root, TEST_ID)
        sha0 = sha256_file(bill_file)
        quiet_client = _FakeClient(error=RuntimeError("must not be called"))
        rc, out = _quiet(run, root, TEST_ID, dry_run=True, client=quiet_client)
        check("dry run: exit 0, no call, bill untouched", rc == 0 and not quiet_client.calls and sha256_file(bill_file) == sha0, out[-300:])
        want = {
            "c1": "SAME", "c2": "CHANGED", "c3": "BORDERLINE", "c4": "DROPPED", "c5": "SAME", "c6": "SAME",
        }
        rows = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(c\d|-)\s+<->\s+\S+(?:\s+\(renumbered\))?\s+([A-Z]+)", out, re.M)}
        check("dry run prints every before clause's verdict, with similarity scores",
              all(rows.get(k) == v for k, v in want.items()) and re.search(r"sim 0\.\d{3}", out) is not None, f"{rows}\n{out[:900]}")
        check("dry run: the added clause is printed too, and c5 is marked renumbered",
              re.search(r"-\s+<->\s+5\s+ADDED", out) is not None and re.search(r"c5\s+<->\s+4\s+\(renumbered\)\s+SAME", out) is not None, out[:1200])
        check("dry run: the borderline pair is listed in full (block, context, PDF page, how to include it)",
              "BORDERLINE c3" in out and "before 't' -> after 'l'" in out and "--include c3" in out and "PDF page 2" in out, out[-700:])
        check("dry run says what it would send and nothing was asked yet",
              "would ask Claude for 3 note(s)" in out and "c2 (changed)" in out and "c4 (dropped)" in out and "added:5 (added)" in out, out[-600:])

        # --- D. the real run -------------------------------------------------
        client = _FakeClient()
        rc, out = _quiet(run, root, TEST_ID, client=client)
        stored = load_bill(bill_file)
        check("run: exit 0 and exactly ONE call for the whole bill", rc == 0 and len(client.calls) == 1, f"{rc} {len(client.calls)}")
        check("changes: exactly the changed, the dropped and the added clause (same / borderline / renumbered-same produce nothing)",
              [c.clause_id for c in stored.changes] == ["c2", "c4", "added:5"], str([c.clause_id for c in stored.changes]))
        by = {c.clause_id: c for c in stored.changes}
        check("kinds: all 'draft'; before/after null exactly where the clause is added / dropped",
              all(c.kind == "draft" for c in stored.changes) and by["c4"].after is None and by["added:5"].before is None
              and by["c2"].before is not None and by["c2"].after is not None)
        check("before is the bill's own clause text word for word; after is the OCR clause text",
              by["c2"].before == stored.clauses[1].text and by["c4"].before == stored.clauses[3].text
              and by["c2"].after is not None and by["c2"].after.startswith("2. A person must file a return within fourteen days"), str(by["c2"].after))
        check("OCR flags: ocr true and PDF pages on every after text; false and no pages on a dropped clause",
              by["c2"].ocr and by["c2"].after_pages == [2] and by["added:5"].ocr and by["added:5"].after_pages == [3]
              and not by["c4"].ocr and by["c4"].after_pages == [], str([(c.clause_id, c.ocr, c.after_pages) for c in stored.changes]))
        check("numbers: c2 is clause 2 both sides; the added clause has no before number",
              (by["c2"].before_number, by["c2"].after_number) == ("2", "2") and by["added:5"].before_number is None and by["added:5"].after_number == "5")
        check("every note is a draft whose sha256 is its current fingerprint",
              all(c.review.status == "draft" and c.review.sha256 == current_sha256(c) and re.fullmatch(r"[0-9a-f]{64}", c.review.sha256 or "") for c in stored.changes))
        check("the notes are the translator's three languages",
              by["c2"].note == Tri(en="EN changed c2", sw="SW changed c2", sheng="SHENG changed c2"), str(by["c2"].note))
        check("source_label names both versions", FIRST_LABEL in by["c2"].source_label and OCR_LABEL in by["c2"].source_label)
        check("clause texts are untouched (every clause, every field)",
              [c.model_dump() for c in stored.clauses] == [c.model_dump() for c in load_bill(bill_file).clauses]
              and [c.text for c in stored.clauses] == [normalise(f"{n}. {t}") for n, t, _h in BEFORE_CLAUSES])
        # What was sent: the system prompt and the request.
        sent = client.calls[0]
        prompt = sent["system"][0]["text"]
        user = sent["messages"][0]["content"]
        check("request uses generate's model and effort", sent["model"] == generate.MODEL and sent["output_config"]["effort"] == generate.EFFORT)
        check("the system prompt is comparer.md plus translator.md's rules sections (single-sourced), with no em dash",
              prompt.startswith("You are the comparer") and "## What never to say" in prompt and "## Sheng words you kept" in prompt
              and "## The two languages" in prompt and "## Punctuation" in prompt and "What translating means here" not in prompt and "—" not in prompt)
        check("the request carries each pair's texts in <before>/<after> blocks and the two labels",
              '<change id="c2" kind="changed">' in user and "<before>\n2. A person shall file a return within thirty days" in user
              and "<after>\n2. A person must file" in user and f"before = {FIRST_LABEL}" in user and f"after = {OCR_LABEL}" in user
              and '<change id="c4" kind="dropped">' in user and "<after>" not in user.split('<change id="c4"')[1].split("</change>")[0])
        check("borderline c3 and the same pairs are NOT in the request", 'id="c3"' not in user and 'id="c1"' not in user and 'id="c5"' not in user)
        check("answer schema forbids extra properties, and the SDK was given it as the output format",
              generate._all_objects_forbid_additional(NotesOut.model_json_schema()) and "format" not in sent["output_config"]
              and sent["betas"] == [generate.FALLBACK_BETA] and sent["fallbacks"] == "default")
        usage = root / "pipeline" / ".cache" / "batches" / f"{TEST_ID}.direct.jsonl"
        check("the call's usage went to the temp root's log (one line), not the repo's",
              usage.exists() and len(usage.read_text(encoding="utf-8").splitlines()) == 1)

        # lint: notes are linted, before/after never
        check("lint finds nothing in clean notes", [f for f in lint_bill(stored, phrases)] == [])
        check("positive control: lint walks the three languages of all three notes (9 texts)",
              sum(1 for item, _l, _t in iter_texts(stored) if item.startswith("changes[")) == 9)

        # --- E. second run: no call, no byte ---------------------------------
        sha1 = sha256_file(bill_file)
        client2 = _FakeClient(error=RuntimeError("must not be called"))
        rc, out = _quiet(run, root, TEST_ID, client=client2)
        check("second run: exit 0, no call, byte-identical", rc == 0 and not client2.calls and sha256_file(bill_file) == sha1, out[-300:])
        check("second run says each change is already up to date", out.count("already up to date") == 3, out[-500:])
        forced = _FakeClient()
        rc, out = _quiet(run, root, TEST_ID, force=True, client=forced)
        check("control: --force on the same state DOES call (so 'no call' above was not an artefact)", rc == 0 and len(forced.calls) == 1)
        check("--force with a deterministic client gives byte-identical output", sha256_file(bill_file) == sha1)

        # --- F. --include promotes a borderline pair ---------------------------
        client3 = _FakeClient()
        rc, out = _quiet(run, root, TEST_ID, include=["c3"], client=client3)
        after_inc = load_bill(bill_file)
        check("--include c3: the borderline pair becomes a change, and only it is asked for",
              rc == 0 and [c.clause_id for c in after_inc.changes] == ["c2", "c3", "c4", "added:5"]
              and len(client3.calls) == 1 and 'id="c3"' in client3.calls[0]["messages"][0]["content"] and 'id="c2"' not in client3.calls[0]["messages"][0]["content"], out[-400:])
        rc, out = _quiet(run, root, TEST_ID, include=["c1"], client=_FakeClient(error=RuntimeError("no call")))
        check("--include of a pair that is not borderline is refused before any call", rc == 1 and "not borderline pairs" in out, out[-200:])

        # --- G. human work is never overwritten --------------------------------
        def scenario(make_human):
            with tempfile.TemporaryDirectory(prefix="politricks-compare-") as tmp2:
                r, _p = _temp_root(repo_root, Path(tmp2))
                _quiet(run, r, TEST_ID, client=_FakeClient())
                bill = load_bill(bill_path(r, TEST_ID))
                make_human(bill)
                save_bill(bill, bill_path(r, TEST_ID))
                human = load_bill(bill_path(r, TEST_ID))
                # the later text of clause 2 moves, so the pair would be re-asked
                new_lines = {k: [l.replace("fourteen days", "twenty-one days") for l in v] for k, v in AFTER_LINES.items()}
                _ocr_file(r, human.sources[1].sha256, new_lines)
                client_h = _FakeClient()
                rc, text = _quiet(run, r, TEST_ID, client=client_h)
                return client_h, human, load_bill(bill_path(r, TEST_ID)), text

        def review_it(bill: Bill) -> None:
            change = bill.changes[0]
            change.review = Review(status="reviewed", at="2026-01-01", sha256=current_sha256(change))

        def hand_edit(bill: Bill) -> None:
            bill.changes[0].note.sheng = "Hand-written Sheng by Ian."

        client_h, human, after_h, text = scenario(review_it)
        check("a reviewed change is not overwritten when the texts move, nothing else is asked, and the run says so",
              after_h.changes[0] == human.changes[0] and after_h.changes[0].review.status == "reviewed"
              and "(reviewed)" in text and "NOT updated" in text and not client_h.calls, text[:500])
        client_h, human, after_h, text = scenario(hand_edit)
        check("a hand-edited draft is not overwritten either",
              after_h.changes[0].note.sheng == "Hand-written Sheng by Ian." and after_h.changes[0].review.sha256 != current_sha256(after_h.changes[0])
              and "(hand-edited)" in text and not client_h.calls, text[:300])
        client_u, human, after_u, text = scenario(lambda bill: None)
        check("control: an UNTOUCHED draft is re-asked when the texts move, and its stamp follows",
              "twenty-one days" in (after_u.changes[0].after or "") and after_u.changes[0].review.sha256 == current_sha256(after_u.changes[0]) and len(client_u.calls) == 1, text[:300])

        # --- H. the model's answers are checked -------------------------------
        def fresh(answer=None, drop=(), error=None, expect_rc=None):
            with tempfile.TemporaryDirectory(prefix="politricks-compare-") as tmp3:
                r, _p = _temp_root(repo_root, Path(tmp3))
                sha = sha256_file(bill_path(r, TEST_ID))
                rc, text = _quiet(run, r, TEST_ID, client=_FakeClient(answer=answer, drop=drop, error=error))
                return rc, text, load_bill(bill_path(r, TEST_ID)), sha, sha256_file(bill_path(r, TEST_ID))

        rc, text, bill_n, _s, _t = fresh(answer=lambda i, k, b, a: ("No change in meaning.", "Hakuna mabadiliko.", "No change.", True) if i == "c2" else (f"EN {i}", f"SW {i}", f"SHENG {i}", False))
        check("a pair Claude calls OCR noise is listed and NOT written; the others land",
              rc == 0 and "c2: Claude read this as OCR noise" in text and [c.clause_id for c in bill_n.changes] == ["c4", "added:5"], text[-400:])
        rc, text, bill_n, _s, _t = fresh(answer=lambda i, k, b, a: (f"EN {i}", f"SW — {i}", f"SHENG {i}", False) if i == "c4" else (f"EN {i}", f"SW {i}", f"SHENG {i}", False))
        check("a note with an em dash is not applied (exit 1, reason shown); the rest land",
              rc == 1 and "NOT APPLIED c4" in text and "dash character" in text and [c.clause_id for c in bill_n.changes] == ["c2", "added:5"], text[-400:])
        rc, text, bill_n, _s, _t = fresh(drop=("added:5",))
        check("a missing answer is reported by key and exits 1", rc == 1 and "NOT APPLIED added:5: no result came back" in text and [c.clause_id for c in bill_n.changes] == ["c2", "c4"], text[-300:])
        rc, text, bill_n, _s, _t = fresh(answer=lambda i, k, b, a: ("EN", "SW", "SHENG", True) if i == "added:5" else (f"EN {i}", f"SW {i}", f"SHENG {i}", False))
        check("an ADDED clause marked as OCR noise is refused", rc == 1 and "marked a clause that is added as OCR noise" in text, text[-300:])
        rc, text, bill_n, sha, after_sha = fresh(error=RuntimeError("network down"))
        check("a failed call: exit 1, the bill untouched, the reason shown", rc == 1 and sha == after_sha and "network down" in text, text[-250:])
        rc, text, bill_n, _s, _t = fresh(answer=lambda i, k, b, a: ("You should check clause 2.", f"SW {i}", f"SHENG {i}", False) if i == "c2" else (f"EN {i}", f"SW {i}", f"SHENG {i}", False))
        found = [f for f in lint_bill(bill_n, phrases) if f.kind == "banned"]
        check("a banned phrase in a note is saved as a draft, caught by lint on changes[0].note, and the run exits 1",
              rc == 1 and any(f.item == "changes[0].note" and f.lang == "en" and f.detail == "you should*" for f in found)
              and "changes[0].note en" in text, text[-300:] + str([f.line() for f in found]))
        rc, text, bill_n, _s, _t = fresh(answer=lambda i, k, b, a: ("The period changed from 30 days to 14 days.", "SW", "SHENG", False) if i == "c2" else (f"EN {i}", f"SW {i}", f"SHENG {i}", False))
        check("a number in the English note that is in neither text is flagged for a human (CHECK line), not blocked",
              rc == 0 and "CHECK c2: the English note mentions 30" in text and "mentions 14" in text, text[-400:])

        # --- I. before/after are law: never linted -----------------------------
        law = load_bill(bill_file)
        law.changes[0].before = "A person who may vote and who you should ask is illegal and invalid."
        law.changes[0].after = "unconstitutional, kinyume cha katiba, you must make sure."
        check("before/after full of banned words: lint finds nothing (they are law, not notes)", lint_bill(law, phrases) == [], str(lint_bill(law, phrases)))
        law.changes[0].note.en = "This is illegal."
        law.changes[0].review = Review(status="draft")
        check("control: the same bill with a banned word in the NOTE is caught",
              [(f.item, f.lang) for f in lint_bill(law, phrases)] == [("changes[0].note", "en")], str(lint_bill(law, phrases)))

        # --- J. the OCR file is checked --------------------------------------
        stale_sha = "f" * 64
        _ocr_file(root, stale_sha, AFTER_LINES)
        client_s = _FakeClient(error=RuntimeError("must not be called"))
        sha_s = sha256_file(bill_file)
        rc, out = _quiet(run, root, TEST_ID, client=client_s)
        check("a stale OCR file (read from a different PDF) is refused: exit 1, no call, bill untouched, the fix named",
              rc == 1 and "STALE" in out and f"ocr {TEST_ID} --force" in out and not client_s.calls and sha256_file(bill_file) == sha_s, out[:300])
        (root / "sources" / TEST_ID / "approved.ocr.txt").unlink()
        rc, out = _quiet(run, root, TEST_ID, dry_run=True)
        check("no OCR text at all: exit 1 and it says to run ocr first", rc == 1 and "politricks ocr" in out, out[:200])
        _ocr_file(root, sha256_bytes(pdf_bytes), AFTER_LINES)
        (root / "sources" / TEST_ID / "approved.pdf").write_bytes(pdf_bytes + b"x")
        rc, out = _quiet(run, root, TEST_ID, dry_run=True)
        check("the PDF on disk is not the one the bill records: refused", rc == 1 and "is not the one the bill records" in out, out[:300])
        (root / "sources" / TEST_ID / "approved.pdf").write_bytes(pdf_bytes)

        # the lock
        client_l = _FakeClient()
        with bill_lock(bill_file):
            rc, out = _quiet(run, root, TEST_ID, client=client_l)
        check("bill held by another command: exit 1, nothing sent", rc == 1 and not client_l.calls and "another politricks command" in out, out[:250])

    # --- K. the prompt files ----------------------------------------------------
    own = (repo_root / "pipeline" / "prompts" / "comparer.md").read_text(encoding="utf-8")
    check("comparer.md has no em dash or en dash", "—" not in own and "–" not in own)
    check("translator.md still has every section comparer.md reuses", all(f"## {title}" in (repo_root / "pipeline" / "prompts" / "translator.md").read_text(encoding="utf-8") for title in REUSED_SECTIONS))
    with tempfile.TemporaryDirectory(prefix="politricks-compare-") as tmp4:
        broken = Path(tmp4)
        (broken / "pipeline" / "prompts").mkdir(parents=True)
        (broken / "pipeline" / "prompts" / "comparer.md").write_text(own, encoding="utf-8")
        (broken / "pipeline" / "prompts" / "translator.md").write_text("## Punctuation\nx\n", encoding="utf-8")
        try:
            system_prompt(broken)
            message = ""
        except CompareError as error:
            message = str(error)
        check("a renamed section in translator.md fails loudly instead of dropping a rule", "no longer has the section" in message and "What never to say" in message, message)

    # --- L. the real files were not touched -------------------------------------
    rc, out = _quiet(run, repo_root, "public-participation-bill-2025", dry_run=True)
    check("dry run on the real bill (no OCR text exists yet): exit 1, says to run ocr, writes nothing",
          rc == 1 and ("politricks ocr" in out), out[:300])
    check("the real bill files, sources.yaml and prompts are byte-identical (SHA-256)", {p: sha256_file(p) for p in watched} == before_real)
    check("the repo's usage logs are untouched", {log: (log.stat().st_size if log.exists() else None) for log in logs} == logs_before)
    return check.finish()


def _with_noise(chars: int, fn):
    """Run fn with the noise threshold set to `chars`, then put it back. A
    self-test control only: it proves the threshold is what separates the two
    verdicts."""
    global NOISE_CHARS
    saved = NOISE_CHARS
    NOISE_CHARS = chars
    try:
        return fn()
    finally:
        NOISE_CHARS = saved
