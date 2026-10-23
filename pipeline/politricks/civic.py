"""Civic facts: validate the hand-entered YAML, have Claude TRANSLATE it, and write
it into the bill (devpost/spec.md > Civic Data).

`politricks civic <bill-id>` reads `pipeline/civic/<bill-id>.yaml` (English
facts a human drafted and checked, each with a `source_url`) and fills the bill
JSON's `participation`, `stage_next` and `challenge`. Claude never writes a
fact here: it only translates the English sentences into Kiswahili and Sheng,
through `pipeline/prompts/translator.md`. Dates, channels (emails, offices,
P.O. Boxes), URLs and the `open` flag are copied from the YAML as they are.

What it does, in order:
1. VALIDATE the YAML with strict Pydantic models. A fact a reader will see
   without a non-empty https `source_url`, a date that isn't ISO, or a field the
   format doesn't know, fails the run (exit 1) and names the field.
2. PLAN each block against what the bill already holds. A block a human has
   reviewed, or hand-edited, is never touched (same rule as generate's
   `is_protected`). A block whose English and facts are unchanged is left alone.
3. TRANSLATE what is left in ONE direct call (generate's `_call_direct`: same
   model, effort, refusal fallback and usage log). Each string comes back as
   `sw` and `sheng`; the `en` is copied from the YAML, never from the model.
   A translation is thrown out if it drops a number, an email address or a
   P.O. Box, keeps an em dash, or returns the English unchanged.
4. WRITE the block, as a draft, under the bill's lock with `save_bill`. Each
   block carries ONE `review` (spec > Data Model), fingerprinted by
   `civic_sha256` below, the way `generate` fingerprints its drafts. A banned
   phrase in the result is reported and exits 1 (the text is saved: lint and
   the review walk are where it gets fixed).

Re-running with the same YAML and the same drafts makes no API call and
writes no byte.

FINDING (2026-10-23, see the task report): `politricks review` cannot walk
these blocks yet. `review._candidates` offers only clauses, the summary and
group entries, and `models.current_sha256` takes only those three. This file
therefore carries its own fingerprint (`civic_sha256`) and does NOT touch
`models.py` or `review.py`; those changes are the lead's to decide.

`politricks civic --self-test` proves all of this offline, in a temp folder,
with a fake client. `--dry-run` validates and prints the plan: no API call, no
write, no lock.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictBool, ValidationError

from . import generate
from .hashing import REVIEW_HASH_VERSION, normalise, review_sha256, sha256_file
from .lint import LintError, _pattern, iter_texts, lint_bill, load_phrases
from .models import Bill, Review, ShengSwap, Tri, bill_path, load_bill, save_bill
from .reviewlock import BillLocked, bill_lock
from .selftest import Checker

_STRICT = ConfigDict(extra="forbid")

KINDS = ("participation", "stage_next", "challenge")

# The id the one translation call goes by in the usage log
# (`pipeline/.cache/batches/<bill-id>.direct.jsonl`, shared with generate).
CALL_ID = "civic"


class CivicError(Exception):
    """The civic data can't be used. `problems` is one line per fault, each
    naming the field (or file) at fault."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# --------------------------------------------------------------------------
# The YAML (what a human writes): strict models
# --------------------------------------------------------------------------


def _iso_date(value: object) -> str:
    # YAML turns an unquoted 2026-09-28 into a date object; a quoted one stays
    # a string. Both are fine. A datetime, a number or "28/09/2026" is not.
    if isinstance(value, datetime):
        raise ValueError("must be a date (YYYY-MM-DD), not a date with a time")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{value!r} is not a real calendar date") from None
        return value
    raise ValueError(f"must be an ISO date, YYYY-MM-DD (got {value!r})")


def _https_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("every fact needs a non-empty https source_url")
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or re.search(r"\s", url):
        raise ValueError(f"source_url must be an https URL with a host (got {value!r})")
    return url


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be non-empty text")
    if "</text" in value.lower():
        # The translation request wraps each string in <text>...</text>.
        raise ValueError("must not contain '</text'")
    return value.strip()


IsoDate = Annotated[str, BeforeValidator(_iso_date)]
HttpsUrl = Annotated[str, BeforeValidator(_https_url)]
Text = Annotated[str, BeforeValidator(_text)]


class Channel(BaseModel):
    """One way to send views. Copied as written: never translated."""

    model_config = _STRICT
    type: Literal["email", "portal", "venue"]
    value: Text


class YamlParticipation(BaseModel):
    model_config = _STRICT
    open: StrictBool
    deadline: Optional[IsoDate]  # required key, null allowed (spec: ISO-date | null)
    deadline_time: Optional[Text] = None  # an addition to the spec's shape (drafted YAMLs carry it)
    channels: list[Channel]
    requirements: Text
    source_url: HttpsUrl
    checked_at: IsoDate


class YamlStageNext(BaseModel):
    model_config = _STRICT
    text: Text
    source_url: HttpsUrl


class YamlAvenue(BaseModel):
    model_config = _STRICT
    name: Text
    text: Text
    source_url: HttpsUrl


class YamlChallenge(BaseModel):
    model_config = _STRICT
    avenues: list[YamlAvenue]
    checked_at: IsoDate


class CivicYaml(BaseModel):
    """A whole civic file. A part that is absent or null means "the bill has none"."""

    model_config = _STRICT
    participation: Optional[YamlParticipation] = None
    stage_next: Optional[YamlStageNext] = None
    challenge: Optional[YamlChallenge] = None


def _where(loc: tuple) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def format_errors(error: ValidationError) -> list[str]:
    lines = []
    for item in error.errors():
        where = _where(item["loc"]) or "(top level)"
        if item["type"] == "missing":
            reason = "missing"
        elif item["type"] == "extra_forbidden":
            reason = "unknown field (not part of the civic format)"
        else:
            reason = item["msg"].removeprefix("Value error, ")
        lines.append(f"{where}: {reason}")
    return lines


def civic_path(repo_root: Path, bill_id: str) -> Path:
    return repo_root / "pipeline" / "civic" / f"{bill_id}.yaml"


