---
doc: spec
status: approved
---

# PoliTricks — Technical Spec

## How This Works, In Plain Language

PoliTricks has two halves, a **workshop** and a **shop window**.

**The workshop is the pipeline**, a set of Python scripts you run on your own machine. For each bill:
- It **downloads the official PDF** and **fingerprints** it. The fingerprint is a SHA-256 hash; change one comma in the file and the fingerprint changes completely.
- It **pulls out the clauses**, checking that every clause is copied *word for word* from the PDF.
- It asks **Claude** to write the plain-language explanation of each clause in English, Kiswahili and Sheng, tag who it affects, and suggest constitution articles.
- **Facts that must never be wrong** (deadlines, where to submit, how to challenge a law) **you type in by hand** from the official notice. Claude only translates them.
- Everything lands in **one JSON file per bill**, a plain text file you can open and read. You review it (the Sheng especially), mark what's reviewed, and commit it. That committed file is the product's truth.

**The shop window is the website**, built with **Astro**. Astro takes those JSON files and turns them into ready-made pages *before* anyone visits. A phone on cheap data downloads a finished page, not a heavy app. The site uses only a little JavaScript, for the parts that need it: the language toggle, the "I am…" chips, the tabs and search.

**Two small live helpers** run on Vercel for the moments that can't be prepared in advance:
- **Match**: takes someone's own words ("I sell mitumba Gikomba") and asks Claude which clauses matter to them.
- **Explain**: writes an explanation for a clause that doesn't have one yet. The page labels it *"AI draft — not yet reviewed"*.

**The reader's choices** (who they are, which language) are kept in the browser's `localStorage`, a sticky note the browser keeps for one site. There are no accounts and no database.

**Why this shape:**
- Your trust rules say every explanation sits beside its source, the Sheng gets reviewed, and every source text is verifiably official. Those only work if content is made **before** readers see it, not on the fly.
- Precomputing also makes the site fast and cheap. It also means two readers of the same clause always see the same words.

## The Core Journey Through the System
PRD ref: `prd.md > The Core Journey`.

1. **First visit.** A tiny script in the page `<head>` finds no `pt.profile` sticky note and sends the reader to `/welcome` before the home page is drawn. They tap groups and type a few words. The words are cleaned *before* saving, and the browser saves `pt.profile` and returns them home.
2. **Home.** Astro built this page ahead of time from every bill JSON: Active Bills, plus the Una'tafuta? search box. The search looks through a small index file in the browser, with no server call.
3. **Pick a bill.** `/bills/<id>` is a prebuilt page containing the bill's clauses, explanations in all three languages, and its sources. Before the page is drawn, the same `<head>` script reads `pt.profile` and `pt.lang` and sets attributes on `<html>`. CSS then shows the right language and puts their groups first **on the first paint**, with no visible re-shuffle.
4. **What it does.** Their groups come first, because the clauses were tagged in the pipeline. **"See everyone"** expands to all groups, and every clause is always reachable there. If they wrote their own words, the page calls **Match** once, floats up the returned clauses, and saves the answer on the sticky note so it doesn't ask again.
5. **A clause without an explanation.** The card calls **Explain** and shows the result labelled *AI draft — not yet reviewed*, saved on the device. The next pipeline run replaces it with a reviewed version.
6. **What changed.** This is prebuilt comparison data from the pipeline: draft vs draft, bill vs current law, or bill vs Act.
7. **Have your say.** Prebuilt from **your hand-entered civic data**: deadline, channels, requirements, each with its official source link and a **last checked** date. When participation isn't open, it shows the stage and what usually happens next.
8. **Staying current.** A scheduled GitHub Action re-runs intake and verification weekly. If anything changed, whether a new bill, a new draft or an official PDF whose fingerprint no longer matches, it opens a **pull request** for you to review. Nothing publishes without you.

## Stack

