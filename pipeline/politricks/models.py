"""The shape of a bill file (`src/data/bills/<bill-id>.json`).

These Pydantic models are the pipeline's side of the data contract. The website
has a twin in `src/lib/schema.ts` (Zod). If you change a field here, change it
there too — the site build fails loudly on any mismatch, which is the point.

See `devpost/spec.md > Data Model` for the full description.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .hashing import CLAUSE_HASH_VERSION, REVIEW_HASH_VERSION, review_sha256

# "forbid" means an unexpected field is an error, not silently ignored.
_STRICT = ConfigDict(extra="forbid")

GroupId = Literal["employee", "trader", "transport", "student"]


class Tri(BaseModel):
    """The same text in English, Kiswahili and Sheng."""

    model_config = _STRICT
    en: str
    sw: str
    sheng: str


class Review(BaseModel):
    """Who has checked this text.

    - "draft":    written by Claude, not yet reviewed by a human.
    - "reviewed": a human (Ian) has read and accepted it.
    - "sample":   a hand-written placeholder used before the API key existed.
                  It must never be shown as "AI draft" or "Reviewed".
    """

    model_config = _STRICT
    status: Literal["draft", "reviewed", "sample"]
    at: Optional[str] = None  # ISO date when it was reviewed
    # The fingerprint (`current_sha256`) of the content when this status was
    # set: by `generate` for a draft, by `politricks review` on accept. None
    # for samples. "Reviewed" counts only while it still matches, so content
    # changed after an accept can never keep the green badge.
    sha256: Optional[str] = None


class NeutralCheck(BaseModel):
    """Claude's own flag for whether its explanation stayed neutral."""

    model_config = _STRICT
    ok: bool
    note: str


class Source(BaseModel):
    """One fingerprinted official document behind this bill."""

    model_config = _STRICT
    label: str
    url: str
    official: bool
    fetched_at: str  # UTC ISO timestamp of the download
    sha256: str  # fingerprint of the raw file bytes
    file: str  # repo-relative path, e.g. "sources/finance-act-2026/act.pdf"
    # True when the official file's text layer came from scanning paper (so it
    # can contain small typing glitches). Set per source in sources.yaml; the
    # site shows a note only for these.
    scanned: bool = False


class ShengSwap(BaseModel):
    """A word Claude's Sheng kept in pure Kiswahili, with the everyday English
    a Nairobi speaker would more likely use. Sheng here is English-led, so
    these are flagged for the human Sheng review: Claude flags, Ian decides.
    Lint fails on a non-empty list until the item is reviewed."""

    model_config = _STRICT
    word: str
    english: str


class Clause(BaseModel):
    """One numbered clause of the bill, copied word for word from the PDF."""

    model_config = _STRICT
    id: str  # "c12", or "c12-1" when a long clause is split
    number: str  # the clause number as printed, e.g. "12"
    heading: str
    # Where the heading came from: "margin" = read from the PDF's marginal note;
    # "transcribed" = typed by hand from the printed page (used when a scan's
    # margin text is garbled). Headings are signposts, never legal text.
    heading_source: Literal["margin", "transcribed"] = "margin"
    text: str  # normalised legal text (see hashing.normalise)
    text_sha256: str  # fingerprint of `text`
    pages: list[int]  # PDF page numbers the clause spans (for checking by eye)
    explanation: Optional[Tri] = None
    review: Review = Review(status="draft")
    neutral_check: Optional[NeutralCheck] = None
    groups: list[GroupId] = []
    suggested_articles: list[int] = []
    sheng_swaps: list[ShengSwap] = []


class Summary(BaseModel):
    model_config = _STRICT
    text: Tri
    review: Review
    sheng_swaps: list[ShengSwap] = []


class GroupEntry(BaseModel):
    model_config = _STRICT
    group: GroupId
    text: Tri
    clause_ids: list[str]
    review: Review
    sheng_swaps: list[ShengSwap] = []


class Bill(BaseModel):
    """Everything the website needs to show one bill."""

    model_config = _STRICT
    id: str
    title: str
    chamber: Literal["national", "senate", "county"]
    kind: Literal["bill", "motion"]
    status: Literal["active", "passed"]
    stage: str
    stage_checked_at: str
    source_changed: bool = False
    sources: list[Source] = []
    summary: Optional[Summary] = None
    groups: list[GroupEntry] = []
    clauses: list[Clause] = []
    # The fields below are filled by later slices (compare, civic data).
    stage_next: Optional[dict] = None
    changes: list[dict] = []
    participation: Optional[dict] = None
    challenge: Optional[dict] = None


# --------------------------------------------------------------------------
# Review fingerprints: what "Reviewed" is tied to
# --------------------------------------------------------------------------

def _clause_parts(clause: Clause, text: Tri) -> list[str]:
    """A clause's fingerprint parts up to and including its sorted group tags
    (the whole of the v1 layout, after the version)."""
    return ["clause", clause.id, clause.text, text.en, text.sw, text.sheng, ",".join(sorted(clause.groups))]


