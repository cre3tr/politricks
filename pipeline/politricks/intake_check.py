"""`politricks intake --check`: look for bills the registry doesn't know yet.

(devpost/spec.md > Intake and > Scheduled Update.) It reads the official
listing pages, finds PDF links that are not already a `url` in
`pipeline/sources.yaml`, and PROPOSES entries for them as a unified diff on
screen. It never writes `sources.yaml` or any bill file: a human reads the
diff, edits it (the stage is always left as a TODO), and applies it.

The listings today:
    National Assembly bills   parliament.go.ke/the-national-assembly/house-business/bills
    Senate bills              parliament.go.ke/the-senate/house-business/bills
    Nairobi City County bills nairobiassembly.go.ke/bills/
    Kenya Gazette index       new.kenyalaw.org/gazettes/   (403 to every client so far)

Rules this module keeps:
  - An unreachable listing is reported on its own `UNREACHABLE` line, with the
    HTTP status. It is never skipped quietly and never reported as "no new
    items": silence and "nothing new" must not look the same.
  - Only allowlisted (official) https links can become a proposal. Others are
    listed as "skipped, not official" and go no further.
  - "New" means a PDF link whose URL is not already a source in the registry.
    URLs are compared after decoding (%20 and a space are the same URL).
  - A reachable page with no PDF links at all is a PARSE FAILURE (the layout
    probably changed), not an empty listing.

Exit code: 0 when at least one listing was read, even if another was
unreachable. 1 when a reachable page could not be parsed, a redirect left the
allowlist, or NO listing could be read at all (a run that checked nothing must
not look like a clean run). The exception is a listing marked `optional`
(the Gazette): its parse failure is printed, with a NOT CHECKED line, but
doesn't make the exit code 1.

The listing layouts are [UNKNOWN] in the spec, so the parser asks for very
little: every `<a href>` whose path ends in `.pdf`. Parliament's pages also
carry Hansards, order papers and bill digests next to the bills, so a link
only counts as a bill if its text or file name says "bill" and does not look
like one of those other documents. Those are counted, not hidden.
"""

from __future__ import annotations

import difflib
from datetime import date
import json
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from .models import validate_bill_id
from .selftest import Checker


@dataclass(frozen=True)
class Listing:
    name: str
    url: str
    chamber: Optional[str]  # None: the listing doesn't say (the Gazette)
    # A listing known to be unreliable: its PARSE FAILED is still printed and
    # copied to the top of the weekly PR, but it doesn't fail the run.
    optional: bool = False


LISTINGS = (
    Listing(
        "National Assembly bills",
        "https://www.parliament.go.ke/the-national-assembly/house-business/bills",
        "national",
    ),
    Listing(
        "Senate bills",
        "https://www.parliament.go.ke/the-senate/house-business/bills",
        "senate",
    ),
    Listing(
        "Nairobi City County Assembly bills",
        "https://nairobiassembly.go.ke/bills/",
        "county",
    ),
    # Optional (2026-10-26): Kenya Law has answered 403 to every client here
    # since 2026-10-07, and on the first live weekly run it answered GitHub's
    # runner with a 200 page holding no PDF links. One blocked page turned the
    # whole run red while the three Parliament and Nairobi listings read fine.
    Listing("Kenya Gazette index", "https://new.kenyalaw.org/gazettes/", None, optional=True),
)

# What counts as a bill. "bill" as a word, with letters (not digits or "_")
# as the boundary, so "County_AttorneyBill_2016" and "Bill2026" still match
# but "billboard" doesn't.
_BILL_WORD = re.compile(r"(?<![a-z])bills?(?![a-z])", re.IGNORECASE)
# Other documents that sit beside bills and mention them by name.
_NOT_A_BILL = re.compile(
    r"hansard|order paper|digest|tracker|message from|memorand|minutes",
    re.IGNORECASE,
)
# Link texts that say nothing about the document: use the file name instead.
_GENERIC_TEXT = {"", "download", "view", "pdf", "click here", "read more", "submit comments"}

TODO_STAGE = "TODO(human): stage unknown. Check the Bills Tracker, then replace this text"
TODO_CHECKED = "TODO(human): the date you checked the stage"


