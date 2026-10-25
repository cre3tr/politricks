# PoliTricks — notes for a cold-start agent

PoliTricks explains Kenyan bills in English, Kiswahili and Sheng. Each explanation sits beside the official legal text, and that text is fingerprinted. It is built through the Devpost Learn curriculum (`agent/skills/1-start` … `6-ship`).

**Planning and record:** `devpost/`. `scope.md`, `prd.md` and `spec.md` are the approved plan. `checklist.md` is the build doc: slices, verify steps, and an append-only **Revisions** log of every decision and finding. Read the Revisions before changing anything. Don't fork a second build doc.

## Before You Start

Read, in this order, before changing anything:

1. `devpost/checklist.md` in this repo, Revisions first: how the project works and what was decided.
2. `../haven-ea/references/ledger/projects/devpost-basics-2026.md`: rules this repo has already taught, earned from real failures.
3. `../haven-ea/references/ledger/portfolio.md`: cross-repo rules.

Ledger entries are dated. If one names a file, flag, or command, verify it still exists before acting on it.

When this session learns something that cost real time or shipped a defect, append it to the ledger via the `lessons` skill. Do not leave it in a session summary — nothing loads those.

## Working with Ian here

- Use AskUserQuestion, always leaving "Other" open. He answers compactly ("1, 2"), so play back your reading of it before acting on it.
- Build mode: fast, with human-readable comments that match the module style (a docstring saying why the file exists, plain-English notes).
- Get independent Sonnet cold reviews of plans and diffs. Verify the reviewer's citations before relaying them. Only real product decisions go to Ian.
- Ian is the Sheng gate. **Never set `review.status: "reviewed"` on real data.** Tests use in-memory copies (`pipeline/politricks/selftest.py`) or a scratch worktree.

## Commands

```bash
npm run dev                                             # http://localhost:4321 (.claude/launch.json: politricks-dev)
npm run check                                           # astro check (types)
npm run build                                           # astro check && astro build — NOT bare `astro build`, which skips types
node scripts/check-build.mjs <bill-id>                  # per-card assertions on the built HTML
node scripts/check-api.mjs                              # Match/Explain handlers with stubbed Claude, plus the banned-phrase vectors (offline)
uv run --project pipeline politricks <cmd>              # pipeline CLI (pipeline/politricks/cli.py)
uv run --env-file .env --project pipeline politricks generate <id>   # the pipeline does NOT read .env itself
```

**Pipeline commands:**
- `intake [--check]`, `fetch`, `probe`, `extract [--check-json]`, `samples`, `generate [--batch-id X] [--summary-only | --tags-only | --articles-only [--dry-run]]`, `restamp <id> [--dry-run]`, `lint [id]`, `review <id> [--flagged | --only ID ...] [--from-sheet X]`, `verify`, `export <id> [--out X]`, `civic <id> [--dry-run] [--force]`, `constitution`, `ocr <id> [--force]`, `compare <id> [--dry-run] [--force] [--include ID ...]`.
- Self-tests: `lint`, `review`, `intake`, `generate`, `verify`, `export`, `civic`, `constitution`, `restamp`, `extract`, `ocr` and `compare` each take `--self-test`; `review --sheet-self-test` covers `--from-sheet`. All are offline, write nothing, and assert the real bill files' SHA-256 is unchanged.
- **Before committing:** every self-test, `node scripts/check-api.mjs`, then `lint`, then `npm run build`, then `check-build` for each bill.
- **CI** (`.github/workflows/ci.yml`, 2026-10-22) runs that same suite on every PR and on pushes to `main` and `build/poc`, offline with no secrets. Job `test` covers the self-tests, the build and check-build. Job `content` is `lint` on the real bills, and it stays red while any reviewed text has changed since review, until Ian accepts it again. A red `content` with a green `test` means the code is fine and the review is pending. Ubuntu runs the bill lock's POSIX `flock` branch, which Windows never exercises.
- Test kinds and their controls: `TESTING.md` (test-kinds skill).

## Gotchas that cost time

