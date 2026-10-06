"""The doctrine as a check that fails, not a hope (devpost/spec.md > Lint).

`politricks lint [bill-id]` reads every reader-facing generated text in a bill
and fails (exit 1) on either of two things:

1. A BANNED PHRASE from `pipeline/prompts/banned.txt` ("unconstitutional",
   "kinyume cha katiba", "you should", ...). This fails at ANY review status:
   even a human-reviewed text must not carry one.
2. A NEUTRAL FLAG: Claude marked its own clause explanation `neutral_check.ok
   == false`. This fails only while the clause is not "reviewed" — a human
   accepting the clause is exactly what clears the flag.

What is scanned: only text a reader sees in EN / SW / Sheng (a "Tri"): clause
explanations, the summary, group entries, and any Tri found inside the fields
later slices will fill (changes, stage_next, participation, challenge). Never
legal text, headings, URLs or channel values — a law is allowed to say "vote".
Hand-written samples ARE scanned, because they render on the page too.

`politricks lint --self-test` proves the checker itself works, offline, against
the recorded batch fixture, and writes nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from .hashing import normalise, sha256_file
from .models import Bill, NeutralCheck, Review, bill_path, load_bill

LANGS = ("en", "sw", "sheng")

# Item ids a finding can carry. `review.py` uses the same names, so a finding
# and the item a human is looking at can never disagree about what to call it.
SUMMARY_ID = "summary"

# The two clauses the recorded fixture (pipeline/tests/fixtures/batch_results
# .jsonl) turns into drafts. Both self-tests lean on them.
FIXTURE_DRAFT_IDS = ("c3", "c4")

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
    kind: str  # "banned" or "neutral"
    detail: str  # the banned phrase, or the neutral note

    def describe(self) -> str:
        if self.kind == "banned":
            return f'banned phrase "{self.detail}"'
        return f"neutral flag — {self.detail}"

    def line(self) -> str:
        where = f"{self.item} {self.lang}" if self.lang else self.item
        return f"FAIL {self.bill_id} {where}: {self.describe()}"


# --------------------------------------------------------------------------
# The phrase list
# --------------------------------------------------------------------------


def load_phrases(repo_root: Path) -> list[str]:
    """Read `pipeline/prompts/banned.txt`: one phrase per line, # comments."""
    path = repo_root / "pipeline" / "prompts" / "banned.txt"
    if not path.exists():
        raise LintError(f"no banned-phrase list at {path.relative_to(repo_root).as_posix()}")
    phrases: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = normalise(raw)
        if not line or line.startswith("#"):
            continue
        if line.casefold() not in seen:
            seen.add(line.casefold())
            phrases.append(line)
    if not phrases:
        # A lint with nothing to look for can never fail, which is not a pass.
        raise LintError("banned.txt holds no phrases, so lint could never fail")
    return phrases


def _pattern(phrase: str) -> re.Pattern:
    # Whole phrase, either side: "vote" must not fire inside "voting"/"devote",
    # and "piga kura" must not fire inside "kupiga kura".
    return re.compile(r"(?<!\w)" + re.escape(normalise(phrase)) + r"(?!\w)", re.IGNORECASE)


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
        for where, tri in _find_tris(getattr(bill, name), name):
            yield from _tri_texts(where, tri)


def count_texts(bill: Bill) -> int:
    return sum(1 for _ in iter_texts(bill))


def lint_bill(bill: Bill, phrases: list[str]) -> list[Finding]:
    """All findings for one bill. Pure: no I/O, so the self-test can hammer it."""
    patterns = [(phrase, _pattern(phrase)) for phrase in phrases]
    findings: list[Finding] = []

    # (a) banned phrases — any review status.
    for item, lang, text in iter_texts(bill):
        clean = normalise(text)
        for phrase, pattern in patterns:
            if pattern.search(clean):
                findings.append(Finding(bill.id, item, lang, "banned", phrase))

    # (b) neutral flags — only until a human has reviewed the clause.
    for clause in bill.clauses:
        check = clause.neutral_check
        if check is not None and not check.ok and clause.review.status != "reviewed":
            findings.append(Finding(bill.id, clause.id, None, "neutral", check.note))

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


def fixture_bill(repo_root: Path) -> Bill:
    """finance-act-2026 in memory, with the recorded fixture's two drafts applied.

    c3 and c4 are first reset to "no explanation yet", so the copy is the same
    today and after slice 2b has explained (or a human has reviewed) the real
    clauses. Nothing here touches the file on disk.
    """
    from . import generate

    bill = load_bill(bill_path(repo_root, "finance-act-2026")).model_copy(deep=True)
    for clause in bill.clauses:
        if clause.id in FIXTURE_DRAFT_IDS:
            clause.explanation = None
            clause.review = Review(status="draft")
            clause.neutral_check = None
    fixture = repo_root / "pipeline" / "tests" / "fixtures" / "batch_results.jsonl"
    lines = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
    generate.apply(bill, [generate.parse_batch_line(line) for line in lines])
    return bill


def _clause(bill: Bill, clause_id: str):
    return next(clause for clause in bill.clauses if clause.id == clause_id)


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
    check(
        "banned.txt loads and holds the phrases this test plants",
        {"kinyume cha katiba", "vote", "you should"} <= set(phrases),
    )

    base = fixture_bill(repo_root)
    draft_ids = [c.id for c in base.clauses if c.explanation is not None and c.review.status == "draft"]
    check("fixture applied: exactly c3 and c4 are drafts", draft_ids == list(FIXTURE_DRAFT_IDS), str(draft_ids))

    # --- 1. a clean, drafted bill passes -------------------------------------
    findings = lint_bill(base, phrases)
    check("1. clean bill with the fixture drafts: 0 findings", findings == [], str(findings))
    # Control: the scan really reached every explained item, drafts included,
    # so "0 findings" cannot secretly mean "0 texts looked at".
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

    # --- 3. word boundaries, both directions ----------------------------------
    def one_finding_for(text: str, lang: str = "en") -> list[Finding]:
        probe = base.model_copy(deep=True)
        setattr(_clause(probe, "c3").explanation, lang, text)
        return lint_bill(probe, phrases)

    check('3. "voting rights" is not a "vote" match', one_finding_for("The Chief Executive Officer has no voting rights.") == [])
    check('3. "devote" is not a "vote" match', one_finding_for("They devote time to the work.") == [])
    check(
        '3. "kupiga kura" is not a "piga kura" match',
        one_finding_for("Wananchi wengi wanapenda kupiga kura.", "sw") == [],
    )
    found = one_finding_for("The Chief Executive Officer cannot vote.")
    check(
        '3. "cannot vote" is one finding for "vote" in c3',
        len(found) == 1 and (found[0].item, found[0].detail) == ("c3", "vote"),
        str(found),
    )
    check('3. matching ignores case ("CANNOT VOTE")', len(one_finding_for("THE OFFICER CANNOT VOTE.")) == 1)

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
    clause.review = Review(status="reviewed", at="2026-01-01")
    check("4. once c3 is reviewed the neutral flag is gone", lint_bill(bill, phrases) == [])
    clause.explanation.en = "The officer cannot vote."
    check('4. but a banned phrase still fails on a "reviewed" clause', len(lint_bill(bill, phrases)) == 1)

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
        and (findings[0].item, findings[0].lang, findings[0].detail) == ("participation.requirements", "en", "you should"),
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

    # --- 6. nothing written ----------------------------------------------------
    check("6. the real bill file's SHA-256 is unchanged", sha256_file(real_path) == sha_before)

    return ok
