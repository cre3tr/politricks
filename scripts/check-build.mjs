#!/usr/bin/env node
// Assertions on the BUILT HTML, not the source components — this is what
// actually ships to a phone, so it's the only thing worth checking here.
// Node stdlib only (no test framework): `node scripts/check-build.mjs <bill-id>`.
//
// Why this file exists at all: `npm run build` succeeding only proves the
// page rendered without throwing. It says nothing about whether the legal
// text, the explanations, or the badges actually landed in the output —
// this script is the second, independent check (see devpost/checklist.md >
// slice 2a's Verify step).
//
// It checks EACH CARD, not page totals. A page-wide count ("Reviewed appears
// 3 times") cannot see two cards with their statuses swapped, and it trips on
// any legal or explanation text that happens to contain the word. So the built
// HTML is cut into one piece per clause card (and one for the summary block),
// and each piece is checked against the bill JSON for THAT clause:
//   - its legal text is inside its own card (word for word),
//   - its explanation (EN / SW / Sheng) is inside its own card,
//   - its badge label and class are exactly what src/lib/badge.ts gives for
//     its DISPLAY status (src/lib/reviewHash.ts: "Reviewed" only while the
//     stored fingerprint matches the content), and a clause with no
//     explanation has NO badge.
// Before any of that, it proves the site's fingerprint (reviewHash.ts) and the
// pipeline's (models.current_sha256) agree, on the shared vectors in
// pipeline/tests/fixtures/review_hash_vectors.json.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

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
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`PASS: ${label}`);
  } else {
    console.log(`FAIL: ${label}${detail ? ` (${detail})` : ""}`);
    failed = true;
  }
}

// ---------------------------------------------------------------------------
// The review fingerprint: the site's twin must match the pipeline exactly.
// Node 24 loads the .ts file directly (its imports are type-only).
// ---------------------------------------------------------------------------
const reviewHash = await import(new URL("../src/lib/reviewHash.ts", import.meta.url).href);
const { clauseSha256, summarySha256, groupSha256, civicSha256, displayStatus } = reviewHash;
const { vectors } = JSON.parse(
  readFileSync(fileURLToPath(new URL("../pipeline/tests/fixtures/review_hash_vectors.json", import.meta.url)), "utf-8")
);
const twin = {
  clause: clauseSha256,
  summary: summarySha256,
  group: groupSha256,
  "civic-participation": (item) => civicSha256("participation", item),
  "civic-stage_next": (item) => civicSha256("stage_next", item),
  "civic-challenge": (item) => civicSha256("challenge", item),
};
const got = await Promise.all(vectors.map((vector) => twin[vector.kind](vector.item)));
const mismatched = vectors.filter((vector, i) => got[i] !== vector.sha256);
check(
  `review fingerprints: src/lib/reviewHash.ts matches the pipeline on all ${vectors.length} shared vectors`,
  vectors.length > 0 && mismatched.length === 0,
  mismatched.map((vector) => `${vector.kind} ${vector.item.id ?? vector.item.group ?? ""}`).join(", ")
);

// ---------------------------------------------------------------------------
// What each status's badge must say: read from src/lib/badge.ts, the one place
// that decides it (ClauseCard and the bill page both call badgeFor). Located
// from this file's own path, so it works from any working directory.
// ---------------------------------------------------------------------------
const badgeSource = readFileSync(fileURLToPath(new URL("../src/lib/badge.ts", import.meta.url)), "utf-8");
const expectedBadge = {};
for (const status of ["draft", "reviewed", "sample", "changed"]) {
  const found = badgeSource.match(
    new RegExp(`case "${status}":\\s*return \\{ label: "([^"]+)", className: "([^"]+)" \\}`)
  );
  if (!found) {
    console.error(`FAIL: could not read the "${status}" badge from src/lib/badge.ts (did its shape change?)`);
    process.exit(1);
  }
  expectedBadge[status] = { label: found[1], className: found[2] };
}
const labels = Object.values(expectedBadge).map((badge) => badge.label);
check(
  `badge labels read from src/lib/badge.ts: ${Object.entries(expectedBadge)
    .map(([status, badge]) => `${status}="${badge.label}"`)
    .join(", ")}`,
  new Set(labels).size === 4
);

// Every <span class="badge badge-…">label</span> inside a piece of the page.
function badgesIn(fragment) {
  return [...fragment.matchAll(/<span class="badge (badge-[a-z]+)"[^>]*>([^<]*)<\/span>/g)].map((match) => ({
    className: match[1],
    label: htmlToText(match[2]),
  }));
}

function describeBadges(badges) {
  return badges.length === 0 ? "no badge" : badges.map((badge) => `"${badge.label}" (${badge.className})`).join(" + ");
}

