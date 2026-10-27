"""Negotiation: talking to a nation, and turning what was agreed into state.

The worked example from the design lives here. A player talks to Poland; Poland's replies
are generated from a view built for Poland alone. When the conversation closes, a small
structured extraction call turns whatever was agreed into a :class:`Commitment` row. The
world simulator never hears the conversation -- on the next jump it reads the recorded
commitment, which is what "state is the message bus" means in practice.

**A conversation is not memory.** Every reply is a fresh stateless call carrying the
transcript back in as data. The nation remembers nothing; the transcript is the memory,
and it lives here rather than in a model's context window.

**Group chats need a speaker chosen deterministically.** Letting every nation reply to
every message produces a wall of text and multiplies the call count. A cheap
next-speaker rule -- who was addressed, who has most at stake, who has not spoken -- keeps
it to one reply per exchange.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..providers.base import ModelProvider, ProviderRequest
from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.jsonschema import make_strict
from ..state.models import (
    CommitmentKind,
    CommitmentStatus,
    DiffOp,
    EventKind,
    EventOrigin,
    Role,
    TreatyStatus,
    Visibility,
)
from .routing import Router, diplomacy_stakes, log_decision
from .treaties import term_diffs
from .views import build_diplomacy_view

__all__ = [
    "COMMITMENT_SCHEMA",
    "ChatTurn",
    "DiplomacySession",
    "choose_next_speaker",
    "accept_commitment",
    "extract_commitments",
    "implied_treaty_status",
    "next_commitment_id",
]


def next_commitment_id(conn: sqlite3.Connection) -> int:
    """The id the next commitment will be written under.

    **`MAX(id) + 1` is not a placeholder for something safer -- it is the thing that makes
    a replay exact, and this is where that gets written down.**

    The obvious complaint is id reuse: turn 3 signs commitment 7, a rewind to turn 2
    deletes it, and the agreement the *next* turn 3 signs is also 7. That is true, and it
    is the property we want. A rewind removes the row, so re-running the turn allocates the
    same id, and the world that comes back is byte-identical to the one that was there
    before -- which is exactly what `rewind-fingerprint-mismatch`, the gravest detector in
    the laboratory, exists to check, and what a recorded run replayed from a cassette has
    to reproduce.

    A monotonic allocator that survived a rewind would hand the second run id 8, move the
    digest, and turn every replay of a turn containing a treaty into a permanent mismatch.
    It would trade a cosmetic problem -- a bug bundle citing an integer that now names a
    different agreement -- for a false positive on the one check that says the audit trail
    is sound.

    One function rather than the expression written out at each call site, because there
    are two writers now: this module and the Council's ceasefire demand.
    """
    row = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next FROM commitment").fetchone()
    return int(row["next"])

#: What the extraction call must return. Deliberately narrow: it records what was agreed,
#: it does not decide anything. There is no field for a relation change or an outcome,
#: because a conversation does not move the world -- the recorded commitment does, once
#: the world simulator reads it.
_COMMITMENT_SCHEMA_RAW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["commitments"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "One sentence describing what, if anything, was settled.",
        },
        "commitments": {
            "type": "array",
            "description": "Agreements actually reached. Empty if the talks settled nothing.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "status", "condition"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [k.value for k in CommitmentKind],
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "active"],
                        "description": "'pending' if agreed in principle but conditional; 'active' if it takes effect now.",
                    },
                    "condition": {
                        "type": "string",
                        "description": "What each side must do, in plain language.",
                    },
                    "parties": {"type": "array", "items": {"type": "string"}},
                    # Only meaningful for a `territory_cession`, and the only field in this
                    # schema that names something the engine will later *check*. A promise
                    # to hand over a province is exactly what a negotiation produces and
                    # nothing but the transcript knows which one -- so the model records
                    # the promise, and the engine refuses it unless the region resolves and
                    # the promiser actually holds it. Recording a promise is not keeping
                    # one: nothing here moves any ground.
                    "region": {
                        "type": "string",
                        "description": "For a territory cession only: the province promised.",
                    },
                    "ceded_by": {
                        "type": "string",
                        "description": "For a territory cession only: which party hands it over.",
                    },
                    # The second field a negotiation can settle that the engine will later
                    # act on, and bounded the same way. Two powers agreeing to pool what
                    # their services see is a thing talks produce and nothing but the
                    # transcript knows; what crosses is each side's *estimate*, so a clause
                    # makes an ally better informed and never omniscient.
                    "intel_sharing": {
                        "type": "boolean",
                        "description": "True only if the parties agreed to share intelligence assessments with each other.",
                    },
                    "intel_about": {
                        "type": "string",
                        "description": "For intelligence sharing only: the one country the sharing covers. Leave empty if it covers everything each side sees.",
                    },
                },
            },
        },
    },
}

#: Strict form, so the one schema satisfies every provider. See the note in
#: atlas.state.jsonschema.
COMMITMENT_SCHEMA: dict[str, Any] = make_strict(_COMMITMENT_SCHEMA_RAW)


@dataclass(frozen=True, slots=True)
class ChatTurn:
    speaker: str
    text: str
    #: True for the human player, so the UI can style it and the view can label it.
    is_player: bool = False


@dataclass(slots=True)
class DiplomacySession:
    """One conversation between the player's nation and another power."""

    conn: sqlite3.Connection
    world_id: str
    player_nation: str
    counterpart: str
    providers: dict[str, ModelProvider]
    router: Router
    transcript: list[ChatTurn] = field(default_factory=list)
    #: Set when a commitment was recorded, so closing twice is harmless.
    closed: bool = False

    def _counterpart_name(self) -> str:
        row = self.conn.execute(
            "SELECT name FROM nation WHERE id = ?", (self.counterpart,)
        ).fetchone()
        return row["name"] if row else self.counterpart

    async def say(self, message: str, *, ultimatum: bool = False, treaty: bool = False) -> ChatTurn:
        """Send a message and get the counterpart's reply."""
        self.transcript.append(ChatTurn(self.player_nation, message, is_player=True))

        score, factors = diplomacy_stakes(
            self.conn,
            nation_id=self.counterpart,
            counterpart_id=self.player_nation,
            message=message,
            ultimatum=ultimatum,
            treaty_on_table=treaty,
        )
        decision = self.router.route(Role.DIPLOMACY, score, factors)

        # Built for the counterpart, so it contains their knowledge and not the player's.
        view = build_diplomacy_view(
            self.conn,
            self.world_id,
            nation_id=self.counterpart,
            counterpart_id=self.player_nation,
            conversation=[(turn.speaker, turn.text) for turn in self.transcript],
        )

        provider = self.providers.get(decision.provider_key)
        if provider is None or not provider.available():
            reply = ChatTurn(
                self.counterpart,
                f"[{self._counterpart_name()} cannot be reached: "
                f"{decision.provider_key} is unavailable.]",
            )
            self.transcript.append(reply)
            return reply

        result = await provider.invoke(
            ProviderRequest(
                role=Role.DIPLOMACY,
                prompt=view.text,
                model=decision.model,
                timeout_s=180.0,
            )
        )

        log_decision(
            self.conn,
            self.world_id,
            self._turn(),
            decision,
            served_model=result.audit.served if result.audit else None,
            audit_verdict=result.audit.verdict.value if result.audit else None,
            duration_ms=int(result.duration_s * 1000),
            outcome="ok" if result.ok else "failed",
        )
        self.conn.commit()

        text = (
            result.text.strip()
            if result.ok
            else f"[no reply: {result.failure.message if result.failure else 'unknown error'}]"
        )
        reply = ChatTurn(self.counterpart, text)
        self.transcript.append(reply)
        return reply

    def _turn(self) -> int:
        row = self.conn.execute(
            "SELECT turn_number FROM world WHERE id = ?", (self.world_id,)
        ).fetchone()
        return int(row["turn_number"]) if row else 0

    async def close(self) -> list[int]:
        """Extract whatever was agreed into state, and record the exchange as an event.

        This is the hop that makes the conversation matter. Nothing the counterpart said
        changes the world by itself; only the commitment written here is visible to the
        next jump.
        """
        if self.closed or not self.transcript:
            return []
        self.closed = True

        turn = self._turn()
        date = self.conn.execute(
            "SELECT world_date FROM world WHERE id = ?", (self.world_id,)
        ).fetchone()["world_date"]

        # The conversation itself is recorded as a bilateral event, so both parties can
        # cite it later and neither can pretend it did not happen.
        event_id = record_event(
            self.conn,
            world_id=self.world_id,
            turn=turn,
            date=date,
            kind=EventKind.DIPLOMATIC_MESSAGE,
            origin=EventOrigin.ENGINE,
            actor_nation_id=self.player_nation,
            text=f"Talks between {self.player_nation} and {self.counterpart}.",
            visibility=Visibility.BILATERAL,
            visible_to=(self.player_nation, self.counterpart),
        )

        commitments = await extract_commitments(
            self.conn,
            self.world_id,
            transcript=self.transcript,
            parties=(self.player_nation, self.counterpart),
            providers=self.providers,
            router=self.router,
            source_event_id=event_id,
        )
        self.conn.commit()
        return commitments