# --------------------------------------------------------------------------
# Reading a listing page
# --------------------------------------------------------------------------


class _LinkParser(HTMLParser):
    """Every `<a href>` on the page with its visible text (tags inside the link
    are fine: only the text is kept)."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: list[str] = []

    def _flush(self) -> None:
        if self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
        self._href = None
        self._text = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a":
            self._flush()
            self._href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._flush()

    def close(self) -> None:
        super().close()
        self._flush()


@dataclass
class PdfLink:
    url: str
    text: str


def find_pdf_links(html: str, base_url: str) -> list[PdfLink]:
    """The distinct PDF links on a page, in page order, as absolute URLs.

    A link is a PDF when its URL *path* ends in .pdf. That matters on
    Parliament's pages: a "Submit Comments" link carries the file name in its
    query string (`...petition?bill=THE GOLD BILL.pdf`) and is a form, not a
    document.
    """
    parser = _LinkParser()
    parser.feed(html)
    parser.close()
    found: dict[tuple, PdfLink] = {}
    for href, text in parser.links:
        url, _fragment = urldefrag(urljoin(base_url, href.strip()))
        if not urlsplit(url).path.lower().endswith(".pdf"):
            continue
        key = url_key(url)
        if key not in found:
            found[key] = PdfLink(url, text)
        elif found[key].text.lower() in _GENERIC_TEXT and text.lower() not in _GENERIC_TEXT:
            found[key].text = text  # a later link names it better
    return list(found.values())


def url_key(url: str) -> tuple:
    """A URL in the form used to compare two URLs: lower-case scheme and host,
    percent-encoding decoded (so `%20` and a space, `%2C` and `,` agree)."""
    parts = urlsplit(url.strip())
    return (parts.scheme.lower(), (parts.hostname or "").lower(), unquote(parts.path), unquote(parts.query))


def _filename(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


_YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def year_of(link: PdfLink) -> int | None:
    """The newest year named in the link text or URL path ("...Bill, 2026",
    ".../uploads/bill/2018/..."), or None when it names no year."""
    years = [int(match.group(0)) for match in _YEAR.finditer(f"{link.text} {urlsplit(link.url).path}")]
    return max(years) if years else None


def default_min_year() -> int:
    """Last year: a weekly proposer is for NEW bills. The listings go back to
    2013 (Nairobi alone carried 98 PDFs on 2026-10-23), and proposing all of
    them every week buries the one that matters."""
    return date.today().year - 1


def looks_like_a_bill(link: PdfLink) -> bool:
    haystack = f"{link.text} {_filename(link.url)}"
    return bool(_BILL_WORD.search(haystack)) and not _NOT_A_BILL.search(haystack)


def title_of(link: PdfLink) -> str:
    """The link text if it says something, else the file name made readable."""
    text = re.sub(r"\.(pdf|docx?)\s*$", "", link.text.strip(), flags=re.IGNORECASE)
    text = text.strip(" .")
    if text.lower() not in _GENERIC_TEXT:
        return text
    name = re.sub(r"\.pdf$", "", _filename(link.url), flags=re.IGNORECASE)
    if " " not in name:
        name = re.sub(r"[-_]+", " ", name)
    return " ".join(name.split()).strip(" .")


def slug_of(title: str) -> str:
    """A registry id from a title: lower-case letters, digits and hyphens,
    without a leading "the" (the existing ids read `public-participation-bill-2025`)."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    slug = re.sub(r"^the-", "", slug)[:90].strip("-")
    return slug or "untitled-bill"


# --------------------------------------------------------------------------
# One listing -> what it holds
# --------------------------------------------------------------------------


@dataclass
class Proposal:
    listing: Listing
    title: str
    url: str


@dataclass
class ListingResult:
    listing: Listing
    pdf_total: int = 0
    known: int = 0
    duplicates: int = 0  # already proposed from an earlier listing
    too_old: int = 0  # bills naming a year before min_year: counted, not proposed
    not_bills: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)  # PDF links off the allowlist
    proposals: list[Proposal] = field(default_factory=list)