// A block with an explanation must carry exactly the badge for its status; a
// block with none must carry no badge at all.
function checkBadge(what, status, hasText, fragment) {
  const found = badgesIn(fragment);
  if (!hasText) {
    check(`${what} has no explanation, so it has NO badge`, found.length === 0, `page shows ${describeBadges(found)}`);
    return;
  }
  const want = expectedBadge[status];
  check(
    `${what} (${status}) shows the badge "${want.label}"`,
    found.length === 1 && found[0].label === want.label && found[0].className === want.className,
    `page shows ${describeBadges(found)}`
  );
}

function triTexts(tri) {
  return [tri.en, tri.sw, tri.sheng].map(htmlToText);
}

// ---------------------------------------------------------------------------
// One piece of HTML per clause card. ClauseCard renders
//   <article class="clause-card" data-clause-id="c12" ...> ... </article>
// and cards never nest, so a non-greedy match to the next </article> is the card.
// ---------------------------------------------------------------------------
const cards = new Map();
const cardIds = [];
for (const match of html.matchAll(/<article class="clause-card" data-clause-id="([^"]+)"[^>]*>([\s\S]*?)<\/article>/g)) {
  cardIds.push(match[1]);
  cards.set(match[1], match[2]);
}
check(
  `the page has one clause card per clause, in order (${bill.clauses.length} expected)`,
  JSON.stringify(cardIds) === JSON.stringify(bill.clauses.map((clause) => clause.id)),
  `page has ${cardIds.length} card(s)`
);

for (const clause of bill.clauses) {
  const card = cards.get(clause.id);
  if (card === undefined) {
    check(`clause ${clause.id} has its own card`, false, "no <article> with that data-clause-id");
    continue;
  }
  const cardText = htmlToText(card);
  const wanted = [htmlToText(clause.text), ...(clause.explanation ? triTexts(clause.explanation) : [])];
  check(
    `clause ${clause.id} legal text${clause.explanation ? " and explanation (EN/SW/Sheng) are inside its own card" : " is inside its own card"}`,
    wanted.every((text) => cardText.includes(text)),
    `missing: ${wanted.filter((text) => !cardText.includes(text)).map((text) => JSON.stringify(text.slice(0, 40))).join(", ")}`
  );
  const fingerprint = await clauseSha256(clause);
  checkBadge(`clause ${clause.id}`, displayStatus(clause.review, fingerprint), Boolean(clause.explanation), card);
  // The same rule again, without displayStatus, so a bug there can't agree
  // with itself: a green "Reviewed" badge needs a stored fingerprint that
  // equals the one recomputed from what the card shows.
  if (badgesIn(card).some((badge) => badge.label === expectedBadge.reviewed.label)) {
    check(
      `clause ${clause.id} shows "Reviewed" only because its fingerprint matches`,
      clause.review.status === "reviewed" && clause.review.sha256 !== null && clause.review.sha256 === fingerprint,
      `stored ${clause.review.sha256}, recomputed ${fingerprint}`
    );
  }
}

// ---------------------------------------------------------------------------
// The summary block (src/pages/bills/[id].astro):
//   <section class="summary-card" aria-label="Summary"> badge + EN/SW/Sheng </section>
// ---------------------------------------------------------------------------
const summaryBlock = html.match(/<section class="summary-card"[^>]*>([\s\S]*?)<\/section>/);
if (bill.summary) {
  check("the summary block is on the page", summaryBlock !== null);
  if (summaryBlock) {
    const summaryText = htmlToText(summaryBlock[1]);
    check(
      "the summary text (EN/SW/Sheng) is inside the summary block",
      triTexts(bill.summary.text).every((text) => summaryText.includes(text))
    );
    const summaryFingerprint = await summarySha256(bill.summary);
    checkBadge("the summary", displayStatus(bill.summary.review, summaryFingerprint), true, summaryBlock[1]);
    // The same independent rule as for clause cards (no displayStatus).
    if (badgesIn(summaryBlock[1]).some((badge) => badge.label === expectedBadge.reviewed.label)) {
      check(
        'the summary shows "Reviewed" only because its fingerprint matches',
        bill.summary.review.status === "reviewed" && bill.summary.review.sha256 === summaryFingerprint,
        `stored ${bill.summary.review.sha256}, recomputed ${summaryFingerprint}`
      );
    }
  }
} else {
  check("the bill has no summary, so the page has no summary block", summaryBlock === null);
}

for (const source of bill.sources) {
  check(`source "${source.label}" full SHA-256 is on the page`, pageText.includes(source.sha256));
}

process.exit(failed ? 1 : 0);
