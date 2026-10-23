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
const { clauseSha256, summarySha256, groupSha256, civicSha256, changeSha256, displayStatus } = reviewHash;
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
  change: changeSha256,
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

// ---------------------------------------------------------------------------
// The What changed tab (slice 8, src/components/ChangeRow.astro):
//   <article class="change-row" data-change-id="c2" data-change-kind="changed" data-ocr="true"> ... </article>
// One row per changes[] entry, in order. Each row is checked on its own: both
// legal texts and the note's three languages are inside it, its badge is what
// badge.ts gives for its DISPLAY status (changeSha256 twin), and a row whose
// "after" was read by OCR carries the label AND a link to the OCR source's PDF
// (a row without that label, or a label on a row that wasn't OCR, fails).
// A bill with no changes shows the honest one-version note and no rows.
// ---------------------------------------------------------------------------
const OCR_LABEL = "Read by OCR from a scanned official copy: check the PDF";
const rows = new Map();
const rowIds = [];
const rowTags = new Map();
for (const match of html.matchAll(/<article class="change-row" data-change-id="([^"]+)"([^>]*)>([\s\S]*?)<\/article>/g)) {
  rowIds.push(match[1]);
  rowTags.set(match[1], match[2]);
  rows.set(match[1], match[3]);
}
const ocrSource = bill.sources.find((source) => source.ocr) ?? null;
check(
  `What changed: one row per change, in order (${bill.changes.length} expected)`,
  JSON.stringify(rowIds) === JSON.stringify(bill.changes.map((change) => change.clause_id)),
  `page has ${rowIds.length} row(s): ${rowIds.join(", ")}`
);
if (bill.kind === "motion") {
  // A county motion has no tabs (MotionCard): no What changed panel, no rows.
  check("What changed: a motion has no tabs, so no change rows and no one-version note", bill.changes.length === 0 && rowIds.length === 0 && !pageText.includes("Only one version so far."));
} else if (bill.changes.length === 0) {
  check("What changed: no changes, so the page says there is only one version so far", pageText.includes("Only one version so far."));
}
for (const change of bill.changes) {
  const row = rows.get(change.clause_id);
  if (row === undefined) {
    check(`change ${change.clause_id} has its own row`, false, "no <article class=\"change-row\"> with that data-change-id");
    continue;
  }
  const rowText = htmlToText(row);
  const kind = change.before === null ? "added" : change.after === null ? "dropped" : "changed";
  const wanted = [change.before, change.after].filter((text) => text !== null).map(htmlToText).concat(triTexts(change.note));
  check(
    `change ${change.clause_id} (${kind}): ${kind === "added" ? "the later text" : kind === "dropped" ? "the earlier text" : "both texts"} and the note (EN/SW/Sheng) are inside its own row`,
    wanted.every((text) => rowText.includes(text)),
    `missing: ${wanted.filter((text) => !rowText.includes(text)).map((text) => JSON.stringify(text.slice(0, 40))).join(", ")}`
  );
  // The row's own marker must agree with the data, and a missing side must be
  // SAID ("Not in the first version" / "Not in the later version"), not left blank.
  const missingSide = kind === "added" ? "Not in the first version." : kind === "dropped" ? "Not in the later version." : null;
  check(
    `change ${change.clause_id}: the row is marked "${kind}" (data-change-kind) and ${missingSide ? `says "${missingSide}"` : "has both sides"}`,
    rowTags.get(change.clause_id).includes(`data-change-kind="${kind}"`) &&
      (missingSide ? rowText.includes(missingSide) : !rowText.includes("Not in the")),
    rowTags.get(change.clause_id)
  );
  checkBadge(`change ${change.clause_id}`, displayStatus(change.review, await changeSha256(change)), true, row);
  if (badgesIn(row).some((badge) => badge.label === expectedBadge.reviewed.label)) {
    const fingerprint = await changeSha256(change);
    check(
      `change ${change.clause_id} shows "Reviewed" only because its fingerprint matches`,
      change.review.status === "reviewed" && change.review.sha256 === fingerprint,
      `stored ${change.review.sha256}, recomputed ${fingerprint}`
    );
  }
  const link = [...row.matchAll(/<a href="([^"]*)"[^>]*data-ocr-link[^>]*>([\s\S]*?)<\/a>/g)];
  if (change.ocr) {
    check(
      `change ${change.clause_id}: the OCR'd "after" carries the label "${OCR_LABEL}", linked to the OCR source's PDF`,
      ocrSource !== null && link.length === 1 && htmlToText(link[0][2]).includes(OCR_LABEL) && link[0][1].startsWith(ocrSource.url.replace(/&/g, "&amp;")),
      `links: ${link.map((l) => l[1]).join(", ")}`
    );
  } else {
    check(`change ${change.clause_id}: not read by OCR, so it carries no OCR label`, link.length === 0 && !rowText.includes(OCR_LABEL));
  }
}
// The OCR source is only the "after" of a comparison: "Text shown" must list the
// sources the CLAUSES came from and not it.
const textShown = html.match(/<p class="text-shown"[^>]*>([\s\S]*?)<\/p>/);
if (ocrSource) {
  check(
    "the OCR source is not listed under \"Text shown\" (the clauses were not cut from it)",
    textShown !== null && !htmlToText(textShown[1]).includes(ocrSource.label)
  );
}

for (const source of bill.sources) {
  check(`source "${source.label}" full SHA-256 is on the page`, pageText.includes(source.sha256));
}

process.exit(failed ? 1 : 0);
