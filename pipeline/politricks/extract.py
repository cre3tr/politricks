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

5. COUNTY MOTIONS. A motion has no numbered sections. Its overrides carry a
   `motion:` block (pages, start and end markers) and the resolution text,
   cut from the Hansard's text layer between those markers, becomes the one
   clause "c1". It gets the same verbatim and fingerprint checks.

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
from .reviewlock import bill_lock

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
    # For bills whose headings are printed ABOVE the clause, in the body column
    # (no marginal notes), like the Nairobi County Appropriation Bill:
    # `heading_before: {c2: 3}` means the 3 lines just above section 2's number
    # line are its heading. They move out of the previous segment into a
    # "heading" segment of their own, so a heading never becomes the tail of the
    # clause above it. Only a boundary moves; no text is supplied.
    heading_before = {str(k): int(v) for k, v in (overrides.get("heading_before") or {}).items()}
    # `part_heading: '<regex>'` swaps the Part-heading pattern. Only OCR text
    # needs it ("PART HI" for "PART III"; see ocr.py); text-layer bills keep
    # PART_HEADING.
    part_heading = re.compile(overrides["part_heading"]) if overrides.get("part_heading") else PART_HEADING
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
        elif part_heading.match(text):
            kind = "part"
        elif SCHEDULE_HEADING.match(text):
            kind = "schedule"
        if kind:
            heading_lines: list[Line] = []
            count = heading_before.get(f"c{expected}", 0) if kind == "section" else 0
            if count:
                if len(current.lines) < count:
                    raise ExtractError(
                        f"heading_before c{expected}: wants {count} line(s) above it, "
                        f"but only {len(current.lines)} precede it in their segment"
                    )
                heading_lines = current.lines[-count:]
                current.lines = current.lines[:-count]
            if current.lines:
                segments.append(current)
            if heading_lines:
                segments.append(Segment(kind="heading", lines=heading_lines))
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
    _check_kind(repo_root, bill_id, is_motion="motion" in overrides)

    if "motion" in overrides:
        # A county motion has no clauses: one clause holds its resolution text.
        clauses, report = extract_motion(lines, overrides)
        report["stripped"] = len(stripped_log)
        verify_motion(clauses, source_text)
        if write:
            _write_audit(repo_root, bill_id, pdf_file, lines, stripped_log, margin_audit)
        return clauses, report

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

    # Headings printed above a clause (`heading_before`): each heading segment
    # belongs to the section right after it. An id that got none is a typo or a
    # section that doesn't exist, and would otherwise silently do nothing.
    inline_headings: dict[int, str] = {}
    for position, seg in enumerate(segments):
        if seg.kind == "heading" and position + 1 < len(segments) and segments[position + 1].kind == "section":
            inline_headings[segments[position + 1].number] = seg.text
    unplaced = set((overrides.get("heading_before") or {})) - {f"c{n}" for n in inline_headings}
    if unplaced:
        raise ExtractError(f"heading_before names sections that don't exist: {sorted(unplaced)}")

    clauses: list[Clause] = []
    for index, section in enumerate(sections):
        text = section.text
        following = sections[index + 1] if index + 1 < len(sections) else None
        clauses.append(
            Clause(
                id=f"c{section.number}",
                number=str(section.number),
                # A hand-transcribed heading (overrides) wins, then a heading
                # printed above the clause, then the scan's marginal note.
                # Headings are signposts, never legal text.
                heading=transcriptions.get(f"c{section.number}")
                or inline_headings.get(section.number)
                or _heading_for(section, following, margin_lines),
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
        _write_audit(repo_root, bill_id, pdf_file, lines, stripped_log, margin_audit)
    return clauses, report


def _write_audit(
    repo_root: Path, bill_id: str, pdf_file: str, lines: list[Line], stripped_log: list[str], margin_audit: list[str]
) -> None:
    """The audit trail next to the PDF: every body line read, every line stripped."""
    audit = repo_root / "sources" / bill_id
    stem = Path(pdf_file).stem
    (audit / f"{stem}.body.txt").write_text(
        "\n".join(f"p{l.page}: {l.text}" for l in lines) + "\n", encoding="utf-8", newline="\n"
    )
    (audit / f"{stem}.stripped.txt").write_text("\n".join(stripped_log) + "\n", encoding="utf-8", newline="\n")
    if margin_audit:
        # Gutter mode only: every word each page moved to the margin, so a
        # human can confirm no legal text went with the marginal notes.
        (audit / f"{stem}.margin.txt").write_text("\n".join(margin_audit) + "\n", encoding="utf-8", newline="\n")


def _check_kind(repo_root: Path, bill_id: str, is_motion: bool) -> None:
    """A `motion:` override belongs to a `kind: motion` bill and no other.

    Only checked once the bill JSON exists (fetch makes it from sources.yaml).
    """
    path = bill_path(repo_root, bill_id)
    if not path.exists():
        return
    kind = load_bill(path).kind
    if is_motion and kind != "motion":
        raise ExtractError(f"overrides have a 'motion' block but {bill_id} is kind '{kind}'")
    if kind == "motion" and not is_motion:
        raise ExtractError(f"{bill_id} is a motion but its overrides have no 'motion' block (pages, start, end)")


def extract_motion(lines: list[Line], overrides: dict) -> tuple[list[Clause], dict]:
    """A county motion's resolution text as the bill's single clause.

    The overrides' `motion:` block says where it sits in the Hansard:

        motion:
          pages: [9, 10]          # PDF pages it is on
          start: 'THAT, aware'    # regex: the text begins at this match
          end: 'works\\.'         # regex: it ends right after the first match following start
          heading: "The motion"   # optional signpost (never fingerprinted text)

    The speaker label ("Hon. Anthony Maragu:") sits on the same line before
    "THAT,", so the cut is made inside the line, at the start match. Like the
    marker overrides for bills, this only chooses where to cut: every word
    comes from the PDF's text layer, and `verify_motion` re-checks that.
    """
    spec = overrides["motion"]
    for key in ("pages", "start", "end"):
        if key not in spec:
            raise ExtractError(f"motion override needs '{key}'")
    pages = [int(p) for p in spec["pages"]]
    start_re, end_re = re.compile(spec["start"]), re.compile(spec["end"])

    in_pages = [line for line in lines if line.page in pages]
    if not in_pages:
        raise ExtractError(f"motion: no text on pages {pages}")
    joined = "\n".join(line.text for line in in_pages)

    starts = list(start_re.finditer(joined))
    if len(starts) != 1:
        raise ExtractError(f"motion start marker {spec['start']!r} matched {len(starts)} time(s) on pages {pages}, expected exactly 1")
    begin = starts[0].start()
    end_match = end_re.search(joined, begin)
    if not end_match:
        raise ExtractError(f"motion end marker {spec['end']!r} not found after the start on pages {pages}")
    finish = end_match.end()

    # Which pages the cut text really touches (lines overlapping begin..finish).
    touched: set[int] = set()
    offset = 0
    for line in in_pages:
        line_end = offset + len(line.text)
        if offset < finish and line_end > begin:
            touched.add(line.page)
        offset = line_end + 1

    text = normalise(joined[begin:finish])
    clause = Clause(
        id="c1",
        number="1",
        heading=spec.get("heading", "The motion"),
        heading_source="transcribed",  # typed in the overrides, not read from a margin
        text=text,
        text_sha256=sha256_text(text),
        pages=sorted(touched),
    )
    return [clause], {"sections": 1, "parts": 0, "schedules": 0, "stripped": 0, "ignored_starts": []}


def verify_motion(clauses: list[Clause], source_text: str) -> None:
    """The motion's checks: one clause, verbatim in the PDF body stream, and
    its fingerprint matches its (normalised) text. Raises ExtractError."""
    if len(clauses) != 1:
        raise ExtractError(f"a motion holds exactly one clause, found {len(clauses)}")
    clause = clauses[0]
    if normalise(clause.text) not in source_text:
        raise ExtractError(f"VERBATIM FAIL {clause.id}: text not found in the PDF body stream")
    if clause.text != normalise(clause.text):
        raise ExtractError(f"NOT NORMALISED {clause.id}")
    if clause.text_sha256 != sha256_text(clause.text):
        raise ExtractError(f"FINGERPRINT MISMATCH {clause.id}")


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
    # Load to save under the bill's lock (reviewlock.py), so an open review
    # walk can't overwrite this on its next accept.
    with bill_lock(path):
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
    if "motion" in overrides:
        # A motion's one clause is defined by its start/end markers, so the
        # stored text must be exactly what the markers cut now, not merely
        # some other stretch of the same PDF.
        for clause, new in zip(stored, fresh):
            if clause.text != new.text:
                raise ExtractError(f"TEXT MISMATCH {clause.id}: the stored text is not what the PDF's motion markers cut")
    print(f"ok: {len(stored)} sections match the PDF word for word")


# --- self-test -------------------------------------------------------------


def self_test(repo_root: Path) -> bool:
    """Offline check of the two county paths: a motion's one clause, and a bill
    whose headings are printed above each clause. Builds tiny real PDFs by hand
    in a temp folder; writes nothing to the repo."""
    import contextlib
    import io
    import tempfile

    from .constitution import _tiny_pdf
    from .hashing import sha256_file
    from .selftest import Checker

    check = Checker()

    def snapshot() -> dict[str, str]:
        roots = [repo_root / "src" / "data" / "bills", repo_root / "sources", repo_root / "pipeline" / "overrides"]
        files = [p for root in roots for p in sorted(root.rglob("*")) if p.is_file() and p.suffix != ".lock"]
        return {p.relative_to(repo_root).as_posix(): sha256_file(p) for p in files}

    real_before = snapshot()
    check("control: the repo has bill JSONs, sources and overrides to protect", len(real_before) > 6, str(len(real_before)))

    def page(*texts: str) -> list[tuple[float, float, float, bool, str]]:
        return [(40, 40 + 16 * i, 10, False, text) for i, text in enumerate(texts)]

    def attempt(call) -> str:
        """The ExtractError message from a call that should fail, or ''."""
        try:
            call()
        except ExtractError as error:
            return str(error)
        return ""

    # ---- fixture 1: a Hansard with one motion across a page break ----
    head = "Page {} of 3 TEST ASSEMBLY Monday 1st January 2024"
    foot = "Disclaimer: for information only."
    hansard = _tiny_pdf(
        [
            page(
                head.format(1),
                "Hon. Speaker: Order. The first item is below.",
                "Hon. Ann Other: THAT, aware that this is a speech, not the motion",
                foot,
            ),
            page(
                head.format(2),
                "MOTION",
                "SAMPLE ROADS MOTION TITLE",
                "Hon. Jane Doe: THAT, aware that roads matter; further noted that the",
                "assembly urges the Executive to repair damaged roads in the course of",
                foot,
            ),
            page(
                head.format(3),
                "any public or private works.",
                "Hon. Jane Doe: Hon. Speaker, my speech follows here.",
                foot,
            ),
        ]
    )
    motion_overrides = {
        "motion": {
            "pages": [2, 3],
            "start": "THAT, aware that",
            "end": r"in\s+the\s+course\s+of\s+any\s+public\s+or\s+private\s+works\.",
            "heading": "The motion",
        },
        "strip_patterns": [r"^Page \d+ of \d+ TEST ASSEMBLY .+$", r"^Disclaimer: for information only\.$"],
        "margin_x": 10000,
    }

    # ---- fixture 2: a bill with headings printed above its clauses ----
    bill_pdf = _tiny_pdf(
        [
            page("THE TEST APPROPRIATION BILL, 2026"),
            page(
                "9 The Test Appropriation Bill, 2026",
                "ENACTED by the Test Assembly, as follows",
                "Short title",
                "1. This Act may be cited as the Test Act, 2026.",
                "Issue of money out of the fund and",
                "appropriation of the money granted",
                "2. (1) The Treasury may issue money from the fund for the",
                "service of the year.",
                "Appropriations in Aid",
                "3. In addition there may be applied the sums in the Schedule.",
                "FIRST SCHEDULE",
                "R1 Vote 100",
            ),
        ]
    )
    bill_overrides = {
        "start_after": "ENACTED by the Test Assembly",
        "margin_x": 10000,
        "strip_patterns": [r"^\d{1,3} The Test Appropriation Bill, 2026$"],
        "heading_before": {"c1": 1, "c2": 2, "c3": 1},
        "expected_clause_count": 3,
    }

    def build(root: Path, bill_id: str, kind: str, pdf: bytes, overrides: dict, pdf_file: str) -> None:
        """A temp repo holding one bill: its JSON skeleton, its PDF and its overrides."""
        (root / "pipeline" / "overrides").mkdir(parents=True, exist_ok=True)
        (root / "sources" / bill_id).mkdir(parents=True, exist_ok=True)
        (root / "sources" / bill_id / pdf_file).write_bytes(pdf)
        (root / "pipeline" / "overrides" / f"{bill_id}.yaml").write_text(
            yaml.safe_dump(overrides, allow_unicode=True), encoding="utf-8", newline="\n"
        )
        save_bill(
            Bill(
                id=bill_id, title="Self-test", chamber="county", kind=kind, status="passed",
                stage="Self-test fixture", stage_checked_at="2026-01-01",
            ),
            bill_path(root, bill_id),
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mid = "test-motion"
        build(root, mid, "motion", hansard, motion_overrides, "hansard.pdf")

        clauses, report = extract(root, mid, "hansard.pdf", write=False)
        text = clauses[0].text
        check("a motion extracts to exactly one clause, c1", [c.id for c in clauses] == ["c1"], str([c.id for c in clauses]))
        check(
            "the text starts at 'THAT,' and ends at the last words of the resolution",
            text.startswith("THAT, aware that roads matter") and text.endswith("in the course of any public or private works."),
            text,
        )
        check(
            "the resolution runs across the page break as one text",
            "in the course of any public or private works." in text and clauses[0].pages == [2, 3],
            f"pages {clauses[0].pages}",
        )
        check("the speaker label is left out", "Jane Doe" not in text and "Hon." not in text, text)
        check(
            "page headers and footers are not in the text",
            "TEST ASSEMBLY" not in text and "Disclaimer" not in text and "MOTION" not in text,
            text,
        )
        check("the earlier 'THAT, aware that' on page 1 was not used (pages restrict the cut)", "this is a speech" not in text)
        check("the fingerprint is the SHA-256 of the normalised text", clauses[0].text_sha256 == sha256_text(text))
        check("a motion's report counts one section", report["sections"] == 1)

        # Positive control: the header/footer WERE in the PDF, so stripping did something.
        lines_kept, _, stripped = read_pdf(root / "sources" / mid / "hansard.pdf", motion_overrides)
        lines_all, _, _ = read_pdf(root / "sources" / mid / "hansard.pdf", {**motion_overrides, "strip_patterns": []})
        check(
            "positive control: with stripping off the page header IS in the body stream",
            any("TEST ASSEMBLY" in line.text for line in lines_all) and not any("TEST ASSEMBLY" in l.text for l in lines_kept),
            f"{len(stripped)} lines stripped",
        )

        # The verbatim check must be able to fail: one word changed, with a matching hash.
        source_text = normalise("\n".join(line.text for line in lines_kept))
        verify_motion(clauses, source_text)  # the intact clause passes
        check("the verbatim check passes the intact motion", True)
        changed_text = text.replace("repair", "mend")
        changed = clauses[0].model_copy(update={"text": changed_text, "text_sha256": sha256_text(changed_text)})
        error = attempt(lambda: verify_motion([changed], source_text))
        check("a one-word change fails the verbatim check and names c1", "VERBATIM FAIL c1" in error, error)
        wrong_hash = clauses[0].model_copy(update={"text_sha256": "0" * 64})
        error = attempt(lambda: verify_motion([wrong_hash], source_text))
        check("a wrong fingerprint fails", "FINGERPRINT MISMATCH c1" in error, error)
        error = attempt(lambda: verify_motion([clauses[0], clauses[0]], source_text))
        check("two clauses fail: a motion holds exactly one", "exactly one clause" in error, error)

        # The markers must be able to fail too.
        def with_motion(**changes) -> tuple[list[Clause], dict]:
            return extract_motion(lines_kept, {"motion": {**motion_overrides["motion"], **changes}})

        error = attempt(lambda: with_motion(pages=[1, 2, 3]))
        check("a start marker that matches twice is refused", "matched 2 time(s)" in error, error)
        error = attempt(lambda: with_motion(start="THAT, noted that"))
        check("a start marker that matches nothing is refused", "matched 0 time(s)" in error, error)
        error = attempt(lambda: with_motion(end="never printed anywhere"))
        check("an end marker that is never found is refused", "end marker" in error and "not found" in error, error)
        error = attempt(lambda: with_motion(pages=[9]))
        check("pages with no text are refused", "no text on pages [9]" in error, error)

        # Kind and override must agree.
        build(root, "test-kind-a", "bill", hansard, motion_overrides, "hansard.pdf")
        error = attempt(lambda: extract(root, "test-kind-a", "hansard.pdf", write=False))
        check("a 'motion' override on a bill (kind 'bill') is refused", "is kind 'bill'" in error, error)
        build(root, "test-kind-b", "motion", hansard, {"margin_x": 10000}, "hansard.pdf")
        error = attempt(lambda: extract(root, "test-kind-b", "hansard.pdf", write=False))
        check("a motion with no 'motion' override is refused", "no 'motion' block" in error, error)

        # The whole run: load to save, then read back with --check-json.
        with contextlib.redirect_stdout(io.StringIO()):
            run(root, mid, "hansard.pdf")
        json_path = bill_path(root, mid)
        stored = load_bill(json_path)
        check("run saves one clause into the motion's JSON", [c.id for c in stored.clauses] == ["c1"] and stored.kind == "motion")
        check("a motion's participation stays null", stored.participation is None and stored.changes == [])
        first_bytes = json_path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            run(root, mid, "hansard.pdf")
        check("running again gives byte-identical JSON", json_path.read_bytes() == first_bytes)
        with contextlib.redirect_stdout(io.StringIO()) as said:
            check_json(root, mid, "hansard.pdf", json_path)
        check("--check-json passes on the saved motion", "ok: 1 sections" in said.getvalue(), said.getvalue())

        tampered = stored.model_copy(deep=True)
        tampered.clauses[0].text = tampered.clauses[0].text.replace("repair", "mend")
        tampered.clauses[0].text_sha256 = sha256_text(tampered.clauses[0].text)
        tamper_path = root / "tampered.json"
        save_bill(tampered, tamper_path)
        error = attempt(lambda: check_json(root, mid, "hansard.pdf", tamper_path))
        check("--check-json fails a motion with one word changed", "VERBATIM FAIL c1" in error, error)

        # A DIFFERENT stretch of the same PDF is verbatim but not what the markers cut.
        other = stored.model_copy(deep=True)
        other.clauses[0].text = "Hon. Speaker, my speech follows here."
        other.clauses[0].text_sha256 = sha256_text(other.clauses[0].text)
        other_path = root / "other.json"
        save_bill(other, other_path)
        error = attempt(lambda: check_json(root, mid, "hansard.pdf", other_path))
        check("--check-json fails another stretch of the PDF (verbatim, but not the cut)", "TEXT MISMATCH c1" in error, error)

        # ---- the bill with headings above its clauses ----
        bid = "test-bill"
        build(root, bid, "bill", bill_pdf, bill_overrides, "bill.pdf")
        clauses, report = extract(root, bid, "bill.pdf", write=False)
        by_id = {c.id: c for c in clauses}
        check("the bill splits into 3 clauses and 1 schedule", len(clauses) == 3 and report["schedules"] == 1, str(report))
        check(
            "each heading is the printed line(s) above its clause (two lines joined)",
            [c.heading for c in clauses]
            == ["Short title", "Issue of money out of the fund and appropriation of the money granted", "Appropriations in Aid"],
            str([c.heading for c in clauses]),
        )
        check(
            "no heading is left on the tail of the clause above",
            by_id["c1"].text.endswith("Test Act, 2026.") and by_id["c2"].text.endswith("service of the year."),
            f"{by_id['c1'].text!r} / {by_id['c2'].text!r}",
        )
        check("the running header is stripped", all("Appropriation Bill, 2026" not in c.text for c in clauses))

        # Control: without heading_before the heading DOES land on the clause above.
        no_headings = {k: v for k, v in bill_overrides.items() if k != "heading_before"}
        build(root, "test-bill-plain", "bill", bill_pdf, no_headings, "bill.pdf")
        plain, _ = extract(root, "test-bill-plain", "bill.pdf", write=False)
        check(
            "positive control: without heading_before, c2's heading ends c1's text",
            plain[0].text.endswith("appropriation of the money granted"),
            plain[0].text,
        )

        error = attempt(lambda: segment(read_pdf_region(root, bid), {**bill_overrides, "heading_before": {"c1": 5}}))
        check("heading_before wanting more lines than precede the clause is refused", "wants 5 line(s)" in error, error)
        typo = {**bill_overrides, "heading_before": {"c1": 1, "c2": 2, "c3": 1, "c9": 1}}
        build(root, "test-bill-typo", "bill", bill_pdf, typo, "bill.pdf")
        error = attempt(lambda: extract(root, "test-bill-typo", "bill.pdf", write=False))
        check("heading_before naming a section that doesn't exist is refused", "c9" in error, error)

    check("the repo's real bills, sources and overrides were not touched", snapshot() == real_before)
    return check.finish()


def read_pdf_region(root: Path, bill_id: str) -> list[Line]:
    """Self-test helper: the legal-body lines of a temp bill."""
    overrides = load_overrides(root, bill_id)
    lines, _, _ = read_pdf(root / "sources" / bill_id / "bill.pdf", overrides)
    return _region(lines, overrides)
