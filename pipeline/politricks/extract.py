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
from .models import Bill, Clause, bill_path, load_bill, save_bill, validate_bill_id

# Header/footer patterns used unless an override replaces them.
DEFAULT_STRIP_PATTERNS = [
    r"^\d{1,4}$",  # a bare page number
]
MAX_STRIPPED_PER_PAGE = 4
EDGE_LINES = 3  # patterns only apply to the first/last 3 lines of a page

# A section starts with its number and a dot. In this scan, two-digit numbers
# often have no space after the dot ("10.Section 15 ..."), so the space is optional.
SECTION_START = re.compile(r"^(\d{1,3})\.\s*(?=[A-Z(\"“'])")
# Opt-in, for NAMED clause numbers only (override `start_noise: [13]`), for
# scans where a speck of dirt is read as a stray character before the number,
# e.g. "i 13.(1) A responsible...". Up to two junk characters are tolerated,
# and only when the expected next clause is one of the listed numbers — so it
# can't start a false clause anywhere else. The junk stays in the text: we
# never edit legal text, we only decide where a section begins.
SECTION_START_NOISY = re.compile(r"^(?:\S{1,2}\s+)?(\d{1,3})\.\s*(?=[A-Z(\"“'])")
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


def find_gutter(words: list[dict], search: tuple[float, float], min_width: float = 6.0) -> float | None:
    """Find the empty vertical strip between the body column and the notes.

    Scanned pages shift and stretch, so the body can end at x≈315 on one page
    and x≈344 on the next — no single fixed line works. What IS stable is that
    no word crosses the gutter. We merge every word's [x0, x1] span into
    covered intervals, then return the middle of the widest UNcovered strip
    inside `search`.

    `min_width` guards against mistaking an ordinary word space for the gutter:
    measured on the Public Participation scan, real gutters are 7.8–14pt wide
    while word spaces are ~3pt. Returns None if no strip is wide enough (then
    nothing is treated as a note on that page).
    """
    lo, hi = search
    # Merge the horizontal spans of all words that reach into the search window.
    spans = sorted((max(w["x0"], lo), min(w["x1"], hi)) for w in words if w["x1"] > lo and w["x0"] < hi)
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    # The gaps between covered intervals (and the window edges) are empty strips.
    edges = [lo] + [x for span in merged for x in span] + [hi]
    gaps = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]
    best = max(gaps, key=lambda gap: gap[1] - gap[0], default=None)
    if best is None or best[1] - best[0] < min_width:
        return None
    return (best[0] + best[1]) / 2


def _margin_words(page, search: tuple[float, float], min_width: float) -> tuple[list[dict], float | None, int]:
    """Words right of this page's gutter: the marginal notes.

    Returns (note words, gutter x, total words on the page). Used for noisy
    scans where notes come out at body size. A body word that ends a line just
    short of the gutter stays in the legal text, because the gutter is found
    from where words actually are on THIS page.
    """
    words = page.extract_words()
    gutter = find_gutter(words, search, min_width)
    if gutter is None:
        return [], None, len(words)
    return [w for w in words if w["x0"] >= gutter], gutter, len(words)


# If more than this share of a page's words land right of the gutter, the
# "gutter" is probably something else (an indented block, a table column) and
# real legal text would be dropped. We stop rather than guess.
MAX_NOTE_SHARE = 0.25


