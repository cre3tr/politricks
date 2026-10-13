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

**`pipeline/politricks/generate.py` (782):**
- `MODEL` / `EFFORT` 60–61 (`claude-sonnet-5-5`; effort set explicitly so a model swap can't change it)
- output models 86–117
- `build_params` 144
- `build_summary_params` 174
- `build_batch` 196
- `direct_params` 205
- `retry_params` 217 (drops only `output_config.format`, keeps effort)
- outcomes 242–272
- `parse_batch_line` 275
- `apply` 330 (keeps reviewed items and groups)
- self-test fakes: `_FakeClient` 402, `_run_offline` 436
- `self_test` 471
- `BATCH_RECORDS` 690
- `_retry_direct` 693 (never raises)
- `run` 714: saves the batch id and raw results under `pipeline/.cache/batches/`, `--batch-id` resumes, exits 1 listing anything not applied

**`pipeline/politricks/intake.py` (454):**
- `_clause_count` 51
- `_remove_if_incomplete` 59
- `_first_load` 85 (raises if generate leaves requests unapplied)
- `run` 120
- `_blank_pdf` 219
- `self_test` 245

**`pipeline/politricks/lint.py` (440):**
- `Finding` 69
- `load_phrases` 91
- `_pattern` 121
- `iter_texts` 165
- `lint_bill` 183
- `run` 210
- `self_test` 270

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