async def extract_commitments(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    transcript: list[ChatTurn],
    parties: tuple[str, str],
    providers: dict[str, ModelProvider],
    router: Router,
    source_event_id: int,
) -> list[int]:
    """Turn a conversation into commitment rows. Returns the ids written.

    Runs as a small, cheap, schema-enforced call rather than by scraping the prose. The
    schema cannot express a world change, so an extraction that hallucinates enthusiasm
    still cannot do anything except record an agreement the engine will later test.
    """
    if not transcript:
        return []

    lines = [
        "Read this diplomatic exchange and record only what was actually agreed.",
        "",
        "If nothing was settled, return an empty list. Do not record aspirations, offers "
        "that were refused, or things one side merely proposed. An agreement needs both "
        "sides to have accepted it.",
        "",
        f"The parties are {parties[0]} and {parties[1]}.",
        "",
        "## Transcript",
    ]
    from .untrusted import wrap_untrusted

    for entry in transcript:
        lines.append(wrap_untrusted(entry.text, source=entry.speaker, kind="a diplomatic message"))

    decision = router.route(Role.PARSER, 0.0)
    provider = providers.get(decision.provider_key)
    if provider is None or not provider.available():
        return []

    result = await provider.invoke(
        ProviderRequest(
            role=Role.PARSER,
            prompt="\n".join(lines),
            model=decision.model,
            schema=COMMITMENT_SCHEMA,
            timeout_s=120.0,
            options={"reasoning_effort": "low"},
        )
    )
    if result.failed or not isinstance(result.data, dict):
        return []

    clock = conn.execute(
        "SELECT turn_number, world_date FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    turn = clock["turn_number"]
    # **And the day, because a deadline is a date.** A cession's grace period used to be
    # `turn + 3`, which is three months at monthly pacing and three years at yearly.
    today = str(clock["world_date"] or "")

    # **The diffs inherit the talks' own visibility, and that is not a detail.** A
    # negotiation is `BILATERAL` -- it happened between two powers and nobody else saw it --
    # and rule INFO-002 refuses a public change caused by a private event: *a covert cause
    # cannot produce overt state*. Marking these PUBLIC made every extraction refuse, which
    # is the same mistake the gambit layer made with a SECRET cause and the same fix.
    scope = _visibility_of(conn, source_event_id)

    written: list[int] = []
    for entry in result.data.get("commitments") or ():
        try:
            kind = CommitmentKind(entry["kind"])
        except (KeyError, ValueError):
            continue

        # **Written as diffs, so an agreement unwinds with the turn that produced it.**
        # This was a bare INSERT, and PT31 caught what that costs: a pact signed on turn 3
        # and rewound to turn 2 was still standing, with `relation.treaty_status` still
        # reading `non_aggression`. `commitment` is in `_FINGERPRINT_TABLES`, so the digest
        # moved and the world did not.
        #
        # The id is chosen here rather than left to AUTOINCREMENT because an insert diff
        # has to *name* the row it created -- that name is what a rewind deletes by.
        commitment_id = next_commitment_id(conn)

        diffs = [
            Diff("commitment", str(commitment_id), "__insert__", DiffOp.INSERT,
                 {
                     "world_id": world_id,
                     "kind": kind.value,
                     "status": entry.get("status", "pending"),
                     "condition_text": str(entry.get("condition", ""))[:1000],
                     "source_event_id": source_event_id,
                     "created_turn": turn,
                 },
                 reason_event_id=source_event_id, visibility_scope=scope),
        ]
        # Parties come from the session, not from the model: a nation cannot write itself
        # into someone else's treaty by naming them in an extraction.
        # **Acceptance is recorded per party, and only when the talks actually settled.**
        # The extractor is told an agreement needs both sides to have accepted it, so an
        # `active` answer is its judgement that both did -- and both were in the room, which
        # is what makes recording it for both honest. A `pending` answer records neither,
        # and rule DIP-004 then refuses any attempt to bring it into force until they do.
        accepted = turn if str(entry.get("status", "pending")) == "active" else None
        diffs += [
            Diff("commitment_nation", f"{commitment_id}|{nation_id}", "__insert__",
                 DiffOp.INSERT, {"role": "party", "accepted_turn": accepted},
                 reason_event_id=source_event_id, visibility_scope=scope)
            for nation_id in parties
        ]
        # **And what it actually promised, in terms the engine can check.** Without these an
        # agreement is prose: `condition_text` says what each side must do and nothing can
        # read it, which is why six of the nine kinds could be signed and change nothing at
        # all. The terms ride in the same batch as the agreement, so a refused batch leaves
        # neither behind.
        diffs += term_diffs(
            conn, world_id, commitment_id=commitment_id, kind=kind.value,
            parties=tuple(parties), turn=turn, date=today,
            event_id=source_event_id, scope=scope,
            cede_region=_ceded_region(conn, world_id, entry, parties),
            cede_from=str(entry.get("ceded_by") or ""),
        )
        diffs += _intel_diffs(
            conn, world_id, entry, parties=tuple(parties), commitment_id=commitment_id,
            kind=kind.value, event_id=source_event_id, scope=scope)
        outcome = apply_batch(
            conn,
            DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None, diffs=tuple(diffs)),
        )
        if not outcome.applied:
            continue
        written.append(commitment_id)

    # ...and make the column the rest of the engine reads agree with what was just signed.
    # Unconditional rather than only-when-something-was-written, so an extraction that
    # records nothing still reconciles a status left over from a treaty since broken.
    sync_treaty_status(conn, world_id, parties, event_id=source_event_id)
    return written


