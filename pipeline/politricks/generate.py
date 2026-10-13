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
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": EFFORT},
    }


def build_summary_params(repo_root: Path, bill: Bill) -> dict:
    """The Messages API params for the bill's overall summary + group blurbs.

    Sends every section's full text, not just its heading: a summary and
    group notes written from headings alone would be guesses. The Finance Act
    adds about 14k input tokens to this one request.
    """
    schema = SummaryOut.model_json_schema()
    assert _all_objects_forbid_additional(schema), "SummaryOut schema must forbid extra properties everywhere"
    sections = "\n\n".join(
        f'<section id="{clause.id}">\n{clause.number}. {clause.heading}\n{clause.text}\n</section>'
        for clause in bill.clauses
    )
    user_text = f"Bill: {bill.title}\n\nThis is the bill summary request. Every section of the bill:\n\n{sections}"
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
            seen: set[str] = set()
            for group in summary_out.groups:
                # The model can repeat a group, or name a clause id this bill
                # doesn't have. Neither may reach the site: keep the first
                # entry per group, and only real ids (in order, no repeats).
                if group.group in seen:
                    print(f"  note: the summary named group {group.group!r} twice; kept the first")
                    continue
                seen.add(group.group)
                if group.group in reviewed:
                    groups.append(reviewed.pop(group.group))
                    continue
                known = [cid for cid in dict.fromkeys(group.clause_ids) if cid in clauses_by_id]
                unknown = [cid for cid in group.clause_ids if cid not in clauses_by_id]
                if unknown:
                    print(f"  note: summary group {group.group!r} named unknown clause id(s) {', '.join(unknown)}; dropped")
                if not known:
                    print(f"  note: summary group {group.group!r} names no section of this bill; left out")
                    continue
                groups.append(
                    GroupEntry(
                        group=group.group,
                        text=Tri(en=group.text.en, sw=group.text.sw, sheng=group.text.sheng),
                        clause_ids=known,
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
        clause.groups = list(dict.fromkeys(clause_out.groups))  # no repeats
        clause.suggested_articles = []
        applied += 1
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


def _run_offline(repo_root: Path, lines: list[dict], parse, batch_id: str | None = None) -> SimpleNamespace:
    """Run run() in a temp copy of the repo against a fake client.

    The temp repo holds only what run() reads: the explainer prompt and the
    test bill (`reset_bill`, never the real file). Its exceptions are caught
    and returned as the code, so a crash fails a check instead of the test.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        prompts = root / "pipeline" / "prompts"
        prompts.mkdir(parents=True)
        shutil.copy(repo_root / "pipeline" / "prompts" / "explainer.md", prompts)
        save_bill(reset_bill(repo_root), bill_path(root, FIXTURE_BILL_ID))

        client = _FakeClient(lines, parse)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = run(root, FIXTURE_BILL_ID, batch_id=batch_id, client=client, poll_seconds=0)
            except Exception as error:
                code = f"raised {type(error).__name__}: {error}"

        records = root.joinpath(*BATCH_RECORDS)
        id_file = records / f"{FIXTURE_BILL_ID}.batch-id"
        raw_file = records / f"{_FAKE_BATCH_ID}.jsonl"
        return SimpleNamespace(
            code=code,
            out=out.getvalue(),
            bill=load_bill(bill_path(root, FIXTURE_BILL_ID)),
            created=client.created,
            id_saved=id_file.read_text(encoding="utf-8").strip() if id_file.exists() else None,
            raw_lines=len(raw_file.read_text(encoding="utf-8").splitlines()) if raw_file.exists() else 0,
        )


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

    # --- the summary request sees section text, not just headings ------------
    summary_request = next(request for request in requests if request["custom_id"] == "summary")
    summary_user = summary_request["params"]["messages"][0]["content"]
    longest = max(bill.clauses, key=lambda clause: len(clause.text))
    check(
        "summary request carries every section's id and full text",
        all(f'<section id="{clause.id}">' in summary_user for clause in bill.clauses) and longest.text in summary_user,
    )

    # --- a model's repeated groups and invented clause ids never get through -
    messy = Applied(
        "summary",
        SummaryOut(
            summary=tri_out("summary"),
            groups=[
                GroupOut(group="employee", text=tri_out("first employee"), clause_ids=["c3", "c999", "c3"]),
                GroupOut(group="employee", text=tri_out("second employee"), clause_ids=["c4"]),
                GroupOut(group="student", text=tri_out("made-up only"), clause_ids=["c999"]),
            ],
        ),
    )
    cleaned = reset_bill(repo_root)
    with contextlib.redirect_stdout(io.StringIO()) as notes:
        apply(cleaned, [messy])
    by_group = {entry.group: entry for entry in cleaned.groups}
    check(
        "summary apply: a repeated group keeps only its first entry",
        [entry.group for entry in cleaned.groups].count("employee") == 1 and by_group["employee"].text.en == "first employee",
        str([entry.group for entry in cleaned.groups]),
    )
    check(
        "summary apply: unknown clause ids are dropped, and repeats removed",
        by_group["employee"].clause_ids == ["c3"],
        str(by_group["employee"].clause_ids),
    )
    check("summary apply: a group naming no real section is left out", "student" not in by_group, str(sorted(by_group)))
    # 4 notes: employee's c999, employee repeated, student's c999, student left out.
    check("summary apply: each repair is reported", notes.getvalue().count("note:") == 4, notes.getvalue())

    # --- run() end to end, offline -------------------------------------------
    # A fake client stands in for the API, and a temp folder for the repo, so
    # run()'s own code is what's tested: the saved batch id, the raw results
    # file, resume, the refusal retry and its failures, and the exit code.
    # Fixture lines: c3, c4 applied; c5 refused; c6 errored; c7 invalid.
    fixture_ok = next(outcome.parsed for outcome in outcomes if isinstance(outcome, Applied))
    parse_calls: list[dict] = []

    def parse_ok(**kwargs):
        parse_calls.append(kwargs)
        # Only `parsed_output`, like the real SDK object; reading `.parsed` fails.
        return SimpleNamespace(parsed_output=fixture_ok, stop_reason="end_turn", model="claude-opus-5-5")

    def parse_raises(**kwargs):
        raise ValueError("planted: the retry itself failed")

    def parse_refused(**kwargs):
        return SimpleNamespace(parsed_output=None, stop_reason="refusal", model=MODEL)

    first_three = lines[:3]  # c3, c4, c5 (the refusal)

    a = _run_offline(repo_root, first_three, parse_ok)
    check("run: a good retry gives exit 0", a.code == 0, f"{a.code} / {a.out}")
    check("run: one batch submitted, with every request", a.created == [len(bill.clauses) + 1], str(a.created))
    check("run: the batch id is saved to disk the moment it's submitted", a.id_saved == _FAKE_BATCH_ID, str(a.id_saved))
    check("run: the raw results are saved (one line per result)", a.raw_lines == 3, str(a.raw_lines))
    c5 = next(clause for clause in a.bill.clauses if clause.id == "c5")
    check("run: the retried clause is saved as a draft", c5.explanation is not None and c5.review.status == "draft")
    check("run: it says which model answered the retry", "c5 directly: answered by claude-opus-5-5" in a.out, a.out)
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

    b = _run_offline(repo_root, lines, parse_raises)
    check("run: a retry that raises doesn't crash the run (exit 1)", b.code == 1, f"{b.code} / {b.out}")
    saved = {clause.id for clause in b.bill.clauses if clause.explanation is not None and clause.review.status == "draft"}
    check("run: the other results are still saved", {"c3", "c4"} <= saved, str(sorted(saved)))
    check(
        "run: every request not applied is listed",
        all(f"NOT APPLIED {cid}" in b.out for cid in ("c5", "c6", "c7")),
        b.out,
    )
    check("run: the batch error keeps the API's type and message", "c6: errored: api_error: internal server error" in b.out, b.out)

    c = _run_offline(repo_root, first_three, parse_refused)
    check("run: a retry that's refused again is listed, not applied as None (exit 1)",
          c.code == 1 and "NOT APPLIED c5: direct retry gave no usable output" in c.out, f"{c.code} / {c.out}")

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


def _retry_direct(client, custom_id: str, params: dict) -> Outcome:
    """Retry one refused request directly, with the server-side fallback on.

    Never raises: anything that goes wrong becomes an `Errored`, so one bad
    retry can't throw away every other result the batch already paid for.
    """
    model_cls = SummaryOut if custom_id == "summary" else ClauseOut
    try:
        message = client.beta.messages.parse(output_format=model_cls, **retry_params(params))
    except Exception as error:  # a second refusal, truncated JSON, a network drop...
        return Errored(custom_id, f"direct retry failed: {type(error).__name__}: {error}")
    # The SDK puts the validated object on `parsed_output` (anthropic 1.8.0,
    # types/beta/parsed_beta_message.py). There is no `.parsed`.
    if message.stop_reason in ("refusal", "max_tokens") or message.parsed_output is None:
        return Errored(custom_id, f"direct retry gave no usable output (stop_reason {message.stop_reason!r})")
    # With fallbacks on, a different model may have written this answer.
    # Say which, so the reviewer knows.
    print(f"  retried {custom_id} directly: answered by {message.model}")
    return Applied(custom_id, message.parsed_output)


def run(
    repo_root: Path,
    bill_id: str,
    batch_id: str | None = None,
    client=None,
    poll_seconds: float = 10,
) -> int:
    """Submit a batch for `bill_id` (or resume `batch_id`), wait, apply the results.

    Returns the exit code: 0 only if every request was applied, 1 if any was
    not. Each one that wasn't is listed with its reason. `client` and
    `poll_seconds` exist so the self-test can drive this offline with a fake.
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    path = bill_path(repo_root, bill_id)
    bill = load_bill(path)
    requests = build_batch(repo_root, bill)
    records = repo_root.joinpath(*BATCH_RECORDS)
    records.mkdir(parents=True, exist_ok=True)

    if batch_id is None:
        batch = client.messages.batches.create(requests=requests)
        batch_id = batch.id
        (records / f"{bill_id}.batch-id").write_text(batch_id + "\n", encoding="utf-8")
        print(f"submitted batch {batch_id} ({len(requests)} requests)")
        print(f"  if this stops, resume without paying again: politricks generate {bill_id} --batch-id {batch_id}")
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
    outcomes = [parse_batch_line(line) for line in raw]

    # Refusals are retried one at a time, directly, with the fallback on —
    # never inside a batch, which rejects the fallback option outright.
    by_custom_id = {request["custom_id"]: request["params"] for request in requests}
    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, NeedsDirect):
            params = by_custom_id.get(outcome.custom_id)
            outcomes[i] = (
                _retry_direct(client, outcome.custom_id, params)
                if params is not None
                else Errored(outcome.custom_id, "refused, and no longer in the bill to retry")
            )

    applied = apply(bill, outcomes)
    save_bill(bill, path)
    print(f"applied {applied} results (status: draft)")

    failures = [outcome for outcome in outcomes if not isinstance(outcome, Applied)]
    for failure in failures:
        print(f"  NOT APPLIED {failure.custom_id}: {getattr(failure, 'reason', type(failure).__name__)}")
    if failures:
        print(f"{len(failures)} of {len(outcomes)} request(s) not applied. Raw results: {raw_path}")
        return 1
    return 0
