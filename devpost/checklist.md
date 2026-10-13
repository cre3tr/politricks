---
doc: checklist
status: approved
---

# Build Checklist

Build mode: fast — with human-readable comments in the code (learner request)

## Slices

- [x] **1. The official Finance Act 2026 becomes fingerprinted, word-for-word clauses**
  Becomes usable: `politricks fetch` and `politricks extract` turn the official Finance Act 2026 PDF (Kenya Law) into a bill JSON of clauses. Each clause is verbatim-checked against the PDF and carries its own fingerprint. `politricks verify --self-test` proves the fingerprint catches a one-byte change.
  Why now: This is the single biggest unverified risk in the whole plan: do official, text-extractable PDFs exist, and can clauses be pulled out word for word? It's the one allowed technical-first slice, because it independently proves that risk and leaves runnable evidence. It bootstraps the Python pipeline, and every later slice stands on it.
  PRD ref: `prd.md > Product Decisions` (Finance Bill hand-loaded from a hashed copy), `prd.md > Open Questions`
  Spec ref: `spec.md > Fetch & Fingerprint`, `spec.md > Extract Clauses`, `spec.md > Verify`, `spec.md > Data Model`, `spec.md > Parliament of Kenya (parliament.go.ke)`, `spec.md > Kenya Law (kenyalaw.org)`
  Build: `git init`. Scaffold `pipeline/` with uv (pdfplumber, pydantic, httpx, typer or argparse). Find and pin the official Finance Bill 2026 and Finance Act 2026 URLs on the allowlisted domains in `sources.yaml`. Write `hashing.py` (`normalise()`, SHA-256), `fetch.py` (allowlist, stores to `sources/`), `extract.py` (clause split, verbatim check, `overrides/`), `verify.py` (with `--self-test`), `models.py` (Bill/Clause per the Data Model) and `cli.py`.
  Verify (mechanical): `politricks fetch finance-act-2026` prints a SHA-256, and `certutil -hashfile` on the saved file prints the same value. `extract` exits 0 with a clause count above 0, and a printed sample clause matches the PDF text. `verify --self-test` prints `unchanged` for the intact copy and `CHANGED` for the one-byte-altered copy. A deliberately corrupted clause in a scratch copy makes `extract`'s verbatim check fail.
  Learner check: Open `src/data/bills/finance-act-2026.json`, pick any clause, find it in the official PDF, and confirm it's word for word. Then run `certutil -hashfile` on the PDF yourself and compare it with the fingerprint in the JSON.
  Commit: `Fetch, fingerprint and extract Finance Act 2026 sections`

