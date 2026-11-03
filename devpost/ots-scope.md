# OpenTimestamps for official sources: scope

**Status:** decided 2026-11-04 (Ian took all five recommendations in §5). Spike part 1 done the
same day (§2.1); part 2 waits on Bitcoin. Nothing in the pipeline, workflow or site is built yet.

**Current path:**
1. ✅ Spike part 1: library installs on Python 3.14, a real stamp of the Finance Act PDF, binding
   checked offline (`devpost/ots-spike/`).
2. ⏳ Spike part 2, from 2026-11-05: run the upgrade until it names a Bitcoin block, then check the
   proof on opentimestamps.org. Command in §2.1.
3. Build `politricks stamp` (§2.2), the `stamp.yml` workflow (§2.3), the site line (§2.4), the README
   (§2.5), then the backfill (§2.6).
**Why it exists:** the first cloud session (2026-11-03) was BLOCKED on this. Its container's network
allowlist refused every calendar host. The session suggested running stamping on GitHub's runners,
and Ian asked for that bigger build to be scoped. Budget: the PoliTricks line has about $17 of its
$20 left (`haven-ea/decisions/log.md`, 2026-11-03 and 2026-11-04).
**Reviewed:** an independent Sonnet cold review read this plan against the code on 2026-11-04. Its
findings, with their citations checked, are folded in below and marked *(review)*.

## 1. What it adds, and what it does not prove

Each bill page shows the official PDF's SHA-256, so a reader can check that the legal text came from
that file. An OpenTimestamps proof adds one thing: **the file existed, byte for byte, no later than
a given Bitcoin block.** Nobody, PoliTricks included, can quietly swap a source afterwards and
pretend it was always there.

It does **not** prove:
- that the government published the file, or published it on that date;
- that the file was fetched on the "Fetched" date shown. The five bills were fetched in September
  and October; a stamp made now proves "by November 2026", nothing earlier;
- that the explanations are right;
- by itself, that the block named is real. Our pipeline can't check the Bitcoin chain (no node), so
  "block N" comes from the calendar's answer. The reader's independent check is what proves it
  *(review)*. The page copy says so.

The copy has to say exactly this much (the honesty doctrine in `CLAUDE.md`). Over-claiming would
damage the one thing the footer exists for.

## 2. The build

**Stamp the committed file, never a fresh download.** Kenya Law answers the runner with a WAF
challenge since 2026-11-02 (`update.yml` header), so a step that re-fetched would fail. The PDF in
`sources/<bill-id>/` is the file whose SHA-256 the page shows; that is what gets stamped.

**Scope: the five bills' official PDFs.** The constitution is not a bill source: it has no `Source`
record and no bill JSON, and `constitution.py` keeps its own `sha256` in `src/data/constitution.json`
(`ConstitutionFile`, `extra="forbid"`, `constitution.py:122`) *(review)*. Stamping it needs a second
record type and a second place on the site. It is a follow-up, not this build.

1. **Spike, before any code (local, no credits).** Scripts and the proof are in `devpost/ots-spike/`.
   - **Part 1, done 2026-11-04 on Ian's machine (Windows, Python 3.14.3):**
     - **The `ots` command-line tool does not run on Windows.** `opentimestamps-client` 0.8.0 installs,
       but `ots` crashes on import: `python-bitcoinlib` loads OpenSSL through `ctypes` and finds none
       (`TypeError: LoadLibrary() argument 1 must be str, not None`, from `bitcoin/rpc.py`). So the
       `.bak`-on-upgrade and default-calendar-count questions were never reached through it.
     - **The `opentimestamps` library works, and it is the path.** `opentimestamps` 0.5.0 (with
       `python-bitcoinlib` 0.12.2 and `pycryptodomex` 3.24.1 as its dependencies) stamped and upgraded
       without the CLI. `spike_stamp.py` sent the Finance Act PDF to 4 calendars: **4 of 4 answered**.
       The proof is 875 bytes. Our code sets the minimum of 2 answers itself, and writes no `.bak`.
     - **It binds to the right bytes.** `spike_check.py`: the proof's file digest equals the PDF's
       SHA-256 (`809ed38b…19af`, the same value `src/data/bills/finance-act-2026.json` shows on the
       page), and a copy with one byte flipped does not match.
     - **Pool hosts are not the hosts that finish the proof.** The stamp went to
       `a.pool.opentimestamps.org`, `b.pool.opentimestamps.org`, `a.pool.eternitywall.com` and
       `ots.btc.catallaxy.com`. The pending proof names `alice.btc.calendar.opentimestamps.org`,
       `bob.btc.calendar.opentimestamps.org`, `btc.calendar.catallaxy.com` and
       `finney.calendar.eternitywall.com`, and the upgrade has to reach those. Any network allowlist
       needs both sets.
     - `spike_upgrade.py`, run straight after: all 4 answered "Pending confirmation in Bitcoin
       blockchain", and the proof was left byte-identical.
   - **Part 2, from 2026-11-05:**
     `uv run --no-project --python 3.14 --with opentimestamps python devpost/ots-spike/spike_upgrade.py devpost/ots-spike/finance-act-2026.act.pdf.ots`
     until it prints a block, then drop `sources/finance-act-2026/act.pdf` and that proof on
     opentimestamps.org. Exit: one complete proof that a verifier other than our code accepts.
