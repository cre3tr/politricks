# How PoliTricks is tested

The kinds of test this repo uses, from the cross-repo catalog
(`haven-ea/references/testing/test-kinds.md`). Kept by the `test-kinds` skill.
How to prove any test is live: `~/.claude/rules/verification-gates.md`.

## Run them

Before every commit, in this order (from `CLAUDE.md`):

```bash
uv run --project pipeline politricks lint --self-test
uv run --project pipeline politricks review --self-test
uv run --project pipeline politricks generate --self-test
uv run --project pipeline politricks intake --self-test
uv run --project pipeline politricks verify --self-test
uv run --project pipeline politricks export --self-test
uv run --project pipeline politricks review --sheet-self-test
uv run --project pipeline politricks extract --self-test     # county motion + headings-above-clauses paths (2026-10-23)
uv run --project pipeline politricks lint
npm run build
node scripts/check-build.mjs <bill-id>        # once per bill in src/data/bills/
```

All of these are offline and free. Nothing here calls the Anthropic API.

## Kinds in use

Check counts: see the newest entry under Changes. Verify prints `unchanged` /
`CHANGED`. `check-build`: 117 / 141 / 47 for the three bills (2026-10-19).

| Kind | Where | Command | Control last run | Result |
|---|---|---|---|---|
| K1 Offline self-test, writes nothing | `pipeline/politricks/{lint.py:294, review.py:510, generate.py:683, intake.py:245, verify.py:30, export.py, review_sheet.py}`; real-file SHA guard e.g. `lint.py:499` | `politricks <cmd> --self-test` | not on record | see Gaps |
| K2 Planted-bug controls | scratch scripts (session scratchpad, not committed) | `uv run --project pipeline python <script>` | 2026-10-14 | ignore protection → 11 FAILs; fingerprint ignores tags → 4; lint treats bare "reviewed" as reviewed → 1; accept without fingerprint → 6; edit keeps fingerprint → 2; tampered vector → 1 |
| K3 Fake client through the real `run()` | `generate.py:586` (`_FakeClient`), `:620` (`_run_offline`) | `generate --self-test` | 2026-10-13 | old `.parsed` retry → 9 FAILs |
| K4 Fixtures built from a reset | `pipeline/politricks/selftest.py` (`reset_bill`, `fixture_bill`) | every self-test | 2026-10-14 | real data changed (Ian accepted c1, c2) → 2 FAILs in generate, which built its batch from the real bill; fixed to use `reset_bill` → 0 |
| K5 Byte-identical re-run | `intake.py:450` (check 7), `models.save_bill` | `intake --self-test` | 2026-10-13 | new field without re-save → 2 FAILs |
| K6 Shared vectors, Python vs TS | `pipeline/tests/fixtures/review_hash_vectors.json`; checked in `review.py` (check "I. … shared vector") and `scripts/check-build.mjs` | `review --self-test`, `check-build` | 2026-10-14 | TS sort removed → 1 FAIL (c12-1); tampered expected value → Python FAIL |
| K7 Built-artefact checks per card | `scripts/check-build.mjs` | `check-build <bill>` | 2026-10-14 | every card forced to the draft badge → 2 FAILs (c3, c4) |
| K8 Second observer (Reviewed invariant) | `scripts/check-build.mjs` (clause cards and summary) | `check-build <bill>` | 2026-10-14 | `displayStatus` ignoring the fingerprint → 1 FAIL (c4) |
| K9 Exhaustiveness by type check | `src/lib/badge.ts:37` (`never`) | `npm run build` | 2026-10-14 | `DisplayStatus` grew a value → `ts(2322)` at `badge.ts:37` |
| K10 One test per state-table row | generate checks "fingerprints:" and "refresh:"; review scenario I | `generate`/`review --self-test` | 2026-10-14 | rows: reviewed+match, changed, hand-edited, edit-back, null fingerprint, sample |
| K11 Scripted interactive walk | `review.py:535` (`drive`), `:819` (`drive_terminal`) | `review --self-test` | 2026-10-07 | the cold review's 17 named mutations (lint, review and intake together) all caught, including "abort" read as accept and EOF written as text |
| K12 Live probe before a paid run | session scratchpad only (`probe_batch.py`, `reprobe*.py`) | n/a (costs credit; ask first) | 2026-10-13 | 1-request batch `msgbatch_01CHds…`, 126 s, `Applied` |
| K13 Control build from a scratch copy | session scratchpad (`sitectl/`, `make_control_bill.py`) | `npm run build` + `check-build zz-control` in the copy | 2026-10-14 | c3 *Reviewed*, c4 *Changed since review*, c5 *AI draft*; read in raw HTML too |
| K14 One-byte tamper check | `pipeline/politricks/verify.py:30` | `verify --self-test` | 2026-10-14 (runs on every call) | intact `unchanged`, one-byte copy `CHANGED` |
| K15 Content gate with near misses | `lint.py` self-test | `lint --self-test` | 2026-10-07 | deleting `invalid*` → 2 FAILs |

