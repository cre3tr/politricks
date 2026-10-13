"""Ask Claude to explain every clause of a bill, using the Message Batches API.

Two halves live in this file:

1. **Pure, offline logic** — building batch requests, parsing a batch result
   line, deciding what to do with each outcome, and writing accepted results
   onto a `Bill`. None of this touches the network. It is exercised by
   `politricks generate --self-test` against a recorded fixture, which is the
   only way this file is verified in slice 2a (no API key exists yet).
2. **`run()`**, the live entry point that actually submits a batch, polls it,
   and retries refusals directly. It exists because slice 2b needs it the
   moment a key exists, but it is unverified until then — there is no network
   egress in this environment, so it has never actually been called here.

Why the Batches API, and why no `fallbacks` in the batch requests: see
`devpost/spec.md > Generate (Claude)` and the "Revisions" note in
`devpost/checklist.md`. Short version — Batches are half price and this work
isn't time-sensitive, but the Batches API rejects the server-side `fallbacks`
option, so a refused clause is retried one at a time with `client.beta.
messages.parse`, which does accept it.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Union, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from .hashing import sha256_file
from .models import (
    Bill,
    Clause,
    GroupEntry,
    GroupId,
    NeutralCheck,
    Review,
    ShengSwap,
    Summary,
    Tri,
    HAND_EDITED,
    bill_path,
    changed_since_review,
    current_sha256,
    draft_review,
    is_hand_edited,
    is_protected,
    is_reviewed,
    load_bill,
    save_bill,
)
from .selftest import FIXTURE_BILL_ID, Checker, mark_reviewed, reset_bill

# Sonnet 5.5, chosen for keeping closely to the brief: explain the clause and
# never editorialise (learner decision, 2026-10-13; see checklist Revisions).
# Its effort already defaults to "high". EFFORT is still set explicitly, so a
# future model swap can't quietly change depth (Opus 5.5 defaults to
# "medium"). Sonnet 5.5 can't switch thinking off with {"type": "disabled"},
# and it rejects a forced tool_choice. This file uses neither.
MODEL = "claude-sonnet-5-5"
EFFORT = "high"
MAX_TOKENS = 16000

# The beta needed for a direct (non-batch) refusal retry to accept
# `fallbacks: "default"`. Never sent on a batch request — the Batches API
# rejects server-side fallbacks outright.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# A Message Batch custom_id must match this. Clause ids ("c12") and the
# literal "summary" both satisfy it comfortably.
CUSTOM_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

_STRICT = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Output schemas. These are what we ASK Claude to return (via
# output_config.format.schema), and what we validate its answer against. They
# are deliberately separate from `models.Tri` / `models.NeutralCheck` in
# models.py: those describe what the SITE reads; these describe exactly what
# the MODEL must produce, with every field required and no extras allowed, so
# a malformed response fails loudly instead of silently dropping a field.
# --------------------------------------------------------------------------


class TriOut(BaseModel):
    model_config = _STRICT
    en: str
    sw: str
    sheng: str


class NeutralOut(BaseModel):
    model_config = _STRICT
    ok: bool
    note: str


class SwapOut(BaseModel):
    # A word the Sheng kept in pure Kiswahili + the everyday English for it.
    # Stored as models.ShengSwap; lint flags it until Ian reviews the item.
    model_config = _STRICT
    word: str
    english: str


class ClauseOut(BaseModel):
    model_config = _STRICT
    explanation: TriOut
    groups: list[GroupId]
    suggested_articles: list[int]
    neutral_check: NeutralOut
    sheng_swaps: list[SwapOut]


class GroupOut(BaseModel):
    # No clause_ids: which sections belong to a group comes only from the
    # per-clause tags, filled in by apply(). One source of truth, so the
    # summary can't disagree with the clause cards about who a section affects.
    model_config = _STRICT
    group: GroupId
    text: TriOut
    sheng_swaps: list[SwapOut]


class SummaryOut(BaseModel):
    model_config = _STRICT
    summary: TriOut
    summary_sheng_swaps: list[SwapOut]
    groups: list[GroupOut]


class TagsOut(BaseModel):
    # The tags-only pass: re-decide a clause's groups without paying to
    # rewrite its explanation (`generate <bill> --tags-only`).
    model_config = _STRICT
    groups: list[GroupId]


def _out_model(custom_id: str, tags_only: bool = False) -> type[BaseModel]:
    """Which schema a request's answer must match."""
    if custom_id == "summary":
        return SummaryOut
    return TagsOut if tags_only else ClauseOut


def _swaps(swaps: list[SwapOut]) -> list[ShengSwap]:
    return [ShengSwap(word=swap.word, english=swap.english) for swap in swaps]


def _all_objects_forbid_additional(schema: dict) -> bool:
    """True if `schema`, and every nested `$defs` entry, sets
    `additionalProperties: false` on every object it defines.

    Pydantic's `extra="forbid"` already guarantees this for every model above
    — this function is the assertion that it actually happened, so a Pydantic
    version change that quietly stopped emitting the flag would be caught here
    instead of by a rejected API request months later.
    """

    def object_ok(node: dict) -> bool:
        if node.get("type") == "object" or "properties" in node:
            return node.get("additionalProperties") is False
        return True

    if not object_ok(schema):
        return False
    return all(object_ok(definition) for definition in schema.get("$defs", {}).values())


def _explainer_prompt(repo_root: Path) -> str:
    return (repo_root / "pipeline" / "prompts" / "explainer.md").read_text(encoding="utf-8")