def current_sha256(owner: Clause | Summary | GroupEntry) -> Optional[str]:
    """The fingerprint of what a reviewer accepts for this item, as it is now.

    It covers the three texts a reader sees, plus what decides WHO sees the
    item: a clause's group tags (sorted, so order alone changes nothing) and a
    group entry's section list. A clause also includes its legal text itself,
    so a changed law un-reviews its explanation, and (since slice 10, clause
    fingerprint v2) its `suggested_articles`, sorted numerically, because the
    chips are shown to readers beside the explanation. Left out: review aids
    (`sheng_swaps`, `neutral_check`) and the heading (a signpost, not legal
    text). None when there is no text. Twin: `src/lib/reviewHash.ts`.
    """
    if isinstance(owner, Clause):
        if owner.explanation is None:
            return None
        articles = ",".join(str(number) for number in sorted(owner.suggested_articles))
        return review_sha256([CLAUSE_HASH_VERSION, *_clause_parts(owner, owner.explanation), articles])
    elif isinstance(owner, Summary):
        text = owner.text
        parts = ["summary", text.en, text.sw, text.sheng]
    else:
        text = owner.text
        parts = ["group", owner.group, text.en, text.sw, text.sheng, ",".join(owner.clause_ids)]
    return review_sha256([REVIEW_HASH_VERSION, *parts])


def legacy_clause_sha256_v1(clause: Clause) -> Optional[str]:
    """A clause's fingerprint under the RETIRED v1 rules (no `suggested_articles`).

    Exists only for `politricks restamp`, the one-time migration: a draft whose
    stored fingerprint equals this was written by `generate` under v1 and never
    touched since, so it may be re-stamped under v2. Nothing else may call it.
    """
    if clause.explanation is None:
        return None
    return review_sha256([REVIEW_HASH_VERSION, *_clause_parts(clause, clause.explanation)])


def is_v1_stamped(clause: Clause) -> bool:
    """A clause whose stored fingerprint is exactly its v1 fingerprint (and not
    its current one): written under the retired rules and not touched since.
    Whatever its status, it needs `politricks restamp` (a draft) or a human
    re-accept (a reviewed one) before it can read as anything but changed."""
    stored = clause.review.sha256
    return stored is not None and stored == legacy_clause_sha256_v1(clause) and stored != current_sha256(clause)


def is_reviewed(owner: Clause | Summary | GroupEntry) -> bool:
    """Reviewed AND unchanged since. The only state that earns "Reviewed"."""
    review = owner.review
    return review.status == "reviewed" and review.sha256 is not None and review.sha256 == current_sha256(owner)


def changed_since_review(owner: Clause | Summary | GroupEntry) -> bool:
    return owner.review.status == "reviewed" and not is_reviewed(owner)


# What `politricks review` stores as a draft's fingerprint after a human edits
# it. It never matches a real fingerprint, so the draft stays protected even if
# the edit is later typed back to the old words.
HAND_EDITED = "hand-edited"


def is_hand_edited(owner: Clause | Summary | GroupEntry) -> bool:
    """A draft that is no longer exactly what `generate` wrote: edited in
    `politricks review` (fingerprint HAND_EDITED) or in the JSON (fingerprint
    no longer matches). A draft with no fingerprint is treated as untouched:
    that is how to hand one back to `generate`, by setting its
    `review.sha256` to null."""
    review = owner.review
    return review.status == "draft" and review.sha256 is not None and review.sha256 != current_sha256(owner)


def is_protected(owner: Clause | Summary | GroupEntry) -> bool:
    """Human work `generate` must never overwrite: anything reviewed (changed
    since or not) and any hand-edited draft."""
    return owner.review.status == "reviewed" or is_hand_edited(owner)


def draft_review(owner: Clause | Summary | GroupEntry) -> Review:
    """The review record `generate` gives an item it has just written."""
    return Review(status="draft", sha256=current_sha256(owner))


# Bill ids become folder and file names (sources/<id>/, bills/<id>.json), so
# they're restricted to lowercase letters, digits and hyphens. That rules out
# "../" tricks before any id ever arrives from a web request (slice 4's APIs).
_BILL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_bill_id(bill_id: str) -> str:
    if not _BILL_ID.fullmatch(bill_id):
        raise ValueError(f"invalid bill id {bill_id!r}: use lowercase letters, digits and hyphens")
    return bill_id


def bill_path(repo_root: Path, bill_id: str) -> Path:
    return repo_root / "src" / "data" / "bills" / f"{validate_bill_id(bill_id)}.json"


def load_bill(path: Path) -> Bill:
    return Bill.model_validate_json(path.read_text(encoding="utf-8"))


def save_bill(bill: Bill, path: Path) -> None:
    """Write the bill JSON deterministically.

    Same input -> byte-identical file, so re-running the pipeline produces no
    git diff unless something actually changed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bill.model_dump(mode="json")
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