def parse_civic(text: str, label: str = "civic YAML") -> CivicYaml:
    """Validate YAML text. Raises CivicError naming each faulty field."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise CivicError([f"{label}: not valid YAML ({str(error).splitlines()[0]})"]) from error
    if not isinstance(data, dict):
        raise CivicError([f"{label}: must be a mapping with participation / stage_next / challenge"])
    try:
        civic = CivicYaml.model_validate(data)
    except ValidationError as error:
        raise CivicError(format_errors(error)) from error
    if civic.challenge is not None and not civic.challenge.avenues:
        raise CivicError(["challenge.avenues: needs at least one avenue (or leave challenge null)"])
    return civic


def load_civic_yaml(path: Path) -> CivicYaml:
    if not path.exists():
        raise CivicError([f"no civic file at {path.as_posix()}"])
    return parse_civic(path.read_text(encoding="utf-8-sig"), path.name)


def check_against_bill(civic: CivicYaml, bill: Bill) -> list[str]:
    """Faults that need the bill to see (spec > Data Model: `challenge` is for passed bills only)."""
    problems = []
    if civic.challenge is not None and bill.status != "passed":
        problems.append(f"challenge: only a passed bill has avenues to challenge; {bill.id} is {bill.status}")
    return problems


# --------------------------------------------------------------------------
# What is written into the bill (the blocks): strict models
# --------------------------------------------------------------------------
# One `review` per block, as spec > Data Model says. Disagreements with the
# spec's shapes are listed in the task report, not here.


class ParticipationBlock(BaseModel):
    model_config = _STRICT
    open: bool
    deadline: Optional[str]
    deadline_time: Optional[str]
    channels: list[Channel]
    requirements: Tri
    source_url: str
    checked_at: str
    sheng_swaps: list[ShengSwap] = []
    review: Review


class StageNextBlock(BaseModel):
    model_config = _STRICT
    text: Tri
    source_url: str
    sheng_swaps: list[ShengSwap] = []
    review: Review


class AvenueBlock(BaseModel):
    model_config = _STRICT
    name: Tri
    text: Tri
    source_url: str


class ChallengeBlock(BaseModel):
    model_config = _STRICT
    avenues: list[AvenueBlock]
    checked_at: str
    sheng_swaps: list[ShengSwap] = []
    review: Review


BLOCK_MODELS: dict[str, type[BaseModel]] = {
    "participation": ParticipationBlock,
    "stage_next": StageNextBlock,
    "challenge": ChallengeBlock,
}


# --------------------------------------------------------------------------
# The fingerprint of a civic block
# --------------------------------------------------------------------------


def _tri_parts(tri: Tri) -> list[str]:
    return [tri.en, tri.sw, tri.sheng]


def civic_sha256(kind: str, block: BaseModel) -> str:
    """The fingerprint of a civic block as a reader sees it, `review` excluded.

    Built like `models.current_sha256` (same `review_sha256`, same
    `REVIEW_HASH_VERSION` first, each part length-prefixed), with the part
    list namespaced "civic-<kind>" so it can never equal a clause, summary or
    group fingerprint. It covers every reader-visible field, facts included
    (a deadline edited in the JSON after a review reads as changed). Left out:
    `sheng_swaps` (a review aid, as for clauses) and `review` itself.
    No TypeScript twin exists yet; `self_test` pins a known answer for it.
    """
    parts = [REVIEW_HASH_VERSION, f"civic-{kind}"]
    if isinstance(block, ParticipationBlock):
        parts += [
            "true" if block.open else "false",
            block.deadline or "",
            block.deadline_time or "",
            str(len(block.channels)),
        ]
        for channel in block.channels:
            parts += [channel.type, channel.value]
        parts += _tri_parts(block.requirements) + [block.source_url, block.checked_at]
    elif isinstance(block, StageNextBlock):
        parts += _tri_parts(block.text) + [block.source_url]
    elif isinstance(block, ChallengeBlock):
        parts += [block.checked_at, str(len(block.avenues))]
        for avenue in block.avenues:
            parts += _tri_parts(avenue.name) + _tri_parts(avenue.text) + [avenue.source_url]
    else:  # pragma: no cover
        raise TypeError(f"not a civic block: {type(block).__name__}")
    return review_sha256(parts)


def is_hand_edited(kind: str, block: BaseModel) -> bool:
    """Same rule as `models.is_hand_edited`: a draft whose fingerprint is
    HAND_EDITED or no longer matches its content. No fingerprint = untouched."""
    review = block.review  # type: ignore[attr-defined]
    return review.status == "draft" and review.sha256 is not None and review.sha256 != civic_sha256(kind, block)


def is_protected(kind: str, block: BaseModel) -> bool:
    """Human work this command must never overwrite (as `models.is_protected`)."""
    return block.review.status == "reviewed" or is_hand_edited(kind, block)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# The strings to translate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    """One English string that reaches the reader, and where it goes."""

    id: str  # "participation.requirements", "challenge.avenues[1].text"
    en: str
    what: str  # a hint for the translator, never shown to a reader


def units_for(kind: str, part: Any) -> list[Unit]:
    if kind == "participation":
        return [Unit("participation.requirements", part.requirements, "how to send views, and to whom")]
    if kind == "stage_next":
        return [Unit("stage_next.text", part.text, "what happens next to the bill in Parliament")]
    units = []
    for index, avenue in enumerate(part.avenues):
        units.append(Unit(f"challenge.avenues[{index}].name", avenue.name, "the name of a way to challenge a law"))
        units.append(Unit(f"challenge.avenues[{index}].text", avenue.text, "general information on that way"))
    return units


def tris_of_block(kind: str, block: BaseModel) -> dict[str, Tri]:
    """The Tri at each unit id of a block already in the bill."""
    if isinstance(block, ParticipationBlock):
        return {"participation.requirements": block.requirements}
    if isinstance(block, StageNextBlock):
        return {"stage_next.text": block.text}
    out = {}
    for index, avenue in enumerate(block.avenues):  # type: ignore[attr-defined]
        out[f"challenge.avenues[{index}].name"] = avenue.name
        out[f"challenge.avenues[{index}].text"] = avenue.text
    return out


def facts_of_part(kind: str, part: Any) -> dict:
    """The non-translated facts a YAML part carries."""
    if kind == "participation":
        return {
            "open": part.open, "deadline": part.deadline, "deadline_time": part.deadline_time,
            "channels": [channel.model_dump() for channel in part.channels],
            "source_url": part.source_url, "checked_at": part.checked_at,
        }
    if kind == "stage_next":
        return {"source_url": part.source_url}
    return {"checked_at": part.checked_at, "avenues": [{"source_url": avenue.source_url} for avenue in part.avenues]}


def facts_of_block(kind: str, block: BaseModel) -> dict:
    if isinstance(block, ParticipationBlock):
        return {
            "open": block.open, "deadline": block.deadline, "deadline_time": block.deadline_time,
            "channels": [channel.model_dump() for channel in block.channels],
            "source_url": block.source_url, "checked_at": block.checked_at,
        }
    if isinstance(block, StageNextBlock):
        return {"source_url": block.source_url}
    return {  # ChallengeBlock
        "checked_at": block.checked_at,  # type: ignore[attr-defined]
        "avenues": [{"source_url": avenue.source_url} for avenue in block.avenues],  # type: ignore[attr-defined]
    }


def assemble(kind: str, part: Any, tris: dict[str, Tri], swaps: list[ShengSwap]) -> dict:
    """The block as it is written to the bill: YAML facts + translated Tris +
    a draft `review` carrying the fingerprint. Stamped LAST, after every
    other field is set, so the fingerprint matches what is on disk."""
    if kind == "participation":
        block: BaseModel = ParticipationBlock(
            open=part.open, deadline=part.deadline, deadline_time=part.deadline_time,
            channels=list(part.channels), requirements=tris["participation.requirements"],
            source_url=part.source_url, checked_at=part.checked_at,
            sheng_swaps=swaps, review=Review(status="draft"),
        )
    elif kind == "stage_next":
        block = StageNextBlock(
            text=tris["stage_next.text"], source_url=part.source_url, sheng_swaps=swaps, review=Review(status="draft")
        )
    else:
        block = ChallengeBlock(
            avenues=[
                AvenueBlock(
                    name=tris[f"challenge.avenues[{index}].name"],
                    text=tris[f"challenge.avenues[{index}].text"],
                    source_url=avenue.source_url,
                )
                for index, avenue in enumerate(part.avenues)
            ],
            checked_at=part.checked_at, sheng_swaps=swaps, review=Review(status="draft"),
        )
    block.review = Review(status="draft", sha256=civic_sha256(kind, block))  # type: ignore[attr-defined]
    return block.model_dump(mode="json")


# --------------------------------------------------------------------------
# The translation request and its answer
# --------------------------------------------------------------------------


class ItemOut(BaseModel):
    # `en` is deliberately absent: the English comes from the YAML, never from the model.
    model_config = _STRICT
    id: str
    sw: str
    sheng: str
    sheng_swaps: list[generate.SwapOut]


class TranslationsOut(BaseModel):
    model_config = _STRICT
    items: list[ItemOut]


def _translator_prompt(repo_root: Path) -> str:
    return (repo_root / "pipeline" / "prompts" / "translator.md").read_text(encoding="utf-8")


def build_params(repo_root: Path, bill: Bill, units: list[Unit]) -> dict:
    """The Messages API params for translating `units`. Same shape, model and
    effort as `generate.build_params`; only the prompt and schema differ."""
    schema = TranslationsOut.model_json_schema()
    assert generate._all_objects_forbid_additional(schema), "TranslationsOut schema must forbid extra properties"
    texts = "\n\n".join(f'<text id="{unit.id}" what="{unit.what}">\n{unit.en}\n</text>' for unit in units)
    user_text = (
        f"Bill: {bill.title}\n\n"
        "Translate each text below into Kiswahili and Sheng. Return one item for every id.\n\n" + texts
    )
    return {
        "model": generate.MODEL,
        "max_tokens": generate.MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [{"type": "text", "text": _translator_prompt(repo_root), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": generate.EFFORT},
    }


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_POBOX = re.compile(r"P\.\s?O\.\s?Box\s+\d[\d-]*", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)]+")
_NUMBER = re.compile(r"\d+(?:[.:,\-]\d+)*")
_DASHES = ("—", "–")


def translation_problems(unit: Unit, item: ItemOut) -> list[str]:
    """Why a translation can't be used (empty = fine). These are the things the
    prompt says never to change, checked in code rather than hoped for."""
    problems = []
    for lang, text in (("sw", item.sw), ("sheng", item.sheng)):
        if not text.strip():
            problems.append(f"{lang} is empty")
            continue
        for dash in _DASHES:
            if dash in text and dash not in unit.en:
                problems.append(f"{lang} has a dash character (U+{ord(dash):04X})")
        must_keep = (
            _EMAIL.findall(unit.en) + _POBOX.findall(unit.en) + _URL.findall(unit.en) + _NUMBER.findall(unit.en)
        )
        lost = [token for token in dict.fromkeys(must_keep) if token not in text]
        if lost:
            problems.append(f"{lang} dropped or changed: {', '.join(lost)}")
    if normalise(item.sw) == normalise(unit.en):
        problems.append("sw is the English unchanged")
    return problems


def collect_translations(
    units: list[Unit], outcome: generate.Outcome
) -> tuple[dict[str, tuple[Tri, list[ShengSwap]]], dict[str, str]]:
    """Match the model's answer to the strings that were asked for.

    Returns (good translations by unit id, the reason for each id that has none).
    A missing, repeated or unusable item fails that id only."""
    if not isinstance(outcome, generate.Applied):
        reason = getattr(outcome, "reason", type(outcome).__name__)
        return {}, {unit.id: reason for unit in units}
    by_unit = {unit.id: unit for unit in units}
    seen: dict[str, list[ItemOut]] = {}
    for item in outcome.parsed.items:
        seen.setdefault(item.id, []).append(item)
    good: dict[str, tuple[Tri, list[ShengSwap]]] = {}
    bad: dict[str, str] = {}
    for unit in units:
        items = seen.get(unit.id, [])
        if not items:
            bad[unit.id] = "no result came back for this text"
        elif len(items) > 1:
            bad[unit.id] = "the answer repeated this id"
        else:
            problems = translation_problems(unit, items[0])
            if problems:
                bad[unit.id] = "; ".join(problems)
            else:
                good[unit.id] = (
                    Tri(en=unit.en, sw=items[0].sw.strip(), sheng=items[0].sheng.strip()),
                    [ShengSwap(word=swap.word, english=swap.english) for swap in items[0].sheng_swaps],
                )
    for stray in sorted(set(seen) - set(by_unit)):
        print(f"  note: the answer had an id nobody asked for ({stray!r}); ignored")
    return good, bad


# --------------------------------------------------------------------------
# Planning: what each block needs
# --------------------------------------------------------------------------


@dataclass
class BlockPlan:
    kind: str
    # none      : not in the YAML, nothing in the bill
    # kept      : a human's block stays exactly as it is
    # clear     : not in the YAML; the bill's untouched draft is removed
    # unchanged : the bill already holds exactly this
    # write     : facts changed, strings didn't: rebuilt from the saved translations (no API call)
    # translate : strings are new or changed: needs the translator
    action: str
    note: str = ""
    part: Any = None
    units: list[Unit] = field(default_factory=list)
    new: Optional[dict] = None  # the block to write, once known


def _parse_existing(kind: str, raw: Optional[dict]) -> Optional[BaseModel]:
    if raw is None:
        return None
    try:
        return BLOCK_MODELS[kind].model_validate(raw)
    except ValidationError as error:
        raise CivicError(
            [f"{kind}: the block already in the bill doesn't have the civic shape, so it is not touched "
             f"({'; '.join(format_errors(error)[:3])})"]
        ) from error


def plan_block(kind: str, part: Any, raw: Optional[dict], force: bool) -> BlockPlan:
    existing = _parse_existing(kind, raw)
    if part is None:
        if existing is None:
            return BlockPlan(kind, "none", "not in the YAML, and the bill has none")
        if is_protected(kind, existing):
            return BlockPlan(kind, "kept", "not in the YAML, but a human reviewed or edited the bill's block: kept")
        return BlockPlan(kind, "clear", "not in the YAML: the bill's untouched draft is removed")
    units = units_for(kind, part)
    if existing is not None and is_protected(kind, existing):
        wanted_en = {unit.id: unit.en for unit in units}
        have_en = {key: tri.en for key, tri in tris_of_block(kind, existing).items()}
        differs = wanted_en != have_en or facts_of_part(kind, part) != facts_of_block(kind, existing)
        why = "reviewed" if existing.review.status == "reviewed" else "hand-edited"  # type: ignore[attr-defined]
        note = f"a human's block ({why}): kept"
        if differs:
            note += "; the YAML differs from it, and it is NOT updated (clear its review first if the YAML should win)"
        return BlockPlan(kind, "kept", note, part, units)
    if existing is not None and not force:
        have = tris_of_block(kind, existing)
        if set(have) == {unit.id for unit in units} and all(
            have[unit.id].en == unit.en and have[unit.id].sw.strip() and have[unit.id].sheng.strip() for unit in units
        ):
            new = assemble(kind, part, have, list(existing.sheng_swaps))  # type: ignore[attr-defined]
            if new == raw:
                return BlockPlan(kind, "unchanged", "already up to date", part, units)
            return BlockPlan(kind, "write", "same sentences, other facts: no translation needed", part, units, new)
    return BlockPlan(kind, "translate", f"{len(units)} text(s) to translate", part, units)


def english_banned(units: list[Unit], phrases: list[str]) -> list[str]:
    """Banned phrases already in the English a human wrote (caught before any API spend)."""
    patterns = [(phrase, _pattern(phrase)) for phrase in phrases]
    return [
        f"{unit.id}: the English has the banned phrase {phrase!r}"
        for unit in units
        for phrase, pattern in patterns
        if pattern.search(normalise(unit.en))
    ]


def civic_findings(bill: Bill, phrases: list[str]):
    """lint's findings on the civic fields only (the bill's other texts are `lint`'s business)."""
    return [
        finding for finding in lint_bill(bill, phrases)
        if finding.item.split(".")[0].split("[")[0] in KINDS
    ]


