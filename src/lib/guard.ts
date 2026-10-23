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
      // Control characters (Unicode Cc: C0, DEL, C1) and format characters
      // (Cf: zero-width, bidi overrides, BOM, soft hyphen) that can hide text
      // from a human reader. Written as property classes, not code-point
      // ranges, so no invisible character ever sits in this source file.
      .replace(/[\p{Cc}\p{Cf}]/gu, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, MAX_WORDS)
      .trim()
  );
}
