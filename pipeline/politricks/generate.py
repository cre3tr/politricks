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

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from pydantic import BaseModel, ConfigDict, ValidationError

from .hashing import sha256_file
from .models import (
    Bill,
    Clause,
    GroupEntry,
    GroupId,
    NeutralCheck,
    Review,
    Summary,
    Tri,
    bill_path,
    load_bill,
    save_bill,
)
from .selftest import FIXTURE_BILL_ID, Checker, reset_bill

MODEL = "claude-opus-5"
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


class ClauseOut(BaseModel):
    model_config = _STRICT
    explanation: TriOut
    groups: list[GroupId]
    suggested_articles: list[int]
    neutral_check: NeutralOut


class GroupOut(BaseModel):
    model_config = _STRICT
    group: GroupId
    text: TriOut
    clause_ids: list[str]


class SummaryOut(BaseModel):
    model_config = _STRICT
    summary: TriOut
    groups: list[GroupOut]


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
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }


def build_summary_params(repo_root: Path, bill: Bill) -> dict:
    """The Messages API params for the bill's overall summary + group blurbs."""
    schema = SummaryOut.model_json_schema()
    assert _all_objects_forbid_additional(schema), "SummaryOut schema must forbid extra properties everywhere"
    sections = "\n".join(f"{clause.id}: {clause.number}. {clause.heading}" for clause in bill.clauses)
    user_text = f"Bill: {bill.title}\n\nSections in this bill:\n{sections}"
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
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }


def build_batch(repo_root: Path, bill: Bill) -> list[dict]:
    """One batch request per clause, plus one for the bill-wide summary."""
    requests = [
        {"custom_id": clause.id, "params": build_params(repo_root, bill, clause)} for clause in bill.clauses
    ]
    requests.append({"custom_id": "summary", "params": build_summary_params(repo_root, bill)})
    return requests


def direct_params(params: dict) -> dict:
    """`params` plus the server-side fallback, for a DIRECT retry of a refusal.

    Checked against the installed SDK (anthropic 1.8.0): `client.beta.messages
    .parse` accepts `betas` and `fallbacks` as ordinary keyword arguments —
    there was no need to smuggle them through `extra_body`. This function
    still returns a plain params dict (not SDK objects), so the caller decides
    how to pass it (`client.beta.messages.parse(**direct_params(params))`).
    """
    return {**params, "betas": [FALLBACK_BETA], "fallbacks": "default"}


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