| Piece | Choice | Why (learner decision unless marked) | Docs |
|---|---|---|---|
| Site | **Astro 7.x** (TypeScript) | Your **new tool** for the learning goal. Static-first, ships almost no JS, suits mobile and cheap data. | [docs.astro.build](https://docs.astro.build) |
| Hosting adapter | `@astrojs/vercel` 11.x | You chose Vercel. Static pages plus two on-demand API routes. | [Astro × Vercel](https://docs.astro.build/en/guides/integrations-guide/vercel/), [Vercel × Astro](https://vercel.com/docs/frameworks/frontend/astro) |
| Styling | **Tailwind CSS v4** via `@tailwindcss/vite` | *Derived:* already familiar to you, which keeps Astro the only new thing. | [tailwindcss.com](https://tailwindcss.com/docs) |
| Interactivity | Plain TypeScript `<script>` in Astro components (no React) | *Derived:* the least JavaScript over the wire, and it teaches Astro's model directly. You can switch to React islands if you prefer. | [Astro scripts](https://docs.astro.build/en/guides/client-side-scripts/) |
| Pipeline | **Python 3.12+**, managed with **uv** | You chose Python for its stronger PDF tooling. | [docs.astral.sh/uv](https://docs.astral.sh/uv/) |
| PDF text | **pdfplumber** | *Derived:* MIT licence, good text-position access. PyMuPDF was avoided because it's AGPL. | [pdfplumber](https://github.com/jsvine/pdfplumber) |
| Schemas | **Pydantic v2** (Python), **Zod** (TS) | *Derived:* the same shapes are validated on both sides, and they plug into Claude structured outputs. | [Pydantic](https://docs.pydantic.dev), [Zod](https://zod.dev) |
| AI | Anthropic SDKs: `anthropic` (Python), `@anthropic-ai/sdk` (TS) | Claude does the explaining, tagging, translating, matching and article suggestions. | [Messages API](https://platform.claude.com/docs/en/api/messages), [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md) |
| Model | **`claude-sonnet-5-5`** for pipeline and live calls | *Your decision, 2026-10-13:* Sonnet 5.5 rather than Opus 5.5 or Fable 5.1, for keeping closely to the brief (explain, never editorialise). It is also the cheapest of the three ($2/$10 per million tokens). Downgrading the live calls to `claude-haiku-5-5` for cost is your decision (see Decisions). | [Pricing](https://platform.claude.com/docs/en/about-claude/pricing.md) |
| Scheduled updates | GitHub Actions (weekly cron) + `peter-evans/create-pull-request` | You chose manual intake for setup and scheduled runs for updates. | [Actions cron](https://docs.github.com/actions/using-workflows/events-that-trigger-workflows#schedule), [create-pull-request](https://github.com/peter-evans/create-pull-request) |

**To confirm in build step 0 (don't assume):** that the exact current versions of Astro, the adapter and Tailwind install cleanly together, and that the model IDs answer. Make one tiny `claude-sonnet-5-5` call and read back a non-empty reply.

## Where It Runs and How Someone Tries It

**Requirements:** Node 22+, Python 3.12+, `uv`, and an **Anthropic API key**. You need to create the key at [console.anthropic.com](https://console.anthropic.com) (build step 0). Put it in `.env` as `ANTHROPIC_API_KEY=` (git-ignored; `.env.example` shows the name only), and in the Vercel project's environment variables and the GitHub repo's Actions secrets. Never paste it in chat. **Set a monthly spend limit in the Anthropic console**, because it's the real backstop for the public live helpers.

**Run the site locally:**
```bash
npm install
uv sync --project pipeline
npm run dev          # → open http://localhost:4321
```

**Pipeline (workshop) — the CLI is `politricks`:**
```bash
# one-time
uv run --project pipeline politricks constitution          # fetch + fingerprint + split the Constitution → src/data/constitution.json
# first load of every bill in sources.yaml (the manual intake command)
uv run --project pipeline politricks intake                # fetch → extract → generate for each registry entry
# per bill, or to re-run one stage
uv run --project pipeline politricks fetch <bill-id>       # download + fingerprint
uv run --project pipeline politricks extract <bill-id>     # clauses, verbatim-checked
uv run --project pipeline politricks generate <bill-id>    # Claude → JSON (Batches API)
uv run --project pipeline politricks compare <bill-id>     # build changes[] (drafts / current law / Act)
uv run --project pipeline politricks civic <bill-id>       # validate your civic YAML, translate to SW/Sheng
uv run --project pipeline politricks lint <bill-id>        # banned-phrase + neutrality + schema checks
uv run --project pipeline politricks review <bill-id>      # walk every draft text, mark reviewed
# maintenance (what the weekly Action runs)
uv run --project pipeline politricks intake --check        # also look at Parliament/Gazette/County listings for new items
uv run --project pipeline politricks verify                # re-download all sources, compare fingerprints
```

**Demo recording (required):** run `npm run dev`, or use the Vercel URL. Walk the minimum demoable line: welcome → home → the passed **Finance Act 2026** page → a clause in **Sheng** beside its legal text → toggle languages → the source fingerprint → an active education bill's **Have your say** tab.

**Deployment (your choice: Vercel):** connect the GitHub repo in Vercel and set `ANTHROPIC_API_KEY`. Each push to `main` deploys. It's free-tier sized.

**Submission:** a public GitHub repo plus the short demo video. The deployed URL is a bonus, not a substitute.

## Look and Feel
Carries `prd.md > Look and Feel` and `scope.md > Inspiration & Identity`.

- **Base: civic-tech clean.** Light background, generous line height (1.6), a 16px+ body, one column on mobile, and nothing decorative that costs data.
- **Street/mtaa energy** in the headers and labels: a condensed, poster-like display face (**Anton**, self-hosted, subset to Latin, `font-display: swap` so text shows immediately in the fallback font) for bill titles, tab labels and the "PoliTricks" wordmark, with uppercase tracking on labels. The body uses the system font stack, which costs zero bytes.
- **Palette tokens** (CSS variables in `global.css`):
  - `--flag-black #111111`, `--flag-red #BB0000`, `--flag-green #006600`, `--paper #FFFFFF`, `--matatu-brown #6B3F22`, `--muted #5F5A52`.
  - **No orange or yellow as dominant colours**, for neutrality.
- **Matatu highlighter:** key legal phrases get a thick lower-band mark. **Brown/red** (`--matatu-brown` band with a `--flag-red` underline) marks *what changes*, and **brown/black** marks *amounts and dates*. The marks are never colour-only: marked text is also **bold**, so the meaning survives sunlight glare and colour-blindness. Contrast is checked at build (WCAG AA for text).
- **Copy tone:** plain, direct and neutral, with Sheng for flavour but never mockery. UI labels are trilingual where they're short ("Una'tafuta?", "Hatujapata kitu").

## Components

### Source Pipeline
Implements `prd.md > Automatic Bill Intake`, `prd.md > What It Does` (content), `prd.md > What Changed` (content), `prd.md > Have Your Say` (content), `prd.md > Constitution Check` (content). Python package in `pipeline/`, CLI name `politricks`.

#### Fetch & Fingerprint
- Reads the registry `pipeline/sources.yaml`, which lists each bill's official URLs.
- Downloads each PDF to `sources/<bill-id>/`, then computes the **SHA-256 of the raw file bytes**. That's what a reader can re-verify.
- Records `{url, fetched_at, sha256, file}` in the bill's `sources[]`.
- **Official-domain allowlist** for anything marked `official: true`: `parliament.go.ke`, `kenyalaw.org` (which includes the Kenya Gazette archive), and the Nairobi City County Assembly's official site. **Its exact domain is [UNKNOWN] and gets pinned in step 0.** A third-party mirror can never become a hashed source.

#### Extract Clauses
- pdfplumber turns the text into clauses. This is a rule-based split on clause numbering, with `pipeline/overrides/<bill-id>.yaml` for manual fixes (split points, column order).
- **One `normalise()` function** in `hashing.py` is used everywhere: Unicode NFC, line-break hyphens joined ("amend-\ned" → "amended"), whitespace collapsed.
- **Verbatim check:** `normalise(clause.text)` must appear in `normalise(full_pdf_text)`, or the run fails and names the clause.
- **`text_sha256` = SHA-256 of `normalise(clause.text)`** encoded as UTF-8. The stored `text` *is* the normalised text, so the displayed text and its fingerprint always agree.
- **No OCR.** A PDF with no extractable text fails loudly with "scanned PDF — not supported yet".

#### Generate (Claude)
- Uses the **Message Batches API** (50% cheaper, not time-sensitive) with `claude-sonnet-5-5`, adaptive thinking and **structured outputs**. `client.messages.parse` validates the response against a Pydantic model.
- Per clause, it returns: `explanation {en, sw, sheng}`, `groups[]` (from the fixed four), `suggested_articles[]` (article numbers only), `neutral_check: {ok: bool, note}`.
- Per bill, it returns `summary {en, sw, sheng}` and group entries `{group, text {en,sw,sheng}, clause_ids[]}`.
- **System prompt rules** live in `pipeline/prompts/explainer.md`, and the live Explain API loads the same file. Say what the clause does, never whether it's good or bad. **Never state or imply that anything is unconstitutional, illegal or invalid.** No advice. Sheng for everyday clarity, never mockery.
- **Suggested article numbers are validated** against `constitution.json`. Unknown numbers are dropped, never shown.
- **Batch requests use `output_config: {format: {type: "json_schema", schema}}`**, and results are validated with the Pydantic model's `model_validate_json`. `messages.parse` isn't available inside a batch.
- **Refusal fallback:** server-side `fallbacks` are **rejected on the Batches API**, so batch items carry none. An item that comes back with `stop_reason: "refusal"` is retried **directly** via `messages.parse`, with `fallbacks: "default"` and beta `server-side-fallback-2026-07-01`. `stop_reason` is always checked before content is read.
- `politricks generate --self-test` checks request building and result parsing offline against a recorded fixture.
- Everything it writes gets `review.status: "draft"`: clause explanations, the summary, and group entries.

#### Compare
- Implements `prd.md > What Changed`: it builds `changes[]` from two versions (draft vs draft, or bill vs Act) by matching clause numbers.
- A clause present in one version and absent in the other gets `before: null` or `after: null`, which covers "what was dropped".
- **Bill vs current law** works by pulling the amended section of the Act it names (for example, "Section 12 of the Excise Duty Act") from that Act's fingerprinted Kenya Law PDF, with the same verbatim rule.
- Claude writes a note per change. The note is a draft and gets reviewed.

#### Civic Data
Implements `prd.md > Have Your Say` (content). **Your decision: hand-entered facts, AI translation only.**
- You write `pipeline/civic/<bill-id>.yaml` from the official notice. It holds `participation` (open, deadline, channels, requirements, source_url, checked_at), `stage_next` (English text + source_url), and for passed bills, `challenge.avenues` (English text + source_url each).
- `politricks civic <bill-id>` validates the YAML, including that **every fact has a `source_url`**, then asks Claude to **translate** (not write) the English text into SW and Sheng. Translations land as drafts and get reviewed.
- Challenge avenues are general information only. The prompt forbids advice or any prediction of outcome.

#### Lint
- Implements the doctrine as a check that fails the run, not a hope.
- `politricks lint` scans **every generated string** for banned phrases: "unconstitutional", "kinyume cha katiba", "haramu", "illegal", "invalid", "you should", "vote", with the list kept in `pipeline/prompts/banned.txt`. It also lists every clause whose `neutral_check.ok` is false, and schema-validates the bill JSON.
- `review` refuses to mark a bill done while lint has failures.

#### Review
- An interactive terminal walk through **every draft text**: clause explanations, summary, group entries, change notes and civic translations. Items flagged by `neutral_check` or lint come first.
- For each: accept, edit, or skip. It sets `review {status: "reviewed", at, sha256}`. This is your Sheng gate.
- `sha256` fingerprints what you accepted: the three texts, plus a clause's legal text and tags or a group's section list (`models.current_sha256`, twin `src/lib/reviewHash.ts`). The page shows *Reviewed* only while it still matches; anything changed afterwards shows *Changed since review* and lint flags it until you accept it again. An edit you don't accept is a hand-edited draft, which `generate` never overwrites or pays for again (decided 2026-10-14).

#### Verify
- Re-downloads every official source and compares fingerprints, printing `unchanged` / `CHANGED` / `unreachable` per source.
- A changed fingerprint marks that bill `source_changed: true` until you re-run and review.
- **Positive control:** `verify --self-test` fingerprints a temp copy with one byte altered and must report `CHANGED`.

#### Constitution Loader
- `politricks constitution`, run once: Constitution of Kenya 2010 from Kenya Law, fingerprinted, then split into `src/data/constitution.json` (`{number, chapter, title, text}` per article).

#### Intake
- `politricks intake` (the manual first load) runs fetch → extract → generate for every `sources.yaml` entry that has no bill JSON yet.
- `--check` (weekly) also reads the Parliament bill listings, the Kenya Gazette index and the County Assembly listings. **It only proposes** new entries as a `sources.yaml` diff in the PR; you confirm them. Listing-page structures are **[UNKNOWN]**.

### Astro Site
Pages are prebuilt from `src/data/`.

#### Head Script (`src/components/HeadScript.astro`)
- An inline, blocking `<head>` script of a few hundred bytes. It reads `pt.profile` and `pt.lang` (in `try/catch`; missing storage means defaults).
- It sets `<html data-lang="sheng" data-groups="trader student">` **before first paint**, or redirects to `/welcome` if the profile is absent and this isn't `/welcome`.
- CSS uses those attributes to show the right language and to put matching group blocks first with `order`. This prevents a visible re-shuffle.

#### Welcome Page (`/welcome`)
Implements `prd.md > First-Visit Profile`.
- Four group chips plus a free-text field (max **80 characters**, with a live counter).
- One line explains that *"your words are sent to an AI service to find matching clauses"* (a data-protection courtesy). There's a Skip button.
- Words are cleaned with the **same rules as the API guard** (`src/lib/guard.ts`, shared) before saving. If nothing usable remains, the words are saved as empty, which counts as skipped.
- On submit or skip, the page writes `pt.profile` and goes to `/`.

#### Home Page (`/`)
Implements `prd.md > Home: Active Bills and Una'tafuta?`.
- **Active Bills** list: title, chamber and stage, with a *last checked* date. A **deadline countdown only when `participation.open`**; otherwise "Participation not open".
- **Una'tafuta?** search box filters `search-index.json` (titles, keywords, summaries in all three languages) in the browser. With no results it shows *Hatujapata kitu* in the current language, with a link back.
- Passed bills appear in search results with a **Passed** label.

#### Bill Page (`/bills/[id]`)
Implements `prd.md > What It Does`, `prd.md > What Changed`, `prd.md > Have Your Say`.
- Title, stage, an **Active/Passed** label and a **Source fingerprint** footer: URL, fetched date, the SHA-256 in short form expandable to full, and "verify it yourself" steps (`certutil -hashfile <file> SHA256` on Windows, `shasum -a 256 <file>` on macOS/Linux).
- Three tabs:
  - **What it does:** your groups, then **"See everyone"** (expands all four groups plus an "all clauses" list, so nothing is hidden by the profile), then the summary, then clause cards.
  - **What changed:** from `changes[]`, or the honest empty note.
  - **Have your say:** from `participation`, or `stage_next` when participation isn't open. For a passed bill it becomes **Where it can still be challenged**, from `challenge`.
- **Per-bill chips** (`ProfileChips`) change which groups lead on this page view only. `pt.profile` is not changed.
- If `source_changed: true`, a banner says *"The official document changed since we last checked. Explanations may be out of date."*
- **Payload:** the page includes only the constitution articles this bill suggests, sliced at build. The full `constitution.json` never ships to a phone.

#### Clause Card
Implements `prd.md > What It Does`, `prd.md > Language Switching`, `prd.md > Constitution Check`.
- The legal text, with highlighter marks, sits **beside** (on desktop) or **directly above** (on mobile) the explanation.
- Per-card EN/SW/Sheng flip, plus a review badge (*Reviewed* / *AI draft (not yet reviewed)* / *Sample text (placeholder)* / *Changed since review*). One exhaustive switch picks the label, so a sample can never show either of the other two.
- "Suggested articles" chips open the Article Sheet.
- If there's no explanation, it calls the **Explain API**.

#### Article Sheet
Implements `prd.md > Constitution Check`.
- A bottom sheet showing the article's full text from the page's sliced articles, labelled **"Suggested article — you judge"**.
- It links to Kenya Law's official copy and shows the constitution's fingerprint.

#### Language Toggle
Implements `prd.md > Language Switching`. A global switch in the header writes `pt.lang` and updates `<html data-lang>`. Cards follow via CSS, except those flipped individually, which get their own `data-lang` on the card. The legal text never changes.

#### Motion Card
Implements `prd.md > Automatic Bill Intake` (county motions). Resolution text plus a trilingual note and source fingerprint. It has no tabs.

### Live API
Astro on-demand routes (`export const prerender = false`), run as Vercel functions. They share `src/lib/guard.ts` (input cleaning) and `src/lib/claude.ts` (client, prompts).

#### Match API (`POST /api/match`)
Implements `prd.md > First-Visit Profile` (free-text matching).
- **Request:** `{bill_id: string, words: string}`.
- **Guard:** strip control characters and markup, collapse whitespace, max 80 characters.
- **Call:** `claude-sonnet-5-5` via `client.messages.parse` with a Zod schema. The prompt contains each clause's ID and English explanation, plus the reader's words **inside a quoted data block, labelled as data, never as instructions**.
- **Responses:**
  - `200 {clause_ids: string[]}`: at most 8, filtered to IDs that exist in the bill.
  - `400 {error: "empty" | "unknown_bill"}`.
  - `502 {error: "upstream"}` on an API error or refusal.
- **Fallback is client-side:** on any non-200 response or a 15-second timeout (`AbortController`), the page keeps the fixed-group ordering and shows a quiet "couldn't personalise right now".
- The browser caches 200s under `pt.match.<bill_id>.<hex>`. The hex is the first 16 characters of SHA-256 of the cleaned words, via `crypto.subtle.digest` (async, awaited before storage).

#### Explain API (`POST /api/explain`)
Implements `prd.md > States and Boundaries` (explanation unavailable).
- **Request:** `{bill_id, clause_id, lang: "en"|"sw"|"sheng"}`.
- **Responses:**
  - `200 {text, label: "AI draft — not yet reviewed"}`.
  - `409 {error: "already_explained"}`.
  - `400 {error: "unknown_clause"}`.
  - `502 {error: "upstream"}`.
- It uses the same `explainer.md` prompt and banned-phrase list as the pipeline. A generated text containing a banned phrase is **discarded**, and the endpoint returns 502.
- **Fallback is client-side:** on a non-200 response or timeout, the card shows *explanation not ready*. The browser caches 200s under `pt.draft.<clause_id>.<lang>`.

### Scheduled Update (GitHub Action)
Implements `prd.md > Automatic Bill Intake` (maintenance). `.github/workflows/update.yml`:
- `on: schedule` (weekly) plus `workflow_dispatch`.
- `permissions: {contents: write, pull-requests: write}`. **Repo setting required:** Settings → Actions → General → *Allow GitHub Actions to create and approve pull requests*. Without it, PR creation fails.
- Runs `intake --check` → `verify` → `generate` on new material → `lint`, then `peter-evans/create-pull-request` with the label *needs review*. It never auto-merges. It needs the `ANTHROPIC_API_KEY` secret.

## Data Model

**Bill file** — `src/data/bills/<bill-id>.json`, validated by `pipeline/politricks/models.py` (Pydantic) and `src/lib/schema.ts` (Zod):
```text
Review   = { status: "draft" | "reviewed" | "sample", at: ISO-date | null, sha256: string | null }   // "sample" = hand-written placeholder (slice 2a), never shown as AI draft or Reviewed; sha256 = fingerprint of the content when the status was set ("hand-edited" after a review-walk edit)
Tri      = { en: string, sw: string, sheng: string }

Bill {
  id, title, chamber: "national" | "senate" | "county",
  kind: "bill" | "motion", status: "active" | "passed",
  stage: string, stage_checked_at: ISO-date,
  stage_next: { text: Tri, source_url, review: Review } | null,   // shown when participation isn't open
  source_changed: bool,
  sources: [{ label, url, official: bool, fetched_at, sha256, file, scanned: bool /* text layer came from a scan */ }],
  summary: { text: Tri, review: Review },
  groups: [{ group: "employee"|"trader"|"transport"|"student", text: Tri, clause_ids: string[], review: Review }],
  clauses: [{ id, number, heading, heading_source: "margin" | "transcribed" /* marginal note, or hand-typed from the printed page when a scan garbles it */, text /* normalised */, text_sha256, pages: number[] /* PDF pages it spans */,
              explanation: Tri | null, review: Review,
              neutral_check: { ok: bool, note: string },
              groups: string[], suggested_articles: number[] }],
  changes: [{ kind: "draft"|"current_law"|"act", clause_id, before: string | null, after: string | null,
              note: Tri, review: Review, source_label }],
  participation: { open: bool, deadline: ISO-date | null, channels: [{type: "email"|"portal"|"venue", value}],
                   requirements: Tri, source_url, checked_at, review: Review } | null,
  challenge: { avenues: [{ name, text: Tri, source_url }], checked_at, review: Review } | null   // passed bills only
}
```
**Motion** uses the same file with `kind: "motion"`: `clauses` holds one entry (the resolution text), and `changes`, `participation` and `challenge` are empty or null.

**Civic YAML** — `pipeline/civic/<bill-id>.yaml`: the hand-entered English facts behind `participation`, `stage_next` and `challenge`. The pipeline fills in the translations.

**Constitution** — `src/data/constitution.json`: `{sha256, source_url, fetched_at, articles: [{number, chapter, title, text}]}`. Pages import only their suggested slice.

**Search index** — `src/data/search-index.json` is generated at site build from the bill files.

**Reader profile** — `localStorage` key `pt.profile`, validated by `ProfileSchema` in `src/lib/schema.ts`:
```text
Profile { v: 1, groups: ("employee"|"trader"|"transport"|"student")[], words: string /* cleaned, ≤80 chars, "" = none */, skipped: bool, saved_at: ISO-date }
```
If the profile is unparseable or has the wrong `v`, it's treated as absent, which means the reader goes to `/welcome`.

| Data | Lives in | Updated by | When the reader leaves and returns |
|---|---|---|---|
| Bills, clauses, explanations, sources | Committed JSON → prebuilt pages | Pipeline, then your review, then commit/PR | Unchanged until the next deploy |
| Civic facts | `pipeline/civic/*.yaml` → bill JSON | You, from official notices | Unchanged until the next deploy |
| Profile | `localStorage` `pt.profile` | Welcome page only | Remembered on this device |
| Per-bill chip choice | Page memory only | Chips on the bill page | Forgotten (by design) |
| Language | `localStorage` `pt.lang` (`"en"|"sw"|"sheng"`) | Global toggle | Remembered |
| Match results | `localStorage` `pt.match.*` | Match API 200 | Reused; no repeat call |
| Live drafts | `localStorage` `pt.draft.*` | Explain API 200 | Reused until a reviewed version ships |
| Official PDFs | `sources/<bill-id>/` in the repo | Fetch | Kept, so anyone can re-hash |

## File Structure

```
politricks/
├── src/                         # Astro site (the shop window)
│   ├── pages/
│   │   ├── index.astro          # Home: Active Bills + Una'tafuta?
│   │   ├── welcome.astro        # First-visit profile
│   │   ├── bills/[id].astro     # Bill page with three tabs
│   │   └── api/
│   │       ├── match.ts         # Match API (live, Vercel function)
│   │       └── explain.ts       # Explain API (live, Vercel function)
│   ├── components/
│   │   ├── HeadScript.astro     # Pre-paint profile/lang attributes + welcome redirect
│   │   ├── Header.astro         # Wordmark + LanguageToggle
│   │   ├── LanguageToggle.astro
│   │   ├── ProfileChips.astro   # "I am…" chips (welcome + per bill)
│   │   ├── GroupsPanel.astro    # Your groups first + "See everyone"
│   │   ├── BillTabs.astro
│   │   ├── ClauseCard.astro
│   │   ├── ArticleSheet.astro
│   │   ├── ChangeRow.astro      # One before → after row (null = added/dropped)
│   │   ├── ParticipationPanel.astro  # Have your say / stage_next / challenge
│   │   ├── MotionCard.astro
│   │   ├── LastChecked.astro
│   │   └── SourceFingerprint.astro
│   ├── layouts/BaseLayout.astro # Shared page shell: head script, fonts, header
│   ├── lib/
│   │   ├── highlight.ts         # Splits legal text into plain / change / amount segments
│   │   ├── schema.ts            # Zod: Bill, Constitution, Profile (mirrors models.py)
│   │   ├── storage.ts           # Safe localStorage get/set (try/catch)
│   │   ├── guard.ts             # Word cleaning, shared by welcome page + APIs
│   │   └── claude.ts            # Anthropic client, loads the shared prompts + banned list
│   ├── data/
│   │   ├── bills/*.json         # Pipeline output: the product's truth
│   │   ├── constitution.json
│   │   └── search-index.json    # Generated at build
│   └── styles/global.css        # Tailwind + palette tokens + highlighter + data-lang/data-groups rules
├── public/fonts/anton-latin.woff2
├── pipeline/                    # Python workshop
│   ├── pyproject.toml
│   ├── sources.yaml             # Registry of bills + official URLs
│   ├── overrides/               # Manual clause-split fixes per bill
│   ├── civic/                   # Your hand-entered participation/challenge facts
│   ├── prompts/
│   │   ├── explainer.md         # Neutral explainer rules (shared with the Explain API)
│   │   ├── matcher.md           # Match rules (words are data)
│   │   ├── translator.md        # Civic translation (translate, never add)
│   │   └── banned.txt           # Banned phrases (shared with the Explain API)
│   ├── samples/                 # Hand-written, labelled sample explanations (slice 2a, until an API key exists)
│   ├── tests/fixtures/          # Recorded batch results for the offline generate self-test
│   └── politricks/
│       ├── cli.py               # constitution / intake / fetch / probe / extract / samples / generate / compare / civic / lint / review / verify
│       ├── models.py            # Pydantic models (mirror schema.ts)
│       ├── fetch.py  extract.py  generate.py  compare.py  civic.py
│       ├── lint.py  review.py  verify.py  intake.py  constitution.py
│       └── hashing.py           # normalise() + SHA-256 of files and clause text
├── sources/<bill-id>/*.pdf      # Fingerprinted official PDFs
├── scripts/check-build.mjs      # Assertions on the built HTML (legal text, badges, fingerprints)
├── .github/workflows/update.yml # Weekly intake + verify → PR
├── astro.config.mjs             # Vercel adapter, Tailwind
├── package.json
├── .env.example                 # ANTHROPIC_API_KEY= (no value)
├── README.md                    # What it is, run steps, how to verify a fingerprint
└── devpost/                     # Planning docs (this folder)
```
`src/lib/claude.ts` reads `pipeline/prompts/*` at build time (bundled into the functions), so the pipeline and the live APIs can never drift apart on rules.

## External Services and Dependencies

### Anthropic Messages API
- **Endpoint:** `POST https://api.anthropic.com/v1/messages`, through the SDKs. Auth is `ANTHROPIC_API_KEY`.
- **Structured output:** `output_config: {format: <schema>}` via `messages.parse` (Python: Pydantic model; TS: `zodOutputFormat(schema)` from `@anthropic-ai/sdk/helpers/zod`).
- **Model:** `claude-sonnet-5-5`, with adaptive thinking. On **direct** calls (live APIs, refusal retries), add `fallbacks: "default"` plus beta `server-side-fallback-2026-07-01`. **Not on batches, which reject it.** Always check `stop_reason` before reading content.
- **Batches (pipeline):** `POST /v1/messages/batches`. The pipeline polls until `ended` and matches results by `custom_id` (= clause ID). Results arrive in any order. That structured outputs and the fallback option are accepted inside batch requests is **to be confirmed with one test batch in step 0 [MEDIUM]**. If not, fall back to plain `messages.parse` calls (full price, still cheap at this scale).
- **Pricing:** Opus 5 is $5 in / $25 out per million tokens, halved on Batches ([pricing](https://platform.claude.com/docs/en/about-claude/pricing.md)).
- **Rough estimate [LOW]:** about **$1–3 per bill** in the pipeline, and **under $0.02 per live Match call**.
- **Docs:** [Messages](https://platform.claude.com/docs/en/api/messages), [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md), [Batches](https://platform.claude.com/docs/en/build-with-claude/batch-processing).

### Parliament of Kenya (parliament.go.ke)
- **Build finding:** Parliament often publishes bills as **scans with no text layer** (the Finance Bill 2026 was 0/124 pages). **Prefer the Kenya Law copy of any bill**, which has had a text layer in every case checked. Use Parliament's copy only if it passes the text-layer probe.
- **Used for:** official bill PDFs, and the listing pages that `intake --check` reads for new bills.
- **No API:** it's plain HTTPS downloads and HTML parsing. The listing structure is **[UNKNOWN]**, so the registry is the reliable path and the scraper only proposes.

### Kenya Law (kenyalaw.org)
- **Used for:** the Constitution of Kenya 2010, the Acts that bills amend (for the current-law comparison), and the **Kenya Gazette** archive of notices.
- **URLs are [UNKNOWN]** and get pinned in build step 0.

### Nairobi City County Assembly
- **Used for:** county bills and motions (step 6).
- **The official domain and document availability are [UNKNOWN]** and get pinned before step 6 (not before step 1).

### Vercel
- Hosts static pages plus two functions. It needs `ANTHROPIC_API_KEY` set as an environment variable.
- **Free tier assumed [MEDIUM].**

### GitHub Actions
- Runs the weekly workflow. It needs the repo secret, the `permissions` block and the PR-creation repo setting (see Scheduled Update).

## Important Failure Modes

- **The PDF is scanned or has no text layer** → the pipeline stops with "scanned PDF — not supported yet". The bill is **not** published. Nothing half-built ships.
- **The verbatim check fails on a real clause** (column order, footnotes, odd hyphenation) → the run stops and names the clause. You fix it with an override entry. The check is never loosened. Expect this on Finance Bill tables.
- **The model changes wording, or writes a banned phrase** → the verbatim check or lint fails the run. The Explain API discards the output.
- **Live API is slow, failing, or has no key** → the client falls back to fixed groups with a quiet note, or to the *explanation not ready* state. There's a 15-second timeout.
- **Participation data goes stale** → *last checked* is always visible. If it's more than 7 days old, the panel adds "may be out of date — check the official notice", linked.
- **An official PDF changes silently** → `verify` flags it, the bill shows the *document changed* banner, and the weekly Action opens a PR.
- **`localStorage` unavailable** (private mode, blocked) → the head script falls back to defaults (English, all groups). The welcome page still works but won't be remembered.
- **Someone spams the public APIs** → length caps, known-ID checks and the **Anthropic console spend limit**, which is the real backstop. A per-IP in-memory limit is best effort only, because serverless instances don't share memory.

## What Was Simplified and Why

- **Precomputed, reviewed JSON** instead of generating everything live. This is what makes your Sheng review and source-beside-summary rules enforceable. The fuller version would need a review queue and a database.
- **Hand-entered civic facts** instead of AI extraction. Deadlines and channels are where a wrong answer costs someone their say. The fuller version would extract with verbatim checks, and still need you.
- **Scheduled PR** instead of auto-publishing updates. A human gate on every change keeps the neutrality and accuracy promises.
- **Registry-first intake**, with the scraper only proposing. Listing pages may change or be inconsistent. A full scraper needs monitoring and repair.
- **`localStorage`** instead of accounts or sync. This is `prd.md > Deferred From the POC`.
- **In-browser search over a small index**, because the corpus is a handful of bills. A search service only makes sense at hundreds.
- **Drafts cached per device** rather than in a server store. That avoids a database, and the pipeline's reviewed version supersedes them anyway.
- **No OCR.** A failed text layer fails loudly. OCR would add a heavy dependency and a new accuracy risk.

## Decisions and Open Issues

### Learner decisions (this session)
- **Active Bills:** *Tertiary Education, Placement and Funding Bill, 2026* and the *Public Participation Bill, 2025 (Senate)*, with the **Finance Act 2026** (Kenya Law, text layer) as the passed, hand-loaded, hashed demo item. *(Build finding: every official Finance Bill 2026 copy is a scan; the learner chose the Act, shown exactly as in the file.)*
- **Shape:** precomputed and reviewed content is the default, and live AI is only a **fallback labelled draft**.
- **New tool: Astro.**
- **Pipeline language: Python.**
- **Intake:** a **manual command for the first load, a scheduled GitHub Action for updates**.
- **Deploy: Vercel.**
- **API key:** you'll create one (build step 0).
- **Civic data:** **hand-entered facts, with AI translation only** (from the cold review).

### Derived implementation details (agent, open to change)
- Tailwind v4, plain-TypeScript islands, pdfplumber, uv, and Anton for display type.
- The pre-paint head script, per-bill constitution slicing and the shared `normalise()`.
- The 80-character free-text cap, `localStorage` keys, 15-second timeouts and the 7-day staleness threshold.
- The `lint` stage with a banned-phrase list, and prompts shared between the pipeline and the live APIs.
- Storing official PDFs in the repo, so anyone can re-hash them. **Redistribution terms are [UNKNOWN].** Government publications are usually fine to share, but check this before making the repo public. The fallback is to keep only the fingerprints and URLs.
- `claude-sonnet-5-5` for all calls, with the refusal fallback on. **Your call:** switching the live calls to `claude-haiku-5-5` cuts their cost about 20× ($0.10/$0.50 against $2/$10 per million tokens) at some quality cost, especially for Sheng.

### The one useful unknown: what a hash proves
**Question (raised by your "hashed copy" idea):** does a hash prove a copy is official?
**What clarified it:** a hash proves the text came from *this exact file*, fetched from *this URL* on *this date*, and anyone can re-check it. It doesn't prove "official" beyond the URL's authority, because Parliament doesn't publish fingerprints. It also enables a bonus feature: silent changes to official PDFs get detected.
**Checked during the build by:** `verify --self-test`, where an intact file reports `unchanged` and a one-byte-altered copy reports `CHANGED`. The fingerprint only counts as working if it catches the altered copy.

### Cold review (Sonnet, 2026-09-25)
An independent read-only review found 5 blockers and about 20 should-fix items. All were fixed in this revision:
- the missing `compare`, `constitution`, `civic` and `lint` commands;
- county and Gazette domains;
- the `pt.profile` schema;
- "See everyone";
- the civic data source;
- constitution payload slicing;
- the pre-paint head script;
- review coverage of all Sheng text;
- banned-phrase enforcement;
- Action permissions;
- the pinned `text_sha256` input;
- nullable `before`/`after`;
- client-side fallback ownership;
- the Match cache hash.
One item was declined: "the model ID is unverified". It's taken from the current Claude API reference, and step 0 includes a live check anyway.

### Open issues (resolve in build step 0 unless noted)
- **Official PDFs exist and are text-extractable** for all three bills, the Finance Act 2026 and the Acts it amends. Carried from `prd.md > Open Questions`. **[UNKNOWN]**
- **Kenya Law URLs** for the constitution, the amended Acts and the Gazette. **[UNKNOWN]**
- **Current package versions:** Astro 7.x, adapter 11.x and Tailwind v4 install cleanly together. **[MEDIUM]**
- **Batches accept structured outputs and fallbacks:** confirm with one test batch. **[MEDIUM]**
- **Listing-page structures** (Parliament, Gazette, County), for `intake --check`. **[UNKNOWN]** They don't block steps 1–3.
- **Nairobi County Assembly domain.** **[UNKNOWN]** Resolve before step 6.
- **Sheng review workload:** about 60+ clauses across three bills, plus summaries, groups, change notes and civic text, each in three languages. Budget your review time.
