"""The manual first load of every new bill (devpost/spec.md > Intake).

`politricks intake` looks at the registry (`pipeline/sources.yaml`) and, for
every bill that is not loaded yet, runs the whole first-load chain in order:

    fetch  -> download + fingerprint the official PDF(s)
    scan check -> a PDF with no text layer stops here ("scanned PDF")
    extract -> pull the clauses out word for word, with the verbatim check
    generate -> ask Claude for EN/SW/Sheng drafts (only when a key exists)
    lint   -> the doctrine check on whatever was generated

A bill counts as LOADED only when its JSON exists AND holds at least one
clause. A JSON with no clauses is what an earlier failed load leaves behind (fetch
writes the skeleton first), so intake says "resume" and loads it again. A
loaded bill is skipped: re-running a single stage on it is what the individual
commands (`fetch`, `extract`, `generate`) are for, and intake must never
silently redo work a human has already reviewed.

One bad bill never stops the run: each bill's first load is wrapped, a failure
prints `FAIL <id>: <ExceptionType>: <message>`, and the next bill goes ahead.
And nothing half-built is left for the site, which globs every bill JSON: if a
failed load leaves a JSON with no clauses, intake deletes it (the downloaded
files in sources/<id>/ stay so you can look at them).

Why generate can be skipped: with no ANTHROPIC_API_KEY set there is nothing to
call. The chain still runs fetch and extract (the parts that prove a new bill's
PDF is usable) and says plainly that generate was skipped, instead of failing.

`intake --check` (proposing NEW registry entries from the Parliament and
Gazette listings) is slice 9/12's job and is not built yet.

`politricks intake --self-test` runs the chain offline in a temp folder with
the download stubbed, and checks the failure paths (see `self_test`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .models import bill_path, load_bill, validate_bill_id
from .selftest import Checker

NO_KEY_MESSAGE = (
    "generate skipped: no ANTHROPIC_API_KEY. Put it in .env and run "
    "`uv run --env-file .env --project pipeline politricks generate <bill-id>`"
)


def _clause_count(repo_root: Path, bill_id: str) -> Optional[int]:
    """How many clauses the bill's JSON holds, or None if there is no JSON."""
    path = bill_path(repo_root, bill_id)
    if not path.exists():
        return None
    return len(load_bill(path).clauses)


def _remove_if_incomplete(repo_root: Path, bill_id: str) -> None:
    """After a failed load, delete the bill JSON if it has no clauses.

    A JSON with no clauses is a skeleton: either this load created it (fetch
    does, before extract has run) or an earlier failed load left it. Either
    way it is not a bill, and the site builds a page for every JSON it finds.
    A JSON that can't be read at all is left alone: it isn't ours to judge.
    """
    path = bill_path(repo_root, bill_id)
    try:
        count = _clause_count(repo_root, bill_id)
    except (ValueError, OSError):
        return
    if count == 0:
        path.unlink()
        print(
            f"removed incomplete src/data/bills/{bill_id}.json; "
            f"downloaded files left in sources/{bill_id}/ for inspection"
        )
    elif count:
        print(
            f"kept src/data/bills/{bill_id}.json ({count} clause(s) extracted); "
            f"fix the error above, then run `politricks generate {bill_id}`"
        )


def _first_load(repo_root: Path, bill_id: str) -> bool:
    """fetch -> scan check -> extract -> generate for one bill. Raises on any
    failure. Returns True if generate ran, False if it was skipped (no key)."""
    from . import extract, fetch, generate

    bill = fetch.fetch_bill(repo_root, bill_id)
    if not bill.sources:
        raise extract.ExtractError("the registry lists no sources for this bill")

    # The PDF whose clauses we read is the bill's first official source, the
    # same rule `politricks extract` uses. A PDF with no text layer is a scan
    # (images only); we do no OCR, so stop here with the spec's wording rather
    # than let extract fail with a confusing "start marker not found".
    pdf = repo_root / bill.sources[0].file
    with_text, total = fetch.text_layer_report(pdf)
    if with_text == 0:
        raise extract.ExtractError(
            f"scanned PDF — not supported yet ({with_text}/{total} pages have a text layer)"
        )
    extract.run(repo_root, bill_id, pdf.name)

    if os.environ.get("ANTHROPIC_API_KEY"):
        if generate.run(repo_root, bill_id) != 0:
            # The bill now has clauses, so the next intake will skip it. The
            # missing explanations have to be finished by hand, from the batch
            # already paid for.
            raise RuntimeError(
                f"generate left requests unapplied (listed above). Finish with "
                f"`politricks generate {bill_id} --batch-id <id from pipeline/.cache/batches/{bill_id}.batch-id>`"
            )
        return True
    print(f"  {NO_KEY_MESSAGE}")
    return False


