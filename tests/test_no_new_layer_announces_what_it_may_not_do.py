"""No new engine layer writes its news before its batch without taking it back on refusal.

**Every engine act that can be refused writes its news first.** An outcome event has to
exist *before* the batch so the batch's diffs can cite it, so the layer finds out only
afterwards whether the thing it announced actually happened. When the batch is refused the
event must go with it -- :func:`atlas.state.apply.unrecord_events` exists for exactly that,
and its docstring says why: left behind, the news is indistinguishable from news of
something real. Every later briefing quotes it, the treaty layer attributes from it, and the
world reasons from an event with no state behind it.

Eighteen layers do that. The bug scan of 2026-09-26 found one that did not (E5: a second
build order in one turn was refused and its "lays down" event stayed), and the review of
its fix found eight more beside it in the same two modules. A sweep of the whole engine
found fifty functions with the shape and no take-back.

**This is a ratchet, not a fix.** Most of those fifty are in war, resolution and
intelligence paths that open fix branches are still changing, and each one needs its own
refusal test to be fixed honestly. So they are listed below, by name, and this test fails on
a *fifty-first*: a new function that calls ``record_event`` and ``apply_batch`` and never
``unrecord_events``. Fixing a listed site means taking its line out -- a line that no
longer matches is reported by :func:`test_the_known_list_says_what_is_still_true`, which is
lenient on purpose so that fixes landing in any order never turn the suite red.

The check is structural and deliberately coarse. A function that handles its refusal some
other way, or whose batch cannot be refused, belongs in :data:`EXEMPT` with the reason.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "atlas"

#: Functions that write an event, apply a batch, and take nothing back when it is refused,
#: as of b021417. Each is a place a refused batch can leave false news on the record.
KNOWN: frozenset[str] = frozenset({
    "engine/air.py::_lapse_uncontested",
    "engine/air.py::_fight_over",
    "engine/air.py::strike",
    "engine/answering.py::assess_unanswered_calls",
    "engine/attribution.py::_record",
    "engine/budget.py::collect_revenue",
    "engine/budget.py::erode_development",
    "engine/capitulation.py::assess_capitulation",
    "engine/constituencies.py::assess_constituencies",
    "engine/doctrines.py::settle_shifts",
    "engine/exposure.py::_publish",
    "engine/focus.py::apply_focus",
    "engine/freeform.py::apply_freeform",
    "engine/gambit.py::_charge_stake",
    "engine/gambit.py::apply_gambit",
    "engine/intelligence.py::invest_counterintel",
    "engine/intelligence.py::assess_networks",
    "engine/intelligence.py::run_espionage",
    "engine/intent.py::propose",
    "engine/intent.py::retire",
    "engine/intent.py::pursue",
    "engine/leaders.py::assess_successions",
    "engine/logistics.py::assess_attrition",
    "engine/objectives.py::declare_objectives",
    "engine/objectives.py::prune_objectives",
    "engine/organisation.py::_record_decline",
    "engine/organisation.py::apply_sanctions",
    # The nine below are fixed on fix/high-economy (#10), alongside E5 itself.
    "engine/production.py::order_construction",
    "engine/production.py::order_project",
    "engine/production.py::order_research",
    "engine/production.py::_deliver_research",
    "engine/production.py::_deliver_project",
    "engine/production.py::_deliver_warhead",
    "engine/production.py::advance_construction",
    "engine/warheads.py::order_warhead",
    "engine/resolution.py::_decay_unpressed",
    "engine/resolution.py::_redeploy",
    "engine/resolution.py::_march_to_the_front",
    "engine/resolution.py::_invoke_defensive_pacts",
    "engine/resolution.py::_settle_war",
    "engine/resolution.py::reconcile_self_sieges",
    "engine/support.py::_lapse",
    "engine/trade.py::assess_trade",
    "engine/treaties.py::expire_commitments",
    "engine/unrest.py::assess_unrest",
    "engine/unrest.py::assess_partisans",
    "engine/waraims.py::declare_war_aims",
    "engine/waraims.py::prune_war_aims",
    "engine/wareffort.py::assess_war_effort",
})

#: Functions with the shape that are right not to take their news back, and why.
EXEMPT: dict[str, str] = {
    "engine/turn_engine.py::jump": (
        "its order-issued events record that the player gave an order, which is true "
        "whether or not the model's batch that follows is accepted"),
    "engine/leaders.py::shock_assassination": (
        "the event is cited by `seat`, which installs the successor in its own batch before "
        "the consequences batch is applied -- the government is gone whether or not the "
        "stability and morale that follow are accepted, so the news is true either way, and "
        "taking it back would orphan the seat's diffs"),
}


def _calls(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == name))
        for n in ast.walk(node))


def _announcing_without_take_back() -> set[str]:
    found: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if (_calls(fn, "record_event") and _calls(fn, "apply_batch")
                    and not _calls(fn, "unrecord_events")):
                found.add(f"{path.relative_to(_SRC).as_posix()}::{fn.name}")
    return found


def test_the_sweep_can_see_the_shape():
    """A guard on the guard: a sweep that found nothing would pass for the wrong reason."""
    found = _announcing_without_take_back()
    assert "engine/turn_engine.py::jump" in found
    assert len(found) >= 10


def test_no_new_layer_announces_what_it_may_not_do():
    new = sorted(_announcing_without_take_back() - KNOWN - EXEMPT.keys())
    assert not new, (
        "these functions write an event, then apply a batch, and never take the event back "
        "if the batch is refused -- call `unrecord_events` on the refused path (see "
        "`production.order_construction` on fix/high-economy), or add them to EXEMPT with "
        f"the reason their news is true either way: {new}")


def test_the_known_list_says_what_is_still_true():
    """Lenient on purpose: a fix can land before or after this list learns of it. What it
    must not do is hide a fix, so a line that no longer matches is said out loud."""
    stale = sorted((KNOWN | EXEMPT.keys()) - _announcing_without_take_back())
    if stale:
        warnings.warn(
            f"fixed or gone, and still listed -- take these lines out: {stale}",
            stacklevel=1)
