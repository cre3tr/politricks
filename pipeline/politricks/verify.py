"""Check that stored official files still match what the official site serves.

`politricks verify` re-downloads every official source to a temporary folder
(never overwriting our stored copy) and compares fingerprints:
    unchanged    — same bytes as when we fetched it
    CHANGED      — the official file is different now (marks the bill source_changed)
    unreachable  — couldn't download right now

`politricks verify --self-test` proves the fingerprint check itself works,
offline: an intact copy must report "unchanged" and a copy with ONE byte
altered must report "CHANGED". If the check can't catch a one-byte change, it
isn't evidence of anything.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .fetch import download_pdf
from .hashing import sha256_bytes, sha256_file
from .models import load_bill, save_bill
from .reviewlock import bill_lock


def compare(stored_sha256: str, candidate: Path) -> str:
    return "unchanged" if sha256_file(candidate) == stored_sha256 else "CHANGED"


def self_test(repo_root: Path) -> bool:
    """Run the one-byte positive control against a real stored PDF."""
    pdfs = sorted((repo_root / "sources").glob("*/*.pdf"))
    if not pdfs:
        print("no stored PDFs to test against — run fetch first")
        return False
    original = pdfs[0]
    stored = sha256_file(original)
    with tempfile.TemporaryDirectory() as tmp:
        intact = Path(tmp) / "intact.pdf"
        altered = Path(tmp) / "altered.pdf"
        shutil.copyfile(original, intact)
        shutil.copyfile(original, altered)
        data = bytearray(altered.read_bytes())
        data[len(data) // 2] ^= 0x01  # flip one bit of one byte in the middle
        altered.write_bytes(bytes(data))
        first = compare(stored, intact)
        second = compare(stored, altered)
    print(f"testing with {original.relative_to(repo_root).as_posix()}")
    print(f"intact copy: {first}")
    print(f"one-byte-altered copy: {second}")
    return first == "unchanged" and second == "CHANGED"


def verify_all(repo_root: Path) -> None:
    for json_path in sorted((repo_root / "src" / "data" / "bills").glob("*.json")):
        # Load to save under the bill's lock (reviewlock.py), so an open
        # review walk can't overwrite a source_changed flag on its next accept.
        with bill_lock(json_path):
            bill = load_bill(json_path)
            changed = False
            for source in bill.sources:
                if not source.official:
                    continue
                try:
                    body, _ = download_pdf(source.url)
                    status = "unchanged" if sha256_bytes(body) == source.sha256 else "CHANGED"
                except Exception as error:  # network trouble is reported, not fatal
                    status = f"unreachable ({type(error).__name__})"
                print(f"{bill.id}: {source.label}: {status}")
                changed = changed or status == "CHANGED"
            if changed and not bill.source_changed:
                bill.source_changed = True
                save_bill(bill, json_path)
