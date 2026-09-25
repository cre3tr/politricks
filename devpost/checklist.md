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

- [ ] **2b. Real Claude explanations for every clause**
  Becomes usable: Every Finance Bill clause and the summary carry Claude-generated EN/SW/Sheng explanations (badged *AI draft*), replacing the samples.
  Why now: This runs as soon as the API key exists, and before slice 3's review gate, which needs real drafts to review. It proves the second risk: Claude via Batches with structured outputs.
  PRD ref: `prd.md > What It Does`
  Spec ref: `spec.md > Generate (Claude)`, `spec.md > Anthropic Messages API`
  Build: The learner creates the API key and puts it in `.env`. Run a tiny live call, a 1-item test batch, then `generate finance-act-2026`, retrying refusals directly.
  Verify (mechanical): A tiny `claude-opus-5` call returns non-empty text. The 1-item batch returns output that parses with the Pydantic model; if the schema is rejected, record a revision and fall back to direct `messages.parse`. After `generate`, every clause has an `explanation`, no item is `"sample"`, all are `"draft"`, the Zod schema validates, and `check:build` shows "AI draft" on every card.
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
