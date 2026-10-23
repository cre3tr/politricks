"""Load the Constitution of Kenya, 2010: download, fingerprint, split into articles.

Why this file exists: the site's Article Sheet and the "suggested articles"
check (generate.py drops any article number that isn't in constitution.json)
both need the Constitution as data, word for word, with a fingerprint a reader
can re-verify. This is run once (and again only if the official PDF changes).

How it works, in plain terms:

1. DOWNLOAD. The official Parliament copy (parliament.go.ke, on the fetch
   allowlist) goes through fetch.download_pdf, so it can't be a mirror and it
   must really be a PDF. The fingerprint is SHA-256 of the exact bytes saved,
   the same number `certutil -hashfile <file> SHA256` prints.

2. LINES. pdfplumber reads every page into lines. Each line remembers its
   page, left edge, font size and whether it is bold. The running header
   ("Constitution of Kenya, 2010  21") is removed ONLY from the first lines of
   a page, ONLY by explicit patterns, and every removal is logged to
   sources/constitution/*.stripped.txt.

3. REGION. The legal body starts at the line "THE CONSTITUTION OF KENYA" (the
   contents list and the preamble come before it) and stops at "SCHEDULES".
   The Schedules are not articles, so they never get in.

4. ARTICLES. An article starts on an INDENTED line like "23. (1) The High
   Court..." and must be numbered exactly one more than the one before. That
   keeps a wrapped line that happens to begin "10." from starting an article,
   and the text "Article 165" in the middle of a line can never match. The
   bold marginal note printed just above the start is the title. A line
   "CHAPTER FOUR..." sets the chapter for the articles after it; a small
   "PART 2..." line is structure and is left out of every article's text.

5. CHECKS, all against text re-derived from the fingerprinted PDF:
   a. numbering: exactly 264 articles, numbered 1..264, no gap, no repeat;
   b. verbatim: every article's text appears in the PDF's normalised text;
   c. coverage: every line of the legal body is either structure (chapter and
      part headings), a title, or sits in exactly one article, and the
      characters add up exactly, so nothing was dropped or invented. This
      catches a truncated article, which the verbatim check cannot.
   Any failure stops the run (exit 1) and names what failed. Nothing is
   written to constitution.json unless all of them pass.

The stored text is `hashing.normalise()` output, like every clause in the
bills, so displayed text and fingerprint can never disagree. An article's
text starts with its own number, as a clause's does ("12. Section 5 of...").
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pdfplumber
from pydantic import BaseModel, ConfigDict

from .fetch import download_pdf, is_allowed
from .hashing import normalise, sha256_bytes
from .selftest import Checker

# The official Parliament copy (Kenya Law answers 403, so we use this one).
SOURCE_URL = "https://www.parliament.go.ke/sites/default/files/2017-05/The_Constitution_of_Kenya_2010.pdf"
SOURCE_FILE = "The_Constitution_of_Kenya_2010.pdf"
ARTICLE_COUNT = 264

# Where things live, relative to the repo root.
PDF_DIR = Path("sources") / "constitution"
JSON_PATH = Path("src") / "data" / "constitution.json"

# The legal body is between these two lines (each must match a whole line).
BODY_START = re.compile(r"^THE CONSTITUTION OF KENYA$")
BODY_END = re.compile(r"^SCHEDULES$")

# Running headers, as seen in the text layer:
#   "Constitution of Kenya, 2010 21"   "164 Constitution of Kenya, 2010"
#   "Constitution of Kenya, 2010"      "Constitution of Kenya, 2010 [Subsidiary]193"
HEADER_PATTERNS = [
    re.compile(r"^(?:\d{1,3}\s+)?Constitution of Kenya, 2010(?:\s*(?:\[Subsidiary\])?\s*\d{1,3})?$"),
]
EDGE_LINES = 3  # headers are only looked for in the first 3 lines of a page
MAX_STRIPPED_PER_PAGE = 2

# "23. (1) The High Court..." or "5. Kenya consists...": the number, a dot, then
# an opening bracket, quote or capital. Whether it is a real start is decided
# by the number being the expected next one and the line being indented.
ARTICLE_START = re.compile(r"^(\d{1,3})\.\s+(?=[(A-Z“‘\"'])")
# The long dash after the chapter name is part of the printed legal heading, so it stays.
CHAPTER_LINE = re.compile(r"^CHAPTER [A-Z-]+—")
INDENT = 10.0 # an article start sits this many points right of the body edge
HYPHENS = "-" + chr(0xAD)  # a hyphen and a soft hyphen, ignored by the coverage count


class ConstitutionError(Exception):
    """Raised when a check fails. The message says exactly what and where."""


@dataclass
class Line:
    text: str
    page: int
    x0: float
    top: float
    size: float
    bold: bool


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    chapter: str
    title: str
    text: str


class ConstitutionFile(BaseModel):
    """The shape of src/data/constitution.json (see devpost/spec.md, Data Model)."""

    model_config = ConfigDict(extra="forbid")
    sha256: str
    source_url: str
    fetched_at: str
    articles: list[Article]


# --- reading the PDF -------------------------------------------------------


def read_lines(pdf_source, strip_headers: bool = True) -> tuple[list[Line], list[str]]:
    """Every text line of the PDF, header-stripped. Returns (lines, stripped log).

    `pdf_source` is a path or a file-like object. With strip_headers=False the
    running headers are kept (the self-test uses that as its positive control).
    """
    lines: list[Line] = []
    stripped_log: list[str] = []
    pages_with_text = 0
    with pdfplumber.open(pdf_source) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            if not page.chars:
                continue
            pages_with_text += 1
            raw = page.extract_text_lines(strip=True, return_chars=True)
            removed_here = 0
            for index, item in enumerate(raw):
                text = item["text"]
                at_top = index < EDGE_LINES
                if strip_headers and at_top and any(p.match(text) for p in HEADER_PATTERNS):
                    stripped_log.append(f"p{number}: {text}")
                    removed_here += 1
                    continue
                chars = item["chars"]
                size = Counter(round(c["size"], 1) for c in chars).most_common(1)[0][0]
                bold_chars = sum(1 for c in chars if "bold" in c["fontname"].lower())
                lines.append(
                    Line(text=text, page=number, x0=item["x0"], top=item["top"], size=size, bold=bold_chars * 2 > len(chars))
                )
            if removed_here > MAX_STRIPPED_PER_PAGE:
                raise ConstitutionError(
                    f"page {number}: {removed_here} lines stripped (max {MAX_STRIPPED_PER_PAGE}); "
                    "a header pattern is probably matching real text"
                )
    if pages_with_text == 0:
        raise ConstitutionError("scanned PDF, not supported yet: no page has a text layer")
    return lines, stripped_log


def legal_body(lines: list[Line]) -> list[Line]:
    """Cut the lines down to the legal body: after the title, before SCHEDULES."""
    starts = [i for i, line in enumerate(lines) if BODY_START.match(line.text)]
    if not starts:
        raise ConstitutionError(f"start marker not found: a line reading 'THE CONSTITUTION OF KENYA' ({BODY_START.pattern!r})")
    body = lines[starts[0] + 1 :]
    ends = [i for i, line in enumerate(body) if BODY_END.match(line.text)]
    if not ends:
        raise ConstitutionError(f"end marker not found: a line reading 'SCHEDULES' ({BODY_END.pattern!r})")
    return body[: ends[0]]


# --- splitting into articles -----------------------------------------------


def split_articles(region: list[Line]) -> tuple[list[Article], list[str], list[str]]:
    """Tile the legal body into articles. Returns (articles, notes, structure lines).

    Every line is given one job: chapter heading, part heading, marginal note
    (title) or article text. A line with no job is a failure, not a guess.
    """
    sizes = Counter(line.size for line in region)
    if not sizes:
        raise ConstitutionError("the legal body is empty")
    body_size = sizes.most_common(1)[0][0]
    # The body's left edge: the leftmost x that a real share of lines start at.
    # (Not the most common x: indented paragraph starts are more common.)
    starts_at = Counter(round(line.x0) for line in region)
    body_left = min(x for x, n in starts_at.items() if n >= 0.05 * len(region))
    notes: list[str] = []

    # Pass 1: label each line.
    #   chapter / part: structure.   start: first line of an article.
    #   line: anything else (article text, or a title waiting to be claimed).
    kinds: list[str] = []
    expected = 1
    in_chapter = False
    for line in region:
        match = ARTICLE_START.match(line.text)
        indented = line.x0 >= body_left + INDENT
        if line.bold and CHAPTER_LINE.match(line.text):
            kinds.append("chapter")
            in_chapter = True
        elif in_chapter and line.bold and line.text == line.text.upper():
            kinds.append("chapter")  # the second line of a long chapter name
        elif line.size < body_size * 0.9 and line.text == line.text.upper():
            kinds.append("part")
            in_chapter = False
        elif match and int(match.group(1)) == expected and indented:
            kinds.append("start")
            expected += 1
            in_chapter = False
        else:
            if match:
                why = "not indented" if int(match.group(1)) == expected else f"expected {expected}"
                notes.append(f"ignored start on p{line.page} ({why}): {line.text[:60]}")
            kinds.append("line")
            in_chapter = False

    # Pass 2: the bold lines directly above an article start are its title.
    kinds_final = list(kinds)
    for i, kind in enumerate(kinds):
        if kind != "start":
            continue
        j = i - 1
        while j >= 0 and kinds[j] == "line" and region[j].bold:
            kinds_final[j] = "title"
            j -= 1

    # Pass 3: walk the labelled lines and build the articles.
    articles: list[Article] = []
    structure: list[str] = []  # chapter and part lines, kept for the coverage check
    chapter_parts: list[str] = []
    chapter = ""
    title_parts: list[str] = []
    current_lines: list[Line] = []
    current_number: int | None = None
    current_chapter = ""
    current_title = ""
    previous = ""

    def close() -> None:
        nonlocal current_lines, current_number
        if current_number is not None:
            articles.append(
                Article(
                    number=current_number,
                    chapter=current_chapter,
                    title=current_title,
                    text=normalise("\n".join(l.text for l in current_lines)),
                )
            )
        current_lines = []
        current_number = None

    for line, kind in zip(region, kinds_final):
        if kind in ("chapter", "part"):
            structure.append(line.text)
        if kind == "chapter":
            close()
            if previous != "chapter":
                chapter_parts = []
            chapter_parts.append(line.text)
            chapter = normalise(" ".join(chapter_parts))
        elif kind == "part":
            close()
        elif kind == "title":
            close()
            if previous != "title":
                title_parts = []
            title_parts.append(line.text)
        elif kind == "start":
            close()
            current_number = int(ARTICLE_START.match(line.text).group(1))
            current_chapter = chapter
            current_title = normalise(" ".join(title_parts)) if previous == "title" else ""
            current_lines = [line]
        else:  # plain line
            if current_number is None:
                raise ConstitutionError(
                    f"COVERAGE FAIL: p{line.page} line belongs to no article: {line.text[:70]!r}"
                )
            current_lines.append(line)
        previous = kind
    close()
    return articles, notes, structure


# --- the checks ------------------------------------------------------------


def check_numbering(articles: list[Article], expected_count: int = ARTICLE_COUNT) -> None:
    """Exactly `expected_count` articles, numbered 1..N with no gap or repeat."""
    numbers = [a.number for a in articles]
    problems: list[str] = []
    repeats = sorted({n for n in numbers if numbers.count(n) > 1})
    if repeats:
        problems.append(f"repeated: {repeats}")
    missing = [n for n in range(1, expected_count + 1) if n not in set(numbers)]
    if missing:
        shown = ", ".join(str(n) for n in missing[:10]) + (" ..." if len(missing) > 10 else "")
        problems.append(f"missing: {shown}")
    extra = [n for n in numbers if n < 1 or n > expected_count]
    if extra:
        problems.append(f"outside 1..{expected_count}: {extra[:10]}")
    if numbers != sorted(numbers):
        problems.append("out of order")
    if problems or len(articles) != expected_count:
        raise ConstitutionError(
            f"found {len(articles)} articles, expected {expected_count} ({'; '.join(problems) or 'wrong count'})"
        )


def verify_verbatim(articles: list[Article], source_text: str) -> None:
    """Every article's text must be in the PDF's normalised text, word for word."""
    for article in articles:
        if article.text != normalise(article.text):
            raise ConstitutionError(f"NOT NORMALISED Article {article.number}")
        if not article.text:
            raise ConstitutionError(f"EMPTY Article {article.number}")
        if article.text not in source_text:
            raise ConstitutionError(f"VERBATIM FAIL Article {article.number}: text not found in the PDF")


def check_coverage(region: list[Line], articles: list[Article], structure: list[str]) -> None:
    """Nothing lost, nothing invented: the legal body's characters, counted.

    Every non-space, non-hyphen character in the body must appear exactly as
    often in the articles' texts and titles plus the chapter and part lines.
    (Spaces and hyphens are left out of the count because normalise() may join
    a word the PDF broke across two lines.) The verbatim check can't catch a
    truncated article, since a shorter text is still inside the PDF; this does.
    """

    def count(texts: list[str]) -> Counter:
        return Counter(ch for t in texts for ch in t if not ch.isspace() and ch not in HYPHENS)

    body = count([line.text for line in region])
    kept = count([a.text for a in articles] + [a.title for a in articles] + structure)
    if body != kept:
        lost = body - kept
        invented = kept - body
        raise ConstitutionError(
            f"COVERAGE FAIL: the articles do not rebuild the legal body "
            f"(missing {sum(lost.values())} characters, extra {sum(invented.values())})"
        )


def extract(pdf_source, expected_count: int = ARTICLE_COUNT, strip_headers: bool = True) -> tuple[list[Article], dict, list[Line]]:
    """Read the PDF, split it, run every check. Returns (articles, report, all lines)."""
    lines, stripped_log = read_lines(pdf_source, strip_headers=strip_headers)
    region = legal_body(lines)
    articles, notes, structure = split_articles(region)
    check_numbering(articles, expected_count)
    check_coverage(region, articles, structure)
    source_text = normalise("\n".join(line.text for line in lines))
    verify_verbatim(articles, source_text)
    report = {
        "articles": len(articles),
        "chapters": len({a.chapter for a in articles}),
        "untitled": [a.number for a in articles if not a.title],
        "stripped": stripped_log,
        "notes": notes,
    }
    return articles, report, lines


# --- writing ---------------------------------------------------------------


def dumps(data: ConstitutionFile) -> str:
    """The JSON text, deterministic: same input gives byte-identical output."""
    return json.dumps(data.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def save_constitution(data: ConstitutionFile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dumps(data))


def load_constitution(path: Path) -> ConstitutionFile:
    return ConstitutionFile.model_validate_json(path.read_text(encoding="utf-8"))


def run(
    repo_root: Path,
    url: str = SOURCE_URL,
    download: Callable[[str], tuple[bytes, str]] = download_pdf,
    expected_count: int = ARTICLE_COUNT,
) -> int:
    """Download, fingerprint, extract, check, write, read back. Returns an exit code."""
    try:
        return _run(repo_root, url, download, expected_count)
    except ConstitutionError as error:
        print(f"FAILED: {error}")
        return 1


def _run(repo_root: Path, url: str, download: Callable[[str], tuple[bytes, str]], expected_count: int) -> int:
    if not is_allowed(url):
        raise ConstitutionError(f"refused: {url} is not on the official allowlist")
    body, final_url = download(url)
    digest = sha256_bytes(body)

    folder = repo_root / PDF_DIR
    folder.mkdir(parents=True, exist_ok=True)
    pdf_path = folder / SOURCE_FILE
    pdf_path.write_bytes(body)
    print(f"url:        {url}")
    if final_url != url:
        print(f"redirected: {final_url}")
    print(f"saved:      {pdf_path.relative_to(repo_root).as_posix()} ({len(body):,} bytes)")
    print(f"sha256:     {digest}")

    # The checks read the saved file, the same bytes the fingerprint covers.
    articles, report, lines = extract(pdf_path, expected_count)

    json_path = repo_root / JSON_PATH
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if json_path.exists():
        previous = load_constitution(json_path)
        if previous.sha256 == digest:
            # Same file as last time: keep the old timestamp so re-running gives no git diff.
            fetched_at = previous.fetched_at
        else:
            print(f"NOTE: the official PDF changed since the last run (was {previous.sha256})")
    data = ConstitutionFile(sha256=digest, source_url=url, fetched_at=fetched_at, articles=articles)
    save_constitution(data, json_path)
    (folder / "constitution.body.txt").write_text(
        "\n".join(f"p{l.page}: {l.text}" for l in lines) + "\n", encoding="utf-8", newline="\n"
    )
    (folder / "constitution.stripped.txt").write_text(
        "\n".join(report["stripped"]) + "\n", encoding="utf-8", newline="\n"
    )

    # Read the file back and check what actually landed, not what we meant to write.
    stored = load_constitution(json_path)
    check_numbering(stored.articles, expected_count)
    verify_verbatim(stored.articles, normalise("\n".join(line.text for line in lines)))
    if json_path.read_text(encoding="utf-8") != dumps(data):
        raise ConstitutionError("read-back mismatch: the file on disk is not what was written")

    print(f"articles:   {report['articles']} in {report['chapters']} chapters (numbered 1..{expected_count}, verbatim and coverage checks passed)")
    print(f"headers/footers stripped: {len(report['stripped'])} (logged to {PDF_DIR.as_posix()}/constitution.stripped.txt)")
    if report["untitled"]:
        print(f"no title found for: {report['untitled']}")
    for note in report["notes"]:
        print(f"  {note}")
    print(f"wrote:      {JSON_PATH.as_posix()}")
    return 0


# --- self-test -------------------------------------------------------------


def _tiny_pdf(pages: list[list[tuple[float, float, float, bool, str]]]) -> bytes:
    """Build a small real PDF for the self-test, by hand (no PDF library needed).

    Each page is a list of (x, top, font size, bold, text). The text layer it
    produces goes through the same pdfplumber reading as the real file.
    """
    objects: list[bytes] = []
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for index, items in enumerate(pages):
        content_number = 6 + 2 * index
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 492 703] "
                "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                f"/Contents {content_number} 0 R >>"
            ).encode()
        )
        parts = []
        for x, top, size, bold, text in items:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            parts.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x} {703 - top} Td ({safe}) Tj ET")
        stream = "\n".join(parts).encode("cp1252")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _fixture_pdf() -> bytes:
    """A 3-article mini Constitution that has every trap the real one has."""
    L, I = 90.0, 114.0  # body edge and article-start indent
    cover = [(150, 200, 12, True, "THE CONSTITUTION OF KENYA, 2010")]
    contents = [
        (150, 40, 10, False, "Constitution of Kenya, 2010"),
        (90, 100, 10, False, "ARRANGEMENT OF ARTICLES"),
        (90, 130, 10, False, "1. Sovereignty of the people (contents line, before the body)"),
    ]
    page3 = [
        (150, 40, 10, False, "Constitution of Kenya, 2010 3"),
        (150, 80, 11, True, "THE CONSTITUTION OF KENYA"),
        (110, 110, 10, True, "CHAPTER ONE—SOVEREIGNTY OF THE PEOPLE AND"),
        (130, 122, 10, True, "SUPREMACY OF THIS CONSTITUTION"),
        (L, 150, 10, True, "Sovereignty of the people."),
        (I, 166, 10, False, "1. (1) All sovereign power belongs to the people and shall be"),
        (L, 178, 10, False, "exercised only under this Constitution, see Article"),
        (L, 190, 10, False, "2. A wrapped line that merely begins with a number."),
        (I, 214, 10, False, "(2) The people may exercise power directly."),
        (L, 250, 10, True, "Supremacy of this"),
        (L, 262, 10, True, "Constitution."),
        (I, 278, 10, False, "2. (1) This Constitution is the supreme law of the Republic and"),
        (L, 290, 10, False, "binds all persons; no person may claim State authority"),
    ]
    page4 = [
        (36, 40, 10, False, "4 Constitution of Kenya, 2010"),
        (I, 80, 10, False, "(2) except as authorised under this Constitution, as in Article"),
        (L, 92, 10, False, "165, to hear and determine the matter shall hold."),
        (150, 130, 8, False, "PART 1—GENERAL PROVISIONS"),
        (L, 160, 10, True, "Defence of this Constitution."),
        (I, 176, 10, False, "3. Every person has an obligation to defend this Constitution."),
        (200, 230, 10, True, "SCHEDULES"),
        (190, 250, 10, True, "FIRST SCHEDULE"),
        (I, 280, 10, False, "1. Mombasa"),
        (I, 292, 10, False, "2. Kwale"),
    ]
    return _tiny_pdf([cover, contents, page3, page4])


def self_test(repo_root: Path) -> bool:
    """Offline check of the loader. Writes nothing to the repo (a temp folder only)."""
    check = Checker()
    real_json = repo_root / JSON_PATH
    before = real_json.read_bytes() if real_json.exists() else None
    pdf_bytes = _fixture_pdf()

    # 1. Reading and splitting the fixture.
    articles, report, lines = extract(io.BytesIO(pdf_bytes), expected_count=3)
    by_number = {a.number: a for a in articles}
    check("the fixture splits into articles 1, 2, 3", [a.number for a in articles] == [1, 2, 3])
    check(
        "the bold note above an article is its title, two lines joined",
        by_number[1].title == "Sovereignty of the people." and by_number[2].title == "Supremacy of this Constitution.",
        f"{by_number[1].title!r} / {by_number[2].title!r}",
    )
    check("an article with a title in the margin keeps it (Article 3)", by_number[3].title == "Defence of this Constitution.")
    check(
        "the chapter name (two lines) is attached to every article after it",
        all(a.chapter == "CHAPTER ONE—SOVEREIGNTY OF THE PEOPLE AND SUPREMACY OF THIS CONSTITUTION" for a in articles),
        by_number[1].chapter,
    )
    check("an article keeps its own number at the start", by_number[3].text.startswith("3. Every person"))

    # 2. Traps.
    check(
        "a wrapped line starting '2.' (not indented) does not start Article 2",
        "2. A wrapped line that merely begins with a number." in by_number[1].text and len(articles) == 3,
    )
    check(
        "'Article' then a line starting '165,' stays inside the article",
        "165, to hear and determine the matter shall hold." in by_number[2].text,
    )
    check(
        "an article that runs over a page break is one article",
        "binds all persons" in by_number[2].text and "(2) except as authorised" in by_number[2].text,
    )
    check(
        "the small PART line is structure, left out of every article",
        all("PART 1" not in a.text for a in articles),
    )
    check(
        "the Schedules are left out (their '1. Mombasa' never becomes an article)",
        all("Mombasa" not in a.text for a in articles) and len(articles) == 3,
    )
    check("the contents-list line before the body is left out", all("contents line" not in a.text for a in articles))

    # 3. Headers: gone from the text, and the control proves they were really there.
    check("all three running header styles were removed", len(report["stripped"]) == 3, str(report["stripped"]))
    check(
        "no article contains the running header",
        all("Constitution of Kenya, 2010" not in a.text for a in articles),
    )
    unstripped, _ = read_lines(io.BytesIO(pdf_bytes), strip_headers=False)
    check(
        "positive control: with header stripping off, the header IS in the text",
        any("Constitution of Kenya, 2010" in line.text and line.page >= 3 for line in unstripped),
    )

    # 4. The checks must be able to fail.
    try:
        check_numbering(articles, 264)
        failed = ""
    except ConstitutionError as error:
        failed = str(error)
    check("the 264 count check rejects a 3-article result", "found 3 articles, expected 264" in failed, failed)

    gappy = [by_number[1], by_number[2], by_number[3].model_copy(update={"number": 4})]
    try:
        check_numbering(gappy, 4)
        failed = ""
    except ConstitutionError as error:
        failed = str(error)
    check("a gap (1, 2, 4) fails and names Article 3", "missing: 3" in failed, failed)

    repeated = [by_number[1], by_number[2], by_number[2]]
    try:
        check_numbering(repeated, 3)
        failed = ""
    except ConstitutionError as error:
        failed = str(error)
    check("a repeat (1, 2, 2) fails and names Article 2", "repeated: [2]" in failed, failed)

    source_text = normalise("\n".join(line.text for line in lines))
    verify_verbatim(articles, source_text)  # raises if the intact articles don't pass
    check("the verbatim check passes on the intact articles", True)
    changed = by_number[2].model_copy(update={"text": by_number[2].text.replace("supreme", "highest")})
    try:
        verify_verbatim([by_number[1], changed, by_number[3]], source_text)
        failed = ""
    except ConstitutionError as error:
        failed = str(error)
    check("a one-word change fails the verbatim check and names Article 2", "VERBATIM FAIL Article 2" in failed, failed)

    # A truncated article is still "in the PDF", so only the coverage check can see it.
    region = legal_body(lines)
    _, _, structure = split_articles(region)
    check_coverage(region, articles, structure)  # raises if the intact articles don't pass
    check("the coverage check passes on the intact articles", True)
    short = by_number[2].model_copy(update={"text": by_number[2].text.rsplit(" ", 1)[0]})
    verify_verbatim([by_number[1], short, by_number[3]], source_text)  # still passes: that is the point
    try:
        check_coverage(region, [by_number[1], short, by_number[3]], structure)
        failed = ""
    except ConstitutionError as error:
        failed = str(error)
    check(
        "an article missing its last word passes the verbatim check but fails coverage",
        "COVERAGE FAIL" in failed and "missing" in failed,
        failed,
    )

    # 5. A PDF with no text layer is refused, not read as empty.
    blank = _tiny_pdf([[]])
    try:
        read_lines(io.BytesIO(blank))
        failed = ""
    except ConstitutionError as error:
        failed = str(error)
    check("a PDF with no text layer is refused as scanned", "scanned PDF" in failed, failed)

    # 6. The whole run, offline, in a temp folder: allowlist, write, read back, determinism.
    def fake_download(url: str) -> tuple[bytes, str]:
        return pdf_bytes, url

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with contextlib.redirect_stdout(io.StringIO()) as said:  # expected refusals: only shown if a check fails
            refused = run(root, url="https://example.com/constitution.pdf", download=fake_download, expected_count=3)
        check(
            "a URL off the official allowlist is refused (exit 1)",
            refused == 1 and not (root / JSON_PATH).exists(),
            said.getvalue(),
        )

        print("--- a full run on the fixture (output below is the run itself) ---")
        code = run(root, download=fake_download, expected_count=3)
        print("--- end of the fixture run ---")
        check("a full run on the fixture exits 0", code == 0)
        written = (root / JSON_PATH).read_bytes()
        stored = load_constitution(root / JSON_PATH)
        check("the JSON records the PDF's own SHA-256", stored.sha256 == sha256_bytes(pdf_bytes), stored.sha256)
        check("the saved PDF is byte-for-byte what was downloaded", (root / PDF_DIR / SOURCE_FILE).read_bytes() == pdf_bytes)
        check("the JSON has the shape the spec names", list(json.loads(written)) == ["sha256", "source_url", "fetched_at", "articles"])
        with contextlib.redirect_stdout(io.StringIO()):  # the second run's output is the same as the first
            run(root, download=fake_download, expected_count=3)
        check("running again gives byte-identical JSON", (root / JSON_PATH).read_bytes() == written)
        with contextlib.redirect_stdout(io.StringIO()) as said:
            code = run(root, download=fake_download, expected_count=264)
        check(
            "the same fixture run with the real count (264) fails, exit 1, and leaves the JSON alone",
            code == 1 and (root / JSON_PATH).read_bytes() == written and "found 3 articles, expected 264" in said.getvalue(),
            said.getvalue(),
        )

    after = real_json.read_bytes() if real_json.exists() else None
    check("the repo's own constitution.json was not touched", before == after)
    return check.finish()

