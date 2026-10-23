// What "Reviewed" is tied to: a fingerprint of the content a human accepted.
//
// TypeScript twin of `current_sha256` and `review_sha256` in the pipeline
// (`pipeline/politricks/models.py`, `hashing.py`). `politricks review` stores
// the fingerprint when Ian accepts an item; the site recomputes it at build
// time, and shows "Reviewed" only while the two still match. So a hand-edit
// of the JSON, or a changed tag, can never keep the green badge.
//
// The two implementations are proved to agree by the shared vectors in
// `pipeline/tests/fixtures/review_hash_vectors.json`, checked by the pipeline
// self-test and by `scripts/check-build.mjs`.
//
// Uses Web Crypto (`crypto.subtle`), built into Node and browsers alike, so
// it needs no dependency; that makes it async. Imports are type-only, so
// `node` can load this file directly (check-build).
import type { Challenge, Clause, GroupEntry, Participation, Review, StageNext, Summary } from "./schema";

// Per kind, as in the pipeline (hashing.py): adding `suggested_articles`
// changed only a clause's parts, so only clauses are on v2. Summaries and
// groups are unchanged, so their reviews and stamps stay valid.
const VERSION = "politricks-review-v1"; // summary, group
const CLAUSE_VERSION = "politricks-review-v2"; // clause: v1 plus sorted suggested_articles

// Each part is UTF-8 encoded and prefixed with its byte length and a colon,
// then all are hashed together. No JSON: its escaping differs by language.
export async function reviewSha256(parts: string[]): Promise<string> {
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [];
  for (const part of parts) {
    const data = encoder.encode(part);
    chunks.push(encoder.encode(`${data.length}:`), data);
  }
  const bytes = new Uint8Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function clauseSha256(clause: Clause): Promise<string | null> {
  const text = clause.explanation;
  if (!text) return null;
  // Articles sort numerically (a plain .sort() would put 10 before 9).
  const articles = [...clause.suggested_articles].sort((a, b) => a - b).join(",");
  return reviewSha256([
    CLAUSE_VERSION, "clause", clause.id, clause.text, text.en, text.sw, text.sheng,
    [...clause.groups].sort().join(","), articles,
  ]);
}

export async function summarySha256(summary: Summary): Promise<string> {
  const text = summary.text;
  return reviewSha256([VERSION, "summary", text.en, text.sw, text.sheng]);
}

export async function groupSha256(entry: GroupEntry): Promise<string> {
  const text = entry.text;
  return reviewSha256([VERSION, "group", entry.group, text.en, text.sw, text.sheng, entry.clause_ids.join(",")]);
}

// A civic block (participation, stage_next, challenge): twin of
// `civic.civic_sha256` in the pipeline. It covers every reader-visible field,
// facts included, namespaced "civic-<kind>" so it can never equal another
// fingerprint. Checked against the same shared vectors as the others.
export type CivicKind = "participation" | "stage_next" | "challenge";

export async function civicSha256(kind: "participation", block: Participation): Promise<string>;
export async function civicSha256(kind: "stage_next", block: StageNext): Promise<string>;
export async function civicSha256(kind: "challenge", block: Challenge): Promise<string>;
export async function civicSha256(kind: CivicKind, block: Participation | StageNext | Challenge): Promise<string> {
  const parts = [VERSION, `civic-${kind}`];
  if (kind === "participation") {
    const p = block as Participation;
    parts.push(p.open ? "true" : "false", p.deadline ?? "", p.deadline_time ?? "", String(p.channels.length));
    for (const channel of p.channels) parts.push(channel.type, channel.value);
    parts.push(p.requirements.en, p.requirements.sw, p.requirements.sheng, p.source_url, p.checked_at);
  } else if (kind === "stage_next") {
    const s = block as StageNext;
    parts.push(s.text.en, s.text.sw, s.text.sheng, s.source_url);
  } else {
    const c = block as Challenge;
    parts.push(c.checked_at, String(c.avenues.length));
    for (const avenue of c.avenues) {
      parts.push(avenue.name.en, avenue.name.sw, avenue.name.sheng, avenue.text.en, avenue.text.sw, avenue.text.sheng, avenue.source_url);
    }
  }
  return reviewSha256(parts);
}

// What the badge shows. "changed" = it was reviewed, but the content is no
// longer what was accepted.
export type DisplayStatus = Review["status"] | "changed";

export function displayStatus(review: Review, current: string | null): DisplayStatus {
  if (review.status !== "reviewed") return review.status;
  return review.sha256 !== null && review.sha256 === current ? "reviewed" : "changed";
}