## Gaps

| Kind | Why it applies here | Risk if it stays missing |
|---|---|---|
| K1 control | Every self-test ends by asserting the real bill's SHA-256 is unchanged, but no one has planted a write to prove that assert can fail. | A self-test that writes to real data would pass silently, and real data holds Ian's reviews. |
| K15 in the build | Lint isn't part of `npm run build` until slice 4 adds the TS matcher. | A banned phrase in a hand-edit ships unless lint is run by hand. |
| K12, K13 tooling | The probe and control-copy scripts live in a session scratchpad. | The next bill's probe, or the next badge change, has to rebuild them, and may skip it. |
| K12 for fetch | Kenya Law has returned 403 since 2026-10-07; live `fetch`/`verify`/`intake` can't be probed. | A changed Kenya Law page shape is found only when it's back. |

## Not applicable

| Kind | Why not |
|---|---|
| (none) | Every catalog kind was earned in this repo. |

## Changes

- 2026-10-22: `politricks review --flagged` / `--only`, closing the walk trap that accepted 59 items.
  - **New checks:** review scenario K, 11 checks. Counts are now lint 36, review 54, sheet 37, generate 77, intake 22, export 11.
  - **Control inside K:** the unfiltered walk on the same trap bill, with the same answers, accepts all three items. So K tests the filter, not a bill with nothing to trap.
  - **K2 controls, each mutation alone, all caught:** `--flagged` ignored (`chosen = offered`) → 4 FAILs; `selection_errors` always empty → 3 FAILs.
  - **Real-path checks (read-only, stdin closed):** `--only c999 c2` on the Finance Act exited 1 naming c999; `--flagged` walked 0 and reported "58 other draft(s) left out", matching the 58 drafts counted from the JSON; both argparse refusals fired. The Finance Act SHA was unchanged.

- 2026-10-22: CI (`.github/workflows/ci.yml`) now runs the full "Run them" list on every PR, on Ubuntu. Job `test` covers the self-tests, build and check-build. Job `content` runs real-data `lint`. First run: identical counts to the 2026-10-19 entry. The POSIX branch of the bill lock (`fcntl.flock`; review J, sheet L) passed. Until now it was "unexercised on this machine".

- 2026-10-19: the bill lock, direct-call usage log and permanent banned phrases.
  - **New checks:** review scenario J, sheet section L and generate "a bill another writer holds…" (the lock); generate "every direct call's usage is logged"; lint now guards all 19 doctrine phrases. Counts are now lint 36, review 43, sheet 37, generate 77, intake 22, export 11.
  - **K2 controls, each mutation alone, all caught:**
    - a different lock file per call → review 2 FAILs, sheet 1, generate 1;
    - the `_log_usage` call removed → 1 FAIL;
    - `batili` deleted from `banned.txt` → 1 FAIL.
  - **Not hanging:** with the lock broken, scenario J ends at its first prompt (stdin is emptied), and L uses an unedited sheet, so neither can sit waiting for input.
  - **Real-path checks (a lock held from a second process):** a real `politricks review finance-act-2026` exited 1 with the Finance Act SHA unchanged, and `politricks verify` refused the held Public Participation bill through the CLI (exit 1, SHA unchanged).
  - **Cross-process and crash release:** checked by the cold reviewer by hand (holder process → second process `REFUSED`; holder force-killed → `ACQUIRED`). The self-tests take both locks in one process, so this stays a manual control. The POSIX `fcntl` branch is unexercised on this machine.

- 2026-10-14: K4 control on record. Ian's first accepts broke two generate checks that still read the real bill; they now use the reset copy. The gap row is removed.
- 2026-10-14: created by the first `test-kinds` audit, after the review-fingerprint work. That audit ran the K7 and K9 controls for the first time on this change (both FAILed as they should). Its inventory also caught two faults in the skill's own search (see the skill's Temper note).
