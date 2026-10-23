"""Check that stored official files still match what the official site serves.

`politricks verify` re-downloads every official source (to memory; our stored
copy is never overwritten) and compares fingerprints. Each source ends in
exactly ONE of four states:
    unchanged    — same bytes as when we fetched it
    CHANGED      — the official file is different now (marks the bill source_changed)
    UNREACHABLE  — the site answered with an HTTP error (403, 404, 5xx) or the
                   network failed (timeout, DNS, TLS). We learned NOTHING about
                   the file, so it is never "unchanged" and never sets
                   source_changed. The line carries the HTTP status.
    ERROR        — the site answered but what came back can't be compared: not
                   a PDF, a redirect off the allowlist, too many redirects.
                   That is a real problem, not an outage.

Why UNREACHABLE is its own state (2026-10-23): kenyalaw.org has answered 403 to
every client since 2026-10-07, and all three bills' sources live there. A
weekly job (.github/workflows/update.yml) that treated that as a failure would
fail every week and say nothing useful; one that treated it as "unchanged"
would claim a check that never happened. Same rule as `intake --check`
(intake_check.py): silence and "nothing changed" must not look the same.

Exit code: 1 if any source is CHANGED or ERROR. Otherwise 0 — even when some
or all sources are UNREACHABLE — but then the LAST line says how many were not
checked, e.g. "3 source(s) UNREACHABLE, not checked".

`politricks verify --self-test` proves the check itself works, offline and with
the network stubbed. See `self_test` below for what it covers.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import httpx

from .fetch import download_pdf
from .hashing import sha256_bytes, sha256_file
from .models import Source, load_bill, save_bill
from .reviewlock import bill_lock

UNCHANGED = "unchanged"
CHANGED = "CHANGED"
UNREACHABLE = "UNREACHABLE"
ERROR = "ERROR"


def compare(stored_sha256: str, candidate: Path) -> str:
    return UNCHANGED if sha256_file(candidate) == stored_sha256 else CHANGED


def check_source(source: Source) -> tuple[str, str]:
    """Download one source and return (state, detail). Never raises.

    `download_pdf` is looked up at call time, so the self-test can stub it.
    """
    try:
        body, _ = download_pdf(source.url)
    except httpx.HTTPStatusError as error:
        # download_pdf calls raise_for_status(): 403, 404, 5xx all land here.
        return UNREACHABLE, f"HTTP {error.response.status_code}"
    except httpx.HTTPError as error:
        # Timeout, DNS, TLS, connection reset: the request never got an answer.
        return UNREACHABLE, f"network error, {type(error).__name__}: {error}"
    except Exception as error:  # noqa: BLE001 - anything else is reported as a real error
        return ERROR, f"{type(error).__name__}: {error}"
    if sha256_bytes(body) == source.sha256:
        return UNCHANGED, ""
    return CHANGED, f"expected {source.sha256[:12]}..., got {sha256_bytes(body)[:12]}..."


def verify_all(repo_root: Path) -> int:
    """Check every official source of every bill. Returns the exit code."""
    counts = {UNCHANGED: 0, CHANGED: 0, UNREACHABLE: 0, ERROR: 0}
    for json_path in sorted((repo_root / "src" / "data" / "bills").glob("*.json")):
        # Load to save under the bill's lock (reviewlock.py), so an open
        # review walk can't overwrite a source_changed flag on its next accept.
        with bill_lock(json_path):
            bill = load_bill(json_path)
            changed = False
            for source in bill.sources:
                if not source.official:
                    continue
                state, detail = check_source(source)
                counts[state] += 1
                line = f"{bill.id}: {source.label}: {state}"
                if state != UNCHANGED:
                    line += f" ({detail})"
                print(line)
                # Only a real fingerprint mismatch marks a bill. UNREACHABLE
                # and ERROR say nothing about the file.
                changed = changed or state == CHANGED
            if changed and not bill.source_changed:
                bill.source_changed = True
                save_bill(bill, json_path)

    total = sum(counts.values())
    if total == 0:
        print("FAILED: no official sources to check (is src/data/bills empty?)")
        return 1
    print(
        f"{total} source(s): {counts[UNCHANGED]} unchanged, {counts[CHANGED]} CHANGED, "
        f"{counts[UNREACHABLE]} UNREACHABLE, {counts[ERROR]} ERROR"
    )
    if counts[CHANGED]:
        print(f"{counts[CHANGED]} source(s) CHANGED: the bill(s) are marked source_changed")
    if counts[ERROR]:
        print(f"{counts[ERROR]} source(s) ERROR: see the lines above")
    # The UNREACHABLE line stays LAST so a reader (or the weekly PR body) sees
    # at once how much of the job did not happen.
    if counts[UNREACHABLE]:
        print(f"{counts[UNREACHABLE]} source(s) UNREACHABLE, not checked")
    return 1 if counts[CHANGED] or counts[ERROR] else 0


def _pick_bill_pdf(repo_root: Path) -> tuple[Path, Path, Source] | None:
    """Choose the self-test's PDF DELIBERATELY: from a bill JSON's own sources.

    This used to be `sorted(sources/*/*.pdf)[0]`, which silently became
    `sources/constitution/...` (a non-bill PDF) when the Constitution was
    added on 2026-10-23. Here we walk the bill files in order and take the
    first official source whose stored PDF exists and still matches the
    fingerprint recorded in the bill. Returns (bill json, pdf, source).
    """
    for json_path in sorted((repo_root / "src" / "data" / "bills").glob("*.json")):
        bill = load_bill(json_path)
        for source in bill.sources:
            pdf = repo_root / source.file
            if source.official and pdf.is_file() and sha256_file(pdf) == source.sha256:
                return json_path, pdf, source
    return None


def self_test(repo_root: Path) -> bool:
    """Offline controls for `verify`. Nothing real is written: the bill JSON
    is copied to a temp folder, the network is stubbed, and the real file's
    SHA-256 is compared before and after.

    1. The one-byte control: an intact copy of a bill's stored PDF reports
       unchanged, a copy with ONE bit flipped reports CHANGED.
    2. verify_all against a temp copy of that bill, stubbed network:
       intact -> unchanged, exit 0 · 403 -> UNREACHABLE line, exit 0, bill file
       byte-identical · timeout -> UNREACHABLE · altered -> CHANGED, exit 1,
       source_changed set · not a PDF -> ERROR, exit 1 · CHANGED + UNREACHABLE
       together -> exit 1 · everything UNREACHABLE -> exit 0 with the
       "not checked" line last.
    3. The positive control for the whole point of this change: an
       UNREACHABLE source is never counted as unchanged (see the planted-bug
       note on that check).
    """
    import contextlib
    import io

    from . import verify as this_module
    from .selftest import Checker

    check = Checker()
    picked = _pick_bill_pdf(repo_root)
    if picked is None:
        print("no bill source whose stored PDF matches its recorded fingerprint — run fetch first")
        return False
    real_json, original, real_source = picked
    print(
        f"testing with {original.relative_to(repo_root).as_posix()} "
        f"(source of bill {real_json.stem}; chosen from the bill JSON, not from sources/*/*.pdf)"
    )
    real_json_hash = sha256_file(real_json)
    real_pdf_hash = sha256_file(original)
    good_bytes = original.read_bytes()
    stored = real_source.sha256

    # --- 1. the one-byte control ---------------------------------------------
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
    print(f"intact copy: {first}")
    print(f"one-byte-altered copy: {second}")
    check("one-byte control: the intact copy is unchanged", first == UNCHANGED, first)
    check("one-byte control: a copy with one bit flipped is CHANGED", second == CHANGED, second)

    # --- 2. verify_all with the network stubbed ------------------------------
    url_one = real_source.url
    url_two = real_source.url.replace("source.pdf", "second.pdf")
    altered_bytes = bytes(bytearray(good_bytes[:-1]) + bytes([good_bytes[-1] ^ 0x01]))
    request = httpx.Request("GET", url_one)
    calls: list[str] = []
    answers: dict[str, object] = {}  # url -> bytes to return, or an exception to raise

    def stub_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        answer = answers.get(url)
        if answer is None:
            raise AssertionError(f"the self-test never expects a download of {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer, url  # type: ignore[return-value]

    forbidden = httpx.HTTPStatusError(
        "Client error '403 Forbidden'", request=request, response=httpx.Response(403, request=request)
    )
    timeout = httpx.ConnectTimeout("timed out", request=request)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bills = root / "src" / "data" / "bills"
        bills.mkdir(parents=True)
        temp_json = bills / real_json.name
        # Two official sources, so one run can mix outcomes. The second differs
        # only in its URL; its fingerprint is the same as the first's.
        bill = load_bill(real_json)
        bill.source_changed = False
        bill.sources = [real_source, real_source.model_copy(update={"url": url_two, "label": "second source"})]
        save_bill(bill, temp_json)
        pristine = temp_json.read_bytes()

        def go() -> tuple[int, str]:
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    code = verify_all(root)
            except BaseException as error:  # a run must return a code, never raise
                code = -1
                buffer.write(f"{type(error).__name__} escaped verify_all: {error}\n")
            return code, buffer.getvalue()

        def scenario(first: object, second: object) -> tuple[int, str, list[str]]:
            temp_json.write_bytes(pristine)
            answers.clear()
            answers[url_one] = first
            answers[url_two] = second
            calls.clear()
            code, out = go()
            return code, out, list(calls)

        real_download = this_module.download_pdf
        this_module.download_pdf = stub_download
        try:
            # a) everything intact
            code, out, seen = scenario(good_bytes, good_bytes)
            check(
                "all intact: both sources unchanged, exit 0, bill file byte-identical",
                code == 0 and out.count(": unchanged") == 2 and "UNREACHABLE" not in out.replace("0 UNREACHABLE", "")
                and temp_json.read_bytes() == pristine and len(seen) == 2,
                f"{code}: {out}",
            )

            # b) a 403 on both: the live situation on kenyalaw.org
            code, out, seen = scenario(forbidden, forbidden)
            lines = out.splitlines()
            check(
                "403: every source gets its own UNREACHABLE line carrying the HTTP status",
                sum(1 for l in lines if l.endswith(": UNREACHABLE (HTTP 403)")) == 2,
                out,
            )
            check("403: exit code 0 (nothing was found to be wrong)", code == 0, f"{code}")
            check("403: the last line says how many were not checked", lines[-1] == "2 source(s) UNREACHABLE, not checked", lines[-1])
            check("403: the bill file is byte-identical (source_changed not set)", temp_json.read_bytes() == pristine)
            check(
                "403 positive control: the stub really was asked for both URLs (the UNREACHABLE comes from a call, not from skipping)",
                seen == [url_one, url_two],
                str(seen),
            )
            # PLANTED-BUG CONTROL. This is the check that must fail if
            # UNREACHABLE is ever counted as unchanged: in check_source, change
            # the `except httpx.HTTPStatusError` branch to
            # `return UNCHANGED, ""` and this (and the lines above) go red.
            check(
                "403 control: an UNREACHABLE source is never reported or counted as unchanged",
                ": unchanged" not in out and "0 unchanged" in out and "2 UNREACHABLE" in out,
                out,
            )

            # c) a network failure is UNREACHABLE too
            code, out, _ = scenario(timeout, good_bytes)
            check(
                "timeout: UNREACHABLE line with the error name, the other source still checked, exit 0",
                code == 0 and "UNREACHABLE (network error, ConnectTimeout" in out and out.count(": unchanged") == 1
                and out.splitlines()[-1] == "1 source(s) UNREACHABLE, not checked"
                and temp_json.read_bytes() == pristine,
                f"{code}: {out}",
            )

            # d) a changed file
            code, out, _ = scenario(altered_bytes, good_bytes)
            saved = load_bill(temp_json)
            check(
                "changed: CHANGED line, exit 1, bill marked source_changed",
                code == 1 and f": {CHANGED} (" in out and saved.source_changed is True,
                f"{code}: {out}",
            )

            # e) changed + unreachable together: CHANGED wins the exit code
            code, out, _ = scenario(altered_bytes, forbidden)
            saved = load_bill(temp_json)
            check(
                "changed + unreachable: exit 1, source_changed set, the not-checked line still last",
                code == 1 and saved.source_changed is True and out.splitlines()[-1] == "1 source(s) UNREACHABLE, not checked",
                f"{code}: {out}",
            )

            # f) 403 must not flip a bill that is not changed, whatever its prior flag
            code, out, _ = scenario(good_bytes, forbidden)
            check(
                "one intact + one 403: exit 0, source_changed still False",
                code == 0 and load_bill(temp_json).source_changed is False,
                f"{code}: {out}",
            )

            # g) a real error (the site answered, but with something that is not a PDF)
            code, out, _ = scenario(ValueError("not a PDF (content-type text/html)"), good_bytes)
            check(
                "not a PDF: ERROR line, exit 1, bill file byte-identical",
                code == 1 and ": ERROR (ValueError: not a PDF" in out and temp_json.read_bytes() == pristine,
                f"{code}: {out}",
            )

            # h) no bills at all is a failure, not a pass (empty space)
            temp_json.unlink()
            code, out = go()
            check("empty space: no bills to check is exit 1, not a quiet pass", code == 1 and "FAILED: no official sources" in out, f"{code}: {out}")
        finally:
            this_module.download_pdf = real_download

    # --- the real files were never touched -----------------------------------
    check(
        "the real bill JSON and PDF are byte-identical after the run",
        sha256_file(real_json) == real_json_hash and sha256_file(original) == real_pdf_hash,
    )
    return check.finish()
