// Splits a clause's legal text into segments so ClauseCard can render the
// "matatu highlighter" marks (devpost/spec.md > Look and Feel) without ever
// touching the text itself — the legal text a reader sees always stays
// byte-for-byte what's in the bill JSON; only WHICH SPAN gets a highlight
// class changes.
//
// Two kinds of highlight, deliberately conservative (a false "not
// highlighted" is fine; a false highlight over ordinary prose looks like a
// bug):
//   - "change": the small set of verbs Kenyan drafting actually uses for
//     amending an existing Act ("deleting", "substituting", "inserting",
//     "repealing", "amended"/"amending", "replacing").
//   - "amount": money and dates — the numbers a reader most wants to spot at
//     a glance (shillings, percentages, and full "1st July, 2026" dates).
//
// INVARIANT: joining every segment's `text` back together, in order, MUST
// reproduce the input exactly. `splitHighlights` asserts this itself (so a
// broken regex fails the Astro build loudly, not a silent rendering bug),
// and `scripts/check-build.mjs` re-checks it against the built HTML's own
// textContent as a second, independent axis (Iron Law I: one axis is a
// claim, two is a measurement).

export type SegmentKind = "plain" | "change" | "amount";

export interface Segment {
  text: string;
  kind: SegmentKind;
}

const CHANGE_WORDS = "deleting|substituting|inserting|repealing|amended|amending|replacing";

// A conservative set of written-out numbers. Kenyan Acts write amounts this
// way often enough ("one shilling and fifty cents") that skipping them would
// miss real money in the text — see finance-act-2026's own c55 sample.
const NUMBER_WORDS =
  "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|" +
  "sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|" +
  "hundred|thousand|million|billion";

const MONTHS =
  "January|February|March|April|May|June|July|August|September|October|November|December";

// Each alternative is its own conservative pattern; combined with `|` inside
// one named group so a single pass finds all of them in text order.
const AMOUNT_ALTERNATIVES = [
  String.raw`per\s*centum`,
  String.raw`%`,
  String.raw`Kenya\s+shillings\s+[\d,]+`,
  String.raw`KSh\.?\s?[\d,]+`,
  String.raw`\d+(?:\.\d+)?\s?per\s?cent`,
  // Written amounts: a run of number words immediately followed by
  // "shilling(s)"/"cent(s)" — e.g. "one shilling", "fifty cents". A phrase
  // like "one shilling and fifty cents" becomes two separate amount
  // segments either side of the plain word "and", which is fine: both
  // numbers still get marked.
  // \b stops a match starting mid-word (e.g. "someone shilling" -> "one shilling").
  String.raw`\b(?:${NUMBER_WORDS})(?:[ -](?:${NUMBER_WORDS}))*\s+(?:shillings?|cents?)`,
  // Full dates like "1st July, 2026" (comma optional).
  String.raw`\d{1,2}(?:st|nd|rd|th)\s+(?:${MONTHS}),?\s+\d{4}`,
].join("|");

const SEGMENT_PATTERN = new RegExp(
  `(?<change>\\b(?:${CHANGE_WORDS})\\b)|(?<amount>${AMOUNT_ALTERNATIVES})`,
  "gi"
);

export function splitHighlights(text: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  // A fresh RegExp is built above per module load, but `lastIndex` is still
  // stateful on the shared instance — reset it so repeated calls (one per
  // clause) don't skip ahead from a previous call.
  SEGMENT_PATTERN.lastIndex = 0;

  let match: RegExpExecArray | null;
  while ((match = SEGMENT_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), kind: "plain" });
    }
    const kind: SegmentKind = match.groups?.change !== undefined ? "change" : "amount";
    segments.push({ text: match[0], kind });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), kind: "plain" });
  }

  // The invariant this whole module exists to guarantee. If this ever
  // throws, it means a pattern above skipped or duplicated a character —
  // fail the build rather than ship legal text that quietly doesn't match
  // its own fingerprint's source string.
  const rejoined = segments.map((segment) => segment.text).join("");
  if (rejoined !== text) {
    throw new Error("highlight.ts: segments do not rejoin to the original clause text");
  }

  return segments;
}