# --------------------------------------------------------------------------
# `politricks civic`
# --------------------------------------------------------------------------


def _preview(text: str, width: int = 72) -> str:
    text = normalise(text)
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def run(repo_root: Path, bill_id: str, dry_run: bool = False, force: bool = False, client=None) -> int:
    """Returns the exit code: 0 done (or already up to date), 1 anything wrong.

    The YAML is validated before the bill's lock is taken and before any client
    exists, so a bad file costs nothing. `--dry-run` takes no lock, makes no
    call and writes nothing. `client` exists so the self-test can pass a fake.
    """
    try:
        path = bill_path(repo_root, bill_id)
    except ValueError as error:
        print(f"FAILED: {error}")
        return 1
    try:
        civic = load_civic_yaml(civic_path(repo_root, bill_id))
        phrases = load_phrases(repo_root)
    except CivicError as error:
        print(f"FAILED: {bill_id}: the civic file is not valid; nothing was sent or changed")
        for problem in error.problems:
            print(f"  {problem}")
        return 1
    except LintError as error:
        print(f"FAILED: {error}")
        return 1
    if not path.exists():
        print(f"FAILED: no bill file at {path}")
        return 1
    try:
        if dry_run:
            return _run(repo_root, bill_id, path, civic, phrases, True, force, client)
        with bill_lock(path):
            return _run(repo_root, bill_id, path, civic, phrases, False, force, client)
    except BillLocked as error:
        print(f"FAILED: {error} Nothing was sent.")
        return 1