2. **Pipeline command: `politricks stamp`** (`pipeline/politricks/stamp.py`, `cli.py`).
   - One pass, idempotent: stamp every bill source that has no proof, upgrade every pending proof,
     leave complete proofs alone. A stamp adds a random nonce, so idempotency rests on "a proof file
     exists", never on re-deriving one *(review)*. A second run with nothing to do writes nothing.
   - Built on the `opentimestamps` library, never the `ots` CLI (it crashes on Windows; §2.1). The
     spike scripts are the starting code.
   - Proof file: `sources/<bill-id>/<file>.ots` (e.g. `act.pdf.ots`). Upgrading through the library
     writes no `.bak`.
   - Outcomes per source, in the style of `verify`: `STAMPED`, `PENDING`, `COMPLETE` (with the block
     height the proof names), `UNREACHABLE` (calendars didn't answer: no proof file, no field, exit 0,
     listed last) and `ERROR` (exit 1).
   - The record on the bill JSON's source: `timestamp: { sha256, status, ots_file, bitcoin_block }`.
     - **Omitted while absent**, the way `ocr` is (`models.py:86-91` drops it when False), so the five
       bill files stay byte-identical until a stamp exists. The Zod twin (`src/lib/schema.ts`) is
       `.optional()`, since today's JSON has no key *(review)*.
     - **Carries the `sha256` it stamped.** `fetch` rebuilds every `Source` from the registry and
       keeps only `fetched_at` when the hash is unchanged (`fetch.py:212-239`), so it must also keep
       `timestamp` when the hash matches, and drop it (and move the old proof aside) when the file
       changed. `ocr.py` rebuilds sources too and needs the same rule *(review)*.
   - Takes `bill_lock(path)` from load to save, like every command that saves a bill *(review)*.
   - `--self-test`, offline, calendars stubbed: the proof's digest equals the file's SHA-256; a
     one-byte change to the file fails the match; an unreachable calendar leaves no proof and no field;
     a second run writes nothing; the real `sources/` and bills are byte-identical afterwards; a
     re-fetch with a changed hash drops the stale `timestamp`.
3. **Workflow: its own, not the weekly review PR** *(review, the biggest change)*.
   - The weekly PR (`update.yml`) uses one branch, `auto/weekly-update`, deleted after each run, and
     every run starts from `main`. If a weekly PR holding pending proofs isn't merged before the next
     Monday, that run starts again from `main`, stamps afresh and never upgrades. Proofs would stay
     pending for as long as Ian's review takes, and Ian's Sheng review is meant to take time.
   - So: a separate `stamp.yml` (daily, plus `workflow_dispatch`) that checks out a long-lived
     `auto/timestamps` branch if it exists, runs `politricks stamp` there, and pushes to the same
     branch. Its PR stays open and fills up; upgrades happen on the branch whatever the merge timing.
     Merging it is mechanical: proofs and one JSON field, no text to review.
   - `add-paths`: `sources/**/*.ots` and `src/data/bills/*.json`. These are git pathspecs, where `*`
     crosses `/`, so the pattern matches `sources/finance-act-2026/act.pdf.ots` *(review)*.
   - Red run only on `ERROR`. UNREACHABLE goes at the top of the PR body, like verify's.
   - `.gitattributes` gains `*.ots binary`, beside `*.pdf binary` *(review)*.
4. **Site (`src/components/SourceFingerprint.astro`).** One line per source, from the JSON:
   - complete: "Timestamped: the proof places this file's fingerprint in Bitcoin block N. Check it
     yourself." Verify steps link the **repo copies** of the PDF and the `.ots` on GitHub, not the
     official URL. The official URL is behind a WAF now and may later serve different bytes; the
     stamped bytes are the repo copy, and `.ots` files aren't served by the site *(review)*;
   - pending: "Timestamp pending";
   - none: nothing.
   - `scripts/check-build.mjs` asserts the line matches the JSON, inside its existing per-source loop.
5. **README:** "Verify a timestamp yourself", next to "Verify a fingerprint yourself".
6. **Backfill:** the first run stamps the five bill PDFs.

Out of scope: the constitution (above), stamping the bill JSON or the explanations, making stamping
block a merge, and running a Bitcoin node.

## 3. What we might get wrong on the first try

- **Proofs stuck pending behind a slow review.** Covered by the separate workflow and branch in §2.3;
  get this wrong and the weekly PR quietly restarts every proof *(review)*.
- **A stale proof after a re-fetch.** If `fetch` keeps a `timestamp` whose `sha256` no longer matches,
  the page would say a new file was timestamped when only the old one was *(review)*.
- **A diff on every bill on the first save.** A plain optional field writes `"timestamp": null` into
  all five JSONs and breaks byte-identical output *(review)*.
- **Promising a date we can't show.** A proof names a block *height*, not a time. A date would need a
  third-party lookup. The block number with a link needs none.