- **Bare `astro build` doesn't type-check.** `npm run build` does. The `never` branch in `src/lib/badge.ts` only matters because of that. Proof: adding a value to `DisplayStatus` (`src/lib/reviewHash.ts`) without a badge fails at the `never` line in `badge.ts` (37). No Vercel project is linked yet; at slice 7, pin `buildCommand: "npm run build"` in `vercel.json`.
- **Kenya Law returned 403 to every request on 2026-10-07**, from curl, the browser and an off-network fetcher. parliament.go.ke worked. The live paths of `fetch`, `verify` and `intake` fail while that lasts. `intake --self-test` stubs the download and covers the logic. Still 403 on 2026-10-19; Ian's call is to wait it out.
  - **Stage lines meanwhile:** `fetch` is what copies `stage`/`stage_checked_at` from `sources.yaml` into the bill JSON, so while it's blocked, change both: the YAML (with a comment naming the source document) and the JSON (under `bill_lock`). Stage isn't in the review fingerprint, so no review state moves.
  - **Where Parliament keeps stage facts:** the NA and Senate **Bills Tracker** PDFs (linked from `/the-national-assembly/house-business/bill-tracker` and `/the-senate/house-business/bills-tracker`) and the Hansard. The bill listing pages carry only the PDF and a generic comments form. Committee chairs' progress statements in the Hansard are the best official source for deadlines and next steps.
  - **The Constitution** has an official copy on parliament.go.ke (`/sites/default/files/2017-05/The_Constitution_of_Kenya_2010.pdf`), used by `pipeline/civic/finance-act-2026.yaml`.
