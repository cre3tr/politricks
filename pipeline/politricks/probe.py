"""Look at a PDF's layout before writing rules to split it.

`politricks probe <bill-id>` prints, for a handful of pages:
  - the most common character size (the body text size),
  - where body characters sit horizontally (so marginal notes can be told apart),
  - the first and last few lines (to spot running headers, footers, page numbers).

It changes nothing. Its only job is to replace guesses with evidence.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pdfplumber


def probe(pdf_path: Path, pages: list[int] | None = None) -> None:
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        chosen = pages or sorted({1, 2, 3, total // 2, total - 1, total})
        for number in chosen:
            if not 1 <= number <= total:
                continue
            page = pdf.pages[number - 1]
            chars = page.chars
            print(f"=== page {number}/{total}  (width {page.width:.0f}) ===")
            if not chars:
                print("  (no text on this page)")
                continue
            sizes = Counter(round(c["size"], 1) for c in chars)
            body_size = sizes.most_common(1)[0][0]
            print(f"  sizes (top 4): {sizes.most_common(4)}  -> body size {body_size}")
            # Horizontal spread of body-size characters, in 20pt buckets.
            xs = Counter(int(c["x0"] // 20) * 20 for c in chars if round(c["size"], 1) == body_size)
            print(f"  body x0 buckets: {sorted(xs.items())}")
            small = [c for c in chars if c["size"] < body_size * 0.9]
            if small:
                sx = Counter(int(c["x0"] // 20) * 20 for c in small)
                print(f"  small-font x0 buckets: {sorted(sx.items())}")
            lines = (page.extract_text() or "").splitlines()
            print("  first lines:", lines[:3])
            print("  last lines: ", lines[-3:])
