"""Pull numbered sections (clauses) out of an official PDF, word for word.

How it works, in plain terms:

1. BODY STREAM. For each page we separate the main text from the marginal
   notes. A character counts as marginal only if it is BOTH small (under 90%
   of the page's body font size) AND sits to the right of the body column
   (x >= margin_x). Marginal notes are kept aside and used only as headings.

2. HEADERS/FOOTERS. Running headers ("No.19 Finance 2026") and page numbers
   are removed ONLY by explicit patterns, ONLY on the first/last 3 lines of a
   page, and every removal is logged to <file>.stripped.txt. If any page
   loses more than 4 lines, extraction fails — a pattern eating real text
   shows up immediately.

3. SECTIONS. The legal body starts after "ENACTED by the Parliament of
   Kenya". A section starts on a line like "12. Section 5 of ..." and must be
   numbered exactly one more than the previous section, which filters out
   numbered lists inside quoted amendments.

4. THREE CHECKS, all against text re-derived from the fingerprinted PDF:
   a. verbatim — every section's text appears in the PDF's body stream;
   b. fingerprint — each section's text_sha256 matches its text;
   c. coverage — the segments rebuild the whole legal body, nothing lost.

Overrides (pipeline/overrides/<bill-id>.yaml) can only move boundaries or add
header patterns. They can never supply or edit legal text.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import yaml

from .hashing import normalise, sha256_text
from .models import Bill, Clause, bill_path, load_bill, save_bill

# Header/footer patterns used unless an override replaces them.
DEFAULT_STRIP_PATTERNS = [
    r"^\d{1,4}$",  # a bare page number
]
MAX_STRIPPED_PER_PAGE = 4
EDGE_LINES = 3  # patterns only apply to the first/last 3 lines of a page

# A section starts with its number and a dot. In this scan, two-digit numbers
# often have no space after the dot ("10.Section 15 ..."), so the space is optional.
SECTION_START = re.compile(r"^(\d{1,3})\.\s*(?=[A-Z(\"“'])")
PART_HEADING = re.compile(r"^PART\s+[IVXL]+\b")
SCHEDULE_HEADING = re.compile(r"^(FIRST |SECOND |THIRD |FOURTH )?SCHEDULE\b")


class ExtractError(Exception):
    """Raised when a check fails. The message says exactly what and where."""


@dataclass
class Line:
    text: str
    page: int
    top: float


@dataclass
class Segment:
    kind: str  # "section", "part", "schedule", "other"
    lines: list[Line] = field(default_factory=list)
    number: int | None = None

    @property
    def text(self) -> str:
        return normalise("\n".join(line.text for line in self.lines))


def load_overrides(repo_root: Path, bill_id: str) -> dict:
    path = repo_root / "pipeline" / "overrides" / f"{bill_id}.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _page_lines(page, margin_x: float) -> tuple[list[dict], list[dict]]:
    """Split one page into (body lines, marginal-note lines), each with `top`."""
    sizes = Counter(round(c["size"], 1) for c in page.chars)
    body_size = sizes.most_common(1)[0][0]

    def is_margin(obj) -> bool:
        return (
            obj.get("object_type") == "char"
            and obj["size"] < body_size * 0.9
            and obj["x0"] >= margin_x
        )

    body = page.filter(lambda obj: not is_margin(obj))
    margin = page.filter(is_margin)
    return body.extract_text_lines(strip=True), margin.extract_text_lines(strip=True)


def read_pdf(pdf_path: Path, overrides: dict) -> tuple[list[Line], dict[int, list[Line]], list[str]]:
    """Return (body lines, marginal lines per page, stripped-line log).

    This is the single source of truth every check compares against.
    """
    margin_x = float(overrides.get("margin_x", 395))
    patterns = [re.compile(p) for p in overrides.get("strip_patterns", DEFAULT_STRIP_PATTERNS)]
    skip_pages = set(overrides.get("skip_pages", []))

    body_lines: list[Line] = []
    margin_lines: dict[int, list[Line]] = {}
    stripped_log: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            if number in skip_pages or not page.chars:
                continue
            body, margin = _page_lines(page, margin_x)
            kept: list[Line] = []
            removed_here = 0
            for index, raw in enumerate(body):
                text = raw["text"]
                at_edge = index < EDGE_LINES or index >= len(body) - EDGE_LINES
                if at_edge and any(p.search(text) for p in patterns):
                    stripped_log.append(f"p{number}: {text}")
                    removed_here += 1
                    continue
                kept.append(Line(text=text, page=number, top=raw["top"]))
            if removed_here > MAX_STRIPPED_PER_PAGE:
                raise ExtractError(
                    f"page {number}: {removed_here} lines stripped (max {MAX_STRIPPED_PER_PAGE}) — "
                    "a header pattern is probably matching real text"
                )
            body_lines.extend(kept)
            margin_lines[number] = [Line(m["text"], number, m["top"]) for m in margin]
    return body_lines, margin_lines, stripped_log


def _region(lines: list[Line], overrides: dict) -> list[Line]:
    """Cut the list of lines down to the legal body."""
    start_pattern = re.compile(overrides.get("start_after", r"ENACTED by the Parliament of Kenya"))
    starts = [i for i, line in enumerate(lines) if start_pattern.search(line.text)]
    if not starts:
        raise ExtractError(f"start marker not found: {start_pattern.pattern!r}")
    region = lines[starts[0] + 1 :]
    end_pattern = overrides.get("end_before")
    if end_pattern:
        compiled = re.compile(end_pattern)
        ends = [i for i, line in enumerate(region) if compiled.search(line.text)]
        if ends:
            region = region[: ends[0]]
    return region


def segment(region: list[Line], overrides: dict) -> tuple[list[Segment], list[str]]:
    """Tile the legal body into sections, part headings and schedules."""
    ignore = set(overrides.get("ignore_starts", []))
    segments: list[Segment] = []
    log: list[str] = []
    expected = 1
    current = Segment(kind="other")
    for line in region:
        text = line.text
        match = SECTION_START.match(text)
        kind = None
        if text in ignore:
            kind = None
        elif match and int(match.group(1)) == expected:
            kind = "section"
        elif match:
            log.append(f"ignored start on p{line.page} (expected {expected}): {text[:60]}")
        elif PART_HEADING.match(text):
            kind = "part"
        elif SCHEDULE_HEADING.match(text):
            kind = "schedule"
        if kind:
            if current.lines:
                segments.append(current)
            current = Segment(kind=kind, number=int(match.group(1)) if kind == "section" else None)
            if kind == "section":
                expected += 1
        current.lines.append(line)
    if current.lines:
        segments.append(current)
    return segments, log


def _heading_for(section: Segment, next_section: Segment | None, margin_lines: dict[int, list[Line]]) -> str:
    """The marginal note printed beside a section's first line, e.g.
    "Amendment of section 2 of Cap. 470." — read from the margin, not invented.

    We take margin lines on the same page from just above the section's first
    line down to where the next section starts (or 60pt below, whichever is first).
    """
    first = section.lines[0]
    limit = first.top + 60
    if next_section and next_section.lines[0].page == first.page:
        limit = min(limit, next_section.lines[0].top - 1)
    notes = [m.text for m in margin_lines.get(first.page, []) if first.top - 4 <= m.top <= limit]
    return normalise(" ".join(notes))


def extract(repo_root: Path, bill_id: str, pdf_file: str, write: bool = True) -> tuple[list[Clause], dict]:
    """Run extraction and all three checks. Returns (clauses, report)."""
    overrides = load_overrides(repo_root, bill_id)
    pdf_path = repo_root / "sources" / bill_id / pdf_file
    lines, margin_lines, stripped_log = read_pdf(pdf_path, overrides)
    source_text = normalise("\n".join(line.text for line in lines))

    region = _region(lines, overrides)
    segments, seg_log = segment(region, overrides)
    sections = [s for s in segments if s.kind == "section"]

    # Check: section count, if we know it independently.
    expected_count = overrides.get("expected_clause_count")
    if expected_count is not None and len(sections) != int(expected_count):
        raise ExtractError(f"found {len(sections)} sections, expected {expected_count}")

    clauses: list[Clause] = []
    for index, section in enumerate(sections):
        text = section.text
        following = sections[index + 1] if index + 1 < len(sections) else None
        clauses.append(
            Clause(
                id=f"c{section.number}",
                number=str(section.number),
                heading=_heading_for(section, following, margin_lines),
                text=text,
                text_sha256=sha256_text(text),
                pages=sorted({line.page for line in section.lines}),
            )
        )

    report = {
        "sections": len(sections),
        "parts": sum(1 for s in segments if s.kind == "part"),
        "schedules": sum(1 for s in segments if s.kind == "schedule"),
        "stripped": len(stripped_log),
        "ignored_starts": seg_log,
    }
    verify_clauses(clauses, source_text, segments, region)

    if write:
        audit = repo_root / "sources" / bill_id
        stem = Path(pdf_file).stem
        (audit / f"{stem}.body.txt").write_text(
            "\n".join(f"p{l.page}: {l.text}" for l in lines) + "\n", encoding="utf-8", newline="\n"
        )
        (audit / f"{stem}.stripped.txt").write_text(
            "\n".join(stripped_log) + "\n", encoding="utf-8", newline="\n"
        )
    return clauses, report


def verify_clauses(clauses: list[Clause], source_text: str, segments: list[Segment], region: list[Line]) -> None:
    """The three checks. Raises ExtractError naming the first failure."""
    for clause in clauses:
        # (a) verbatim: the section's text must be in the PDF's body stream.
        if normalise(clause.text) not in source_text:
            raise ExtractError(f"VERBATIM FAIL {clause.id}: text not found in the PDF body stream")
        # (b) fingerprint: stored text is normalised and matches its hash.
        if clause.text != normalise(clause.text):
            raise ExtractError(f"NOT NORMALISED {clause.id}")
        if clause.text_sha256 != sha256_text(clause.text):
            raise ExtractError(f"FINGERPRINT MISMATCH {clause.id}")
    # (c) coverage: all segments, in order, rebuild the whole legal body.
    rebuilt = normalise(" ".join(s.text for s in segments))
    original = normalise("\n".join(line.text for line in region))
    if rebuilt != original:
        raise ExtractError("COVERAGE FAIL: segments do not rebuild the legal body")


def run(repo_root: Path, bill_id: str, pdf_file: str) -> None:
    """Extract, check, and save into the bill JSON (keeping existing explanations)."""
    clauses, report = extract(repo_root, bill_id, pdf_file)
    path = bill_path(repo_root, bill_id)
    bill = load_bill(path)
    previous = {c.id: c for c in bill.clauses}
    merged: list[Clause] = []
    for clause in clauses:
        old = previous.get(clause.id)
        if old and old.text_sha256 == clause.text_sha256:
            # Same words as before: keep explanations, reviews and samples.
            clause = old.model_copy(update={"heading": clause.heading, "pages": clause.pages})
        merged.append(clause)
    bill.clauses = merged
    save_bill(bill, path)
    print(f"sections: {report['sections']}  parts: {report['parts']}  schedules: {report['schedules']}")
    print(f"header/footer lines stripped: {report['stripped']} (logged to sources/{bill_id}/*.stripped.txt)")
    for note in report["ignored_starts"]:
        print(f"  {note}")


def check_json(repo_root: Path, bill_id: str, pdf_file: str, json_path: Path) -> None:
    """Re-derive everything from the PDF and check an existing bill JSON.

    Writes nothing. Fails if any stored section text isn't word-for-word in the
    PDF, or its fingerprint doesn't match, or sections are missing/extra.
    """
    overrides = load_overrides(repo_root, bill_id)
    lines, _, _ = read_pdf(repo_root / "sources" / bill_id / pdf_file, overrides)
    source_text = normalise("\n".join(line.text for line in lines))
    fresh, _ = extract(repo_root, bill_id, pdf_file, write=False)
    stored = load_bill(json_path).clauses
    if [c.id for c in stored] != [c.id for c in fresh]:
        raise ExtractError("SECTION LIST MISMATCH between the JSON and the PDF")
    for clause in stored:
        if normalise(clause.text) not in source_text:
            raise ExtractError(f"VERBATIM FAIL {clause.id}: stored text not found in the PDF")
        if clause.text_sha256 != sha256_text(clause.text):
            raise ExtractError(f"FINGERPRINT MISMATCH {clause.id}")
    print(f"ok: {len(stored)} sections match the PDF word for word")