#: Which commitment kinds imply a standing treaty, and which treaty they imply. Ordered
#: weakest to strongest; the strongest active one between two nations is what
#: ``relation.treaty_status`` ends up saying.
#:
#: **Deliberately not every kind.** A ``ceasefire`` or a ``peace_treaty`` ends a war and is
#: already expressed by ``relation.at_war``; reading either as a standing pact would make
#: marching into the other country stop being an invasion, which is not what either
#: document says. ``military_access``, ``territory_cession`` and ``tribute`` are obligations
#: rather than pacts and imply nothing about whether the border is safe.
_TREATY_FROM_COMMITMENT: tuple[tuple[CommitmentKind, TreatyStatus], ...] = (
    (CommitmentKind.TRADE_AGREEMENT, TreatyStatus.TRADE),
    (CommitmentKind.NON_AGGRESSION_PACT, TreatyStatus.NON_AGGRESSION),
    (CommitmentKind.DEFENSIVE_PACT, TreatyStatus.DEFENSIVE),
    (CommitmentKind.ALLIANCE, TreatyStatus.ALLIANCE),
)


#: Which agreements can carry an intelligence-sharing clause.
#:
#: **Partnerships, not every signature.** A defensive pact and an alliance are arrangements
#: against a common threat, and pooling what each side's service sees is what such an
#: arrangement is for. A non-aggression pact is a promise not to fight, a trade agreement is
#: about cargo, and a peace treaty is signed by two powers who have just been killing each
#: other -- none of them implies handing over your assessments. Tunable: widening this is a
#: one-line change and a balance decision, so it is flagged rather than assumed.
SHARING_KINDS: frozenset[str] = frozenset({"defensive_pact", "alliance"})


