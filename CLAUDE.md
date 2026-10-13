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
- `intake`, `fetch`, `probe`, `extract [--check-json]`, `samples`, `generate [--batch-id X] [--summary-only] [--tags-only]`, `lint [id]`, `review <id>`, `verify`.
- Self-tests: `lint`, `review`, `intake`, `generate` and `verify` each take `--self-test`. All are offline, write nothing, and assert the real bill files' SHA-256 is unchanged.
- **Before committing:** every self-test, then `lint`, then `npm run build`, then `check-build` for each bill.
- Test kinds and their controls: `TESTING.md` (test-kinds skill).

## Gotchas that cost time

- **Bare `astro build` doesn't type-check.** `npm run build` does. The `never` branch in `src/lib/badge.ts` only matters because of that. Proof: adding a value to `DisplayStatus` (`src/lib/reviewHash.ts`) without a badge fails at the `never` line in `badge.ts` (37). No Vercel project is linked yet; at slice 7, pin `buildCommand: "npm run build"` in `vercel.json`.
- **Kenya Law returned 403 to every request on 2026-10-07**, from curl, the browser and an off-network fetcher. parliament.go.ke worked. The live paths of `fetch`, `verify` and `intake` fail while that lasts. `intake --self-test` stubs the download and covers the logic.
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
  - **Spending:** a run with `ANTHROPIC_API_KEY` spends real credit. Probe first (filter `build_batch` to a few ids) and ask Ian before a full run.
- **No em dashes** in UI copy, data labels or the explainer prompt (Ian's call). Clause text keeps its own, because it's verbatim and fingerprinted.
- **Never set `review.status: "reviewed"` from code or a script.** Only `politricks review` does that, driven by Ian.
- **"Reviewed" is tied to a fingerprint** (`review.sha256`, from `models.current_sha256`). It covers the three texts, plus a clause's legal text and sorted tags, or a group's section list. The site recomputes it at build time (`src/lib/reviewHash.ts`) and shows *Reviewed* only on a match, otherwise *Changed since review*. Lint flags those until Ian accepts again.
  - **Python and TS must agree.** Change both twins together, then regenerate the shared vectors with `uv run --project pipeline python -m politricks.hashvectors` and commit them. `check-build` checks the TS side against them and `review --self-test` the Python side, so neither can drift alone.
  - **Covered or not:** a new field shown to readers belongs in the fingerprint; bump `REVIEW_HASH_VERSION` (`hashing.py`) when you add it. Slice 10 must do this for `suggested_articles`.
  - **What `generate` won't touch:** a hand-edited draft (`sha256` is `"hand-edited"`, or doesn't match). To hand one back to `generate`, set its `review.sha256` to null. **This includes an AI draft whose legal text changed** (a re-extract): the fingerprint covers the text, so the draft reads as hand-edited and `generate` leaves it out (it prints "left out N clause(s)"). Null those drafts' `sha256` to redraft them.
- **Bill JSON is written deterministically** (`models.save_bill`). Re-running a stage must give byte-identical output, and a diff means something changed.

## Line maps (files over 300 lines)

**`pipeline/politricks/extract.py` (450):**
- `find_gutter` 94
- `_page_lines` 147
- `read_pdf` 208
- `segment` 271
- `_heading_for` 307
- `extract` 322
- `verify_clauses` 391
- `run` 409
- `check_json` 432

**`pipeline/politricks/generate.py` (1361):**
- `MODEL` / `EFFORT` 68–69 (`claude-sonnet-5-5`; effort set explicitly so a model swap can't change it)
- output models 94–146 (`SwapOut` = a Sheng word kept in Kiswahili; `GroupOut` has no `clause_ids`: group membership comes only from clause tags; `TagsOut` = groups only)
- `_out_model` 148 (which schema an answer must match), `_swaps` 155
- `build_params` 183
- `tagged_sections` 213 (each group's clause ids, from the per-clause tags)
- `build_summary_params` 218 (full section text in `<section id>` blocks, plus the tag list)
- `build_tag_params` 261, `build_tag_batch` 284 (tags-only pass; same system prompt, so the cache is reused)
- `build_batch` 293 (clauses only; no summary). Both batch builders leave out protected clauses (reviewed or hand-edited), so they're never paid for again
- `direct_params` 307
- `retry_params` 319 (drops only `output_config.format`, keeps effort)
- outcomes 344–374
- `parse_batch_line` 377
- `apply` 434 (applies the summary last; never overwrites a protected item; stamps every draft it writes with its fingerprint, AFTER setting its fields; a group's `clause_ids` come from the tags; drops repeated or untagged groups, with a note; copies `sheng_swaps`)
- `refresh_group_sections` 519 (every group's `clause_ids` follow the tags; decides human-or-AI BEFORE changing; an AI draft is re-stamped or dropped, a reviewed group goes "changed since review")
- `apply_tags` 551 (only `groups` changes, plus the draft's re-stamp; protected clauses keep theirs; prints each change)
- self-test fakes: `_FakeClient` 586, `_run_offline` 620 (cuts the temp bill to the fixture's clauses; `prepare=` edits it first), `mark_reviewed_with_text` 676
- `self_test` 683
- `BATCH_RECORDS` 1190
- `_call_direct` 1193 (refusal retries and the summary; never raises)
- `_summarise` 1216 (skips a protected summary), `_report` 1227
- `run` 1242: phase 1 is the clause batch (saves the batch id and raw results under `pipeline/.cache/batches/`; `--batch-id` resumes; a missing result or one for a request this bill didn't make fails the run, except a clause reviewed or edited while the batch ran, which is left alone; no batch at all if every clause is protected). Phase 2 is the summary as a direct call from the clause tags, **skipped if any clause failed** (`--summary-only` redoes just that). `--tags-only` makes phase 1 a groups-only batch (id saved as `<bill>.tags.batch-id`). Exits 1 listing anything not applied.

**`pipeline/politricks/intake.py` (454):**
- `_clause_count` 51
- `_remove_if_incomplete` 59
- `_first_load` 85 (raises if generate leaves requests unapplied)
- `run` 120
- `_blank_pdf` 219
- `self_test` 245

**`pipeline/politricks/lint.py` (501):**
- `Finding` 69 (kinds: banned, neutral, sheng, changed)
- `load_phrases` 95
- `_pattern` 125
- `iter_texts` 169
- `lint_bill` 187 (banned phrases at any status; neutral flags and Sheng swaps until the item `is_reviewed`; `changed` for anything reviewed whose content has changed since)
- `run` 234
- `self_test` 294

**`pipeline/politricks/review.py` (825):**
- `QuitReview` 107
- `_collect` 151 (flagged first; then clauses, summary, groups)
- `_describe` 185 (shows a clause's GROUPS or a group's SECTIONS: accepting accepts them too)
- `_edit_text` 229
- `walk` 267 (accept stores the fingerprint; any edit makes a hand-edited draft, `sha256: "hand-edited"`)
- `_ask_from_terminal` 385
- `run` 394
- `self_test` 418

**`pipeline/politricks/models.py` (248), review fingerprints:** `current_sha256` 155, `is_reviewed` 181, `changed_since_review` 187, `HAND_EDITED` 194, `is_hand_edited` 197, `is_protected` 207, `draft_review` 213.

The shared test support lives in `pipeline/politricks/selftest.py`: `Checker`, `mark_reviewed` (in-memory copies only), `reset_bill` and `fixture_bill`.
