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
import type { Clause, GroupEntry, Review, Summary } from "./schema";

const VERSION = "politricks-review-v1";

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
  return reviewSha256([
    VERSION, "clause", clause.id, clause.text, text.en, text.sw, text.sheng, [...clause.groups].sort().join(","),
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

// What the badge shows. "changed" = it was reviewed, but the content is no
// longer what was accepted.
export type DisplayStatus = Review["status"] | "changed";

export function displayStatus(review: Review, current: string | null): DisplayStatus {
  if (review.status !== "reviewed") return review.status;
  return review.sha256 !== null && review.sha256 === current ? "reviewed" : "changed";
}