def read_listing(
    listing: Listing, pdfs: list[PdfLink], known: set, proposed: set, min_year: Optional[int] = None
) -> ListingResult:
    """Sort one page's PDF links. `known` is the registry's URLs (as url_keys),
    `proposed` is what earlier listings already proposed (updated here).
    A bill naming only years before `min_year` is counted as too old, never
    proposed; one naming no year at all is still proposed (we can't tell)."""
    from . import fetch

    result = ListingResult(listing, pdf_total=len(pdfs))
    for link in pdfs:
        key = url_key(link.url)
        year = year_of(link)
        if not fetch.is_allowed(link.url):
            result.refused.append(link.url)
        elif key in known:
            result.known += 1
        elif not looks_like_a_bill(link):
            result.not_bills.append(link.text or _filename(link.url))
        elif min_year is not None and year is not None and year < min_year:
            result.too_old += 1
        elif key in proposed:
            result.duplicates += 1
        else:
            proposed.add(key)
            result.proposals.append(Proposal(listing, title_of(link), link.url))
    return result


# --------------------------------------------------------------------------
# Turning proposals into a diff
# --------------------------------------------------------------------------


def _q(value: str) -> str:
    """A YAML double-quoted scalar (a JSON string is one)."""
    return json.dumps(value, ensure_ascii=False)


def entry_block(bill_id: str, proposal: Proposal, note: str = "") -> str:
    chamber = proposal.listing.chamber
    chamber_line = (
        f"  chamber: {chamber}"
        if chamber
        else "  chamber: national   # TODO(human): the Gazette index doesn't say which chamber"
    )
    lines = [
        f"{bill_id}:",
        f"  # Proposed by `intake --check` from the {proposal.listing.name} listing. Unconfirmed:",
        "  # a human checks every line. Fill in the stage, and the date you checked it.",
    ]
    if note:
        lines.append(f"  # {note}")
    lines += [
        f"  title: {_q(proposal.title)}",
        chamber_line,
        "  kind: bill",
        "  status: active   # TODO(human): the listing doesn't say whether it has passed",
        f"  stage: {_q(TODO_STAGE)}",
        f"  stage_checked_at: {_q(TODO_CHECKED)}",
        "  sources:",
        f"    - label: {_q(proposal.title)}",
        f"      url: {_q(proposal.url)}",
        "      official: true",
        "      file: bill.pdf",
    ]
    return "\n".join(lines) + "\n"


def assign_ids(proposals: list[Proposal], registry: dict) -> list[tuple[str, Proposal, str]]:
    """(id, proposal, note) for each proposal. Two bills can slug alike, and a
    slug can already be a registry id (often the same bill under another
    URL): the id then gets `-2`, `-3`, and the entry says why. A proposal
    whose title matches a registry entry's TITLE also gets a note (the
    Tertiary Education Bill is in the registry from Kenya Law and is also on
    Parliament's listing, under a different id and URL)."""
    taken = set(registry)
    title_owner = {slug_of(str(entry.get("title", ""))): bill_id for bill_id, entry in registry.items()}
    out = []
    for proposal in proposals:
        base = slug_of(proposal.title)
        bill_id, n, note = base, 1, ""
        while bill_id in taken:
            n += 1
            bill_id = f"{base}-{n}"
        if n > 1:
            note = (
                f"id '{base}' is already taken (in the registry, or by another proposal): this may be "
                "another copy of that bill. If so, add the URL under that entry's sources instead."
            )
        elif base in title_owner:
            note = (
                f"its title matches the registry entry '{title_owner[base]}': this may be another copy of "
                "that bill. If so, add the URL under that entry's sources instead of as a new bill."
            )
        validate_bill_id(bill_id)
        taken.add(bill_id)
        out.append((bill_id, proposal, note))
    return out


