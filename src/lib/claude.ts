// The real Claude calls behind the two live helpers, plus everything else
// the routes need to run them (devpost/spec.md > Live API, Anthropic
// Messages API). The handlers themselves are in src/lib/api.ts.
//
// - The prompts and the banned-phrase list are the pipeline's own files,
//   bundled into the function at build time (`?raw`), so the site and the
//   pipeline can never drift onto different copies.
// - The API key is read at RUN time with `getSecret`, never inlined into the
//   build: a local `npm run build` must not copy the key into .vercel/output.
// - Model and effort match the pipeline (pipeline/politricks/generate.py):
//   claude-sonnet-5-5. A refusal is retried server-side on another model
//   (`fallbacks: "default"`), and one that still refuses becomes a 502.
import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import { getSecret } from "astro:env/server";
import { z } from "zod";
import explainerPrompt from "../../pipeline/prompts/explainer.md?raw";
import matcherPrompt from "../../pipeline/prompts/matcher.md?raw";
import bannedText from "../../pipeline/prompts/banned.txt?raw";
import { UpstreamError, type ApiDeps, type ExplainAnswer } from "./api.ts";
import { parsePhrases } from "./banned.ts";
import { BillSchema, GroupIdSchema, type Bill, type Clause } from "./schema";

const MODEL = "claude-sonnet-5-5";
const FALLBACK_BETA = "server-side-fallback-2026-07-01";

// Under the client-side timeouts (15 s for Match, 60 s for Explain), so the
// server gives up first and answers 502 rather than leaving the page waiting.
const MATCH_TIMEOUT_MS = 14_000;
const EXPLAIN_TIMEOUT_MS = 55_000;

// What each call must return. Strict objects, like the pipeline's schemas:
// a malformed answer fails to parse and becomes a 502, never half a draft.
const MatchOut = z.object({ clause_ids: z.array(z.string()) }).strict();
const Tri = z.object({ en: z.string(), sw: z.string(), sheng: z.string() }).strict();
const ClauseOut = z
  .object({
    explanation: Tri,
    groups: z.array(GroupIdSchema),
    suggested_articles: z.array(z.number().int()),
    neutral_check: z.object({ ok: z.boolean(), note: z.string() }).strict(),
    sheng_swaps: z.array(z.object({ word: z.string(), english: z.string() }).strict()),
  })
  .strict();

let client: Anthropic | null = null;

function getClient(): Anthropic {
  if (client) return client;
  const apiKey = getSecret("ANTHROPIC_API_KEY");
  if (!apiKey) throw new UpstreamError("ANTHROPIC_API_KEY is not set");
  // maxRetries 0: a retry would blow the timeouts above, and the page has
  // its own quiet fallback.
  client = new Anthropic({ apiKey, maxRetries: 0 });
  return client;
}

// Every bill the site was built with, validated once per function instance.
const bills = new Map<string, Bill>(
  Object.values(
    import.meta.glob<{ default: unknown }>("../data/bills/*.json", { eager: true }),
  ).map((module) => {
    const bill = BillSchema.parse(module.default);
    return [bill.id, bill];
  }),
);

async function callMatch(bill: Bill, words: string): Promise<string[]> {
  // The bill's sections go in a cached system block: they're the same for
  // every reader of this bill, so repeat calls pay a fraction for them.
  const sections = bill.clauses
    .filter((clause) => clause.explanation)
    .map((clause) => `<section id="${clause.id}">${clause.explanation!.en}</section>`)
    .join("\n");
  const response = await getClient().beta.messages.parse(
    {
      model: MODEL,
      max_tokens: 2048,
      system: [
        { type: "text", text: matcherPrompt },
        { type: "text", text: `Bill: ${bill.title}\n\n${sections}`, cache_control: { type: "ephemeral" } },
      ],
      messages: [{ role: "user", content: `<reader_words>\n${words}\n</reader_words>` }],
      output_config: { effort: "low", format: betaZodOutputFormat(MatchOut) },
      betas: [FALLBACK_BETA],
      fallbacks: "default",
    },
    { timeout: MATCH_TIMEOUT_MS },
  );
  if (response.stop_reason === "refusal" || !response.parsed_output) {
    throw new UpstreamError(`match: ${response.stop_reason}`);
  }
  return response.parsed_output.clause_ids;
}

async function callExplain(bill: Bill, clause: Clause): Promise<ExplainAnswer> {
  // Word for word the request the pipeline sends (generate.py > build_params),
  // so a live draft reads like a batch one.
  const userText = `Bill: ${bill.title}\nSection ${clause.number}: ${clause.heading}\n\n<clause>\n${clause.text}\n</clause>`;
  const response = await getClient().beta.messages.parse(
    {
      model: MODEL,
      max_tokens: 16000,
      thinking: { type: "adaptive" },
      system: [{ type: "text", text: explainerPrompt, cache_control: { type: "ephemeral" } }],
      messages: [{ role: "user", content: userText }],
      output_config: { effort: "high", format: betaZodOutputFormat(ClauseOut) },
      betas: [FALLBACK_BETA],
      fallbacks: "default",
    },
    { timeout: EXPLAIN_TIMEOUT_MS },
  );
  if (response.stop_reason === "refusal" || !response.parsed_output) {
    throw new UpstreamError(`explain: ${response.stop_reason}`);
  }
  return response.parsed_output;
}

export function liveDeps(): ApiDeps {
  return {
    bills,
    phrases: parsePhrases(bannedText),
    match: callMatch,
    explain: callExplain,
  };
}
