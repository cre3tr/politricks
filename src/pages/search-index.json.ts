// /search-index.json, built with the site (devpost/spec.md > Home Page,
// Data Model > Search index): what the home page's Una'tafuta? box filters
// in the browser. Prebuilt like everything else, so search costs no server
// and works on the cached page.
//
// Kept small for phones on cheap data: per bill, the title, chamber, stage
// and status, plus the words a citizen would search with (the summary and
// the group notes in all three languages, and every clause heading). Full
// clause explanations stay on the bill pages.
import type { APIRoute } from "astro";
import { BillSchema } from "../lib/schema";

const bills = Object.values(import.meta.glob<{ default: unknown }>("../data/bills/*.json", { eager: true })).map(
  (module) => BillSchema.parse(module.default),
);

export interface SearchEntry {
  id: string;
  title: string;
  chamber: string;
  status: "active" | "passed";
  stage: string;
  text: string; // lower-cased haystack, accents stripped
}

// The same folding the page applies to the query: lower case, no accents,
// single spaces. Searching "ushuru" or "Ushuru" or "USHURU" finds the same.
export function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/\s+/g, " ").trim();
}

// Citizens search by the names of institutions, often by their initials
// ("HELB", "KUCCPS", "KRA"), which the explanations rarely spell out. So the
// index also carries every multi-word proper name in the legal text, plus its
// initials: "Higher Education Loans Board" adds "HELB". Small words inside a
// name ("of", "and", "the") are kept in the name but skipped in the initials.
const SMALL = new Set(["of", "and", "the", "for", "in", "on"]);
const NAME = /\b[A-Z][a-z]+(?:\s+(?:(?:of|and|the|for|in|on)\s+)*[A-Z][a-z]+)+/g;

function namesAndInitials(legalText: string): string[] {
  const found = new Set<string>();
  for (const match of legalText.matchAll(NAME)) {
    const name = match[0].replace(/\s+/g, " ");
    const initials = name
      .split(" ")
      .filter((word) => !SMALL.has(word))
      .map((word) => word[0])
      .join("");
    found.add(name);
    if (initials.length >= 3) found.add(initials);
  }
  return [...found];
}

export const GET: APIRoute = () => {
  const entries: SearchEntry[] = bills.map((bill) => {
    const parts = [
      bill.title,
      bill.stage,
      ...(bill.summary ? [bill.summary.text.en, bill.summary.text.sw, bill.summary.text.sheng] : []),
      ...bill.groups.flatMap((entry) => [entry.text.en, entry.text.sw, entry.text.sheng]),
      ...bill.clauses.map((clause) => clause.heading),
      ...namesAndInitials(bill.clauses.map((clause) => clause.text).join(" ")),
    ];
    return {
      id: bill.id,
      title: bill.title,
      chamber: bill.chamber,
      status: bill.status,
      stage: bill.stage,
      text: fold(parts.join(" ")),
    };
  });
  return new Response(JSON.stringify(entries), { headers: { "content-type": "application/json" } });
};
