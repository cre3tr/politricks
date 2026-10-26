# PoliTricks

Kenyan bills explained in English, Kiswahili and Sheng, each explanation
sitting beside the official legal text it explains. The legal text is copied
word for word from the official PDF, and that PDF's SHA-256 fingerprint is on
every bill page, so anyone can check the source themselves.

Explanations are written by Claude and reviewed by a person before they are
marked *Reviewed*. Anything not yet reviewed says so on the page.

Live: https://poli-tricks.vercel.app

## Run the site

Requirements: Node 22.22+ (24 recommended), Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
npm ci
npm run dev            # http://localhost:4321
```

The site is prebuilt from the JSON in `src/data/bills/`. Two small live
helpers, Match (your own words to the clauses that fit you) and Explain (a
draft for a clause with no explanation yet), need an Anthropic API key:

```bash
cp .env.example .env   # then put your key after ANTHROPIC_API_KEY=
```

Without a key the site still works. The helpers fall back quietly.

`npm run build` type-checks and builds. Bare `astro build` skips the type check.

## The pipeline

The Python pipeline in `pipeline/` turns an official PDF into the bill JSON
the site reads. Its CLI is `politricks`:

```bash
uv sync --project pipeline
uv run --project pipeline politricks fetch <bill-id>      # download + fingerprint
uv run --project pipeline politricks extract <bill-id>    # clauses, verbatim-checked
uv run --env-file .env --project pipeline politricks generate <bill-id>   # Claude drafts (spends credit)
uv run --project pipeline politricks lint                 # banned phrases, neutrality, changed-since-review
uv run --project pipeline politricks review <bill-id>     # a person accepts or edits each draft
uv run --project pipeline politricks verify               # re-download sources, compare fingerprints
```

Most commands have an offline `--self-test` (`fetch`, `probe` and `samples`
do not). The CI workflow (`.github/workflows/ci.yml`) runs every self-test, plus `node scripts/check-api.mjs`,
the build and `node scripts/check-build.mjs <bill-id>` for each bill.

## Verify a fingerprint yourself

Each bill page ends with its source: the official URL, the date it was
fetched, and the SHA-256 of the PDF. To check it:

1. Download the PDF from the official URL on the page.
2. Hash it:
   - Windows: `certutil -hashfile <file.pdf> SHA256`
   - macOS / Linux: `shasum -a 256 <file.pdf>`
3. Compare with the SHA-256 on the page. The copies PoliTricks used are also
   in `sources/<bill-id>/` in this repository.

A match means the legal text on the page came from exactly that official
file. A mismatch means the official file has changed since it was fetched;
`politricks verify` checks for this and the page then shows a banner.

## Where things are

- `devpost/`: the plan (`scope.md`, `prd.md`, `spec.md`) and the build log (`checklist.md`).
- `src/`: the Astro site. `src/lib/api.ts` and `src/pages/api/` are the live helpers.
- `pipeline/`: the Python pipeline, prompts (`pipeline/prompts/`) and civic facts (`pipeline/civic/`), each with its source, drafted by Claude and not yet checked by a person.
- `sources/`: the fingerprinted official PDFs.
