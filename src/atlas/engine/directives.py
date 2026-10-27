"""What this government is *trying to do*, standing, across turns.

A player has always had orders and never had a policy. Every turn began from nothing: the
advisor argued each question on its merits, the action list offered every legal act in
catalogue order, and a refusal suggested whatever the rules could back — none of which knew
that this government had spent six years avoiding a continental war and was not about to
start one over a border province.

**These are the player's own, and they live in the workspace.** Not the database: a
directive is a statement of intent by the person playing, it changes nothing about the
world, and it must not appear in a fingerprint, survive into somebody else's save, or be
undone by a rewind. It sits beside the watchlist and the draft orders for exactly the
reasons those do — see :mod:`atlas.state.workspace`.

**They advise. They never act.** Nothing here writes a diff, orders anything, or removes a
legal option from a list. What a directive does is *weigh*: the advisor is told what the
government is trying to do, the action list puts what serves it first, a refusal's
alternatives are offered in that order, and a briefing says so out loud. A player who wants
to do the opposite of their own policy can, in one click, with no argument.

**Not doctrines for anybody else.** What a *tracked nation* is trying to do is a different
feature: engine-owned, in the database, subject to the rules a model is held to. This is a
preference belonging to the person at the keyboard, and conflating the two would put a
player's private note into the world's state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = [
    "DIRECTIVES",
    "Directive",
    "DirectiveKind",
    "active",
    "as_lines",
    "favour",
    "set_active",
]

_KEY = "directives"

#: How much a directive may move something in a ranking. Deliberately small: a directive is
#: a thumb on the scale and never a gate, so a strongly-favoured act cannot leapfrog an act
#: the rules actually make better, and a counselled-against act is never removed.
WEIGHT = 1.0


@dataclass(frozen=True, slots=True)
class DirectiveKind:
    """One standing policy a government can adopt, and what it makes of the options."""

    id: str
    #: What it is called on screen, in the government's own voice.
    label: str
    #: The sentence an advisor is told and a briefing prints.
    blurb: str
    #: Whether it names a power -- *contain whom*, *protect whom*.
    names_a_power: bool = False
    #: Action types this policy inclines the government toward.
    favours: tuple[str, ...] = ()
    #: Action types it inclines against. **Never removed, only ranked lower**, because a
    #: policy that could hide a legal act from its own government is a policy that governs.
    against: tuple[str, ...] = ()


#: The six. Each is a sentence a foreign minister could actually say, and each declares what
#: it makes of the catalogue rather than being interpreted somewhere else.
DIRECTIVES: tuple[DirectiveKind, ...] = (
    DirectiveKind(
        "avoid_continental_war", "Avoid a continental war",
        "This government's standing policy is to avoid a general war on the continent. It "
        "prefers to buy time, to talk, and to let a quarrel cool.",
        favours=("diplomatic_message", "propose_treaty", "sue_for_peace"),
        against=("declare_war",)),
    DirectiveKind(
        "contain", "Contain a rival",
        "This government's standing policy is containment: to check the growth of one power "
        "without fighting it directly.",
        names_a_power=True,
        favours=("propose_treaty", "espionage", "counterintelligence", "raise_units"),
        against=("declare_war",)),
    DirectiveKind(
        "restore_borders", "Restore historic borders",
        "This government holds that certain ground is rightfully its own and intends to "
        "have it back.",
        favours=("declare_war", "move_units", "propose_treaty"),
        against=()),
    DirectiveKind(
        "naval_supremacy", "Prioritise naval supremacy",
        "This government's first call on its yards and its treasury is the fleet.",
        favours=("raise_units", "build_project", "economic_investment"),
        against=()),
    DirectiveKind(
        "protect_client", "Protect a client state",
        "This government has undertaken to keep one smaller power standing, and treats a "
        "threat to it as a threat to itself.",
        names_a_power=True,
        favours=("diplomatic_message", "propose_treaty", "move_units", "declare_war"),
        against=()),
    DirectiveKind(
        "industrialise", "Industrialise despite the opposition",
        "This government intends to build, and has accepted that it will be unpopular for "
        "it.",
        favours=("build_project", "economic_investment", "raise_units"),
        against=("sue_for_peace",)),
)

_BY_ID = {kind.id: kind for kind in DIRECTIVES}


@dataclass(frozen=True, slots=True)
class Directive:
    """One adopted policy: which kind, and the power it is about where it names one."""

    kind: str
    subject: str = ""

    @property
    def spec(self) -> DirectiveKind | None:
        return _BY_ID.get(self.kind)

    def describe(self, names: dict[str, str] | None = None) -> str:
        """The policy in a sentence, with the power named if it has one."""
        spec = self.spec
        if spec is None:
            return ""
        if not self.subject:
            return spec.blurb
        who = (names or {}).get(self.subject, self.subject)
        return f"{spec.blurb} The power in question is {who}."


# --------------------------------------------------------------------------- keeping them


def active(conn: sqlite3.Connection) -> list[Directive]:
    """The directives this workspace holds, or none.

    Tolerant on the way in, like every other thing the workspace keeps: a file a person can
    edit is a file that will sometimes be wrong, and a strategy game that refused to open
    over a malformed note would be absurd. Anything unrecognisable is dropped.
    """
    from ..state.workspace import recall

    kept = recall(conn, _KEY, []) or []
    if not isinstance(kept, list):
        return []
    found: list[Directive] = []
    for entry in kept:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        if kind not in _BY_ID:
            continue
        directive = Directive(kind, str(entry.get("subject") or ""))
        # A kind that names a power and has not been given one is not yet a policy: it is
        # half a sentence, and telling an advisor to contain nobody in particular is worse
        # than telling it nothing.
        if _BY_ID[kind].names_a_power and not directive.subject:
            continue
        if directive not in found:
            found.append(directive)
    return found


def set_active(conn: sqlite3.Connection, directives: list[Directive]) -> bool:
    """Keep these as the standing policy. ``False`` if this world has nowhere to keep them."""
    from ..state.workspace import remember

    return remember(conn, _KEY, [
        {"kind": d.kind, "subject": d.subject} for d in directives
        if d.kind in _BY_ID
    ])


# ------------------------------------------------------------------------- reading them


def as_lines(
    conn: sqlite3.Connection, world_id: str, directives: list[Directive] | None = None
) -> list[str]:
    """The standing policy in words, for a prompt or a briefing.

    Named powers are resolved to their names, because an advisor told to contain `sov`
    is being handed a database key and asked to sound like a foreign ministry.
    """
    found = active(conn) if directives is None else directives
    if not found:
        return []
    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    return [directive.describe(names) for directive in found if directive.spec]


def favour(directives: list[Directive], action_type: str, target: str = "") -> float:
    """How much the standing policy inclines toward this act, positive or negative.

    **A thumb on the scale, never a gate.** The value is bounded by `WEIGHT` per directive
    and is only ever used to order a list somebody is already allowed to choose from. A
    directive that could remove an option would be a policy that governs rather than one
    that advises, and the player is the government here.

    A directive that names a power counts double when the act is aimed at that power, and
    not at all when it is aimed elsewhere -- containing the Soviet Union says nothing about
    what to do in South America.
    """
    score = 0.0
    for directive in directives:
        spec = directive.spec
        if spec is None:
            continue
        if spec.names_a_power and directive.subject and target:
            if directive.subject != target:
                continue
            weight = WEIGHT * 2.0
        else:
            weight = WEIGHT
        if action_type in spec.favours:
            score += weight
        if action_type in spec.against:
            score -= weight
    return score
