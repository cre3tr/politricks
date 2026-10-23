"""The shared review-fingerprint vectors (pipeline/tests/fixtures/review_hash_vectors.json).

Items with awkward text (newlines, colons, quotes, a backslash, an em dash,
an emoji, NFD accents, U+2028, unsorted tags, empty strings) and the
fingerprint `models.current_sha256` gives each. The site's twin
(`src/lib/reviewHash.ts`) must give the same: `scripts/check-build.mjs` checks
it, and `review --self-test` checks this side against the file.

If you change what a fingerprint covers, change both twins, then rewrite
the file and commit it:

    uv run --project pipeline python -m politricks.hashvectors
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Clause, GroupEntry, Summary, current_sha256

FIXTURE = ("pipeline", "tests", "fixtures", "review_hash_vectors.json")

clause = {
    "id": "c12-1", "number": "12", "heading": "Amendment of section 6",
    "text": "Section 6 is amended\u2014 (a) by deleting \"and\"; (b) KSh 1,000:",
    "text_sha256": "0" * 64, "pages": [3],
    "explanation": {
        "en": "Line one.\nLine two: 12:34 \u2014 a dash, \"quotes\", back\\slash, tab\there.",
        "sw": "Mswada unabadilisha \u00e9l\u00e8ve na \u00fc (NFD e\u0301 stays as written).",
        "sheng": "Ina-add 1.5% kwa scrap metal \U0001F69A na winnings \u2028 sep.",
    },
    "review": {"status": "draft", "at": None, "sha256": None},
    "neutral_check": None, "groups": ["transport", "trader"], "suggested_articles": [], "sheng_swaps": [],
}
untagged = {**clause, "id": "c1", "groups": [], "explanation": {"en": "", "sw": "", "sheng": ""}}
# Articles given out of order, with a two-digit number among one-digit ones: a
# plain string sort would put 10 before 9, a numeric sort does not.
with_articles = {**clause, "id": "c9", "suggested_articles": [43, 10, 9, 201]}
summary = {"text": {"en": "Summary: 3 changes.", "sw": "Muhtasari.", "sheng": "Summary ya bill."},
           "review": {"status": "draft", "at": None, "sha256": None}, "sheng_swaps": []}
group = {"group": "trader", "text": {"en": "Traders.", "sw": "Wafanyabiashara.", "sheng": "Ma-trader."},
         "clause_ids": ["c3", "c12-1"], "review": {"status": "draft", "at": None, "sha256": None}, "sheng_swaps": []}



# Civic blocks (civic.civic_sha256; twin `civicSha256` in reviewHash.ts).
# Facts are in the fingerprint too, so the vectors carry awkward ones: a null
# deadline_time, two channels, a colon and quotes in a value, accents.
draft = {"status": "draft", "at": None, "sha256": None}
participation = {
    "open": True, "deadline": "2026-11-02", "deadline_time": None,
    "channels": [{"type": "email", "value": "clerk@parliament.go.ke (copy: \"committee\")"},
                 {"type": "portal", "value": "https://www.parliament.go.ke/contact/x?bill=A%20B"}],
    "requirements": {"en": "Written memoranda: 2 copies.", "sw": "Maoni ya maandishi: nakala 2.", "sheng": "Memoranda za maandishi, copies 2."},
    "source_url": "https://www.parliament.go.ke/node/1", "checked_at": "2026-10-23", "sheng_swaps": [], "review": draft,
}
stage_next = {"text": {"en": "Second Reading next.", "sw": "Kusomwa kwa mara ya pili kunafuata.", "sheng": "Second Reading ndio inafuata."},
              "source_url": "https://www.parliament.go.ke/tracker.pdf", "sheng_swaps": [], "review": draft}
challenge = {"checked_at": "2026-10-19", "sheng_swaps": [], "review": draft, "avenues": [
    {"name": {"en": "A petition", "sw": "Ombi", "sheng": "Petition"}, "text": {"en": "Article 119.", "sw": "Ibara ya 119.", "sheng": "Article 119."}, "source_url": "https://example.org/a"},
    {"name": {"en": "Court", "sw": "Mahakama", "sheng": "Kort"}, "text": {"en": "Article 22.", "sw": "Ibara ya 22.", "sheng": "Article 22."}, "source_url": "https://example.org/b"},
]}


def build() -> list[dict]:
    """Every vector, with the fingerprint this side computes NOW."""
    from . import civic  # imported here: civic imports generate, which is heavy

    vectors = []
    for kind, data, model in (("clause", clause, Clause), ("clause", untagged, Clause),
                              ("clause", with_articles, Clause), ("summary", summary, Summary), ("group", group, GroupEntry)):
        vectors.append({"kind": kind, "item": data, "sha256": current_sha256(model.model_validate(data))})
    for kind, data in (("participation", participation), ("stage_next", stage_next), ("challenge", challenge)):
        block = civic.BLOCK_MODELS[kind].model_validate(data)
        vectors.append({"kind": f"civic-{kind}", "item": data, "sha256": civic.civic_sha256(kind, block)})
    return vectors


def write(repo_root: Path) -> Path:
    out = repo_root.joinpath(*FIXTURE)
    out.write_text(json.dumps({
        "about": "Review fingerprints the pipeline (models.current_sha256) and the site (src/lib/reviewHash.ts) "
                 "must both produce. Checked by `politricks review --self-test` and scripts/check-build.mjs.",
        "vectors": build(),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    print(f"wrote {write(Path.cwd())}")
