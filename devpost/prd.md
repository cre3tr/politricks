---
doc: prd
status: approved
---

# PoliTricks — Product Requirements

A mobile-first guide to Kenyan bills for an ordinary citizen. It shows what a bill does, who it affects, what changed, and how to have your say, in English, Kiswahili and Sheng, always beside the source text.
Source: `scope.md > The Unique Kernel`, `scope.md > Who It's For`.

## The Core Journey
Develops `scope.md > The Core Loop` and `scope.md > What "Working" Looks Like`.

1. **First visit.** The citizen opens PoliTricks and is asked once, "who are you?". They tap any of the "I am…" groups and can add a few words of their own. They can skip this. Their answer is remembered on this device.
2. **Home.** Two ways in: **Active Bills** (everything currently before Parliament, whether or not participation is open) and **Una'tafuta?** (search recently passed bills).
3. **Pick a bill.** They tap an active bill and land on its page, with three tabs: **What it does / What changed / Have your say**.
4. **What it does.** Their groups come first, showing what this bill does to people like them. Below that are a short whole-bill summary and **clause cards**, each with a plain-language note beside the legal text. They switch the whole page between EN / SW / Sheng, or flip a single card.
5. **Adjust.** On this bill they can change their "I am…" chips, or expand to see every group.
6. **What changed.** They see what changed between published drafts, or the bill compared with the current law it amends.
7. **Have your say.** They see the deadline, where to submit, and what a submission needs, each linked to its official notice. If participation isn't open, they see the bill's current stage and what usually happens next.
8. **Loop closes.** They know what the bill does to them and how to respond before the deadline. They come back when the next bill or the next draft drops.

**Success:** a citizen who has never read a bill can say what it does to people like them, in their own language, and where and by when to respond, in under a minute.

## Screens and Layout
Mobile-first; desktop is the same layout, wider.

- **First-visit "who are you?"** A single screen with the group chips, a short free-text field, and a Skip option.
- **Home.** The **Active Bills** list and the **Una'tafuta?** search. The global language toggle sits in the header on every screen.
- **Bill page.** Title, stage and a status label (Active / **Passed**), then three tabs:
  - **What it does:** your groups first (chips editable, "see everyone"), then the summary, then clause cards.
  - **What changed:** a side-by-side comparison of changed clauses.
  - **Have your say** (active bill) / **Where it can still be challenged** (passed bill).

## Look and Feel
- **Style:** a **civic-tech clean** base (calm, very readable on a cheap phone) with **street / mtaa energy** layered on top: bold moments, poster-like headers, matatu-art attitude.
- **Palette:** Kenyan flag colours (black, red, green, white) as the base and accents. **Avoid party-coded colours**, for neutrality; orange and yellow must never be dominant.
- **Signature element:** **highlighter marks** over key legal phrases, inspired by matatu/matwana art, in **brown with red** and **brown with black** pairings.
- **Mobile-first:** designed for small screens and cheap data before desktop.

## Features and Behavior

### First-Visit Profile
Develops `scope.md > Who It's For`.
- As a citizen, I want to say once who I am so that every bill shows me what affects people like me first.
  - [ ] The first visit shows the "who are you?" screen with the groups **Employee / salaried**, **Small trader / hustler**, **Boda / matatu / driver** and **Student / youth**. More than one can be picked.
  - [ ] A short free-text field accepts the person's own words (e.g. "I sell mitumba Gikomba"). Input is length-limited and sanitised. The app uses their words, not a rewritten label.
  - [ ] **Their own words drive matching:** clauses that match what they wrote are floated to the top alongside their chosen groups.
  - [ ] Skip works, and a skipped profile shows every group on bill pages.
  - [ ] On a return visit the screen doesn't appear, and the saved profile is applied.

### Home: Active Bills and Una'tafuta?
*Active Bills: build step 1. Una'tafuta? and passed-bill pages: build step 4.*
- [ ] **Active Bills** lists every tracked bill currently before Parliament, whether or not participation is open. Each shows its title, its stage, and a participation deadline if one is open.
- [ ] **Una'tafuta?** searches recently passed bills by name or keyword. Passed bills open with a **Passed** label.

