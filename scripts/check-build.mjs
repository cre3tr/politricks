#!/usr/bin/env node
// Assertions on the BUILT HTML, not the source components — this is what
// actually ships to a phone, so it's the only thing worth checking here.
// Node stdlib only (no test framework): `node scripts/check-build.mjs <bill-id>`.
//
// Why this file exists at all: `npm run build` succeeding only proves the
// page rendered without throwing. It says nothing about whether the legal
// text, the sample Sheng, or the badges actually landed in the output —
// this script is the second, independent check (see devpost/checklist.md >
// slice 2a's Verify step).
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const billId = process.argv[2];
if (!billId) {
  console.error("usage: node scripts/check-build.mjs <bill-id>");
  process.exit(1);
}

// Astro's Vercel adapter writes static output under .vercel/output/static;
// a plain `astro build` (no adapter, or `astro preview`-only setups) writes
// to dist/. Try both, in that order, so this script works either way.
const candidates = [
  join(".vercel", "output", "static", "bills", billId, "index.html"),
  join("dist", "bills", billId, "index.html"),
];
const htmlPath = candidates.find(existsSync);
if (!htmlPath) {
  console.error(`FAIL: no built page found at either of:\n  ${candidates.join("\n  ")}`);
  process.exit(1);
}

const html = readFileSync(htmlPath, "utf-8");

// Strip tags, decode the entities Astro/browsers actually emit for plain
// text, then collapse whitespace — turning the HTML into roughly what a
// screen reader (or a human squinting at "view source") would read as text.
function htmlToText(raw) {
  // Tags are stripped to NOTHING, not a space. Astro's output has no
  // whitespace between tags (it's not pretty-printed), and ClauseCard wraps
  // highlighted spans (hl-change/hl-amount) INSIDE a clause's own legal
  // text with no surrounding space of their own — e.g. "on <span>1st
  // January, 2027</span>, section" — so replacing a tag with a space would
  // insert a character that was never actually there ("2027 , section"),
  // breaking an exact substring match against the clause's stored text.
  const noTags = raw.replace(/<[^>]*>/g, "");
  const decoded = noTags
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&nbsp;/g, " ")
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)));
  return decoded.replace(/\s+/g, " ").trim();
}

const pageText = htmlToText(html);
const bill = JSON.parse(readFileSync(join("src", "data", "bills", `${billId}.json`), "utf-8"));

let failed = false;
function check(label, condition) {
  if (condition) {
    console.log(`PASS: ${label}`);
  } else {
    console.log(`FAIL: ${label}`);
    failed = true;
  }
}

function countOccurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
}

for (const clause of bill.clauses) {
  check(`clause ${clause.id} legal text is on the page`, pageText.includes(htmlToText(clause.text)));
}

const sampleClauses = bill.clauses.filter((clause) => clause.review.status === "sample");
for (const clause of sampleClauses) {
  check(`clause ${clause.id} sample Sheng text is on the page`, pageText.includes(htmlToText(clause.explanation.sheng)));
}

const summaryIsSample = bill.summary?.review.status === "sample";
if (summaryIsSample) {
  check("summary sample Sheng text is on the page", pageText.includes(htmlToText(bill.summary.text.sheng)));
}

const expectedSampleBadges = sampleClauses.length + (summaryIsSample ? 1 : 0);
check(
  `"Sample text — placeholder" appears ${expectedSampleBadges} times (${sampleClauses.length} sample clause(s) + summary)`,
  countOccurrences(pageText, "Sample text — placeholder") === expectedSampleBadges
);

// Case-sensitive on purpose: these are the exact badge label strings
// (ClauseCard's `badgeFor`). Neither should appear anywhere in slice 2a,
// since every explained item here is "sample" and everything else is
// unexplained — this also implicitly checks that banner copy never
// accidentally contains a badge's exact wording.
check('"AI draft" appears 0 times', countOccurrences(pageText, "AI draft") === 0);
check('"Reviewed" appears 0 times', countOccurrences(pageText, "Reviewed") === 0);

for (const source of bill.sources) {
  check(`source "${source.label}" full SHA-256 is on the page`, pageText.includes(source.sha256));
}

process.exit(failed ? 1 : 0);
