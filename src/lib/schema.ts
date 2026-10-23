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
    // Fingerprint of the content when the status was set (models.py twin);
    // "Reviewed" shows only while it matches. See src/lib/reviewHash.ts.
    sha256: z.string().nullable(),
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
    // True when the file is a scan with NO text layer, read by OCR (slice 8):
    // it is the "after" of a comparison, never the source of the clauses.
    // Absent in the JSON (models.py leaves it out) unless true.
    ocr: z.boolean().default(false),
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

// The civic blocks (devpost/spec.md > Civic Data), written by
// `politricks civic` (pipeline/politricks/civic.py) from the hand-checked
// YAML in pipeline/civic/. Facts are copied from the YAML; only the Tri
// texts are Claude's translations, reviewed like any other draft.
const ChannelSchema = z
  .object({ type: z.enum(["email", "portal", "venue"]), value: z.string() })
  .strict();

export const ParticipationSchema = z
  .object({
    open: z.boolean(),
    deadline: z.string().nullable(), // ISO date
    deadline_time: z.string().nullable(), // "5pm"
    channels: z.array(ChannelSchema),
    requirements: TriSchema,
    source_url: z.string(),
    checked_at: z.string(), // ISO date
    sheng_swaps: ShengSwaps,
    review: ReviewSchema,
  })
  .strict();
export type Participation = z.infer<typeof ParticipationSchema>;

export const StageNextSchema = z
  .object({ text: TriSchema, source_url: z.string(), sheng_swaps: ShengSwaps, review: ReviewSchema })
  .strict();
export type StageNext = z.infer<typeof StageNextSchema>;

const AvenueSchema = z.object({ name: TriSchema, text: TriSchema, source_url: z.string() }).strict();
export const ChallengeSchema = z
  .object({
    avenues: z.array(AvenueSchema).min(1),
    checked_at: z.string(),
    sheng_swaps: ShengSwaps,
    review: ReviewSchema,
  })
  .strict();
export type Challenge = z.infer<typeof ChallengeSchema>;

// One difference between two versions of a bill (devpost/spec.md > Data Model,
// `changes[]`), written by `politricks compare` (pipeline/politricks/compare.py,
// models.py twin: Change). `before` is the earlier version's legal text word for
// word; `after` the later one's, and when `ocr` is true it was READ BY OCR from a
// scan, so ChangeRow labels it on every row and links the PDF. A clause only the
// later version has is `before: null` ("added"); one only the earlier has is
// `after: null` ("dropped"). Only `note` is Claude's; it is a draft until reviewed.
export const ChangeSchema = z
  .object({
    kind: z.enum(["draft", "current_law", "act"]),
    // The earlier version's clause id ("c7"), or "added:<number>".
    clause_id: z.string(),
    heading: z.string(),
    before_number: z.string().nullable(),
    after_number: z.string().nullable(),
    before: z.string().nullable(),
    after: z.string().nullable(),
    note: TriSchema,
    review: ReviewSchema,
    source_label: z.string(),
    ocr: z.boolean(),
    after_pages: z.array(z.number()),
    sheng_swaps: ShengSwaps,
  })
  .strict()
  .refine((change) => change.before !== null || change.after !== null, {
    message: "a change needs a before or an after text",
  })
  .refine((change) => !change.ocr || change.after !== null, { message: "ocr: true needs an after text" });
export type Change = z.infer<typeof ChangeSchema>;

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
    stage_next: StageNextSchema.nullable(),
    changes: z.array(ChangeSchema),
    participation: ParticipationSchema.nullable(),
    challenge: ChallengeSchema.nullable(),
  })
  .strict();
export type Bill = z.infer<typeof BillSchema>;

// The reader's profile, kept in this browser only (localStorage `pt.profile`,
// devpost/spec.md > Data Model). It has no Python twin: the pipeline never
// sees it. An unparseable profile, or one with another `v`, counts as absent,
// which sends the reader back to /welcome.
export const ProfileSchema = z
  .object({
    v: z.literal(1),
    groups: z.array(GroupIdSchema),
    words: z.string().max(80), // already cleaned by src/lib/guard.ts; "" = none
    skipped: z.boolean(),
    saved_at: z.string(),
  })
  .strict();
export type Profile = z.infer<typeof ProfileSchema>;

// The Constitution of Kenya, 2010, split into its 264 articles by
// `politricks constitution` (pipeline/politricks/constitution.py, whose
// Pydantic models are this file's twin). A bill page ships only the articles
// its clauses suggest (devpost/spec.md > Bill Page, Payload).
export const ArticleSchema = z
  .object({ number: z.number().int(), chapter: z.string(), title: z.string(), text: z.string() })
  .strict();
export type Article = z.infer<typeof ArticleSchema>;

export const ConstitutionSchema = z
  .object({
    sha256: z.string(),
    source_url: z.string(),
    fetched_at: z.string(),
    articles: z.array(ArticleSchema).length(264),
  })
  .strict();
export type Constitution = z.infer<typeof ConstitutionSchema>;