def _run(repo_root: Path, bill_id: str, path: Path, civic: CivicYaml, phrases: list[str],
         dry_run: bool, force: bool, client) -> int:
    """run()'s body, with the bill's lock already held (unless it's a dry run)."""
    try:
        bill = load_bill(path)
    except (ValueError, OSError) as error:
        print(f"FAILED: {error}")
        return 1
    problems = check_against_bill(civic, bill)
    english = [unit for kind in KINDS if getattr(civic, kind) is not None for unit in units_for(kind, getattr(civic, kind))]
    problems += english_banned(english, phrases)
    try:
        plans = [plan_block(kind, getattr(civic, kind), getattr(bill, kind), force) for kind in KINDS]
    except CivicError as error:
        problems += error.problems
    if problems:
        print(f"FAILED: {bill_id}: nothing was sent or changed")
        for problem in problems:
            print(f"  {problem}")
        return 1

    mode = " (dry run: no API call, nothing written)" if dry_run else ""
    print(f"civic {bill_id}{mode}")
    for plan in plans:
        print(f"  {plan.kind}: {plan.action}, {plan.note}")
        if plan.part is not None and hasattr(plan.part, "source_url"):
            print(f"      source: {plan.part.source_url}")
        if plan.action == "translate":
            for unit in plan.units:
                print(f"      {unit.id}: {_preview(unit.en)}")

    asking = [unit for plan in plans if plan.action == "translate" for unit in plan.units]
    if dry_run:
        print(f"would ask Claude to translate {len(asking)} text(s) in {1 if asking else 0} call(s)")
        return 0

    failures: dict[str, str] = {}
    results: dict[str, tuple[Tri, list[ShengSwap]]] = {}
    if asking:
        if client is None:
            try:
                import anthropic

                client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
            except Exception as error:
                print(f"FAILED: cannot start the Anthropic client ({type(error).__name__}: {error}). Nothing was sent.")
                return 1
        print(f"asking Claude to translate {len(asking)} text(s) (one direct call)...")
        outcome = generate._call_direct(
            client, CALL_ID, build_params(repo_root, bill, asking),
            usage_log=generate._usage_log(repo_root, bill.id), model_cls=TranslationsOut,
        )
        results, failures = collect_translations(asking, outcome)

    changed = False
    for plan in plans:
        if plan.action == "clear":
            setattr(bill, plan.kind, None)
            changed = True
        elif plan.action == "write":
            setattr(bill, plan.kind, plan.new)
            changed = True
        elif plan.action == "translate":
            if any(unit.id in failures for unit in plan.units):
                continue  # the whole block waits: half a block is worse than the old one
            swaps: list[ShengSwap] = []
            for unit in plan.units:
                for swap in results[unit.id][1]:
                    if swap.word not in {existing.word for existing in swaps}:
                        swaps.append(swap)
            plan.new = assemble(plan.kind, plan.part, {unit.id: results[unit.id][0] for unit in plan.units}, swaps)
            setattr(bill, plan.kind, plan.new)
            changed = True
    if changed:
        save_bill(bill, path)

    wrote = [plan.kind for plan in plans if plan.action in ("write", "clear") or plan.new is not None]
    print(f"wrote {', '.join(wrote)} (status: draft)" if wrote else "wrote nothing: every block is up to date or a human's")
    code = 0
    for unit_id, reason in failures.items():
        print(f"  NOT APPLIED {unit_id}: {reason}")
        code = 1
    if failures:
        print(f"{len(failures)} text(s) not applied; the blocks holding them were left as they were. Run again.")
    findings = civic_findings(bill, phrases)
    for finding in findings:
        print(finding.line())
        code = 1
    if findings:
        print(f"{len(findings)} banned phrase(s) in the civic texts: edit them in `politricks review` after fixing the text")
    return code


