# OpenTimestamps for official sources: scope

**Status:** proposed 2026-11-04, not decided. Ian decides; nothing here is built.
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

1. **Spike, before any code (local, no credits, about an hour plus a day's wait).**
   - Install `opentimestamps-client` under the pipeline's Python 3.14 (`pipeline/.python-version`;
     `pyproject.toml` allows 3.12+, but 3.14 is what runs).
   - `ots stamp` one PDF, wait for Bitcoin, `ots upgrade`, then check it on opentimestamps.org.
   - Confirm three things the plan assumes from general knowledge: the proof is named
     `<file>.pdf.ots`; `upgrade` leaves a `.bak` beside it *(review)*; and how many calendar answers
     the client needs by default.
   - Exit: one complete proof that a verifier other than our code accepts. If the library won't run on
     3.14, stop and decide between an older Python for this step and writing the small part of the
     protocol we need.
2. **Pipeline command: `politricks stamp`** (`pipeline/politricks/stamp.py`, `cli.py`).
   - One pass, idempotent: stamp every bill source that has no proof, upgrade every pending proof,
     leave complete proofs alone. A stamp adds a random nonce, so idempotency rests on "a proof file
     exists", never on re-deriving one *(review)*. A second run with nothing to do writes nothing.
   - Proof file: `sources/<bill-id>/<file>.ots` (e.g. `act.pdf.ots`). Upgrade through the library,
     or delete the `.bak`, so no stray file appears.
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
- **Untested self-tests.** Reading the code, `intake --self-test` snapshots `sources/` before and
  after and `ocr --self-test` only globs `*.ocr.*`, so new `.ots` files shouldn't break them
  *(review)*. Not yet checked: whether `lint`, `export` or `review` self-tests fix the key set on
  `sources[]`. Grep before building, and run all of them after the backfill.

## 4. Risks

| Risk | Likelihood | Impact | Guard |
|---|---|---|---|
| `opentimestamps-client` doesn't run on Python 3.14, or is unmaintained | `[UNKNOWN]` | the build stalls | the spike, before any code |
| The page over-claims what a stamp proves | medium on a first draft | credibility, the site's whole point | §1 copy, reviewed by Ian before merge |
| A stale or stuck proof (re-fetch, slow review) | medium if unhandled | a false or never-finished claim | `sha256` on the record; the separate branch |
| A public calendar goes away before a proof is upgraded | low | that proof restamps; nothing false shown | daily upgrade on the timestamps branch |
| The explorer linked for a block disappears | low | a dead link; the proof still stands | link only |
| New dependency (`opentimestamps-client`, `python-bitcoinlib`) | low | supply chain | pinned in `uv.lock`, reviewed like any dependency |
| Cost | low | about $6–10 of the $17 left, at the one measured $3 a session `[LOW]` | spike first; stop past $10 |

## 5. Decisions for Ian before building

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
`[MEDIUM]`: read, not tested. The OpenTimestamps behaviour (the `.pdf.ots` name, `.bak` on upgrade,
verify needing a node, the default calendar count) is general knowledge `[MEDIUM]`; the spike tests it.