- **Expecting `ots verify` to work in CI.** It needs a Bitcoin Core node `[MEDIUM]`. The self-test
  checks the digest binding offline; the full check is the reader's.
- **Proofs made, then dropped.** `add-paths` leaves out anything not listed, the run still looks
  green, and no proof reaches the PR. Check the first PR's diff, not the log.
- **No CI on the proofs.** A PR opened with the built-in `GITHUB_TOKEN` doesn't trigger `ci.yml`
  (noted in `update.yml`). Someone has to push to it, or close and reopen it, before merge.
- **A calendar outage read as success.** Too few calendar answers must mean UNREACHABLE: no proof,
  no field.
- **Verify links to the wrong bytes.** Pointing readers at the official URL instead of the repo copy
  would make a correct proof fail to verify the day the official file changes *(review)*.
- **Two locks, one file.** `stamp` running while `generate` holds a bill must refuse, not wait or
  overwrite. That's what `bill_lock` already does, if `stamp` takes it.
- **Reaching for the `ots` CLI.** It's the obvious tool and it crashes on Windows (§2.1). A workflow
  step that shells out to `ots` would work on Linux and fail for Ian locally.
- **An allowlist with only the pool hosts.** Stamping works through them, but the upgrade calls the
  backend calendars the proof names (§2.1). A cloud environment, firewall or proxy that allows only
  the pool hosts stamps fine and then never completes.
- **Untested self-tests.** Reading the code, `intake --self-test` snapshots `sources/` before and
  after and `ocr --self-test` only globs `*.ocr.*`, so new `.ots` files shouldn't break them
  *(review)*. Not yet checked: whether `lint`, `export` or `review` self-tests fix the key set on
  `sources[]`. Grep before building, and run all of them after the backfill.

## 4. Risks

| Risk | Likelihood | Impact | Guard |
|---|---|---|---|
| The library doesn't work on Python 3.14 | settled 2026-11-04: `opentimestamps` 0.5.0 works; the CLI doesn't on Windows | none if the CLI is never used | build on the library |
| The library goes unmaintained | `[UNKNOWN]` | a future Python breaks it | pinned in `uv.lock`; the protocol subset we use is small |
| The page over-claims what a stamp proves | medium on a first draft | credibility, the site's whole point | §1 copy, reviewed by Ian before merge |
| A stale or stuck proof (re-fetch, slow review) | medium if unhandled | a false or never-finished claim | `sha256` on the record; the separate branch |
| A public calendar goes away before a proof is upgraded | low | that proof restamps; nothing false shown | daily upgrade on the timestamps branch |
| The explorer linked for a block disappears | low | a dead link; the proof still stands | link only |
| New dependency (`opentimestamps`, `python-bitcoinlib`, `pycryptodomex`) | low | supply chain | pinned in `uv.lock`, reviewed like any dependency |
| Cost | low | about $6–10 of the $17 left, at the one measured $3 a session `[LOW]` | spike first; stop past $10 |

## 5. Decisions

**Decided 2026-11-04: Ian took the recommended option on all five.** The block number with no date;
the spike locally; a separate `stamp.yml` on `auto/timestamps`; the constitution left for later; the
library accepted. The spike then narrowed the last one: `opentimestamps`, not `opentimestamps-client`
(§2.1). The options as they were put:

1. **Copy:** "the proof places this file's fingerprint in Bitcoin block N, check it yourself", with
   a block number and no date (recommended)? Or pay for a block-time lookup to show a date?
2. **Where the spike runs:** locally on Ian's machine (open network, no credits), or in a cloud
   session after adding the calendar hosts to its allowlist?
3. **Workflow shape:** a separate daily `stamp.yml` on a long-lived `auto/timestamps` branch
   (recommended), accepting one more PR to merge now and then?
4. **The constitution:** leave it for a follow-up (recommended), or widen this build?
5. **Dependency:** accept `opentimestamps-client`, or write the protocol subset if the spike shows the
   library is a problem?

## Grounding

Read from `main` at `d945747` on 2026-11-04: `.github/workflows/update.yml` (steps, `add-paths`,
`branch: auto/weekly-update` with `delete-branch: true`, the WAF note); `sources/` (19 files,
5,399,098 bytes); each bill JSON's `sources[]` (one PDF each; no bill lists the constitution);
`pipeline/sources.yaml`; `src/components/SourceFingerprint.astro`; the README's fingerprint section;
`pipeline/.python-version` (3.14); `models.py:78-92` (the `ocr` serializer); `fetch.py:210-240` (how
sources are rebuilt and merged); `constitution.py:69-75, 122-128`; `.gitattributes` (`*.pdf binary`,
no `.ots`); and the tracked `public/` (only `public/fonts`). `src/lib/reviewHash.ts` hashes civic
blocks' `source_url`s, not the bill's `sources[]`, so a `timestamp` field moves no review stamp
`[MEDIUM]`: read, not tested. Measured in the spike (§2.1): the library on 3.14, the CLI's Windows
crash, 4 of 4 calendars, the digest binding, and the pool-to-backend host split. Still general
knowledge `[MEDIUM]`: that `ots verify` needs a Bitcoin node, and how long confirmation takes.
