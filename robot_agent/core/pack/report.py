"""What a robot reports about its pack: the header, each goal kind with its needs, and the question kinds (``GET /robot/manifest``).

What: pure functions from a resolved pack (``RobotView.pack``) to plain JSON data. ``pack_header(pack)`` is the pack's name, schema,
version and base; ``goal_kinds(pack, missing)`` maps every goal kind the robot's table has to ``{feasible, needs, slots}``;
``question_kinds(pack)`` lists the kinds its questions carry; ``fields(pack, missing)`` joins the three into the added keys
``pack``, ``goal_kinds`` and ``question_kinds`` of the manifest answer.
Why: a fleet manager repeated each robot's needs in a table of its own and could not assign a kind it had not heard of
(docs/proposals/generalize_core_design.md, "Fleet needs", Step S27a); the robot already knows, so it says it.
How: ``feasible`` is the robot's own answer (``missing(kind)`` is empty); ``missing`` is passed in (the engine's
``goals.missing_actions`` bound to the robot's actions and pack), so this module reads data and imports no planning code.
``needs`` is the declared list: an action name, or a list of which any one will do. ``slots`` is what the kind declares, name ->
``{type, required}`` (``{}`` for a kind that declares none: the standard pack's kinds do not yet). ``question_kinds`` is the sorted
set of the ``kind`` of the pack's ``questions`` entries plus ``questions_default_kind``; kinds the engine itself puts on its own
questions are literals of the loop, not declared data, so they are not listed (a pack that declares none reports ``[]``).
Off: leave the keys out of the answer (``api/robot_info.py`` does so for a view with no readable pack); no state is kept.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from . import goal_kinds as kind_data


def pack_header(pack: Any) -> dict:
    """``{schema, name, version, extends}`` of the resolved pack (``Pack.header``), as plain data."""
    h = pack.header()
    return {"schema": int(h["schema"]), "name": str(h["name"] or ""), "version": str(h["version"] or ""),
            "extends": None if h["extends"] is None else str(h["extends"])}


def _entries(pack: Any) -> Mapping:
    """The declared goal kinds the robot's table is built from: the default table for a pack that has it or none, else the pack's own
    (the rule of ``goals.table``)."""
    return kind_data.default_table() if kind_data.is_default(pack) else pack.table(kind_data.TABLE)


def _need(n: Any) -> Any:
    return [str(a) for a in n] if isinstance(n, (list, tuple)) else str(n)


def _slots(entry: Any) -> dict:
    declared = entry.get("slots") if isinstance(entry, dict) else None
    return {str(n): dict(s) for n, s in declared.items()} if isinstance(declared, dict) else {}


def goal_kinds(pack: Any, missing: Callable[[str], list]) -> dict:
    """``{kind: {feasible, needs, slots}}`` in the table's order; ``feasible`` is True when ``missing(kind)`` is empty."""
    out = {}
    for name, entry in _entries(pack).items():
        out[str(name)] = {"feasible": not missing(name), "needs": [_need(n) for n in kind_data.fields(entry)["needs"]],
                          "slots": _slots(entry)}
    return out


def question_kinds(pack: Any) -> list:
    """The kinds the pack's questions carry, sorted, without repeats."""
    kinds = {str(e["kind"]) for e in pack.table("questions").values() if isinstance(e, dict) and e.get("kind")}
    if pack.questions_default_kind:
        kinds.add(str(pack.questions_default_kind))
    return sorted(kinds)


def fields(pack: Any, missing: Callable[[str], list]) -> dict:
    """The keys this module adds to the manifest answer: ``pack``, ``goal_kinds``, ``question_kinds``."""
    return {"pack": pack_header(pack), "goal_kinds": goal_kinds(pack, missing), "question_kinds": question_kinds(pack)}


__all__ = ["pack_header", "goal_kinds", "question_kinds", "fields"]