def parse_batch_line(obj: dict) -> Outcome:
    """Turn one decoded line of a batch results `.jsonl` file into an Outcome.

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

    if result.type in ("errored", "expired", "canceled"):
        return Errored(custom_id, result.type)

    # result.type == "succeeded" from here on.
    message = result.message
    if message.stop_reason == "refusal":
        return NeedsDirect(custom_id)
    if message.stop_reason == "max_tokens":
        # Truncated output can never be valid JSON for our schema — treat it
        # as a hard failure rather than trying (and likely failing) to parse it.
        return Errored(custom_id, "max_tokens")

    # `stop_reason` is checked before content is read (spec.md > Generate),
    # so by this point we expect ordinary structured-output text. Thinking
    # blocks come first when extended thinking is on; skip past them.
    text = next((block.text for block in message.content if block.type == "text"), None)
    if text is None:
        return Invalid(custom_id, "no text content block in the response")

    model_cls = SummaryOut if custom_id == "summary" else ClauseOut
    try:
        parsed = model_cls.model_validate_json(text)
    except ValidationError as error:
        return Invalid(custom_id, str(error))
    return Applied(custom_id, parsed)


def apply(bill: Bill, outcomes: list[Outcome]) -> int:
    """Write every `Applied` outcome onto `bill`, in place. Returns how many were.

    Skips anything already `"reviewed"` — a human's accepted text is never
    overwritten by a fresh draft. That includes group entries: a new summary
    result replaces the summary and the NON-reviewed group entries, but every
    group entry a human has reviewed is kept, even if the new output leaves
    that group out. A `"sample"` item IS overwritten: that's the whole point of
    slice 2a's placeholders, they exist to be replaced.
    `suggested_articles` is always forced to `[]` here; validating suggested
    article numbers against the constitution is slice 10's job, not this one's.
    """
    clauses_by_id = {clause.id: clause for clause in bill.clauses}
    applied = 0
    for outcome in outcomes:
        if not isinstance(outcome, Applied):
            continue

        if outcome.custom_id == "summary":
            if bill.summary is not None and bill.summary.review.status == "reviewed":
                continue
            summary_out: SummaryOut = outcome.parsed
            bill.summary = Summary(
                text=Tri(en=summary_out.summary.en, sw=summary_out.summary.sw, sheng=summary_out.summary.sheng),
                review=Review(status="draft"),
            )
            # A reviewed group entry is a human's accepted text: keep it, matched
            # by group id, in place of that group's new draft.
            reviewed = {entry.group: entry for entry in bill.groups if entry.review.status == "reviewed"}
            groups: list[GroupEntry] = []
            for group in summary_out.groups:
                if group.group in reviewed:
                    groups.append(reviewed.pop(group.group))
                else:
                    groups.append(
                        GroupEntry(
                            group=group.group,
                            text=Tri(en=group.text.en, sw=group.text.sw, sheng=group.text.sheng),
                            clause_ids=list(group.clause_ids),
                            review=Review(status="draft"),
                        )
                    )
            # Reviewed groups the new output left out are still kept (at the end).
            groups.extend(reviewed.values())
            bill.groups = groups
            applied += 1
            continue

        clause = clauses_by_id.get(outcome.custom_id)
        if clause is None or clause.review.status == "reviewed":
            continue
        clause_out: ClauseOut = outcome.parsed
        clause.explanation = Tri(
            en=clause_out.explanation.en, sw=clause_out.explanation.sw, sheng=clause_out.explanation.sheng
        )
        clause.review = Review(status="draft")
        clause.neutral_check = NeutralCheck(ok=clause_out.neutral_check.ok, note=clause_out.neutral_check.note)
        clause.groups = list(clause_out.groups)
        clause.suggested_articles = []
        applied += 1
    return applied


# --------------------------------------------------------------------------
# Self-test: proves the above offline, against a recorded fixture. No network,
# no Anthropic client is ever constructed.
# --------------------------------------------------------------------------


def self_test(repo_root: Path) -> bool:
    import re

    check = Checker()

    real_path = bill_path(repo_root, FIXTURE_BILL_ID)
    sha_before = sha256_file(real_path)
    bill = load_bill(real_path)

    # --- request building --------------------------------------------------
    requests = build_batch(repo_root, bill)
    check("request count == len(clauses) + 1", len(requests) == len(bill.clauses) + 1)

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
    # employee arrives as a fresh draft.
    def tri_out(text: str) -> TriOut:
        return TriOut(en=text, sw=text, sheng=text)

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
            summary=tri_out("new summary"),
            groups=[
                GroupOut(group="trader", text=tri_out("new trader draft"), clause_ids=["c3"]),
                GroupOut(group="employee", text=tri_out("new employee draft"), clause_ids=["c4"]),
            ],
        ),
    )
    trader_before, student_before = human_trader.model_copy(deep=True), human_student.model_copy(deep=True)
    grouped = reset_bill(repo_root)
    grouped.groups = [human_trader, draft_transport, human_student]
    grouped_applied = apply(grouped, [summary_result])
    by_group = {entry.group: entry for entry in grouped.groups}
    check("summary apply: reported 1 applied", grouped_applied == 1, str(grouped_applied))
    check(
        "summary apply: the reviewed trader entry keeps the human's words (not overwritten)",
        by_group.get("trader") == trader_before,
        str(by_group.get("trader")),
    )
    check(
        "summary apply: the reviewed student entry is kept although the new output omitted it",
        by_group.get("student") == student_before,
        str(sorted(by_group)),
    )
    check(
        "summary apply: the draft transport entry is replaced, and employee arrives as a draft",
        "transport" not in by_group
        and by_group.get("employee") is not None
        and by_group["employee"].review.status == "draft"
        and by_group["employee"].text.en == "new employee draft",
        str(sorted(by_group)),
    )
    check("summary apply: the summary itself is the new draft", grouped.summary.text.en == "new summary" and grouped.summary.review.status == "draft")

    # --- nothing written -----------------------------------------------------
    check("the real bill file's SHA-256 is unchanged", sha256_file(real_path) == sha_before)

    return check.finish()


# --------------------------------------------------------------------------
# Live entry point. NOT exercised in slice 2a — no ANTHROPIC_API_KEY and no
# network egress exist in this environment. Written so slice 2b has
# something real to run the moment a key exists, not left as a stub.
# --------------------------------------------------------------------------


def run(repo_root: Path, bill_id: str) -> None:
    """Submit a batch for `bill_id`, poll it to completion, apply the results.

    [UNKNOWN — unverified]: this function has never actually been run. It is
    built directly from the installed SDK's own type signatures (confirmed
    2026-09-25 against anthropic==1.8.0), not from guesswork, but "the types
    line up" is not the same claim as "this has executed against the real
    API". Treat the first live run as the actual test of this function.
    """
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    path = bill_path(repo_root, bill_id)
    bill = load_bill(path)

    requests = build_batch(repo_root, bill)
    batch = client.messages.batches.create(requests=requests)
    print(f"submitted batch {batch.id} ({len(requests)} requests)")

    while batch.processing_status != "ended":
        time.sleep(10)
        batch = client.messages.batches.retrieve(batch.id)
        print(f"  status: {batch.processing_status}")

    outcomes: list[Outcome] = []
    needs_direct: list[str] = []
    for response in client.messages.batches.results(batch.id):
        outcome = parse_batch_line(response.model_dump(mode="json"))
        if isinstance(outcome, NeedsDirect):
            needs_direct.append(outcome.custom_id)
        outcomes.append(outcome)

    # Refusals are retried one at a time, directly, with the fallback on —
    # never inside a batch, which rejects the fallback option outright.
    by_custom_id = {request["custom_id"]: request["params"] for request in requests}
    for custom_id in needs_direct:
        params = direct_params(by_custom_id[custom_id])
        # `client.beta.messages.parse` builds its own schema from
        # `output_format` and raises if `output_config.format` is ALSO set —
        # drop the pre-built schema here and hand it a Pydantic class instead,
        # which is what actually populates `.parsed` on the response.
        params.pop("output_config", None)
        model_cls = SummaryOut if custom_id == "summary" else ClauseOut
        parsed_message = client.beta.messages.parse(output_format=model_cls, **params)
        outcomes = [o for o in outcomes if not (isinstance(o, NeedsDirect) and o.custom_id == custom_id)]
        outcomes.append(Applied(custom_id, parsed_message.parsed))

    applied = apply(bill, outcomes)
    save_bill(bill, path)
    print(f"applied {applied} results (status: draft)")
