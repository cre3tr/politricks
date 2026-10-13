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
- `intake`, `fetch`, `probe`, `extract [--check-json]`, `samples`, `generate`, `lint [id]`, `review <id>`, `verify`.
- Self-tests: `lint`, `review`, `intake`, `generate` and `verify` each take `--self-test`. All are offline, write nothing, and assert the real bill files' SHA-256 is unchanged.
- **Before committing:** every self-test, then `lint`, then `npm run build`, then `check-build` for each bill.

## Gotchas that cost time

- **Bare `astro build` doesn't type-check.** `npm run build` does. The `never` branch in `src/lib/badge.ts` only matters because of that. Proof: adding a fourth review status fails at `badge.ts:28`. No Vercel project is linked yet; at slice 7, pin `buildCommand: "npm run build"` in `vercel.json`.
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

**`pipeline/politricks/generate.py` (1029):**
- `MODEL` / `EFFORT` 61–62 (`claude-sonnet-5-5`; effort set explicitly so a model swap can't change it)
- output models 87–131 (`SwapOut` = a Sheng word kept in Kiswahili; `GroupOut` has no `clause_ids`: group membership comes only from clause tags)
- `_swaps` 134
- `build_params` 162
- `tagged_sections` 192 (each group's clause ids, from the per-clause tags)
- `build_summary_params` 197 (full section text in `<section id>` blocks, plus the tag list)
- `build_batch` 240 (clauses only; no summary)
- `direct_params` 246
- `retry_params` 258 (drops only `output_config.format`, keeps effort)
- outcomes 283–313
- `parse_batch_line` 316
- `apply` 371 (applies the summary last; keeps reviewed items and groups; a group's `clause_ids` come from the tags; drops repeated or untagged groups, with a note; copies `sheng_swaps`)
- self-test fakes: `_FakeClient` 460, `_run_offline` 494 (cuts the temp bill to the fixture's clauses)
- `self_test` 543
- `BATCH_RECORDS` 880
- `_call_direct` 883 (refusal retries and the summary; never raises)
- `_summarise` 906, `_report` 917
- `run` 932: phase 1 is the clause batch (saves the batch id and raw results under `pipeline/.cache/batches/`; `--batch-id` resumes; a missing result or one for a request this bill didn't make fails the run). Phase 2 is the summary as a direct call from the clause tags, **skipped if any clause failed** (`--summary-only` redoes just that). Exits 1 listing anything not applied.

**`pipeline/politricks/intake.py` (454):**
- `_clause_count` 51
- `_remove_if_incomplete` 59
- `_first_load` 85 (raises if generate leaves requests unapplied)
- `run` 120
- `_blank_pdf` 219
- `self_test` 245

**`pipeline/politricks/lint.py` (473):**
- `Finding` 69 (kinds: banned, neutral, sheng)
- `load_phrases` 93
- `_pattern` 123
- `iter_texts` 167
- `lint_bill` 185 (banned phrases at any status; neutral flags and Sheng swaps until the item is reviewed)
- `run` 223
- `self_test` 283

**`pipeline/politricks/review.py` (736):**
- `QuitReview` 82
- `_collect` 126
- `_describe` 159
- `_edit_text` 196
- `walk` 234
- `_ask_from_terminal` 347
- `run` 356
- `self_test` 380

The shared test support lives in `pipeline/politricks/selftest.py`: `Checker`, `reset_bill` and `fixture_bill`.
