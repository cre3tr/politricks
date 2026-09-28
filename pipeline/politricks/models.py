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


class Clause(BaseModel):
    """One numbered clause of the bill, copied word for word from the PDF."""

    model_config = _STRICT
    id: str  # "c12", or "c12-1" when a long clause is split
    number: str  # the clause number as printed, e.g. "12"
    heading: str
    text: str  # normalised legal text (see hashing.normalise)
    text_sha256: str  # fingerprint of `text`
    pages: list[int]  # PDF page numbers the clause spans (for checking by eye)
    explanation: Optional[Tri] = None
    review: Review = Review(status="draft")
    neutral_check: Optional[NeutralCheck] = None
    groups: list[GroupId] = []
    suggested_articles: list[int] = []


class Summary(BaseModel):
    model_config = _STRICT
    text: Tri
    review: Review


class GroupEntry(BaseModel):
    model_config = _STRICT
    group: GroupId
    text: Tri
    clause_ids: list[str]
    review: Review


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
