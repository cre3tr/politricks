// Offline checks for the two live helpers (devpost/spec.md > Live API) and
// the banned-phrase matcher they share with the pipeline. No network, no API
// key, writes nothing: the Claude calls are stubs.
//
//   node scripts/check-api.mjs
//
// What it proves:
//   - src/lib/banned.ts agrees with pipeline/politricks/lint.py on every case
//     in pipeline/tests/fixtures/banned_vectors.json (lint --self-test checks
//     the Python side against the same file).
//   - handleMatch / handleExplain (src/lib/api.ts) give the spec's status
//     codes, filter what Claude returns, and throw away a draft that carries
//     a banned phrase.
//   - Each check has a control that must fail if the code under test is broken.
//
// Node 24 loads the .ts files directly (type stripping).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const banned = await import(new URL("../src/lib/banned.ts", import.meta.url).href);
const api = await import(new URL("../src/lib/api.ts", import.meta.url).href);
const guard = await import(new URL("../src/lib/guard.ts", import.meta.url).href);

let passed = 0;
let failed = 0;
function check(label, condition, detail = "") {
  if (condition) {
    passed += 1;
    console.log(`PASS: ${label}`);
  } else {
    failed += 1;
    console.log(`FAIL: ${label}${detail ? ` (${detail})` : ""}`);
  }
}

// --- 1. The banned-phrase matcher: the same answers as the pipeline -------
const phrases = banned.parsePhrases(readFileSync(here("../pipeline/prompts/banned.txt"), "utf-8"));
check("banned.txt parses to a non-empty phrase list", phrases.length >= 19, String(phrases.length));
const vectors = JSON.parse(readFileSync(here("../pipeline/tests/fixtures/banned_vectors.json"), "utf-8")).vectors;
const wrong = vectors.filter((vector) => {
  const hits = banned.findBanned(vector.text, phrases);
  return JSON.stringify(hits) !== JSON.stringify(vector.hits);
});
check(
  `all ${vectors.length} shared banned-phrase vectors match (banned_vectors.json)`,
  vectors.length > 0 && wrong.length === 0,
  wrong.map((v) => `${JSON.stringify(v.text)} got ${JSON.stringify(banned.findBanned(v.text, phrases))}`).join("; "),
);
// Control: a matcher that ignored word boundaries would fire inside "devote".
check("control: 'vote' really is in the list, so the 'devote' case tests the boundary", phrases.includes("vote"));

// --- 2. The guard ----------------------------------------------------------
check("guard strips markup and control characters", guard.cleanWords("<b>I sell</b>\u0000 mitumba‮") === "I sell mitumba");
check("guard caps the words at 80 characters", guard.cleanWords("x".repeat(200)).length === 80);
check("guard turns a non-string into empty", guard.cleanWords(42) === "");

// --- 3. The handlers, with stubbed Claude calls -----------------------------
const bill = {
  id: "test-bill",
  title: "Test Bill",
  clauses: [
    { id: "c1", number: "1", heading: "Short title", text: "1. This Act may be cited as the Test Act.", explanation: { en: "Names the Act.", sw: "Jina.", sheng: "Jina." } },
    { id: "c2", number: "2", heading: "Fees", text: "2. A trader shall pay a fee.", explanation: null },
  ],
};
const goodAnswer = {
  explanation: { en: "Traders pay a fee.", sw: "Wafanyabiashara hulipa ada.", sheng: "Traders wana-pay fee." },
  neutral_check: { ok: true, note: "" },
};
let calls = 0;
function deps(overrides = {}) {
  return {
    bills: new Map([[bill.id, bill]]),
    phrases,
    match: async () => {
      calls += 1;
      return ["c2", "c99", "c2", "c1"];
    },
    explain: async () => {
      calls += 1;
      return goodAnswer;
    },
    ...overrides,
  };
}

let result = await api.handleMatch({ bill_id: "test-bill", words: "I sell mitumba Gikomba" }, deps());
check(
  "Match 200: only ids that exist, each once, in Claude's order",
  result.status === 200 && JSON.stringify(result.body.clause_ids) === JSON.stringify(["c2", "c1"]),
  JSON.stringify(result),
);
calls = 0;
result = await api.handleMatch({ bill_id: "test-bill", words: "  <i></i>  " }, deps());
check("Match 400 empty: words that clean to nothing, and no call is made", result.status === 400 && result.body.error === "empty" && calls === 0, JSON.stringify(result));
result = await api.handleMatch({ bill_id: "nope", words: "trader" }, deps());
check("Match 400 unknown_bill", result.status === 400 && result.body.error === "unknown_bill");
result = await api.handleMatch({ bill_id: "test-bill", words: "trader" }, deps({ match: async () => { throw new api.UpstreamError("refusal"); } }));
check("Match 502 upstream when the call fails", result.status === 502 && result.body.error === "upstream");
const many = Array.from({ length: 12 }, () => "c1").concat(["c2"]);
result = await api.handleMatch({ bill_id: "test-bill", words: "trader" }, deps({ match: async () => many }));
check("Match never returns more than 8 ids", result.status === 200 && result.body.clause_ids.length <= 8);

calls = 0;
result = await api.handleExplain({ bill_id: "test-bill", clause_id: "c1", lang: "sw" }, deps());
check("Explain 409 for a clause that already has an explanation, and no call is made", result.status === 409 && calls === 0, JSON.stringify(result));
result = await api.handleExplain({ bill_id: "test-bill", clause_id: "c2", lang: "sheng" }, deps());
check(
  "Explain 200 for a blank clause, in the asked language, with the draft label",
  result.status === 200 && result.body.text === goodAnswer.explanation.sheng && result.body.label === api.DRAFT_LABEL,
  JSON.stringify(result),
);
check("the draft label carries no em dash (learner's rule)", !api.DRAFT_LABEL.includes("—"));
result = await api.handleExplain({ bill_id: "test-bill", clause_id: "c9", lang: "en" }, deps());
check("Explain 400 unknown_clause for a clause that doesn't exist", result.status === 400 && result.body.error === "unknown_clause");
result = await api.handleExplain({ bill_id: "test-bill", clause_id: "c2", lang: "fr" }, deps());
check("Explain 400 for a language that isn't en/sw/sheng", result.status === 400);
const bannedAnswer = { ...goodAnswer, explanation: { ...goodAnswer.explanation, sw: "Hii ni kinyume cha katiba." } };
result = await api.handleExplain({ bill_id: "test-bill", clause_id: "c2", lang: "en" }, deps({ explain: async () => bannedAnswer }));
check("Explain 502: a banned phrase in ANY language throws the whole draft away, even when English was asked for", result.status === 502, JSON.stringify(result));
const flagged = { ...goodAnswer, neutral_check: { ok: false, note: "a value judgement" } };
result = await api.handleExplain({ bill_id: "test-bill", clause_id: "c2", lang: "en" }, deps({ explain: async () => flagged }));
check("Explain 502 when the model flags its own draft as not neutral", result.status === 502);
// Control: the same flow with a clean answer passes, so the 502s above come
// from the banned phrase / flag and not from something else in the setup.
result = await api.handleExplain({ bill_id: "test-bill", clause_id: "c2", lang: "en" }, deps({ explain: async () => goodAnswer }));
check("control: the identical request with a clean answer is 200", result.status === 200);

console.log(`checks: ${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
