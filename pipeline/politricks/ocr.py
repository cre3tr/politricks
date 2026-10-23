"""Read a scanned official PDF with OCR, so it can be compared (slice 8).

Why this exists: the National Assembly-approved Public Participation Bill is a
10-page scan with no text layer (`devpost/spec.md > Extract Clauses` says "No
OCR", and that is still true of EVERYTHING the clauses and explanations are
made from). OCR is used for one thing only: the "after" side of a comparison
(`politricks compare`), and every line read this way is labelled on the site as
read by OCR, with a link to the PDF to check it against. It is never fingerprinted
as legal text and never becomes a clause.

`politricks ocr <bill-id>` does this, for the source marked `ocr: true` in
`pipeline/sources.yaml`:

1. FIND TESSERACT first. If it is not installed the command stops at once with
   the install line and writes nothing, not even the download.
2. FETCH + FINGERPRINT the PDF exactly as `fetch` does (official-domain
   allowlist on every redirect, `%PDF-` check, SHA-256 of the saved bytes) and
   record it as a source of the bill with `ocr: true`.
3. RENDER each page to an image (300 dpi, pdfplumber's `to_image`, which uses
   pypdfium2) and run the Tesseract command line on it, asking for word boxes
   (`tsv`). Words right of each page's empty gutter strip are the printed
   MARGINAL NOTES ("Short title.") and are set aside, the same idea as
   `extract.find_gutter`; where no gutter is found, small type right of the
   search window is the fallback. Everything set aside is listed in the audit
   file so a human can confirm no legal text went with it.
4. SAVE the text, page by page, to `sources/<bill-id>/<name>.ocr.txt`, with the
   PDF's SHA-256 in its header. It is committed, so CI and the site never need
   Tesseract, and a stale file (one read from a different PDF) is refused by
   `compare`. It holds the page BODY text after the margin split and the junk
   filter, not Tesseract's raw output: the removals are in
   `<name>.ocr.audit.txt`.
5. SEGMENT the text into clauses with the existing `extract.segment`, helped by
   a tolerant reading of clause numbers ("7," or "T." for "7."), and report how
   many it found. `compare` runs the same segmentation. What OCR noise can't
   resolve goes in `pipeline/overrides/<bill-id>.ocr.yaml`.

`politricks ocr --self-test` proves all of this offline: the Tesseract step is
a function that can be swapped, and the self-test swaps in a fake.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

from . import extract
from .extract import EDGE_LINES, MAX_NOTE_SHARE, MAX_STRIPPED_PER_PAGE, SECTION_START, SECTION_START_NOISY, Line
from .fetch import download_pdf, is_allowed, load_registry
from .hashing import normalise, sha256_bytes, sha256_file
from .models import Bill, Source, bill_path, load_bill, save_bill, validate_bill_id
from .reviewlock import BillLocked, bill_lock
from .selftest import Checker

DPI = 300
PSM = 4  # "a single column of text of variable sizes"
LANG = "eng"
FORMAT = "politricks ocr v1"

INSTALL_MESSAGE = (
    "Tesseract not installed: run `winget install --id UB-Mannheim.TesseractOCR -e` in an admin terminal, "
    "then open a new terminal and run this command again. (It looks for `tesseract` on PATH, then in "
    "C:/Program Files/Tesseract-OCR/. Set TESSERACT_CMD to its full path if it lives somewhere else.)"
)

# Tesseract is looked for here after PATH (the UB-Mannheim installer's default).
WINDOWS_LOCATIONS = (
    "C:/Program Files/Tesseract-OCR/tesseract.exe",
    "C:/Program Files (x86)/Tesseract-OCR/tesseract.exe",
)

# Defaults for the segmenter, used unless `pipeline/overrides/<id>.ocr.yaml`
# says otherwise. They are deliberately looser than extract's: OCR reads
# "Kenya" as "Kenyo" and "PART III" as "PART HI".
OCR_START_AFTER = r"(?i)ENACTED\s+by\s+the\s+Parliament"
OCR_END_BEFORE = r"(?i)^MEMORANDUM\s+OF\s+OBJECTS"
OCR_PART_HEADING = r"^PART\s+[IVXLH1|]{1,6}\b"

# Words under this Tesseract confidence (0 to 100) are counted per page in the
# text file's page header, so a human can see which pages to look at first.
LOW_CONFIDENCE = 60

# Characters Tesseract commonly reads in place of a digit, used ONLY to read a
# clause number whose value is already known (the next one expected).
_DIGITISE = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "i": "1", "|": "1", "!": "1",
                           "T": "7", "Z": "2", "z": "2", "S": "5", "s": "5", "B": "8", "g": "9", "A": "4"})
_OCR_START = re.compile(r"^(?:[^\w\s]{1,2}\s*)?([A-Za-z0-9|!]{1,3})\s?([.,;:])\s*(?=[A-Z(\"“'‘])")


class OcrError(Exception):
    """The OCR step can't go on. The message says exactly what and where."""


# --------------------------------------------------------------------------
# Words, lines and pages
# --------------------------------------------------------------------------


@dataclass
class Word:
    """One word Tesseract found, in IMAGE PIXELS (origin top left)."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    conf: float = 90.0
    line: tuple[int, int, int] = (0, 0, 0)  # Tesseract's own (block, paragraph, line)


# A recogniser turns one page image into words: (image, dpi, page number) -> words.
Recogniser = Callable[[object, int, int], list[Word]]


def parse_tsv(text: str) -> list[Word]:
    """Tesseract's `tsv` output to words. Only level-5 (word) rows with text."""
    words: list[Word] = []
    for raw in text.splitlines()[1:]:  # row 0 is the header
        fields = raw.split("\t", 11)
        if len(fields) < 12 or fields[0] != "5":
            continue
        word = fields[11].strip()
        if not word:
            continue
        try:
            left, top, width, height = (float(fields[i]) for i in (6, 7, 8, 9))
            conf = float(fields[10])
            key = (int(fields[2]), int(fields[3]), int(fields[4]))
        except ValueError:
            continue
        words.append(Word(word, left, left + width, top, top + height, conf, key))
    return words


