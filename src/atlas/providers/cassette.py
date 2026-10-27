"""Recording model calls, and playing them back with no CLI, no network and no quota.

A live run is the most expensive thing this project produces and it has always been spent
once. PT31 cost 2032 seconds and two providers' quota, found one real bug and three defects
in its own instrument, and is now unreproducible: the world is in a bundle and the model's
answers are not. When the observability fix landed it came with the discovery that six
scratchpad harnesses had been running scriptless, and the honest consequence -- *any
conclusion drawn from them should be treated as unverified* -- could not be checked, because
there was no record of what had been asked or answered.

A cassette is that record. Every provider call becomes a line of JSON; a
:class:`ReplayProvider` plays them back later. A recorded run turns into a regression fixture
that exercises the engine against **real model output**, including the malformed, the empty
and the merely strange -- which is the half a mock cannot reach, because a mock only ever
returns what somebody already thought to script.

**It records at the provider seam, not at the turn engine.** `TurnEngine._invoke` is the one
door for every call *inside a jump* and is not the one door in general: `parse_orders`,
`build_advisor_view`'s call, both diplomacy calls, EVE and the diagnostics probe all reach a
provider without passing through it, and the first of those runs for every order a human
types. A recorder that decorates a provider sees all of them, and needs no engine change at
all to do it.

**It records attempts, not calls.** `_invoke` retries a rate limit and logs one line per
call; a replay has to reproduce the sequence the *provider* saw, so a failure that was
retried is recorded as a failure and replayed as one. Recording only the answer that came
back after the retries would desynchronise a replay the first time a live run hit a 429.

**Matching is by request digest, not by arrival order.** Tracked agents run concurrently
through ``asyncio.gather``, so arrival order is a race -- which is the same reason the engine
sorts their results by nation id before using them. The key is a hash over role, model,
prompt, schema and options: order-independent, and identical for identical questions.
Repeats of one key are consumed in recorded order, which is what makes a retried call replay
as a retry.

**A miss is loud.** A prompt that no longer matches means the engine is now asking a
different question, and that is the finding rather than an inconvenience to be smoothed over
with a nearest-match. :data:`MatchMode.LOOSE` exists for the case where the prompt changed on
purpose and the question is whether the new engine copes with last week's answers -- but it
is opt-in and a run that used it can say so.

**No schema, no fingerprint, no rewind.** A cassette is a file beside the world, never a
table inside it.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..state.models import Provider
from .base import AuditVerdict, ModelAudit, ModelProvider, ProviderRequest, ProviderResult
from .errors import Failure, FailureKind

__all__ = [
    "CassetteMiss",
    "Entry",
    "MatchMode",
    "RecordingProvider",
    "ReplayProvider",
    "digest_of",
    "asked",
    "summarise",
    "read_cassette",
]

#: Bumped when the recorded shape changes in a way an older reader would misread. A
#: cassette that does not carry this is from before it existed and is refused rather than
#: guessed at.
#:
#: **2** — keys are computed over the *canonical* prompt, with the untrusted-block nonce
#: normalised away. A tape at this format needs no weak-key rescue and is never given one.
#:
#: **1** — keys were computed over the raw prompt, nonce included, so a call carrying
#: quarantined text can never be matched by digest again. Those entries, and only those, are
#: also indexed under :func:`asked`. See :class:`ReplayProvider`.
FORMAT = 2

#: Formats this build can read. A tape outside the set is refused rather than reinterpreted:
#: reading one as though it matched would replay answers whose fields no longer mean what
#: this build thinks they mean.
READABLE: frozenset[int] = frozenset({1, 2})


class MatchMode(StrEnum):
    """How a replayed call finds its recorded answer."""

    #: The request must be the one that was recorded, to the byte. The default, because a
    #: changed prompt is a changed question.
    EXACT = "exact"
    #: Match on role alone, in recorded order. For replaying old answers against a *new*
    #: prompt on purpose -- which is a real thing to want and a bad thing to do by accident.
    LOOSE = "loose"


class CassetteMiss(LookupError):
    """A call was made that the cassette has no answer for."""


#: The per-call nonce :func:`atlas.engine.untrusted.wrap_untrusted` fences quarantined text
#: with. It is `secrets.token_hex(8).upper()` and its unguessability is load-bearing -- with
#: a fixed delimiter a nation only has to type the closing token to escape the quarantine and
#: address the model directly.
_NONCE = re.compile(r"(UNTRUSTED_)[0-9A-F]{16}")


def _canonical(prompt: str) -> str:
    """The prompt with the one thing in it that is not part of the question taken out.

    **Found by replaying a real recorded run.** Every diplomacy prompt carries an untrusted
    block fenced with a fresh random nonce, so two calls asking the identical question in
    identical worlds hash differently, and a replay of a negotiation could never match --
    which is exactly what happened on turn 4 of the first recorded live run.

    Normalising it here rather than making the nonce deterministic is deliberate: the nonce
    is a security property of the *call*, not a property of the question, and weakening it
    to make a test harness easier would be trading a real defence for a convenience. The
    stored prompt keeps the nonce it was sent with, because a tape is also a transcript.
    """
    return _NONCE.sub(r"\1x", prompt)


def digest_of(request: ProviderRequest) -> str:
    """A stable key for one question.

    Everything that could change the answer and nothing that could not: the correlation id
    is deliberately absent, because it identifies the *order* a call was made about rather
    than the question asked, and including it would make an otherwise identical call miss.
    The untrusted-block nonce is normalised away for the same reason -- see
    :func:`_canonical`.
    """
    return _digest(request, _canonical(request.prompt))


def _digest(request: ProviderRequest, prompt: str) -> str:
    material = json.dumps(
        {
            "role": request.role.value,
            "model": request.model,
            "prompt": prompt,
            "schema": request.schema,
            "options": {k: v for k, v in sorted(request.options.items())},
            "system_prompt": request.system_prompt,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()


def asked(role: str, model: str, prompt: str) -> tuple[str, str, str]:
    """A weaker key over only what an `Entry` stores: who was asked, and what.

    **The fallback, and it exists because of a specific and instructive failure.** A tape
    recorded before the untrusted nonce was normalised carries keys computed over the raw
    prompt -- including a random token that will never occur again -- so those entries can
    never be matched by digest, by the replay's own prompt or by any recomputation, because
    an `Entry` does not store the schema and options the digest also covers.

    Indexing what the entry *does* store recovers them. It is weaker than the digest, which
    is why it is only ever tried second: two calls with the same role, model and prompt but
    different schemas would collide here and not there. In practice a prompt determines a
    call, and the alternative is throwing away every recorded run made before a change to how
    prompts are hashed -- which would make the cassette exactly as disposable as the runs it
    exists to preserve.
    """
    return (role, model, _canonical(prompt))


@dataclass(frozen=True, slots=True)
class Entry:
    """One recorded call: the question, and everything the engine got back."""

    key: str
    role: str
    provider: str
    model: str
    #: The prompt verbatim. Kept even though only its hash is matched on, because a
    #: cassette a person cannot read is a cassette nobody will check.
    prompt: str
    ok: bool
    text: str = ""
    data: Any = None
    served_model: str | None = None
    verdict: str | None = None
    failure_kind: str | None = None
    failure_message: str = ""
    duration_s: float = 0.0
    #: Where in the run this call happened. Recorded for reading and for `LOOSE`, never
    #: used by an exact match -- see the module docstring on why order cannot be the key.
    seq: int = 0
    #: Which format this entry was *read* from, defaulting to what this build writes. It
    #: decides whether the entry is eligible for the weak-key rescue, so it travels with the
    #: entry rather than being a property of the file -- a tape can in principle be
    #: concatenated from two recordings, and the rescue must follow the older lines only.
    format: int = FORMAT

    def as_json(self) -> dict[str, Any]:
        return {
            "format": FORMAT, "key": self.key, "seq": self.seq, "role": self.role,
            "provider": self.provider, "model": self.model, "prompt": self.prompt,
            "ok": self.ok, "text": self.text, "data": self.data,
            "served_model": self.served_model, "verdict": self.verdict,
            "failure_kind": self.failure_kind, "failure_message": self.failure_message,
            "duration_s": round(self.duration_s, 3),
        }

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> Entry:
        written = int(row.get("format") or 0)
        if written not in READABLE:
            raise ValueError(
                f"this cassette is format {written!r} and this build reads "
                f"{sorted(READABLE)}. Re-record it rather than reading it as if it matched."
            )
        return cls(
            format=written,
            key=str(row["key"]), role=str(row.get("role") or ""),
            provider=str(row.get("provider") or ""), model=str(row.get("model") or ""),
            prompt=str(row.get("prompt") or ""), ok=bool(row.get("ok")),
            text=str(row.get("text") or ""), data=row.get("data"),
            served_model=row.get("served_model"), verdict=row.get("verdict"),
            failure_kind=row.get("failure_kind"),
            failure_message=str(row.get("failure_message") or ""),
            duration_s=float(row.get("duration_s") or 0.0),
            seq=int(row.get("seq") or 0),
        )

    def result(self, request: ProviderRequest) -> ProviderResult:
        """The recorded answer, rebuilt as the engine originally received it."""
        provider = _provider_of(self.provider)
        if not self.ok:
            kind = FailureKind(self.failure_kind or FailureKind.UNKNOWN.value)
            return ProviderResult(
                provider=provider, role=request.role, ok=False, text=self.text,
                failure=Failure(kind, provider.value,
                                self.failure_message or "replayed failure"),
                audit=ModelAudit(request.model, self.served_model,
                                 AuditVerdict(self.verdict or AuditVerdict.UNVERIFIED)),
                duration_s=self.duration_s,
            )
        return ProviderResult(
            provider=provider, role=request.role, ok=True, text=self.text, data=self.data,
            audit=ModelAudit(request.model, self.served_model,
                             AuditVerdict(self.verdict or AuditVerdict.UNVERIFIED)),
            duration_s=self.duration_s,
        )


def _provider_of(name: str) -> Provider:
    try:
        return Provider(name)
    except ValueError:
        return Provider.MOCK


def _entry_for(request: ProviderRequest, result: ProviderResult, seq: int) -> Entry:
    return Entry(
        key=digest_of(request),
        seq=seq,
        role=request.role.value,
        provider=(result.provider.value if isinstance(result.provider, Provider)
                  else str(result.provider or "")),
        model=request.model,
        prompt=request.prompt,
        ok=result.ok,
        text=result.text,
        data=result.data,
        served_model=result.audit.served if result.audit else None,
        verdict=result.audit.verdict.value if result.audit else None,
        failure_kind=result.failure.kind.value if result.failure else None,
        failure_message=result.failure.message if result.failure else "",
        duration_s=result.duration_s,
    )


def read_cassette(path: Path) -> list[Entry]:
    """Every entry in a cassette, in recorded order.

    **Only an incomplete *final* line is tolerated.** A recording interrupted mid-write --
    what a killed live run leaves behind -- stops in the middle of its last line, and
    everything that reached the disk before it is good. That is the one case, and it was
    being used to excuse all the others: a malformed line *anywhere* was skipped, so a tape
    with a corrupt entry in the middle replayed as though that call had never been made, and
    the run it produced was quietly not the run that was recorded. Found by the Codex review
    of phase 2.

    An interior line that will not parse is a damaged tape, and a damaged tape is a finding
    rather than an inconvenience. A cassette written at a format this build does not know is
    refused for the same reason, from :meth:`Entry.from_json`.
    """
    entries: list[Entry] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        last = number == len(lines)
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            if last:
                # The interrupted write. Nothing after it, so nothing is being skipped over.
                break
            raise ValueError(
                f"{path.name} line {number} of {len(lines)} is not readable JSON, and it "
                "is not the last line -- this tape is damaged rather than interrupted, and "
                f"replaying it would silently drop a recorded call: {exc}"
            ) from exc
        try:
            # **`seq` is the entry's position in the file, not what the line says.** The
            # file is appended to one flushed line at a time, so line order *is* the
            # recorded order. The stored number is not: every recorder used to keep its own
            # counter, and a run that recorded its providers into one tape through several
            # recorders wrote colliding sequences (97 entries, 60 distinct values), which
            # made "recorded order" in `ReplayProvider.unused` a tie-break by accident.
            entries.append(replace(Entry.from_json(row), seq=len(entries) + 1))
        except KeyError as exc:
            # **Not excused on the last line either, and that is the sharper rule.** An
            # interrupted write leaves *unparseable* JSON -- the line stops mid-token. A
            # line that parses cleanly and is missing a required field was written in full
            # and is wrong, wherever it sits. Tolerating it at the end would let a genuinely
            # damaged final entry pass as "interrupted".
            raise ValueError(
                f"{path.name} line {number} of {len(lines)} parses but is missing {exc}: "
                "a complete line that is not an entry is damage, not an interrupted write"
            ) from exc
    return entries


@dataclass(slots=True)
class RecordingProvider:
    """Wraps a provider and writes every call it makes to a cassette.

    Appends and flushes per call rather than writing at the end, because the runs worth
    recording are the ones long enough to be interrupted, and a cassette that only exists
    if the run finished cleanly is a cassette for the runs that needed it least.
    """

    inner: ModelProvider
    path: Path
    name: Provider = field(init=False)

    def __post_init__(self) -> None:
        self.name = getattr(self.inner, "name", Provider.MOCK)
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return self.inner.available()

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        result = await self.inner.invoke(request)
        # **Numbered by the file, not by the recorder.** A run records each of its
        # providers through its own recorder into the same tape, and per-instance counters
        # wrote colliding numbers. Counting the lines already there, under one lock with the
        # append, keeps the stored `seq` equal to the position `read_cassette` assigns.
        with _APPEND_LOCK:
            entry = _entry_for(request, result, _lines_in(self.path) + 1)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.as_json(), default=str) + "\n")
        return result


#: Serialises numbering-and-appending across every recorder in this process.
_APPEND_LOCK = threading.Lock()


def _lines_in(path: Path) -> int:
    """How many entries a tape already holds, so appending to it continues its numbering."""
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


@dataclass(slots=True)
class ReplayProvider:
    """Answers from a cassette. Never touches a CLI.

    One instance can stand in for every provider in a run: the recorded entry carries the
    provider that actually answered, so a call routed to codex replays codex's answer even
    though the engine was handed one object for all three keys.

    **The weak-key rescue applies to format-1 entries and nothing else, and that gate is the
    whole point of the format bump.** :func:`asked` matches on role, model and canonical
    prompt -- everything an `Entry` stores -- which is strictly less than the digest, because
    an entry records neither the schema nor the options. Consulted unconditionally, as it was
    when first written, a change to the *schema* missed the digest and then quietly hit the
    weak key: the engine asked for a different shape of answer and was handed the old one,
    with no miss reported. That defeats the guarantee this class exists to provide, on
    exactly the class of change it exists to catch.

    Format-2 tapes carry canonical keys and never need the rescue, so they never get it. The
    shim now dies with the recordings it was written for.
    """

    entries: list[Entry]
    mode: MatchMode = MatchMode.EXACT
    name: Provider = Provider.MOCK
    #: Requests that found no recorded answer. Populated only in the non-strict mode,
    #: where a miss is a failed call rather than an exception.
    missed: list[ProviderRequest] = field(default_factory=list)
    #: Raise on a miss instead of returning a failure. On by default: a replay that
    #: silently substitutes "the model failed" for "we have no record of this" would
    #: produce a clean-looking run whose every conclusion was about the wrong world.
    strict: bool = True
    _by_key: dict[str, list[Entry]] = field(default_factory=dict, init=False, repr=False)
    _by_role: dict[str, list[Entry]] = field(default_factory=dict, init=False, repr=False)
    _by_asked: dict[tuple[str, str, str], list[Entry]] = field(
        default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        by_key: dict[str, list[Entry]] = defaultdict(list)
        by_role: dict[str, list[Entry]] = defaultdict(list)
        by_asked: dict[tuple[str, str, str], list[Entry]] = defaultdict(list)
        for entry in self.entries:
            by_key[entry.key].append(entry)
            by_role[entry.role].append(entry)
            # Only the older format is given a second way to be found. A format-2 entry that
            # the digest does not match is an entry for a different question, and saying so
            # is the job.
            if entry.format < 2:
                by_asked[asked(entry.role, entry.model, entry.prompt)].append(entry)
        self._by_key = dict(by_key)
        self._by_role = dict(by_role)
        self._by_asked = dict(by_asked)

    @classmethod
    def from_file(cls, path: Path, **kwargs: Any) -> ReplayProvider:
        return cls(read_cassette(Path(path)), **kwargs)

    def available(self) -> bool:
        return True

    def remaining(self) -> int:
        return sum(len(queue) for queue in self._by_key.values())

    def unused(self) -> tuple[Entry, ...]:
        """Recorded calls the engine never asked for, in recorded order.

        **The other half of a miss, and the half that used to be a number nobody read.** A
        miss says the engine asked something the tape has no answer for; leftovers say the
        opposite -- the recorded run made a call this one did not, which is a call that has
        gone missing from the turn loop. That is exactly as much of a regression as a
        changed prompt, and it was being printed as a count at the end of a report.

        Lives here rather than in the replay harness because it is a property of the tape
        and the run, not of a script -- and because a rule that only exists in a scratchpad
        harness is a rule with no test.
        """
        left = [entry for queue in self._by_key.values() for entry in queue]
        return tuple(sorted(left, key=lambda entry: entry.seq))

    def exhausted(self) -> None:
        """Raise unless every recorded call was asked for. For a harness or a test."""
        left = self.unused()
        if not left:
            return
        by_role: dict[str, int] = {}
        for entry in left:
            by_role[entry.role] = by_role.get(entry.role, 0) + 1
        raise CassetteMiss(
            "the engine did not make every call the recorded run made: "
            + ", ".join(f"{role} x{count}" for role, count in sorted(by_role.items()))
        )

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        entry = self._take(request)
        if entry is None:
            self.missed.append(request)
            if self.strict:
                raise CassetteMiss(self._miss_message(request))
            return ProviderResult(
                provider=self.name, role=request.role, ok=False,
                failure=Failure(FailureKind.EMPTY_RESPONSE, self.name.value,
                                self._miss_message(request)),
                audit=ModelAudit(request.model, None, AuditVerdict.UNVERIFIED),
            )
        return entry.result(request)

    def _take(self, request: ProviderRequest) -> Entry | None:
        """Consume the next recorded answer to this question, or ``None``.

        Consumed rather than merely looked up, so a call made twice with the same prompt
        gets the two answers it got the first time round -- which is how a retried failure
        replays as a failure and then a success rather than as two failures.
        """
        queue = self._by_key.get(digest_of(request))
        if not queue:
            queue = self._by_asked.get(
                asked(request.role.value, request.model, request.prompt)
            )
        if queue:
            entry = queue.pop(0)
            self._forget(entry)
            return entry
        if self.mode is not MatchMode.LOOSE:
            return None
        by_role = self._by_role.get(request.role.value)
        if not by_role:
            return None
        entry = by_role.pop(0)
        self._forget(entry, keep_role=True)
        return entry

    def _forget(self, entry: Entry, *, keep_role: bool = False) -> None:
        """Consume an entry from every index it appears in.

        All three, or a tape with a repeated question hands the same answer out twice: the
        entry would be gone from the index it was found in and still sitting in the others.
        """
        for index, key in (
            (self._by_key, entry.key),
            (self._by_asked, asked(entry.role, entry.model, entry.prompt)),
            *(() if keep_role else ((self._by_role, entry.role),)),
        ):
            queue = index.get(key)
            if queue and entry in queue:
                queue.remove(entry)

    def _miss_message(self, request: ProviderRequest) -> str:
        """Why the miss happened, in terms of what to do about it.

        Three causes, three fixes, and telling them apart is most of the value: a call made
        more often than it was recorded is a loop that now runs once more; a prompt that no
        longer matches is a briefing that changed; and a prompt that *does* match while the
        digest does not is a **schema or options** change, which is the one this class was
        letting through silently until the format-1 rescue was gated.
        """
        key = digest_of(request)
        role = request.role.value
        if key in self._by_key or asked(
            request.role.value, request.model, request.prompt
        ) in self._by_asked:
            return (
                f"the cassette has no answer left for a {role} call it recorded "
                f"{len([e for e in self.entries if e.key == key])} time(s): the engine is "
                "asking it more often than the recorded run did"
            )

        # Scanning what is left rather than keeping a fourth index: this runs only on the
        # miss path, immediately before raising.
        wanted = asked(role, request.model, request.prompt)
        same_prompt = [
            entry
            for queue in self._by_key.values()
            for entry in queue
            if asked(entry.role, entry.model, entry.prompt) == wanted
        ]
        if same_prompt:
            return (
                f"the cassette recorded this exact {role} prompt, and the request no longer "
                "matches it: the schema, the options or the system prompt have changed, so "
                "the engine is asking for a different answer to the same question. That is "
                "the finding. Re-record."
            )

        recorded = len(self._by_role.get(role, ()))
        return (
            f"the cassette has no record of this {role} call. Its prompt does not match "
            f"any of the {recorded} unused {role} call(s) recorded, so the engine is asking "
            "a different question than it asked when this was recorded -- which is the "
            "finding. Re-record, or replay with MatchMode.LOOSE if the prompt changed on "
            "purpose."
        )


def summarise(entries: list[Entry]) -> str:
    """A readable line per role, for a harness reporting what it loaded."""
    by_role: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_role[entry.role].append(entry)
    failed = sum(1 for entry in entries if not entry.ok)
    lines = [f"{len(entries)} recorded call(s), {failed} of them failures:"]
    lines.extend(
        f"  {role}: {len(rows)}" for role, rows in sorted(by_role.items())
    )
    return "\n".join(lines)