# --------------------------------------------------------------------------
# Self-test: offline, in a temp folder, with a fake client. Writes nothing to the repo.
# --------------------------------------------------------------------------

PP = "public-participation-bill-2025"  # active: participation + stage_next
TE = "tertiary-education-bill-2026"  # active: participation with channels: [], stage_next
FA = "finance-act-2026"  # passed: participation/stage_next null, challenge with 3 avenues
ALL_IDS = (PP, TE, FA)

_TEXT_BLOCK = re.compile(r'<text id="([^"]+)"[^>]*>\n(.*?)\n</text>', re.DOTALL)


class _FakeClient:
    """Just enough of `anthropic.Anthropic` for generate._call_direct: records
    each call and answers from `answer(id, english) -> (sw, sheng)`."""

    def __init__(self, answer=None, error: Exception | None = None, drop: tuple[str, ...] = ()) -> None:
        self.calls: list[dict] = []
        self._answer = answer or (lambda unit_id, en: (f"SW: {en}", f"SHENG: {en}"))
        self._error = error
        self._drop = drop
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, *, output_format, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        user = kwargs["messages"][0]["content"]
        items = []
        for unit_id, en in _TEXT_BLOCK.findall(user):
            if unit_id in self._drop:
                continue
            sw, sheng = self._answer(unit_id, en)
            items.append({"id": unit_id, "sw": sw, "sheng": sheng, "sheng_swaps": []})
        parsed = output_format.model_validate({"items": items})
        return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed, model="fake-model", usage=None)


def _quiet(fn, *args, **kwargs) -> tuple[Any, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = fn(*args, **kwargs)
    return result, buffer.getvalue()


# The civic YAMLs as drafted on 2026-10-19, frozen for the self-test. The
# real files change as facts are checked (they did on 2026-10-23), and a test
# that asserted on live facts broke CI the moment they did.
FIXTURE_DIR = Path("pipeline") / "tests" / "fixtures" / "civic"


def _temp_root(repo_root: Path, tmp: Path, ids: tuple[str, ...] = ALL_IDS) -> Path:
    """A throwaway repo root holding copies of the prompts, the given bills,
    and the FROZEN civic fixtures (never the real, changing YAML)."""
    for rel in ("pipeline/prompts/translator.md", "pipeline/prompts/banned.txt"):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo_root / rel, tmp / rel)
    for bill_id in ids:
        (tmp / "pipeline" / "civic").mkdir(parents=True, exist_ok=True)
        shutil.copy(repo_root / FIXTURE_DIR / f"{bill_id}.yaml", tmp / "pipeline" / "civic" / f"{bill_id}.yaml")
        rel = f"src/data/bills/{bill_id}.json"
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo_root / rel, tmp / rel)
    return tmp


def _blank_bill(root: Path, bill_id: str) -> None:
    """The copied bill with its three civic fields emptied, whatever the real file holds."""
    bill = load_bill(bill_path(root, bill_id))
    bill.participation = bill.stage_next = bill.challenge = None
    save_bill(bill, bill_path(root, bill_id))


def _swap_yaml_text(root: Path, bill_id: str, old: str, new: str) -> None:
    path = civic_path(root, bill_id)
    text = path.read_text(encoding="utf-8")
    assert old in text, f"self-test setup: {old!r} not in {path.name}"
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def _civic_text_count(bill: Bill) -> int:
    return sum(1 for item, _lang, _text in iter_texts(bill) if item.split(".")[0].split("[")[0] in KINDS)