### What It Does
Develops `scope.md > The POC Boundary` step 1 and the kernel.
- As a citizen, I want to understand a bill in my language so that I can judge it myself.
  - [ ] Content for the reader's groups appears first. Each group entry links to the clauses behind it.
  - [ ] "See everyone" shows all groups. The "I am…" chips on this page change which groups lead, for this bill.
  - [ ] A short whole-bill summary sits above the clause cards.
  - [ ] **Every clause card shows the original legal text beside its plain-language note.** No explanation ever appears without its source clause.
  - [ ] The explanation is neutral: it says what the clause does and doesn't tell the reader what to think of it.

### Language Switching
Develops `scope.md > Inspiration & Identity`.
- [ ] The global toggle (EN / SW / Sheng) switches every explanation on the page at once and is remembered next visit.
- [ ] Any single card can be flipped to another language without changing the rest.
- [ ] Legal source text stays in its original language; only the explanations change.

### What Changed
Develops `scope.md > The POC Boundary` step 3.
- [ ] With two published versions: changed clauses appear side by side (before → after), each with a plain-language note in the chosen language.
- [ ] With only one version: an honest note ("Only one version so far — we'll show changes when the next draft is published"), **plus** a comparison of the bill's clauses against the **current law** they amend.
- [ ] For a passed bill: the comparison is **bill as introduced vs Act as passed**, showing what survived and what was dropped.

### Have Your Say
Develops `scope.md > The POC Boundary` step 2.
- [ ] Participation open: shows the deadline, where to submit (email, portal or venue) and what a submission needs, **each linked to the official notice it came from**.
- [ ] Every deadline and stage shows a **"last checked"** date, so a stale date is visible as stale.
- [ ] Participation not open: shows "Participation not open", the bill's current stage, and what usually happens next, sourced.
- [ ] Passed bill: the tab becomes **Where it can still be challenged**, listing the avenues after passage (e.g. petitions, courts). It is general information, sourced, and never legal advice.

