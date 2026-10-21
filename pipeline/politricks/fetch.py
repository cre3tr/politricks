"""Download official documents and fingerprint them.

Three promises this module keeps:
  1. Only official government domains are allowed (the allowlist below), and
     that is re-checked on EVERY redirect, so a link can't quietly bounce us
     to a mirror.
  2. Only real PDFs are saved: a file must start with "%PDF-". An HTML error
     page can never be stored and fingerprinted as if it were the bill.
  3. The fingerprint recorded is SHA-256 of the exact bytes saved to disk —
     the same number `certutil -hashfile <file> SHA256` prints.
"""

from __future__ import annotations

import ssl
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import truststore
import yaml

from .hashing import sha256_bytes
from .models import Bill, Source, bill_path, load_bill, save_bill
from .reviewlock import bill_lock

# Official domains. A host is allowed if it IS one of these or is a subdomain
# of one (e.g. new.kenyalaw.org). The match is on a dot boundary, so
# "kenyalaw.org.evil.com" and "notparliament.go.ke" are both refused.
ALLOWED_DOMAINS = ("parliament.go.ke", "kenyalaw.org")

USER_AGENT = "PoliTricks/0.1 (civic research; https://github.com/cre3tr)"
MAX_REDIRECTS = 5


def is_allowed(url: str) -> bool:
    """True if `url` is https and its host is on the official allowlist."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def _client() -> httpx.Client:
    # truststore uses the Windows/macOS certificate store, which copes better
    # with government sites that serve incomplete certificate chains.
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.Client(
        verify=context,
        headers={"User-Agent": USER_AGENT},
        timeout=60.0,
        follow_redirects=False,  # we follow redirects by hand, checking each hop
    )


def download_pdf(url: str) -> tuple[bytes, str]:
    """Download a PDF from an allowlisted URL. Returns (bytes, final_url).

    Raises ValueError if any hop leaves the allowlist or the body isn't a PDF.
    """
    current = url
    with _client() as client:
        for _ in range(MAX_REDIRECTS + 1):
            if not is_allowed(current):
                raise ValueError(f"refused: {current} is not on the official allowlist")
            response = client.get(current)
            if response.is_redirect:
                # Resolve relative redirects, then loop to re-check the allowlist.
                current = urljoin(current, response.headers["location"])
                continue
            response.raise_for_status()
            body = response.content
            if not body.startswith(b"%PDF-"):
                kind = response.headers.get("content-type", "unknown")
                raise ValueError(f"not a PDF (content-type {kind}): {current}")
            return body, current
    raise ValueError(f"too many redirects starting from {url}")


def text_layer_report(path: Path) -> tuple[int, int]:
    """Count how many pages have extractable characters: (with_text, total).

    A scanned PDF (images only) reports 0 pages with text — PoliTricks does not
    do OCR, so such a file cannot be used.
    """
    import pdfplumber  # imported here so `--check-url` works without it

    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        with_text = sum(1 for page in pdf.pages if len(page.chars) > 0)
    return with_text, total


def load_registry(repo_root: Path) -> dict:
    with open(repo_root / "pipeline" / "sources.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def fetch_bill(repo_root: Path, bill_id: str) -> Bill:
    """Download and fingerprint every source for one bill; update its JSON.

    Holds the bill's lock (reviewlock.py) from load to save: a review walk
    open meanwhile would otherwise overwrite this fetch on its next accept.
    """
    with bill_lock(bill_path(repo_root, bill_id)):
        return _fetch_bill(repo_root, bill_id)


def _fetch_bill(repo_root: Path, bill_id: str) -> Bill:
    registry = load_registry(repo_root)
    if bill_id not in registry:
        raise SystemExit(f"unknown bill id '{bill_id}' — add it to pipeline/sources.yaml")
    entry = registry[bill_id]

    # The registry (sources.yaml) is the authority for a bill's title and
    # where it stands. These fields are refreshed on EVERY fetch, so editing
    # the stage or "last checked" date in sources.yaml always reaches the site.
    registry_fields = dict(
        title=entry["title"],
        chamber=entry["chamber"],
        kind=entry["kind"],
        status=entry["status"],
        stage=entry["stage"],
        stage_checked_at=str(entry["stage_checked_at"]),
    )
    json_path = bill_path(repo_root, bill_id)
    if json_path.exists():
        bill = load_bill(json_path).model_copy(update=registry_fields)
    else:
        # First fetch: create a skeleton bill with no clauses yet.
        bill = Bill(id=bill_id, **registry_fields)

    folder = repo_root / "sources" / bill_id
    folder.mkdir(parents=True, exist_ok=True)
    fetched: list[Source] = []
    for item in entry["sources"]:
        if item.get("official") and not is_allowed(item["url"]):
            raise SystemExit(f"refused: {item['url']} is marked official but not allowlisted")
        body, final_url = download_pdf(item["url"])
        target = folder / item["file"]
        target.write_bytes(body)
        digest = sha256_bytes(body)
        with_text, total = text_layer_report(target)
        print(f"{item['label']}")
        print(f"  url:        {item['url']}")
        if final_url != item["url"]:
            print(f"  redirected: {final_url}")
        print(f"  saved:      {target.relative_to(repo_root).as_posix()} ({len(body):,} bytes)")
        print(f"  sha256:     {digest}")
        print(f"  text layer: {with_text}/{total} pages")
        fetched.append(
            Source(
                label=item["label"],
                url=item["url"],
                official=bool(item.get("official")),
                fetched_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                sha256=digest,
                file=target.relative_to(repo_root).as_posix(),
                scanned=bool(item.get("scanned", False)),
            )
        )

    # The bill's sources become exactly what the registry lists, in its order.
    # Matching is by saved file (not label), so fixing a label in sources.yaml
    # replaces the old entry instead of adding a duplicate. A source removed
    # from the registry disappears from the bill.
    previous_by_file = {s.file: s for s in bill.sources}
    merged: list[Source] = []
    for source in fetched:
        previous = previous_by_file.get(source.file)
        # Keep the original timestamp if the file didn't change, so re-running
        # fetch produces no git diff. Label, URL and scanned flag always come
        # fresh from sources.yaml.
        if previous and previous.sha256 == source.sha256:
            source = source.model_copy(update={"fetched_at": previous.fetched_at})
        merged.append(source)
    bill.sources = merged
    save_bill(bill, json_path)
    return bill
