---
doc: scope
status: approved
---

# PoliTricks

A citizen's guide to Kenyan bills. Pick a bill, see what it does and what changed between drafts, get it explained in English, Swahili and Sheng, and find out how and when to have your say.

## The Unique Kernel
Kenyan legislation, explained in the language people actually speak. Every explanation sits beside the original clause text, so the reader can check the AI against the source instead of taking its word for it. Other trackers publish PDFs. This one tells you what a clause does to you, and says it in Sheng.

## Who It's For
An ordinary Kenyan citizen, not a lawyer, facing a public-participation deadline on a bill that affects them. Today they skim a dense PDF on the Parliament or Gazette site, or rely on social-media hot takes. By the time they understand it, participation is over. In the learner's words, public participation is "reactive rather than proactive."

## The Core Loop
Open the app, pick a bill, and read what it does and what it means for them in the language of their choice. See what changed between drafts. See the deadline and where to submit a view. They come back when the next bill drops or the next draft of this one is published.

## Inspiration & Identity
- **Trilingual (EN / SW / Sheng) is the identity.** Sheng makes it fun, and plain language makes it useful. The name, in the learner's words: the "tricks played in politics. In plain sight but buried under access, jargon and volume." The tricks are the opacity, not the politics, so the name is consistent with the neutral stance. Chosen over "Mswada," which reads as Tanzanian Swahili-first.
- **Neutral explainer, not advocacy.** It explains what a bill does. It doesn't tell people what to think about it. That makes it trustworthy across political camps and harder to attack. "What it means for you" is worded as explanation, never verdict.
- Nothing else was volunteered on look and feel. `3-prd` will ask.

## Why This Matters to the Learner
Proposed bills, county motions and statutory instruments sit in dense, unsearchable PDFs, which makes public participation reactive. The learner chose this project out of their politics & policy interest, wants something a citizen can act on in time, and sees it as something that "could help a lot of people."

## What "Working" Looks Like
In a one-minute demo, someone picks a real national bill and immediately sees what it does and what it means for them, in English, Swahili or Sheng. They see the changes between two published versions side by side, with a plain-language note on each change. They see how and when to take part: the deadline, where to submit, and what a submission needs, each linked to its official source. **The "oh, that's cool" moment is a Finance Bill clause explained in Sheng, right next to the legal text.**

## The POC Boundary
The learner chose to keep all of the following, and says time is not a hard constraint:
1. **Explain one bill.** A real national bill (one genuinely before Parliament; Finance Bill 2026 appears as a passed bill, see `prd.md`), with what it does and what it means for you, in EN / SW / Sheng, shown beside the clause text.
2. **Public participation, how and when.** Deadline, where to submit, and what a submission needs. **Every date and channel links to the official notice it came from.**
3. **Diff two versions.** A real published pair of the same national bill, with changed clauses side by side and a trilingual note per change.
4. **Gazette scraper.** Fetch bills and notices automatically, not by hand.
5. **Constitution check.** **Show suggested articles** of the Constitution of Kenya 2010 beside a clause, generated and labelled "Suggested", with the official text. **It never rules** that a clause is unconstitutional; the reader judges.
6. **County.** Nairobi County Assembly bills (same shape as national) and motions (a simpler card, since a motion has no clauses or drafts).

**Build order is the risk control.** Items 1–2 make up the minimum demoable line: that alone proves the kernel and the core loop. Items 3–6 get built in order after that. Whatever is finished gets demoed, and nothing half-built goes into the demo.

**Recorded disagreement (agent, once):** items 4–6 go past the course's 2–4-hour target. The learner kept them, citing no hard time constraint. Not reopened.

## Quality Guardrails
- **Sheng review:** the learner reviews Sheng output personally.
- **Source-beside-summary** everywhere: clause text beside every explanation, and an official notice behind every date.

## Later
- **Fiscal flag:** mark changes that touch a tax, levy, rate or fee, with before → after figures. This is the first stretch goal.
- **Drafted comment / memorandum:** "how and when" was chosen for the proof of concept instead.
- **Production path:** once real users rely on it, it gets wrapped in the full framework (learner's own §6 rule) as a separate step from the hackathon build.

## Explicitly Cut
- **"Unconstitutional" verdicts:** replaced by surfacing articles. A model's legal verdict on a live bill is a claim the project shouldn't make.
- **Advocacy framing:** the project explains and doesn't campaign. That's a deliberate stance, not an oversight.

## Open Questions for `3-prd`
- **Which real bill pair?** It must be a national bill with two published versions, both obtainable as text-extractable PDFs. Not yet verified [UNKNOWN].
- **Reach:** a web app for the proof of concept. Whether a WhatsApp or SMS digest belongs in Later is to be discussed, given mobile data costs and feature phones.
