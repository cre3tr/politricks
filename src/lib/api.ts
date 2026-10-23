// The two live helpers, as plain functions (devpost/spec.md > Live API).
//
// Match: the reader's own words -> the clauses most likely to affect them.
// Explain: a draft explanation for a clause that has none yet.
//
// Everything that talks to the outside world (the Claude calls) is passed in
// as `deps`, so `scripts/check-api.mjs` can drive both handlers with stubbed
// answers, offline, and check every status code, including the rule that a
// draft carrying a banned phrase is thrown away. The real calls live in
// src/lib/claude.ts; the routes in src/pages/api/ only glue the two together.
//
// Imports carry their `.ts` extension so `node` can load this file directly.
import type { Bill, Clause } from "./schema";
import { findBanned } from "./banned.ts";
import { cleanWords } from "./guard.ts";

export type Lang = "en" | "sw" | "sheng";
const LANGS: readonly Lang[] = ["en", "sw", "sheng"];

// Matches the clause card's own badge wording (src/lib/badge.ts). The spec
// wrote it with an em dash; UI copy has none (learner's call, 2026-10-13).
export const DRAFT_LABEL = "AI draft (not yet reviewed)";
export const MAX_MATCHES = 8;

// What Claude returns for one clause: the same shape the pipeline asks for
// (pipeline/politricks/generate.py > ClauseOut). Only the explanation and the
// model's own neutrality flag are used here.
export interface ExplainAnswer {
  explanation: { en: string; sw: string; sheng: string };
  neutral_check: { ok: boolean; note: string };
}

// Thrown by a model call for anything that isn't a usable answer: an API
// error, a refusal, output that failed to parse. Always becomes a 502.
export class UpstreamError extends Error {}

export interface ApiDeps {
  bills: Map<string, Bill>;
  phrases: string[];
  match: (bill: Bill, words: string) => Promise<string[]>;
  explain: (bill: Bill, clause: Clause) => Promise<ExplainAnswer>;
}

export interface ApiResult {
  status: number;
  body: Record<string, unknown>;
}

function field(body: unknown, name: string): unknown {
  return typeof body === "object" && body !== null ? (body as Record<string, unknown>)[name] : undefined;
}

export async function handleMatch(body: unknown, deps: ApiDeps): Promise<ApiResult> {
  const billId = field(body, "bill_id");
  const bill = typeof billId === "string" ? deps.bills.get(billId) : undefined;
  if (!bill) return { status: 400, body: { error: "unknown_bill" } };
  const words = cleanWords(field(body, "words"));
  if (!words) return { status: 400, body: { error: "empty" } };

  let ids: string[];
  try {
    ids = await deps.match(bill, words);
  } catch {
    return { status: 502, body: { error: "upstream" } };
  }
  // Only ids that exist in this bill, each once, at most 8: the page trusts
  // this list to reorder its own cards, so nothing invented gets through.
  const known = new Set(bill.clauses.map((clause) => clause.id));
  const clauseIds = [...new Set(ids)].filter((id) => known.has(id)).slice(0, MAX_MATCHES);
  return { status: 200, body: { clause_ids: clauseIds } };
}

export async function handleExplain(body: unknown, deps: ApiDeps): Promise<ApiResult> {
  const billId = field(body, "bill_id");
  const clauseId = field(body, "clause_id");
  const lang = field(body, "lang");
  const bill = typeof billId === "string" ? deps.bills.get(billId) : undefined;
  const clause = bill?.clauses.find((candidate) => candidate.id === clauseId);
  if (!bill || !clause || !LANGS.includes(lang as Lang)) {
    return { status: 400, body: { error: "unknown_clause" } };
  }
  // A clause that already has an explanation shows that one (reviewed or
  // labelled draft). Never pay for a second, unreviewed version of it.
  if (clause.explanation) return { status: 409, body: { error: "already_explained" } };

  let answer: ExplainAnswer;
  try {
    answer = await deps.explain(bill, clause);
  } catch {
    return { status: 502, body: { error: "upstream" } };
  }
  // The same doctrine the pipeline's lint enforces: a banned phrase in ANY of
  // the three languages throws the whole draft away, and so does the model's
  // own "this isn't neutral" flag. The reader sees "not ready" instead.
  const texts = LANGS.map((one) => answer.explanation[one]);
  const banned = texts.some((text) => findBanned(text, deps.phrases).length > 0);
  if (banned || !answer.neutral_check.ok || texts.some((text) => !text.trim())) {
    return { status: 502, body: { error: "upstream" } };
  }
  return { status: 200, body: { text: answer.explanation[lang as Lang], label: DRAFT_LABEL } };
}
