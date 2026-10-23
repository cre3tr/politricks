"""Fingerprints and text normalisation.

A *fingerprint* here is a SHA-256 hash: change a single byte of a file (or a
single character of a clause) and the fingerprint changes completely. That is
what lets anyone check that the text PoliTricks shows really came from the
official PDF.

There is exactly ONE `normalise()` function and every part of the pipeline uses
it. The verbatim check and the clause fingerprint both run on normalised text,
so the text a reader sees and the fingerprint beside it can never disagree.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

# A hyphen (or an invisible "soft hyphen") at the end of a line, between two
# lowercase letters, is almost always a word the PDF broke across two lines:
# "amend-\ned" -> "amended". We deliberately do NOT touch em/en dashes, so
# legal text like "amended—\n(a)" keeps its dash.
_LINE_BREAK_HYPHEN = re.compile(r"([a-z])[-­]\n([a-z])")

# Any run of spaces, tabs or newlines becomes one space.
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Return the canonical form of a piece of legal text.

    Steps, in order:
      1. Unicode NFC, so the same visible character always has the same bytes.
      2. Re-join words that the PDF hyphenated across a line break.
      3. Collapse all whitespace (including line breaks) to single spaces.
    """
    text = unicodedata.normalize("NFC", text)
    text = _LINE_BREAK_HYPHEN.sub(r"\1\2", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def sha256_file(path: Path) -> str:
    """Fingerprint a file's raw bytes (this is what `certutil`/`shasum` compute)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        # Read in chunks so large PDFs don't need to fit in memory at once.
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Fingerprint bytes already in memory (used when downloading)."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Fingerprint a clause: SHA-256 of its normalised text, encoded as UTF-8."""
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


# The first part of every review fingerprint. Change a kind's version only if
# that kind's parts change, and change src/lib/reviewHash.ts with it.
#
# The version is per kind, not global, on purpose (slice 10, 2026-10-23).
# Adding `suggested_articles` changed only a CLAUSE's parts, so only clauses
# move to v2. Summary and group fingerprints (and civic.py's, which shares
# REVIEW_HASH_VERSION) are byte-for-byte what they were, so their reviews and
# AI-draft stamps stay valid and need no migration.
REVIEW_HASH_VERSION = "politricks-review-v1"  # summary, group, civic
CLAUSE_HASH_VERSION = "politricks-review-v2"  # clause: v1 plus sorted suggested_articles


def review_sha256(parts: list[str]) -> str:
    """Fingerprint the content a human reviews (see models.current_sha256).

    Each part is UTF-8 encoded and prefixed with its byte length and a colon,
    then all are joined and hashed. The length prefix means no two different
    lists of parts can produce the same bytes, and it avoids JSON, whose
    escaping differs between Python and JavaScript. The site's twin is
    `src/lib/reviewHash.ts`; `pipeline/tests/fixtures/review_hash_vectors.json`
    proves they agree.
    """
    digest = hashlib.sha256()
    for part in parts:
        data = part.encode("utf-8")
        digest.update(f"{len(data)}:".encode("ascii"))
        digest.update(data)
    return digest.hexdigest()
