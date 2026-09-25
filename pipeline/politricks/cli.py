"""The `politricks` command-line tool.

Run from anywhere; paths are resolved from the repo root, not your current folder.

    uv run --project pipeline politricks fetch finance-bill-2026
    uv run --project pipeline politricks fetch --check-url <url>
    uv run --project pipeline politricks probe finance-act-2026
    uv run --project pipeline politricks extract finance-act-2026 [--check-json <path>]
    uv run --project pipeline politricks verify [--self-test]
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

    parser = argparse.ArgumentParser(prog="politricks")
    sub = parser.add_subparsers(dest="command", required=True)

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

    args = parser.parse_args()

    if args.command == "fetch":
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


if __name__ == "__main__":
    main()