def run(repo_root: Path) -> int:
    """Returns the exit code: 0 all good, 1 if any bill failed a stage or lint."""
    from . import fetch, lint

    registry = fetch.load_registry(repo_root) or {}
    if not registry:
        # An empty registry would "succeed" at doing nothing. Say so instead.
        print("FAILED: pipeline/sources.yaml lists no bills")
        return 1

    loaded: list[str] = []  # fully loaded by this run
    no_key: list[str] = []  # of those, the ones whose generate was skipped
    skipped: list[str] = []
    failed: list[str] = []
    for bill_id in registry:
        try:
            validate_bill_id(bill_id)
        except ValueError as error:
            print(f"FAIL {bill_id}: {error}")
            failed.append(bill_id)
            continue

        try:
            clauses = _clause_count(repo_root, bill_id)
            if clauses:
                print(f"skip {bill_id}: already loaded ({clauses} clause(s))")
                skipped.append(bill_id)
                continue
            if clauses == 0:
                print(f"resume {bill_id}: bill JSON has no clauses (an earlier load stopped part-way)")
            print(f"intake {bill_id}")
            generated = _first_load(repo_root, bill_id)
        except (Exception, SystemExit) as error:
            # Exception, not BaseException: Ctrl-C must still stop the run. But
            # fetch reports its refusals ("not on the allowlist", "unknown bill
            # id") as SystemExit, which Exception does not cover; one bad entry
            # must not hide the result for the rest.
            print(f"FAIL {bill_id}: {type(error).__name__}: {error}")
            failed.append(bill_id)
            _remove_if_incomplete(repo_root, bill_id)
            continue
        loaded.append(bill_id)
        if not generated:
            no_key.append(bill_id)

    # Lint every bill this run loaded, even when others failed (and even when
    # generate was skipped: then there is nothing generated to scan, and lint
    # says so).
    for bill_id in loaded:
        try:
            lint_code = lint.run(repo_root, bill_id)
        except Exception as error:
            print(f"FAIL {bill_id}: lint could not run: {type(error).__name__}: {error}")
            lint_code = 1
        if lint_code != 0:
            failed.append(bill_id)

    print(
        f"{len(registry)} bill(s) in the registry: {len(loaded)} loaded "
        f"({len(no_key)} with generate skipped: no ANTHROPIC_API_KEY), "
        f"{len(skipped)} skipped (already loaded), {len(set(failed))} failed"
    )
    return 1 if failed else 0


# --------------------------------------------------------------------------
# Self-test: the whole chain, offline, in a temp folder, download stubbed.
# --------------------------------------------------------------------------

REAL_ID = "public-participation-bill-2025"  # a real, extractable bill: the happy path
SCANNED_ID = "test-scanned-bill"  # downloads fine, but the PDF has no text layer
ERROR_ID = "test-error-bill"  # the download itself raises
REFUSED_ID = "test-refused-bill"  # marked official but off the allowlist: fetch raises SystemExit

SCANNED_URL = "https://new.kenyalaw.org/test/scanned.pdf"
ERROR_URL = "https://new.kenyalaw.org/test/error.pdf"

# Everything a first load does NOT produce. The committed bill may carry these
# (explanations, reviews, a summary) once a human has worked on it, so the
# happy-path comparison resets them on the committed side: the check stays
# "intake reproduces the committed extraction", whatever has been layered on.
_BILL_LATER_LAYERS = {
    "source_changed": False,
    "summary": None,
    "groups": [],
    "stage_next": None,
    "changes": [],
    "participation": None,
    "challenge": None,
}
_CLAUSE_LATER_LAYERS = {
    "explanation": None,
    "review": {"status": "draft", "at": None},
    "neutral_check": None,
    "groups": [],
    "suggested_articles": [],
}


