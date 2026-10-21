# PoliTricks — notes for a cold-start agent

PoliTricks explains Kenyan bills in English, Kiswahili and Sheng. Each explanation sits beside the official legal text, and that text is fingerprinted. It is built through the Devpost Learn curriculum (`agent/skills/1-start` … `6-ship`).

**Planning and record:** `devpost/`. `scope.md`, `prd.md` and `spec.md` are the approved plan. `checklist.md` is the build doc: slices, verify steps, and an append-only **Revisions** log of every decision and finding. Read the Revisions before changing anything. Don't fork a second build doc.

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
uv run --project pipeline politricks <cmd>              # pipeline CLI (pipeline/politricks/cli.py)
uv run --env-file .env --project pipeline politricks generate <id>   # the pipeline does NOT read .env itself
```

**Pipeline commands:**
- `intake`, `fetch`, `probe`, `extract [--check-json]`, `samples`, `generate [--batch-id X] [--summary-only] [--tags-only]`, `lint [id]`, `review <id> [--from-sheet X]`, `verify`, `export <id> [--out X]`.
- Self-tests: `lint`, `review`, `intake`, `generate`, `verify` and `export` each take `--self-test`; `review --sheet-self-test` covers `--from-sheet`. All are offline, write nothing, and assert the real bill files' SHA-256 is unchanged.
- **Before committing:** every self-test, then `lint`, then `npm run build`, then `check-build` for each bill.
- Test kinds and their controls: `TESTING.md` (test-kinds skill).

## Gotchas that cost time

- **Bare `astro build` doesn't type-check.** `npm run build` does. The `never` branch in `src/lib/badge.ts` only matters because of that. Proof: adding a value to `DisplayStatus` (`src/lib/reviewHash.ts`) without a badge fails at the `never` line in `badge.ts` (37). No Vercel project is linked yet; at slice 7, pin `buildCommand: "npm run build"` in `vercel.json`.
- **Kenya Law returned 403 to every request on 2026-10-07**, from curl, the browser and an off-network fetcher. parliament.go.ke worked. The live paths of `fetch`, `verify` and `intake` fail while that lasts. `intake --self-test` stubs the download and covers the logic. Still 403 on 2026-10-19; Ian's call is to wait it out.
  - **Stage lines meanwhile:** `fetch` is what copies `stage`/`stage_checked_at` from `sources.yaml` into the bill JSON, so while it's blocked, change both: the YAML (with a comment naming the source document) and the JSON (under `bill_lock`). Stage isn't in the review fingerprint, so no review state moves.
  - **Where Parliament keeps stage facts:** the NA and Senate **Bills Tracker** PDFs (linked from `/the-national-assembly/house-business/bill-tracker` and `/the-senate/house-business/bills-tracker`) and the Hansard. The bill listing pages carry only the PDF and a generic comments form. Committee chairs' progress statements in the Hansard are the best official source for deadlines and next steps.
  - **The Constitution** has an official copy on parliament.go.ke (`/sites/default/files/2017-05/The_Constitution_of_Kenya_2010.pdf`), used by `pipeline/civic/finance-act-2026.yaml`.
- **`fetch` writes a bill JSON before `extract` runs.** `intake` treats a 0-clause JSON as incomplete: it resumes it, and deletes it on failure. The site builds a page for every `src/data/bills/*.json`, so a stray skeleton would publish.
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
- **Never edit a bill JSON while `politricks review` is running.** The walk holds the bill in memory and saves the whole file on every accept, so a hand or script edit made meanwhile is silently overwritten. Ask Ian to quit (`q`; his edits are kept as drafts), edit, then restart the walk. A tab he has typed in can't be stopped from here.
  - **The bill lock** (`reviewlock.py`, 2026-10-19): every command that saves a bill (the walk, `--from-sheet`, `generate`, `fetch`, `extract`, `samples`, `verify`) holds an OS lock on `<bill>.json.lock` from load to save, and refuses at once (exit 1, "is open in another politricks command") if another holds it. The OS frees it when the process ends however it ends, so it can't go stale. The `.lock` files are gitignored.
  - **What it doesn't cover:** a hand edit, a script that calls `save_bill` directly, and a walk started before 2026-10-19's code. A script editing a bill should take `bill_lock(path)` itself (as the stage update of 2026-10-19 did).
- **"Reviewed" is tied to a fingerprint** (`review.sha256`, from `models.current_sha256`). It covers the three texts, plus a clause's legal text and sorted tags, or a group's section list. The site recomputes it at build time (`src/lib/reviewHash.ts`) and shows *Reviewed* only on a match, otherwise *Changed since review*. Lint flags those until Ian accepts again.
  - **Python and TS must agree.** Change both twins together, then regenerate the shared vectors with `uv run --project pipeline python -m politricks.hashvectors` and commit them. `check-build` checks the TS side against them and `review --self-test` the Python side, so neither can drift alone.
  - **Covered or not:** a new field shown to readers belongs in the fingerprint; bump `REVIEW_HASH_VERSION` (`hashing.py`) when you add it. Slice 10 must do this for `suggested_articles`.
  - **What `generate` won't touch:** a hand-edited draft (`sha256` is `"hand-edited"`, or doesn't match). To hand one back to `generate`, set its `review.sha256` to null. **This includes an AI draft whose legal text changed** (a re-extract): the fingerprint covers the text, so the draft reads as hand-edited and `generate` leaves it out (it prints "left out N clause(s)"). Null those drafts' `sha256` to redraft them.
- **Bill JSON is written deterministically** (`models.save_bill`). Re-running a stage must give byte-identical output, and a diff means something changed.

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

**`pipeline/politricks/generate.py` (1436):**
- `MODEL` / `EFFORT` 69–70 (`claude-sonnet-5-5`; effort set explicitly so a model swap can't change it)
- output models 95–147 (`SwapOut` = a Sheng word kept in Kiswahili; `GroupOut` has no `clause_ids`: group membership comes only from clause tags; `TagsOut` = groups only)
- `_out_model` 149 (which schema an answer must match), `_swaps` 156
- `build_params` 184
- `tagged_sections` 214 (each group's clause ids, from the per-clause tags)
- `build_summary_params` 219 (full section text in `<section id>` blocks, plus the tag list)
- `build_tag_params` 262, `build_tag_batch` 285 (tags-only pass; same system prompt, so the cache is reused)
- `build_batch` 294 (clauses only; no summary). Both batch builders leave out protected clauses (reviewed or hand-edited), so they're never paid for again
- `direct_params` 308
- `retry_params` 320 (drops only `output_config.format`, keeps effort)
- outcomes 344–376
- `parse_batch_line` 378
- `apply` 435 (applies the summary last; never overwrites a protected item; stamps every draft it writes with its fingerprint, AFTER setting its fields; a group's `clause_ids` come from the tags; drops repeated or untagged groups, with a note; copies `sheng_swaps`)
- `refresh_group_sections` 520 (every group's `clause_ids` follow the tags; decides human-or-AI BEFORE changing; an AI draft is re-stamped or dropped, a reviewed group goes "changed since review")
- `apply_tags` 552 (only `groups` changes, plus the draft's re-stamp; protected clauses keep theirs; prints each change)
- self-test fakes: `_FakeClient` 587, `_run_offline` 621 (cuts the temp bill to the fixture's clauses; `prepare=` edits it first; `hold_lock=` holds the bill lock around the run), `mark_reviewed_with_text` 686
- `self_test` 693
- `BATCH_RECORDS` 1218
- `_usage_log` 1221, `_log_usage` 1230 (direct calls' token usage, appended to `<bill>.direct.jsonl`)
- `_call_direct` 1251 (refusal retries and the summary; never raises; logs usage for every answer that came back)
- `_summarise` 1276 (skips a protected summary), `_report` 1287
- `run` 1302 takes the bill lock (refuses before anything is sent), then `_run` 1337 does the work: phase 1 is the clause batch (saves the batch id and raw results under `pipeline/.cache/batches/`; `--batch-id` resumes; a missing result or one for a request this bill didn't make fails the run, except a clause reviewed or edited while the batch ran, which is left alone; no batch at all if every clause is protected). Phase 2 is the summary as a direct call from the clause tags, **skipped if any clause failed** (`--summary-only` redoes just that). `--tags-only` makes phase 1 a groups-only batch (id saved as `<bill>.tags.batch-id`). Exits 1 listing anything not applied.

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

**`pipeline/politricks/review.py` (875):**
- `QuitReview` 109
- `_collect` 153 (flagged first; then clauses, summary, groups)
- `_describe` 187 (shows a clause's GROUPS or a group's SECTIONS: accepting accepts them too)
- `_edit_text` 231
- `walk` 269 (accept stores the fingerprint; any edit makes a hand-edited draft, `sha256: "hand-edited"`)
- `_ask_from_terminal` 387
- `run` 396 (holds the bill lock for the whole walk)
- `self_test` 434 (scenario J: the lock)

**`pipeline/politricks/review_sheet.py` (693):**
- `TEXT_COLUMNS` 82 (cells that must be text, not numbers or dates)
- `read_sheet` 127 (refuses formulas, numbers, repeats, renamed columns, another bill's sheet)
- `_check_rows` 210 (unknown ids, empty texts, a column sorted on its own)
- `plan` 244 (on a copy: skip stale rows, apply edits, refresh group sections, then accepts)
- `_save_atomically` 352
- `review_from_sheet` 361 (prints the plan, asks for `yes`, refuses if the bill file changed meanwhile)
- `run` 414 (refuses a non-terminal stdin; holds the bill lock from read to save, across the `yes` prompt)
- `self_test` 445 (section L: the lock)

**`pipeline/politricks/reviewlock.py`:** `bill_lock(path)` (context manager; raises `BillLocked`). `cli.main` turns a `BillLocked` from fetch/extract/samples/verify into "FAILED", exit 1.

**`pipeline/civic/*.yaml`:** civic facts for slice 6, DRAFTED by Claude on 2026-10-19 and not yet checked by Ian. Each fact carries its source and an `[OFFICIAL]`/`[NEWS]`/`[UNKNOWN]` note. `deadline_time` is an addition to the spec's schema; `civic.py` (slice 6) doesn't exist yet.

**`pipeline/politricks/models.py` (248), review fingerprints:** `current_sha256` 155, `is_reviewed` 181, `changed_since_review` 187, `HAND_EDITED` 194, `is_hand_edited` 197, `is_protected` 207, `draft_review` 213.

The shared test support lives in `pipeline/politricks/selftest.py`: `Checker`, `mark_reviewed` (in-memory copies only), `reset_bill` and `fixture_bill`.