def build_params(repo_root: Path, bill: Bill, clause: Clause) -> dict:
    """The Messages API params for explaining one clause.

    No `betas` or `fallbacks` here on purpose — these params are used inside a
    batch request, and the Batches API rejects the server-side fallback
    option. A refused clause gets retried through `direct_params()` instead.
    """
    schema = ClauseOut.model_json_schema()
    assert _all_objects_forbid_additional(schema), "ClauseOut schema must forbid extra properties everywhere"
    user_text = (
        f"Bill: {bill.title}\n"
        f"Section {clause.number}: {clause.heading}\n\n"
        f"<clause>\n{clause.text}\n</clause>"
    )
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [
            {
                "type": "text",
                "text": _explainer_prompt(repo_root),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": EFFORT},
    }


def tagged_sections(bill: Bill) -> dict[str, list[str]]:
    """Each group's clause ids, in bill order, from the per-clause tags."""
    return {group: [clause.id for clause in bill.clauses if group in clause.groups] for group in get_args(GroupId)}


def build_summary_params(repo_root: Path, bill: Bill) -> dict:
    """The Messages API params for the bill's overall summary + group blurbs.

    Built AFTER the clause results are applied, because it hands the model
    the per-clause group tags: the summary writes a note for each tagged
    group, and never decides group membership itself.

    Sends every section's full text, not just its heading: a summary and
    group notes written from headings alone would be guesses. The Finance Act
    adds about 14k input tokens to this one request.
    """
    schema = SummaryOut.model_json_schema()
    assert _all_objects_forbid_additional(schema), "SummaryOut schema must forbid extra properties everywhere"
    tags = tagged_sections(bill)
    tag_lines = "\n".join(
        f"- {group}: {', '.join(ids) if ids else 'no sections (write no entry for this group)'}"
        for group, ids in tags.items()
    )
    sections = "\n\n".join(
        f'<section id="{clause.id}">\n{clause.number}. {clause.heading}\n{clause.text}\n</section>'
        for clause in bill.clauses
    )
    user_text = (
        f"Bill: {bill.title}\n\nThis is the bill summary request.\n\n"
        f"Sections already tagged for each group:\n{tag_lines}\n\n"
        f"Every section of the bill:\n\n{sections}"
    )
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [
            {
                "type": "text",
                "text": _explainer_prompt(repo_root),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": EFFORT},
    }


def build_tag_params(repo_root: Path, bill: Bill, clause: Clause) -> dict:
    """The params for re-deciding one clause's groups, and nothing else.

    Same system prompt as the explainer, so the batch reuses its cache; the
    user message says this is a tags-only request, and the schema allows only
    `groups`.
    """
    params = build_params(repo_root, bill, clause)
    schema = TagsOut.model_json_schema()
    assert _all_objects_forbid_additional(schema), "TagsOut schema must forbid extra properties everywhere"
    params["messages"] = [
        {
            "role": "user",
            "content": (
                "This is a tags-only request: return only `groups` for this section, under the rules in "
                '"Who a section affects". Don\'t explain it.\n\n' + params["messages"][0]["content"]
            ),
        }
    ]
    params["output_config"] = {"format": {"type": "json_schema", "schema": schema}, "effort": EFFORT}
    return params


def build_tag_batch(repo_root: Path, bill: Bill) -> list[dict]:
    """One tags-only request per clause a human hasn't reviewed or edited."""
    return [
        {"custom_id": clause.id, "params": build_tag_params(repo_root, bill, clause)}
        for clause in bill.clauses
        if not is_protected(clause)
    ]


def build_batch(repo_root: Path, bill: Bill) -> list[dict]:
    """One batch request per clause. The summary is NOT in the batch: it
    needs the clause tags this batch produces, so run() asks for it after.

    Clauses a human reviewed or edited are left out: `apply` would keep the
    human's version anyway, so asking for them only costs money.
    """
    return [
        {"custom_id": clause.id, "params": build_params(repo_root, bill, clause)}
        for clause in bill.clauses
        if not is_protected(clause)
    ]


def direct_params(params: dict) -> dict:
    """`params` plus the server-side fallback, for a DIRECT retry of a refusal.

    Checked against the installed SDK (anthropic 1.8.0): `client.beta.messages
    .parse` accepts `betas` and `fallbacks` as ordinary keyword arguments —
    there was no need to smuggle them through `extra_body`. This function
    still returns a plain params dict (not SDK objects), so the caller decides
    how to pass it (`client.beta.messages.parse(**direct_params(params))`).
    """
    return {**params, "betas": [FALLBACK_BETA], "fallbacks": "default"}


def retry_params(params: dict) -> dict:
    """`direct_params` with the pre-built schema removed, ready for `.parse`.

    `client.beta.messages.parse` builds its own schema from `output_format`
    and raises if `output_config.format` is ALSO set, so only `format` goes.
    The rest of `output_config` (the effort level) stays: the SDK merges it
    with the schema it builds (anthropic 1.8.0, `_merge_output_configs`).
    Dropping the whole `output_config` would quietly retry at the model's
    default effort.
    """
    params = direct_params(params)
    output_config = {k: v for k, v in params.get("output_config", {}).items() if k != "format"}
    if output_config:
        params["output_config"] = output_config
    else:
        params.pop("output_config", None)
    return params


# --------------------------------------------------------------------------
# Outcomes. Every batch result line becomes exactly one of these.
# --------------------------------------------------------------------------


@dataclass
class Applied:
    """The model answered, and its JSON validated against our schema."""

    custom_id: str
    parsed: Union[ClauseOut, SummaryOut]


@dataclass
class NeedsDirect:
    """The model refused inside the batch. Retry it directly with fallbacks on."""

    custom_id: str


@dataclass
class Errored:
    """The batch line itself failed: errored / expired / canceled / max_tokens."""

    custom_id: str
    reason: str


@dataclass
class Invalid:
    """The model answered, but the text wasn't valid JSON for our schema."""

    custom_id: str
    reason: str


Outcome = Union[Applied, NeedsDirect, Errored, Invalid]


def parse_batch_line(obj: dict, tags_only: bool = False) -> Outcome:
    """Turn one decoded line of a batch results `.jsonl` file into an Outcome.

    `tags_only` says the line answers a tags-only request (`TagsOut`).

    `obj` is a plain dict (e.g. from `json.loads` on one line, or from
    `response.model_dump(mode="json")` for a line the SDK already parsed for
    us in `run()`) — always validated fresh against the SDK's own model here,
    never trusted as already the right shape.
    """
    # Imported lazily so `--self-test` never needs network credentials to
    # import this module — the SDK type itself has no such requirement, but
    # keeping the import beside its only use makes that guarantee visible.
    from anthropic.types.messages.message_batch_individual_response import (
        MessageBatchIndividualResponse,
    )

    response = MessageBatchIndividualResponse.model_validate(obj)
    custom_id = response.custom_id
    result = response.result

    if result.type == "errored":
        # Keep the API's own error type and message: "errored" alone can't
        # tell an overloaded server from a request we built wrong.
        error = result.error.error
        return Errored(custom_id, f"errored: {error.type}: {error.message}")
    if result.type in ("expired", "canceled"):
        return Errored(custom_id, result.type)

    # result.type == "succeeded" from here on.
    message = result.message
    if message.stop_reason == "refusal":
        return NeedsDirect(custom_id)
    if message.stop_reason == "max_tokens":
        # Truncated output can never be valid JSON for our schema — treat it
        # as a hard failure rather than trying (and likely failing) to parse it.
        return Errored(custom_id, "max_tokens")
    if message.stop_reason not in ("end_turn", "stop_sequence"):
        # e.g. pause_turn or model_context_window_exceeded: say which, rather
        # than letting it surface later as a confusing JSON error.
        return Invalid(custom_id, f"unexpected stop_reason {message.stop_reason!r}")

    # `stop_reason` is checked before content is read (spec.md > Generate),
    # so by this point we expect ordinary structured-output text. Thinking
    # blocks come first when extended thinking is on; skip past them.
    text = next((block.text for block in message.content if block.type == "text"), None)
    if text is None:
        return Invalid(custom_id, "no text content block in the response")

    model_cls = _out_model(custom_id, tags_only)
    try:
        parsed = model_cls.model_validate_json(text)
    except ValidationError as error:
        return Invalid(custom_id, str(error))
    return Applied(custom_id, parsed)


def apply(bill: Bill, outcomes: list[Outcome]) -> int:
    """Write every `Applied` outcome onto `bill`, in place. Returns how many were.

    Skips anything a human reviewed or edited (`models.is_protected`): a
    human's text is never overwritten by a fresh draft. That includes group
    entries: a new summary result replaces the summary and the unprotected
    group entries, but every protected one is kept, even if the new output
    leaves that group out. A `"sample"` item IS overwritten: that's the whole
    point of slice 2a's placeholders, they exist to be replaced. Every draft
    written here is stamped with its fingerprint (`draft_review`), AFTER all
    its fields are set, so a later hand-edit can be told apart from it.
    `suggested_articles` is always forced to `[]` here; validating suggested
    article numbers against the constitution is slice 10's job, not this one's.
    """
    clauses_by_id = {clause.id: clause for clause in bill.clauses}
    applied = 0
    # The summary goes last: its groups take their sections from the clause
    # tags, which must already be on the bill when it's applied.
    for outcome in sorted(outcomes, key=lambda outcome: outcome.custom_id == "summary"):
        if not isinstance(outcome, Applied):
            continue

        if outcome.custom_id == "summary":
            if bill.summary is not None and is_protected(bill.summary):
                print("  note: the summary was reviewed or edited by a human; kept it and its groups")
                continue
            summary_out: SummaryOut = outcome.parsed
            bill.summary = Summary(
                text=Tri(en=summary_out.summary.en, sw=summary_out.summary.sw, sheng=summary_out.summary.sheng),
                review=Review(status="draft"),
                sheng_swaps=_swaps(summary_out.summary_sheng_swaps),
            )
            bill.summary.review = draft_review(bill.summary)
            # A reviewed or hand-edited group entry is a human's text: keep it,
            # matched by group id, in place of that group's new draft.
            reviewed = {entry.group: entry for entry in bill.groups if is_protected(entry)}
            tags = tagged_sections(bill)
            groups: list[GroupEntry] = []
            seen: set[str] = set()
            for group in summary_out.groups:
                # The model can repeat a group, or write about one no clause
                # is tagged with. Neither may reach the site: keep the first
                # entry per group, and only groups the clause tags back up.
                if group.group in seen:
                    print(f"  note: the summary wrote group {group.group!r} twice; kept the first")
                    continue
                seen.add(group.group)
                if group.group in reviewed:
                    groups.append(reviewed.pop(group.group))
                    continue
                if not tags[group.group]:
                    print(f"  note: the summary wrote about group {group.group!r}, but no section is tagged with it; left out")
                    continue
                entry = GroupEntry(
                    group=group.group,
                    text=Tri(en=group.text.en, sw=group.text.sw, sheng=group.text.sheng),
                    clause_ids=list(tags[group.group]),
                    review=Review(status="draft"),
                    sheng_swaps=_swaps(group.sheng_swaps),
                )
                entry.review = draft_review(entry)
                groups.append(entry)
            # Reviewed groups the new output left out are still kept (at the end).
            groups.extend(reviewed.values())
            bill.groups = groups
            applied += 1
            continue

        clause = clauses_by_id.get(outcome.custom_id)
        if clause is None or is_protected(clause):
            continue
        clause_out: ClauseOut = outcome.parsed
        clause.explanation = Tri(
            en=clause_out.explanation.en, sw=clause_out.explanation.sw, sheng=clause_out.explanation.sheng
        )
        clause.neutral_check = NeutralCheck(ok=clause_out.neutral_check.ok, note=clause_out.neutral_check.note)
        clause.groups = list(dict.fromkeys(clause_out.groups))  # no repeats
        clause.suggested_articles = []
        clause.sheng_swaps = _swaps(clause_out.sheng_swaps)
        clause.review = draft_review(clause)  # last: it fingerprints the fields above
        applied += 1
    refresh_group_sections(bill)
    return applied


def refresh_group_sections(bill: Bill) -> None:
    """Make every group entry's `clause_ids` match the clause tags now on the bill.

    A group's section list comes only from the tags, so when tags change the
    list must follow. Whether the entry is a human's is decided BEFORE the
    list changes (the change itself would make any entry look edited):
    - an untouched AI draft gets the new list and a fresh fingerprint, or is
      dropped if no section is tagged with its group any more;
    - a reviewed or hand-edited entry gets the new list and is NOT
      re-fingerprinted, so a reviewed one now reads "changed since review"
      and lint and `politricks review` bring it back to a human.
    """
    tags = tagged_sections(bill)
    kept: list[GroupEntry] = []
    for entry in bill.groups:
        new = tags[entry.group]
        if new == entry.clause_ids:
            kept.append(entry)
            continue
        human = is_protected(entry)
        if not new and not human:
            print(f"  group {entry.group}: no section is tagged with it any more; draft dropped")
            continue
        note = " (a human's text: review it again)" if human else ""
        print(f"  group {entry.group} sections: {entry.clause_ids or '[]'} -> {new or '[]'}{note}")
        entry.clause_ids = list(new)
        if not human:
            entry.review = draft_review(entry)
        kept.append(entry)
    bill.groups = kept


def apply_tags(bill: Bill, outcomes: list[Outcome]) -> int:
    """Write the groups from a tags-only pass onto `bill`. Returns how many.

    Only `groups` changes (and the draft's fingerprint, which covers them):
    explanations and everything else are left exactly as they were. A clause a
    human reviewed or edited keeps its tags. Every change is printed, so the
    reviewer sees what moved. Group entries' section lists follow the new tags.
    """
    clauses_by_id = {clause.id: clause for clause in bill.clauses}
    applied = 0
    for outcome in outcomes:
        if not isinstance(outcome, Applied) or outcome.custom_id == "summary":
            continue
        clause = clauses_by_id.get(outcome.custom_id)
        if clause is None or is_protected(clause):
            continue
        new = list(dict.fromkeys(outcome.parsed.groups))  # no repeats
        if new != clause.groups:
            print(f"  tags {clause.id}: {clause.groups or '[]'} -> {new or '[]'}")
        clause.groups = new
        clause.review = draft_review(clause)  # still Claude's draft, now with these tags
        applied += 1
    refresh_group_sections(bill)
    return applied


# --------------------------------------------------------------------------
# Self-test: proves the above offline, against a recorded fixture. No network,
# no Anthropic client is ever constructed.
# --------------------------------------------------------------------------


_FAKE_BATCH_ID = "msgbatch_selftest"


class _FakeClient:
    """Just enough of `anthropic.Anthropic` for run(): batches and beta parse.

    Results come from recorded fixture lines, turned into the SDK's own
    response type, as the real `batches.results()` would give them.
    """

    def __init__(self, lines: list[dict], parse) -> None:
        self.created: list[int] = []
        self._lines = lines
        self.messages = SimpleNamespace(
            batches=SimpleNamespace(create=self._create, retrieve=self._retrieve, results=self._results)
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=parse))

    def _batch(self, status: str) -> SimpleNamespace:
        counts = SimpleNamespace(model_dump=lambda: {"fake": len(self._lines)})
        return SimpleNamespace(id=_FAKE_BATCH_ID, processing_status=status, request_counts=counts)

    def _create(self, requests: list[dict]) -> SimpleNamespace:
        self.created.append(len(requests))
        return self._batch("in_progress")

    def _retrieve(self, batch_id: str) -> SimpleNamespace:
        return self._batch("ended")

    def _results(self, batch_id: str) -> list:
        from anthropic.types.messages.message_batch_individual_response import (
            MessageBatchIndividualResponse,
        )

        return [MessageBatchIndividualResponse.model_validate(line) for line in self._lines]


def _run_offline(
    repo_root: Path,
    lines: list[dict],
    parse,
    batch_id: str | None = None,
    summary_only: bool = False,
    clause_ids: list[str] | None = None,
    tags_only: bool = False,
    prepare=None,
) -> SimpleNamespace:
    """Run run() in a temp copy of the repo against a fake client.

    The temp repo holds only what run() reads: the explainer prompt and the
    test bill (`reset_bill`, never the real file), cut down to `clause_ids`
    (default: the clauses the fixture lines answer for), so the batch asks
    for exactly what the fake returns unless a test says otherwise. Its
    exceptions are caught and returned as the code, so a crash fails a check
    instead of the test. `prepare(bill)`, if given, changes the test bill
    before the run (e.g. marks a clause reviewed).
    """
    keep = clause_ids if clause_ids is not None else [line["custom_id"] for line in lines]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        prompts = root / "pipeline" / "prompts"
        prompts.mkdir(parents=True)
        shutil.copy(repo_root / "pipeline" / "prompts" / "explainer.md", prompts)
        test_bill = reset_bill(repo_root)
        test_bill.clauses = [clause for clause in test_bill.clauses if clause.id in keep]
        if prepare is not None:
            prepare(test_bill)
        save_bill(test_bill, bill_path(root, FIXTURE_BILL_ID))

        client = _FakeClient(lines, parse)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = run(root, FIXTURE_BILL_ID, batch_id=batch_id, client=client, poll_seconds=0,
                           summary_only=summary_only, tags_only=tags_only)
            except Exception as error:
                code = f"raised {type(error).__name__}: {error}"

        records = root.joinpath(*BATCH_RECORDS)
        id_file = records / f"{FIXTURE_BILL_ID}.batch-id"
        tags_id_file = records / f"{FIXTURE_BILL_ID}.tags.batch-id"
        raw_file = records / f"{_FAKE_BATCH_ID}.jsonl"
        return SimpleNamespace(
            code=code,
            out=out.getvalue(),
            bill=load_bill(bill_path(root, FIXTURE_BILL_ID)),
            created=client.created,
            id_saved=id_file.read_text(encoding="utf-8").strip() if id_file.exists() else None,
            tags_id_saved=tags_id_file.read_text(encoding="utf-8").strip() if tags_id_file.exists() else None,
            raw_lines=len(raw_file.read_text(encoding="utf-8").splitlines()) if raw_file.exists() else 0,
        )


def mark_reviewed_with_text(bill: Bill, clause_id: str) -> None:
    """Self-test only, in memory: give a clause words and mark them reviewed."""
    clause = next(clause for clause in bill.clauses if clause.id == clause_id)
    clause.explanation = Tri(en="reviewed words", sw="reviewed words", sheng="reviewed words")
    mark_reviewed(clause)


def self_test(repo_root: Path) -> bool:
    import re

    check = Checker()

    real_path = bill_path(repo_root, FIXTURE_BILL_ID)
    sha_before = sha256_file(real_path)
    bill = load_bill(real_path)

    # --- request building --------------------------------------------------
    requests = build_batch(repo_root, bill)
    check("one batch request per clause, and no summary in the batch", len(requests) == len(bill.clauses)
          and all(request["custom_id"] != "summary" for request in requests))

    ids = [request["custom_id"] for request in requests]
    check("custom_ids are unique", len(ids) == len(set(ids)))
    pattern = re.compile(CUSTOM_ID_PATTERN)
    check("custom_ids match the batch pattern", all(pattern.match(cid) for cid in ids))

    schemas_ok = all(
        _all_objects_forbid_additional(request["params"]["output_config"]["format"]["schema"]) for request in requests
    )
    check("every output schema forbids additional properties", schemas_ok)

    no_fallbacks = all("fallbacks" not in request["params"] for request in requests)
    check('no "fallbacks" key in any batch params', no_fallbacks)

    direct = direct_params(requests[0]["params"])
    check(
        'direct_params carries fallbacks "default" and the beta',
        direct.get("fallbacks") == "default" and FALLBACK_BETA in direct.get("betas", []),
    )

    check(
        # Proves the builders wire in MODEL and EFFORT; the IDs themselves live
        # only in the constants above.
        f"every batch request carries MODEL ({MODEL}) and EFFORT ({EFFORT!r})",
        all(
            request["params"]["model"] == MODEL and request["params"]["output_config"].get("effort") == EFFORT
            for request in requests
        ),
    )
    retry = retry_params(requests[0]["params"])
    check(
        "retry_params drops the schema but keeps the effort and the fallback",
        "format" not in retry.get("output_config", {})
        and retry.get("output_config", {}).get("effort") == EFFORT
        and retry.get("fallbacks") == "default",
    )
    check("retry_params leaves the batch params untouched", "format" in requests[0]["params"]["output_config"])

    # --- result parsing ------------------------------------------------------
    fixture_path = repo_root / "pipeline" / "tests" / "fixtures" / "batch_results.jsonl"
    lines = [json.loads(line) for line in fixture_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    outcomes = [parse_batch_line(line) for line in lines]

    by_type: dict[str, int] = {}
    for outcome in outcomes:
        by_type[type(outcome).__name__] = by_type.get(type(outcome).__name__, 0) + 1
    check(
        "outcome counts: Applied 2 / NeedsDirect 1 / Errored 1 / Invalid 1",
        by_type == {"Applied": 2, "NeedsDirect": 1, "Errored": 1, "Invalid": 1},
    )

    # --- applying onto an in-memory copy (nothing is written to disk) -------
    # Not a plain copy of the real bill: once a human reviews c3 or c4 in the
    # real file, apply() would (correctly) skip it and "2 applied" would fail on
    # a healthy repo. `reset_bill` is the same clauses with all of that cleared.
    working = reset_bill(repo_root)
    applied_count = apply(working, outcomes)
    check("apply() reports 2 applied", applied_count == 2)

    applied_ids = {outcome.custom_id for outcome in outcomes if isinstance(outcome, Applied)}
    still_draft = all(
        clause.review.status == "draft" and clause.explanation is not None
        for clause in working.clauses
        if clause.id in applied_ids
    )
    check("applied clauses become status \"draft\" with an explanation", still_draft)

    # --- the result is still a valid Bill ------------------------------------
    try:
        Bill.model_validate(working.model_dump(mode="json"))
        revalidates = True
    except ValidationError:
        revalidates = False
    check("the resulting bill still validates as models.Bill", revalidates)

    # --- a reviewed group entry survives a new summary result -----------------
    # Three existing groups: trader and student are reviewed, transport is a
    # draft. The new summary names trader and employee, and leaves out student
    # and transport. Expected: trader keeps the human's words, student is kept
    # even though the output omitted it, transport (a draft) is replaced,
    # employee arrives as a fresh draft whose sections come from the clause
    # tags (c4 is tagged employee), never from the model.
    def tri_out(text: str) -> TriOut:
        return TriOut(en=text, sw=text, sheng=text)

    def tagged_bill(**tags: list[str]) -> Bill:
        """`reset_bill` with the given clause tags, e.g. c4=["employee"]."""
        tagged = reset_bill(repo_root)
        for clause in tagged.clauses:
            clause.groups = list(tags.get(clause.id, []))
        return tagged

    human_trader = GroupEntry(
        group="trader", text=Tri(en="human trader", sw="human trader", sheng="human trader"),
        clause_ids=["c3"], review=Review(status="reviewed", at="2026-01-01"),
    )
    human_student = GroupEntry(
        group="student", text=Tri(en="human student", sw="human student", sheng="human student"),
        clause_ids=["c4"], review=Review(status="reviewed", at="2026-01-01"),
    )
    draft_transport = GroupEntry(
        group="transport", text=Tri(en="old draft", sw="old draft", sheng="old draft"),
        clause_ids=["c3"], review=Review(status="draft"),
    )
    summary_result = Applied(
        "summary",
        SummaryOut(
            summary=tri_out("new summary"), summary_sheng_swaps=[],
            groups=[
                GroupOut(group="trader", text=tri_out("new trader draft"), sheng_swaps=[]),
                GroupOut(group="employee", text=tri_out("new employee draft"), sheng_swaps=[]),
            ],
        ),
    )
    trader_before, student_before = human_trader.model_copy(deep=True), human_student.model_copy(deep=True)
    grouped = tagged_bill(c3=["trader"], c4=["employee"])
    grouped.groups = [human_trader, draft_transport, human_student]
    grouped_applied = apply(grouped, [summary_result])
    by_group = {entry.group: entry for entry in grouped.groups}
    check("summary apply: reported 1 applied", grouped_applied == 1, str(grouped_applied))
    check(
        "summary apply: the reviewed trader entry keeps the human's words (not overwritten)",
        by_group.get("trader") == trader_before,
        str(by_group.get("trader")),
    )
    student = by_group.get("student")
    check(
        "summary apply: the reviewed student entry is kept although the new output omitted it",
        student is not None and student.text == student_before.text and student.review == student_before.review,
        str(sorted(by_group)),
    )
    check(
        "summary apply: its sections follow the tags (none now), so it reads changed since review",
        student is not None and student.clause_ids == [] and changed_since_review(student),
        str(student),
    )
    check(
        "summary apply: the draft transport entry is replaced, and employee arrives as a draft",
        "transport" not in by_group
        and by_group.get("employee") is not None
        and by_group["employee"].review.status == "draft"
        and by_group["employee"].text.en == "new employee draft",
        str(sorted(by_group)),
    )
    employee = by_group.get("employee")
    check("summary apply: a group's sections come from the clause tags",
          employee is not None and employee.clause_ids == ["c4"], str(employee))
    check("summary apply: the summary itself is the new draft", grouped.summary.text.en == "new summary" and grouped.summary.review.status == "draft")

    # --- the summary request: full section text, plus the clause tags --------
    summary_params = build_summary_params(repo_root, tagged_bill(c3=["trader"], c4=["employee", "trader"]))
    summary_user = summary_params["messages"][0]["content"]
    longest = max(bill.clauses, key=lambda clause: len(clause.text))
    check(
        "summary request carries every section's id and full text",
        all(f'<section id="{clause.id}">' in summary_user for clause in bill.clauses) and longest.text in summary_user,
    )
    check(
        "summary request lists each group's tagged sections, and says when there are none",
        "- trader: c3, c4" in summary_user and "- employee: c4" in summary_user
        and "- student: no sections" in summary_user,
        summary_user[:400],
    )
    check("summary schema forbids additional properties",
          _all_objects_forbid_additional(summary_params["output_config"]["format"]["schema"]))

    # --- a repeated group, or one no clause is tagged with, never gets through
    messy = Applied(
        "summary",
        SummaryOut(
            summary=tri_out("summary"), summary_sheng_swaps=[],
            groups=[
                GroupOut(group="employee", text=tri_out("first employee"), sheng_swaps=[]),
                GroupOut(group="employee", text=tri_out("second employee"), sheng_swaps=[]),
                GroupOut(group="student", text=tri_out("no section is tagged student"), sheng_swaps=[]),
            ],
        ),
    )
    cleaned = tagged_bill(c3=["employee"])
    with contextlib.redirect_stdout(io.StringIO()) as notes:
        apply(cleaned, [messy])
    by_group = {entry.group: entry for entry in cleaned.groups}
    check(
        "summary apply: a repeated group keeps only its first entry",
        [entry.group for entry in cleaned.groups].count("employee") == 1
        and by_group.get("employee") is not None and by_group["employee"].text.en == "first employee",
        str([entry.group for entry in cleaned.groups]),
    )
    check("summary apply: a group no clause is tagged with is left out", "student" not in by_group, str(sorted(by_group)))
    # 2 notes: employee repeated, student untagged.
    check("summary apply: each repair is reported", notes.getvalue().count("note:") == 2, notes.getvalue())

    # --- in one apply() call, the summary still sees the clause tags ---------
    # Outcomes in the "wrong" order (summary first) must still give the
    # summary's transport group the section tagged in the same call.
    # The same outcomes carry Sheng swaps, which must land on the clause, the
    # summary and the group entry.
    clause_result = Applied(
        "c3",
        ClauseOut(explanation=tri_out("c3 text"), groups=["transport"], suggested_articles=[],
                  neutral_check=NeutralOut(ok=True, note=""),
                  sheng_swaps=[SwapOut(word="ushuru", english="tax")]),
    )
    transport_summary = Applied(
        "summary",
        SummaryOut(
            summary=tri_out("s"),
            summary_sheng_swaps=[SwapOut(word="mapato", english="income")],
            groups=[GroupOut(group="transport", text=tri_out("t"), sheng_swaps=[SwapOut(word="magari", english="vehicles")])],
        ),
    )
    ordered = reset_bill(repo_root)
    apply(ordered, [transport_summary, clause_result])
    check("summary apply: clause tags from the same call are applied first",
          [(entry.group, entry.clause_ids) for entry in ordered.groups] == [("transport", ["c3"])],
          str([(entry.group, entry.clause_ids) for entry in ordered.groups]))
    landed = (
        [(swap.word, swap.english) for swap in next(c for c in ordered.clauses if c.id == "c3").sheng_swaps],
        [(swap.word, swap.english) for swap in ordered.summary.sheng_swaps],
        [(swap.word, swap.english) for swap in ordered.groups[0].sheng_swaps] if ordered.groups else None,
    )
    check("apply: Sheng swaps land on the clause, the summary and the group",
          landed == ([("ushuru", "tax")], [("mapato", "income")], [("magari", "vehicles")]), str(landed))

    # --- run() end to end, offline -------------------------------------------
    # A fake client stands in for the API, and a temp folder for the repo, so
    # run()'s own code is what's tested: the saved batch id, the raw results
    # file, resume, the refusal retry and its failures, and the exit code.
    # Fixture lines: c3, c4 applied; c5 refused; c6 errored; c7 invalid.
    fixture_ok = next(outcome.parsed for outcome in outcomes if isinstance(outcome, Applied))
    fake_summary = SummaryOut(summary=tri_out("fake summary"), summary_sheng_swaps=[], groups=[])
    parse_calls: list[dict] = []

    def parse_ok(**kwargs):
        parse_calls.append(kwargs)
        # Only `parsed_output`, like the real SDK object; reading `.parsed` fails.
        answer = fake_summary if kwargs.get("output_format") is SummaryOut else fixture_ok
        return SimpleNamespace(parsed_output=answer, stop_reason="end_turn", model="claude-opus-5-5")

    def parse_raises(**kwargs):
        raise ValueError("planted: the retry itself failed")

    def parse_refused(**kwargs):
        return SimpleNamespace(parsed_output=None, stop_reason="refusal", model=MODEL)

    first_three = lines[:3]  # c3, c4, c5 (the refusal)

    a = _run_offline(repo_root, first_three, parse_ok)
    check("run: a good retry gives exit 0", a.code == 0, f"{a.code} / {a.out}")
    check("run: one batch submitted, with every clause", a.created == [len(first_three)], str(a.created))
    check("run: the batch id is saved to disk the moment it's submitted", a.id_saved == _FAKE_BATCH_ID, str(a.id_saved))
    check("run: the raw results are saved (one line per result)", a.raw_lines == 3, str(a.raw_lines))
    c5 = next(clause for clause in a.bill.clauses if clause.id == "c5")
    check("run: the retried clause is saved as a draft", c5.explanation is not None and c5.review.status == "draft")
    check("run: it says which model answered the retry", "c5 answered directly by claude-opus-5-5" in a.out, a.out)
    check("run: phase 2 writes the summary as a draft", a.bill.summary is not None
          and a.bill.summary.text.en == "fake summary" and a.bill.summary.review.status == "draft")
    last = parse_calls[-1] if parse_calls else {}
    check(
        "run: the summary is asked for last, as a SummaryOut, with the clause tags in the request",
        last.get("output_format") is SummaryOut
        and "Sections already tagged for each group" in last.get("messages", [{}])[0].get("content", ""),
        str(sorted(last)),
    )
    sent = parse_calls[0] if parse_calls else {}
    check(
        "run: the retry gets the Pydantic class, the effort and the fallback, and no pre-built schema",
        sent.get("output_format") is ClauseOut
        and sent.get("output_config", {}).get("effort") == EFFORT
        and "format" not in sent.get("output_config", {})
        and sent.get("fallbacks") == "default",
        str(sorted(sent)),
    )

    resumed = _run_offline(repo_root, first_three, parse_ok, batch_id=_FAKE_BATCH_ID)
    check("run --batch-id: resumes without submitting (or paying) again", resumed.created == [] and resumed.code == 0,
          f"{resumed.created} / {resumed.code}")

    parse_calls.clear()
    only = _run_offline(repo_root, first_three, parse_ok, summary_only=True)
    check("run --summary-only: no batch, one direct call, summary written",
          only.created == [] and only.code == 0 and len(parse_calls) == 1
          and only.bill.summary.text.en == "fake summary",
          f"{only.created} / {only.code} / {len(parse_calls)} / {only.out}")

    b = _run_offline(repo_root, lines, parse_raises)
    check("run: a retry that raises doesn't crash the run (exit 1)", b.code == 1, f"{b.code} / {b.out}")
    saved = {clause.id for clause in b.bill.clauses if clause.explanation is not None and clause.review.status == "draft"}
    check("run: the other results are still saved", {"c3", "c4"} <= saved, str(sorted(saved)))
    check(
        "run: every request not applied is listed",
        all(f"NOT APPLIED {cid}" in b.out for cid in ("c5", "c6", "c7")),
        b.out,
    )
    check(
        "run: with clauses missing, the summary is not asked for (or paid for)",
        "summary not asked for" in b.out and "NOT APPLIED summary" not in b.out,
        b.out,
    )

    missing = _run_offline(repo_root, lines[:2], parse_ok, clause_ids=["c3", "c4", "c5"])
    check("run: a request with no result is listed and fails the run",
          missing.code == 1 and "NOT APPLIED c5: no result came back" in missing.out, f"{missing.code} / {missing.out}")
    stray = _run_offline(repo_root, lines[:2], parse_ok, clause_ids=["c3"])
    check("run: a result for a request this bill didn't make fails the run (wrong --batch-id)",
          stray.code == 1 and "NOT APPLIED c4: a result for a request this bill didn't make" in stray.out,
          f"{stray.code} / {stray.out}")
    check("run: the batch error keeps the API's type and message", "c6: errored: api_error: internal server error" in b.out, b.out)

    c = _run_offline(repo_root, first_three, parse_refused)
    check("run: a retry that's refused again is listed, not applied as None (exit 1)",
          c.code == 1 and "NOT APPLIED c5: direct call gave no usable output" in c.out, f"{c.code} / {c.out}")

    # --- the tags-only pass ---------------------------------------------------
    tag_requests = build_tag_batch(repo_root, bill)
    tag_schema = tag_requests[0]["params"]["output_config"]["format"]["schema"]
    check(
        "tags batch: one request per clause, schema allows only `groups`, and says it's tags-only",
        len(tag_requests) == len(bill.clauses)
        and set(tag_schema.get("properties", {})) == {"groups"}
        and _all_objects_forbid_additional(tag_schema)
        and "tags-only request" in tag_requests[0]["params"]["messages"][0]["content"],
        str(sorted(tag_schema.get("properties", {}))),
    )
    check(
        "tags batch: same system prompt as the explainer (so it reuses the cache)",
        tag_requests[0]["params"]["system"] == requests[0]["params"]["system"],
    )

    tag_bill = reset_bill(repo_root)
    apply(tag_bill, outcomes)  # c3, c4 get fixture explanations and tags
    c3_before = next(clause for clause in tag_bill.clauses if clause.id == "c3").model_copy(deep=True)
    reviewed_c4 = next(clause for clause in tag_bill.clauses if clause.id == "c4")
    reviewed_c4.review = Review(status="reviewed", at="2026-01-01")
    c4_tags = list(reviewed_c4.groups)
    with contextlib.redirect_stdout(io.StringIO()) as tag_notes:
        tagged_count = apply_tags(tag_bill, [
            Applied("c3", TagsOut(groups=["transport", "transport"])),
            Applied("c4", TagsOut(groups=["student"])),
        ])
    c3_after = next(clause for clause in tag_bill.clauses if clause.id == "c3")
    check("apply_tags: a draft clause gets the new groups, without repeats", c3_after.groups == ["transport"],
          str(c3_after.groups))
    check("apply_tags: nothing but groups changes (explanation, status, swaps untouched)",
          c3_after.model_copy(update={"groups": c3_before.groups, "review": c3_before.review}) == c3_before
          and c3_after.review.status == "draft")
    check("apply_tags: the draft is re-fingerprinted with its new tags (still Claude's, still replaceable)",
          c3_after.review.sha256 == current_sha256(c3_after) and not is_protected(c3_after), str(c3_after.review))
    check("apply_tags: a reviewed clause keeps the tags a human accepted",
          reviewed_c4.groups == c4_tags and tagged_count == 1, f"{reviewed_c4.groups} / {tagged_count}")
    check("apply_tags: each change is printed", "tags c3:" in tag_notes.getvalue(), tag_notes.getvalue())

    # A whole tags-only run, offline: answers are {"groups": [...]} lines.
    def tags_line(custom_id: str, groups: list[str]) -> dict:
        line = json.loads(json.dumps(lines[0]))  # a real succeeded line, re-pointed
        line["custom_id"] = custom_id
        line["result"]["message"]["content"] = [{"type": "text", "text": json.dumps({"groups": groups})}]
        return line

    parse_calls.clear()
    tagged_run = _run_offline(repo_root, [tags_line("c3", ["employee"]), tags_line("c4", [])], parse_ok, tags_only=True)
    tagged_c3 = next(clause for clause in tagged_run.bill.clauses if clause.id == "c3")
    check("run --tags-only: exit 0, tags applied, explanations untouched",
          tagged_run.code == 0 and tagged_c3.groups == ["employee"] and tagged_c3.explanation is None,
          f"{tagged_run.code} / {tagged_c3.groups} / {tagged_run.out}")
    check("run --tags-only: its batch id is saved under its own name",
          tagged_run.tags_id_saved == _FAKE_BATCH_ID and tagged_run.id_saved is None,
          f"{tagged_run.tags_id_saved} / {tagged_run.id_saved}")
    check("run --tags-only: the summary is rewritten from the new tags",
          len(parse_calls) == 1 and parse_calls[0].get("output_format") is SummaryOut
          and "- employee: c3" in parse_calls[0]["messages"][0]["content"],
          str(len(parse_calls)))

    # --- review fingerprints: what generate may overwrite ----------------------
    # See models.is_protected. Every draft generate writes carries its
    # fingerprint; anything a human reviewed or edited is never overwritten or
    # paid for again.
    stamped = reset_bill(repo_root)
    apply(stamped, [*outcomes, clause_result, transport_summary])  # clause_result tags c3 transport
    drafts = [c for c in stamped.clauses if c.explanation is not None and c.review.status == "draft"]
    drafts += [stamped.summary, *stamped.groups]
    check("fingerprints: every draft apply writes (clauses, summary, groups) is stamped with its own fingerprint",
          len(drafts) >= 4 and all(item.review.sha256 == current_sha256(item) for item in drafts),
          str([item.review.sha256 for item in drafts]))

    def protected_bill() -> Bill:
        """c3 reviewed (fingerprint matches), c4 a hand-edited draft."""
        guarded = reset_bill(repo_root)
        apply(guarded, outcomes)
        mark_reviewed(next(c for c in guarded.clauses if c.id == "c3"))
        c4 = next(c for c in guarded.clauses if c.id == "c4")
        c4.explanation.en = "a human's words"
        return guarded

    guarded = protected_bill()
    g3, g4 = (next(c for c in guarded.clauses if c.id == cid) for cid in ("c3", "c4"))
    check("fingerprints: a reviewed clause whose content matches is reviewed", is_reviewed(g3))
    check("fingerprints: a draft edited in the JSON reads as hand-edited", is_hand_edited(g4))
    before = (g3.model_copy(deep=True), g4.model_copy(deep=True))
    apply(guarded, outcomes)
    check("apply: a reviewed clause and a hand-edited draft are both kept as they were",
          (g3, g4) == before, f"{g3.explanation} / {g4.explanation}")
    asked = {request["custom_id"] for request in build_batch(repo_root, guarded)}
    asked_tags = {request["custom_id"] for request in build_tag_batch(repo_root, guarded)}
    check("build_batch / build_tag_batch: leave out reviewed and hand-edited clauses (not paid for again)",
          "c3" not in asked and "c4" not in asked and "c3" not in asked_tags and "c4" not in asked_tags
          and "c5" in asked and "c5" in asked_tags, f"{sorted(asked)[:4]} / {sorted(asked_tags)[:4]}")

    # Edit-back: a review-walk edit stores HAND_EDITED, which never matches,
    # so typing the old words back in doesn't hand the text back to generate.
    edited_back = reset_bill(repo_root)
    apply(edited_back, outcomes)
    e3 = next(c for c in edited_back.clauses if c.id == "c3")
    e3.review = Review(status="draft", sha256=HAND_EDITED)
    check("fingerprints: an edit typed back to the old words stays hand-edited (protected)",
          is_hand_edited(e3) and is_protected(e3))
    e3.review = Review(status="draft", sha256=None)
    check("fingerprints: a draft with no fingerprint is generate's to replace (the documented escape hatch)",
          not is_protected(e3))

    tagged_reviewed = reset_bill(repo_root)
    apply(tagged_reviewed, outcomes)
    t3 = next(c for c in tagged_reviewed.clauses if c.id == "c3")
    mark_reviewed(t3)
    t3.groups = [*t3.groups, "student"]
    check("fingerprints: changing a reviewed clause's tags makes it changed since review",
          changed_since_review(t3) and not is_reviewed(t3))
    reordered = t3.model_copy(deep=True)
    mark_reviewed(reordered)
    reordered.groups = list(reversed(reordered.groups))
    check("fingerprints: the same tags in another order change nothing", is_reviewed(reordered), str(reordered.groups))
    texted = t3.model_copy(deep=True)
    mark_reviewed(texted)
    texted.text = texted.text + " (edited)"
    check("fingerprints: editing the legal text un-reviews its explanation", changed_since_review(texted))
    no_hash = t3.model_copy(deep=True)
    no_hash.review = Review(status="reviewed", at="2026-01-01")
    check("fingerprints: reviewed with no fingerprint is NOT reviewed (fail closed)", changed_since_review(no_hash))

    # refresh_group_sections, case by case.
    refreshing = tagged_bill(c3=["trader"], c4=["trader"])
    fresh = GroupEntry(group="trader", text=Tri(en="t", sw="t", sheng="t"), clause_ids=["c3"], review=Review(status="draft"))
    fresh.review = draft_review(fresh)
    gone = GroupEntry(group="student", text=Tri(en="s", sw="s", sheng="s"), clause_ids=["c4"], review=Review(status="draft"))
    gone.review = draft_review(gone)
    human = GroupEntry(group="employee", text=Tri(en="e", sw="e", sheng="e"), clause_ids=["c4"], review=Review(status="draft"))
    mark_reviewed(human)
    refreshing.groups = [fresh, gone, human]
    with contextlib.redirect_stdout(io.StringIO()):
        refresh_group_sections(refreshing)
    after = {entry.group: entry for entry in refreshing.groups}
    check("refresh: an AI draft group follows the tags and is re-fingerprinted (still replaceable)",
          after["trader"].clause_ids == ["c3", "c4"] and not is_protected(after["trader"]), str(after.get("trader")))
    check("refresh: an AI draft group with no tagged section left is dropped", "student" not in after, str(sorted(after)))
    check("refresh: a reviewed group is kept, follows the tags, and reads changed since review",
          "employee" in after and after["employee"].clause_ids == [] and changed_since_review(after["employee"]),
          str(after.get("employee")))

    # run() with protected clauses: nothing sent for them, and a result for a
    # clause reviewed while the batch ran is left alone, not a failure.
    created_for = _run_offline(repo_root, lines[:2], parse_ok, clause_ids=["c3", "c4", "c5"],
                               prepare=lambda b: mark_reviewed_with_text(b, "c5"))
    check("run: a reviewed clause is not in the batch", created_for.created == [2], f"{created_for.created} / {created_for.out}")

    all_done = _run_offline(repo_root, [], parse_ok, clause_ids=["c3"], prepare=lambda b: mark_reviewed_with_text(b, "c3"))
    check("run: every clause reviewed means no batch at all (and no crash)", all_done.created == [] and all_done.code == 0,
          f"{all_done.created} / {all_done.code} / {all_done.out}")

    def reviewed_since(b: Bill) -> None:
        mark_reviewed_with_text(b, "c4")

    since = _run_offline(repo_root, lines[:2], parse_ok, clause_ids=["c3", "c4"], batch_id=_FAKE_BATCH_ID, prepare=reviewed_since)
    since_c4 = next(clause for clause in since.bill.clauses if clause.id == "c4")
    check("run --batch-id: a result for a clause reviewed since the batch was sent is left alone (exit 0)",
          since.code == 0 and "left alone" in since.out and is_reviewed(since_c4) and since_c4.explanation.en == "reviewed words",
          f"{since.code} / {since.out}")

    # --- nothing written -----------------------------------------------------
    check("the real bill file's SHA-256 is unchanged", sha256_file(real_path) == sha_before)

    return check.finish()


# --------------------------------------------------------------------------
# Live entry point. The self-test drives it end to end with a fake client
# (no network). Against the real API, only a 1-request probe of build_batch
# and parse_batch_line has run so far (2026-10-13), not run() itself.
# --------------------------------------------------------------------------

# Where a run keeps what it paid for, under repo_root (gitignored via
# pipeline/.cache/): "<bill-id>.batch-id" is written the moment a batch is
# submitted, and "<batch-id>.jsonl" holds the raw results before anything
# parses them. Anthropic keeps results for 29 days, so with the id on disk a
# crash or Ctrl-C costs a resume, not a second payment.
BATCH_RECORDS = ("pipeline", ".cache", "batches")


def _call_direct(client, custom_id: str, params: dict, tags_only: bool = False) -> Outcome:
    """One direct (non-batch) request, with the server-side fallback on.

    Used for refused clauses and for the summary, which needs the clause tags
    and so can't ride in the clause batch. Never raises: anything that goes
    wrong becomes an `Errored`, so one bad call can't throw away every other
    result the batch already paid for.
    """
    model_cls = _out_model(custom_id, tags_only)
    try:
        message = client.beta.messages.parse(output_format=model_cls, **retry_params(params))
    except Exception as error:  # a refusal, truncated JSON, a network drop...
        return Errored(custom_id, f"direct call failed: {type(error).__name__}: {error}")
    # The SDK puts the validated object on `parsed_output` (anthropic 1.8.0,
    # types/beta/parsed_beta_message.py). There is no `.parsed`.
    if message.stop_reason in ("refusal", "max_tokens") or message.parsed_output is None:
        return Errored(custom_id, f"direct call gave no usable output (stop_reason {message.stop_reason!r})")
    # With fallbacks on, a different model may have written this answer.
    # Say which, so the reviewer knows.
    print(f"  {custom_id} answered directly by {message.model}")
    return Applied(custom_id, message.parsed_output)


def _summarise(repo_root: Path, bill: Bill, client) -> list[Outcome]:
    """Phase 2: the summary, written from the clause tags now on `bill`."""
    if bill.summary is not None and is_protected(bill.summary):
        print("summary already reviewed or edited by a human; not asked for again")
        return []
    print("asking for the summary (direct call, from the clause tags)...")
    outcome = _call_direct(client, "summary", build_summary_params(repo_root, bill))
    apply(bill, [outcome])
    return [outcome]


def _report(applied: int, outcomes: list[Outcome], raw_path: Path | None) -> int:
    """Print what landed and what didn't. Returns the exit code."""
    print(f"applied {applied} results (status: draft)")
    failures = [outcome for outcome in outcomes if not isinstance(outcome, Applied)]
    for failure in failures:
        print(f"  NOT APPLIED {failure.custom_id}: {getattr(failure, 'reason', type(failure).__name__)}")
    if failures:
        where = f" Raw batch results: {raw_path}" if raw_path else ""
        print(f"{len(failures)} of {len(outcomes)} request(s) not applied.{where}")
        if [failure.custom_id for failure in failures] == ["summary"]:
            print("  only the summary failed: redo it alone with --summary-only")
        return 1
    return 0


def run(
    repo_root: Path,
    bill_id: str,
    batch_id: str | None = None,
    client=None,
    poll_seconds: float = 10,
    summary_only: bool = False,
    tags_only: bool = False,
) -> int:
    """Explain every clause in one batch, then write the summary from their tags.

    Phase 1 submits the clause batch (or resumes `batch_id`), waits, retries
    refusals directly and applies the results. Phase 2 asks for the summary
    directly, because it needs the tags phase 1 produced. `summary_only`
    skips phase 1 and redoes just the summary from the tags already saved.
    `tags_only` makes phase 1 re-decide only each clause's groups (cheap,
    explanations untouched); phase 2 then rewrites the summary from them.

    Returns the exit code: 0 only if every request was applied, 1 if any was
    not. Each one that wasn't is listed with its reason. `client` and
    `poll_seconds` exist so the self-test can drive this offline with a fake.
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    path = bill_path(repo_root, bill_id)
    bill = load_bill(path)

    if summary_only:
        outcomes = _summarise(repo_root, bill, client)
        save_bill(bill, path)
        return _report(sum(isinstance(outcome, Applied) for outcome in outcomes), outcomes, None)

    requests = build_tag_batch(repo_root, bill) if tags_only else build_batch(repo_root, bill)
    protected_ids = {clause.id for clause in bill.clauses if is_protected(clause)}
    if protected_ids:
        print(f"left out {len(protected_ids)} clause(s) a human reviewed or edited: {', '.join(sorted(protected_ids))}")
    if not requests and batch_id is None:
        print("no clause needs a draft: nothing sent")
        if tags_only:
            return 0
        summary = _summarise(repo_root, bill, client)
        save_bill(bill, path)
        return _report(sum(isinstance(outcome, Applied) for outcome in summary), summary, None)
    records = repo_root.joinpath(*BATCH_RECORDS)
    records.mkdir(parents=True, exist_ok=True)
    mode_flag = " --tags-only" if tags_only else ""

    if batch_id is None:
        batch = client.messages.batches.create(requests=requests)
        batch_id = batch.id
        id_name = f"{bill_id}.tags.batch-id" if tags_only else f"{bill_id}.batch-id"
        (records / id_name).write_text(batch_id + "\n", encoding="utf-8")
        print(f"submitted {'tags-only ' if tags_only else ''}batch {batch_id} ({len(requests)} requests)")
        print(f"  if this stops, resume without paying again: politricks generate {bill_id}{mode_flag} --batch-id {batch_id}")
    else:
        print(f"resuming batch {batch_id} (nothing new is submitted)")
        batch = client.messages.batches.retrieve(batch_id)

    while batch.processing_status != "ended":
        time.sleep(poll_seconds)
        batch = client.messages.batches.retrieve(batch_id)
        print(f"  status: {batch.processing_status}")
    print(f"batch ended: {batch.request_counts.model_dump()}")

    # Save the raw results BEFORE parsing anything, so a parsing bug can never
    # cost us what the API already returned.
    raw = [response.model_dump(mode="json") for response in client.messages.batches.results(batch_id)]
    raw_path = records / f"{batch_id}.jsonl"
    raw_path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in raw), encoding="utf-8")
    outcomes = [parse_batch_line(line, tags_only) for line in raw]

    # Check what came back against what was asked for. A missing line, or a
    # line for a request this bill never made (a wrong --batch-id), must fail
    # loudly, not pass as "applied 0".
    by_custom_id = {request["custom_id"]: request["params"] for request in requests}
    returned = {outcome.custom_id for outcome in outcomes}
    # A clause a human reviewed or edited WHILE the batch ran is no longer
    # asked for, so its result looks foreign. It isn't a failure: leave the
    # human's version alone and say so.
    since = sorted(outcome.custom_id for outcome in outcomes if outcome.custom_id not in by_custom_id and outcome.custom_id in protected_ids)
    if since:
        print(f"  left alone (reviewed or edited since the batch was sent): {', '.join(since)}")
    outcomes = [outcome for outcome in outcomes if outcome.custom_id not in since]
    outcomes = [
        outcome if outcome.custom_id in by_custom_id
        else Errored(outcome.custom_id, "a result for a request this bill didn't make (wrong --batch-id?)")
        for outcome in outcomes
    ]
    outcomes += [
        Errored(custom_id, "no result came back for this request")
        for custom_id in by_custom_id
        if custom_id not in returned
    ]

    # Refusals are retried one at a time, directly, with the fallback on —
    # never inside a batch, which rejects the fallback option outright.
    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, NeedsDirect):
            params = by_custom_id.get(outcome.custom_id)
            outcomes[i] = (
                _call_direct(client, outcome.custom_id, params, tags_only)
                if params is not None
                else Errored(outcome.custom_id, "refused, and no longer in the bill to retry")
            )

    applied = apply_tags(bill, outcomes) if tags_only else apply(bill, outcomes)
    save_bill(bill, path)  # the clause results are safe on disk before phase 2

    # The summary is written from the clause tags, so it waits until every
    # clause landed: a summary from half the tags would be wrong, and paid for.
    if any(not isinstance(outcome, Applied) for outcome in outcomes):
        print(f"summary not asked for: some clauses didn't land. Fix them, then: politricks generate {bill_id} --summary-only")
        return _report(applied, outcomes, raw_path)

    summary = _summarise(repo_root, bill, client)
    applied += sum(isinstance(outcome, Applied) for outcome in summary)
    save_bill(bill, path)
    return _report(applied, outcomes + summary, raw_path)
