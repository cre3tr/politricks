// The shape of a bill file (`src/data/bills/<bill-id>.json`), in Zod.
//
// This is the TypeScript twin of `pipeline/politricks/models.py` (Pydantic).
// The two must describe the same shapes — if you change a field here, change
// it there too. `pipeline/politricks/models.py` is the twin file; this
// comment is the pointer the other direction.
//
// `.strict()` on every object schema is the Zod equivalent of Pydantic's
// `extra="forbid"`: an unexpected field in a bill JSON is a build error, not
// something silently dropped. See devpost/spec.md > Data Model.
import { z } from "zod";

export const GroupIdSchema = z.enum(["employee", "trader", "transport", "student"]);
export type GroupId = z.infer<typeof GroupIdSchema>;

// The same text in English, Kiswahili and Sheng.
export const TriSchema = z
  .object({
    en: z.string(),
    sw: z.string(),
    sheng: z.string(),
  })
  .strict();
export type Tri = z.infer<typeof TriSchema>;

// Who has checked this text. "sample" = hand-written placeholder (slice 2a),
// and must never be shown as "AI draft" or "Reviewed" — see ClauseCard.astro.
export const ReviewSchema = z
  .object({
    status: z.enum(["draft", "reviewed", "sample"]),
    at: z.string().nullable(),
  })
  .strict();
export type Review = z.infer<typeof ReviewSchema>;

// Claude's own flag for whether its explanation stayed neutral.
export const NeutralCheckSchema = z
  .object({
    ok: z.boolean(),
    note: z.string(),
  })
  .strict();
export type NeutralCheck = z.infer<typeof NeutralCheckSchema>;

// A word Claude's Sheng kept in pure Kiswahili, with the everyday English it
// could use instead (models.py twin: ShengSwap). A review aid only: the site
// never shows it. Defaults to [] so bill files written before the field
// existed still validate.
export const ShengSwapSchema = z
  .object({
    word: z.string(),
    english: z.string(),
  })
  .strict();
const ShengSwaps = z.array(ShengSwapSchema).default([]);

// One fingerprinted official document behind a bill.
export const SourceSchema = z
  .object({
    label: z.string(),
    url: z.string(),
    official: z.boolean(),
    fetched_at: z.string(),
    sha256: z.string(),
    file: z.string(),
    // True when the official file's text came from a scan (see models.py).
    scanned: z.boolean(),
  })
  .strict();
export type Source = z.infer<typeof SourceSchema>;

// One numbered clause of the bill, copied word for word from the PDF.
export const ClauseSchema = z
  .object({
    id: z.string(),
    number: z.string(),
    heading: z.string(),
    // "margin" = read from the PDF's marginal note; "transcribed" = typed by
    // hand from the printed page because the scan garbled it (models.py twin).
    heading_source: z.enum(["margin", "transcribed"]),
    text: z.string(),
    text_sha256: z.string(),
    pages: z.array(z.number()),
    explanation: TriSchema.nullable(),
    review: ReviewSchema,
    neutral_check: NeutralCheckSchema.nullable(),
    groups: z.array(GroupIdSchema),
    suggested_articles: z.array(z.number()),
    sheng_swaps: ShengSwaps,
  })
  .strict();
export type Clause = z.infer<typeof ClauseSchema>;

export const SummarySchema = z
  .object({
    text: TriSchema,
    review: ReviewSchema,
    sheng_swaps: ShengSwaps,
  })
  .strict();
export type Summary = z.infer<typeof SummarySchema>;

export const GroupEntrySchema = z
  .object({
    group: GroupIdSchema,
    text: TriSchema,
    clause_ids: z.array(z.string()),
    review: ReviewSchema,
    sheng_swaps: ShengSwaps,
  })
  .strict();
export type GroupEntry = z.infer<typeof GroupEntrySchema>;

// `stage_next`, `changes[]`, `participation` and `challenge` are filled by
// later slices (compare, civic data — slices 6/8). They're typed loosely
// here (nullable "any object" / an open array) on purpose: models.py leaves
// them as plain `dict`/`list[dict]` for the same reason, and slice 2a has no
// data for them yet. Tightening these is slice 6/8's job, not this one's.
export const BillSchema = z
  .object({
    id: z.string(),
    title: z.string(),
    chamber: z.enum(["national", "senate", "county"]),
    kind: z.enum(["bill", "motion"]),
    status: z.enum(["active", "passed"]),
    stage: z.string(),
    stage_checked_at: z.string(),
    source_changed: z.boolean(),
    sources: z.array(SourceSchema),
    summary: SummarySchema.nullable(),
    groups: z.array(GroupEntrySchema),
    clauses: z.array(ClauseSchema),
    stage_next: z.record(z.string(), z.unknown()).nullable(),
    changes: z.array(z.record(z.string(), z.unknown())),
    participation: z.record(z.string(), z.unknown()).nullable(),
    challenge: z.record(z.string(), z.unknown()).nullable(),
  })
  .strict();
export type Bill = z.infer<typeof BillSchema>;
