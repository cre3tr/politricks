"""The `politricks` command-line tool.

Run from anywhere; paths are resolved from the repo root, not your current folder.

    uv run --project pipeline politricks intake [--self-test]
    uv run --project pipeline politricks fetch finance-bill-2026
    uv run --project pipeline politricks fetch --check-url <url>
    uv run --project pipeline politricks probe finance-act-2026
    uv run --project pipeline politricks extract finance-act-2026 [--check-json <path>]
    uv run --project pipeline politricks verify [--self-test]
    uv run --project pipeline politricks samples finance-act-2026
    uv run --project pipeline politricks generate finance-act-2026 [--self-test]
    uv run --project pipeline politricks lint [finance-act-2026] [--self-test]
    uv run --project pipeline politricks review finance-act-2026 [--self-test]
    uv run --project pipeline politricks review finance-act-2026 --from-sheet exports/finance-act-2026-review.xlsx
    uv run --project pipeline politricks export finance-act-2026 [--self-test]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# pipeline/politricks/cli.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    # Windows consoles default to an old code page that crashes on "—" and
    # curly quotes, which Kenyan legal text is full of. Force UTF-8 output.
    sys.stdout.reconfigure(encoding="utf-8")

    # fetch, extract, samples and verify take the bill's lock (reviewlock.py)
    # but have no exit-code path of their own: a refusal ends here, plainly.
    from .reviewlock import BillLocked

    try:
        _dispatch()
    except BillLocked as error:
        print(f"FAILED: {error} Nothing was changed.")
        sys.exit(1)


def _dispatch() -> None:

    parser = argparse.ArgumentParser(prog="politricks")
    sub = parser.add_subparsers(dest="command", required=True)

    p_intake = sub.add_parser(
        "intake", help="first load of every new bill in sources.yaml: fetch, extract, generate, lint"
    )
    p_intake.add_argument(
        "--self-test", action="store_true", help="offline run in a temp folder with the download stubbed (writes nothing real)"
    )

    p_fetch = sub.add_parser("fetch", help="download + fingerprint a bill's official sources")
    p_fetch.add_argument("bill_id", nargs="?")
    p_fetch.add_argument("--check-url", help="only say whether a URL is allowlisted (no download)")

    p_probe = sub.add_parser("probe", help="print a PDF's layout (font sizes, margins, headers)")
    p_probe.add_argument("bill_id")
    p_probe.add_argument("--pages", help="comma-separated page numbers, e.g. 2,3,20")

    p_extract = sub.add_parser("extract", help="pull sections out word for word, with checks")
    p_extract.add_argument("bill_id")
    p_extract.add_argument("--check-json", help="only check an existing bill JSON against the PDF")

    p_verify = sub.add_parser("verify", help="re-download sources and compare fingerprints")
    p_verify.add_argument("--self-test", action="store_true", help="offline one-byte positive control")

    p_samples = sub.add_parser("samples", help="apply hand-written sample explanations (slice 2a)")
    p_samples.add_argument("bill_id")

    p_generate = sub.add_parser("generate", help="ask Claude to explain every clause (Batches API)")
    p_generate.add_argument("bill_id", nargs="?")
    p_generate.add_argument(
        "--self-test", action="store_true", help="offline check of request-building and result-parsing (no network)"
    )
    p_generate.add_argument(
        "--batch-id", help="resume a batch already submitted (its results are kept 29 days); submits nothing new"
    )
    p_generate.add_argument(
        "--summary-only", action="store_true", help="redo only the bill summary, from the clause tags already saved"
    )
    p_generate.add_argument(
        "--tags-only", action="store_true",
        help="re-decide only each clause's groups (explanations untouched), then redo the summary",
    )

    p_lint = sub.add_parser("lint", help="fail on banned phrases and neutrality flags in every generated text")
    p_lint.add_argument("bill_id", nargs="?", help="one bill (default: every bill in src/data/bills)")
    p_lint.add_argument("--self-test", action="store_true", help="offline check of the linter itself (writes nothing)")

    p_review = sub.add_parser("review", help="walk every draft text: accept, edit or skip (the human gate)")
    p_review.add_argument("bill_id", nargs="?")
    p_review.add_argument("--self-test", action="store_true", help="offline scripted walk (writes nothing)")
    p_review.add_argument(
        "--from-sheet", help="apply a filled-in export sheet: its Accept rows are your accepts (asks before saving)"
    )
    p_review.add_argument("--sheet-self-test", action="store_true", help="offline check of --from-sheet (writes nothing)")

    p_export = sub.add_parser("export", help="write the bill's texts to an Excel sheet for review (reads only)")
    p_export.add_argument("bill_id", nargs="?")
    p_export.add_argument("--out", help="where to write the .xlsx (default: exports/<bill-id>-review.xlsx)")
    p_export.add_argument("--self-test", action="store_true", help="offline round-trip in a temp folder (writes nothing real)")

    args = parser.parse_args()

    if args.command == "intake":
        from . import intake

        if args.self_test:
            sys.exit(0 if intake.self_test(REPO_ROOT) else 1)
        sys.exit(intake.run(REPO_ROOT))

    elif args.command == "fetch":
        from . import fetch

        if args.check_url:
            print("allowed" if fetch.is_allowed(args.check_url) else "refused")
            return
        if not args.bill_id:
            parser.error("fetch needs a bill id (or --check-url)")
        fetch.fetch_bill(REPO_ROOT, args.bill_id)

    elif args.command in ("probe", "extract"):
        # The PDF whose sections we read is the bill's first official source.
        from .models import bill_path, load_bill

        bill = load_bill(bill_path(REPO_ROOT, args.bill_id))
        pdf_file = Path(bill.sources[0].file).name
        if args.command == "probe":
            from .probe import probe

            pages = [int(p) for p in args.pages.split(",")] if args.pages else None
            probe(REPO_ROOT / "sources" / args.bill_id / pdf_file, pages)
        else:
            from . import extract

            try:
                if args.check_json:
                    extract.check_json(REPO_ROOT, args.bill_id, pdf_file, Path(args.check_json).resolve())
                else:
                    extract.run(REPO_ROOT, args.bill_id, pdf_file)
            except extract.ExtractError as error:
                print(f"FAILED: {error}")
                sys.exit(1)

    elif args.command == "verify":
        from . import verify

        if args.self_test:
            sys.exit(0 if verify.self_test(REPO_ROOT) else 1)
        verify.verify_all(REPO_ROOT)

    elif args.command == "samples":
        from . import samples

        try:
            samples.run(REPO_ROOT, args.bill_id)
        except samples.SamplesError as error:
            print(f"FAILED: {error}")
            sys.exit(1)

    elif args.command == "generate":
        from . import generate

        if args.self_test:
            sys.exit(0 if generate.self_test(REPO_ROOT) else 1)
        if not args.bill_id:
            parser.error("generate needs a bill id (or --self-test)")
        # Exit 1 if any request wasn't applied; run() lists each one and why.
        sys.exit(
            generate.run(
                REPO_ROOT, args.bill_id, batch_id=args.batch_id,
                summary_only=args.summary_only, tags_only=args.tags_only,
            )
        )

    elif args.command == "lint":
        from . import lint

        if args.self_test:
            sys.exit(0 if lint.self_test(REPO_ROOT) else 1)
        sys.exit(lint.run(REPO_ROOT, args.bill_id))

    elif args.command == "review":
        from . import review

        if args.self_test:
            sys.exit(0 if review.self_test(REPO_ROOT) else 1)
        if args.sheet_self_test:
            from . import review_sheet

            sys.exit(0 if review_sheet.self_test(REPO_ROOT) else 1)
        if not args.bill_id:
            parser.error("review needs a bill id (or --self-test)")
        if args.from_sheet:
            from . import review_sheet

            sys.exit(review_sheet.run(REPO_ROOT, args.bill_id, Path(args.from_sheet).resolve()))
        sys.exit(review.run(REPO_ROOT, args.bill_id))

    elif args.command == "export":
        from . import export

        if args.self_test:
            sys.exit(0 if export.self_test(REPO_ROOT) else 1)
        if not args.bill_id:
            parser.error("export needs a bill id (or --self-test)")
        sys.exit(export.run(REPO_ROOT, args.bill_id, Path(args.out).resolve() if args.out else None))


if __name__ == "__main__":
    main()
