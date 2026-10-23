"""The doctrine as a check that fails, not a hope (devpost/spec.md > Lint).

`politricks lint [bill-id]` reads every reader-facing generated text in a bill
and fails (exit 1) on either of two things:

1. A BANNED PHRASE from `pipeline/prompts/banned.txt` ("unconstitutional",
   "kinyume cha katiba", "you should", ...). This fails at ANY review status:
   even a human-reviewed text must not carry one. A phrase matches as a whole
   phrase; a trailing `*` in banned.txt lets its last word keep going, so
   `illegal*` also catches "illegally" while plain `vote` does not catch "votes".
2. A NEUTRAL FLAG: Claude marked its own clause explanation `neutral_check.ok
   == false`. This fails only while the clause is not "reviewed" — a human
   accepting the clause is exactly what clears the flag.

What is scanned: only text a reader sees in EN / SW / Sheng (a "Tri"): clause
explanations, the summary, group entries, and any Tri found inside the fields
later slices will fill (changes, stage_next, participation, challenge). Never
legal text, headings, URLs or channel values — a law is allowed to say "vote".
Hand-written samples ARE scanned, because they render on the page too.

`politricks lint --self-test` proves the checker itself works, offline, against
the shared test bill in `selftest.py`, and writes nothing.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from .hashing import normalise, sha256_file
from .models import Bill, Change, NeutralCheck, Review, ShengSwap, bill_path, changed_since_review, is_reviewed, load_bill
from .selftest import FIXTURE_BILL_ID, FIXTURE_DRAFT_IDS, Checker, fixture_bill, mark_reviewed

LANGS = ("en", "sw", "sheng")

# Item ids a finding can carry. `review.py` uses the same names, so a finding
# and the item a human is looking at can never disagree about what to call it.
SUMMARY_ID = "summary"

# The seven phrases devpost/spec.md > Lint names, plus the twelve Ian made
# permanent on 2026-10-19 (Kiswahili/Sheng twins and advice phrases).
# banned.txt may hold more, but never fewer: `lint --self-test` fails if one of
# these goes missing.
DOCTRINE_PHRASES = (
    "unconstitutional",
    "kinyume cha katiba",
    "haramu",
    "illegal",
    "invalid",
    "you should",
    "vote",
    "unlawful",
    "violates the constitution",
    "kinyume na katiba",
    "inakiuka katiba",
    "si halali",
    "kinyume cha sheria",
    "batili",
    "unapaswa",
    "mnapaswa",
    "piga kura",
    "you must",
    "make sure",
)

# Bill fields that have no typed model yet (later slices). A Tri hiding inside
# any of them is still reader-facing text, so we hunt for it by shape.
_LOOSE_FIELDS = ("changes", "stage_next", "participation", "challenge")


class LintError(Exception):
    """The lint could not run at all (no phrase list, nothing to scan)."""


def group_item_id(group: str) -> str:
    return f"group:{group}"


@dataclass(frozen=True)
class Finding:
    bill_id: str
    item: str  # "c12", "summary", "group:trader", "participation.requirements"
    lang: Optional[str]  # None for a neutral flag (it is about the clause, not a language)
    kind: str  # "banned", "neutral", "sheng" or "changed"
    detail: str  # the banned phrase (as written in banned.txt), the neutral note, or the Sheng swaps

    def describe(self) -> str:
        if self.kind == "banned":
            return f'banned phrase "{self.detail}"'
        if self.kind == "sheng":
            return f"Sheng kept in Kiswahili: {self.detail}"
        if self.kind == "changed":
            return f"changed since it was reviewed ({self.detail}): accept it again in `politricks review`"
        return f"neutral flag — {self.detail}"

    def line(self) -> str:
        where = f"{self.item} {self.lang}" if self.lang else self.item
        return f"FAIL {self.bill_id} {where}: {self.describe()}"


# --------------------------------------------------------------------------
# The phrase list
# --------------------------------------------------------------------------


def load_phrases(repo_root: Path) -> list[str]:
    """Read `pipeline/prompts/banned.txt`: one phrase per line, # comments.

    Phrases come back exactly as written, trailing `*` included, because a
    finding quotes the line a human would search for in banned.txt.
    """
    path = repo_root / "pipeline" / "prompts" / "banned.txt"
    if not path.exists():
        raise LintError(f"no banned-phrase list at {path.relative_to(repo_root).as_posix()}")
    phrases: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = normalise(raw)
        if not line or line.startswith("#"):
            continue
        # `*` means "last word may continue", so it only makes sense at the very
        # end, after at least one real character. Anywhere else it is a typo we
        # would otherwise match literally and never notice.
        stem = line[:-1].rstrip() if line.endswith("*") else line
        if not stem or "*" in stem:
            raise LintError(f'banned.txt: "{line}" — a * is only allowed as the last character, after a phrase')
        if line.casefold() not in seen:
            seen.add(line.casefold())
            phrases.append(line)
    if not phrases:
        # A lint with nothing to look for can never fail, which is not a pass.
        raise LintError("banned.txt holds no phrases, so lint could never fail")
    return phrases


def _pattern(phrase: str) -> re.Pattern:
    """The regex for one banned.txt line.

    Whole phrase on the left, always: "vote" must not fire inside "devote", and
    "piga kura" must not fire inside "kupiga kura". On the right, a plain
    phrase must also end at a word boundary ("vote" does not fire on "votes");
    a phrase ending in `*` lets its last word continue ("illegal*" fires on
    "illegally") and then ends at a word boundary.
    """
    starred = phrase.endswith("*")
    stem = phrase[:-1].rstrip() if starred else phrase
    return re.compile(
        r"(?<!\w)" + re.escape(normalise(stem)) + (r"\w*" if starred else "") + r"(?!\w)",
        re.IGNORECASE,
    )


# --------------------------------------------------------------------------
# Finding every reader-facing text in a bill
# --------------------------------------------------------------------------


def _is_tri(value: object) -> bool:
    return isinstance(value, dict) and set(value) == set(LANGS) and all(isinstance(v, str) for v in value.values())


def _find_tris(node: object, path: str) -> Iterator[tuple[str, dict]]:
    """Walk loose JSON, yielding (where, tri) for every dict shaped like a Tri."""
    if _is_tri(node):
        yield path, node  # type: ignore[misc]
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _find_tris(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _find_tris(value, f"{path}[{index}]")


def _plain(value: object) -> object:
    """Typed models (a Change) as plain dicts, so the shape walk sees one thing."""
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[union-attr]
    return value


def _tri_texts(item: str, tri: object) -> Iterator[tuple[str, str, str]]:
    values = tri if isinstance(tri, dict) else tri.model_dump()  # type: ignore[union-attr]
    for lang in LANGS:
        yield item, lang, values[lang]


def iter_texts(bill: Bill) -> Iterator[tuple[str, str, str]]:
    """Every (item id, language, text) lint looks at. Pure: no I/O."""
    if bill.summary is not None:
        yield from _tri_texts(SUMMARY_ID, bill.summary.text)
    for entry in bill.groups:
        yield from _tri_texts(group_item_id(entry.group), entry.text)
    for clause in bill.clauses:
        if clause.explanation is not None:
            yield from _tri_texts(clause.id, clause.explanation)
    for name in _LOOSE_FIELDS:
        # `changes` is typed since slice 8 (models.Change). Only its `note` is a
        # Tri, so `before` and `after` (legal text, which may say "vote") are
        # strings to this walk and are never scanned.
        for where, tri in _find_tris(_plain(getattr(bill, name)), name):
            yield from _tri_texts(where, tri)


def count_texts(bill: Bill) -> int:
    return sum(1 for _ in iter_texts(bill))


def lint_bill(bill: Bill, phrases: list[str]) -> list[Finding]:
    """All findings for one bill. Pure: no I/O, so the self-test can hammer it."""
    patterns = [(phrase, _pattern(phrase)) for phrase in phrases]
    findings: list[Finding] = []

    # (a) banned phrases — any review status. Text is normalised first so a
    # phrase that wrapped across a line or sits after a double space still hits.
    for item, lang, text in iter_texts(bill):
        clean = normalise(text)
        for phrase, pattern in patterns:
            if pattern.search(clean):
                findings.append(Finding(bill.id, item, lang, "banned", phrase))

    # (b) neutral flags — only until a human has reviewed the clause (and it
    # is still what they reviewed: see models.is_reviewed).
    for clause in bill.clauses:
        check = clause.neutral_check
        if check is not None and not check.ok and not is_reviewed(clause):
            findings.append(Finding(bill.id, clause.id, None, "neutral", check.note))

    # (c) Sheng words Claude kept in pure Kiswahili (Sheng here is English-led),
    # only until a human has reviewed the item. Accepting it in `review` is
    # the decision that each word stays; editing it is the swap.
    flagged_items = [(SUMMARY_ID, bill.summary)] if bill.summary is not None else []
    flagged_items += [(group_item_id(entry.group), entry) for entry in bill.groups]
    flagged_items += [(clause.id, clause) for clause in bill.clauses if clause.explanation is not None]
    # Change notes (slice 8): named by their place in changes[], like lint's
    # own text paths ("changes[0].note"). A loose dict (lint's own self-test
    # feeds some) has no review to check.
    flagged_items += [(f"changes[{i}]", change) for i, change in enumerate(bill.changes) if isinstance(change, Change)]
    for item, holder in flagged_items:
        if holder.sheng_swaps and not is_reviewed(holder):
            swaps = ", ".join(f'"{swap.word}" (English: "{swap.english}")' for swap in holder.sheng_swaps)
            findings.append(Finding(bill.id, item, "sheng", "sheng", swaps))

    # (d) reviewed, but the content is no longer what was accepted (a hand
    # edit, new tags, a refreshed section list). The site already shows
    # "Changed since review"; this brings it back to a human.
    for item, holder in flagged_items:
        if changed_since_review(holder):
            what = "no fingerprint" if holder.review.sha256 is None else f"reviewed {holder.review.at}"
            findings.append(Finding(bill.id, item, None, "changed", what))

    # (e) the same for the civic blocks (2026-10-23, once `review` could walk
    # them). Their fingerprint is civic.civic_sha256; imported here because
    # civic imports this module.
    from pydantic import ValidationError

    from . import civic

    for kind in civic.KINDS:
        raw = getattr(bill, kind)
        if raw is None:
            continue
        try:
            block = civic.BLOCK_MODELS[kind].model_validate(raw)
        except ValidationError:
            # Not a block civic.py wrote (lint's own self-test feeds loose
            # dicts to prove the banned scan reaches them): no review to check.
            continue
        if block.review.status == "reviewed" and block.review.sha256 != civic.civic_sha256(kind, block):
            findings.append(Finding(bill.id, kind, None, "changed", f"reviewed {block.review.at}"))

    return findings


# --------------------------------------------------------------------------
# `politricks lint`
# --------------------------------------------------------------------------


def run(repo_root: Path, bill_id: Optional[str] = None) -> int:
    """Lint one bill (or all of them). Returns the process exit code:
    0 clean, 1 findings (or a bill file that won't load), 2 nothing to scan."""
    try:
        phrases = load_phrases(repo_root)
    except LintError as error:
        print(f"FAILED: {error}")
        return 2

    bills_dir = repo_root / "src" / "data" / "bills"
    if bill_id:
        try:
            wanted = [bill_path(repo_root, bill_id)]
        except ValueError as error:
            print(f"FAILED: {error}")
            return 2
    else:
        wanted = sorted(bills_dir.glob("*.json"))
    paths = [path for path in wanted if path.exists()]
    if not paths:
        # An empty space is not a pass: say what was looked for and exit 2.
        print(f"no bills found (looked for {bill_id or '*'}.json in {bills_dir.relative_to(repo_root).as_posix()})")
        return 2

    bills_scanned = texts_scanned = finding_count = load_failures = 0
    for path in paths:
        try:
            bill = load_bill(path)
        except (ValueError, OSError) as error:
            # A pydantic ValidationError (including broken JSON) is a ValueError.
            print(f"FAIL {path.name}: cannot load as a bill")
            for line in str(error).splitlines()[:6]:
                print(f"     {line}")
            load_failures += 1
            continue
        findings = lint_bill(bill, phrases)
        for finding in findings:
            print(finding.line())
        n_texts = count_texts(bill)
        print(f"scanned {bill.id}: {n_texts} text(s)")
        bills_scanned += 1
        texts_scanned += n_texts
        finding_count += len(findings)

    footer = f"{bills_scanned} bill(s), {texts_scanned} text(s) scanned, {finding_count} finding(s)"
    if load_failures:
        footer += f", {load_failures} file(s) failed to load"
    print(footer)
    return 1 if (finding_count or load_failures) else 0


# --------------------------------------------------------------------------
# Self-test: proves the checker works, offline, writing nothing.
# --------------------------------------------------------------------------


def _clause(bill: Bill, clause_id: str):
    return next(clause for clause in bill.clauses if clause.id == clause_id)


def self_test(repo_root: Path) -> bool:
    check = Checker()

    real_path = bill_path(repo_root, FIXTURE_BILL_ID)
    sha_before = sha256_file(real_path)
    phrases = load_phrases(repo_root)

    # Every doctrine phrase, not just the three this test happens to plant:
    # deleting "haramu" from banned.txt must fail here, not pass silently.
    bare = {phrase[:-1].rstrip() if phrase.endswith("*") else phrase for phrase in phrases}
    missing = [phrase for phrase in DOCTRINE_PHRASES if phrase not in bare]
    check(f"banned.txt holds all {len(DOCTRINE_PHRASES)} doctrine phrases", not missing, f"missing {missing}")
    check(
        'banned.txt: "illegal*" is starred (inflections caught) and "vote" is not ("voting rights" stays allowed)',
        "illegal*" in phrases and "vote" in phrases,
        str(sorted(phrases)),
    )

    base = fixture_bill(repo_root)
    draft_ids = [c.id for c in base.clauses if c.explanation is not None and c.review.status == "draft"]
    check("fixture applied: exactly c3 and c4 are drafts", draft_ids == list(FIXTURE_DRAFT_IDS), str(draft_ids))
    check(
        "fixture: the summary is a sample and there is exactly one draft group entry (trader)",
        base.summary is not None
        and base.summary.review.status == "sample"
        and [(g.group, g.review.status) for g in base.groups] == [("trader", "draft")],
        str(base.groups),
    )

    # --- 1. a clean, drafted bill passes -------------------------------------
    findings = lint_bill(base, phrases)
    check("1. clean bill with the fixture drafts: 0 findings", findings == [], str(findings))
    # Control: the scan really reached every explained item, drafts, summary and
    # group entries included, so "0 findings" cannot secretly mean "0 texts".
    explained = sum(1 for c in base.clauses if c.explanation is not None)
    expected = 3 * (explained + (1 if base.summary else 0) + len(base.groups))
    check(f"1. control: scan covers all {expected} texts", count_texts(base) == expected, str(count_texts(base)))

    # --- 2. a planted banned phrase is found, and named ------------------------
    bill = base.model_copy(deep=True)
    _clause(bill, "c4").explanation.sheng += " Hii ni kinyume cha katiba."
    findings = lint_bill(bill, phrases)
    check(
        '2. planted "kinyume cha katiba" in c4 sheng: exactly one finding naming c4 and sheng',
        len(findings) == 1
        and (findings[0].item, findings[0].lang, findings[0].kind, findings[0].detail)
        == ("c4", "sheng", "banned", "kinyume cha katiba"),
        str(findings),
    )
    # The same phrase in the two other places a reader sees text. Without these,
    # deleting the summary or groups loop from iter_texts would pass every test.
    bill = base.model_copy(deep=True)
    bill.groups[0].text.sw += " Hii ni kinyume cha katiba."
    findings = lint_bill(bill, phrases)
    check(
        "2. planted phrase in the draft GROUP entry: exactly one finding naming group:trader and sw",
        len(findings) == 1 and (findings[0].item, findings[0].lang, findings[0].kind) == ("group:trader", "sw", "banned"),
        str(findings),
    )
    bill = base.model_copy(deep=True)
    bill.summary.text.en += " This is illegal."
    findings = lint_bill(bill, phrases)
    check(
        "2. planted phrase in the SUMMARY: exactly one finding naming summary and en",
        len(findings) == 1 and (findings[0].item, findings[0].lang, findings[0].kind) == ("summary", "en", "banned"),
        str(findings),
    )

    # --- 3. matching: whole phrases, inflections, normalised whitespace --------
    def findings_for(text: str, lang: str = "en") -> list[Finding]:
        probe = base.model_copy(deep=True)
        setattr(_clause(probe, "c3").explanation, lang, text)
        return lint_bill(probe, phrases)

    def one_hit(text: str, phrase: str, lang: str = "en") -> bool:
        found = findings_for(text, lang)
        return len(found) == 1 and (found[0].item, found[0].lang, found[0].detail) == ("c3", lang, phrase)

    # Starred phrases catch their inflections...
    check('3. "unconstitutionally" is found (unconstitutional*)', one_hit("The rule is unconstitutionally broad.", "unconstitutional*"))
    check('3. "illegally" is found (illegal*)', one_hit("The goods were illegally imported.", "illegal*"))
    check('3. "illegality" is found (illegal*)', one_hit("Illegality is for a court to decide.", "illegal*"))
    check('3. "invalidated" is found (invalid*)', one_hit("The licence was invalidated.", "invalid*"))
    check("3. \"You shouldn't\" is found (you should*)", one_hit("You shouldn't wait.", "you should*"))
    check('3. "unlawfully" is found (unlawful*)', one_hit("The land was held unlawfully.", "unlawful*"))
    check(
        '3. "violates the constitution" is found',
        one_hit("This clause violates the constitution.", "violates the constitution"),
    )
    # ...and unstarred ones do not. Each of these would fire if the right-hand
    # word boundary were dropped (the first), or the left one (the second).
    check('3. "He votes in Nairobi." is NOT found (right boundary: "vote" is unstarred)', findings_for("He votes in Nairobi.") == [])
    check('3. "voting rights" is not a "vote" match', findings_for("The Chief Executive Officer has no voting rights.") == [])
    check('3. "devote" is not a "vote" match (left boundary)', findings_for("They devote time to the work.") == [])
    check(
        '3. "kupiga kura" is not a "piga kura" match',
        findings_for("Wananchi wengi wanapenda kupiga kura.", "sw") == [],
    )
    found = findings_for("The Chief Executive Officer cannot vote.")
    check(
        '3. "cannot vote" is one finding for "vote" in c3',
        len(found) == 1 and (found[0].item, found[0].detail) == ("c3", "vote"),
        str(found),
    )
    check('3. matching ignores case ("CANNOT VOTE")', len(findings_for("THE OFFICER CANNOT VOTE.")) == 1)
    # Text is normalised before matching: the PDF-style line break and the
    # double space below only match because lint collapses whitespace first.
    check('3. "you  should" (double space) is found', one_hit("Please note you  should apply early.", "you should*"))
    check(
        '3. "kinyume cha<newline>katiba" is found',
        one_hit("Hii ni kinyume cha\nkatiba ya nchi.", "kinyume cha katiba", "sw"),
    )

    # --- 3b. a malformed banned.txt is refused, not matched literally ---------
    with tempfile.TemporaryDirectory() as tmp:
        prompts = Path(tmp) / "pipeline" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "banned.txt").write_text("ill*egal\n", encoding="utf-8")
        try:
            load_phrases(Path(tmp))
            refused = False
        except LintError:
            refused = True
    check('3. a "*" in the middle of a banned.txt line is refused', refused)

    # --- 4. neutral flag: fails until a human reviews the clause --------------
    bill = base.model_copy(deep=True)
    clause = _clause(bill, "c3")
    clause.neutral_check = NeutralCheck(ok=False, note="Described a value judgement")
    findings = lint_bill(bill, phrases)
    check(
        "4. neutral_check.ok=false on draft c3: one neutral finding",
        len(findings) == 1 and (findings[0].item, findings[0].kind, findings[0].lang) == ("c3", "neutral", None),
        str(findings),
    )
    mark_reviewed(clause)
    check("4. once c3 is reviewed the neutral flag is gone", lint_bill(bill, phrases) == [])
    clause.explanation.en = "The officer cannot vote."
    mark_reviewed(clause)  # reviewed WITH the phrase (in memory only)
    findings = lint_bill(bill, phrases)
    check('4. but a banned phrase still fails on a "reviewed" clause',
          [finding.kind for finding in findings] == ["banned"], str(findings))

    # --- 4c. reviewed, then changed: flagged until accepted again --------------
    bill = base.model_copy(deep=True)
    clause = _clause(bill, "c3")
    mark_reviewed(clause)
    check("4c. a reviewed clause still as accepted: no finding", lint_bill(bill, phrases) == [])
    clause.groups = [*clause.groups, "student"]
    findings = lint_bill(bill, phrases)
    check(
        "4c. its tags change after review: one 'changed' finding, telling you to accept it again",
        [(finding.item, finding.kind) for finding in findings] == [("c3", "changed")]
        and "accept it again" in findings[0].describe(),
        str(findings),
    )

    # --- 4b. Sheng swaps: fail until a human reviews the item -----------------
    bill = base.model_copy(deep=True)
    _clause(bill, "c3").sheng_swaps = [ShengSwap(word="wasio wakazi", english="non-residents")]
    bill.summary.sheng_swaps = [ShengSwap(word="mapato", english="income")]
    findings = lint_bill(bill, phrases)
    got = sorted((finding.item, finding.kind, finding.lang) for finding in findings)
    check(
        "4b. Sheng swaps on draft c3 and on the summary: one sheng finding each",
        got == [("c3", "sheng", "sheng"), ("summary", "sheng", "sheng")],
        str(findings),
    )
    check(
        "4b. the finding names the word and its English",
        any('"wasio wakazi" (English: "non-residents")' in finding.describe() for finding in findings),
        str([finding.describe() for finding in findings]),
    )
    mark_reviewed(_clause(bill, "c3"))
    mark_reviewed(bill.summary)
    check("4b. once reviewed, the Sheng flags are gone", lint_bill(bill, phrases) == [], str(lint_bill(bill, phrases)))

    # --- 5. Tris hiding in loose fields are scanned; URLs and legal text are not
    bill = base.model_copy(deep=True)
    bill.participation = {
        "open": True,
        "source_url": "https://example.org/vote",
        "channels": [{"type": "email", "value": "vote@example.org"}],
        "requirements": {"en": "You should bring your ID.", "sw": "Leta kitambulisho.", "sheng": "Leta ID yako."},
    }
    findings = lint_bill(bill, phrases)
    check(
        '5. "you should" inside a nested participation Tri is found (the URL and channel "vote" are not)',
        len(findings) == 1
        and (findings[0].item, findings[0].lang, findings[0].detail) == ("participation.requirements", "en", "you should*"),
        str(findings),
    )
    bill = base.model_copy(deep=True)
    bill.changes = [
        {
            "kind": "draft",
            "clause_id": "c3",
            "before": "A person who may vote and who you should ask is illegal.",
            "after": "invalid",
            "note": {"en": "One word changed.", "sw": "Neno moja limebadilishwa.", "sheng": "Neno moja imebadilika."},
        }
    ]
    check("5. changes[].before/after (legal text) are never scanned", lint_bill(bill, phrases) == [])

    # --- 5a. the typed Change (slice 8): notes are linted, before/after never
    from .models import Tri, current_sha256

    typed = Change(
        kind="draft", clause_id="c3", heading="Rules.", before_number="3", after_number="3",
        before="3. A person who may vote and who you should ask is illegal.",
        after="3. unconstitutional, kinyume cha katiba, make sure you must.",
        note=Tri(en="One word changed.", sw="Neno moja limebadilishwa.", sheng="Neno moja imebadilika."),
        source_label="a compared with b", ocr=True, after_pages=[2],
    )
    typed.review = Review(status="draft", sha256=current_sha256(typed))
    bill = base.model_copy(deep=True)
    bill.changes = [typed]
    texts = [(item, lang) for item, lang, _text in iter_texts(bill) if item.startswith("changes[")]
    check("5a. positive control: lint walks the note of a typed Change (3 languages) and nothing else",
          texts == [("changes[0].note", "en"), ("changes[0].note", "sw"), ("changes[0].note", "sheng")], str(texts))
    check("5a. a typed Change whose before/after are full of banned words and whose note is clean: no findings",
          lint_bill(bill, phrases) == [], str(lint_bill(bill, phrases)))
    bill.changes = [typed.model_copy(update={"note": Tri(en="You should read it.", sw="x", sheng="y")})]
    findings = lint_bill(bill, phrases)
    check("5a. the same Change with a banned phrase in its NOTE is caught and named changes[0].note",
          [(f.item, f.lang, f.detail) for f in findings] == [("changes[0].note", "en", "you should*")], str(findings))
    reviewed = typed.model_copy(deep=True)
    reviewed.review = Review(status="reviewed", at="2026-10-23", sha256=current_sha256(reviewed))
    bill.changes = [reviewed]
    check("5a. a reviewed Change that still matches its fingerprint is clean", lint_bill(bill, phrases) == [])
    reviewed.after = "3. Quite different words."
    findings = lint_bill(bill, phrases)
    check("5a. a reviewed Change whose texts moved since is flagged 'changed since review'",
          [(f.item, f.kind) for f in findings] == [("changes[0]", "changed")], str(findings))
    swapped = typed.model_copy(update={"sheng_swaps": [ShengSwap(word="mahakama", english="court")]})
    bill.changes = [swapped]
    check("5a. a Change note with a Sheng swap is flagged until reviewed",
          [(f.item, f.kind) for f in lint_bill(bill, phrases)] == [("changes[0]", "sheng")], str(lint_bill(bill, phrases)))

    # --- 5b. the shared vectors: the live Explain API (src/lib/banned.ts) is
    # checked against the same file by scripts/check-api.mjs, so the two
    # matchers can't drift apart while each passes its own tests.
    import json

    vectors = json.loads(
        (repo_root / "pipeline" / "tests" / "fixtures" / "banned_vectors.json").read_text(encoding="utf-8")
    )["vectors"]
    wrong = []
    for vector in vectors:
        clean = normalise(vector["text"])
        hits = [phrase for phrase in phrases if _pattern(phrase).search(clean)]
        if hits != vector["hits"]:
            wrong.append(f"{vector['text']!r}: got {hits}, want {vector['hits']}")
    check(
        f"5b. all {len(vectors)} shared banned-phrase vectors match (banned_vectors.json)",
        len(vectors) > 0 and not wrong,
        "; ".join(wrong),
    )

    # --- 6. nothing written ----------------------------------------------------
    check("6. the real bill file's SHA-256 is unchanged", sha256_file(real_path) == sha_before)

    return check.finish()