def _intel_diffs(  # noqa: PLR0913 - a clause is its parties, its commitment, and its scope
    conn: sqlite3.Connection, world_id: str, entry: dict, *, parties: tuple[str, ...],
    commitment_id: int, kind: str, event_id: int, scope: Visibility,
) -> list[Diff]:
    """The sharing clauses an agreement carries, as insert diffs.

    Mutual: a clause written from a negotiation is two rows, one each way, because two
    governments agreeing to pool what they see is not one of them reporting to the other.

    Bounded like a cession is. The subject has to resolve to a real nation -- an
    unrecognisable one is refused rather than quietly widened into *everything we see*,
    which would hand over far more than was agreed.
    """
    from .intelligence import share_intelligence

    if not entry.get("intel_sharing") or kind not in SHARING_KINDS or len(parties) < 2:
        return []
    named = str(entry.get("intel_about") or "").strip()
    subject = ""
    if named:
        from .reference import resolve

        subject = resolve(conn, world_id, "nation", named).value or ""
        if not subject or subject in parties:
            # An unresolvable third party, or one of the signatories: the first is a clause
            # nobody can act on, the second is an offer to spy on the person you are
            # signing with, which is a different agreement than the one that was reached.
            return []
    return [
        share_intelligence(
            conn, world_id, commitment_id=commitment_id, sharer=sharer,
            recipient=recipient, subject=subject, seq=seq, turn=0, event_id=event_id,
            scope=scope)
        for seq, (sharer, recipient) in enumerate(
            ((parties[0], parties[1]), (parties[1], parties[0])))
    ]


