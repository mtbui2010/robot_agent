"""What a robot reports of its pack: the header, the goal kinds with their needs, and the question kinds (Steps S27a, S32).

What: ``of(pack, kinds, missing)`` is the pure rule (data in, data out) behind the three added keys of ``GET /robot/manifest``:
``pack`` (schema, name, version, extends), ``goal_kinds`` (kind -> ``{feasible, needs, slots}``) and ``question_kinds`` (the
kinds the robot's questions carry, sorted). ``kinds`` is the goal-kind table the robot reads (name -> an object with ``needs``,
as ``core/planning/goals.table(pack)``); ``missing(goal)`` is the robot's own rule for the actions a goal lacks
(``goals.missing_actions`` bound to its actions), so the manager does not repeat it.
Why: a fleet manager kept its own table of what each goal kind needs, and it had drifted from the robot's
(docs/proposals/generalize_core_design.md, "Fleet needs"); the robot says it itself now, so a kind the manager never heard of
can be assigned (Steps S27b, S32). Additive only: no old key of the answer changes.
How: ``needs`` is a list whose item is an action or a list of alternatives (any one of them), the shape of the manager's table.
``slots`` is what the pack declares for the kind (slot name -> type); ``{}`` when it declares none: nothing is guessed, and a
reader then uses its own default. ``question_kinds`` is the ``kind`` of each declared question plus ``questions_default_kind``.
Step S32 adds one key to each goal-kind row, ``footprint`` ({objects, places}: the goal slots that are objects and places,
core/pack/fleet.py), and only when the robot's OWN pack declares a ``fleet:`` section; every other pack reports the rows it did.
Off: ``GET /robot/manifest`` leaves the three keys out when the view has no readable pack (a stub, a pack that fails to
resolve), and a reader that does not know them ignores them.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from . import fleet

TABLE = "goal_kinds"
QUESTIONS = "questions"


def _needs(kind: Any) -> list:
    """The kind's needs as plain lists: an any-of tuple becomes a list."""
    return [list(n) if isinstance(n, (tuple, list)) else n for n in getattr(kind, "needs", ()) or ()]


def _slots(entry: Any) -> dict:
    """slot name -> declared type, from a pack's goal-kind entry; {} when it declares no slots."""
    declared = entry.get("slots") if isinstance(entry, Mapping) else None
    if not isinstance(declared, Mapping):
        return {}
    return {str(n): (s.get("type") if isinstance(s, Mapping) else s) for n, s in declared.items()}


def goal_kinds(pack: Any, kinds: Mapping, missing: Callable[[dict], Any]) -> dict:
    """Every kind of ``kinds`` -> {feasible: the robot lacks no action for it, needs, slots}, in the table's order; with the
    ``footprint`` of the kind too when the robot's own pack declares a ``fleet:`` section (core/pack/fleet.py)."""
    rows = {str(name): {"feasible": not missing({"type": name}), "needs": _needs(kind),
                        "slots": _slots(pack.entry(TABLE, name))} for name, kind in kinds.items()}
    if fleet.declared(pack):
        for name, spec in fleet.kinds(pack, rows).items():
            rows[name]["footprint"] = spec
    return rows


def question_kinds(pack: Any) -> list:
    """The kinds the robot's questions carry: each declared question's ``kind`` and the default kind, sorted, no duplicates."""
    found = {e["kind"] for e in pack.table(QUESTIONS).values() if isinstance(e, Mapping) and isinstance(e.get("kind"), str) and e["kind"]}
    default = getattr(pack, "questions_default_kind", None)
    if isinstance(default, str) and default:
        found.add(default)
    return sorted(found)


def of(pack: Any, kinds: Mapping, missing: Callable[[dict], Any]) -> dict:
    """The three added keys of the manifest answer: {pack, goal_kinds, question_kinds}."""
    return {"pack": pack.header(), "goal_kinds": goal_kinds(pack, kinds, missing), "question_kinds": question_kinds(pack)}


__all__ = ["of", "goal_kinds", "question_kinds"]
