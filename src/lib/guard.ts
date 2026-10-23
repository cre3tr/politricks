// Cleaning the reader's own words (devpost/spec.md > Welcome Page, Match API).
//
// One function, shared by the welcome page (before it saves the profile) and
// the Match API (before anything reaches Claude), so the words a reader sees
// saved are exactly the words that get sent. The reader's words are data,
// never instructions: this strips anything that could pass for markup or a
// control sequence, and caps the length.
//
// No imports, so `node` can load this file directly (check-api).

export const MAX_WORDS = 80;

export function cleanWords(input: unknown): string {
  if (typeof input !== "string") return "";
  return (
    input
      .normalize("NFC")
      // Markup first, so "<b>trader</b>" keeps its word and loses the tags.
      .replace(/<[^>]*>/g, " ")
      // Control characters (C0, DEL, C1) and the invisible bidi/zero-width
      // characters that can hide text from a human reader.
      .replace(/[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁦-⁩﻿]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, MAX_WORDS)
      .trim()
  );
}
