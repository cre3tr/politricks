// The banned-phrase matcher, TypeScript twin of `pipeline/politricks/lint.py`
// (`load_phrases`, `_pattern`) and `hashing.py` (`normalise`).
//
// The live Explain API uses it to DISCARD a generated text that carries a
// banned phrase (devpost/spec.md > Explain API), so the site can never
// publish what the pipeline's lint would have failed. The two sides must
// agree on every phrase and every text: the shared vectors in
// `pipeline/tests/fixtures/banned_vectors.json` are checked by
// `lint --self-test` (Python) and `scripts/check-api.mjs` (this file).
//
// No imports, so `node` can load this file directly (check-api).

// hashing.normalise: NFC, re-join a word hyphenated across a line break,
// collapse every run of whitespace to one space, trim.
export function normalise(text: string): string {
  return text
    .normalize("NFC")
    .replace(/([a-z])[-­]\n([a-z])/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

// lint.load_phrases: one phrase per line, `#` comments, blank lines skipped,
// a `*` only as the last character, case-insensitive duplicates dropped.
export function parsePhrases(fileText: string): string[] {
  const phrases: string[] = [];
  const seen = new Set<string>();
  for (const raw of fileText.replace(/^﻿/, "").split(/\r?\n/)) {
    const line = normalise(raw);
    if (!line || line.startsWith("#")) continue;
    const stem = line.endsWith("*") ? line.slice(0, -1).trimEnd() : line;
    if (!stem || stem.includes("*")) {
      throw new Error(`banned.txt: "${line}" has a * that is not the last character`);
    }
    const key = line.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      phrases.push(line);
    }
  }
  if (phrases.length === 0) {
    // A matcher with nothing to look for can never fail, which is not a pass.
    throw new Error("banned.txt holds no phrases");
  }
  return phrases;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Python's \w (Unicode): letters, numbers and the underscore. JavaScript's
// \w is ASCII-only, so the class is spelled out with Unicode properties.
const WORD = "[\\p{L}\\p{N}_]";

// lint._pattern: whole phrase on the left always ("vote" never fires inside
// "devote"); on the right a plain phrase ends at a word boundary, and a
// phrase ending in `*` lets its last word continue ("illegal*" fires on
// "illegally").
export function phrasePattern(phrase: string): RegExp {
  const starred = phrase.endsWith("*");
  const stem = starred ? phrase.slice(0, -1).trimEnd() : phrase;
  return new RegExp(
    `(?<!${WORD})${escapeRegExp(normalise(stem))}${starred ? `${WORD}*` : ""}(?!${WORD})`,
    "iu",
  );
}

// Every phrase that hits `text`, in banned.txt order. Empty = clean.
export function findBanned(text: string, phrases: string[]): string[] {
  const clean = normalise(text);
  return phrases.filter((phrase) => phrasePattern(phrase).test(clean));
}