def _ceded_region(
    conn: sqlite3.Connection, world_id: str, entry: dict, parties: tuple[str, str]
) -> str:
    """The province a cession names, resolved through the reference boundary, or ``""``.

    The one place a model gets to say what a term is about, and it is bounded twice: the
    phrase has to resolve to a real region -- an ambiguous one is refused rather than
    guessed at, which is what the boundary is for -- and :func:`atlas.engine.treaties.
    term_diffs` then refuses it again unless the party promising it actually holds it.
    """
    written = str(entry.get("region") or "").strip()
    if not written:
        return ""
    from .reference import resolve

    found = resolve(conn, world_id, "region", written)
    return found.value or ""


def _visibility_of(conn: sqlite3.Connection, event_id: int | None) -> Visibility:
    """The visibility of the event a diff cites, defaulting to public.

    Rule INFO-002 holds a change to the observability of its cause, so a diff must be
    scoped to the event that justifies it rather than to whatever the writer assumed.
    """
    if event_id is None:
        return Visibility.PUBLIC
    row = conn.execute(
        "SELECT visibility FROM event WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        return Visibility.PUBLIC
    try:
        return Visibility(str(row["visibility"]))
    except ValueError:
        return Visibility.PUBLIC


def accept_commitment(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    commitment_id: int,
    nation_id: str,
    turn: int,
    event_id: int,
    scope: Visibility = Visibility.BILATERAL,
) -> bool:
    """Record one party's acceptance, and activate the agreement if it was the last.

    **Activation is a consequence rather than a decision**, which is what makes "pending"
    mean something. It used to be a status any of three model roles could write, so one
    signatory could bring an agreement into force on its own and every consumer of
    `relation.treaty_status` would believe it.

    Returns whether the agreement became active on this acceptance.
    """
    parties = [
        (str(row["nation_id"]), row["accepted_turn"])
        for row in conn.execute(
            "SELECT nation_id, accepted_turn FROM commitment_nation"
            "  WHERE commitment_id = ? ORDER BY nation_id", (commitment_id,))
    ]
    if nation_id not in {party for party, _accepted in parties}:
        return False

    outstanding = [
        party for party, accepted in parties
        if accepted is None and party != nation_id
    ]
    diffs = [
        Diff("commitment_nation", f"{commitment_id}|{nation_id}", "accepted_turn",
             DiffOp.SET, turn, reason_event_id=event_id, visibility_scope=scope)
    ]
    if not outstanding:
        diffs.append(
            Diff("commitment", str(commitment_id), "status", DiffOp.SET,
                 CommitmentStatus.ACTIVE.value, reason_event_id=event_id,
                 visibility_scope=scope)
        )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        return False
    conn.commit()
    return not outstanding


def implied_treaty_status(
    conn: sqlite3.Connection, world_id: str, a: str, b: str
) -> str:
    """What these two powers' active commitments say their standing treaty is.

    Split out from :func:`sync_treaty_status` so that the writer and the *checker* cannot
    drift: an invariant that asserted the column matches the commitments while computing
    "matches" its own way would eventually be testing its own arithmetic. One definition,
    one place, read by both.
    """
    active = {
        str(row["kind"])
        for row in conn.execute(
            """
            SELECT c.kind FROM commitment c
              JOIN commitment_nation ca ON ca.commitment_id = c.id AND ca.nation_id = ?
              JOIN commitment_nation cb ON cb.commitment_id = c.id AND cb.nation_id = ?
             WHERE c.world_id = ? AND c.status = ?
            """,
            (a, b, world_id, CommitmentStatus.ACTIVE.value),
        )
    }
    status = TreatyStatus.NONE
    for kind, implied in _TREATY_FROM_COMMITMENT:
        if kind.value in active:
            status = implied
    return status.value


def sync_treaty_status(
    conn: sqlite3.Connection, world_id: str, parties: tuple[str, str],
    *, event_id: int | None = None,
) -> str | None:
    """Make ``relation.treaty_status`` say what the active commitments actually are.

    **This is the seam a real-provider playtest found open.** A negotiated non-aggression
    pact wrote a `commitment` row and nothing else, while every consumer in the engine --
    the invasion guard at :func:`atlas.engine.resolution._is_friendly`, the gambit odds,
    the router, and four separate briefing lines -- reads `relation.treaty_status`. So a
    pact existed in the history a model was shown, appeared in its own briefing, and did
    not stop the engine treating a march across the border as an invasion.

    Of the two ways to close it -- teach every consumer to read commitments, or make the
    column say what the commitments say -- this is the second, because there is one column
    and seven readers, and because the column is already what the *player* is shown.
    `commitment` stays the source of truth and this is its materialised view.

    **Recomputed from scratch rather than patched**, so it is idempotent and so the same
    call handles a treaty being broken as handles one being signed: drop the commitment to
    `broken` and the next sync takes the status back down. Returns the status written, or
    ``None`` if the pair has no relation rows.

    Written as ordinary SQL rather than as a diff, matching how the commitment rows
    themselves are written a few lines above. That means neither rewinds -- a known gap in
    the diplomatic record rather than one this introduces, and the important half of it is
    that the two stay *consistent with each other* through a rewind rather than one
    reverting and leaving the other standing.
    """
    a, b = parties
    status = TreatyStatus(implied_treaty_status(conn, world_id, a, b))

    # **Both directed rows, always.** A pact one side honours and the other does not is not
    # a pact, and a one-sided write would make the invasion guard answer differently
    # depending on who was marching.
    #
    # As diffs, so the column unwinds with the agreement it reflects. Without a cause to
    # cite there is nothing to write: `event_id` is optional only so the seeding path,
    # which runs before any turn exists, can still reconcile without a rewindable record.
    present = [
        (first, second)
        for first, second in ((a, b), (b, a))
        if conn.execute(
            "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
            (world_id, first, second),
        ).fetchone()
    ]
    if not present:
        return None

    if event_id is None:
        conn.execute(
            "UPDATE relation SET treaty_status = ?"
            " WHERE world_id = ? AND ((nation_a = ? AND nation_b = ?)"
            "                      OR (nation_a = ? AND nation_b = ?))",
            (status.value, world_id, a, b, b, a),
        )
        return status.value

    # **The batch's turn comes from the event it cites, not from the world clock.** Rule
    # CORE-001 refuses a diff citing an event from a later turn than the batch, and a
    # breach recorded during turn 3 cites a turn-3 event while `world.turn_number` may
    # still read 0 in a fixture -- which refused the reconciliation and left the status
    # standing behind the agreement it was meant to follow.
    cause = conn.execute("SELECT turn FROM event WHERE id = ?", (event_id,)).fetchone()
    turn = int(cause["turn"]) if cause else int(
        conn.execute(
            "SELECT turn_number FROM world WHERE id = ?", (world_id,)
        ).fetchone()["turn_number"]
    )
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None,
            diffs=tuple(
                Diff("relation", f"{first}|{second}", "treaty_status", DiffOp.SET,
                     status.value, reason_event_id=event_id,
                     visibility_scope=_visibility_of(conn, event_id))
                for first, second in present
            ),
        ),
    )
    return status.value if outcome.applied else None


def choose_next_speaker(
    conn: sqlite3.Connection,
    *,
    participants: list[str],
    transcript: list[ChatTurn],
    last_message: str,
) -> str | None:
    """Pick who replies next in a group conversation. Deterministic, and free.

    Letting every participant answer every message multiplies the call count by the number
    of nations in the room and produces an unreadable wall of text. This is a rule rather
    than a model call for the same reason routing is: it should be cheap, inspectable, and
    incapable of escalating on its own.
    """
    candidates = [n for n in participants if n != (transcript[-1].speaker if transcript else None)]
    if not candidates:
        return None

    # Directly addressed by name wins outright.
    lowered = last_message.lower()
    for nation_id in candidates:
        row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
        if row and row["name"].lower() in lowered:
            return nation_id

    spoken = {turn.speaker for turn in transcript}
    unheard = [n for n in candidates if n not in spoken]
    if unheard:
        # Everyone gets a first word before anyone gets a second.
        return unheard[0]

    def stake(nation_id: str) -> float:
        row = conn.execute(
            "SELECT MAX(ABS(score)) AS s FROM relation WHERE nation_a = ?", (nation_id,)
        ).fetchone()
        return float(row["s"] or 0.0)

    return max(candidates, key=stake)