def proposed_diff(current_text: str, blocks: list[str]) -> str:
    """A unified diff that appends `blocks` to sources.yaml. Text only."""
    head = (
        "\n# --- Proposed by `politricks intake --check`. Nothing here has been confirmed. ---\n"
        "# Parliament's own PDFs are often scans with no text layer. After applying an entry,\n"
        "# `politricks fetch <id>` tells you; prefer the Kenya Law copy of a bill when there is one.\n"
    )
    base = current_text if current_text.endswith("\n") else current_text + "\n"
    new_text = base + head + "\n" + "\n".join(blocks)
    diff = difflib.unified_diff(
        base.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="a/pipeline/sources.yaml",
        tofile="b/pipeline/sources.yaml (PROPOSED: not written)",
    )
    return "".join(diff)


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def run(repo_root: Path, listings: tuple = LISTINGS, min_year: Optional[int] = -1) -> int:
    """Read every listing, print what is new. Returns the exit code (see the
    module docstring). Writes nothing. `min_year` -1 means last year
    (`default_min_year`); None proposes every year."""
    if min_year == -1:
        min_year = default_min_year()
    import httpx

    from . import fetch

    registry_path = repo_root / "pipeline" / "sources.yaml"
    registry = fetch.load_registry(repo_root) or {}
    known = {
        url_key(source["url"])
        for entry in registry.values()
        for source in entry.get("sources", [])
        if source.get("url")
    }
    print(f"intake --check: {len(listings)} listing(s); the registry knows {len(known)} source URL(s)")

    results: list[ListingResult] = []
    unreachable: list[str] = []
    errors: list[str] = []
    soft_errors: list[str] = []  # PARSE FAILED on an optional listing
    proposed_keys: set = set()
    for listing in listings:
        label = f"{listing.name} ({listing.url})"
        try:
            status, html, final_url = fetch.fetch_page(listing.url)
        except httpx.HTTPError as error:
            print(f"UNREACHABLE  {label}: network error, {type(error).__name__}: {error}")
            unreachable.append(listing.name)
            continue
        except ValueError as error:
            print(f"ERROR        {label}: {error}")
            errors.append(listing.name)
            continue
        if not 200 <= status < 300:
            print(f"UNREACHABLE  {label}: HTTP {status}")
            unreachable.append(listing.name)
            continue
        pdfs = find_pdf_links(html, final_url)
        if not pdfs:
            print(
                f"PARSE FAILED {label}: HTTP {status} but no PDF links found "
                f"(the page layout may have changed)"
                + (" [optional listing: reported, does not fail the run]" if listing.optional else "")
            )
            (soft_errors if listing.optional else errors).append(listing.name)
            continue
        result = read_listing(listing, pdfs, known, proposed_keys, min_year)
        results.append(result)
        print(
            f"read         {label}: HTTP {status}, {result.pdf_total} distinct PDF link(s): "
            f"{result.known} already in sources.yaml, {len(result.proposals)} proposed, "
            f"{len(result.not_bills)} ignored (not a bill), {len(result.refused)} skipped (not official), "
            f"{result.duplicates} already proposed from an earlier listing"
            + (f", {result.too_old} older than {min_year}" if min_year is not None else "")
        )

    for r in results:
        for url in r.refused:
            print(f"skipped, not official ({r.listing.name}): {url}")
        for text in r.not_bills:
            print(f"ignored, not a bill ({r.listing.name}): {text}")

    proposals = [p for r in results for p in r.proposals]
    if proposals:
        numbered = assign_ids(proposals, registry)
        blocks = [entry_block(bill_id, p, note) for bill_id, p, note in numbered]
        print()
        print(f"PROPOSED additions to pipeline/sources.yaml ({len(blocks)} entr{'y' if len(blocks) == 1 else 'ies'}). Nothing was written:")
        print(proposed_diff(registry_path.read_text(encoding="utf-8"), blocks), end="")
        print()
    elif results:
        print("no new bills proposed from the listings that were read")

    for name in unreachable:
        print(f"NOT CHECKED: {name} was UNREACHABLE, so nothing can be said about it")
    for name in soft_errors:
        print(f"NOT CHECKED: {name} could not be parsed, so nothing can be said about it")
    print(
        f"{len(results)} of {len(listings)} listing(s) read, {len(unreachable)} UNREACHABLE, "
        f"{len(errors) + len(soft_errors)} failed ({len(soft_errors)} optional); "
        f"{len(proposals)} entr{'y' if len(proposals) == 1 else 'ies'} proposed. "
        f"sources.yaml and the bill files were not touched."
    )
    if errors:
        return 1
    if not results:
        print("FAILED: no listing could be read, so nothing was checked")
        return 1
    return 0