### Constitution Check
Develops `scope.md > The POC Boundary` step 5.
- [ ] Each clause card can list **generated** article suggestions from the Constitution of Kenya 2010, as tappable references, **always labelled "Suggested articles"**.
- [ ] Tapping one opens that article's text **in the app** (no leaving to a heavy external site), with a link to the official source.
- [ ] The in-app constitution text is **verifiably the official text**: an integrity check (the learner's "hashed copy") proves it hasn't been altered. *How* it's checked is for `4-spec`.
- [ ] The app never states or implies that a clause is unconstitutional. The reader judges.

### Automatic Bill Intake
Develops `scope.md > The POC Boundary` steps 4 and 6.
- [ ] New national bills and Gazette notices appear in Active Bills without manual entry.
- [ ] Nairobi County Assembly items appear too, labelled as county.
  - **County bills** use the same bill page and tabs as national bills.
  - **County motions** use a simpler card: the resolution text plus a trilingual note. No clause cards, no What changed tab and no Have your say tab, because a motion has none of those.

## States and Boundaries
- **First use:** the "who are you?" screen, which can be skipped.
- **No groups match a clause:** the clause still appears under "see everyone". Nothing is hidden from a reader because of their profile.
- **Nonsense or abusive free text:** it's sanitised and length-limited. If nothing usable remains, the app treats it as skipped. *(Assumption: the exact limit and sanitisation rules are for `4-spec`.)*
- **Participation not open / one version only / passed bill:** as specified above.
- **Search finds nothing:** "Hatujapata kitu" (nothing found), in the reader's language, with a way back to Active Bills. *(Assumption: wording to be confirmed.)*
- **Explanation unavailable for a clause:** the clause text still shows, with a note that the explanation isn't ready. *(Assumption.)*
- **Persists on this device:** profile (groups + free text) and language choice. No accounts.

## Product Decisions
- **Home has two ways in, Active Bills and Una'tafuta?**: active bills are for acting, and search covers what already passed.
- **Group by who it affects, then summary, then clauses**: the citizen's first question is "what does this do to me?"
- **Ask once globally, adjust per bill**: set up once, but one bill might matter to you differently.
- **Free-text "I am…" in the person's own words, sanitised**: lets the fixed groups miss nobody.
- **Tabs for What it does / What changed / Have your say**.
- **Global + per-card language**: one default for the page, with room to compare languages on a single clause.
- **Passed bills compare the bill with the Act and show where it can still be challenged**: participation is over, but accountability isn't.
- **One-version bills compare against current law** (in build step 3): amendment clauses are meaningless without the Act they change.
- **Look:** civic-tech clean + street energy, flag palette, no party colours, matatu brown/red and brown/black highlighter.
- **Demo bills are Finance Bills**: they're what citizens feel directly, and as pure amendment bills they're where the current-law comparison matters most.
- **Constitution articles are an in-app hyperlinked section backed by a hashed copy**: the reader stays in the app on cheap data, and the hash proves the text is the official one. Source beside summary applies to the constitution too.
- **The free-text "I am…" drives matching**: their own words decide what floats up, not just a label.
- **Constitution articles are generated and labelled "Suggested articles"**, with the official text beside them: it scales past hand-curation, and the label plus the source keep it honest.
- **County covers both bills and motions**: bills get the bill UI, and motions get a simpler card suited to their shape.
- **Active Bills uses a bill genuinely before Parliament now**, and Finance Bill 2026 appears as a passed bill, because Finance Bills normally pass by 30 June. To be verified in `4-spec`.
- **"Last checked" stamp on deadlines and stages**: the value of the product depends on dates being current.
- **The passed Finance Bill 2026 page is loaded by hand in step 1, from a hashed copy**: this keeps the Finance-in-Sheng moment inside the minimum demoable line without waiting for step 4's search, and the hash proves the hand-loaded text matches the official source. **Rule: every piece of source text in the app (bill, Act, constitution) is verifiably official.** *How* it's hashed and verified is for `4-spec`.
- **Assumption, not a learner decision:** passed bills show a simple **Passed** label. The learner didn't pick the "now law" option, but something must stop people trying to make a submission on a passed bill.

## What We're Building
In the approved build order (`scope.md > The POC Boundary`):
1. **Explain one bill:** first-visit profile (including free-text matching), a home screen showing at least one bill genuinely before Parliament, **plus the passed Finance Bill 2026 page loaded by hand from a hashed copy**, and the What it does tab with groups, summary, clause cards and language switching.
2. **Have your say:** the tab in both its open and not-open states. **← Minimum demoable line.**
3. **What changed:** draft vs draft, bill vs current law, and the honest empty state.
4. **Automatic bill intake:** the Gazette/Parliament scraper, **Una'tafuta?** search over passed bills, and passed-bill pages (Passed label, bill vs Act, where it can still be challenged).
5. **Constitution check:** tappable article references on clause cards, opening the in-app hashed copy.
6. **County:** Nairobi County Assembly bills (bill UI) and motions (simpler card).

## Deferred From the POC
- **Accounts / sync across devices:** "remembered" means this device only.
- **Notifications** ("a new draft dropped"): the loop implies them, but the proof of concept relies on the reader coming back on their own.

## Possible Later Enhancements
- **Fiscal flag:** before → after figures on changes to a tax, levy, rate or fee.
- **Drafted comment / memorandum** for submissions.
- **WhatsApp / SMS digest** for reach on cheap data and feature phones.
- **Production path:** wrap in the full framework once real users rely on it.

## Non-Goals
- **Constitutionality verdicts:** the project surfaces articles and never rules.
- **Advocacy:** the project explains and doesn't campaign.
- **Legal advice:** "Where it can still be challenged" is general, sourced information only.
- **Translating the law itself:** the source text stays as published.

## Open Questions
- **Verify the bill calendar (answer in `4-spec`, before build step 1).** Has Finance Bill 2026 passed? Which bill is genuinely before Parliament now, to fill Active Bills? Source the Finance Bill 2026 versions, the Finance Act 2026, and the Acts it amends. [UNKNOWN]

## Acknowledged Complexity
Found in the post-approval review. These are consequences of the choices above, not new scope.
- **Free-text matching** means matching arbitrary personal text against clause content: classification work, and a place where a match can be wrong. "See everyone" remains the guard.
- **Generated article suggestions** can cite the wrong article, in the feature whose promise is "we show you the real text". The "Suggested" label and the official text beside it are the mitigation.
- **Automatic intake** from Gazette and Parliament PDFs that are often scanned. The demo pair isn't the only place text extraction can fail.
- **Groups-first is interpretive:** deciding that a clause affects traders is a judgement, even when the explanation is neutral.
- **Readers may not check the source:** the target user avoids dense legal text, so the source beside each note should be compact and highlighted, not buried.
