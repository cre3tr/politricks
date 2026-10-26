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

- [x] **3. Nothing ships without your review, and banned phrases can't get in**
  Becomes usable: `politricks lint` fails on any banned phrase or neutrality flag. `politricks review` walks every draft text, flagged items first, and marks each one reviewed. The page shows *Reviewed* badges for reviewed clauses and *AI draft* for the rest.
  Why now: Your doctrine (neutral, Sheng reviewed by you, never "unconstitutional") has to be mechanical before more content piles up. Every later slice's content goes through this gate.
  PRD ref: `prd.md > What It Does` (neutral), `prd.md > Non-Goals`, `scope.md > Quality Guardrails`
  Spec ref: `spec.md > Lint`, `spec.md > Review`, `spec.md > Data Model`
  Build: Write `lint.py`, `prompts/banned.txt`, `review.py` (terminal walk: accept / edit / skip, flagged first, refuses to finish while lint fails), and the badge rendering in `ClauseCard`.
  Verify (mechanical): Plant "kinyume cha katiba" in one explanation in a scratch copy: `lint` exits non-zero and names the clause. The clean file passes. After reviewing one clause, its JSON shows `"reviewed"` and the built page shows the *Reviewed* badge on that card only.
  Learner check: Run `politricks review finance-act-2026` and review the Sheng for 3–5 clauses you'd put in the demo, editing anything that sounds off. Reload the page and see those cards say *Reviewed*.
  Commit: `Add lint and review gate for all generated text`

- [x] **4. PoliTricks knows who you are, and puts what hits you first**
  Becomes usable: A first visit goes to `/welcome` (chips, 80-character words, data notice, Skip). Bill pages then show your groups first on the first paint, with "See everyone" and per-bill chips. Your own words call **Match** and float up matching clauses. A clause with no explanation calls **Explain** and shows a labelled draft. Both APIs fall back quietly on failure.
  Why now: This completes "what it means for you" on the kernel page, and it brings in the two live helpers and their failure paths while there's still only one bill to test against.
  PRD ref: `prd.md > First-Visit Profile`, `prd.md > What It Does`, `prd.md > States and Boundaries`
  Spec ref: `spec.md > Welcome Page (/welcome)`, `spec.md > Head Script (src/components/HeadScript.astro)`, `spec.md > Bill Page (/bills/[id])`, `spec.md > Match API (POST /api/match)`, `spec.md > Explain API (POST /api/explain)`, `spec.md > Data Model` (Reader profile)
  Build: `guard.ts`, `storage.ts`, `ProfileSchema`, `claude.ts` (loads the shared prompts and banned list), `welcome.astro`, `ProfileChips`, `GroupsPanel`, HeadScript (groups + redirect), `api/match.ts`, `api/explain.ts`, `prompts/matcher.md`. Re-run `generate` so group entries and clause `groups[]` exist.
  Verify (mechanical): With cleared storage, `/bills/finance-act-2026` redirects to `/welcome`. After saving "trader", the trader block comes first in the served HTML plus head-script output (browser check). `curl` Match with "I sell mitumba Gikomba" returns 200 with valid clause IDs; empty words return 400; a bad key (simulated) makes the page show the quiet fallback. Explain returns 409 for an explained clause and 200 with the draft label for a blanked one. A banned-phrase output is discarded (unit check with a stubbed response).
  Learner check: In a private window, open the bill page, go through the welcome screen as yourself (pick groups, type a few of your own words), and see what floats to the top. Then open "See everyone" and confirm nothing is hidden.
  Commit: `Add first-visit profile, groups-first view and live helpers`

- [x] **5. Home shows the two live bills**
  Becomes usable: `/` lists the Tertiary Education, Placement and Funding Bill 2026 and the Public Participation Bill 2025, with title, chamber, stage and *last checked*, and each opens its own bill page with explained clauses.
  Why now: This gives the core journey its real front door, and it proves the pipeline generalises beyond the Finance Bill (different bill layouts, and a Senate bill).
  PRD ref: `prd.md > Home: Active Bills and Una'tafuta?`, `prd.md > The Core Journey` (steps 2–3)
  Spec ref: `spec.md > Home Page (/)`, `spec.md > Intake`, `spec.md > Extract Clauses`
  Build: Add both bills to `sources.yaml` with pinned official URLs. Write the `intake` command (the manual first load: fetch → extract → generate for new entries), then run it plus `lint`, and `index.astro` with the Active Bills list and `LastChecked`. Add overrides as the verbatim check demands.
  Verify (mechanical): `intake` exits 0 for both bills (or a revision records a scanned/unavailable PDF), and `lint` passes. `npm run build` passes, and the home HTML contains both titles plus links that resolve to built bill pages.
  Learner check: Open `http://localhost:4321/`, tap each live bill, and check that its clauses and explanations make sense, especially the Tertiary Education bill from a student's point of view.
  Commit: `Ingest both live bills and list them on home`

- [x] **6. Have your say: how, where and by when, with every fact sourced**
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

- [x] ~~**8. What changed: drafts and current law**~~ **Cut by the learner at the 2026-10-25 cut-off** (see Revisions). The code is merged; it was never run on real data.
  Becomes usable: *What changed* shows, for each Finance Act 2026 section that amends another Act, the current law beside the amendment. For a bill amending an Act, it shows the current law beside the amendment. Bills with one version show the honest note plus a current-law comparison.
  Why now: This is PRD build step 3, and it depends on the fetch, extract and review machinery from slices 1–3.
  PRD ref: `prd.md > What Changed`
  Spec ref: `spec.md > Compare`, `spec.md > Data Model` (changes), `spec.md > Kenya Law (kenyalaw.org)`
  Build: Pin the Kenya Law URLs for the Acts the Finance Bill amends. Write `compare.py` (clause matching, `null` for added/dropped, amended-section lookup with the verbatim rule, Claude notes as drafts), `ChangeRow` and the *What changed* tab. Then run `review`.
  Verify (mechanical): `compare finance-act-2026` produces `changes[]`, including at least one `kind: "current_law"` entry. Every `before`/`after` is verbatim in its source PDF (scripted check). `lint` passes. The built tab shows the rows and the empty-state note on a single-version bill.
  Learner check: Open the Finance Bill's *What changed* tab and pick one change you remember from the 2026 debate. Does the before/after and its note match what actually happened?
  Commit: `Add What changed: drafts and current law`

- [x] **9. Una'tafuta? and passed-bill pages**
  Becomes usable: Search on home finds bills by name or keyword in any language. Passed bills show the **Passed** label, and their *Have your say* tab becomes *Where it can still be challenged* (from civic YAML). `intake --check` proposes new items as a `sources.yaml` diff.
  Why now: This is PRD build step 4. It needs several bills in the system, which slices 5–8 provided.
  PRD ref: `prd.md > Home: Active Bills and Una'tafuta?`, `prd.md > Have Your Say` (passed), `prd.md > Automatic Bill Intake`
  Spec ref: `spec.md > Home Page (/)`, `spec.md > Intake`, `spec.md > Civic Data`, `spec.md > Parliament of Kenya (parliament.go.ke)`
  Build: Build `search-index.json` at site build, then the search UI with the *Hatujapata kitu* empty state. Add the passed variant of `ParticipationPanel`, the challenge avenues in the Finance civic YAML (which you write), and `intake --check` (Parliament and Gazette listings → proposed diff).
  Verify (mechanical): Searching "fedha" or "finance" finds the Finance Bill and a nonsense query shows *Hatujapata kitu* (browser check). The Finance page shows *Passed* and the challenge tab with source links. `intake --check` prints a proposed diff (or "no new items") with no errors.
  Learner check: Search for something the way a citizen would ("HELB", "tax", "ushuru") and see what comes up. Open the Finance Bill's challenge tab and check it reads as information, not advice.
  Commit: `Add Una'tafuta search, passed bills and intake check`