def find_tesseract(
    which: Callable[[str], Optional[str]] = shutil.which,
    exists: Callable[[str], bool] = os.path.isfile,
    environ: Optional[dict] = None,
) -> Optional[str]:
    """The Tesseract executable, or None. TESSERACT_CMD, then PATH, then the
    installer's default folders."""
    env = os.environ if environ is None else environ
    named = env.get("TESSERACT_CMD")
    if named and exists(named):
        return named
    found = which("tesseract")
    if found:
        return found
    return next((path for path in WINDOWS_LOCATIONS if exists(path)), None)


def check_tesseract(binary: str, run=subprocess.run) -> str:
    """Prove the binary works before trusting what it says: it must print a
    version and list the `eng` language data. Returns the version line."""
    try:
        version = run([binary, "--version"], capture_output=True, timeout=60)
        languages = run([binary, "--list-langs"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise OcrError(f"Tesseract at {binary} did not run ({type(error).__name__}: {error})") from error
    out = (version.stdout + version.stderr).decode("utf-8", "replace")
    if version.returncode != 0 or "tesseract" not in out.lower():
        raise OcrError(f"Tesseract at {binary} did not print a version (exit {version.returncode}): {out[:200]!r}")
    langs = (languages.stdout + languages.stderr).decode("utf-8", "replace").split()
    if LANG not in langs:
        raise OcrError(f"Tesseract at {binary} has no '{LANG}' language data (it lists: {', '.join(langs[:8])})")
    return out.splitlines()[0].strip()


def tesseract_recogniser(binary: str, psm: int = PSM, run=subprocess.run) -> Recogniser:
    """The real recogniser: save the page as a PNG and run the command line on it."""

    def recognise(image, dpi: int, page_no: int) -> list[Word]:
        with tempfile.TemporaryDirectory(prefix="politricks-ocr-") as tmp:
            png = Path(tmp) / f"page-{page_no}.png"
            image.save(png, dpi=(dpi, dpi))
            try:
                done = run(
                    [binary, str(png), "stdout", "-l", LANG, "--psm", str(psm), "--dpi", str(dpi), "tsv"],
                    capture_output=True,
                    timeout=300,
                )
            except subprocess.TimeoutExpired as error:
                raise OcrError(f"page {page_no}: Tesseract took over 5 minutes") from error
            if done.returncode != 0:
                tail = done.stderr.decode("utf-8", "replace").strip()[-300:]
                raise OcrError(f"page {page_no}: Tesseract exited {done.returncode}: {tail}")
            return parse_tsv(done.stdout.decode("utf-8", "replace"))

    return recognise


def _is_junk(text: str) -> bool:
    """A line with nothing to read: rules and specks come out as "—_ ——" or "'".
    One real character survives only as a bare list marker like "(a)"."""
    letters = sum(1 for char in text if char.isalnum())
    if letters == 0:
        return True
    return letters == 1 and not re.fullmatch(r"\(\w{1,3}\)", text.strip())


@dataclass
class PageRead:
    """One page after the margin split."""

    number: int
    lines: list[str]
    words: int = 0
    low_confidence: int = 0
    margin_words: int = 0
    audit: list[str] = field(default_factory=list)


def read_page(
    words: list[Word], page_no: int, dpi: int, page_width_pt: float, overrides: dict
) -> PageRead:
    """Words to body lines: set the marginal notes and junk aside, keep the rest.

    Margin mode (`margin_mode` in the OCR overrides):
    - "gutter" (default): the page's widest empty vertical strip inside
      `gutter_search` (points; default 66% to 76% of the page width) is the
      gutter, and words right of it are notes, however big they are.
    - "none": nothing is a note.
    If no strip is wide enough (`gutter_min`, points), small type (under 80% of
    the page's median word height) right of the window's start is the note.
    """
    scale = 72.0 / dpi
    mode = overrides.get("margin_mode", "gutter")
    search = tuple(overrides.get("gutter_search") or (0.66 * page_width_pt, 0.76 * page_width_pt))
    min_width = float(overrides.get("gutter_min", 5))
    page = PageRead(page_no, [], words=len(words), low_confidence=sum(1 for w in words if 0 <= w.conf < LOW_CONFIDENCE))

    notes: set[int] = set()
    if mode not in ("gutter", "none"):
        raise OcrError(f"margin_mode must be 'gutter' or 'none', not {mode!r}")
    if mode == "gutter" and words:
        pts = [{"x0": w.x0 * scale, "x1": w.x1 * scale} for w in words]
        gutter = extract.find_gutter(pts, search, min_width)
        if gutter is not None:
            notes = {i for i, p in enumerate(pts) if p["x0"] >= gutter}
            how = f"gutter x={gutter:.1f}pt"
        else:
            median = statistics.median(w.bottom - w.top for w in words)
            notes = {
                i for i, w in enumerate(words)
                if w.x0 * scale >= search[0] and (w.bottom - w.top) < 0.8 * median
            }
            how = "no gutter found, small type right of the window"
        if len(notes) / len(words) > MAX_NOTE_SHARE:
            raise OcrError(
                f"page {page_no}: {len(notes)}/{len(words)} words fall in the margin ({how}); that looks like body "
                "text, not marginal notes. Stopping rather than dropping it: set margin_mode: none, or a tighter "
                f"gutter_search, in pipeline/overrides/<bill-id>.ocr.yaml (now {list(search)} pt)"
            )
        page.margin_words = len(notes)
        if notes:
            ordered = sorted(notes, key=lambda i: (words[i].top, words[i].x0))
            page.audit.append(f"p{page_no}: margin ({how}), {len(notes)} words: " + " | ".join(words[i].text for i in ordered))

    # Group what is left into Tesseract's own lines, in the order it gave them.
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for index, word in enumerate(words):
        if index not in notes:
            grouped.setdefault(word.line, []).append(word)  # dicts keep first-seen order
    for members in grouped.values():
        text = " ".join(w.text for w in sorted(members, key=lambda w: w.x0))
        if _is_junk(text):
            page.audit.append(f"p{page_no}: junk line dropped: {text}")
        else:
            page.lines.append(text)
    return page


def render_pages(pdf_path: Path, dpi: int):
    """Yield (page number, grayscale image, page width in points) for each page."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            image = page.to_image(resolution=dpi).original.convert("L")
            yield number, image, float(page.width)


# --------------------------------------------------------------------------
# The OCR text file
# --------------------------------------------------------------------------


@dataclass
class OcrText:
    sha256: str
    engine: str
    pages: dict[int, list[str]]
    headers: dict[int, str] = field(default_factory=dict)


_PAGE_MARK = re.compile(r"^=== page (\d+)\b.*===$")


def format_ocr_text(pdf_rel: str, sha256: str, engine: str, pages: list[PageRead]) -> str:
    out = [f"# {FORMAT}", f"# pdf: {pdf_rel}", f"# sha256: {sha256}", f"# engine: {engine}"]
    for page in pages:
        out.append(f"=== page {page.number} | {page.words} words | {page.low_confidence} under {LOW_CONFIDENCE}% confidence ===")
        out.extend(page.lines)
    return "\n".join(out) + "\n"


def read_ocr_text(path: Path) -> OcrText:
    if not path.exists():
        raise OcrError(f"no OCR text at {path.as_posix()}: run `politricks ocr <bill-id>` first")
    sha = engine = None
    pages: dict[int, list[str]] = {}
    headers: dict[int, str] = {}
    current: Optional[list[str]] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        mark = _PAGE_MARK.match(raw)
        if mark:
            number = int(mark.group(1))
            current = pages.setdefault(number, [])
            headers[number] = raw
        elif current is None:
            if raw.startswith("# sha256: "):
                sha = raw.removeprefix("# sha256: ").strip()
            elif raw.startswith("# engine: "):
                engine = raw.removeprefix("# engine: ").strip()
        else:
            current.append(raw)
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise OcrError(f"{path.name}: no valid '# sha256:' header line, so it can't be tied to a PDF")
    if not pages:
        raise OcrError(f"{path.name}: no pages")
    return OcrText(sha, engine or "unknown", pages, headers)


def require_fresh(ocr: OcrText, pdf_sha256: str, path: Path, bill_id: str) -> None:
    """Refuse an OCR file that was read from a different PDF than the one the
    bill records. Comparing against it would put the wrong text beside the law."""
    if ocr.sha256 != pdf_sha256:
        raise OcrError(
            f"STALE: {path.name} was read from a PDF with SHA-256 {ocr.sha256[:16]}..., but the bill's OCR source "
            f"is {pdf_sha256[:16]}.... Run `politricks ocr {bill_id} --force` to read the current PDF."
        )


# --------------------------------------------------------------------------
# From OCR text to clauses
# --------------------------------------------------------------------------


@dataclass
class OcrClause:
    id: str  # "c7"
    number: str
    text: str  # normalised; starts with "7. "
    pages: list[int]


@dataclass
class OcrSegmentation:
    clauses: list[OcrClause]
    log: list[str]  # every OCR fix and every ignored start, so nothing is silent


def ocr_overrides_path(repo_root: Path, bill_id: str) -> Path:
    return repo_root / "pipeline" / "overrides" / f"{validate_bill_id(bill_id)}.ocr.yaml"


def load_ocr_overrides(repo_root: Path, bill_id: str) -> dict:
    path = ocr_overrides_path(repo_root, bill_id)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def fix_clause_starts(region: list[Line], overrides: dict) -> tuple[list[Line], list[str]]:
    """Read clause numbers through OCR noise, using the one thing we know: which
    number comes next.

    `extract.segment` accepts a line only if it starts "<next number>." followed
    by a capital. A scan gives "7," or "T." or "l2." instead. This pass mirrors
    segment's own counter and, only where segment would not recognise the line,
    asks whether the line's first token reads as the expected number once
    look-alike characters are mapped (O to 0, l to 1, T to 7 ...). If so, the
    token is rewritten to "<number>." so segment sees a normal start. Only that
    token changes; every fix is logged. A token that doesn't read as the
    expected number is left alone, so this can never start a clause that isn't
    the next one.
    """
    ignore = set(overrides.get("ignore_starts", []))
    noisy_numbers = set(overrides.get("start_noise", []))
    expected = 1
    out: list[Line] = []
    log: list[str] = []
    for line in region:
        text = line.text
        pattern = SECTION_START_NOISY if expected in noisy_numbers else SECTION_START
        match = pattern.match(text)
        if text in ignore:
            pass
        elif match:
            if int(match.group(1)) == expected:
                expected += 1
        else:
            noisy = _OCR_START.match(text)
            if noisy and noisy.group(1).translate(_DIGITISE) == str(expected):
                fixed = f"{expected}. " + text[noisy.end():]
                log.append(f"read clause number {expected} through OCR noise on p{line.page}: {text[:30]!r} -> {fixed[:30]!r}")
                line = Line(fixed, line.page, line.top)
                expected += 1
        out.append(line)
    return out, log


def segment_ocr(ocr: OcrText, overrides: dict) -> OcrSegmentation:
    """OCR pages to numbered clauses. Raises OcrError if the result can't be trusted:
    wrong clause count, a clause whose text is not in the OCR text, or segments
    that do not rebuild the legal body."""
    merged = {"start_after": OCR_START_AFTER, "end_before": OCR_END_BEFORE, "part_heading": OCR_PART_HEADING, **overrides}
    skip = set(merged.get("skip_pages", []))
    patterns = [re.compile(p) for p in merged.get("strip_patterns", extract.DEFAULT_STRIP_PATTERNS)]
    lines: list[Line] = []
    for number in sorted(ocr.pages):
        if number in skip:
            continue
        page_lines = ocr.pages[number]
        kept: list[Line] = []
        removed = 0
        for index, text in enumerate(page_lines):
            at_edge = index < EDGE_LINES or index >= len(page_lines) - EDGE_LINES
            if at_edge and any(p.search(text) for p in patterns):
                removed += 1
                continue
            kept.append(Line(text, number, float(index)))
        if removed > MAX_STRIPPED_PER_PAGE:
            raise OcrError(f"page {number}: {removed} lines stripped (max {MAX_STRIPPED_PER_PAGE}): a strip pattern is probably matching real text")
        lines.extend(kept)

    try:
        region = extract._region(lines, merged)
        original_text = normalise("\n".join(line.text for line in region))
        fixed, log = fix_clause_starts(region, merged)
        segments, seg_log = extract.segment(fixed, merged)
    except extract.ExtractError as error:
        raise OcrError(str(error)) from error
    log += seg_log
    sections = [s for s in segments if s.kind == "section"]
    expected_count = merged.get("expected_clause_count")
    if expected_count is not None and len(sections) != int(expected_count):
        raise OcrError(
            f"found {len(sections)} clauses in the OCR text, expected {expected_count}. "
            "See the log above; fix with start_noise / ignore_starts in pipeline/overrides/<bill-id>.ocr.yaml"
        )
    if not sections:
        raise OcrError("no clauses found in the OCR text")
    if normalise(" ".join(s.text for s in segments)) != normalise("\n".join(line.text for line in fixed)):
        raise OcrError("COVERAGE FAIL: the segments do not rebuild the OCR legal body")
    clauses = []
    for section in sections:
        text = section.text
        # The clause's words (everything after its number token) must be in the
        # OCR text as read: segmentation may only choose where to cut.
        body = re.sub(r"^\d{1,3}\.\s*", "", text)
        if body not in original_text:
            raise OcrError(f"clause {section.number}: its text is not in the OCR text (segmentation error)")
        clauses.append(OcrClause(f"c{section.number}", str(section.number), text, sorted({line.page for line in section.lines})))
    return OcrSegmentation(clauses, log)


# --------------------------------------------------------------------------
# `politricks ocr`
# --------------------------------------------------------------------------


def ocr_source_entry(repo_root: Path, bill_id: str) -> dict:
    """The registry entry of the bill's one `ocr: true` source."""
    registry = load_registry(repo_root)
    if bill_id not in registry:
        raise OcrError(f"unknown bill id '{bill_id}': it is not in pipeline/sources.yaml")
    found = [item for item in registry[bill_id]["sources"] if item.get("ocr")]
    if not found:
        raise OcrError(f"{bill_id} has no source marked `ocr: true` in pipeline/sources.yaml")
    if len(found) > 1:
        raise OcrError(f"{bill_id} has {len(found)} sources marked `ocr: true`; this command reads exactly one")
    return found[0]


def paths_for(repo_root: Path, bill_id: str, entry: dict) -> tuple[Path, Path, Path]:
    """(the PDF, its OCR text, its audit file), all under sources/<bill-id>/."""
    folder = repo_root / "sources" / validate_bill_id(bill_id)
    pdf = folder / entry["file"]
    stem = pdf.stem
    return pdf, folder / f"{stem}.ocr.txt", folder / f"{stem}.ocr.audit.txt"


def _record_source(repo_root: Path, bill_id: str, entry: dict, pdf: Path, digest: str) -> None:
    path = bill_path(repo_root, bill_id)
    with bill_lock(path):
        bill = load_bill(path)
        previous = {s.file: s for s in bill.sources}
        file = pdf.relative_to(repo_root).as_posix()
        old = previous.get(file)
        fetched_at = (
            old.fetched_at if old is not None and old.sha256 == digest
            else datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        source = Source(
            label=entry["label"], url=entry["url"], official=bool(entry.get("official")), fetched_at=fetched_at,
            sha256=digest, file=file, scanned=bool(entry.get("scanned", False)), ocr=True,
        )
        bill.sources = [source if s.file == file else s for s in bill.sources]
        if file not in previous:
            bill.sources.append(source)
        save_bill(bill, path)


def _report_segmentation(repo_root: Path, bill_id: str, ocr: OcrText) -> bool:
    try:
        result = segment_ocr(ocr, load_ocr_overrides(repo_root, bill_id))
    except OcrError as error:
        print(f"SEGMENTATION FAILED: {error}")
        return False
    for line in result.log:
        print(f"  {line}")
    print(f"segmented {len(result.clauses)} clauses from the OCR text (clause numbers {result.clauses[0].number} to {result.clauses[-1].number})")
    return True


def run(
    repo_root: Path,
    bill_id: str,
    force: bool = False,
    psm: int = PSM,
    dpi: int = DPI,
    finder: Callable[[], Optional[str]] = find_tesseract,
    download: Callable[[str], tuple[bytes, str]] = download_pdf,
    recogniser: Optional[Recogniser] = None,
) -> int:
    """Returns the exit code: 0 done (or already up to date), 1 anything wrong.

    `finder`, `download` and `recogniser` exist so the self-test can pass fakes;
    the defaults are the real Tesseract and the real, allowlisted download.
    """
    try:
        validate_bill_id(bill_id)
        if not bill_path(repo_root, bill_id).exists():
            raise OcrError(f"no bill file for {bill_id}: run `politricks fetch {bill_id}` first")
        entry = ocr_source_entry(repo_root, bill_id)
        pdf, text_path, audit_path = paths_for(repo_root, bill_id, entry)
        if not is_allowed(entry["url"]):
            raise OcrError(f"refused: {entry['url']} is not on the official allowlist")

        # Already read? Same PDF on disk, same hash in the OCR text: nothing to do,
        # and no need for Tesseract or the network.
        if not force and pdf.exists() and text_path.exists():
            try:
                existing = read_ocr_text(text_path)
            except OcrError:
                existing = None
            if existing is not None and existing.sha256 == sha256_file(pdf):
                print(f"{text_path.relative_to(repo_root).as_posix()} was already read from this PDF (sha256 {existing.sha256[:16]}...). Use --force to read it again.")
                return 0 if _report_segmentation(repo_root, bill_id, existing) else 1

        # Tesseract first: if it is missing, nothing has been downloaded or written.
        if recogniser is None:
            binary = finder()
            if binary is None:
                print(f"FAILED: {INSTALL_MESSAGE}")
                return 1
            engine = f"{check_tesseract(binary)}; lang {LANG}; psm {psm}; {dpi} dpi"
            recogniser = tesseract_recogniser(binary, psm)
        else:
            engine = f"injected recogniser; lang {LANG}; psm {psm}; {dpi} dpi"

        body, final_url = download(entry["url"])
        digest = sha256_bytes(body)
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(body)
        print(entry["label"])
        print(f"  url:    {entry['url']}")
        if final_url != entry["url"]:
            print(f"  redirected: {final_url}")
        print(f"  saved:  {pdf.relative_to(repo_root).as_posix()} ({len(body):,} bytes)")
        print(f"  sha256: {digest}")

        overrides = load_ocr_overrides(repo_root, bill_id)
        pages: list[PageRead] = []
        for number, image, width_pt in render_pages(pdf, dpi):
            words = recogniser(image, dpi, number)
            page = read_page(words, number, dpi, width_pt, overrides)
            pages.append(page)
            print(f"  page {number}: {page.words} words, {len(page.lines)} lines kept, "
                  f"{page.margin_words} margin words set aside, "
                  f"{page.low_confidence} under {LOW_CONFIDENCE}% confidence")
        if not pages:
            raise OcrError("the PDF has no pages")
        if sum(p.words for p in pages) == 0:
            raise OcrError("OCR found no words on any page: is the language data installed, and is this the right file?")

        rel = pdf.relative_to(repo_root).as_posix()
        text_path.write_text(format_ocr_text(rel, digest, engine, pages), encoding="utf-8", newline="\n")
        audit = [line for page in pages for line in page.audit]
        audit_path.write_text(
            f"# {FORMAT} audit: what was set aside from {rel} (sha256 {digest})\n" + "\n".join(audit) + ("\n" if audit else ""),
            encoding="utf-8", newline="\n",
        )

        # Read both files back: what landed, not what we meant to write.
        stored = read_ocr_text(text_path)
        require_fresh(stored, digest, text_path, bill_id)
        if sorted(stored.pages) != [p.number for p in pages]:
            raise OcrError(f"read-back mismatch: the saved text has pages {sorted(stored.pages)}")
        _record_source(repo_root, bill_id, entry, pdf, digest)
        print(f"wrote {text_path.relative_to(repo_root).as_posix()} and {audit_path.relative_to(repo_root).as_posix()} ({engine})")
        print(f"recorded {rel} as a source of {bill_id} (ocr: true)")
        return 0 if _report_segmentation(repo_root, bill_id, stored) else 1
    except OcrError as error:
        print(f"FAILED: {error}")
        return 1
    except BillLocked as error:
        print(f"FAILED: {error}")
        return 1
    except Exception as error:  # a download or render error: report it as one line, exit 1
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"FAILED: {type(error).__name__}: {error}")
        return 1


# --------------------------------------------------------------------------
# Self-test: offline, in a temp folder, with a fake Tesseract
# --------------------------------------------------------------------------

TEST_ID = "test-ocr-bill"
OCR_URL = "https://www.parliament.go.ke/sites/default/files/test-approved.pdf"
PAGE_W, PAGE_H = 492.0, 703.0  # the self-test PDF's page size in points (constitution._tiny_pdf)


def _quiet(fn, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = fn(*args, **kwargs)
    return result, buffer.getvalue()


def _fixture_pdf_bytes() -> bytes:
    from .constitution import _tiny_pdf

    # Three pages of text the fake OCR never looks at: only the page images matter.
    return _tiny_pdf([[(90, 100, 10, False, "page one")], [(90, 100, 10, False, "page two")], [(90, 100, 10, False, "page three")]])


def lay_out(lines: list[str], dpi: int, x_pt: float = 60.0, top_pt: float = 80.0, size_pt: float = 10.0,
            conf: float = 92.0, block: int = 1) -> list[Word]:
    """Words for the fake recogniser: each line's words laid left to right in
    pixels, 14 pt apart vertically. A stand-in for Tesseract's own boxes."""
    px = dpi / 72.0
    out: list[Word] = []
    for row, line in enumerate(lines):
        x = x_pt
        for word in line.split():
            width = 4.6 * len(word) * size_pt / 10.0
            out.append(Word(word, x * px, (x + width) * px, (top_pt + 14 * row) * px, (top_pt + 14 * row + size_pt) * px,
                            conf, (block, 1, row + 1)))
            x += width + 3.0
    return out


# Invented text, never real law: three pages of a made-up bill.
SELFTEST_PAGES = {
    1: ["THE EXAMPLE BILL, 2026", "ARRANGEMENT OF CLAUSES", "1-Short title.", "2-Duty."],
    2: [
        "ENACTED by the Parliament of Kenya, as follows",
        "PART I - PRELIMINARY",
        "1. This Act may be cited as the Example Act, 2026.",
        "2, A person shall file a return within thirty days.",
        "(a) the first thing;",
        "(b) the second thing.",
        "PART HI - GENERAL",
        "3. The Minister may make rules.",
    ],
    3: ["9. Another numbered line that is not clause 4", "A. This Act comes into force on assent."],
}
SELFTEST_NOTES = {2: ["Short title", "Duty"]}  # printed marginal notes, right of the gutter


def _selftest_recogniser(dpi_seen: list, junk: bool = True) -> Recogniser:
    def recognise(image, dpi: int, page_no: int) -> list[Word]:
        dpi_seen.append((page_no, image.size, dpi))
        body = lay_out(SELFTEST_PAGES.get(page_no, []), dpi)
        notes = lay_out(SELFTEST_NOTES.get(page_no, []), dpi, x_pt=370.0, top_pt=96.0, size_pt=7.0, block=2)
        extra = lay_out(["— _ —"], dpi, top_pt=300.0, block=3) if junk and page_no == 2 else []
        return body + notes + extra

    return recognise


def _root(repo_root: Path, tmp: Path, overrides: Optional[str] = None) -> Path:
    """A throwaway repo: a registry with one ocr source, one bill, the OCR overrides."""
    (tmp / "pipeline" / "overrides").mkdir(parents=True)
    (tmp / "src" / "data" / "bills").mkdir(parents=True)
    registry = {
        TEST_ID: {
            "title": "The Example Bill, 2026", "chamber": "national", "kind": "bill", "status": "active",
            "stage": "Test", "stage_checked_at": "2026-10-23",
            "sources": [
                {"label": "Example Bill (as first published)", "url": "https://www.parliament.go.ke/test/first.pdf", "official": True, "file": "bill.pdf"},
                {"label": "Example Bill (as approved: text read by OCR)", "url": OCR_URL, "official": True, "file": "approved.pdf", "ocr": True},
            ],
        }
    }
    (tmp / "pipeline" / "sources.yaml").write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8", newline="\n")
    (tmp / "pipeline" / "overrides" / f"{TEST_ID}.ocr.yaml").write_text(
        overrides
        if overrides is not None
        else (
            "skip_pages: [1]\n"
            "expected_clause_count: 4\n"
            "gutter_search: [335, 355]\n"
            "gutter_min: 6\n"
            "start_noise: []\n"
        ),
        encoding="utf-8", newline="\n",
    )
    bill = Bill(
        id=TEST_ID, title="The Example Bill, 2026", chamber="national", kind="bill", status="active", stage="Test",
        stage_checked_at="2026-10-23",
        sources=[Source(label="Example Bill (as first published)", url="https://www.parliament.go.ke/test/first.pdf",
                        official=True, fetched_at="2026-10-01T00:00:00+00:00", sha256="0" * 64, file=f"sources/{TEST_ID}/bill.pdf")],
    )
    save_bill(bill, bill_path(tmp, TEST_ID))
    return tmp


def self_test(repo_root: Path) -> bool:
    check = Checker()
    watched = [
        repo_root / "pipeline" / "sources.yaml",
        repo_root / "pipeline" / "overrides" / "public-participation-bill-2025.ocr.yaml",
    ] + sorted((repo_root / "src" / "data" / "bills").glob("*.json"))
    watched = [p for p in watched if p.exists()]
    before_real = {p: sha256_file(p) for p in watched}
    real_ocr_before = sorted(p.name for p in (repo_root / "sources").rglob("*.ocr.*"))
    pdf_bytes = _fixture_pdf_bytes()
    pdf_digest = sha256_bytes(pdf_bytes)

    # --- A. the pure pieces --------------------------------------------------
    tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" \
          "4\t1\t1\t1\t1\t0\t10\t10\t90\t20\t-1\t\n" \
          "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t96.5\tHello\n" \
          "5\t1\t1\t1\t1\t2\t60\t10\t40\t20\t41\tworld\n" \
          "5\t1\t1\t1\t1\t3\t110\t10\t10\t20\t-1\t \n"
    parsed = parse_tsv(tsv)
    check("parse_tsv keeps only the two level-5 rows that have text",
          [w.text for w in parsed] == ["Hello", "world"] and parsed[1].conf == 41.0 and parsed[0].x1 == 50.0, str(parsed))
    check("find_tesseract: TESSERACT_CMD wins, then PATH, then the install folder, then None",
          find_tesseract(lambda n: "/usr/bin/tesseract", lambda p: p == "/x/t", {"TESSERACT_CMD": "/x/t"}) == "/x/t"
          and find_tesseract(lambda n: "/usr/bin/tesseract", lambda p: False, {}) == "/usr/bin/tesseract"
          and find_tesseract(lambda n: None, lambda p: p == WINDOWS_LOCATIONS[0], {}) == WINDOWS_LOCATIONS[0]
          and find_tesseract(lambda n: None, lambda p: False, {}) is None)
    class _Done:
        def __init__(self, out: bytes = b"", err: bytes = b"", code: int = 0) -> None:
            self.stdout, self.stderr, self.returncode = out, err, code

    def fake_run(version_ok=True, langs=b"List of available languages (2):\neng\nosd\n", tsv_out=None, record=None):
        def run_(args, **kwargs):
            if record is not None:
                record.append(list(args))
                if len(args) > 1 and str(args[1]).endswith(".png"):  # the page image must exist while Tesseract runs
                    record.append(("png", Path(args[1]).exists(), Path(args[1]).read_bytes()[:8]))
            if "--version" in args:
                return _Done(err=b"tesseract 5.3.3\n leptonica-1.83.1\n") if version_ok else _Done(err=b"nope", code=1)
            if "--list-langs" in args:
                return _Done(out=langs)
            return _Done(out=(tsv_out or tsv).encode("utf-8"))
        return run_

    check("check_tesseract returns the version line when the binary runs and lists eng",
          check_tesseract("tess", fake_run()) == "tesseract 5.3.3")
    for label, runner, want in (
        ("a binary that fails --version", fake_run(version_ok=False), "did not print a version"),
        ("a binary with no eng language data", fake_run(langs=b"List of available languages (1):\nosd\n"), "no 'eng' language data"),
    ):
        try:
            check_tesseract("tess", runner)
            message = ""
        except OcrError as error:
            message = str(error)
        check(f"check_tesseract refuses {label}", want in message, message)
    calls: list = []
    from PIL import Image

    image = Image.new("L", (80, 60), 255)
    got = tesseract_recogniser("tess", 4, fake_run(record=calls))(image, 300, 7)
    args = calls[0]
    check("the real recogniser asks the CLI for word boxes: <png> stdout -l eng --psm 4 --dpi 300 tsv",
          args[0] == "tess" and args[2:] == ["stdout", "-l", "eng", "--psm", "4", "--dpi", "300", "tsv"]
          and args[1].endswith("page-7.png"), str(args))
    check("the page image existed on disk as a real PNG while Tesseract ran",
          calls[1][1] is True and calls[1][2] == b"\x89PNG\r\n\x1a\n", str(calls[1]))
    check("and its words came back from the TSV", [w.text for w in got] == ["Hello", "world"])

    check("a bare rule line is junk, a lone list marker is not",
          _is_junk("— _ —") and _is_junk("'") and not _is_junk("(a)") and not _is_junk("1. Hello"))

    # clause numbers through OCR noise, and ONLY the expected one
    def starts(*texts: str, overrides: Optional[dict] = None):
        lines = [Line(t, 1, float(i)) for i, t in enumerate(texts)]
        return fix_clause_starts(lines, overrides or {})

    fixed, log = starts("1. First clause.", "2, Second clause.", "T. ignored?", "l3. Thirteen?")
    check("'2,' is read as clause 2 and 'T.' is NOT read as 7 while 3 is expected",
          [l.text for l in fixed][:3] == ["1. First clause.", "2. Second clause.", "T. ignored?"], str([l.text for l in fixed]))
    fixed, log = starts(*[f"{n}. Text." for n in range(1, 7)], "T. Seventh clause.", "8. Eighth clause.")
    check("'T.' IS read as clause 7 when 7 is the one expected, and the fix is logged",
          fixed[6].text == "7. Seventh clause." and len(log) == 1 and "T." in log[0], str(log))
    fixed, log = starts(*[f"{n}. Text." for n in range(1, 10)], "I0. Tenth clause.")
    check("'I0.' is read as clause 10", fixed[9].text == "10. Tenth clause.", fixed[9].text)
    fixed, log = starts("1. First.", "of 2. Second mentioned in a wrapped line", "2. Real second.")
    check("a wrapped line that merely contains '2.' is not a clause start", fixed[1].text.startswith("of 2."), fixed[1].text)

    # --- B. Tesseract missing: nothing written, nothing downloaded -----------
    with tempfile.TemporaryDirectory(prefix="politricks-ocr-") as tmp_name:
        root = _root(repo_root, Path(tmp_name))
        listing = lambda: sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        files_before = listing()
        downloads: list[str] = []
        rc, out = _quiet(run, root, TEST_ID, finder=lambda: None, download=lambda url: downloads.append(url) or (pdf_bytes, url))
        check("no Tesseract: exit 1 with the winget install line", rc == 1 and "winget install --id UB-Mannheim.TesseractOCR -e" in out and "admin terminal" in out, out[:300])
        check("no Tesseract: nothing downloaded and not one file written", not downloads and listing() == files_before, str(downloads))

        # --- C. the happy path, with a fake recogniser -----------------------
        seen: list = []
        rc, out = _quiet(run, root, TEST_ID, download=lambda url: (pdf_bytes, url), recogniser=_selftest_recogniser(seen))
        check("ocr runs end to end and exits 0", rc == 0, out[-400:])
        want_w = round(PAGE_W * DPI / 72)
        check("every page was rendered at 300 dpi (positive control: the image is 2050 px wide)",
              len(seen) == 3 and all(abs(size[0] - want_w) <= 1 and dpi == DPI for _page, size, dpi in seen), str(seen))
        folder = root / "sources" / TEST_ID
        stored = read_ocr_text(folder / "approved.ocr.txt")
        check("the OCR text carries the PDF's SHA-256 in its header", stored.sha256 == pdf_digest and sha256_file(folder / "approved.pdf") == pdf_digest)
        page2 = stored.pages[2]
        check("body lines kept, in order", page2[:3] == ["ENACTED by the Parliament of Kenya, as follows", "PART I - PRELIMINARY", "1. This Act may be cited as the Example Act, 2026."], str(page2))
        check("the marginal notes are not in the body text, and the junk line is gone",
              not any("Short title" in l or "Duty" in l for l in page2) and not any("— _" in l for l in page2), str(page2))
        audit = (folder / "approved.ocr.audit.txt").read_text(encoding="utf-8")
        check("the audit file lists exactly what was set aside (margin words and the junk line)",
              "Short | title | Duty" in audit and "junk line dropped" in audit and "gutter x=" in audit, audit[:300])
        bill = load_bill(bill_path(root, TEST_ID))
        check("the bill gained the PDF as a second source with ocr: true and the right hash; the first source is unchanged",
              len(bill.sources) == 2 and bill.sources[1].ocr and bill.sources[1].sha256 == pdf_digest
              and not bill.sources[0].ocr and bill.sources[0].sha256 == "0" * 64, str([s.model_dump() for s in bill.sources]))
        check("the saved JSON leaves `ocr` out of a source that has none (older bill files stay byte-identical)",
              '"ocr": true' in bill_path(root, TEST_ID).read_text(encoding="utf-8") and bill_path(root, TEST_ID).read_text(encoding="utf-8").count('"ocr"') == 1)
        check("segmentation: 4 clauses, '2,' and 'A.' read through the noise and both fixes logged",
              "segmented 4 clauses" in out and "'2, A" in out and "'A. This" in out, out[-600:])
        seg = segment_ocr(stored, load_ocr_overrides(root, TEST_ID))
        check("clause numbers 1 to 4; the 'PART HI' heading is not part of clause 2; clause 4 starts '4. This Act'",
              [c.number for c in seg.clauses] == ["1", "2", "3", "4"] and "PART" not in seg.clauses[1].text
              and seg.clauses[3].text.startswith("4. This Act comes into force"), str([c.text for c in seg.clauses]))
        check("a stray '9.' is NOT a clause: it is logged as ignored and stays in clause 3, which now spans pages 2 and 3",
              seg.clauses[2].pages == [2, 3] and "9. Another numbered line" in seg.clauses[2].text
              and any("ignored start" in l and "9. Another numbered" in l for l in seg.log), str(seg.log))
        check("clause 1's page and the arrangement page: page 1 is skipped, so no clause lists page 1",
              all(1 not in c.pages for c in seg.clauses))

        # --- C2. `fetch` leaves an ocr source alone ----------------------------
        from . import fetch

        recorded = load_bill(bill_path(root, TEST_ID)).sources[1]
        urls: list[str] = []

        def stub_download(url: str) -> tuple[bytes, str]:
            urls.append(url)
            return pdf_bytes, url

        real_download = fetch.download_pdf
        fetch.download_pdf = stub_download
        try:
            _quiet(fetch.fetch_bill, root, TEST_ID)
        finally:
            fetch.download_pdf = real_download
        after_fetch = load_bill(bill_path(root, TEST_ID)).sources
        check("fetch downloads the text-layer source and never the ocr: true one",
              urls == ["https://www.parliament.go.ke/test/first.pdf"], str(urls))
        check("and the OCR source `politricks ocr` recorded survives a fetch, fingerprint and date intact",
              len(after_fetch) == 2 and after_fetch[1] == recorded and after_fetch[0].sha256 == pdf_digest and not after_fetch[0].ocr,
              str([s.model_dump() for s in after_fetch]))

        # --- D. up to date: no Tesseract, no network, no new bytes ------------
        snapshot = {p: sha256_file(p) for p in folder.iterdir()}
        snapshot[bill_path(root, TEST_ID)] = sha256_file(bill_path(root, TEST_ID))
        calls: list = []
        rc, out = _quiet(run, root, TEST_ID, finder=lambda: calls.append("find") or None,
                         download=lambda url: calls.append("download") or (b"", url), recogniser=None)
        check("up to date: exit 0, says so, never looks for Tesseract or the network", rc == 0 and "already read" in out and not calls, out[:200])
        check("up to date: every file byte-identical", {p: sha256_file(p) for p in snapshot} == snapshot)

        # --- E. a stale OCR file is refused -----------------------------------
        text_path = folder / "approved.ocr.txt"
        good = text_path.read_text(encoding="utf-8")
        other = "f" * 64
        text_path.write_text(good.replace(f"# sha256: {pdf_digest}", f"# sha256: {other}"), encoding="utf-8", newline="\n")
        try:
            require_fresh(read_ocr_text(text_path), pdf_digest, text_path, TEST_ID)
            refused = ""
        except OcrError as error:
            refused = str(error)
        check("a stale OCR file (another PDF's hash) is refused, and the message names the fix",
              "STALE" in refused and f"ocr {TEST_ID} --force" in refused, refused)
        seen2: list = []
        rc, out = _quiet(run, root, TEST_ID, download=lambda url: (pdf_bytes, url), recogniser=_selftest_recogniser(seen2))
        check("ocr does NOT treat a stale file as up to date: it reads the PDF again", rc == 0 and len(seen2) == 3 and "already read" not in out, out[:200])
        check("and the fresh file is byte-identical to the first one", text_path.read_text(encoding="utf-8") == good)
        seen3: list = []
        rc, _out = _quiet(run, root, TEST_ID, force=True, download=lambda url: (pdf_bytes, url), recogniser=_selftest_recogniser(seen3))
        check("control: --force reads again even when up to date", rc == 0 and len(seen3) == 3)
        text_path.write_text(good.replace("# sha256: ", "# sha: "), encoding="utf-8", newline="\n")
        try:
            read_ocr_text(text_path)
            headerless = ""
        except OcrError as error:
            headerless = str(error)
        check("a file with no hash header can't be used", "sha256" in headerless, headerless)
        text_path.write_text(good, encoding="utf-8", newline="\n")

        # --- F. segmentation trouble is reported, never papered over ----------
        (root / "pipeline" / "overrides" / f"{TEST_ID}.ocr.yaml").write_text(
            "skip_pages: [1]\nexpected_clause_count: 5\ngutter_search: [335, 355]\n", encoding="utf-8", newline="\n")
        rc, out = _quiet(run, root, TEST_ID)
        check("wrong clause count: exit 1 and the count is named", rc == 1 and "found 4 clauses in the OCR text, expected 5" in out, out[-300:])

    # --- G. margins: no gutter, and a page that is mostly 'margin' ------------
    plain = lay_out(["A body line of ordinary words here.", "Another body line of words."], DPI, x_pt=60)
    small = lay_out(["tiny note"], DPI, x_pt=420, size_pt=6.0, block=2)
    page = read_page(plain + small, 1, DPI, PAGE_W, {"gutter_search": [300, 340], "gutter_min": 200})
    check("no gutter wide enough: small type right of the window is the note, body is kept",
          page.lines == ["A body line of ordinary words here.", "Another body line of words."] and "no gutter found" in page.audit[0], str(page.lines) + str(page.audit))
    wide = lay_out(["x " * 12], DPI, x_pt=350, block=2)
    try:
        read_page(plain + wide, 1, DPI, PAGE_W, {"gutter_search": [330, 345], "gutter_min": 6})
        refused = ""
    except OcrError as error:
        refused = str(error)
    check("more than a quarter of a page 'in the margin': refused with the override to set", "looks like body text" in refused and "margin_mode: none" in refused, refused)
    page = read_page(plain + wide, 1, DPI, PAGE_W, {"margin_mode": "none"})
    check("margin_mode: none keeps every word", len(page.lines) == 3 and not page.audit)

    # --- H. the real files were not touched ----------------------------------
    check("the real sources.yaml, OCR overrides and every real bill file are byte-identical (SHA-256)",
          {p: sha256_file(p) for p in watched} == before_real)
    check("no OCR output appeared under the real sources folders",
          sorted(p.name for p in (repo_root / "sources").rglob("*.ocr.*")) == real_ocr_before)
    return check.finish()
