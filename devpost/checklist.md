---
doc: checklist
status: approved
---

# Build Checklist

Build mode: fast — with human-readable comments in the code (learner request)

## Slices

- [ ] **1. The official Finance Bill 2026 becomes fingerprinted, word-for-word clauses**
  Becomes usable: `politricks fetch` and `politricks extract` turn the official Finance Bill 2026 PDF (and the Finance Act 2026) into a bill JSON of clauses. Each clause is verbatim-checked against the PDF and carries its own fingerprint. `politricks verify --self-test` proves the fingerprint catches a one-byte change.
  Why now: This is the single biggest unverified risk in the whole plan: do official, text-extractable PDFs exist, and can clauses be pulled out word for word? It's the one allowed technical-first slice, because it independently proves that risk and leaves runnable evidence. It bootstraps the Python pipeline, and every later slice stands on it.
  PRD ref: `prd.md > Product Decisions` (Finance Bill hand-loaded from a hashed copy), `prd.md > Open Questions`
  Spec ref: `spec.md > Fetch & Fingerprint`, `spec.md > Extract Clauses`, `spec.md > Verify`, `spec.md > Data Model`, `spec.md > Parliament of Kenya (parliament.go.ke)`, `spec.md > Kenya Law (kenyalaw.org)`
  Build: `git init`. Scaffold `pipeline/` with uv (pdfplumber, pydantic, httpx, typer or argparse). Find and pin the official Finance Bill 2026 and Finance Act 2026 URLs on the allowlisted domains in `sources.yaml`. Write `hashing.py` (`normalise()`, SHA-256), `fetch.py` (allowlist, stores to `sources/`), `extract.py` (clause split, verbatim check, `overrides/`), `verify.py` (with `--self-test`), `models.py` (Bill/Clause per the Data Model) and `cli.py`.
  Verify (mechanical): `politricks fetch finance-bill-2026` prints a SHA-256, and `certutil -hashfile` on the saved file prints the same value. `extract` exits 0 with a clause count above 0, and a printed sample clause matches the PDF text. `verify --self-test` prints `unchanged` for the intact copy and `CHANGED` for the one-byte-altered copy. A deliberately corrupted clause in a scratch copy makes `extract`'s verbatim check fail.
  Learner check: Open `src/data/bills/finance-bill-2026.json`, pick any clause, find it in the official PDF, and confirm it's word for word. Then run `certutil -hashfile` on the PDF yourself and compare it with the fingerprint in the JSON.
  Commit: `Fetch, fingerprint and extract Finance Bill 2026 clauses`

- [ ] **2. A Finance Bill clause in Sheng, right next to the legal text**
  Becomes usable: `npm run dev` → `/bills/finance-bill-2026` shows the bill's summary and clause cards. Each card has the legal text beside its Claude explanation, with a global EN / SW / Sheng toggle, a per-card flip, *AI draft* badges and the source fingerprint footer, all in the PoliTricks look.
  Why now: This is the unique kernel and the demo's "oh, that's cool" moment. It comes early so everything else is built around a proven kernel. It also proves the second risk: Claude via Batches with structured outputs.
  PRD ref: `prd.md > What It Does`, `prd.md > Language Switching`, `prd.md > Look and Feel`
  Spec ref: `spec.md > Generate (Claude)`, `spec.md > Bill Page (/bills/[id])`, `spec.md > Clause Card`, `spec.md > Language Toggle`, `spec.md > Head Script (src/components/HeadScript.astro)`, `spec.md > Look and Feel`, `spec.md > Anthropic Messages API`, `spec.md > File Structure`
  Build: The learner creates the API key and puts it in `.env`. Scaffold Astro 7 + `@astrojs/vercel` + Tailwind v4, and confirm the versions install together. Write `generate.py` (Batches, `messages.parse`, Pydantic output, refusal fallback, `prompts/explainer.md`; `suggested_articles` left empty until slice 9). Write `schema.ts` mirroring `models.py`, the bill page (What it does tab only), `ClauseCard`, `LanguageToggle`, `HeadScript` (language only for now), `SourceFingerprint`, `Header`, `global.css` tokens and highlighter, and the Anton font.
  Verify (mechanical): One tiny `claude-opus-5` call returns non-empty text. The test batch returns parsed structured output; if Batches reject it, record a revision and fall back to direct `messages.parse`. `generate` fills every clause's `explanation` and all `review.status` are `"draft"`. The Zod schema validates the JSON. `npm run build` succeeds. The built page HTML contains a clause's legal text and its Sheng explanation. The toggle changes `data-lang` in a browser check.
  Learner check: Open `http://localhost:4321/bills/finance-bill-2026` on your phone-sized window. Find a clause that hits your pocket, read it in Sheng beside the legal text, flip the whole page to Kiswahili, then flip just one card back to Sheng. Does it look and sound like PoliTricks?
  Commit: `Show Finance Bill clauses beside trilingual explanations`