- [x] **2a. A Finance Bill clause in Sheng, right next to the legal text (sample explanations)**
  Becomes usable: `npm run dev` → `/bills/finance-act-2026` shows the bill's clause cards. Each card has the legal text beside its explanation, with a global EN / SW / Sheng toggle, a per-card flip and the source fingerprint footer, all in the PoliTricks look. Three pocket-hitting clauses and the summary carry **hand-written sample** explanations, badged *Sample text — placeholder*. The rest show *Explanation not ready yet*. `generate.py` exists and passes an offline self-test.
  Why now: This is the unique kernel and the demo's "oh, that's cool" moment, so everything else gets built around a proven kernel. There's no API key yet (the learner chose to build without it first), so the page runs on clearly labelled samples and the Claude pipeline is proven offline.
  PRD ref: `prd.md > What It Does`, `prd.md > Language Switching`, `prd.md > Look and Feel`
  Spec ref: `spec.md > Generate (Claude)`, `spec.md > Bill Page (/bills/[id])`, `spec.md > Clause Card`, `spec.md > Language Toggle`, `spec.md > Head Script (src/components/HeadScript.astro)`, `spec.md > Look and Feel`, `spec.md > Data Model`, `spec.md > File Structure`
  Build: Scaffold Astro 7 + `@astrojs/vercel` + Tailwind v4 + Zod, and confirm the versions install together. Write `schema.ts` (the Zod twin of `models.py`), `politricks samples` + `pipeline/samples/finance-act-2026.yaml`, `generate.py` (Batches with `output_config` JSON schema, no fallbacks in batch, direct refusal retry with `fallbacks: "default"`) + `prompts/explainer.md` + an offline fixture self-test. Build `BaseLayout`, `HeadScript`, `Header`, `LanguageToggle`, `ClauseCard`, `SourceFingerprint`, `highlight.ts`, `global.css` (tokens, highlighter, CSS-only language switching), `bills/[id].astro` (Zod-validated at build) and `scripts/check-build.mjs`.
  Verify (mechanical): `npm ls` is clean for the trio. `samples` applies, and then `extract --check-json` still passes. `generate --self-test` passes with a dead proxy and an empty key. `npm run build` exits 0. A broken bill JSON fails the build and names the file. `check:build` passes: every clause's legal text is present, the sample Sheng is present, sample badge count = samples + 1, "AI draft" and "Reviewed" appear 0 times, and both SHA-256s are present. Browser check: the global and per-card language flips work and persist; the legal text sits above on mobile and beside on desktop; the font is served from the same origin.
  Learner check: Open `http://localhost:4321/bills/finance-act-2026` in a phone-sized window. Read a sample clause in Sheng beside the legal text, flip the whole page to Kiswahili, then flip one card back to Sheng. Does it look and sound like PoliTricks? (The Sheng here is sample text; you'll review the real generated Sheng in slice 3.)
  Commit: `Show Finance Bill clauses beside sample trilingual explanations`

- [x] **2b. Real Claude explanations for every clause**
  Becomes usable: Every Finance Bill clause and the summary carry Claude-generated EN/SW/Sheng explanations (badged *AI draft*), replacing the samples.
  Why now: This runs as soon as the API key exists, and before slice 3's review gate, which needs real drafts to review. It proves the second risk: Claude via Batches with structured outputs.
  PRD ref: `prd.md > What It Does`
  Spec ref: `spec.md > Generate (Claude)`, `spec.md > Anthropic Messages API`
  Build: The learner creates the API key and puts it in `.env`. Run a tiny live call, a 1-item test batch, then `generate finance-act-2026`, retrying refusals directly.
  Verify (mechanical): A tiny `claude-sonnet-5-5` call returns non-empty text. The 1-item batch returns output that parses with the Pydantic model; if the schema is rejected, record a revision and fall back to direct `messages.parse`. After `generate`, every clause has an `explanation`, no item is `"sample"`, all are `"draft"`, the Zod schema validates, and `check:build` shows "AI draft" on every card.
  Learner check: Reload the Finance Bill page and read three clauses in Sheng. Do Claude's explanations sound natural and neutral? Note anything to fix in review.
  Commit: `Generate Claude explanations for Finance Act 2026`

- [ ] **3. Nothing ships without your review, and banned phrases can't get in**
  Becomes usable: `politricks lint` fails on any banned phrase or neutrality flag. `politricks review` walks every draft text, flagged items first, and marks each one reviewed. The page shows *Reviewed* badges for reviewed clauses and *AI draft* for the rest.
  Why now: Your doctrine (neutral, Sheng reviewed by you, never "unconstitutional") has to be mechanical before more content piles up. Every later slice's content goes through this gate.
  PRD ref: `prd.md > What It Does` (neutral), `prd.md > Non-Goals`, `scope.md > Quality Guardrails`
  Spec ref: `spec.md > Lint`, `spec.md > Review`, `spec.md > Data Model`
  Build: Write `lint.py`, `prompts/banned.txt`, `review.py` (terminal walk: accept / edit / skip, flagged first, refuses to finish while lint fails), and the badge rendering in `ClauseCard`.
  Verify (mechanical): Plant "kinyume cha katiba" in one explanation in a scratch copy: `lint` exits non-zero and names the clause. The clean file passes. After reviewing one clause, its JSON shows `"reviewed"` and the built page shows the *Reviewed* badge on that card only.
  Learner check: Run `politricks review finance-act-2026` and review the Sheng for 3–5 clauses you'd put in the demo, editing anything that sounds off. Reload the page and see those cards say *Reviewed*.
  Commit: `Add lint and review gate for all generated text`

- [ ] **4. PoliTricks knows who you are, and puts what hits you first**
  Becomes usable: A first visit goes to `/welcome` (chips, 80-character words, data notice, Skip). Bill pages then show your groups first on the first paint, with "See everyone" and per-bill chips. Your own words call **Match** and float up matching clauses. A clause with no explanation calls **Explain** and shows a labelled draft. Both APIs fall back quietly on failure.
  Why now: This completes "what it means for you" on the kernel page, and it brings in the two live helpers and their failure paths while there's still only one bill to test against.
  PRD ref: `prd.md > First-Visit Profile`, `prd.md > What It Does`, `prd.md > States and Boundaries`
  Spec ref: `spec.md > Welcome Page (/welcome)`, `spec.md > Head Script (src/components/HeadScript.astro)`, `spec.md > Bill Page (/bills/[id])`, `spec.md > Match API (POST /api/match)`, `spec.md > Explain API (POST /api/explain)`, `spec.md > Data Model` (Reader profile)
  Build: `guard.ts`, `storage.ts`, `ProfileSchema`, `claude.ts` (loads the shared prompts and banned list), `welcome.astro`, `ProfileChips`, `GroupsPanel`, HeadScript (groups + redirect), `api/match.ts`, `api/explain.ts`, `prompts/matcher.md`. Re-run `generate` so group entries and clause `groups[]` exist.
  Verify (mechanical): With cleared storage, `/bills/finance-act-2026` redirects to `/welcome`. After saving "trader", the trader block comes first in the served HTML plus head-script output (browser check). `curl` Match with "I sell mitumba Gikomba" returns 200 with valid clause IDs; empty words return 400; a bad key (simulated) makes the page show the quiet fallback. Explain returns 409 for an explained clause and 200 with the draft label for a blanked one. A banned-phrase output is discarded (unit check with a stubbed response).
  Learner check: In a private window, open the bill page, go through the welcome screen as yourself (pick groups, type a few of your own words), and see what floats to the top. Then open "See everyone" and confirm nothing is hidden.
  Commit: `Add first-visit profile, groups-first view and live helpers`

- [ ] **5. Home shows the two live bills**
  Becomes usable: `/` lists the Tertiary Education, Placement and Funding Bill 2026 and the Public Participation Bill 2025, with title, chamber, stage and *last checked*, and each opens its own bill page with explained clauses.
  Why now: This gives the core journey its real front door, and it proves the pipeline generalises beyond the Finance Bill (different bill layouts, and a Senate bill).
  PRD ref: `prd.md > Home: Active Bills and Una'tafuta?`, `prd.md > The Core Journey` (steps 2–3)
  Spec ref: `spec.md > Home Page (/)`, `spec.md > Intake`, `spec.md > Extract Clauses`
  Build: Add both bills to `sources.yaml` with pinned official URLs. Write the `intake` command (the manual first load: fetch → extract → generate for new entries), then run it plus `lint`, and `index.astro` with the Active Bills list and `LastChecked`. Add overrides as the verbatim check demands.
  Verify (mechanical): `intake` exits 0 for both bills (or a revision records a scanned/unavailable PDF), and `lint` passes. `npm run build` passes, and the home HTML contains both titles plus links that resolve to built bill pages.
  Learner check: Open `http://localhost:4321/`, tap each live bill, and check that its clauses and explanations make sense, especially the Tertiary Education bill from a student's point of view.
  Commit: `Ingest both live bills and list them on home`

- [ ] **6. Have your say: how, where and by when, with every fact sourced**
  Becomes usable: Each bill's *Have your say* tab shows the deadline, channels and requirements from your hand-entered civic YAML, each linked to the official notice with a *last checked* date. When participation isn't open, it shows the stage and what usually happens next, plus a stale warning after 7 days. **This is the minimum demoable line.**
  Why now: It completes PRD build steps 1–2. After this there's a coherent, demoable product even if nothing else lands.
  PRD ref: `prd.md > Have Your Say`, `prd.md > States and Boundaries`
  Spec ref: `spec.md > Civic Data`, `spec.md > Bill Page (/bills/[id])`, `spec.md > Data Model` (participation, stage_next)
  Build: The learner writes `pipeline/civic/<bill>.yaml` for all three bills from the official notices. Write `civic.py` (validation: every fact has a `source_url`; translation via `prompts/translator.md`), `BillTabs`, `ParticipationPanel` (open / not open / stale), then run `review` on the civic translations and add the home deadline countdown.
  Verify (mechanical): `civic` rejects a YAML with a missing `source_url` and accepts the real ones. The built pages show the correct panel variant per bill (the Public Participation Bill's 28 Sep deadline shows as not open or passed, depending on the build date). A `checked_at` set 8+ days back renders the stale warning.
  Learner check: Enter the civic facts yourself from the official notices, then open each bill's *Have your say* tab and check every date and channel against the notice you typed it from.
  Commit: `Add sourced Have your say for all bills`

- [ ] **7. It's live on Vercel**
  Becomes usable: A public URL serving the full minimum demoable line, with both live helpers working in production.
  Why now: It's right after the demoable line, so you can share a real link (Discord, early testers) while there's still time to use the feedback. It also surfaces deploy-only failures (functions, environment variables) before more is stacked on top.
  PRD ref: `prd.md > What We're Building` (steps 1–2)
  Spec ref: `spec.md > Where It Runs and How Someone Tries It`, `spec.md > Vercel`
  Build: The learner creates a GitHub repo and a Vercel project and sets `ANTHROPIC_API_KEY` in Vercel. Push, deploy, and add a README with run steps and "how to verify a fingerprint".
  Verify (mechanical): The deployment succeeds. `curl` on the production home returns 200 and contains both bill titles. A production `POST /api/match` returns 200. A production `/bills/finance-act-2026` contains the fingerprint.
  Learner check: Open the public link on your actual phone, over mobile data, and walk the demo path: welcome → Finance Bill → a Sheng clause → Have your say on a live bill.
  Commit: `Deploy to Vercel with README`

- [ ] **8. What changed: drafts and current law**
  Becomes usable: *What changed* shows, for each Finance Act 2026 section that amends another Act, the current law beside the amendment. For a bill amending an Act, it shows the current law beside the amendment. Bills with one version show the honest note plus a current-law comparison.
  Why now: This is PRD build step 3, and it depends on the fetch, extract and review machinery from slices 1–3.
  PRD ref: `prd.md > What Changed`
  Spec ref: `spec.md > Compare`, `spec.md > Data Model` (changes), `spec.md > Kenya Law (kenyalaw.org)`
  Build: Pin the Kenya Law URLs for the Acts the Finance Bill amends. Write `compare.py` (clause matching, `null` for added/dropped, amended-section lookup with the verbatim rule, Claude notes as drafts), `ChangeRow` and the *What changed* tab. Then run `review`.
  Verify (mechanical): `compare finance-act-2026` produces `changes[]`, including at least one `kind: "current_law"` entry. Every `before`/`after` is verbatim in its source PDF (scripted check). `lint` passes. The built tab shows the rows and the empty-state note on a single-version bill.
  Learner check: Open the Finance Bill's *What changed* tab and pick one change you remember from the 2026 debate. Does the before/after and its note match what actually happened?
  Commit: `Add What changed: drafts and current law`

- [ ] **9. Una'tafuta? and passed-bill pages**
  Becomes usable: Search on home finds bills by name or keyword in any language. Passed bills show the **Passed** label, and their *Have your say* tab becomes *Where it can still be challenged* (from civic YAML). `intake --check` proposes new items as a `sources.yaml` diff.
  Why now: This is PRD build step 4. It needs several bills in the system, which slices 5–8 provided.
  PRD ref: `prd.md > Home: Active Bills and Una'tafuta?`, `prd.md > Have Your Say` (passed), `prd.md > Automatic Bill Intake`
  Spec ref: `spec.md > Home Page (/)`, `spec.md > Intake`, `spec.md > Civic Data`, `spec.md > Parliament of Kenya (parliament.go.ke)`
  Build: Build `search-index.json` at site build, then the search UI with the *Hatujapata kitu* empty state. Add the passed variant of `ParticipationPanel`, the challenge avenues in the Finance civic YAML (which you write), and `intake --check` (Parliament and Gazette listings → proposed diff).
  Verify (mechanical): Searching "fedha" or "finance" finds the Finance Bill and a nonsense query shows *Hatujapata kitu* (browser check). The Finance page shows *Passed* and the challenge tab with source links. `intake --check` prints a proposed diff (or "no new items") with no errors.
  Learner check: Search for something the way a citizen would ("HELB", "tax", "ushuru") and see what comes up. Open the Finance Bill's challenge tab and check it reads as information, not advice.
  Commit: `Add Una'tafuta search, passed bills and intake check`

- [ ] **10. Suggested constitution articles**
  Becomes usable: Clause cards show "Suggested articles" chips. Tapping one opens the article's text in-app, labelled *"Suggested article — you judge"*, with the Kenya Law link and the constitution's fingerprint. Only each bill's slice of articles ships with its page.
  Why now: This is PRD build step 5, and it needs the constitution loaded before `generate` can suggest validated articles.
  PRD ref: `prd.md > Constitution Check`
  Spec ref: `spec.md > Constitution Loader`, `spec.md > Generate (Claude)`, `spec.md > Article Sheet`
  Build: `politricks constitution`, then re-run `generate` for `suggested_articles` (validated against the loaded articles), followed by `lint` and `review`. Then build `ArticleSheet` with per-page article slicing.
  Verify (mechanical): `constitution.json` has 264 articles and a fingerprint. Every suggested number exists in it. The built bill-page HTML contains only its suggested articles (grep for an unsuggested article's text finds nothing). `lint` passes.
  Learner check: Open a clause's suggested article. Does the suggestion feel relevant, and does the sheet make clear you're the judge?
  Commit: `Add suggested constitution articles`

- [ ] **11. Nairobi County bills and motions**
  Becomes usable: Home lists at least one Nairobi City County Assembly bill (bill page and tabs) and one motion (simpler Motion Card), both labelled *county* and fingerprinted from the Assembly's official site.
  Why now: This is PRD build step 6, the last boundary item. It's placed last because the county domain and documents are the least known.
  PRD ref: `prd.md > Automatic Bill Intake` (county)
  Spec ref: `spec.md > Motion Card`, `spec.md > Nairobi City County Assembly`, `spec.md > Fetch & Fingerprint` (allowlist)
  Build: Pin the Assembly's official domain into the allowlist. Add one county bill and one motion to `sources.yaml`, run intake, lint and review, then build `MotionCard` and the county labels.
  Verify (mechanical): Fetching from the pinned domain passes the allowlist (and a non-allowlisted URL is refused). The motion JSON has one clause and null participation. The build shows both with the *county* label.
  Learner check: Open the county motion and the county bill. Is it obvious they're county items, and does the motion's simpler card make sense?
  Commit: `Add Nairobi County bills and motions`

- [ ] **12. It keeps itself current (weekly PR)**
  Becomes usable: A GitHub Action runs weekly or on demand: intake check, verify, generate, lint. It then opens a *needs review* PR, and never auto-merges.
  Why now: Maintenance comes last, because it automates commands that must all exist and pass first.
  PRD ref: `prd.md > Automatic Bill Intake`, `scope.md > Later` (production path)
  Spec ref: `spec.md > Scheduled Update (GitHub Action)`, `spec.md > GitHub Actions`
  Build: `.github/workflows/update.yml` (the permissions block, `peter-evans/create-pull-request`). The learner adds the repo secret and enables "Allow GitHub Actions to create PRs".
  Verify (mechanical): A manual `workflow_dispatch` run completes green (`gh run view` shows success). With a deliberately altered registry entry, the run opens a PR labelled *needs review* (`gh pr list` shows it), which is then closed.
  Learner check: Trigger the workflow yourself from the Actions tab and open the PR it creates. Is it clear what changed and what you need to review?
  Commit: `Add weekly update workflow`

## Hands-on Checkpoints

- [ ] Early usable behavior explored — after slice 2 (the kernel page): look and feel, Sheng tone, clause-beside-explanation layout. Feedback here shapes every later page.
- [ ] Integrated core journey explored — after slice 6 (minimum demoable line): welcome → home → bill → What it does → Have your say, end to end.
- [ ] Final kick-the-tires exploration and feedback completed

## Final Review

- [ ] Final review complete — feedback resolved and learner confirms ready to ship

## Code Tour and App Map

- [ ] Learning activity complete — guided route, focused alternative, prior practice connected, or brief recap
- [ ] Optional edit and transfer reflection addressed — offered/declined/already covered/not applicable as appropriate
- [ ] `devpost/app-map.html` generated from finished code, checked, and shown, including a project-grounded practice to reuse

Activity and evidence: 
Route and stops: 
Edit outcome: 
Reflection: 
Activity mode: 

## Revisions

- Slice 2 split into 2a (sample explanations) and 2b (live generation). The learner has no Anthropic API key yet and chose to build without it first. `Review.status` gains `"sample"`, rendered as *Sample text — placeholder* and never as "AI draft" or "Reviewed".
- Batch requests carry no `fallbacks`, and refusals are retried directly with `fallbacks: "default"`. The Claude API reference states server-side fallbacks are rejected on the Batches API, and `messages.parse` can't be used inside a batch, so batch items use `output_config` JSON schema plus Pydantic validation.
- Files added beyond the spec's tree: `src/layouts/BaseLayout.astro` (shared page shell), `src/lib/highlight.ts`, `scripts/check-build.mjs` (built-HTML assertions), and the pipeline commands `probe` (PDF layout calibration) and `samples` (applies labelled sample explanations).
- The Finance demo uses the **Finance Act 2026** (Kenya Law) instead of the Finance Bill, and its ID is `finance-act-2026`. Every official copy of the Bill was a scan (parliament.go.ke original 0/124 pages with text, compressed copy 0/124), and Kenya Law doesn't host it. The learner chose the Act, shown exactly as in the file, over OCR or switching bills. Slice 8's "bill vs Act" for Finance moves to Later (see `prd.md > Product Decisions`).
- Prefer **Kenya Law** copies for every bill (slice 5 on). Parliament often publishes scans. Kenya Law copies had a text layer in every case checked.
- The Act's own text layer comes from a scan: small glitches ("Date ofA ssent", "( a)", a stray "�") and one drafting slip in the printed law ("the end of at item (a)", confirmed against the rendered page image). It's shown as-is, per the learner's choice. The site needs a short note saying the official file's text came from a scan (slice 2a).
- Pipeline pinned to **Python 3.14**, not 3.13. uv couldn't finish installing 3.13 here (a link-creation error), and 3.14 was already installed with wheels for every dependency.
- `Clause` gains `pages` (the PDF pages a section spans), so anyone can find it in the PDF. Section headings are read from the Act's **marginal notes**, not invented.
- Two-digit section numbers print with no space after the dot ("10.Section 15…"). The section-start pattern allows that, and the monotonic guard still rejects stray numbered items (e.g. "161.Bioethanol…").
- Slice 2a builder deviations, verified by the lead:
  - `badge.ts` holds the one badge switch that both the clause cards and the summary use.
  - Live `generate` passes `betas`/`fallbacks` as normal keyword arguments, because SDK 1.8's `beta.messages.parse` accepts them. It drops `output_config` when it uses `output_format`, because the SDK rejects both at once.
  - `check-build.mjs` strips tags to nothing rather than a space, because the inline highlight spans sit flush against the text. A one-character mutation in the data was confirmed to make it FAIL.
  - **Gap:** there's no full `astro check`/`tsc` gate yet, because `@astrojs/check` and `typescript` weren't installed. For now the only type check is Astro's build.
- The Browser pane was hidden during the 2a checks, so it painted no frames: a rAF-count control never finished and screenshots timed out. Style and layout measurements were used instead (viewport 1280×900 / 428×927, not 0×0). The visual look is checked by the learner at the hands-on checkpoint.
- **Sonnet diff review of slices 1–2a** (`4447870..666dbb6`): 0 blockers, 1 should-fix and 2 nits. All three were confirmed in the code and fixed:
  - `<meta charset>` now comes before the head script (built page: charset at byte 236, first script at 411);
  - the `\b` word boundary was added to the written-amount highlight regex;
  - bill IDs are validated (`^[a-z0-9]+(-[a-z0-9]+)*$`) wherever they become a path. `../etc`, `a/b` and `Finance` are refused.

  The reviewer noted it's from the same model family as the builder. The lead (a different model) independently read the doctrine-critical files (badge, clause card, page data gate, highlighter, explainer prompt).
- While fixing the regex, a shell-to-Python escaping slip wrote two raw backspace bytes (0x08) instead of `\b`, and the edit reported success. It was caught by reading the bytes back. It was fixed via byte codes, and a scan of 38 source files found no other control bytes.
- **Reorder (learner-approved, 2026-09-28):** slice 5's key-independent half runs ahead of 2b, 3 and 4 while those wait on the learner (API key, hands-on feedback). That half is: fetch, fingerprint and extract both live bills; the `intake` command; and the home page's Active Bills list. The rest of slice 5 waits: generating explanations for the new bills needs the key (2b), and `lint` doesn't exist yet (slice 3). Slice 5 stays unticked until both are done. Reason: this tests the "prefer Kenya Law" assumption on two more documents before anything depends on it.
- **Slice 5, pipeline half (done 2026-09-28):** both live bills come from **Kenya Law with full text layers**: Tertiary Education 35/35 pages (born-digital), Public Participation 10/10 (a scan). They extract to **68 and 21 clauses**, each matching its own Arrangement of Clauses, which pins the count. The "prefer Kenya Law" rule held on both.
- The Public Participation Bill copy on Kenya Law is **as first published (1 Oct 2025)**, not the National Assembly-approved text now before the Senate. Its source label says so.
- **Margin notes in noisy scans:** the notes are sometimes body-sized, and the page shifts between scans (the body ends at x≈315 on one page and x≈344 on another). A fixed margin line **dropped real body words** ("the", "five"), and the verbatim check couldn't see it, because it compares against the same filtered stream. The fix is `margin_mode: gutter`, which finds each page's empty strip between body and notes. It was confirmed against a rendered page image (clause 13) as a second observer. The Act and the Tertiary Education bill stay on the default rule, and their output is byte-identical.
- `start_noise` override: tolerates a scan speck before a clause number ("i 13.(1)…"). The text stays as published.
- `Source.scanned` flag: the "produced from a scan" note now shows only on scanned sources (Act, Public Participation). The born-digital Tertiary Education bill no longer carries a false claim. `fetch` now keeps only the old timestamp for an unchanged file, so edits to the registry take effect.
- Home: the Active Bills list plus a plain **Recently passed** list, which is how a reader reaches the Finance Act until slice 9's Una'tafuta? search replaces it.
- **Sonnet review of `48f66ff`:** 0 blockers.
  - **Independent confirmation that gutter mode dropped no body words:** per-page character counts matched, left of the gutter against the extracted body (e.g. p6 1505 = 1505).
  - **Fixed** (each confirmed in the code first):
    - The gutter is found by exact interval merging, with a minimum width (`gutter_min: 6`; real gutters are 7.8–14pt, word spaces about 3pt).
    - A page stops extraction if more than 25% of its words fall right of the gutter. Proven by pointing it at the page's left margin: "123/123 words… stopping rather than dropping it".
    - Gutter mode refuses to run without `gutter_search`.
    - Every page's moved-to-margin words are written to `bill.margin.txt`.
    - `start_noise` is scoped to named clause numbers (`[13]`).
    - `fetch` refreshes title, stage and last-checked from the registry on every run, and matches sources by file, not label. It previously never propagated registry edits and could duplicate a relabelled source.
    - The bill page shows "Text shown: <source label>" next to the clauses, so the Public Participation first-published caveat is visible there, not only in the footer.
    - The home page gains an `<h1>`, sorts by title, and has an empty state.
  - All three bill JSONs were byte-identical after the fixes.
  - **Not fixed, waiting on the learner:** scan-garbled marginal-note headings on the Public Participation Bill (e.g. c16 "oeveiopmer^tof specificguidelines forpubii 1C participation.") render beside the clauses. How to handle them is an editorial choice.
  - Declined NITs: a per-source scan note (every bill has one source today), and the clipped Tertiary Education header characters (harmless: they can't reach a heading).
- **Transcribed headings (learner decision, 2026-09-28):** the Public Participation Bill's 21 clause headings are typed by hand from the bill's own Arrangement of Clauses (PDF page 2, checked against a rendered image), because the scan garbled its marginal notes. They're marked `heading_source: "transcribed"`, and each card says so quietly: "heading transcribed from the printed page". Headings are signposts, not legal text. All 21 clause texts and fingerprints are unchanged. A misspelled clause ID in the transcriptions stops extraction.

### Key-free pass, 2026-10-07

- **Correction to the slice 2a "Gap" note above.** It said "the only type check is Astro's build". There was no type check at all, because `astro build` strips types without checking them. This made the `never` guarantee in `badge.ts` unenforced until `7f2d09a`.
- **Correction to the slice 5 reorder note above.** It listed the `intake` command in the key-free half that was marked done. No `intake` code existed (0 hits across 23 source files). It now exists (`4c40781`).
- **TypeScript gate (`7f2d09a`).** Added `@astrojs/check` 0.9.10 and `typescript` 6.0.3 (its peer range is `^5 || ^6`, so not 7.x).
  - `npm run check` runs `astro check`, and `npm run build` now runs `astro check && astro build`.
  - The first check found 0 errors over 15 files.
  - **Positive control:** a planted fourth review status makes the check fail at `badge.ts:28`, and the build stops before `astro build`.
  - The built output is byte-identical before and after.
- **Slice 3 lint and review gate (`f91e749`, fixed in `2171ec3`).**
  - New: `pipeline/prompts/banned.txt`, `lint.py`, `review.py`, and a test-support module `selftest.py`. `check-build.mjs` now checks each card's badge against its status, instead of whole-page counts.
  - **Banned list:** the spec's 7 doctrine phrases, plus 12 *proposed* Kiswahili/Sheng and English twins **for Ian to prune at his first review**. Matching is whole-phrase. A trailing `*` lets the last word continue (`illegal*` → "illegally"). `vote` stays unstarred, so "voting rights" (Tertiary c9, c31) is allowed.
  - **Lint:** a neutral flag fails only until a human accepts the clause; a banned phrase fails at any status. Samples are scanned too.
  - **Review:** walks drafts, plus any flagged item at any status, flagged ones first.
    - It refuses to accept text that still contains a banned phrase.
    - It saves on every accept.
    - Editing a reviewed text makes it a draft again until accepted.
    - A sample is never promoted to Reviewed.
    - End of input or Ctrl-C at any prompt means quit, never typed data.
  - Slice 3 stays **unticked**: its learner check needs real drafts (slice 2b).
- **`intake` (`4c40781`, fixed in `2171ec3`).** Runs fetch → scanned-PDF check → extract → generate → lint for every registry entry that has no loaded bill.
  - Without `ANTHROPIC_API_KEY`, generate is skipped with a message.
  - A failed load deletes its 0-clause skeleton JSON, so nothing half-built ships, and one failing bill never stops the rest.
  - `intake --self-test` covers the happy path (byte-equal to the committed bill apart from `fetched_at`), a scanned PDF, a download error, an off-allowlist source, resume, and retry.
- **Cold Sonnet review of `1ae50c4..4c40781`:** 1 blocker, 8 should-fix and 5 nits, all confirmed in the code.
  - **Blocker:** intake left a 0-clause JSON behind that the site would publish.
  - **Should-fix:**
    - any answer starting with "a" accepted an item;
    - end of input at the edit prompt wrote "q" over the draft;
    - the self-tests depended on the state of the real data;
    - inflections slipped past lint;
    - badges were only checked as page totals;
    - intake's narrow `except` let one failing bill abort the run;
    - review dead-ended on flagged items it wouldn't walk.
  - **Nit fixed:** `generate.apply` overwrote reviewed group entries.
  - The reviewer's 30 planted bugs left 22 surviving. After the fixes, all 17 named mutations are caught. The lead also independently deleted `invalid*` and got 2 FAILs.
  - **Deferred, with reasons:**
    - Lint doesn't gate `npm run build` yet. Slice 4 adds a TypeScript matcher for `banned.txt` (the Explain API needs one); add the build gate there. Until then: generate → lint → review → commit.
    - Review can't reach change notes or civic translations until slices 6 and 8 create them.
    - **Proposal for Ian:** tie a "Reviewed" status to the text a human actually saw (for example, a hash of the reviewed text), so a hand-edit can't keep a green badge.
- **Kenya Law is unreachable (2026-10-07).**
  - `new.kenyalaw.org` and `kenyalaw.org` return 403 to every request, including the home page.
  - Three observers on two networks agree: curl and the browser on this machine, and an off-network fetcher. Controls `parliament.go.ke` and `example.com` return 200.
  - Live `fetch`, `verify` and `intake` fail until it recovers. The committed PDFs and fingerprints are unaffected.
- **Found, not yet acted on:**
  - **Stale stage line:** the Tertiary bill still says "public views open until 2 October 2026" (checked 2026-09-28, past the 7-day staleness rule). Both live bills' stages need re-checking from Parliament.
  - **Model ID:** `generate.py` uses `claude-opus-5` while Opus 5.5 is current. Check it before slice 2b's first live call.
  - **Loading `.env`:** the pipeline doesn't read `.env`. Use `uv run --env-file .env …` (verified: the variable arrives with the flag and is absent without it).
- **Project `CLAUDE.md` created** (commands, gotchas, line maps). No git remote is configured, so nothing was pushed.

### Model switch and state re-check, 2026-10-13

- **Model: `claude-opus-5` → `claude-opus-5-5` (learner decision, 2026-10-13).** Opus 5 is still served, so nothing was broken. Opus 5.5 is the current Opus and costs less: $4/$20 per million tokens against $5/$25.
  - **Compatibility, from the code:** Opus 5.5 rejects disabled thinking, `budget_tokens` and a forced `tool_choice`. `generate.py` uses none of them: it sends adaptive thinking and structured output through `output_config.format`.
  - **Effort is now explicit:** `EFFORT = "high"` in both param builders. Opus 5.5 defaults to `medium`, one level below Opus 5's `high`, so leaving it unset would quietly have lowered depth. `high` vs `medium` is **untested on real clauses** and is worth comparing at slice 2b, especially for Sheng.
  - **Retry fix:** the refusal retry used to drop all of `output_config` before `client.beta.messages.parse`, so it would have retried at the default effort. The new `retry_params` drops only `format`. Evidence: in the installed SDK (anthropic 1.8.0), `_validate_output_config_conflict` raises only when `output_config.format` is set, and `_merge_output_configs` merges the rest with the schema built from `output_format`.
  - **Self-test:** 19 passed, 0 failed. **Positive control:** planting the old retry behaviour gives `FAIL: retry_params drops the schema but keeps the effort and the fallback`.
  - **The control also caught a weak check of mine:** planting `MODEL = "claude-opus-5"` still passed, because the check compares each request against `MODEL` itself. It is relabelled to say what it proves (the builders wire in `MODEL` and `EFFORT`). It is not a version pin, and the ID lives only in the constant.
  - **Copies updated:** `spec.md` (6 places), `spec.html` (2), and slice 2b's verify line above. The "Model ID" bullet in the 2026-10-07 entry is left as written, because that is what was true then.
  - **Not changed, your call:** `spec.md`'s Model row still calls Opus "the most capable standard model", and Claude Fable 5.1 is now above it. The spec's cheaper option for live calls names `claude-haiku-4-5` at "about 5×" cheaper. Haiku 5.5 (`claude-haiku-5-5`) is now current at $0.10/$0.50, about 40× below Opus 5.5 on input, with the Sheng quality question unchanged.
- **State re-check (commands run 2026-10-13):**
  - `main` fast-forwards cleanly: `git log build/poc..main` is empty. Still **no git remote**, so nothing is backed up off this machine.
  - The dev server was not running: `curl localhost:4321` got no connection.
  - **Kenya Law still returns 403** on both hosts, six days after it was first seen. `parliament.go.ke` returns 301.
  - **Bill stages are now 15 days old** (`stage_checked_at: 2026-09-28` on both). The Tertiary bill still shows a closed deadline as open.
  - Three leftover agent worktrees under `.claude/worktrees/`. All have zero uncommitted changes, and their commits are on `build/poc`: two as ancestors, and `f4cce6b` marked already applied by `git cherry`. Safe to remove.
  - The banned list still holds 7 doctrine and 12 proposed phrases, from reading `banned.txt`.

### Model: Sonnet 5.5, and housekeeping, 2026-10-13 (later)

- **Model: `claude-opus-5-5` → `claude-sonnet-5-5` (learner decision).** This supersedes the Opus 5.5 entry above. Chosen for keeping closely to the brief: explain the clause, never editorialise. It's also the cheapest of Fable 5.1, Opus 5.5 and Sonnet 5.5 ($2/$10 per million tokens). "Better at following scope" is the learner's judgement, **not yet measured here**. Slice 2b's first real drafts, and lint's neutral flags on them, are the first evidence.
  - **Compatibility, from the code:** Sonnet 5.5 rejects `thinking: {"type": "disabled"}` and a forced `tool_choice`; `generate.py` uses neither. The refusal fallback is the `fallbacks: "default"` form with beta `server-side-fallback-2026-07-01`, which is the only form Sonnet 5.5 accepts, and only on the Claude API. Batches still never carry it.
  - **Effort:** Sonnet 5.5 already defaults to `high`. `EFFORT = "high"` stays explicit, so a later model swap can't quietly change it. Sonnet 5.5's effort levels are recalibrated from earlier Sonnets, so `high` vs `medium` is still worth a look at slice 2b.
  - **Refusal categories:** Sonnet 5.5 declines in five categories (`cyber`, `bio`, `frontier_llm`, `reasoning_extraction`, `general_harms`). Contentious political text is unlikely to trip them, but the direct retry with the fallback exists for exactly that case. **Unknown until 2b runs.**
  - **Spec:** the Model row now records the decision instead of "the most capable standard model". The cheaper option names `claude-haiku-5-5`, about 20× below Sonnet 5.5, replacing `claude-haiku-4-5` "about 5×". `spec.md` (6 places), `spec.html` (2) and slice 2b's verify line carry the new ID.
- **Jev: dropped (learner decision).** It isn't part of PoliTricks. The register in `haven-ea/references/jev-use-cases.md` is untouched.
- **Stale worktrees removed.** All three folders were deleted with the `\\?\` prefix (two held a `.venv`), then `git worktree prune`. Branches `worktree-agent-a14d…` and `…a697…` were deleted with `-d`. `…a799…` (`f4cce6b`) needed `-D` because it isn't an ancestor; `git cherry` had marked its patch as already applied first. `.vercel/` holds build output only, so there is still no linked Vercel project.

### API key wired, no credit, 2026-10-13

- **Key:** `pipeline-PoliTricks`, scoped to Default workspace (not Organization), expires 2026-11-12. It's in `.env`, which `git check-ignore -v .env` confirms is ignored (`.gitignore:5`). The key line is well-formed: `sk-ant-` prefix, no quotes, no trailing space.
- **Tiny call (step 0):** 400 `invalid_request_error`, "Your credit balance is too low to access the Anthropic API" (`req_011CfzfBR7NyddWWPxz6zheW`).
  - The key itself is valid: a bad key returns 401, and this got past authentication.
  - The model ID is **still unproven**, because the billing check may run before model validation.
- **`messages.count_tokens` is gated the same way at zero balance** (`req_011CfzfDGxo6hZU3SQJE6cpy`), although Anthropic doesn't bill it. So no live check, not even a free one, can run before a top-up.
- **Billing facts (Anthropic support pages, 2026-10-13):** API usage is never charged to a Claude plan. Pro/Max usage credits extend Claude and Claude Code only. Max and Team plans include monthly API credits once a Console organization is linked to the plan.
- **Next:** the learner tops up credit and sets a spend limit. Then rerun step 0, the 1-item batch, and `generate finance-act-2026`. The pre-run estimate for the Finance Act at Sonnet 5.5 batch rates is under about $1.50 (56 requests; the output side is assumed, not measured). Replace it with the batch's real usage after the run.
- **Credit added ($5, Pro plan, so no monthly API credit). Step 0 passes:** `model: claude-sonnet-5-5`, `stop_reason: end_turn`, text `'PoliTricks key works'`, usage 24 in / 10 out. **The model ID is now proven.**
- **Real input size from `count_tokens`** over the exact 56 requests `build_batch` makes: **119,343 input tokens**. Per request: c1 1,876, c2 2,444, summary 3,098. The pre-run guess of ~60k was about 2× low, probably because the output schema and the system prompt count toward every request.
  - Re-estimate at batch rates: input $0.12, plus output $0.43–$1.13 if output runs 85k–225k tokens. Total **$0.54–$1.24**. The output range is still assumed.
  - Scripts used: `tiny_call.py` and `count_tokens.py` in the session scratchpad (not committed). The second sends only the counted fields of each batch request.

### Probe batch, adversarial review, blocker fixes, 2026-10-13

- **1-request probe batch (clause c1), through the real `build_batch` and `parse_batch_line`, nothing written to the bill.** Batch `msgbatch_01CHdsBPHS2b5X2xWB3kuuLR` ended in 126 s: 1 succeeded, `stop_reason: end_turn`, content `[thinking, text]`, outcome `Applied`.
  - **Structured output works inside a batch** with adaptive thinking and effort `high`, for the clause schema. The summary schema is still untested live.
  - Usage: 134 input, 1,742 written to the cache (5-minute TTL), 898 output (439 of it thinking).
  - Projection from that: about $0.40 for the whole Finance Act. c1 is a short-title clause, so treat that as a floor.
  - Script: `probe_batch.py` in the session scratchpad.
- **Cold Sonnet adversarial review of the live path** (read-only, no API calls): 4 blockers, 6 should-fix, 4 nits. The lead checked the key claims before acting on them. Grades as set by the lead:
  - **B1 (fixed):** the refusal retry read `.parsed`. SDK 1.8.0 has only `parsed_output` (`types/beta/parsed_beta_message.py:42,90`), so any refusal crashed the run before saving.
  - **B2 (fixed):** a retry that raised, or returned `None`, crashed `apply()`. Confirmed by planting it: `'NoneType' object has no attribute 'explanation'`.
  - **B3 (fixed):** the batch id and raw results were never saved, and there was no resume.
  - **B4 (fixed):** errored, expired and invalid lines were dropped silently, with exit 0. Errored reasons now keep the API's type and message, and unexpected stop reasons are named (N3 folded in).
  - **S2, promoted to blocker, open (needs the learner's approval):** `explainer.md` never defines the four groups or the summary task. A grep of its 58 lines found no group or summary terms. The summary request sends headings only.
  - **S5:** half closed by the probe; the summary schema is still to test.
  - **S3 and S6, should-fix, open:** unknown group `clause_ids` aren't rejected; the retry's answering model is now printed but not stored.
  - **S1, downgraded:** a review during a batch would be overwritten (likely only for a run longer than the 2 minutes seen).
  - **S4, deferred** to the reviewed-hash decision.
  - **N2 refuted:** the reviewer guessed the prompt was too short to cache, but the probe shows 1,742 tokens written to it.
- **The fixes:**
  - `run()` now writes `pipeline/.cache/batches/<bill>.batch-id` as soon as the batch is submitted, and `<batch-id>.jsonl` with the raw results before parsing. That folder is already gitignored.
  - `generate <bill> --batch-id X` resumes without submitting anything.
  - `_retry_direct` never raises, and prints which model answered.
  - `run()` returns 1 and lists every request not applied. The CLI exits with that code.
  - `intake` now raises when generate leaves anything unapplied, so the bill counts as failed and the message says how to finish it. **Not covered by a self-test:** intake's self-test always runs without a key, so it never reaches generate.
- **Self-test:** generate now drives the real `run()` offline, using `_FakeClient` and a temp copy of the repo. 32 passed, 0 failed, up from 19.
  - **Positive controls:** planting the old `.parsed` retry gives 9 FAILs; planting a retry with the right attribute but no guard gives 6 FAILs.
  - All other self-tests, lint, the build and `check-build` for all 3 bills pass.

### Groups prompt and probe 2, 2026-10-13

- **Group definitions (learner decision):** no fifth group. Unbanked people (cash and mobile money), the informal sector and digital or gig workers fold into `trader`; delivery and ride-hailing drivers fold into `transport`. The prompt (`explainer.md`) now defines all four groups and the summary task. The summary request sends every section's full text. `apply()` drops repeated groups and unknown clause ids, with a note for each (S3). Self-test: 37 passed.
- **Probe 2:** `msgbatch_01Jhx5PXCrDJvGiuoaH7k9Dy`, three requests (summary, c3, c32). 3/3 succeeded, all `Applied`, in 408 s.
  - Lint over the applied in-memory bill: 0 findings. `apply` notes: none, so no invented ids.
  - Cost about $0.084. Output tokens: c3 1,996, c32 2,636, summary 6,572.
  - Re-projected full Finance Act: about **$0.71**, from the mean clause output (1,843 tokens over three samples).
  - **S5 closed:** the summary schema works live.
  - Raw results: `msgbatch_01Jhx5PXCrDJvGiuoaH7k9Dy.jsonl` in the session scratchpad.
- **Clause-level tags looked right in 2/2:** c3 (taxes gratuity contributions) → `employee`; c32 (VAT exemptions, including money transfer) → `trader`, as the new rule intends.
- **The summary's groups over-tag, and give the site two sources of truth for groups:**
  - `trader` named 11 sections. They include general-taxpayer procedure: return deadlines (c18), pre-populated returns (c47), e-system penalties (c48), PIN reinstatement (c40), invoices (c30), import documents (c43) and the amnesty dates (c45).
  - Its group text also bundles betting winnings under traders.
  - c18 and c47 are tagged both `employee` and `trader`, although they apply to every individual.
  - The summary's groups come from a separate request from the clause tags, so the two can disagree about which sections a group covers.
  - Proposed fix: run the summary after the clause batch, feed it the clause tags, and take each group's `clause_ids` from those tags in code. **Waiting on the learner.**
- **Length:** c32's explanation runs about ten sentences against the prompt's "two to four". The clause is 5,300 characters listing about 20 exemptions. Whether long schedule amendments may run longer is a learner call.

### Summary after clauses, long amendments, preview, 2026-10-13

- **Learner decisions:**
  - The summary is written after the clauses, from their tags.
  - Long schedule amendments may run past four sentences, shown in full. No new field and no collapse. This was asked and answered: the learner's phrase "and in background" did not mean a collapsed "more" detail.
- **Groups now have one source of truth: the per-clause tags.**
  - `GroupOut` has no `clause_ids`. `apply()` fills each group's sections from `tagged_sections(bill)`, and applies the summary last within any one call.
  - A group the summary writes about but no clause is tagged with is left out, with a note. A repeated group keeps its first entry.
- **`run()` is two phases:**
  - Phase 1 is the clause batch: 55 requests, the summary no longer among them. The clause results are saved before phase 2 starts.
  - Phase 2 is the summary as one **direct** call (`_call_direct`, with the fallback on), whose request lists each group's tagged sections.
  - **Deviation from spec.md ("Uses the Message Batches API"):** the summary is a direct call. A second one-request batch would add another wait (408 s in probe 2) for about a cent of saving.
  - `generate <bill> --summary-only` redoes just the summary from the saved tags.
- **Prompt:**
  - The summary section now says the tags are fixed and each group's note may mention only its tagged sections.
  - New bullet: a long section that changes many separate things covers every change, even past four sentences, and short sections aren't padded.
- **Self-test: 43 passed, 0 failed** (up from 37).
  - **Positive controls:** planting "groups ignore the tags" gives 6 FAILs; planting "no phase 2" gives 4.
  - The first control exposed a test defect: a missing group raised `KeyError` and aborted the self-test instead of failing a check. Fixed, so both plants now run to `checks:`.
- **Preview (not committed):** probe drafts c1, c3, c32 and the summary are applied to the **working copy** of `finance-act-2026.json` as `draft`, for the learner to see the Sheng on the dev server.
  - The full run replaces them. Lint gives 0 findings over 27 texts.
  - `check-build` passes with them in place.
  - Probe 1's results were re-fetched by batch id at no charge. Its raw results now sit in the scratchpad beside probe 2's.

### Em dashes and Sheng feedback, 2026-10-13

- **The learner reported "tons of em dashes".** Counted first:
  - **0** in Claude's output (4 generated text blocks).
  - 10 in `explainer.md`, to be removed in the Sheng prompt rewrite.
  - The rest were in the site's own copy and data.
- **Fixed in visible copy:**
  - Home title and tagline.
  - The bill page title (`<title> | PoliTricks`) and the Sample banner.
  - The source label line and the fingerprint how-to.
  - Badge labels, now "AI draft (not yet reviewed)" and "Sample text (placeholder)". `check-build` reads these from `badge.ts`, so it followed.
  - Stage lines in `sources.yaml` and the bill JSONs ("Law: assented…", "Committee. Public views…").
  - The Public Participation source label ("1 Oct 2025; not the…").
  - **Not committed yet:** the Finance Act JSON's stage line, which sits in the same file as the uncommitted preview drafts and goes in with the full run.
- **Kept on purpose:** em dashes in clause text are the law's own, and that text is verbatim and fingerprinted. Visible-text counts after stripping scripts, styles and comments:
  - Finance Act 0, and home 0.
  - Public Participation 8, against 8 clauses whose text contains one.
  - Tertiary 26, against 26 such clauses.
  - Code and HTML comments keep theirs; readers never see them.
- **Sheng feedback (being played back before acting):**
  - The summary's first sentence is the target mix. The paragraph after it is too Swahili.
  - Once an English word is used ("rent"), keep it.
  - "wakazi" is too broad.
  - The learner asked for a review for purely Swahili words in context: "Sheng is Swahili-English".
- **Played back and decided (learner, 2026-10-13):**
  - **"wakazi" → tenant / "kama unalipa rent": the lead pushed back, and the learner accepted.** Section 3 taxes non-residents who *receive* rent, who are landlords. "Tenant" would tell renters a tax applies to them. The Sheng term is "non-residents", which also follows the English-led rule.
  - The lead's English-led rewrite of the problem paragraph is accepted as the prompt's reference.
  - Purely-Kiswahili check: **Claude flags, the learner decides.** No word list and no second pass.
  - The review's three fixes: yes.
- **Prompt (`explainer.md`), rewritten once:**
  - 0 em dashes, down from 10. A new rule: never use an em dash in any language.
  - Sheng is English-led: English carries the meaning and Kiswahili/Sheng is the glue. It has a "like this" (the learner's target sentence plus the accepted rewrite) and a "not like this" (the original paragraph).
  - Keep an English word once used ("rent" stays "rent").
  - Legal meaning beats slang.
  - **Tagging:** a section that applies to all taxpayers or everyone equally (deadlines, penalties, PINs, procedure, invoices, amnesties) gets no group (cold review S4).
- **Sheng swaps:**
  - New `ShengSwap {word, english}` and `sheng_swaps` lists on `Clause`, `Summary` and `GroupEntry` (Python defaults `[]`; Zod `.default([])`, so older files validate).
  - The model returns them (`ClauseOut.sheng_swaps`, `SummaryOut.summary_sheng_swaps`, `GroupOut.sheng_swaps`), and `apply()` stores them.
  - `lint` adds a `sheng` finding per item with swaps until it's reviewed. `review` already shows any finding and lets acceptance clear it, so it needed no change. The site never shows swaps.
  - All three bill JSONs were re-saved through `save_bill`, so they carry `"sheng_swaps": []`. The diff is that key alone (tertiary: 68 additions). This was needed because intake's byte-equality check failed on the old files (2 FAILs), exactly as it should.
  - The fixture's clause answers gained `"sheng_swaps": []`. c7 is still invalid for its original reason (no `sheng`).
- **Cold review of `ed3f722..8a8f750`: no blockers.** Six should-fixes and six nits, checked against the code. Fixed now:
  - **S1:** the summary isn't asked for, or paid for, when any clause failed. The run prints the `--summary-only` hint.
  - **S3:** a missing result, or one for a request this bill didn't make (a wrong `--batch-id`), is an `Errored` and fails the run.
  - **N3:** the `--summary-only` hint is printed when only the summary failed.
  - **S4:** fixed in the prompt (above).
  - **Deferred:**
    - **S2:** cheap recovery for errored or expired clauses. Today it means a fresh run, and drafts aren't skipped.
    - **S5:** a reviewed group's `clause_ids` don't refresh when tags change.
    - **S6:** the direct summary call is the first live use of `parse` with `fallbacks`. A failure is caught and `--summary-only` recovers.
  - The SDK timeout guard was checked by the reviewer: 450 s, under the 600 s limit, so it won't raise.
- **Self-tests:** generate 47 (missing and stray results, the summary skipped when clauses failed, swaps landing on clause, summary and group); lint 34 (Sheng flag until reviewed); intake 22; review 33. Lint over all 3 bills: 0 findings. The build and `check-build` pass for all 3.

### Re-probe with the English-led prompt, 2026-10-13

- **Batch `msgbatch_014kXsH8fEAPsXuKKYPX2HAQ`** (c3 and c32): 2/2 `end_turn`, 392 s. Then phase 2 (`_summarise`, the real direct call): the summary was answered by `claude-sonnet-5-5`, so no fallback model was used.
  - **The first live direct `parse` with `fallbacks` worked**, which closes cold-review S6.
  - Applied to the working copy for the preview. 0 em dashes in any generated text.
- **Sheng is now clearly English-led**, for example c3: "Clause hii ina-amend Section 5 ya Income Tax Act kwa njia mbili."
  - It may overshoot into the Act's own phrasing: "haita-be deemed ku-accrue in Kenya au ku-be derived from Kenya", "ina-add proviso", "emoluments". That is legal English with Sheng prefixes, not plain words. **Learner call.**
- **`sheng_swaps` came back empty on all 5 items** (c3, c32, summary, 2 groups).
  - Kiswahili content words do remain, e.g. "nje ya" (outside), "inajumuisha" / "hazijumuishwi" (includes), "kufikia" (by), "haichukuliwi" (isn't counted).
  - So the self-flag may under-report. Unproven either way, until the learner reads them.
- **Length grew.** Output tokens: c3 1,996 → 2,818 (1.4×), c32 2,636 → 6,301 (2.4×).
  - Explanation word counts against the legal text: c3 en 133 / sw 130 / sheng 109, for 171 words of law; c32 452 / 465 / 378, for 800 words.
  - c3 is not a schedule but is now about 75% of the law's length, so the long-amendment rule may be stretching ordinary clauses.
  - The full-run projection, scaled by the 1.97× output growth, rises to about **$1.27** (was $0.71). It's an estimate: the summary's direct-call usage isn't logged yet.
- **Groups:** c3 → employee and c32 → trader. The summary's groups now cover exactly the tagged sections (employee: c3, trader: c32), so the over-tagging seen in probe 2 is gone by design. They're thin because only three clauses carry tags in this preview.
- **Learner decisions on the re-probe:**
  - **Register:** English-led, but with plain words. The prompt now says to use everyday English, not the Act's English ("counted as income", not "deemed to accrue"; "pay", not "emoluments"; "a condition", not "a proviso"), with c3's legalistic line as "not like this" and a plain rewrite as "like this".
  - **Length:** only a section listing many separate items (a schedule) covers every item. Any other section stays at two to four sentences, naming each change without restating its conditions word for word.
  - **Swaps:** "nje ya", "inajumuisha", "kufikia" and "haichukuliwi" are normal Sheng, so an empty list was right. They're added to the prompt's examples of words not to flag. **This answers the under-reporting question: no defect.**
- **c3 re-probe** (`msgbatch_01YLJcQQxbKyBySgXVh7QEhA`, 109 s, `end_turn`, 2,631 output tokens, 1,729 of them thinking):
  - **Register fixed:** "haitakuwa counted kama income from Kenya", "pay ya employee", "(end-of-contract payment)", "non-stop". None of deemed / accrue / proviso / emolument / derived appear in en or Sheng.
  - **Length down:** en 133 → 110 words, sw 130 → 104, Sheng 109 → 88, for 171 words of law. 3 sentences each (counted by reading).
  - The output token count barely moved (2,818 → 2,631) because thinking dominates, so the full-run estimate stays about $1.2.
  - Swaps `[]`, 0 em dashes, tag `employee`. Applied to the working copy for the preview.

### Slice 2b full run, Finance Act, 2026-10-14

- **`generate finance-act-2026`** (learner approved about $1.2): batch `msgbatch_01XPbYqgfcUzCL3WdKUyJRLG`, 55 requests, **55 succeeded**, 0 errored / expired / canceled.
  - It ended after 36 polls (about 6 min).
  - Phase 2's summary was answered directly by `claude-sonnet-5-5` (no fallback).
  - `applied 56 results (status: draft)`, exit 0. The id and raw results are in `pipeline/.cache/batches/`.
- **Real cost (batch usage):** input 20,435, cache write 4,291, cache read 231,714, output 113,821 (66,078 of it thinking), which is **$0.618** at batch rates.
  - The summary's direct call isn't logged. About $0.10, estimated from its probe.
  - Caching worked: 231k tokens read from the cache against 4k written.
- **Slice 2b verify (mechanical), all met:**
  - 55/55 clauses have an explanation, every one `draft`, 0 `sample`. The summary is `draft`.
  - Zod validates (`npm run build` exit 0), and `check-build` shows "AI draft (not yet reviewed)" on every card.
  - Lint: 174 texts, 0 findings. All self-tests pass.
  - **The learner check is still open** (read three clauses in Sheng), so slice 2b stays unticked.
- **Measured over the output:**
  - **Em dashes:** 0 over 174 generated texts.
  - **Neutral flags:** 0.
  - **Sheng swaps:** 0.
  - **Legal terms in Sheng:** 2, both "proviso" (c21, c36), each glossed as "condition (proviso)", which the prompt allows.
  - **Length:**
    - 5 clauses run past 4 English sentences. c32 and c36 (14 each) are schedules, as intended. c39 (5), c43 (6) and c4 (5) are not.
    - Total English is 5,318 words against 8,478 words of law.
  - **Tags:** employee c3, c9; trader c7, c16, c21, c32. **49 of 55 untagged**, transport 0, student 0. The groups match the tags exactly.
- **Open for the learner:**
  - **Tagging may now be too conservative:** 6 of 55, with no transport or student group.
    - c55 halves the Road Maintenance Levy (3 → 1.50 shillings) and is untagged.
    - Its explanation is faithful but never says what the levy is charged on, because the clause text doesn't say. The prompt forbids outside knowledge, so a reader learns only "an amount changed".
  - **Committing:** CLAUDE.md's order is generate → lint → review → commit. Slice 2b has its own commit step (`Generate Claude explanations for Finance Act 2026`). The drafts are **not committed yet**. They stay recoverable from the batch id at no cost.
- **Learner decisions:**
  - **Commit the drafts now** as slice 2b. Done in `89c985a`, with everything still `draft`.
  - **Loosen tagging slightly and re-run only the tags.**
  - **No outside context** for c55-style explanations. Context gets added by hand in review.
- **Tags-only pass, built (`generate <bill> --tags-only`):**
  - `TagsOut` allows only `groups`. `build_tag_params` reuses the explainer system prompt, so the cache carries over.
  - `apply_tags` changes only `groups`, never a reviewed clause's, and prints each change.
  - The batch id is saved as `<bill>.tags.batch-id`. Phase 2 then rewrites the summary from the new tags.
  - **Prompt:** two exceptions that count even though others pay too. A levy, duty, fee or tax on fuel, vehicles, motorcycles, spare parts, roads, parking, driving licences or fares counts for `transport`. School, college and university fees, student loans and bursaries, and youth employment count for `student`.
  - **Self-test: 56 passed** (up from 47).
  - **Positive controls:** an `apply_tags` that overrides a reviewed clause gives 3 FAILs; one that wipes explanations gives 2.
- **Tags-only run (learner approved, effort high):** batch `msgbatch_0169Ezwvu5AFHwcTyWmdFmjT`, 55/55 succeeded, 32 polls. The summary was then rewritten directly by `claude-sonnet-5-5`. Exit 0.
  - **Cost $0.081** (output 6,707 tokens, 6,130 of it thinking; 202k read from the cache). **The lead's pre-run estimate of $0.20–0.65 was 2.5–8× too high:** it assumed the full run's thinking per request, but tagging needed about 110 thinking tokens a request.
  - **Changes:** c33, c36 and c55, each `[]` → `transport`. Nothing else moved. Still no `student` tag.
  - Tags are now employee 2, trader 4, transport 3; 46 untagged. The groups match exactly (transport: c33, c36, c55).
  - **Scope check against HEAD:** 0 explanations changed and no field other than `groups` changed on any clause. Summary and group texts were rewritten, with 0 em dashes.
  - Gate: lint (177 texts) 0 findings, all self-tests, the build and `check-build` pass.
- **For the learner's review: two of the three new transport tags are literal applications of the rule.**
  - c33 only adds tariff codes for bicycles and batteries to the VAT schedule.
  - c36 is the excise schedule. Its vehicle item is a 50% rate on antique, vintage and classic cars, not boda, matatu or delivery vehicles.
  - c55 (the fuel levy cut) is the intended case.
  - Drop or keep each in `review`.

### Slice 2b ticked, and checkpoint before compaction, 2026-10-14

- **Learner check passed:** "Sheng reads well" (learner, 2026-10-14). **Slice 2b is ticked.**
- **State at this checkpoint:**
  - `build/poc` = `main`, both fast-forwarded. Still no git remote, so nothing is pushed or backed up off this machine.
  - The Finance Act carries 55 clause drafts, a summary draft and 3 group drafts, all `draft`. Nothing is `reviewed`.
  - The batch records are in `pipeline/.cache/batches/` (gitignored), for the explanations (`finance-act-2026.batch-id`) and the tags pass (`finance-act-2026.tags.batch-id`).
  - The dev server was started with `preview_start politricks-dev`.
- **Credit:** $5 topped up (Pro plan; API credit is separate from the plan). About $1.15 spent, an estimate:
  - Measured: full batch $0.618, tags batch $0.081, probe 2 about $0.084.
  - Not logged: the direct summary calls, about $0.10 each.
- **Next, in order:**
  1. **Slice 3 review gate.** The learner walks the 56 drafts plus 3 group notes in `politricks review finance-act-2026`, prunes the 12 proposed banned phrases, decides the c33/c36 transport tags, and adds hand context where the law text gives none (e.g. what the c55 levy is charged on).
  2. **Decide before the first accept:** the reviewed-badge hash proposal (tie "Reviewed" to the text the human saw). Cold-review S4 (reruns overwrite edited drafts; reviewed clauses are re-paid) and S5 (reviewed groups keep stale `clause_ids`) hang on it.
  3. Still open from the start of this session:
     - re-check both live bills' stages from Parliament (Tertiary still shows an expired deadline as open; Kenya Law has returned 403 since 2026-10-07);
     - slice 6 civic facts;
     - a git remote (the learner's call; slice 7 Vercel needs one);
     - lint doesn't gate `npm run build` until slice 4.
  4. The Tertiary and Public Participation bills have no explanations yet. Cost them from the Finance Act's measured $0.62 for 55 clauses, scaled by clause count and length.
- **Deferred cold-review items:**
  - S2: cheap recovery for errored or expired clauses (today that means a fresh run).
  - S5: refresh reviewed groups' `clause_ids`.
  - The direct calls' usage isn't logged. Add that before costing the next bill.

### "Reviewed" tied to a fingerprint, 2026-10-14

- **Decided (the learner, before the first accept):** "Reviewed" means the content is exactly what a human accepted. This closes the proposal from the slice 2a cold review, and cold-review S4 and S5.
- **How it works:**
  - `review.sha256` holds a fingerprint of the content when the status was set: by `generate` for a draft, by `politricks review` on accept.
  - The fingerprint covers the three texts, a clause's legal text and sorted tags, and a group's section list. It leaves out review aids, the heading, and `suggested_articles` (slice 10 must add those under v2).
  - Python: `models.current_sha256`. TS twin: `src/lib/reviewHash.ts`, which uses Web Crypto because the project has no Node types.
- **States:**
  - **Reviewed and matching:** the badge says *Reviewed*.
  - **Reviewed, no longer matching:** the badge says *Changed since review*, lint raises a `changed` finding, and review walks it.
  - **Edited draft** (`sha256: "hand-edited"`, or no longer matching): it's protected, so `generate` never overwrites it or pays for it again. Typing the old words back doesn't undo that.
  - **Draft with no fingerprint:** `generate` may replace it. Setting `sha256` to null is how to hand a draft back.
- **`generate`:**
  - Both batch builders leave out protected clauses. If every clause is protected, no batch is sent.
  - On `--batch-id` resume, a result for a clause reviewed while the batch ran is left alone. It doesn't count as a failure.
  - Drafts are stamped after their fields are set.
  - New `refresh_group_sections`: group `clause_ids` follow the tags. It decides human or AI before changing anything, so a reviewed group whose sections moved reads *Changed since review*.
- **`review`:**
  - Shows each clause's GROUPS and each group's SECTIONS, because accepting covers them too.
  - Walks clauses, then the summary, then groups.
- **Backfill:** the Finance Act's 55 clause drafts, its summary and its 3 groups were stamped as drafts (from `91f2ee6`, untouched since). Nothing was set to reviewed: a grep for `"status": "reviewed"` over the 3 bill files found 0 lines, against 59 `"status": "draft"` lines. All 3 bills were re-saved with `sha256`.
- **Parity:**
  - Shared vectors: `pipeline/tests/fixtures/review_hash_vectors.json`, written by `python -m politricks.hashvectors`. They cover a newline, colons, quotes, a backslash, an em dash, an emoji, NFD, U+2028, unsorted tags and empty strings.
  - `check-build` checks the TS side and `review --self-test` the Python side.
  - `check-build` also checks independently that no card or summary shows *Reviewed* unless its stored fingerprint equals the recomputed one.
- **Reviews:** two Sonnet cold reviews.
  - **Plan review:** 2 blockers, both fixed before the build. Editing back to the old words un-protected a reviewed text (fixed by the `hand-edited` sentinel). The stamp/refresh ordering was wrong.
  - **Plan review should-fixes, all taken:** show tags in review; hash the raw legal text (`text_sha256` was never rechecked at build time); guard the resume; guard the empty batch; switch every status check to the helpers; sort the tags.
  - **Diff review:** no blockers. Fixed: the Python side never read the vectors (the docs claimed it did); the independent invariant now covers the summary; a stale line reference in CLAUDE.md.
  - **Documented, not fixed:** an AI draft whose legal text changes reads as hand-edited. Null its `sha256` to redraft it.
- **Tests:**
  - Self-tests: lint 36, review 40, generate 75, intake 22, all passing. Lint over the 3 bills: 0 findings. Build: 0 errors. `check-build` passes for all 3 bills.
  - **Planted-bug controls, all caught:**
    - generate ignores protection: 11 FAILs;
    - the fingerprint ignores tags: 4;
    - lint treats any "reviewed" as reviewed: 1;
    - review accepts without a fingerprint: 6;
    - an edit leaves the fingerprint alone: 2;
    - a tampered vector: 1.
  - **Site controls** (scratch copy with a control bill, never the real data):
    - c3 rendered *Reviewed*, c4 *Changed since review* and c5 *AI draft*, confirmed both by `check-build` and by reading the raw HTML;
    - planting a `displayStatus` that ignores the fingerprint failed the independent invariant on c4;
    - removing the TS tag sort failed the vector check.
- **Next:** slice 3. The learner runs `politricks review finance-act-2026`. The c33/c36 tags now appear on screen during the walk; changing them still means editing the JSON before accepting.