def _blank_pdf() -> bytes:
    """A valid one-page PDF with nothing on the page: no text layer at all.

    Written by hand so the test needs no PDF library: four objects (catalog,
    page tree, one empty page) and a cross-reference table with real byte offsets.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return out


def self_test(repo_root: Path) -> bool:
    import contextlib
    import copy
    import io
    import json
    import shutil
    import tempfile

    import yaml

    from . import fetch
    from .hashing import sha256_file
    from .models import save_bill

    check = Checker()

    def snapshot() -> dict[str, str]:
        """SHA-256 of every real bill JSON and every file under sources/."""
        roots = [repo_root / "src" / "data" / "bills", repo_root / "sources"]
        return {
            path.relative_to(repo_root).as_posix(): sha256_file(path)
            for root in roots
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    real_before = snapshot()
    check("control: the real repo has bill JSONs and source files to protect", len(real_before) > 3, str(len(real_before)))

    real_registry = fetch.load_registry(repo_root)
    real_entry = copy.deepcopy(real_registry[REAL_ID])
    real_url = real_entry["sources"][0]["url"]
    real_pdf = (repo_root / "sources" / REAL_ID / real_entry["sources"][0]["file"]).read_bytes()
    committed = json.loads((repo_root / "src" / "data" / "bills" / f"{REAL_ID}.json").read_text(encoding="utf-8"))

    def test_entry(title: str, url: str, official: bool = True) -> dict:
        return {
            "title": title,
            "chamber": "national",
            "kind": "bill",
            "status": "active",
            "stage": "Self-test fixture",
            "stage_checked_at": "2026-01-01",
            "sources": [{"label": title, "url": url, "official": official, "file": "bill.pdf"}],
        }

    # The error bill goes FIRST on purpose: if its failure aborted the run, the
    # real bill after it would never load.
    registry = {
        ERROR_ID: test_entry("Self-test: download fails", ERROR_URL),
        SCANNED_ID: test_entry("Self-test: scanned PDF", SCANNED_URL),
        REAL_ID: real_entry,
    }

    def comparable(data: dict) -> dict:
        view = copy.deepcopy(data)
        for source in view["sources"]:
            source.pop("fetched_at", None)
        view.update(copy.deepcopy(_BILL_LATER_LAYERS))
        for clause in view["clauses"]:
            clause.update(copy.deepcopy(_CLAUSE_LATER_LAYERS))
        return view

    calls: list[str] = []

    def stub_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        if url == real_url:
            return real_pdf, url
        if url == SCANNED_URL:
            return _blank_pdf(), url
        if url == ERROR_URL:
            raise RuntimeError("simulated network failure")
        raise AssertionError(f"the self-test never expects a download of {url}")

    saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)  # never call the API from a test
    real_download = fetch.download_pdf
    fetch.download_pdf = stub_download
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pipeline" / "overrides").mkdir(parents=True)
            (root / "pipeline" / "prompts").mkdir(parents=True)
            (root / "src" / "data" / "bills").mkdir(parents=True)
            override = f"{REAL_ID}.yaml"
            shutil.copyfile(repo_root / "pipeline" / "overrides" / override, root / "pipeline" / "overrides" / override)
            shutil.copyfile(repo_root / "pipeline" / "prompts" / "banned.txt", root / "pipeline" / "prompts" / "banned.txt")

            def write_registry(entries: dict) -> None:
                text = yaml.safe_dump(entries, sort_keys=False, allow_unicode=True)
                (root / "pipeline" / "sources.yaml").write_text(text, encoding="utf-8")

            def go() -> tuple[Optional[int], str]:
                """intake.run() on the temp root; its stdout captured."""
                buffer = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buffer):
                        code: Optional[int] = run(root)
                except SystemExit as error:  # intake must return an exit code, never raise this
                    code = None
                    buffer.write(f"SystemExit escaped intake.run: {error}\n")
                return code, buffer.getvalue()

            def json_of(bill_id: str) -> Path:
                return root / "src" / "data" / "bills" / f"{bill_id}.json"

            check("the API key is unset for the run", "ANTHROPIC_API_KEY" not in os.environ)
            write_registry(registry)

            # --- run 1: two bills fail, one loads --------------------------------
            code, out = go()
            check("1. exit code is 1 (two bills failed)", code == 1, str(code))
            real_ok = json_of(REAL_ID).exists() and bool(load_bill(json_of(REAL_ID)).clauses)
            check(
                "2. the public-participation bill loaded, with clauses",
                real_ok,
                out[-400:],
            )
            fresh = json.loads(json_of(REAL_ID).read_text(encoding="utf-8")) if json_of(REAL_ID).exists() else {}
            check(
                "2. and, once fetched_at is dropped, equals the committed bill JSON (parsed dicts)",
                bool(fresh) and comparable(fresh) == comparable(committed),
                f"{len(fresh.get('clauses', []))} clause(s) vs {len(committed['clauses'])} committed",
            )
            check(
                "3. the scanned bill failed with the spec's wording",
                f"FAIL {SCANNED_ID}:" in out and "scanned PDF — not supported yet (0/1 pages have a text layer)" in out,
                out[-400:],
            )
            check("3. and no JSON was left for it", not json_of(SCANNED_ID).exists())
            check(
                "3. the cleanup was announced, and the downloaded file was left for inspection",
                f"removed incomplete src/data/bills/{SCANNED_ID}.json; downloaded files left in sources/{SCANNED_ID}/ for inspection" in out
                and (root / "sources" / SCANNED_ID / "bill.pdf").exists(),
            )
            check(
                "4. the download error failed with its type and message",
                f"FAIL {ERROR_ID}: RuntimeError: simulated network failure" in out,
                out[-400:],
            )
            check("4. and no JSON was left for it", not json_of(ERROR_ID).exists())
            check(
                "4. the bill AFTER the failed ones was still processed (the run did not abort)",
                out.find(f"FAIL {ERROR_ID}") != -1 and out.find(f"intake {REAL_ID}") > out.find(f"FAIL {ERROR_ID}"),
            )
            check(
                "4. lint still ran for the loaded bill although others failed",
                f"scanned {REAL_ID}:" in out,
            )
            check("generate was skipped for lack of a key, and said so", NO_KEY_MESSAGE in out)
            check(
                "the closing summary counts the loaded, no-key, skipped and failed bills",
                "3 bill(s) in the registry: 1 loaded (1 with generate skipped: no ANTHROPIC_API_KEY), "
                "0 skipped (already loaded), 2 failed" in out,
                out.strip().splitlines()[-1],
            )

            # --- run 2: the loaded bill is skipped, the failed ones retried -----
            downloads_of_real = calls.count(real_url)
            code, out = go()
            check("5. second run: exit code is still 1", code == 1, str(code))
            check(
                "5. the loaded bill is skipped (and not downloaded again)",
                f"skip {REAL_ID}:" in out and calls.count(real_url) == downloads_of_real == 1,
                f"{calls.count(real_url)} download(s)",
            )
            check(
                "5. both failed bills were retried, not skipped",
                f"intake {ERROR_ID}" in out and f"intake {SCANNED_ID}" in out and "skip test-" not in out,
                out[-400:],
            )

            # --- run 3: a skeleton with no clauses is resumed, not skipped ------
            skeleton = load_bill(json_of(REAL_ID))
            skeleton.clauses = []
            save_bill(skeleton, json_of(REAL_ID))
            check("6. setup: the real bill's JSON is now a 0-clause skeleton", _clause_count(root, REAL_ID) == 0)
            code, out = go()
            check(
                "6. the 0-clause skeleton is reported as 'resume', not 'skip'",
                f"resume {REAL_ID}: bill JSON has no clauses (an earlier load stopped part-way)" in out
                and f"skip {REAL_ID}" not in out,
                out[:300],
            )
            check(
                "6. and it was loaded again: the clauses are back and match the committed bill",
                json_of(REAL_ID).exists()
                and comparable(json.loads(json_of(REAL_ID).read_text(encoding="utf-8"))) == comparable(committed),
            )

            # --- run 4: fetch's SystemExit refusal is a FAIL line, not an abort --
            write_registry({REFUSED_ID: test_entry("Self-test: not on the allowlist", "https://example.com/x.pdf")})
            code, out = go()
            check(
                "6b. an official source off the allowlist (fetch raises SystemExit) fails that bill and intake returns 1",
                code == 1 and f"FAIL {REFUSED_ID}: SystemExit: refused:" in out and not json_of(REFUSED_ID).exists(),
                f"{code}: {out[-300:]}",
            )
    finally:
        fetch.download_pdf = real_download
        if saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_key

    # --- nothing real was written -------------------------------------------
    check(
        "7. every real bill JSON and source file is byte-identical (SHA-256) before and after",
        snapshot() == real_before,
    )

    return check.finish()