- [x] **10. Suggested constitution articles**
  Becomes usable: Clause cards show "Suggested articles" chips. Tapping one opens the article's text in-app, labelled *"Suggested article — you judge"*, with the Kenya Law link and the constitution's fingerprint. Only each bill's slice of articles ships with its page.
  Why now: This is PRD build step 5, and it needs the constitution loaded before `generate` can suggest validated articles.
  PRD ref: `prd.md > Constitution Check`
  Spec ref: `spec.md > Constitution Loader`, `spec.md > Generate (Claude)`, `spec.md > Article Sheet`
  Build: `politricks constitution`, then re-run `generate` for `suggested_articles` (validated against the loaded articles), followed by `lint` and `review`. Then build `ArticleSheet` with per-page article slicing.
  Verify (mechanical): `constitution.json` has 264 articles and a fingerprint. Every suggested number exists in it. The built bill-page HTML contains only its suggested articles (grep for an unsuggested article's text finds nothing). `lint` passes.
  Learner check: Open a clause's suggested article. Does the suggestion feel relevant, and does the sheet make clear you're the judge?
  Commit: `Add suggested constitution articles`

- [x] **11. Nairobi County bills and motions**
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

- [x] Early usable behavior explored — after slice 2 (the kernel page): look and feel, Sheng tone, clause-beside-explanation layout. Feedback here shapes every later page.
- [ ] Integrated core journey explored — after slice 6 (minimum demoable line): welcome → home → bill → What it does → Have your say, end to end. **Folded into the final kick-the-tires** (Revisions, 2026-10-26).
- [ ] Final kick-the-tires exploration and feedback completed

## Final Review

- [x] FR1. Sticky header: wordmark and EN/SW/SHENG stay pinned while scrolling; the active language is visibly filled in.
- [x] FR2. Hackathon notice: a thin banner under the header and a footer line, on every page (learner's wording, 2026-10-26).
- [x] FR3. Fonts: Anton for the wordmark only; headings bold normal-case sans; body 17px with more spacing; legal text quieter.
- [x] FR4. Home: an active bill wears the green stripe only while comments are open; closed ones are grey with a "Comments closed" badge.
- [x] FR5. Bill page summary first: "In short" and "What it means for you" on top; each clause a tap-to-open row, legal text behind "Show the legal text"; Match results float up and are marked.
- [x] FR6. Light motion (cards, tabs, taps, matched clauses), off under reduced motion.
- [x] FR7. Type and colour with character: Bricolage Grotesque headings, Inter body (both self-hosted, OFL), the flag red and green as bold accents, a standout "In short" box, and polish. The learner: readable, but "really flat"; the point is to spark curiosity.
- [ ] FR8. The POLITRICKS wordmark in Bricolage Grotesque too (learner: "forgot the header text"); Anton retired.
- [ ] Final review complete — feedback resolved and learner confirms ready to ship

## Code Tour and App Map

- [x] Learning activity complete — guided route, focused alternative, prior practice connected, or brief recap
- [x] Optional edit and transfer reflection addressed — offered/declined/already covered/not applicable as appropriate
- [x] `devpost/app-map.html` generated from finished code, checked, and shown, including a project-grounded practice to reuse

Activity and evidence: brief evidence-based recap (focused route, chosen because the learner is plan-first and wanted tighter scoping). The connection: the cut-off set in advance on 2026-10-23 (Revisions "Deadline and "all 12"") turned the slice 8 cut into one line ("Cut 8", Revisions "Slice 8 cut; slice 7 key and domain in place"), protecting the kernel in `scope.md` "The Unique Kernel" and freeing Monday for 8 review fixes. A recap, not hands-on practice.
Route and stops: not toured. The app map carries a 3-stop reference route for Match (`[id].astro` fetch, `api.ts` handleMatch, `claude.ts` callMatch).
Edit outcome: not applicable (recap route; no edit offered).
Reflection: offered and answered (kept in the ignored learner profile).
Activity mode: focused alternative, as a recap. App map: `devpost/app-map.html` (commit 9f88167 snapshot; open the file in a browser), shown to the learner 2026-10-26.

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

### Test kinds recorded, 2026-10-14

- **New user-level skill `test-kinds`** (the learner's request): a cross-repo catalog of test kinds at `haven-ea/references/testing/test-kinds.md` (K1–K15, all earned in this repo, each with its evidence), and a per-repo `TESTING.md`.
- **First audit, of this repo:** `TESTING.md` lists every kind with its location, command and last control.
  - It ran two controls this change hadn't had yet. K9: `DisplayStatus` grew a value, giving `ts(2322)` at `badge.ts:37`. K7: every card was forced to the draft badge, giving 2 FAILs.
  - **Gaps it names:** the K1 and K4 controls have never been run; lint doesn't gate the build (slice 4); the probe and control-copy scripts live only in a scratchpad; live fetch is blocked by Kenya Law's 403.

### Git remote added, 2026-10-14

- **The learner's call:** a private GitHub repo, `cre3tr/politricks`, with `main` and `build/poc` pushed. This supersedes the earlier "no git remote" notes, which stay as written because the log is append-only.
- **Checked before the push:** of the 89 tracked files, the only env file is `.env.example`, with an empty key; no `.env` was ever added in history; `.env` is gitignored.
- **Read back:** visibility `PRIVATE`, default branch `main`, and both remote branches at `e7ad079`.
- Slice 7 (Vercel) can link this repo. Making it public for judging is a later decision for the learner.

### First review sitting, paused, 2026-10-14

- **Where it stands:** none of the 59 items are currently reviewed.
  - c1 and c2 were accepted, then changed by the rule rewrite below, so both read *Changed since review* and lint flags them (2 findings). They come first in the walk.
  - 57 drafts follow: c3 to c55, then the summary, then the 3 group notes. Drafts edited by hand, or rewritten under the rules, are hand-edited, so `generate` won't overwrite them.
- **The learner's Sheng rules from this sitting, now standing in `pipeline/prompts/explainer.md`:**
  - `Hii section`, not `Section hii`: "this" and "that" go first, and "that" is spelled `hio`;
  - `ama`, not `au`;
  - `iko na`, not a bare `ina`;
  - `person`, not `mtu`;
  - "new" before the noun, not `mpya` after it;
  - plain verb forms, not the formal `iliyo-`/`anaye-`/`zinazo-` forms;
  - `ina-change`, not `ina-amend`.
- **Applied to the drafts by hand (`b93bc3a`):**
  - 54 of the 59 Sheng texts were rewritten, with wording changes only. Every rewrite keeps exactly the same numbers.
  - A rule check over all 59 texts finds 0 breaks of any rule above and 0 em dashes.
  - Unchanged, because they already passed: c12, c17, c45, and the trader and transport group notes.
- **Found while doing it:**
  - **The walk holds the bill in memory** and saves the whole file on every accept. Never edit the JSON while it runs; quit first (now a CLAUDE.md gotcha).
  - **The real data broke two generate self-tests.** Once real clauses were reviewed, two checks that built their batch from the real bill failed (`cbccbce`); they now use the reset copy. K4 has a control on record (TESTING.md).
  - **A miscount:** the first count of `Section hii` was 16; the true count was 20. It only looked at the opening, and a second search over the whole text found the other 4.
- **Next, when the learner resumes:**
  1. Run `uv run --project pipeline politricks review finance-act-2026` and accept c1 and c2 again.
  2. Continue from c3. c3 is the learner's own edit, with `ina-change` and `ama` applied.
  3. Tags to decide on the way: c33 and c36 (transport). Changing a tag means quitting, editing the JSON, then rerunning the walk.
  4. c55: add what the levy is charged on (`e`, then accept).
  5. After the walk: lint, build and `check-build`, then commit and push the accepts.
  6. Still open: prune the 12 proposed banned phrases (`banned.txt`); GitHub Dependabot reported vulnerable dependencies on the first push (run `dependency-review`); re-check the live bills' stages; slice 6 civic facts.

### Review sheet export, 2026-10-14

- **The learner's request:** the bill as an Excel sheet with English, Kiswahili, Sheng and a state per row, to review and edit by hand and send back complete.
- **Built:** `politricks export <bill-id>` (`pipeline/politricks/export.py`) writes `exports/<bill-id>-review.xlsx`. It reads the JSON and never writes it; the SHA-256 before and after the real export matched (`fd891a4f…`).
  - One row per item in the walk's order: 55 clauses, the summary, then the 3 group notes (59 rows). Yellow columns are his to change: Decision (dropdown: Accept, Edited, Needs work, Skip), Notes, Groups / sections, English, Kiswahili, Sheng. Grey columns are context: ID, heading, state now, legal text, Sheng words flagged, neutral check.
  - "State now" uses the site's rules (Reviewed, Changed since review, Hand-edited draft, AI draft, Sample, No explanation). The real export read: 52 hand-edited, 5 AI draft, 2 changed since review.
  - A hidden `fingerprint` column holds each row's `current_sha256` at export, so a later import can tell which rows he changed and whether the JSON moved since.
  - Texts starting with `=` are stored as text, not formulas; a text over Excel's 32,767-character cell limit stops the export instead of being cut.
- **New dependency:** `openpyxl` 3.1.5 (pipeline only).
- **Tests:** `export --self-test`, 11 checks, offline, temp folder, real-bill SHA guard. Planted-bug control: swapping the Kiswahili and Sheng columns and dropping the "changed since review" state gave 2 FAILs; restored, 11 pass.
- **Open, the learner's call:** there is no import yet. Rule: only an accept in `politricks review` marks anything Reviewed. So an "Accept" in the sheet can't set Reviewed directly unless the learner decides the sheet is his accept. The two ways: import his edits as hand-edited drafts and then accept them in the walk, or have the walk read the sheet with him driving it.

### Review from the sheet, 2026-10-15

- **The learner's call:** a row he marks Accept in the export sheet is his accept, **when he runs the import himself**. This supersedes the 2026-10-14 entry's "no import yet". That entry stays as written, because this log is append-only.
- **Built:** `politricks review <bill-id> --from-sheet <file>` (`pipeline/politricks/review_sheet.py`). It plans the whole sheet on a copy of the bill, prints every action and every new text in full, and saves once, on `yes`, with an atomic replace. It keeps the walk's rules: a banned phrase blocks an Accept; a sample stays a sample; an edit without an Accept is a hand-edited draft; an item already reviewed and unchanged keeps its date.
- **Guards, all from the design or the cold review:**
  - piped input is refused before anything is read (run on the real paths: `echo yes | … --from-sheet …` gives FAILED, exit 1, bill unchanged);
  - a row whose item changed since the export is skipped;
  - an Accept on a group note whose sections moved through tag edits in the same sheet is refused, and so is one on a group the edits dropped;
  - the bill file changing while the prompt is open means no save;
  - the whole sheet fails, writing nothing, on: another bill's sheet; a renamed column; an unknown or repeated row id; an emptied text cell; an unknown group name; a formula; a number or date Excel made of typed text; a Legal text cell that isn't its section's; or an edited text that is exactly another row's original (a column sorted on its own).
- **Sonnet cold review** of the first version found that piped stdin could answer `yes`, that a column sorted on its own was accepted as edits, that numbers typed into cells were saved as their stored value (`5%` as `0.05`), and that the plan showed ids but not content. It also listed 11 mutations the 18 checks missed. All of these are fixed and covered.
- **Not fixed, documented:** a walk already open in another terminal saves its whole in-memory bill on its next accept and would overwrite a sheet import. The prompt says so; there is no lock. The walk also still takes piped answers.
- **Tests:** `review --sheet-self-test`, 35 checks. Planted-bug controls: 18 mutations, each one alone. 17 fail a named check; removing the Ctrl-C catch crashes the self-test, which is loud (and saves nothing either way).

### Outstanding items started, 2026-10-19

The learner asked to start the open items while he reviews the Finance Act in the Excel sheet. His answers (AskUserQuestion, same day):
- keep all 12 proposed banned phrases, permanently;
- generate the Tertiary and Public Participation explanations only **after** his sheet review is imported, so new Sheng rules reach the prompt first;
- for civic facts, "I draft, you check";
- wait out the Kenya Law 403.

**The Finance Act JSON was not touched.** Its SHA-256 is still `fd891a4f…`, the same as at export, so every row of his sheet stays current.

- **Banned phrases:**
  - The 12 are now doctrine, in `banned.txt` and in `lint.py DOCTRINE_PHRASES`, so the self-test fails if one is deleted. Control: deleting `batili` gave 1 FAIL.
  - Lint over the 177 Finance Act texts: 0 banned hits. Its 2 findings are c1 and c2, changed since review, which are his to re-accept.
  - spec.md > Lint now names them.
- **One writer at a time (`reviewlock.py`).** This closes the 2026-10-15 "Not fixed, documented" item: a walk overwriting a sheet import.
  - Every command that saves a bill holds an OS file lock from load to save: the walk, `--from-sheet`, `generate`, `fetch`, `extract`, `samples` and `verify`. Any other command refuses at once, exit 1.
  - The OS frees the lock when the process ends, so it can't go stale.
  - The sheet import holds the lock across its `yes` prompt.
  - `generate` refuses before anything is sent, because a run loads the bill, waits minutes, then saves the whole file.
  - The import's "make sure no walk is open" warning is gone. Its bill-changed check stays, for hand edits and walks started before this code. At the time of this change no `politricks` process was running.
- **Direct-call cost log.** Refusal retries and the summary append their token usage to `pipeline/.cache/batches/<bill>.direct.jsonl`. This closes 2026-10-14's "add that before costing the next bill". It is a lower bound: a call that raised leaves no line.
- **Stages re-checked from Parliament.** Recorded in both `sources.yaml` and the bill JSONs, because `fetch` (which copies them) is blocked.
  - **Tertiary:** NA Bills Tracker as at 8 Oct, row 330 (First Reading 11 Aug 2026, no Second Reading), and the Education Committee chair's statement, Hansard 6 Oct, pp. 6-7. Written memoranda were first due 24 Sep and were extended to Fri 2 Oct 2026, 5pm, so our earlier "2 October" was right. The Committee is preparing its report for Second Reading.
  - **Public Participation:** Senate Bills Tracker as at 16 Oct, row 167. Second Reading was 7 and 8 Oct 2026, "Bill awaiting Division at Second Reading Stage".
  - The first wording said "awaiting the vote", which breaks the `vote` doctrine. It now reads "the decision (Division) is still to come".
- **Civic YAML drafted** (`pipeline/civic/`, three files), for Ian to check fact by fact before slice 6 ships. Each fact is marked:
  - `[OFFICIAL]`: the Hansard, the trackers, and the Constitution (Parliament's copy, since Kenya Law is down);
  - `[NEWS]`: the Senate notice's deadline and channels, from Eastleigh Voice (14 Sep 2026), quoting Senate Clerk Jeremiah Nyegenye. The notice itself wasn't found online;
  - `[UNKNOWN]`: the Tertiary channels.
  - **Schema:** `deadline_time` is an addition to the spec's schema.
  - **Checks:** a scan with lint's own matcher over all 9 texts found 0 banned phrases and 0 em dashes, and every text has a `source_url`. Control: a planted string hit 3 phrases.
  - **Not built yet:** `civic.py`, the panels and the translations stay in slice 6.
- **Dependabot:** 4 high alerts (npm: sharp, source-map-js, http-cache-semantics, path-to-regexp). Started in a separate session on its own branch. Not merged here.
- **Sonnet cold review** of the diff: no blockers. Every date, quote and article was checked against the downloaded PDFs.
  - **Fixed from it:**
    - S1: the Tertiary P.O. Box and venue were in no source we read. Removed and marked `[UNKNOWN]`.
    - S2: the Tertiary next step stated past plans as current. It now carries the date.
    - S3: CLAUDE.md line maps and gotchas.
    - S4: the lock now also covers fetch, extract, samples and verify, and the CLI prints their refusal.
    - N1: a failed unlock can't mask the real result.
    - N2: a typo, and the refusal message.
    - N3: the log docstring now says lower bound.
    - N5: the avenue names are now nouns, not imperatives, and the spec notes the drafting decision.
  - **Left:** N4. The self-tests take both locks in one process. The reviewer checked cross-process refusal and release-on-kill by hand, and the POSIX branch is unexercised.
- **Tests:**
  - Self-tests: lint 36, review 43, sheet 37, generate 77, intake 22, export 11, all passing.
  - Lock controls (each alone): a different lock file per call gave review 2 FAILs, sheet 1, generate 1. Removing the usage log call gave 1 FAIL.
  - `npm run build`: 0 errors. `check-build`: 117 / 47 / 141 PASS.
- **Next:**
  1. Ian finishes the sheet, saves it, and runs `politricks review finance-act-2026 --from-sheet exports/finance-act-2026-review.xlsx` at a terminal. Then lint, build and `check-build`, then commit and push.
  2. New Sheng rules from that review go into `explainer.md`. Then probe and run Tertiary and Public Participation, about $0.70 to $1.20 for both by the Finance Act's measured cost [MEDIUM], with Ian's OK.
  3. Ian checks the civic YAML. The Tertiary channels and the Senate notice still need the official notices.
  4. Slice 3's learner check, then slice 4.

### Ian's trilingual alignment of the Finance Act, 2026-10-19

The learner sent `finance_act_2026_reference_alignment.md`, a clause-by-clause cleanup of the Finance Act across the three languages. His calls (AskUserQuestion, same day):
- **The doc is his review.** It replaces the sheet he had open, which was never saved after the 2026-10-14 export. Edits land as hand-edited drafts, never Reviewed.
- **Restore the print.** Headings are corrected only where the scan misread them, and c5's legal text stays as fingerprinted.
- **Its rules apply everywhere they occur,** and go into the prompt.
- **VAT is "Kodi ya Ongezeko la Thamani"** (the doc proposed "Kodi ya Thamani Iliyoongezwa").

**Checked against the bill and the printed page before applying:**
- Two of the doc's "current state" cells didn't match the data. c33's Sheng already opened "Section 33 ina-", and c45 already said "Taratibu za Kodi". Five rows were no-ops.
- **c5:** the page image prints "(5A)". The text layer says "(SA)", and that text layer is what the legal text and its fingerprint come from.
- **Six headings the doc rewrote are printed that way in the Act:** c34 "Amendment of the section 2", c43 "new 23B section", c42 and c46 with no "a", c46 with no full stop, and c55 "Cap 427." Per his call they keep the printed wording.
- **Numbers:** the Act writes amounts and durations in words ("twentieth day", "ten billion shillings", "thirty days"). So the Kiswahili number words already follow "as in the legal text" and were left alone. The exception is his c4 row ("siku ya ishirini" → "tarehe 20"), applied as he wrote it.

**Applied (the edit scripts are in the session scratchpad; this entry is the record):**
- **38 items changed and are now hand-edited drafts** (counted by comparing every item's texts against HEAD). Lint shows 1 finding: c2, changed since review, untouched by this pass. States are now 55 hand-edited drafts, 3 AI drafts and 1 changed since review.
- **Sheng opens with the section's number:** `Section N ina-` in 16 clauses that opened "Hii section ina-", plus c42 ("Finance Act ina-add").
- **c5 explanations:** "(SA)" → "(5A)" in all three languages, 6 places. The legal text is unchanged.
- **Kiswahili:**
  - "asilimia N" → "N%" in 9 items (c3, c20, c21, c24, c36, the summary and the 3 group notes), matching the English and Sheng columns. A first pattern missed "asilimia 30." at a sentence end; a second pass fixed c24 and c36. 0 are left.
  - "Sehemu" → "Kifungu" where it means a section: c10, c32, c53, with verb agreement. "Sehemu" for a Part of a Schedule is kept.
  - One name per Act:
    - Income Tax Act: `Sheria ya Kodi ya Mapato` (c15, c23, the summary, the employee note);
    - VAT Act: `Sheria ya Kodi ya Ongezeko la Thamani (VAT)` (c25-c31, c33, the summary);
    - Tax Procedures Act: `Sheria ya Taratibu za Kodi` (c38, c41, c49, c50, the summary);
    - Excise Duty Act: `(Excise Duty Act)` added in c36, the summary and the transport note;
    - Stamp Duty Act: `Sheria ya Ushuru wa Stempu (Stamp Duty Act)` (c54).
  - c4: "tarehe 20 ya mwezi".
- **Headings:** 9 transcriptions copied from the page images go into `pipeline/overrides/finance-act-2026.yaml` `heading_transcriptions` (c4, c7, c11, c13, c40, c42, c45, c46, c47). Re-running `extract` changed exactly those 9 headings and their `heading_source`, nothing else. Headings aren't in the review fingerprint.
- **The prompt** (`explainer.md`) now carries the rules:
  - Sheng opens with `Section <N> ina-`, while "this section" mid-text stays `hii section`;
  - Kifungu for a section, Sehemu for a Part;
  - percentages as `31%`;
  - one Kiswahili name per Act;
  - the doc's fixed terms (kukatwa chanzoni, zisizotozwa ushuru/zilizosamehewa, kuwasilisha marejesho; ku-repeal na ku-replace, ku-file return).
- **Fresh review sheet:** `exports/finance-act-2026-review-2026-10-19.xlsx`. The old sheet is stale for every changed row.

**Tests:**
- Lint: 177 texts, 0 banned phrases.
- Self-tests: lint 36, review 43, sheet 37, generate 77, intake 22, export 11, verify (`CHANGED` on the tampered copy), all passing.
- **BLOCKED: `npm run build`.** It exits 1 with EPERM opening `node_modules/astro/dist/cli/index.js`. Every file under `node_modules/astro/dist` is unreadable ("Access to the path is denied", 300 of 300 sampled), while the other 270 packages read fine. The ACLs are normal, no node process is running, and the files date from 2026-09-25. The build passed at about 20:41 the same day. `check-build` against the stale `dist` gave 36 FAILs: the new texts missing from old pages. **Nothing is committed until the build passes.**

**Not done, and why:**
- **c5's legal text still reads "(SA)".** Correcting the legal text needs a decision on an OCR-correction mechanism, because the fingerprint ties it to the PDF's text layer. Other clauses carry the same kind of noise ("m proper alphabetical sequence", "couectorto").
- **The doc's grammar edits to the six printed headings** were left out, per his call.
### Dependabot alerts, 2026-10-19

- **Re-measured:** `gh api "repos/cre3tr/politricks/dependabot/alerts?state=open"` returned the same 4 high, npm, `package-lock.json` alerts first filed 2026-10-13 (#1 to #4). `npm audit` counts them as 6 packages, because it also lists the two Vercel packages in the path-to-regexp chain. `package-lock.json` is identical on `main` and `build/poc`, so the alerts on `main` describe this branch too.
- **Fixed, all in-range lockfile bumps** (`package.json` unchanged; 29 of 471 lock entries moved, all of them these three packages or sharp's per-platform binaries):
  - `sharp` 0.35.4 to 0.35.5 (GHSA-wq5f-xc86-pv6w, librsvg). Optional dependency of `astro@7.3.5` at `^0.35.4`.
  - `source-map-js` 1.2.1 to 1.2.2 (GHSA-68fv-2mgg-jv7q). Declared at `^1.x` by postcss, `@tailwindcss/node`, css-tree and magicast.
  - `http-cache-semantics` 4.2.0 to 4.3.0 (GHSA-ch52-4w7c-c8xp). The advisory lists no patched version, but its range is `<= 4.2.0`, and `astro@7.4.1` now declares `^4.3.0`.
  - Every target was queried in the GitHub advisory database (`gh api "advisories?ecosystem=npm&affects=<pkg>@<ver>"`) and returned none. Control: the same query on each old version returned its alert.
- **Not fixed, path-to-regexp 6.1.0 (GHSA-9wv6-86v2-598j, patched 6.3.0):**
  - It's pinned exactly by `@vercel/routing-utils@6.6.0`, which is that package's latest, and `@astrojs/vercel@11.0.16` (latest) still takes `^6.6.0`.
  - Vercel already ships 6.3.0 next to it under the alias `path-to-regexp-updated`. Its `superstatic.js` compiles every route with both copies and logs any difference. So keeping 6.1.0 is a deliberate migration probe, and an `overrides` entry would defeat it. No override was added.
  - Reachability: it runs at build time, on route patterns we write, never on visitor input. The only pattern this site compiles is `/_astro/(.*)`. With `FORCE_PATH_TO_REGEXP_LOG=1`, Vercel's probe printed the same regex from both copies (`^\/_astro(?:\/(.*))$`, keys identical). That's the patched output.
  - **Recheck when:** `@vercel/routing-utils` goes above 6.6.0, or slice 4 adds API routes or redirects. Each new pattern gets compiled too, so rerun the build with that variable and read the diff lines.
  - **The learner's call:** dismiss alert #1 on GitHub as tolerable risk with this reason, or leave it open. It wasn't dismissed from here.
- **Added `.github/dependabot.yml`:** monthly npm updates, patch and minor grouped, majors kept separate (not ignored), at most 3 open PRs. It takes effect only once it's on `main`. There's no CI, so nothing runs `npm audit` on a push.
- **Verified:** `npm run build` exit 0 (0 errors, 0 warnings), and `check-build` exit 0 for `finance-act-2026`, `public-participation-bill-2025` and `tertiary-education-bill-2026`. All 9 files in `.vercel/output` hash the same before and after the bumps, from a fresh build (timestamps 20:32 against a 20:30 baseline). No bill JSON or pipeline file was touched.
- **Done when it reaches `main`:** Dependabot reads only `main`, so alerts #2 to #4 close after this branch is merged and GitHub rescans. Until then they still show as open.
- **2026-10-21 note:** this entry's three lockfile bumps reached `build/poc` through politricks PR #1 (`b6d83ff`, which also took vite 8.3.5). The 2026-10-19 branch's lockfile was therefore superseded, and only `.github/dependabot.yml` and this entry were carried over from it.

### CI, 2026-10-22

- **Added `.github/workflows/ci.yml`** (politricks PR #6), ahead of going live. This supersedes the 2026-10-19 note above that "There's no CI".
  - **Runs on:** every PR, and pushes to `main` and `build/poc`. Offline: no secrets, no Anthropic API, no Kenya Law. `permissions: contents: read`.
  - **Job `test`:** `uv sync --locked`, `npm ci`, the seven self-tests, `npm run build`, then `check-build` per bill. An empty bills directory exits 2.
  - **Job `content`:** `politricks lint` on the real bills. It's a separate job, so a pending review never reads as broken code.
- **First run (39698688075):** `test` passed with lint 36, review 43, generate 77, intake 22, export 11 and sheet 37, the same counts as the local Windows baseline. That's the control showing each test ran rather than being skipped. `content` failed, as expected, on `finance-act-2026 c2: changed since it was reviewed (reviewed 2026-10-14)`.
  - The bill lock's POSIX `flock` branch passed its first run (review J, sheet L). Until now it had never been exercised.
- **Open for the learner:** re-accept c2 in `politricks review`. That turns `content` green. It's the last lint finding before going live.
- **Dependabot PRs:**
  - **#4 (astro 7.3.5 to 7.4.0, @astrojs/vercel 11.0.11 to 11.0.15, zod 4.6.5 to 4.6.7):** builds clean locally, 0 errors, and `check-build` passes for all three bills. It targets `main`, so it gets CI once `ci.yml` is on `main` and Dependabot rebases it.
  - **#5 (TypeScript 6 to 7):** cannot install. `npm ci` fails with ERESOLVE, because `@astrojs/check` peers `typescript ^5.0.0 || ^6.0.0`, and that's still true at its latest version (0.9.12, 2026-10-21). Leave it open, because majors are kept, not ignored (`dependabot.yml`). **Recheck when:** `@astrojs/check` widens its peer range.

### c2 re-accepted and #4 merged, 2026-10-22

- **c2 re-accepted (`ad5ce8a`, on `main`).** Its 10-14 stamp `d55865b4` matched neither its text before nor after `b93bc3a`, the Sheng rules pass. So the site has shown c2 as *Changed since review* since 10-14. Ian re-accepted it in `politricks review`, and the stamp is now `9525bdbf`. Only `review.at` and `review.sha256` changed. CI on `main` passed both jobs (run 39753474071), with lint at 0 findings over 3 bills.
- **First attempt, not committed:** the walk also offered every draft after c2, and all 59 items got accepted, the summary, 3 groups and 55 clauses. It was caught by a field-by-field diff against `HEAD` and restored. Rule and evidence: `haven-ea/references/ledger/projects/devpost-basics-2026.md`. To re-accept one item, press `q` right after it.
- **#4 merged (`38cc752`):** astro 7.3.5 to 7.4.0, @astrojs/vercel 11.0.11 to 11.0.15, zod 4.6.5 to 4.6.7. It was rebased onto `ad5ce8a`, and both jobs passed (run 39753737094) with the usual counts. `build/poc` was fast-forwarded to `main`.
- **New, open: 29 pip alerts on `pipeline/uv.lock`**, filed 00:22Z on 10-22, the first scan of the pipeline's Python dependencies.
  - Pillow 12.3.0 (28 alerts, 4 critical): every one is patched in 13.0.0. It comes in through `pdfplumber`, which parses the bill PDFs locally.
  - certifi 2026.7.22: patched in 2026.10.19, through `httpx`.
  - `uv lock --dry-run --upgrade-package pillow --upgrade-package certifi` resolves both cleanly. Not applied yet.

### Pillow 13 and certifi, 2026-10-22

- **#8 merged (`c74acc2`):** pillow 12.3.0 to 13.0.0 and certifi 2026.7.22 to 2026.10.19, via `uv lock --upgrade-package`. Only `pipeline/uv.lock` changes, and only those two versions move.
- **Extraction control:** a scratch script dumped everything extraction derives from the three real PDFs: lines, margins, the stripped log, the report and every section. It ran with `write=False`, before and after the upgrade, and touched no repo file. All three dumps are byte-identical: Finance Act 1403 lines and 55 sections, Public Participation 288 and 21, Tertiary Education 1098 and 68.
  - `extract <bill> --check-json` passed on both versions. A copy with one word changed in c1 failed `VERBATIM FAIL c1` on both, which proves the check can fail.
- **CI:** both jobs passed on the PR. The first run's `test` record showed `in_progress` for minutes after every step had succeeded. Closing and reopening the PR gave a clean second run, and both jobs passed there too. `main` at `c74acc2` passed `test`, `content` and `update-uv-graph`.
- **Alerts:** the dependency graph showed 13.0.0 at about 15:15Z, and all 29 pip alerts (#8 to #36) were marked fixed between 15:16:04 and 15:16:10Z. politricks now has 1 open alert, path-to-regexp, kept by decision (2026-10-19 above).

### `review --flagged` and `--only`, 2026-10-22

- **Closes the walk trap** from "c2 re-accepted" above, where answering `a` to re-accept one flagged clause accepted 59 items. The full walk is unchanged. Two options walk less:
  - `politricks review <id> --flagged` walks only what lint flags. The drafts it leaves out are counted and named in one line ("58 other draft(s) left out" on the Finance Act today).
  - `politricks review <id> --only c12 summary group:trader` walks only the named items, **whatever their status**. So a reviewed text can be reopened to fix a typo spotted on the site. An unknown id, or a clause with no explanation, refuses the whole run (exit 1) before anything is shown, so a typo can't walk nothing and report "done".
  - The two can't be combined, and neither applies to `--from-sheet` (argparse refuses both). "Not done while lint fails" still covers the whole bill.
- **This supersedes** the "press `q` right after it" workaround above. Diffing the bill field by field against `HEAD` before committing a review still applies.
- **Tests:** review self-test 43 → 54 (scenario K). Its first check is a control: the unfiltered walk on the same trap bill, with the same answers, accepts all three items. Planted bugs, each alone: `--flagged` ignored → 4 FAILs; `selection_errors` always empty → 3 FAILs.
  - Read-only CLI runs against the real Finance Act, stdin closed: `--only c999 c2` exited 1 naming c999; `--flagged` walked 0. The bill's SHA-256 was unchanged.
  - All self-tests pass (lint 36, review 54, sheet 37, generate 77, intake 22, export 11, verify `CHANGED`). Lint: 0 findings over 177 texts. `npm run build` exit 0. `check-build` exit 0 for all three bills (118 / 47 / 141 PASS).

### Deadline and "all 12", 2026-10-23

- **Deadline:** Mon 26 Oct 2026, 5:00 pm EDT, which is 21:00 UTC and **00:00 EAT on Tue 27 Oct**. Planning started Fri 23 Oct around 12:30 EAT, so about 83 hours remained.
- **The learner's call:** build all 12 slices, not just the minimum demoable line. The `6-ship` gate needs every slice ticked, plus the hands-on checkpoints, the Final Review and the Code Tour and App Map.
- **State at the start:** slices 1, 2a and 2b ticked. Slice 3's code is done; only its learner check is left. Slice 5's key-independent half is done. CI is green on `main` and `build/poc` (`dc65770`). The repo is private.
- **External dependencies, probed today with a browser user agent:**
  - Kenya Law: 403, from both `new.kenyalaw.org` and `kenyalaw.org/kenya_gazette/`. All three bills' pinned sources and the spec's Acts, Constitution and Gazette sources are on Kenya Law.
  - parliament.go.ke: 200. Its Constitution PDF (193 pages) has a usable text layer: page 21 extracts cleanly, and the article number "264." is present.
  - nairobiassembly.go.ke: 200. `/bills/` links 5 PDFs from 2026. `/motions/` holds a Motion Tracker and Notice of Motion section, but no PDF links were found on that page.
  - kra.go.ke: 200. Its home page links no Acts.
  - Vercel: the connector is signed in (Hobby plan), and no PoliTricks project exists yet.
- **Blockers found while planning:**
  - Slice 8's amended Acts, slice 9's Gazette index, slice 10's Constitution and slice 12's weekly `verify` all depend on Kenya Law as the spec stands.
  - Slice 10 bumps `REVIEW_HASH_VERSION`, which turns every reviewed item into "changed since review", so demo-clause reviews done before it must be redone (`review --flagged`). `generate` also skips protected clauses, so the 55 hand-edited Finance Act clauses would get no suggested articles without an articles-only pass.
  - Slice 7 (Vercel) and slice 12 (repo secret, "Allow GitHub Actions to create PRs") need the learner to enter the API key and change settings himself.
- **Learner decisions (AskUserQuestion, 2026-10-23):**
  - **Spend:** both approved. Tertiary and Public Participation explanations (est. $0.70 to $1.20), and the suggested-articles pass when slice 10 is ready (est. $1 to $2 [LOW]). The smaller bill (Public Participation, 21 clauses) runs first as the probe, and its output is checked against the 10-19 Sheng rules before Tertiary runs.
  - **Slice 8 source:** first look for the National Assembly-approved Public Participation Bill on parliament.go.ke, a real two-version pair. If it isn't there, back to the learner (KRA Acts, or wait for Kenya Law).
  - **Cut-off: Sun 25 Oct, 20:00 EAT.** Any slice not done by then gets a recorded cut, with the learner's OK, and Monday is shipping only: checkpoints, Final Review, app map, public repo, video, submission. The submission goes in before the learner's Monday evening shift (an assumption from his context file, not stated today).
  - **Parallel builders:** worktree-isolated Sonnet agents take the independent pipeline pieces (Constitution loader, `intake --check`, and research spikes for slices 6, 8 and 11). The lead builds slice 4 and the site work. Every diff is reviewed before it merges, and slices are still ticked in order.
- **Planned order:** 5 (generate) → 4 → 6 → 7 → 8 → 10 → 9 → 11 → 12. Slice 3's learner check (3 to 5 demo clauses, `review --only`) comes after slice 10, because its fingerprint bump would undo earlier reviews. Pipeline pieces for 8 to 12 are built ahead in worktrees. Reviewing all 58 Finance Act drafts is not required to ship: unreviewed items stay labelled *AI draft*.
- **Learner decisions, second round (AskUserQuestion, 2026-10-23):**
  - **Slice 8: OCR it, labelled.** The National Assembly-approved Public Participation Bill (passed with amendments 12 Aug 2026) is on parliament.go.ke as a 10-page scan with no text layer; the first-published Parliament copy is a scan too. Install Tesseract, OCR the approved text, compare clause by clause against the Kenya Law text-layer copy. Every "after" is labelled as read by OCR from a scan, with a page link. Source: https://www.parliament.go.ke/sites/default/files/2026-09/The%20Public%20Participation%20Bill%2C%202025%20%28National%20Assembly%20Bills%20No.%2044%20of%202025%29-%20as%20approved%20by%20NA%20.pdf
  - **Slice 11: approved.** The Nairobi City County Appropriation Bill 2026 (the only 2026 county bill with a text layer, 14 pages) and the motion "Restoration of Public Infrastructure Damaged by Public and Private Construction Works" from the official Hansard PDF of 3 July 2025 (nairobiassembly.go.ke/ncca/wp-content/uploads/hansard/2025/3rd-July-2025.pdf, pages 9-10). Generation spend approved (est. $0.10 to $0.30 [LOW]).
  - **Slice 6: verified facts only.** Show the sourced deadline and the bill's own official "Submit Comments" form link on parliament.go.ke. Email and P.O. Box show as "not found in an official notice for this bill". The Senate's 28 Sep 2026, 5pm deadline is confirmed on parliament.go.ke (node/26432, 23 Sep 2026). No Tertiary notice was found (pages checked are in the spike report).
- **Progress at the usage-limit stop (Fri 23 Oct, afternoon EAT):**
  - Done and pushed: Tertiary and Public Participation drafts (`feeb1b9`; 6 banned-phrase texts await the learner's `review --flagged`), `intake --check` merged (`c42c21e`), Constitution loader merged (`d1af433`, 264 articles).
  - On branch `wip/slice-4` (pushed, unfinished): guard, banned matcher, profile schema/storage, matcher prompt, Match/Explain handlers and routes, head script. Still to do: welcome page, chips, groups panel, bill-page wiring, explain call, CSS, `scripts/check-api.mjs`, banned vectors, build.
  - Not merged yet: branch `worktree-agent-a55d637c2408567bf` (`7b03e85`): `verify` reports UNREACHABLE (live run: 3 UNREACHABLE, exit 0), and `.github/workflows/update.yml`. Review the diff before merging. Its findings: `intake --check` proposes 147 entries (needs a filter before running weekly), and `generate` redrafts every unprotected clause.
  - Still running when stopped: the `civic.py` builder (slice 6 pipeline) in its own worktree. Nothing from it is merged.

### Slice 4 ticked: profile, groups first, Match and Explain, 2026-10-23

- **Built:**
  - `src/lib/guard.ts` (word cleaning), `banned.ts` (TS twin of lint's matcher), `api.ts` (pure Match/Explain handlers), `claude.ts` (real calls, prompts bundled with `?raw`, key via `getSecret`), `groups.ts` (chip labels).
  - `ProfileSchema` and profile storage.
  - The head script: groups before first paint, and the welcome redirect only when storage works.
  - `/welcome`, `ProfileChips`, `GroupsPanel`, the Match float-up on bill pages, and Explain for an unexplained clause.
  - `pipeline/prompts/matcher.md` and `@anthropic-ai/sdk` ^1.1.0. Vercel `maxDuration: 60`.
- **Mechanical verify (browser on the dev server, 2026-10-23):**
  - With no profile, `/bills/finance-act-2026` redirected to `/welcome?next=%2Fbills%2Ffinance-act-2026`.
  - Saving "trader" with words `I sell mitumba at <b>Gikomba</b>` stored the words with the tags stripped and returned to the bill.
  - The trader note was the only one shown, at `order: -1`. "See everyone" showed all three of the bill's groups, and the per-bill chips changed the order while the saved profile stayed "trader".
  - A live Match call returned 8 clause ids (Tax Procedures and VAT sections), floated them to the top and cached them under `pt.match.finance-act-2026.<hex>`.
  - Error paths: empty words gave 400 `empty`, an unknown bill gave 400 `unknown_bill`, and an explained clause gave Explain 409.
  - At 375 px there was no horizontal scroll, and the welcome page shows the saved choices on a return visit.
  - `scripts/check-api.mjs`, 19 checks, all offline with stubbed calls: Explain 200 for a blank clause, a banned phrase in any language discarded (502), the model's own neutral flag discarded, Match ids filtered and capped at 8.
  - `banned_vectors.json`: 16 hand-written cases, matched by both Python (lint `--self-test` 5b) and TS. Planted bugs, each alone: the matcher without its left boundary failed 1 check ("devote", "kupiga kura"), and Explain without the banned check failed 1.
  - The build is clean, and `check-build` passes 118 / 49 / 143.
  - The local build output held 0 `sk-ant-` matches over 921 files. Control: the key NAME was found.
- **Not observed in the browser:** the page's quiet "couldn't personalise" fallback on a failed Match. The handler's 502 is tested offline, and the client path was read, not run.
- **Copy drafted for the learner's check (Sheng gate):** chip labels, the welcome page text, "Who it affects" and the match notes. The spec's Explain label had an em dash; it is now "AI draft (not yet reviewed)", the card badge's own wording.
- **Learner check pending** (fast mode, at the integrated checkpoint after slice 6): go through `/welcome` as yourself in a private window and see what floats up.

### Slice 6 built (not ticked), 2026-10-23

- **Civic facts:** the YAML now holds verified facts only (the learner's call). Public Participation cites the Senate's own news item, `parliament.go.ke/node/26432`, for the 28 Sep 2026 5pm deadline. Both live bills link Parliament's "Submit Comments" form for the Bill; both forms and the news item loaded (HTTP 200) on 2026-10-23.
- **`politricks civic` ran live** for all three bills (merged from the builder at `943ba18`): 3 direct calls, all drafts. A second run wrote nothing, and lint covers the 30 new texts.
- **Site:**
  - `BillTabs`: three tabs, a progressive enhancement, with the open tab in the hash.
  - `ParticipationPanel`: open, not open (stage and what usually happens next, plus when the window closed), and passed (challenge avenues). *Last checked* is always shown, and the 7-day stale note is worked out in the browser.
  - "What changed" shows the honest one-version note until slice 8.
  - Home shows "Participation not open", or a day count while a window is open.
  - The Zod types for `participation`, `stage_next` and `challenge` are tightened.
- **Verified:**
  - Build clean; check-build 118 / 49 / 143.
  - In the browser: Public Participation shows the closed variant with its 3 official links. The Finance Act shows 3 avenues under "Where it can still be challenged", and switches to Sheng.
  - The stale note appears for a `checked_at` more than 7 days back. That check re-ran the same expression in the console, not the page's own script.
- **Civic badges** show "AI draft" for drafts. Anything else shows "Changed since review" until a TypeScript twin of `civic_sha256` exists, so the site never claims a review it can't check.
- **Why not ticked:** the slice's build step "run `review` on the civic translations" can't happen until `politricks review` walks the civic blocks (deferred until after the other slices, 2026-10-23). The learner also still has to check the civic facts.

### Slice 7 started: Vercel project, 2026-10-23

- **Vercel project `politricks`** created through the Vercel connector (`prj_tDmuRJRgV4OV9gSqvlk8avUW3JVk`, Hobby plan, team `team_HBFNWovYoe2NjNKvxdS5zxLP`). It is linked to GitHub `cre3tr/politricks`, and its production branch is `main`. Pushes to `build/poc` make preview deployments.
- **`vercel.json`** pins `buildCommand: "npm run build"` and `installCommand: "npm ci"`. The README covers run steps and "verify a fingerprint yourself".
- **Left for the learner:**
  - Add `ANTHROPIC_API_KEY` in Vercel → Project → Settings → Environment Variables. A separate key with a low spend limit is suggested, because Match and Explain are public endpoints. Until it is set, both answer 502 and the page falls back quietly.
  - Merging `build/poc` into `main` makes the production deployment.

### Slice 9 ticked: Una'tafuta?, passed bills, intake --check, 2026-10-23

- **Search:** `src/pages/search-index.json.ts` builds `/search-index.json` with the site (26,916 bytes for 3 bills). Per bill it holds the title, stage, the summary and group notes in all three languages, every clause heading, and the multi-word proper names in the legal text with their initials. The initials are why "HELB" finds the Tertiary bill: the explanations never use the acronym, only the legal text's "Higher Education Loans Board".
  - The home page box fetches the index on first use, requires every term (AND), and ignores case and accents (`\p{M}`).
  - Results clone a server-rendered `<template>`, because Astro's scoped CSS doesn't reach elements created in the browser. *Hatujapata kitu* appears for no results, with "Clear search".
- **Verify:**
  - Against the built index: `fedha` finds the Finance Act and Tertiary, `finance` the Finance Act, `HELB` and `KUCCPS` Tertiary, `KRA` and `ushuru` the Finance Act, `zxqv` nothing.
  - In the browser: the same results, *Hatujapata kitu* on nonsense, Clear empties the list, and the result cards are styled with *Passed* on the Finance Act only.
  - The Finance Act page shows *Passed* and "Where it can still be challenged" with its sources (slice 6).
  - A live `intake --check` exited 0: 3 listings read, the Gazette UNREACHABLE (403), 147 proposals, `sources.yaml` unchanged.
- **Found on the way:** the Write tool, like a Bash heredoc, turns `\uXXXX` escapes into literal characters. Two accent regexes and the guard's zero-width and bidi range landed as invisible characters. They still worked, but every one is now written as a Unicode property class (`\p{M}`, `[\p{Cc}\p{Cf}]`), so the source has no invisible characters. `banned.ts` still holds a literal soft hyphen, exactly like its Python twin `hashing.py`.

### Slice 10, pipeline half (built in a worktree, not yet run on real data), 2026-10-23

- **`suggested_articles` is now in the clause fingerprint** (Python `models.current_sha256`, TS `clauseSha256`; sorted numerically, so `[43, 10, 9]` and `[9, 10, 43]` give one hash). The shared vectors gained a fifth case (c9, unsorted articles) and were regenerated.
- **The version is per kind, not global** (deviation from "bump `REVIEW_HASH_VERSION`"): clauses are on `CLAUSE_HASH_VERSION` = `politricks-review-v2`; summaries, groups and civic blocks stay on `REVIEW_HASH_VERSION` v1. Reason: their parts did not change, and `civic.py` also hashes with `REVIEW_HASH_VERSION`. A global bump would have turned every summary, group and civic draft in the repo into "hand-edited" and needed three more migrations. Only reviewed clauses lose their badge (the real data has one: Finance Act c2).
- **The hazard, measured:** after the bump, an untouched AI draft with the old stamp reads as hand-edited, so `generate` would skip it for good. On the real bills that is 3 + 21 + 68 = 92 drafts (read-only count on a scratch copy; plus 51 genuine `"hand-edited"` Finance Act clauses that must stay as they are).
- **`politricks restamp <id> [--dry-run]`** re-stamps exactly the drafts whose stamp equals their v1 fingerprint, under the bill lock. It leaves `"hand-edited"` and mismatched stamps alone and never touches a reviewed clause. A second run writes nothing.
- **`politricks generate <id> --articles-only [--dry-run] [--batch-id X]`** asks for articles only, for every clause with an explanation (protected ones too, per the learner, 2026-10-23), validates each number against `src/data/constitution.json`, caps at 4, and stores them ascending. It refuses to start while any draft still has a v1 stamp. Suggestion rules are in the request, not in `explainer.md`, which the live Explain API also loads and which has no article list to choose from.
- **Reviewer view:** `politricks review` now shows `SUGGESTED ARTICLES:` beside `GROUPS:`, since accepting accepts them.
- **Not done:** the Excel review sheet (`export` / `--from-sheet`) has no articles column, so an Accept row there accepts articles its reader did not see. Add a column before using the sheet on a bill that has articles.
- **Run order for the lead (after review of this diff):** `restamp` each bill, then `generate <id> --articles-only --dry-run`, then the real pass on Public Participation first as the probe, then the other two, then `lint` and `review --flagged` for the changed reviewed clause.

### Slice 6 ticked: review walks the civic blocks, 2026-10-23

- **`politricks review` walks civic blocks** (`review.py`): one item per block, named by its kind (`participation`, `stage_next`, `challenge`). A block's texts are numbered units, and an edit asks which one. Accept stores `civic_sha256` and writes the block back into the bill. An edit makes it a hand-edited draft. `lint` flags a reviewed block whose content changed, so `--flagged` finds it. Self-test scenario M: 7 checks, review 54 → 61. Planted bug (no write-back after accept): 3 FAILs.
- **The site's civic badges are now real:** `civicSha256` in `src/lib/reviewHash.ts` is the twin of `civic.civic_sha256`, and the shared vectors carry one case per kind (8 in all). A planted twin bug (no `checked_at`) failed `check-build`. A block the learner accepts shows *Reviewed* while it still matches.
- **Ticked on the mechanical verify**, as slice 4 was. The learner's two parts wait for the integrated checkpoint: check every civic fact against its source, then `politricks review <bill> --only participation stage_next` (and `challenge` for the Finance Act) to accept or edit the translations.

### Slice 11 ticked: Nairobi County bill and motion, 2026-10-23

- **Data:**
  - Merged from the county builder (`01c5211`): the Nairobi City County Appropriation Bill, 2026 (3 clauses; the money is in two schedules that are not clauses, as with the Finance Act), and the motion "Restoration of Public Infrastructure Damaged by Public and Private Construction Works". The motion was cut verbatim from the fingerprinted Hansard of 3 July 2025, pages 9-10, into one clause.
  - Generated as drafts: `b9aa5b3`, bill 3/3 plus summary, motion 1/1 plus summary.
- **Site:** the Motion Card (the summary as its note, then the resolution beside its explanation, no tabs), and *County* and *Motion* tags on home.
- **Verify:**
  - Build clean; check-build passes 13 for the bill and 9 for the motion.
  - The home page holds both titles, with 2 *County* tags and 1 *Motion* tag.
  - The motion JSON has 1 clause and `participation: null`.
  - `fetch --check-url https://nairobiassembly.go.ke.evil.com/x.pdf` printed `refused`.
- **For the learner:**
  - (a) **The motion's stage.** The Assembly's tracker says "Approved", but the fingerprinted Hansard of 3 July 2025 (p. 12) records "(Motion deferred)", and no later vote was found. The stage line says both, and `status` is `passed` per the tracker. Change the wording in `sources.yaml` if you prefer.
  - (b) **The county bill's `status: passed`** comes from the Assembly's bills table (passed and assented July 2026), though the PDF is headed "Bill for Introduction".
  - (c) **The motion's Sheng** opens "Section 1 ina-urge", the prompt's clause rule applied to a motion. Edit it at review.
  - Learner check at the integrated checkpoint.

### Slice 10 ticked: suggested constitution articles, 2026-10-23

- **Migration:** `politricks restamp` on the three bills re-stamped 92 untouched AI drafts under clause fingerprint v2 (Finance Act 3, Public Participation 21, Tertiary 68). A field-by-field diff showed only `review.sha256` moved, and a second run wrote nothing (the restamp commit).
- **`generate --articles-only`**, learner-approved spend:
  - Public Participation was the probe: 21/21, suggesting e.g. Articles 10, 118, 196, 35.
  - Then Tertiary (68/68, 62 clauses got articles) and the Finance Act (55/55, 42 clauses got articles). No article number was dropped as unknown, and there are at most 4 per clause.
  - The Tertiary and Finance Act batches cost about $0.20 together [MEDIUM: batch at 50%, assumed cache rates]. Output was light (2,000 to 3,000 tokens a batch), far under the $1 to $2 estimate.
  - Protection held: the Finance Act's 51 hand-edited clauses kept their stamps and got articles. Only untouched AI drafts were re-stamped. Reviewed c2 kept its old stamp, so it reads *Changed since review* until the learner re-accepts it (`review finance-act-2026 --flagged`).
- **Verify:**
  - `constitution.json` holds 264 articles and a fingerprint, and every suggested number exists in it (the pass validates each one).
  - Each built page ships exactly its own suggested articles: Finance Act 14, Tertiary 43, Public Participation 15. On each page an unsuggested article's text is absent, and the control (a suggested article's text) is present.
  - `lint`: no new findings.
  - Build clean; check-build passes on all 5 bills.
  - In the browser, the Tertiary page's "Article 43" chip opens the sheet ("Article 43: Economic and social rights", labelled "Suggested article: you judge") and Close shuts it.
- **Learner check pending** (integrated checkpoint): open a suggested article. Does it feel relevant, and is it clear you're the judge?

### Motion stage grounded, and the learner's copy corrections, 2026-10-23

- **The motion is not approved: corrected to `status: active`.** A search of all 414 PDFs on the Assembly's Hansard, Votes and Proceedings and Order Paper listings for 2025-2026 (every one with a text layer) found the motion never put to a vote:
  - The Votes and Proceedings of 3 Jul 2025, p. 2, read "Under Standing Order 104, requested to postpone to a future date the debate on this Motion. Motion Deferred."
  - It was listed again for 25 Sep 2025, 2 Oct 2025 and 6 Aug 2026; each sitting lapsed for lack of quorum before reaching it.
  - The Order Paper of 15 Oct 2026, p. 3, lists it under "Tentative business for Tuesday, 3rd November, 2026".
  - The lead re-downloaded and read both decisive documents. The tracker's "Approved" is contradicted by the Assembly's own records.
  - New stage: "Moved and debated on 3 July 2025, then deferred; not yet put to a vote. Listed as tentative business for Tuesday 3 November 2026 (Order Paper, 15 October 2026)". `fetch` copied it into the JSON with the PDF unchanged (same SHA-256). The motion now sits under Active Bills.
- **Copy, the learner's corrections (AskUserQuestion, played back and confirmed):**
  - Groups heading: SW "Wanaohusika", Sheng "Inawahusu nani".
  - Welcome notice, Sheng: "Zingine zitabaki kwa hii simu tu".
  - Article sheet, Sheng: "Tuna-suggest article: wewe ndio judge".
  - Tabs, Sheng: "Inafanya nini", "Sema view yako", "Inaweza ku-challengiwa wapi".
  - Employee chip: SW "Mfanyikazi", Sheng "Msee wa Mboka". Trader chip, Sheng: "Hustler / Sonko / Boss".
  - Closed panel, SW: "Ushiriki haupo wazi kwa sasa". Match failed, Sheng: "Hatukuweza ku-personalise saa hii".
- **Glossary:** "inconsistent with the Constitution" is now SW `haiendani na Katiba` (or `haipelekani`) and Sheng `hai-skizani na constitution` (or `hai-mesh`), in `translator.md`. The Finance Act's challenge block was re-translated under it (`civic --force`, one direct call) and now uses both terms. The civic self-test checks the new wording.
- **`explainer.md`:** a county motion's Sheng now opens `Hii motion ina-...`. The existing motion draft still says "Section 1 ina-urge", for the learner to fix at review.

### Slice 8 built, waiting on Tesseract, 2026-10-23

- **Merged from the slice 8 builder (`8840fba`):**
  - `politricks ocr` (scan at 300 dpi, Tesseract, margin split, OCR text committed with the PDF's SHA-256).
  - `politricks compare` (order-preserving alignment; BORDERLINE noise threshold; one direct call for trilingual notes as drafts).
  - Typed `changes[]` (Pydantic and Zod), and a `changeSha256` twin with 3 new shared vectors (11 in all, unchanged on regenerate after the merge).
  - `ChangeRow` and the What changed tab, where every OCR "after" is labelled "Read by OCR from a scanned official copy: check the PDF" and linked to the PDF page.
- **Tests:**
  - ocr 42 and compare 66 self-test checks.
  - Planted bugs, each alone: `NOISE_CHARS` 2 → 0 gave 14 FAILs; a changed OCR label failed check-build.
  - Every other self-test passes, and check-build passes on all 5 bills.
  - A live `ocr` exits 1 with the install message and writes nothing.
- **Not run, and not ticked:** Tesseract isn't installed, so no real OCR text exists yet, and the gutter window and page handling are guesses from pages 1-4. Once Tesseract is in, the run order is: `ocr public-participation-bill-2025` → read the audit file → `compare --dry-run` → `compare` (one direct call) → lint, build, check-build → commit.
- **For the learner:** the What changed row labels ("Imechange", "Ime-drop", "Kifungu kipya", the OCR label in SW/Sheng) need the Sheng ear. Change notes are walked by `review` as `changes[0]`... (added the same day; scenario N, review 61 → 66).

### Slices 3 and 5 ticked: the learner's reviews, 2026-10-25
- **Learner's review (Ian, in `politricks review`):**
  - Slice 5: fixed the 6 banned texts (PP c5 sw "unapaswa" → "upewe", PP c13 en "make sure" → "ensure", Tertiary c9 and c31 en and sheng "vote" → "voting rights") and accepted them.
  - Slice 5: re-accepted Finance Act c2, which had changed since review only because the articles pass added article 210 ("Imposition of tax").
  - Slice 3 learner check: reviewed the Sheng of 4 demo clauses (Finance Act c2, c7, c12 and c20), with one Sheng fix in c7 ("inakua-deducted").
- **Checks before commit:**
  - A word-level diff of every edited text against `HEAD` caught two doubled words that Ian's edits left (PP c5 sw "umma umma", Tertiary c9 sheng "lakini lakini"). They were already accepted, so Ian fixed and re-accepted them through `review --only`. A doubled-word scan of the bills afterwards finds only valid reduplication ("ile ile", "moja moja") and scanned legal-text noise.
  - The field-by-field diff of all 5 bills shows 29 changed fields, all in the 9 clauses Ian accepted (texts he edited, plus review stamps). The two county bills are unchanged.
  - lint shows 0 findings over 507 texts; every self-test passes; check-api 19; `npm run build` passes; check-build passes on all 5 bills.
  - The built pages show *Reviewed* on all 9 accepted cards, and *AI draft* on an untouched control card in each of the 3 bills (Finance c3, PP c6, Tertiary c10).
- **Cut-off:** the Sun 25 Oct 20:00 EAT cut-off passed during these reviews. Slices 7, 8 and 12 are still open; whether to finish or cut each one is the learner's call, to be recorded here.

### Slice 8 cut; slice 7 key and domain in place, 2026-10-25
- **Slice 8 cut (learner's call, after the Sun 25 Oct 20:00 EAT cut-off):** Tesseract wasn't installed in time, so `ocr` and `compare` never ran on real data. The code (`ocr.py`, `compare.py`, typed `changes[]`, `ChangeRow`, the What changed tab, review of change notes) stays merged and tested by its self-tests in CI. Every bill's `changes[]` is empty, so the tab shows its empty-state note. Resuming later needs only the run order in the slice 8 entry above.
- **Slice 7, learner's steps done:** `ANTHROPIC_API_KEY` added in Vercel (Production, sensitive), and the production domain set to `https://poli-tricks.vercel.app`.
- **Production check before the redeploy (deployment `80f338a`):** home 200 with all three bill titles; `/bills/finance-act-2026` 200 with the Finance Act PDF's full SHA-256; `POST /api/match` with empty words 400 (control). A real Match returned **502 upstream**, because the key was added about 100 s after that deployment was built and Vercel gives env vars only to new deployments. Rechecked on the next deployment (below or in the next entry).

### Slice 7: production helpers working, 2026-10-26
- **Match answers 200 in production** on `https://poli-tricks.vercel.app` (deployment `dpl_5PRi...`): the Finance Act with "I sell mitumba Gikomba" returned 8 clause ids in 3.4 s; the Tertiary bill with "I am a university student with a HELB loan" returned 8 in 2.5 s. Controls: empty words 400, Explain on an explained clause 409. Home 200 with all three titles; the Finance Act page carries the PDF's full SHA-256; 0 `sk-ant-` in the served HTML.
- **What it took:** six key entries in Vercel, each rejected with 401 (alternating "invalid x-api-key" and "Invalid bearer token"), while the `.env` key got 200 from `api.anthropic.com` directly. The handlers caught model errors without logging, so the first failures left an empty runtime log.
  - `99af580` makes every 502 from a model call log one line (helper, error name, HTTP status, the API's message; never the key). Kept: it is how a production failure gets diagnosed.
  - `ae1d9fe` logged the stored key's shape (length, prefix, last 4, whitespace) to the private log. It showed what Vercel actually held: 68 characters of other text with spaces, then the key with its 7 dashes stripped and the last 2 characters cut (97), then the key with only the last 2 cut (104), then the right value (106). Removed once Match answered 200.
- **Not ticked yet:** the learner check (the public link on the learner's phone, over mobile data: welcome, Finance Act, a Sheng clause, Have your say).

### Checkpoints and the final review plan, 2026-10-26
- **Early checkpoint: done, ticked from the record.** The learner opened the kernel page on 2026-10-13 and 2026-10-14 and gave the feedback that shaped every later page: the em dashes in visible copy ("Em dashes and Sheng feedback, 2026-10-13") and the Sheng rules now standing in `explainer.md` ("First review sitting, paused, 2026-10-14").
- **Integrated checkpoint: not held as its own session.** Its journey (welcome, home, bill, What it does, Have your say) is the same as slice 7's learner check on the phone, so it is folded into one hands-on session with the final kick-the-tires instead of being held separately or ticked unperformed (5-build, "When the Checklist Is Complete"). Its box stays unticked until that session.
- **Final review plan (5-build steps 1-5):**
  1. Verify (agent): every self-test, check-api, lint, build, check-build on all 5 bills, plus the production checks on `poli-tricks.vercel.app`. Deviations recorded here: slice 8 cut; Kenya Law still 403 (verify reports UNREACHABLE); the Public Participation Bill's approved text not processed; Explain cannot fire on live data because every clause has an explanation; civic blocks still drafts (Ian's optional review).
  2. Kick the tires (learner): the public link on the learner's phone over mobile data, then free exploration (script in the session).
  3. Interview: open questions on anything broken, confusing or worth changing.
  4. Agree and revise: each agreed fix becomes an unchecked item under Final Review, is built, verified, committed, and retried by the learner.
  5. Finish: the box is ticked only when the learner says the PoC is ready.
- **Then:** the Code Tour and App Map (`devpost/app-map.html`), then `6-ship`.
- **Step 1, verify: done.** CI on `d6bdc00` (runs 40242659766 on `main`, 40242656746 on `build/poc`): `test` green (all 13 self-tests, check-api 19, `npm run build`, check-build on all 5 bills) and `content` green (lint 0 findings). Production on `poli-tricks.vercel.app`: home 200 with all three titles; Match 200 on the Finance Act, Tertiary and Public Participation bills (2.5-3.4 s); empty words 400; Explain on an explained clause 409; the Finance Act PDF's SHA-256 on its page; 0 `sk-ant-` in served HTML.

### Final review revisions FR1-FR6 built, 2026-10-26
- **Learner's kick-the-tires feedback** (phone): a sticky or quick language switch; the presentation reads as static for non-techies (cleaner fonts, a bill page that shows everything at once, light motion); say plainly that this is a hackathon demo; Active Bills on mobile highlights closed bills. Interviewed with two rounds of questions; agreed as FR1-FR6 under Final Review.
- **Built:** FR1-FR4 by the lead (`0706a41` plus the sweep below); FR5-FR6 by a Sonnet builder in a worktree (`8e8efa0`), diff reviewed and merged (`e794150`).
  - FR3 sweep: the 14 headings that paired the display font with ALL CAPS are now bold normal case; `--font-display` is the phone's sans and `--font-wordmark` (Anton) is used by the wordmark only.
  - Found during the merge: the sticky header would cover a card reached by `#clause-...`, so `.clause-card` has `scroll-margin-top`; the builder found the source URL in `SourceFingerprint` made the page 428px wide on a 375px phone, now `overflow-wrap: anywhere`.
  - Copy: the learner's wording for the banner, badge and footer; SW "Oyesho" read as "Onyesho" (flagged to the learner); the EN banner has no "check official sources" sentence while SW and Sheng do (flagged). New strings for the learner's Sheng check: "In short" / "Kwa ufupi", "Show/Hide the legal text" / "Onyesha/Ficha maandishi ya sheria" / "Cheki/Ficha legal text", "Matches you" / "Inakuhusu".
- **Verified:** `npm run build` 0 errors; check-build on all 5 bills exits 0 with 123/15/11/53/147 PASS lines (equal to the builder's); check-api 19. At 375px in the browser: scrollWidth 375 (was 428), header still at top after scrolling 3000px, headings 700 weight with no text-transform, body 17px, all 55 cards closed on load; renderer live (33 rAF ticks in 500 ms). The builder's headless-Chrome run covered Match opening the top 3, Explain firing only on open, and reduced motion.
- **Not ticked:** each FR item waits for the learner's retry on the phone.
- **Learner's retry, 2026-10-26:** "All six good." FR1-FR6 ticked. Shipping work (6-ship) is slotted from 20:00 EAT tonight, before the 00:00 EAT deadline (learner's call).
- **App map generated, 2026-10-26:** `devpost/app-map.html` from commit `9f88167`: overview, the Match action traced through `[id].astro`, `api.ts` `handleMatch` and `claude.ts` `callMatch` (a reference route, not yet walked with the learner), "start here" pointers, and the scoping practice (the cut-off and kernel that made the slice 8 cut one line). All 26 paths and search anchors checked against the code (a fake anchor as the negative control was reported missing). Opened offline in the browser pane. The learning activity itself and its boxes wait for the learner.
- **Learner, 2026-10-26 morning:** not ready yet. The type reads "really flat"; it must be readable and presentable, because the point is to spark curiosity. *Have your say* works. Agreed as FR7: Bricolage Grotesque + Inter, more colour and polish.
- **FR7 built, 2026-10-26:** by a Sonnet builder in a worktree (`42f051b`), diff reviewed and merged. Bricolage Grotesque (headings, 41,344 bytes) and Inter (body, 48,256 bytes), variable Latin subsets from `@fontsource-variable` 5.3.0, self-hosted in `public/fonts/` with their OFL files and preloaded; no package added (package.json and lock unchanged). Flag-red accent under section headings and on the active tab, a green-tint "In short" pull-quote, softer cards with shadows and focus states, red section numbers, pill badges; badge, summary and section-heading styles now live once in global.css. Contrast measured in Chrome: every new text and background pair is at least 5.75:1 (AA needs 4.5). Verified after the merge: build 0 errors, check-build 123/15/11/53/147 PASS with 0 FAIL, check-api 19; the builder measured scrollWidth 375 on every page at 375px and all three fonts loaded. Not done: "Changed since review" still shares the AI draft badge colour (needs a `badge.ts` change). Waits for the learner's retry.

- **Learner, 2026-10-26 (late morning):** GitHub configured (secret `ANTHROPIC_API_KEY` set 08:12 UTC; "Allow GitHub Actions to create and approve pull requests" on, read back via the API). FR7 "looks better", ticked; the header wordmark was missed, so FR8 moves it to Bricolage Grotesque and retires Anton (its file, licence, @font-face and preload removed; 18,612 bytes less on every first load). Learner: "Ready if you are. Shipping on schedule."
- **New feature, for after the hackathon (learner: "not for demo"):** the language switch should switch EVERY piece of text on the site, not just the explanations, tabs and notices. Added to `scope.md` > Later.
- **Learning wrap-up done** (see Code Tour and App Map).

### Slice 12: weekly workflow verified live, 2026-10-26
- **Settings (learner):** repo secret `ANTHROPIC_API_KEY` (08:12 UTC) and "Allow GitHub Actions to create and approve pull requests" on; both read back via the API.
- **First run on `main` (run 40291557483) went red** at the final gate: `intake --check exited 1`, because the Kenya Gazette index answered GitHub's runner with HTTP 200 and no PDF links (PARSE FAILED) while the three Parliament and Nairobi listings read fine. Fixed in `052fcc5`: the Gazette listing is `optional`, so its failure is printed with a NOT CHECKED line and copied to the top of the PR body, but the exit code stays 0; required listings keep the strict rule (intake self-test 54, the new check fails when the Gazette is made required again).
- **Re-run on `main` (run 40292417440): green**, 3 of 4 listings read (the Gazette reported, optional), 5 sources unchanged, `pull-request-operation = none`: no PR when nothing changed.
- **Run on the throwaway branch `test/slice-12-pr-check` (run 40292941195)**, where the county Appropriation Bill's stored source SHA-256 was set to 64 zeros: green, verify reported `CHANGED (expected 000000000000..., got 16871b4e7adb...)`, and the workflow opened PR #9 "Weekly update: needs review", labelled *needs review*, into the throwaway branch (never `main`).
- **Waiting for:** the learner's check (open PR #9: is it clear what changed and what to review?); then PR #9 is closed and both test branches deleted, and slice 12 ticked.