# --------------------------------------------------------------------------
# Self-test (called from `politricks intake --self-test`)
# --------------------------------------------------------------------------

NA_URL = "https://www.parliament.go.ke/the-national-assembly/house-business/bills"
SENATE_URL = "https://www.parliament.go.ke/the-senate/house-business/bills"
NAIROBI_URL = "https://nairobiassembly.go.ke/bills/"
GAZETTE_URL = "https://new.kenyalaw.org/gazettes/"

_PARL = "https://www.parliament.go.ke/sites/default/files/2026-10"
KNOWN_URL = f"{_PARL}/THE%20KNOWN%20BILL%2C%202026.pdf"
KNOWN_URL_RAW = f"{_PARL}/THE KNOWN BILL, 2026.pdf"  # the same URL, written without percent-encoding
NEW_NA_PATH = "/sites/default/files/2026-10/THE%20NEW%20NATIONAL%20BILL%2C%202026.pdf"  # relative on purpose
SECOND_COPY_URL = f"{_PARL}/Known-Bill-second-copy.pdf"
NEW_SENATE_URL = f"{_PARL}/The%20New%20Senate%20Bill%2C%202026.pdf"
NEW_NAIROBI_URL = "https://nairobiassembly.go.ke/ncca/wp-content/uploads/bill/2026/The-Nairobi-City-County-Test-Bill-2026.pdf"
EVIL_URLS = (
    "https://evil.example.com/THE-EVIL-BILL-2026.pdf",
    "https://parliament.go.ke.evil.com/THE-LOOKALIKE-BILL-2026.pdf",
)


def _page(*links: tuple[str, str]) -> str:
    body = "".join(f'<tr><td><span class="file"><a href="{href}">{text}</a></span></td></tr>' for href, text in links)
    return f"<html><head><title>Bills</title></head><body><table>{body}</table></body></html>"