def self_test(repo_root: Path) -> bool:
    check = Checker()
    phrases = load_phrases(repo_root)
    watched = [bill_path(repo_root, i) for i in ALL_IDS] + [civic_path(repo_root, i) for i in ALL_IDS]
    watched.append(repo_root / "pipeline" / "prompts" / "translator.md")
    before = {path: sha256_file(path) for path in watched}
    logs = [generate._usage_log(repo_root, i) for i in ALL_IDS]
    logs_before = {log: (log.stat().st_size if log.exists() else None) for log in logs}

    # --- A. validation -------------------------------------------------------
    real = {}
    for bill_id in ALL_IDS:
        try:
            real[bill_id] = load_civic_yaml(civic_path(repo_root, bill_id))
            check(f"real {bill_id}.yaml validates", True)
        except CivicError as error:
            check(f"real {bill_id}.yaml validates", False, "; ".join(error.problems))
    # Everything below compares against the frozen fixtures, not the live files.
    real = {bill_id: load_civic_yaml(repo_root / FIXTURE_DIR / f"{bill_id}.yaml") for bill_id in ALL_IDS}
    expected_units = {PP: 2, TE: 2, FA: 6}
    for bill_id, count in expected_units.items():
        if bill_id in real:
            got = sum(len(units_for(kind, getattr(real[bill_id], kind))) for kind in KINDS if getattr(real[bill_id], kind))
            check(f"{bill_id}: {count} reader-facing English strings found", got == count, f"got {got}")

    good_part = (
        "participation:\n  open: false\n  deadline: 2026-09-28\n  channels: []\n"
        "  requirements: Send a memorandum.\n  source_url: \"https://example.org/notice\"\n  checked_at: 2026-10-19\n"
    )

    def faults(text: str) -> list[str]:
        try:
            parse_civic(text)
        except CivicError as error:
            return error.problems
        return []

    check("a clean minimal file validates", faults(good_part) == [], str(faults(good_part)))
    missing = faults(good_part.replace('  source_url: "https://example.org/notice"\n', ""))
    check("missing participation.source_url is rejected and named", any(p.startswith("participation.source_url") for p in missing), str(missing))
    missing = faults("stage_next:\n  text: It goes to the Senate next.\n")
    check("missing stage_next.source_url is rejected and named", any(p.startswith("stage_next.source_url") for p in missing), str(missing))
    avenues = (
        "challenge:\n  checked_at: 2026-10-19\n  avenues:\n"
        "    - {name: A, text: B, source_url: \"https://example.org/a\"}\n"
        "    - {name: C, text: D}\n"
    )
    missing = faults(avenues)
    check("an avenue without a source_url names challenge.avenues[1].source_url", any(p.startswith("challenge.avenues[1].source_url") for p in missing), str(missing))
    for label, bad in (("http (not https)", "http://example.org/x"), ("empty", ""), ("null", "null"), ("no host", "https://")):
        value = bad if bad in ("null",) else f'"{bad}"'
        found = faults(good_part.replace('"https://example.org/notice"', value))
        check(f"a {label} source_url is rejected and named", any(p.startswith("participation.source_url") for p in found), str(found))
    found = faults(good_part.replace("deadline: 2026-09-28", 'deadline: "28/09/2026"'))
    check("a non-ISO date is rejected and named", any(p.startswith("participation.deadline") for p in found), str(found))
    found = faults(good_part.replace("deadline: 2026-09-28", 'deadline: "2026-02-30"'))
    check("an impossible date is rejected and named", any(p.startswith("participation.deadline") for p in found), str(found))
    found = faults(good_part + "  deadline_tme: 5pm\n")
    check("an unknown field is rejected and named", any(p.startswith("participation.deadline_tme") for p in found), str(found))
    check("a participation without `open` is rejected", any(p.startswith("participation.open") for p in faults(good_part.replace("  open: false\n", ""))))
    check("`open: yes` (not a real boolean) is rejected", any(p.startswith("participation.open") for p in faults(good_part.replace("open: false", 'open: "no"'))))
    check("a non-mapping file is rejected", bool(faults("- just\n- a list\n")))

    # --- B. the request ------------------------------------------------------
    sample_bill = load_bill(bill_path(repo_root, PP))
    sample_units = units_for("participation", real[PP].participation) + units_for("stage_next", real[PP].stage_next)
    params = build_params(repo_root, sample_bill, sample_units)
    check("request uses generate's model and effort", params["model"] == generate.MODEL and params["output_config"]["effort"] == generate.EFFORT)
    check("request's system prompt is translator.md", params["system"][0]["text"] == _translator_prompt(repo_root))
    user = params["messages"][0]["content"]
    check("request carries every English string and id", all(u.en in user and f'id="{u.id}"' in user for u in sample_units))
    check("answer schema forbids extra properties", generate._all_objects_forbid_additional(params["output_config"]["format"]["schema"]))
    check("answer schema has no `en` (English is never taken from the model)", '"en"' not in str(params["output_config"]["format"]["schema"]["$defs"]["ItemOut"]["properties"]))
    prompt = _translator_prompt(repo_root)
    check("translator.md has no em dash", "—" not in prompt)
    check("translator.md forbids advice and verdict phrases", all(word in normalise(prompt) for word in ("unapaswa", "kinyume na Katiba", "you should", "inakinzana na Katiba")))

    # --- the fingerprint -----------------------------------------------------
    vector = ParticipationBlock(
        open=False, deadline="2026-09-28", deadline_time="5pm", channels=[Channel(type="email", value="a@b.go.ke")],
        requirements=Tri(en="E", sw="S", sheng="H"), source_url="https://example.org/x", checked_at="2026-10-19",
        review=Review(status="draft"),
    )
    check("civic_sha256 known answer (pins the format for a future TypeScript twin)", civic_sha256("participation", vector) == _VECTOR_PARTICIPATION, civic_sha256("participation", vector))
    other = vector.model_copy(update={"deadline": "2026-09-29"})
    check("civic_sha256 changes with a fact (deadline)", civic_sha256("participation", other) != civic_sha256("participation", vector))
    check("civic_sha256 ignores the review record", civic_sha256("participation", vector.model_copy(update={"review": Review(status="reviewed", at="2026-01-01", sha256="x")})) == civic_sha256("participation", vector))

    # --- C. translations land as drafts --------------------------------------
    with tempfile.TemporaryDirectory(prefix="politricks-civic-") as tmp_name:
        root = _temp_root(repo_root, Path(tmp_name))
        for bill_id in ALL_IDS:
            _blank_bill(root, bill_id)
        client = _FakeClient()
        outcomes = {bill_id: _quiet(run, root, bill_id, client=client) for bill_id in ALL_IDS}
        check("run exits 0 for all three bills", all(rc == 0 for rc, _ in outcomes.values()), str({k: v[0] for k, v in outcomes.items()}))
        check("one translation call per bill", len(client.calls) == 3, f"{len(client.calls)} calls")
        landed = {bill_id: load_bill(bill_path(root, bill_id)) for bill_id in ALL_IDS}
        all_drafts = True
        for bill_id in ALL_IDS:
            for kind in KINDS:
                raw = getattr(landed[bill_id], kind)
                if raw is None:
                    continue
                block = BLOCK_MODELS[kind].model_validate(raw)
                fp = civic_sha256(kind, block)
                ok = block.review.status == "draft" and block.review.sha256 == fp and re.fullmatch(r"[0-9a-f]{64}", fp) is not None
                all_drafts = all_drafts and ok
        check("every written block is a draft whose sha256 is its civic fingerprint", all_drafts)
        part = landed[PP].participation
        want = real[PP].participation
        check("participation requirements: en is the YAML's, sw/sheng come from the translator",
              part["requirements"] == {"en": want.requirements, "sw": f"SW: {want.requirements}", "sheng": f"SHENG: {want.requirements}"})
        check("channels, deadline, deadline_time, source_url, checked_at copied verbatim",
              part["channels"] == [c.model_dump() for c in want.channels] and part["deadline"] == "2026-09-28"
              and part["deadline_time"] == "5pm" and part["source_url"] == want.source_url and part["checked_at"] == "2026-10-19" and part["open"] is False)
        check("empty channels stay empty (tertiary-education)", landed[TE].participation["channels"] == [] and landed[TE].participation["deadline"] == "2026-10-02")
        check("stage_next carries its text and source_url", landed[PP].stage_next["text"]["en"] == real[PP].stage_next.text and landed[PP].stage_next["source_url"] == real[PP].stage_next.source_url)
        fa = landed[FA]
        check("finance-act: participation and stage_next stay null, challenge has 3 avenues each with a source_url",
              fa.participation is None and fa.stage_next is None and len(fa.challenge["avenues"]) == 3
              and all(a["source_url"].startswith("https://") and a["name"]["sw"].startswith("SW: ") for a in fa.challenge["avenues"]))
        # Positive control: lint must actually SEE the civic texts, or "no findings" below means nothing.
        seen = sum(_civic_text_count(b) for b in landed.values())
        check("lint walks the civic texts (positive control: 3 langs x 2+2+6 strings = 30)", seen == 30, f"saw {seen}")
        check("lint finds nothing in clean translations", all(civic_findings(b, phrases) == [] for b in landed.values()))
        check("nothing was written under the repo's usage log", all((log.stat().st_size if log.exists() else None) == size for log, size in logs_before.items()))
        check("usage was logged in the temp root", (root / "pipeline" / ".cache" / "batches" / f"{PP}.direct.jsonl").exists())

        # --- D. a second run: no call, no byte -------------------------------
        sha_one = {bill_id: sha256_file(bill_path(root, bill_id)) for bill_id in ALL_IDS}
        client_two = _FakeClient(error=RuntimeError("must not be called"))
        again = {bill_id: _quiet(run, root, bill_id, client=client_two) for bill_id in ALL_IDS}
        check("second run: exit 0 and NO call made", all(rc == 0 for rc, _ in again.values()) and not client_two.calls, f"{len(client_two.calls)} calls")
        check("second run: bill files byte-identical", {bill_id: sha256_file(bill_path(root, bill_id)) for bill_id in ALL_IDS} == sha_one)
        check("second run says each block is already up to date", all("already up to date" in out for _rc, out in again.values()))
        # Control: the same state, forced, DOES call (so "no call" above was not an artefact).
        forced = _FakeClient()
        _quiet(run, root, PP, force=True, client=forced)
        check("control: --force on the same state does call the translator", len(forced.calls) == 1)
        check("--force with a deterministic translator still gives byte-identical output", sha256_file(bill_path(root, PP)) == sha_one[PP])

        # dry run on the written state: no call, no write, no lock
        dry_client = _FakeClient(error=RuntimeError("must not be called"))
        rc, out = _quiet(run, root, PP, dry_run=True, force=True, client=dry_client)
        check("dry run: exit 0, no call, file untouched, says what it would translate",
              rc == 0 and not dry_client.calls and sha256_file(bill_path(root, PP)) == sha_one[PP] and "would ask Claude to translate 2 text(s)" in out, out[-200:])

    # --- E. human work is never overwritten ----------------------------------
    def scenario(change_both: bool, make_human) -> tuple[_FakeClient, Bill, Bill, str]:
        with tempfile.TemporaryDirectory(prefix="politricks-civic-") as tmp_name:
            root = _temp_root(repo_root, Path(tmp_name), (PP,))
            _blank_bill(root, PP)
            _quiet(run, root, PP, client=_FakeClient())
            first = load_bill(bill_path(root, PP))
            bill = load_bill(bill_path(root, PP))
            make_human(bill)
            save_bill(bill, bill_path(root, PP))
            human_before = load_bill(bill_path(root, PP))
            _swap_yaml_text(root, PP, "Written memoranda", "Written memoranda NEWWORDS")
            if change_both:
                _swap_yaml_text(root, PP, "The Senate debated", "The Senate STAGEWORDS debated")
            client = _FakeClient()
            rc, out = _quiet(run, root, PP, client=client)
            return client, human_before, load_bill(bill_path(root, PP)), out

    def review_it(bill: Bill) -> None:
        block = ParticipationBlock.model_validate(bill.participation)
        block.review = Review(status="reviewed", at="2026-01-01", sha256=civic_sha256("participation", block))
        bill.participation = block.model_dump(mode="json")

    def hand_edit(bill: Bill) -> None:
        bill.participation["requirements"]["sheng"] = "Hand-written Sheng by Ian."  # fingerprint now stale: a hand-edited draft

    def leave_alone(bill: Bill) -> None:
        pass

    client, human, after, out = scenario(True, review_it)
    check("reviewed block: not overwritten when the YAML changes", after.participation == human.participation)
    check("reviewed block: still reviewed, same fingerprint", after.participation["review"]["status"] == "reviewed")
    check("reviewed block: stays out of the request (not paid for), the unprotected block is asked for",
          len(client.calls) == 1 and "NEWWORDS" not in client.calls[0]["messages"][0]["content"] and "STAGEWORDS" in client.calls[0]["messages"][0]["content"])
    check("reviewed block: the run says the YAML differs and what to do", "NOT updated" in out, out[:300])
    check("the unprotected block beside it WAS re-translated", "STAGEWORDS" in after.stage_next["text"]["sw"])

    client, human, after, out = scenario(False, hand_edit)
    check("hand-edited draft: not overwritten when the YAML changes", after.participation == human.participation and after.participation["requirements"]["sheng"] == "Hand-written Sheng by Ian.")
    check("hand-edited draft: no call made", not client.calls)

    client, human, after, out = scenario(False, leave_alone)
    check("control: an untouched draft IS re-translated when the YAML changes",
          len(client.calls) == 1 and "NEWWORDS" in after.participation["requirements"]["sw"] and "NEWWORDS" in after.participation["requirements"]["en"])
    check("control: the re-translated block is a fresh draft with its fingerprint",
          after.participation["review"]["status"] == "draft"
          and after.participation["review"]["sha256"] == civic_sha256("participation", ParticipationBlock.model_validate(after.participation)))

    # a block the YAML no longer has: an untouched draft goes, a reviewed one stays
    with tempfile.TemporaryDirectory(prefix="politricks-civic-") as tmp_name:
        root = _temp_root(repo_root, Path(tmp_name), (PP,))
        _blank_bill(root, PP)
        _quiet(run, root, PP, client=_FakeClient())
        bill = load_bill(bill_path(root, PP))
        review_it(bill)
        save_bill(bill, bill_path(root, PP))
        civic_path(root, PP).write_text("participation: null\nstage_next: null\n", encoding="utf-8", newline="\n")
        rc, out = _quiet(run, root, PP, client=_FakeClient(error=RuntimeError("no call expected")))
        after = load_bill(bill_path(root, PP))
        check("YAML drops a block: the untouched draft is removed, the reviewed one is kept",
              rc == 0 and after.stage_next is None and after.participation is not None and after.participation["review"]["status"] == "reviewed", out[-200:])

    # --- F. the doctrine -----------------------------------------------------
    def plant(unit_id_suffix: str, phrase: str):
        return lambda unit_id, en: (f"{phrase} SW: {en}", f"SHENG: {en}") if unit_id.endswith(unit_id_suffix) else (f"SW: {en}", f"SHENG: {en}")

    with tempfile.TemporaryDirectory(prefix="politricks-civic-") as tmp_name:
        root = _temp_root(repo_root, Path(tmp_name), (PP,))
        _blank_bill(root, PP)
        rc, out = _quiet(run, root, PP, client=_FakeClient(answer=plant("requirements", "Unapaswa")))
        bill = load_bill(bill_path(root, PP))
        found = civic_findings(bill, phrases)
        check("a banned phrase planted in a translation is caught by lint_bill on the result",
              any(f.kind == "banned" and f.item == "participation.requirements" and f.lang == "sw" and f.detail == "unapaswa" for f in found), str([f.line() for f in found]))
        check("and the run exits 1 and names it", rc == 1 and "participation.requirements sw" in out and "unapaswa" in out, out[-250:])
        check("the planted text was saved as a draft (lint and review are where it is fixed)", bill.participation["requirements"]["sw"].startswith("Unapaswa"))

    with tempfile.TemporaryDirectory(prefix="politricks-civic-") as tmp_name:
        root = _temp_root(repo_root, Path(tmp_name), (PP,))
        _blank_bill(root, PP)
        _swap_yaml_text(root, PP, "Written memoranda", "You should send written memoranda")
        client = _FakeClient()
        sha = sha256_file(bill_path(root, PP))
        rc, out = _quiet(run, root, PP, client=client)
        check("a banned phrase in the English YAML stops the run before any call",
              rc == 1 and not client.calls and sha256_file(bill_path(root, PP)) == sha and "you should" in out, out[:300])

    def bad_translation(label: str, answer, expect: str, drop: tuple[str, ...] = ()):
        with tempfile.TemporaryDirectory(prefix="politricks-civic-") as tmp_name:
            root = _temp_root(repo_root, Path(tmp_name), (PP,))
            _blank_bill(root, PP)
            sha = sha256_file(bill_path(root, PP))
            rc, out = _quiet(run, root, PP, client=_FakeClient(answer=answer, drop=drop))
            after = load_bill(bill_path(root, PP))
            only_stage = after.participation is None and after.stage_next is not None
            check(f"{label}: that block is not written, exit 1, reason shown", rc == 1 and after.participation is None and expect in out, out[-300:])
            check(f"{label}: the block beside it still lands", only_stage)

    bad_translation("an em dash in a translation", lambda i, en: (f"SW — {en}", f"SHENG: {en}") if i.endswith("requirements") else (f"SW: {en}", f"SHENG: {en}"), "dash character")
    bad_translation("a dropped number", lambda i, en: ("SW: bila nambari", f"SHENG: {en}") if i.endswith("requirements") else (f"SW: {en}", f"SHENG: {en}"), "41842-00100")
    bad_translation("the English returned as the Swahili", lambda i, en: (en, f"SHENG: {en}") if i.endswith("requirements") else (f"SW: {en}", f"SHENG: {en}"), "English unchanged")
    bad_translation("a missing item", None, "no result came back", drop=("participation.requirements",))
    check("translation_problems: a lost email address is caught",
          bool(translation_problems(Unit("x", "Write to clerk.senate@parliament.go.ke by 5pm.", ""), ItemOut(id="x", sw="Andika kwa karani kabla ya 5pm.", sheng="Write to karani by 5pm.", sheng_swaps=[]))))
    check("translation_problems: a clean translation passes",
          translation_problems(Unit("x", "Write to clerk.senate@parliament.go.ke by 5pm.", ""), ItemOut(id="x", sw="Andika kwa clerk.senate@parliament.go.ke kabla ya 5pm.", sheng="Write kwa clerk.senate@parliament.go.ke before 5pm.", sheng_swaps=[])) == [])

    with tempfile.TemporaryDirectory(prefix="politricks-civic-") as tmp_name:
        root = _temp_root(repo_root, Path(tmp_name), (PP,))
        _blank_bill(root, PP)
        sha = sha256_file(bill_path(root, PP))
        rc, out = _quiet(run, root, PP, client=_FakeClient(error=RuntimeError("network down")))
        check("a failed API call: exit 1, bill untouched, reason shown", rc == 1 and sha256_file(bill_path(root, PP)) == sha and "network down" in out, out[-250:])

        # the lock: a run while another command holds the bill refuses before any call
        client = _FakeClient()
        with bill_lock(bill_path(root, PP)):
            rc, out = _quiet(run, root, PP, client=client)
        check("bill held by another command: exit 1, nothing sent", rc == 1 and not client.calls and "another politricks command" in out, out[:250])

        # invalid YAML through the whole command: no call, bill untouched
        _swap_yaml_text(root, PP, 'source_url: "https://eastleighvoice', 'source_url: "http://eastleighvoice')
        client = _FakeClient()
        rc, out = _quiet(run, root, PP, client=client)
        check("run on an invalid YAML: exit 1, field named, no call, bill untouched",
              rc == 1 and not client.calls and sha256_file(bill_path(root, PP)) == sha and "participation.source_url" in out, out[:300])

        # challenge on an active bill
        civic_path(root, PP).write_text(
            "challenge:\n  checked_at: 2026-10-19\n  avenues:\n    - {name: A, text: B, source_url: \"https://example.org/a\"}\n",
            encoding="utf-8", newline="\n")
        rc, out = _quiet(run, root, PP, client=_FakeClient())
        check("a challenge section on an active bill is refused", rc == 1 and "only a passed bill" in out, out[:250])

    # --- G. the real files, read only ----------------------------------------
    for bill_id in ALL_IDS:
        rc, out = _quiet(run, repo_root, bill_id, dry_run=True)
        check(f"dry run on the real {bill_id}: exit 0", rc == 0 and "dry run" in out, out[:200])
    check("the real bill files, civic YAMLs and translator.md are byte-identical (SHA-256)", {p: sha256_file(p) for p in watched} == before)
    check("the repo's usage logs are untouched", {log: (log.stat().st_size if log.exists() else None) for log in logs} == logs_before)

    return check.finish()


# Known answer for the fingerprint of the vector in self_test (computed once
# from `civic_sha256`; a TypeScript twin must reproduce it).
_VECTOR_PARTICIPATION = "645d20818c5ca14ebf4b4b80f3759e3af033286df5dfd7166ac2e07c931ae8b3"
