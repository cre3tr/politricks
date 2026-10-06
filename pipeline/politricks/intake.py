"""The manual first load of every new bill (devpost/spec.md > Intake).

`politricks intake` looks at the registry (`pipeline/sources.yaml`) and, for
every bill that has no `src/data/bills/<bill-id>.json` yet, runs the whole
first-load chain in order:

    fetch  -> download + fingerprint the official PDF(s)
    extract -> pull the clauses out word for word, with the verbatim check
    generate -> ask Claude for EN/SW/Sheng drafts (only when a key exists)
    lint   -> the doctrine check on whatever was generated

A bill that already has a JSON is skipped: re-running a single stage on it is
what the individual commands (`fetch`, `extract`, `generate`) are for, and
intake must never silently redo work a human has already reviewed.

Why generate can be skipped: until slice 2b there is no ANTHROPIC_API_KEY. The
chain still runs fetch and extract (the parts that prove a new bill's PDF is
usable) and says plainly that generate was skipped, instead of failing.

`intake --check` (proposing NEW registry entries from the Parliament and
Gazette listings) is slice 9/12's job and is not built yet.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .models import bill_path, validate_bill_id

NO_KEY_MESSAGE = (
    "generate skipped: no ANTHROPIC_API_KEY (slice 2b). Put it in .env and run "
    "`uv run --env-file .env --project pipeline politricks generate <bill-id>`"
)


def _first_load(repo_root: Path, bill_id: str) -> None:
    """fetch -> extract -> generate for one new bill. Raises on any failure."""
    from . import extract, fetch, generate

    bill = fetch.fetch_bill(repo_root, bill_id)
    # The PDF whose clauses we read is the bill's first official source, the
    # same rule `politricks extract` uses.
    pdf_file = Path(bill.sources[0].file).name
    extract.run(repo_root, bill_id, pdf_file)

    if os.environ.get("ANTHROPIC_API_KEY"):
        generate.run(repo_root, bill_id)
    else:
        print(f"  {NO_KEY_MESSAGE}")


def run(repo_root: Path) -> int:
    """Returns the exit code: 0 all good, 1 if any new bill failed a stage or lint."""
    from . import extract, fetch, lint

    registry = fetch.load_registry(repo_root) or {}
    if not registry:
        # An empty registry would "succeed" at doing nothing. Say so instead.
        print("FAILED: pipeline/sources.yaml lists no bills")
        return 1

    new_ids: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for bill_id in registry:
        try:
            validate_bill_id(bill_id)
        except ValueError as error:
            print(f"FAIL {bill_id}: {error}")
            failed.append(bill_id)
            continue
        if bill_path(repo_root, bill_id).exists():
            print(f"skip {bill_id}: already has a bill JSON")
            skipped.append(bill_id)
            continue

        print(f"intake {bill_id}")
        try:
            _first_load(repo_root, bill_id)
        except (SystemExit, extract.ExtractError, httpx.HTTPError, OSError, ValueError) as error:
            # fetch reports its own refusals as SystemExit("..."); catch them
            # here so one bad bill doesn't hide the result for the rest.
            print(f"FAIL {bill_id}: {error}")
            failed.append(bill_id)
            continue
        new_ids.append(bill_id)

    # Lint every bill this run created, even if generate was skipped (then
    # there is simply nothing generated to scan yet, and lint says so).
    for bill_id in new_ids:
        if lint.run(repo_root, bill_id) != 0:
            failed.append(bill_id)

    print(
        f"{len(registry)} bill(s) in the registry: {len(new_ids)} loaded, "
        f"{len(skipped)} skipped (already loaded), {len(set(failed))} failed"
    )
    return 1 if failed else 0