def _page_lines(
    page,
    margin_x: float,
    mode: str = "char",
    search: tuple[float, float] = (0.0, 0.0),
    min_width: float = 6.0,
    audit: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split one page into (body lines, marginal-note lines), each with `top`.

    Two ways to spot a marginal note (chosen per document in its overrides):

    - "char" (default): a character is marginal if it is BOTH smaller than the
      body text AND right of `margin_x`. Right for clean PDFs, where notes are
      printed in smaller type.
    - "gutter": whole WORDS right of this page's empty gutter strip are
      marginal, whatever their size (see find_gutter). Needed for noisy scans
      where notes come out at body size and the page shifts from scan to scan.
      We judge whole words, not letters, so no body word loses letters.
    """
    if mode == "gutter":
        notes, gutter, total = _margin_words(page, search, min_width)
        page_no = page.page_number
        if total and len(notes) / total > MAX_NOTE_SHARE:
            raise ExtractError(
                f"page {page_no}: {len(notes)}/{total} words fall right of the gutter at x={gutter} — "
                "that looks like body text, not marginal notes; stopping rather than dropping it"
            )
        if audit is not None:
            # Record exactly what this page loses to the margin, so anyone can
            # check no legal text went with it (written to <file>.margin.txt).
            where = "none found" if gutter is None else f"x={gutter:.1f}"
            audit.append(f"p{page_no}: gutter {where}; {len(notes)} note words: " + " | ".join(w["text"] for w in notes))
        boxes = [(w["x0"], w["top"], w["x1"], w["bottom"]) for w in notes]

        def is_margin(obj) -> bool:
            if obj.get("object_type") != "char":
                return False
            cx = (obj["x0"] + obj["x1"]) / 2
            cy = (obj["top"] + obj["bottom"]) / 2
            return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

        body = page.filter(lambda obj: not is_margin(obj))
        margin = page.filter(is_margin)
        return body.extract_text_lines(strip=True), margin.extract_text_lines(strip=True)

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


def read_pdf(
    pdf_path: Path, overrides: dict, margin_audit: list[str] | None = None
) -> tuple[list[Line], dict[int, list[Line]], list[str]]:
    """Return (body lines, marginal lines per page, stripped-line log).

    This is the single source of truth every check compares against. If
    `margin_audit` is given, gutter mode appends one line per page saying
    exactly which words it moved to the margin.
    """
    margin_x = float(overrides.get("margin_x", 395))
    patterns = [re.compile(p) for p in overrides.get("strip_patterns", DEFAULT_STRIP_PATTERNS)]
    skip_pages = set(overrides.get("skip_pages", []))
    mode = overrides.get("margin_mode", "char")
    if mode == "gutter" and "gutter_search" not in overrides:
        # Without a search window gutter mode would silently find nothing.
        raise ExtractError("margin_mode 'gutter' needs gutter_search: [x_from, x_to] in the overrides")
    search = tuple(overrides.get("gutter_search", (0, 0)))
    min_width = float(overrides.get("gutter_min", 6))

    body_lines: list[Line] = []
    margin_lines: dict[int, list[Line]] = {}
    stripped_log: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            if number in skip_pages or not page.chars:
                continue
            body, margin = _page_lines(page, margin_x, mode, search, min_width, margin_audit)
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
    noisy_numbers = set(overrides.get("start_noise", []))
    expected = 1
    current = Segment(kind="other")
    for line in region:
        text = line.text
        # Only the clause numbers named in the overrides get the noisy pattern.
        start_pattern = SECTION_START_NOISY if expected in noisy_numbers else SECTION_START
        match = start_pattern.match(text)
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
    pdf_path = repo_root / "sources" / validate_bill_id(bill_id) / pdf_file
    margin_audit: list[str] = []
    lines, margin_lines, stripped_log = read_pdf(pdf_path, overrides, margin_audit)
    source_text = normalise("\n".join(line.text for line in lines))

    region = _region(lines, overrides)
    segments, seg_log = segment(region, overrides)
    sections = [s for s in segments if s.kind == "section"]

    # Check: section count, if we know it independently.
    expected_count = overrides.get("expected_clause_count")
    if expected_count is not None and len(sections) != int(expected_count):
        raise ExtractError(f"found {len(sections)} sections, expected {expected_count}")

    # Hand-transcribed headings, for scans whose marginal notes come out garbled.
    # Every id must be a real section — a typo'd id would silently do nothing.
    transcriptions: dict[str, str] = overrides.get("heading_transcriptions", {}) or {}
    unknown = set(transcriptions) - {f"c{s.number}" for s in sections}
    if unknown:
        raise ExtractError(f"heading_transcriptions name sections that don't exist: {sorted(unknown)}")

    clauses: list[Clause] = []
    for index, section in enumerate(sections):
        text = section.text
        following = sections[index + 1] if index + 1 < len(sections) else None
        clauses.append(
            Clause(
                id=f"c{section.number}",
                number=str(section.number),
                # A hand-transcribed heading (overrides) wins over the scan's
                # marginal note. Headings are signposts, never legal text.
                heading=transcriptions.get(f"c{section.number}") or _heading_for(section, following, margin_lines),
                heading_source="transcribed" if f"c{section.number}" in transcriptions else "margin",
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
        if margin_audit:
            # Gutter mode only: every word each page moved to the margin, so a
            # human can confirm no legal text went with the marginal notes.
            (audit / f"{stem}.margin.txt").write_text(
                "\n".join(margin_audit) + "\n", encoding="utf-8", newline="\n"
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
            clause = old.model_copy(
                update={"heading": clause.heading, "heading_source": clause.heading_source, "pages": clause.pages}
            )
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