- [ ] **3. Nothing ships without your review, and banned phrases can't get in**
  Becomes usable: `politricks lint` fails on any banned phrase or neutrality flag. `politricks review` walks every draft text, flagged items first, and marks each one reviewed. The page shows *Reviewed* badges for reviewed clauses and *AI draft* for the rest.
  Why now: Your doctrine (neutral, Sheng reviewed by you, never "unconstitutional") has to be mechanical before more content piles up. Every later slice's content goes through this gate.
  PRD ref: `prd.md > What It Does` (neutral), `prd.md > Non-Goals`, `scope.md > Quality Guardrails`
  Spec ref: `spec.md > Lint`, `spec.md > Review`, `spec.md > Data Model`
  Build: Write `lint.py`, `prompts/banned.txt`, `review.py` (terminal walk: accept / edit / skip, flagged first, refuses to finish while lint fails), and the badge rendering in `ClauseCard`.
  Verify (mechanical): Plant "kinyume cha katiba" in one explanation in a scratch copy: `lint` exits non-zero and names the clause. The clean file passes. After reviewing one clause, its JSON shows `"reviewed"` and the built page shows the *Reviewed* badge on that card only.
  Learner check: Run `politricks review finance-bill-2026` and review the Sheng for 3–5 clauses you'd put in the demo, editing anything that sounds off. Reload the page and see those cards say *Reviewed*.
  Commit: `Add lint and review gate for all generated text`

- [ ] **4. PoliTricks knows who you are, and puts what hits you first**
  Becomes usable: A first visit goes to `/welcome` (chips, 80-character words, data notice, Skip). Bill pages then show your groups first on the first paint, with "See everyone" and per-bill chips. Your own words call **Match** and float up matching clauses. A clause with no explanation calls **Explain** and shows a labelled draft. Both APIs fall back quietly on failure.
  Why now: This completes "what it means for you" on the kernel page, and it brings in the two live helpers and their failure paths while there's still only one bill to test against.
  PRD ref: `prd.md > First-Visit Profile`, `prd.md > What It Does`, `prd.md > States and Boundaries`
  Spec ref: `spec.md > Welcome Page (/welcome)`, `spec.md > Head Script (src/components/HeadScript.astro)`, `spec.md > Bill Page (/bills/[id])`, `spec.md > Match API (POST /api/match)`, `spec.md > Explain API (POST /api/explain)`, `spec.md > Data Model` (Reader profile)
  Build: `guard.ts`, `storage.ts`, `ProfileSchema`, `claude.ts` (loads the shared prompts and banned list), `welcome.astro`, `ProfileChips`, `GroupsPanel`, HeadScript (groups + redirect), `api/match.ts`, `api/explain.ts`, `prompts/matcher.md`. Re-run `generate` so group entries and clause `groups[]` exist.
  Verify (mechanical): With cleared storage, `/bills/finance-bill-2026` redirects to `/welcome`. After saving "trader", the trader block comes first in the served HTML plus head-script output (browser check). `curl` Match with "I sell mitumba Gikomba" returns 200 with valid clause IDs; empty words return 400; a bad key (simulated) makes the page show the quiet fallback. Explain returns 409 for an explained clause and 200 with the draft label for a blanked one. A banned-phrase output is discarded (unit check with a stubbed response).
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
  Verify (mechanical): The deployment succeeds. `curl` on the production home returns 200 and contains both bill titles. A production `POST /api/match` returns 200. A production `/bills/finance-bill-2026` contains the fingerprint.
  Learner check: Open the public link on your actual phone, over mobile data, and walk the demo path: welcome → Finance Bill → a Sheng clause → Have your say on a live bill.
  Commit: `Deploy to Vercel with README`

- [ ] **8. What changed: drafts, current law, and bill vs Act**
  Becomes usable: *What changed* shows Finance Bill 2026 vs Finance Act 2026 (what survived and what was dropped). For a bill amending an Act, it shows the current law beside the amendment. Bills with one version show the honest note plus a current-law comparison.
  Why now: This is PRD build step 3, and it depends on the fetch, extract and review machinery from slices 1–3.
  PRD ref: `prd.md > What Changed`
  Spec ref: `spec.md > Compare`, `spec.md > Data Model` (changes), `spec.md > Kenya Law (kenyalaw.org)`
  Build: Pin the Kenya Law URLs for the Acts the Finance Bill amends. Write `compare.py` (clause matching, `null` for added/dropped, amended-section lookup with the verbatim rule, Claude notes as drafts), `ChangeRow` and the *What changed* tab. Then run `review`.
  Verify (mechanical): `compare finance-bill-2026` produces `changes[]`, including at least one `kind: "act"` and one `kind: "current_law"` entry. Every `before`/`after` is verbatim in its source PDF (scripted check). `lint` passes. The built tab shows the rows and the empty-state note on a single-version bill.
  Learner check: Open the Finance Bill's *What changed* tab and pick one change you remember from the 2026 debate. Does the before/after and its note match what actually happened?
  Commit: `Add What changed: bill vs Act and current law`

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