def self_test(check: Checker, repo_root: Path) -> None:
    """Checks for `intake --check`, offline: the pages are stubbed, nothing is
    written outside a temp folder, and the real sources.yaml is hashed before
    and after."""
    import contextlib
    import io
    import tempfile

    import httpx
    import yaml

    from . import fetch
    from .hashing import sha256_file

    real_registry = repo_root / "pipeline" / "sources.yaml"
    real_hash = sha256_file(real_registry)

    # --- the allowlist: the Nairobi Assembly is official, lookalikes are not ---
    check("allowlist: nairobiassembly.go.ke is allowed", fetch.is_allowed(NAIROBI_URL))
    check(
        "allowlist: a subdomain of it is allowed too",
        fetch.is_allowed("https://www.nairobiassembly.go.ke/x.pdf"),
    )
    check(
        "allowlist: nairobiassembly.go.ke.evil.com and notnairobiassembly.go.ke are refused (dot boundary)",
        not fetch.is_allowed("https://nairobiassembly.go.ke.evil.com/x.pdf")
        and not fetch.is_allowed("https://notnairobiassembly.go.ke/x.pdf"),
    )
    check("allowlist: plain http is refused", not fetch.is_allowed("http://nairobiassembly.go.ke/bills/"))
    check(
        "allowlist: the two original domains still work",
        fetch.is_allowed("https://new.kenyalaw.org/x") and fetch.is_allowed("https://www.parliament.go.ke/x"),
    )

    # --- parts of the parser ----------------------------------------------
    links = find_pdf_links(
        _page(("/a/Some%20Bill.pdf", "Some Bill"), ("/a/Some Bill.pdf#page=2", "")),
        "https://www.parliament.go.ke/the-national-assembly/house-business/bills",
    )
    check(
        "parser: a relative link is made absolute, and the same URL written two ways is one link",
        len(links) == 1 and links[0].url == "https://www.parliament.go.ke/a/Some%20Bill.pdf" and links[0].text == "Some Bill",
        str(links),
    )
    form = find_pdf_links(
        '<a href="/contact/senate_petition?bill=THE GOLD BILL.pdf">Submit Comments</a>', NA_URL
    )
    check("parser: a Submit Comments form link with .pdf only in its query string is not a PDF", form == [], str(form))
    check(
        "slug: 'THE LOCAL AUTHORITIES (AMENDMENT) BILL,2026' -> local-authorities-amendment-bill-2026",
        slug_of("THE LOCAL AUTHORITIES (AMENDMENT) BILL,2026") == "local-authorities-amendment-bill-2026",
        slug_of("THE LOCAL AUTHORITIES (AMENDMENT) BILL,2026"),
    )

    # --- the stubbed listings -------------------------------------------------
    pages: dict[str, object] = {}  # url -> (status, html) or an exception to raise

    def stub_fetch_page(url: str) -> tuple[int, str, str]:
        answer = pages.get(url)
        if answer is None:
            raise AssertionError(f"the self-test never expects a read of {url}")
        if isinstance(answer, Exception):
            raise answer
        status, html = answer
        return status, html, url

    def reset_pages() -> None:
        pages.clear()
        pages[NA_URL] = (
            200,
            _page(
                (KNOWN_URL_RAW.replace(" ", "%20"), "THE KNOWN BILL, 2026"),
                (NEW_NA_PATH, "THE NEW NATIONAL BILL, 2026"),
                (f"{_PARL}/The%20Hansard%20-%20Thursday%2C%2015%20October%202026.pdf", "The Hansard - Thursday, 15 October 2026"),
                (f"{_PARL}/Digest-new.pdf", "BILL DIGEST: THE NEW NATIONAL BILL, 2026"),
                (EVIL_URLS[0], "THE EVIL BILL, 2026"),
                (EVIL_URLS[1], "THE LOOKALIKE BILL, 2026"),
                ("/contact/senate_petition?bill=THE NEW NATIONAL BILL.pdf", "Submit Comments"),
            ),
        )
        pages[SENATE_URL] = (
            200,
            _page(
                (NEW_SENATE_URL, "The New Senate Bill, 2026"),
                (NEW_NA_PATH, "THE NEW NATIONAL BILL, 2026"),
                (SECOND_COPY_URL, "The Known Bill, 2026"),  # the registry's bill again, at a different URL
            ),
        )
        pages[NAIROBI_URL] = (200, _page((NEW_NAIROBI_URL, "The Nairobi City County Test Bill, 2026")))
        pages[GAZETTE_URL] = (403, "<html><title>403 Forbidden</title></html>")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pipeline").mkdir()
        sources = root / "pipeline" / "sources.yaml"

        def write_registry(known_url: Optional[str]) -> None:
            entries = {}
            if known_url:
                entries["known-bill-2026"] = {
                    "title": "The Known Bill, 2026",
                    "chamber": "national",
                    "kind": "bill",
                    "status": "active",
                    "stage": "Self-test fixture",
                    "stage_checked_at": "2026-01-01",
                    "sources": [{"label": "Known", "url": known_url, "official": True, "file": "bill.pdf"}],
                }
            sources.write_text("# self-test registry\n" + yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")

        def go() -> tuple[Optional[int], str]:
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    code: Optional[int] = run(root)
            except BaseException as error:  # a check run must return a code, never raise
                code = None
                buffer.write(f"{type(error).__name__} escaped intake_check.run: {error}\n")
            return code, buffer.getvalue()

        real_fetch_page = fetch.fetch_page
        fetch.fetch_page = stub_fetch_page
        try:
            # --- the main run: one 403 among four listings ------------------
            write_registry(KNOWN_URL)
            reset_pages()
            registry_before = sha256_file(sources)
            tree_before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
            code, out = go()
            added = [line for line in out.splitlines() if line.startswith("+") and not line.startswith("+++")]
            check("check: exit code is 0 when every reachable listing was read (one is UNREACHABLE)", code == 0, f"{code}: {out[-300:]}")
            check(
                "check: the known bill is NOT proposed, the new National Assembly bill is",
                "THE NEW NATIONAL BILL, 2026" in out and not any("THE KNOWN BILL" in line for line in added),
                out[:600],
            )
            check(
                "check: a bill that is new on the Senate and Nairobi listings is proposed from each",
                "The New Senate Bill, 2026" in out and "The Nairobi City County Test Bill, 2026" in out,
                out[:600],
            )
            check(
                "check: a bill listed on two listings is proposed once (the second is counted as already proposed)",
                sum(1 for line in added if "url:" in line and "THE%20NEW%20NATIONAL" in line) == 1
                and "1 already proposed from an earlier listing" in out,
                out[:900],
            )
            check(
                "check: the 403 listing gets its own UNREACHABLE line with the HTTP status",
                f"UNREACHABLE  Kenya Gazette index ({GAZETTE_URL}): HTTP 403" in out
                and "NOT CHECKED: Kenya Gazette index was UNREACHABLE" in out,
                out[-500:],
            )
            check(
                "check: and the other three listings were still read",
                sum(1 for line in out.splitlines() if line.startswith("read ")) == 3,
                out[:400],
            )
            check(
                "check: the Hansard and the bill digest are counted AND named as ignored, not proposed",
                "2 ignored (not a bill)" in out
                and "ignored, not a bill (National Assembly bills): The Hansard - Thursday, 15 October 2026" in out
                and not any("Hansard" in line or "DIGEST" in line for line in added),
                out[:600],
            )
            check(
                "check: non-allowlisted links (and a parliament.go.ke.evil.com lookalike) are refused and named",
                all(f"skipped, not official (National Assembly bills): {u}" in out for u in EVIL_URLS)
                and not any("evil" in line for line in added),
                out[:900],
            )
            check(
                "check: the Submit Comments form link is not mistaken for a PDF",
                "6 distinct PDF link(s)" in out,
                out[:400],
            )
            check(
                "check: the placeholders are TODOs, not guesses (stage and stage_checked_at)",
                any(TODO_STAGE in line for line in added) and any(TODO_CHECKED in line for line in added),
            )
            # The diff must be valid YAML that adds entries without disturbing the old ones.
            applied = "".join(
                line[1:] for line in out.splitlines(keepends=True) if line.startswith("+") and not line.startswith("+++ ")
            )
            try:
                merged = yaml.safe_load(sources.read_text(encoding="utf-8") + applied)
            except Exception as error:  # noqa: BLE001 - reported below
                merged = {"_error": str(error)}
            new_ids = [k for k in merged if k != "known-bill-2026"]
            check(
                "check: appended to the registry, the diff parses as YAML: 4 new entries with valid ids, official: true, the old entry intact",
                len(new_ids) == 4
                and all(validate_bill_id(i) for i in new_ids)
                and all(merged[i]["sources"][0]["official"] is True for i in new_ids)
                and merged["known-bill-2026"]["sources"][0]["url"] == KNOWN_URL,
                str(list(merged)[:6]),
            )
            check(
                "check: a second URL for a bill already in the registry is still proposed, under a new id with a 'may be another copy' note",
                "known-bill-2026-2" in merged
                and "another copy of that bill" in out
                and merged["known-bill-2026-2"]["sources"][0]["url"] == SECOND_COPY_URL,
                str(list(merged)),
            )
            check("check: sources.yaml (temp copy) is byte-identical after the run (SHA-256)", sha256_file(sources) == registry_before)
            check(
                "check: no file was created anywhere in the temp repo",
                sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == tree_before,
            )

            # --- positive control: the 'already known' comparison is what removes the bill
            write_registry(None)
            code, out = go()
            added = [line for line in out.splitlines() if line.startswith("+") and not line.startswith("+++")]
            check(
                "control: with an empty registry the same known bill IS proposed (so the comparison is what hid it before)",
                any("THE%20KNOWN%20BILL" in line for line in added),
                out[:500],
            )
            write_registry(KNOWN_URL)

            # --- an unreachable listing must never read as 'nothing new' --------
            reset_pages()
            pages[NA_URL] = httpx.ConnectTimeout("simulated timeout")
            code, out = go()
            check(
                "check: a network failure is an UNREACHABLE line too, and the run carries on (exit 0)",
                code == 0 and f"UNREACHABLE  National Assembly bills ({NA_URL}): network error, ConnectTimeout" in out
                and "The New Senate Bill, 2026" in out,
                f"{code}: {out[:500]}",
            )
            reset_pages()
            for url in (NA_URL, SENATE_URL, NAIROBI_URL):
                pages[url] = (503, "unavailable")
            code, out = go()
            check(
                "check: when EVERY listing is unreachable the run fails (exit 1): a run that checked nothing is not clean",
                code == 1 and out.count("UNREACHABLE  ") == 4 and "no new bills proposed" not in out and "FAILED: no listing could be read" in out,
                f"{code}: {out[-400:]}",
            )

            # --- a reachable page we cannot read is a real error ---------------
            reset_pages()
            pages[SENATE_URL] = (200, "<html><body><p>No links here.</p></body></html>")
            code, out = go()
            check(
                "check: a reachable page with no PDF links is a PARSE FAILED line and exit 1, but the other listings are still read",
                code == 1 and f"PARSE FAILED {'Senate bills'} ({SENATE_URL})" in out and "THE NEW NATIONAL BILL, 2026" in out,
                f"{code}: {out[:500]}",
            )
            # The same failure on the OPTIONAL Gazette listing is reported, with
            # a NOT CHECKED line, but leaves the exit code 0. The Senate check
            # above is the control: a required listing still fails the run.
            reset_pages()
            pages[GAZETTE_URL] = (200, "<html><body><p>No links here.</p></body></html>")
            code, out = go()
            check(
                "check: an optional listing (the Gazette) with no PDF links is PARSE FAILED and NOT CHECKED, but exit 0",
                code == 0
                and f"PARSE FAILED Kenya Gazette index ({GAZETTE_URL})" in out
                and "[optional listing: reported, does not fail the run]" in out
                and "NOT CHECKED: Kenya Gazette index could not be parsed" in out
                and "1 failed (1 optional)" in out,
                f"{code}: {out[-500:]}",
            )
            reset_pages()
            pages[NAIROBI_URL] = ValueError("refused: https://example.com/ is not on the official allowlist")
            code, out = go()
            check("check: a redirect that leaves the allowlist is an ERROR line and exit 1", code == 1 and "ERROR        Nairobi" in out, f"{code}: {out[:400]}")
        finally:
            fetch.fetch_page = real_fetch_page

    # --- the year filter (2026-10-23): old bills are counted, not proposed ----
    filter_listing = Listing("Test listing", "https://nairobiassembly.go.ke/bills/", "county")
    links = [
        PdfLink(text="Nairobi City County Tobacco Control Bill", url="https://nairobiassembly.go.ke/ncca/wp-content/uploads/bill/2018/Tobacco-Bill.pdf"),
        PdfLink(text="The Nairobi City County Youth Bill, 2026", url="https://nairobiassembly.go.ke/ncca/wp-content/uploads/bill/2026/Youth-Bill-2026.pdf"),
        PdfLink(text="The Undated Bill", url="https://nairobiassembly.go.ke/ncca/wp-content/uploads/bill/Undated-Bill.pdf"),
    ]
    filtered = read_listing(filter_listing, links, set(), set(), min_year=2025)
    check(
        "year filter: a 2018 bill (year in the URL path) is counted as too old and not proposed; 2026 and undated ones are proposed",
        filtered.too_old == 1 and [p.url.split("/")[-1] for p in filtered.proposals] == ["Youth-Bill-2026.pdf", "Undated-Bill.pdf"],
        f"too_old={filtered.too_old}, proposed={[p.url for p in filtered.proposals]}",
    )
    unfiltered = read_listing(filter_listing, links, set(), set(), min_year=None)
    check(
        "year filter control: with no minimum year the same 2018 bill IS proposed (so the filter, not the parser, hid it)",
        unfiltered.too_old == 0 and len(unfiltered.proposals) == 3,
        f"too_old={unfiltered.too_old}, proposed={len(unfiltered.proposals)}",
    )
    check("year filter: the default minimum is last year", default_min_year() == date.today().year - 1)

    check("check: the REAL pipeline/sources.yaml is byte-identical (SHA-256) after all of the above", sha256_file(real_registry) == real_hash)