- **`fetch` writes a bill JSON before `extract` runs.** `intake` treats a 0-clause JSON as incomplete: it resumes it, and deletes it on failure. The site builds a page for every `src/data/bills/*.json`, so a stray skeleton would publish.
- **County items (slice 11, 2026-10-23), both from nairobiassembly.go.ke:**
  - **A motion has one clause.** Its overrides file has a `motion:` block (`pages`, `start`, `end` regexes, optional `heading`). `extract.extract_motion` cuts the Hansard's text layer between the markers into clause `c1` (the speaker label stays out because the cut starts AT `THAT,`), and `verify_motion` checks it verbatim. `extract` refuses a `motion:` block on a `kind: bill`, and a `kind: motion` bill without one. `--check-json` also requires the stored text to equal what the markers cut now. Lines are joined with newlines before matching, so write `\s+` between words in `end`.
  - **Headings printed above a clause** (no marginal notes) use `heading_before: {c2: 3}` (the number of lines above that clause's number line). They become the clause's `heading`, and `heading_source` stays `"margin"` (the schema has no third value).
  - **The Hansard of 3 July 2025 records the motion as deferred** (p. 12, "(Motion deferred)"), while the Assembly's motions tracker says "Approved". The stage line in `sources.yaml` states both. `extract --self-test` covers both paths.
- **Banned phrases** (`pipeline/prompts/banned.txt`):
  - Matching is whole-phrase and case-insensitive, after `normalise()`.
  - A trailing `*` lets the last word continue: `illegal*` also catches "illegally".
  - `vote` is unstarred on purpose, so "voting rights" stays allowed.
  - Lint gates nothing in `npm run build` yet. Slice 4 adds the TypeScript matcher. Until then, run generate → lint → review → commit.
- **Windows and shells:**
  - Write any backslash-bearing pattern with the Write tool, never through a Bash heredoc. A `\b` once landed as a 0x08 byte.
  - Worktrees holding `pipeline/.venv` exceed MAX_PATH. Delete them with a `\\?\`-prefixed path, then `git worktree prune`.
- **The Browser pane** paints no frames while hidden, so rAF and screenshots stall. Measure styles and layout instead.
- **Live generation (Sonnet 5.5, Batches):**
  - **Timing:** a batch takes 2–7 minutes, whether it holds 1 or 55 requests. The summary is a separate direct call afterwards.
  - **Measured cost** for the Finance Act (55 clauses): $0.62 for the explanation batch and $0.08 for a tags-only pass. Thinking is most of the output on explanations and little on tags, so never estimate tags from explanations.
  - **Logging a background run:** `uv run --env-file .env --project pipeline python -u -m politricks.cli generate <id>`. Without `-u`, piped output stays empty until the run exits.
  - **Recovering a run:** batch ids and raw results are in `pipeline/.cache/batches/` (gitignored). A stopped run resumes free with `--batch-id`, plus `--tags-only` for a tags batch.
  - **Costing a run:** batch usage is in the raw results file; direct calls (refusal retries, the summary) log theirs to `pipeline/.cache/batches/<bill>.direct.jsonl` since 2026-10-19. That log is a lower bound: a direct call that raised leaves no line.
  - **Spending:** a run with `ANTHROPIC_API_KEY` spends real credit. Probe first (filter `build_batch` to a few ids) and ask Ian before a full run.
- **No em dashes** in UI copy, data labels or the explainer prompt (Ian's call). Clause text keeps its own, because it's verbatim and fingerprinted.
- **Never set `review.status: "reviewed"` from code or a script.** Only `politricks review` does that, driven by Ian.
- **Reviewing in Excel:** `politricks export <id>` writes `exports/<id>-review.xlsx` (gitignored) and only reads the JSON. `politricks review <id> --from-sheet <file>` (`review_sheet.py`) applies the filled-in sheet: Ian's call (2026-10-15) is that his Accept rows are his accepts **when he runs it**. It refuses piped input (`echo yes |`) before reading anything, so only Ian at a terminal can run it. It plans on a copy, shows every new text in full, and saves only on `yes`. It skips rows whose item changed since export, and refuses an Accept on a group note whose sections moved through tag edits in the same sheet. A sheet that may not mean what it shows fails whole: a column sorted on its own, a number or date Excel made of typed text, a formula. **The walk itself still accepts piped answers** (its self-test drives it that way).
- **The full walk offers every draft after the flagged items, and each `a` saves at once.** Answering `a` to re-accept one changed clause accepted 59 items on 2026-10-22. To re-accept what lint flags, use `politricks review <id> --flagged`; to reopen named items (any status, e.g. a typo seen on the site), `--only c12 summary group:trader`. Still diff the bill against `HEAD` field by field before committing a review.
- **Never edit a bill JSON while `politricks review` is running.** The walk holds the bill in memory and saves the whole file on every accept, so a hand or script edit made meanwhile is silently overwritten. Ask Ian to quit (`q`; his edits are kept as drafts), edit, then restart the walk. A tab he has typed in can't be stopped from here.
  - **The bill lock** (`reviewlock.py`, 2026-10-19): every command that saves a bill (the walk, `--from-sheet`, `generate`, `fetch`, `extract`, `samples`, `verify`) holds an OS lock on `<bill>.json.lock` from load to save, and refuses at once (exit 1, "is open in another politricks command") if another holds it. The OS frees it when the process ends however it ends, so it can't go stale. The `.lock` files are gitignored.
  - **What it doesn't cover:** a hand edit, a script that calls `save_bill` directly, and a walk started before 2026-10-19's code. A script editing a bill should take `bill_lock(path)` itself (as the stage update of 2026-10-19 did).
- **"Reviewed" is tied to a fingerprint** (`review.sha256`, from `models.current_sha256`). It covers the three texts, plus a clause's legal text, sorted tags and sorted `suggested_articles`, or a group's section list. The site recomputes it at build time (`src/lib/reviewHash.ts`) and shows *Reviewed* only on a match, otherwise *Changed since review*. Lint flags those until Ian accepts again.
  - **Python and TS must agree.** Change both twins together, then regenerate the shared vectors with `uv run --project pipeline python -m politricks.hashvectors` and commit them. `check-build` checks the TS side against them and `review --self-test` the Python side, so neither can drift alone.
  - **Covered or not:** a new field shown to readers belongs in the fingerprint, and the kind it belongs to moves to a new version. Slice 10 (2026-10-23) did this for `suggested_articles`: **clauses are on `CLAUSE_HASH_VERSION` v2; summaries, groups and civic blocks stay on `REVIEW_HASH_VERSION` v1**, because their parts did not change, so their reviews and stamps stay valid. `models.legacy_clause_sha256_v1` keeps the retired clause rules for the migration only.
  - **After any fingerprint change, run the migration before anything else:** every untouched AI draft carries the old stamp and `is_hand_edited` reads that as a human's edit, so it would be protected from `generate` for good. `politricks restamp <id>` (`restamp.py`, under the bill lock) re-stamps exactly the drafts whose stamp equals their OLD fingerprint; it leaves `"hand-edited"` and mismatched stamps alone, and never touches a reviewed clause (that one reads *Changed since review* and comes back through `review --flagged`). A second run writes nothing. `generate --articles-only` refuses to run while any draft still carries a v1 stamp.
  - **Articles pass:** `generate <id> --articles-only` writes `suggested_articles` for EVERY clause with an explanation, protected ones included (learner decision, 2026-10-23). Every number is checked against `src/data/constitution.json` (unknown ones are dropped and printed; at most 4 kept). An untouched AI draft is re-stamped; a hand-edited one keeps its stamp; a reviewed one is not re-stamped, so it reads *Changed since review*. Batch id: `<bill>.articles.batch-id`; `--dry-run` prints the request count and an estimated size and needs no key. The rules for suggesting live in the request, not in `explainer.md`, because the live Explain API loads that file and has no article list.
  - **What `generate` won't touch:** a hand-edited draft (`sha256` is `"hand-edited"`, or doesn't match). To hand one back to `generate`, set its `review.sha256` to null. **This includes an AI draft whose legal text changed** (a re-extract): the fingerprint covers the text, so the draft reads as hand-edited and `generate` leaves it out (it prints "left out N clause(s)"). Null those drafts' `sha256` to redraft them.
- **Bill JSON is written deterministically** (`models.save_bill`). Re-running a stage must give byte-identical output, and a diff means something changed.

- **Slice 4, the live site (2026-10-23):**
  - **Prompts and banned list are bundled into the API function with `?raw`** (`src/lib/claude.ts`), so site and pipeline read the same files. The key is read at run time with `getSecret` from `astro:env/server`, never `import.meta.env` (which can inline it into `.vercel/output`). A local build's output held 0 `sk-ant-` matches over 921 files on 2026-10-23.
  - **`src/lib/banned.ts` is the TS twin of lint's matcher.** Python's `\w` is Unicode; JS needs `[\p{L}\p{N}_]` with the `u` flag. Both sides are checked against `pipeline/tests/fixtures/banned_vectors.json` (lint `--self-test` 5b and `check-api.mjs`). Add a case there when the list changes.
  - **The API handlers are pure functions** (`src/lib/api.ts`, deps passed in) so `check-api.mjs` can test every status code offline. Node 24 loads `.ts` directly, which is why runtime imports between lib files carry `.ts` extensions.
  - **`check-build.mjs` matches `<article class="clause-card" data-clause-id=...` literally**: keep `data-clause-id` straight after `class` in `ClauseCard.astro`.
  - **HeadScript only redirects to `/welcome` when storage works** (it test-writes `pt.t`), so a blocked browser can't loop. `next=` is checked to be a same-site path.
  - **Measuring the welcome page right after navigation races its module script.** Wait, or re-read, before calling a prefill broken.
- **Slice 8, OCR'd second versions (2026-10-23; CUT by Ian 2026-10-25, never run on real data, code kept):**
  - A scanned official copy is a source with `ocr: true` in `sources.yaml`. `fetch` skips it; only `politricks ocr` fetches, fingerprints and records it, and writes `sources/<id>/approved.ocr.txt` (body text after the margin split, the PDF's SHA-256 in its header; a file for another PDF is refused) plus `approved.ocr.audit.txt` (what was set aside as margin notes). Both are committed, so CI and the site never need Tesseract.
  - **Tesseract** is found on PATH, then `C:/Program Files/Tesseract-OCR/`, or via `TESSERACT_CMD`. Install: `winget install --id UB-Mannheim.TesseractOCR -e` in an admin terminal (there is no per-user installer).
  - `compare` aligns clauses order-preserving (renumbering doesn't misalign). A differing block of 2 characters or fewer with no digit 2-9, after folding look-alikes (l/1/i, o/0), is BORDERLINE OCR noise: printed with context and PDF page, never written; `--include <id>` promotes it after a human checks the PDF. Claude calling a pair noise also keeps it out. Notes are drafts fingerprinted like other items (`changeSha256` is the TS twin, in the shared vectors).
  - `comparer.md` loads the rules sections of `translator.md` at run time: renaming those headings makes `compare` fail loudly.
  - **`review` walks change notes** as `changes[0]`, `changes[1]`... (shown with before/after for context; only the note is accepted or edited). `added:<n>` is the `clause_id` of a clause only the later version has.
- **`intake --check`** proposes every PDF on the listings that the registry doesn't know: 147 entries on 2026-10-23 (98 from Nairobi back to 2013). It needs a filter before the weekly workflow runs unattended.
- **`verify` states** are `unchanged`, `CHANGED`, `UNREACHABLE` (HTTP error or network) and `ERROR`. UNREACHABLE exits 0 but is printed last as "N source(s) UNREACHABLE, not checked".
- **`politricks review` walks the civic blocks** (2026-10-23) by their kind: `--only participation`, `stage_next`, `challenge`. A block with several texts (a challenge's avenue names and texts) shows them numbered, and an edit asks which. Accept stores `civic.civic_sha256`; the site's twin is `civicSha256` in `src/lib/reviewHash.ts`, held to the shared vectors like the others. Lint flags a reviewed civic block whose content changed (finding item = the kind). Not covered: the Excel sheet (`export` / `--from-sheet`) has no civic rows and no articles column, so review those in the walk.

## Line maps (files over 300 lines)

**`pipeline/politricks/extract.py` (454):**
- `find_gutter` 95
- `_page_lines` 148
- `read_pdf` 209
- `segment` 272
- `_heading_for` 308
- `extract` 323
- `verify_clauses` 392
- `run` 410 (load to save under the bill lock)
- `check_json` 436

**`pipeline/politricks/generate.py` (1850):**
- `MODEL` / `EFFORT` 71–72 (`claude-sonnet-5-5`; effort set explicitly so a model swap can't change it)
- output models 97–157 (`SwapOut` = a Sheng word kept in Kiswahili; `GroupOut` has no `clause_ids`: group membership comes only from clause tags; `TagsOut` = groups only; `ArticlesOut` 151 = `suggested_articles` only)
- `_out_model` 159 (which schema an answer must match), `_swaps` 168
- `build_params` 196
- `tagged_sections` 226 (each group's clause ids, from the per-clause tags)
- `build_summary_params` 231 (full section text in `<section id>` blocks, plus the tag list)
- `build_tag_params` 274, `build_tag_batch` 297 (tags-only pass; same system prompt, so the cache is reused)
- articles pass 309–385: `MAX_ARTICLES` 309, `load_article_titles` 316 (number to title from constitution.json), `articles_system_block` 329 (the 264 "number. title" lines as a second, cached system block), `build_article_params` 342, `build_article_batch` 375 (every clause with an explanation, protected ones too)
- `build_batch` 387 (clauses only; no summary). Both batch builders leave out protected clauses (reviewed or hand-edited), so they're never paid for again
- `direct_params` 401
- `retry_params` 413 (drops only `output_config.format`, keeps effort)
- outcomes 438–468
- `parse_batch_line` 471
- `apply` 529 (applies the summary last; never overwrites a protected item; stamps every draft it writes with its fingerprint, AFTER setting its fields; a group's `clause_ids` come from the tags; drops repeated or untagged groups, with a note; copies `sheng_swaps`)
- `refresh_group_sections` 615 (every group's `clause_ids` follow the tags; decides human-or-AI BEFORE changing; an AI draft is re-stamped or dropped, a reviewed group goes "changed since review")
- `apply_tags` 647 (only `groups` changes, plus the draft's re-stamp; protected clauses keep theirs; prints each change)
- `apply_articles` 673 (only `suggested_articles` changes; validates against constitution.json; decides AI-draft / hand-edited / reviewed / sample BEFORE writing; re-stamps only an untouched AI draft; prints each change)
- self-test fakes: `_FakeClient` 731, `_run_offline` 765 (cuts the temp bill to the fixture's clauses; `prepare=` edits it first; `hold_lock=` holds the bill lock around the run; `articles_only=`, `dry_run=`, `with_constitution=`), `mark_reviewed_with_text` 841
- `_articles_self_test` 848 (called from `self_test` 1032)
- `BATCH_RECORDS` 1559
- `_usage_log` 1562, `_log_usage` 1571 (direct calls' token usage, appended to `<bill>.direct.jsonl`)
- `_call_direct` 1592 (refusal retries and the summary; never raises; logs usage for every answer that came back)
- `_summarise` 1626 (skips a protected summary), `_report` 1637
- `_stale_stamps` 1695 (drafts still on a v1 stamp), `_dry_run_articles` 1707 (size estimate from character counts: no key, no lock, no write)
- `run` 1652 takes the bill lock (refuses before anything is sent), then `_run` 1727 does the work: phase 1 is the clause batch (saves the batch id and raw results under `pipeline/.cache/batches/`; `--batch-id` resumes; a missing result or one for a request this bill didn't make fails the run, except a clause reviewed or edited while the batch ran, which is left alone; no batch at all if every clause is protected). Phase 2 is the summary as a direct call from the clause tags, **skipped if any clause failed** (`--summary-only` redoes just that). `--tags-only` makes phase 1 a groups-only batch (id saved as `<bill>.tags.batch-id`). `--articles-only` makes it an articles batch over every clause with an explanation (nothing is "left out"; id saved as `<bill>.articles.batch-id`; refuses first if constitution.json is missing or a draft has a v1 stamp) and skips the summary. Exits 1 listing anything not applied.

**`pipeline/politricks/intake.py` (454):**
- `_clause_count` 51
- `_remove_if_incomplete` 59
- `_first_load` 85 (raises if generate leaves requests unapplied)
- `run` 120
- `_blank_pdf` 219
- `self_test` 245

**`pipeline/politricks/lint.py` (515):**
- `DOCTRINE_PHRASES` 47 (the 19 phrases `banned.txt` must always hold; the self-test fails if one goes missing)
- `Finding` 83 (kinds: banned, neutral, sheng, changed)
- `load_phrases` 109
- `_pattern` 139
- `iter_texts` 183
- `lint_bill` 201 (banned phrases at any status; neutral flags and Sheng swaps until the item `is_reviewed`; `changed` for anything reviewed whose content has changed since)
- `run` 248
- `self_test` 308

**`pipeline/politricks/review.py` (1054):**
- `QuitReview` 119
- `_candidates` 163, `selection_errors` 177 (why an `--only` id can't be walked)
- `_collect` 192 (flagged first; then clauses, summary, groups; `--flagged` / `--only` narrow it and count the rest as "left out")
- `_describe` 232 (shows a clause's GROUPS or a group's SECTIONS: accepting accepts them too)
- `_edit_text` 276
- `walk` 314 (accept stores the fingerprint; any edit makes a hand-edited draft, `sha256: "hand-edited"`)
- `_ask_from_terminal` 449
- `run` 458 (refuses a bad `--only` id before showing anything; holds the bill lock for the whole walk)
- `self_test` 510 (scenario J 917: the lock; K 951: `--flagged` / `--only`, with the 59-accept trap as its control)

**`pipeline/politricks/review_sheet.py` (693):**
- `TEXT_COLUMNS` 82 (cells that must be text, not numbers or dates)
- `read_sheet` 127 (refuses formulas, numbers, repeats, renamed columns, another bill's sheet)
- `_check_rows` 210 (unknown ids, empty texts, a column sorted on its own)
- `plan` 244 (on a copy: skip stale rows, apply edits, refresh group sections, then accepts)
- `_save_atomically` 352
- `review_from_sheet` 361 (prints the plan, asks for `yes`, refuses if the bill file changed meanwhile)
- `run` 414 (refuses a non-terminal stdin; holds the bill lock from read to save, across the `yes` prompt)
- `self_test` 445 (section L: the lock)

**`pipeline/politricks/civic.py` (1131):** YAML models 130-175, `parse_civic` 202, block models 240-275, `civic_sha256` 292, `is_protected` 332, `assemble` 404, `build_params` 458, `translation_problems` 485, `plan_block` 576, `run` 635, `_run` 671, `_FakeClient` 776, `self_test` 839.

**`pipeline/politricks/constitution.py` (707):** `read_lines` 135, `legal_body` 174, `split_articles` 189, `check_numbering` 305, `verify_verbatim` 327, `check_coverage` 338, `extract` 362, `run` 399, `_run` 413, `_fixture_pdf` 510, `self_test` 549. Source is Parliament's copy (Kenya Law 403); output `src/data/constitution.json`, 264 articles.

**`pipeline/politricks/intake_check.py` (714):** `LISTINGS` 61, `find_pdf_links` 141, `looks_like_a_bill` 176, `read_listing` 225, `entry_block` 259, `assign_ids` 289, `proposed_diff` 321, `run` 344, self-test from 434.

**`pipeline/politricks/reviewlock.py`:** `bill_lock(path)` (context manager; raises `BillLocked`). `cli.main` turns a `BillLocked` from fetch/extract/samples/verify into "FAILED", exit 1.

**`pipeline/civic/*.yaml`:** civic facts for slice 6, DRAFTED by Claude on 2026-10-19 and not yet checked by Ian. Each fact carries its source and an `[OFFICIAL]`/`[NEWS]`/`[UNKNOWN]` note. `deadline_time` is an addition to the spec's schema; `civic.py` (slice 6) doesn't exist yet.

**`pipeline/politricks/models.py` (275), review fingerprints:** `current_sha256` 161, `legacy_clause_sha256_v1` 187 (migration only), `is_v1_stamped` 199, `is_reviewed` 208, `changed_since_review` 214, `HAND_EDITED` 221, `is_hand_edited` 224, `is_protected` 234, `draft_review` 240.

**`pipeline/politricks/restamp.py` (268):** `plan` 80 (sorts every clause into a bucket; pure), `apply_plan` 110, `report` 123, `run` 139 (bill lock; writes nothing when there is nothing to do), `self_test` 171.

The shared test support lives in `pipeline/politricks/selftest.py`: `Checker`, `mark_reviewed` (in-memory copies only), `reset_bill` and `fixture_bill`.
