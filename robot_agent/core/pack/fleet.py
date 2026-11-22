"""The footprint of each goal kind as pack data: which slots of a goal are objects and which are places (Step S32).

What: pure readers of the pack's ``fleet:`` section (table ``fleet.kinds``: kind -> ``{footprint: {objects, places}}``).
``footprint(entry)`` is the resolved footprint of one kind: ``objects`` and ``places`` are lists of GOAL SLOT NAMES (``object``,
``from``, ``to``, ...); ``kinds(pack, names)`` gives it for every name; ``declared(pack)`` tells whether the robot's OWN pack
declares a ``fleet:`` entry.
Why: a fleet manager decides which goals may run at the same time from the objects and places each goal touches, and it keeps
that rule per kind in its own code (objects = the goal's object, places = its ``from`` and ``to``, and for ``open`` / ``close``
also the object). The robot says it itself now, so a kind a manager never heard of gets the right footprint
(docs/proposals/generalize_core_design.md, "Fleet needs", Steps S32 and S27b).
How: the standard pack (robot_agent/packs/household/fleet.yaml) holds the old rule as data: ``open`` and ``close`` list
``from``, ``to`` and ``object`` as places; a kind no pack names takes ``DEFAULT`` (objects = ``object``, places = ``from`` and
``to``). A kind declared with only one of the two lists takes the default for the other.
Off: the report (core/pack/report.py) shows a ``footprint`` per kind only when the robot's OWN pack declares a ``fleet:``
section. A pack that does not (every pack of today, every schema-1 manifest) reports nothing new, and a reader that does not
know the key ignores it; the resolved default is still what ``footprint(None)`` returns.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

TABLE = "fleet.kinds"
DEFAULT = {"objects": ["object"], "places": ["from", "to"]}     # the rule of robot_fleet/graph.py before the pack said it


def _names(value: Any, fallback: list) -> list:
    """The slot names of one footprint list as declared (strings only, in order); a copy of ``fallback`` when not a list."""
    if not isinstance(value, (list, tuple)):
        return list(fallback)
    return [v for v in value if isinstance(v, str) and v]


def footprint(entry: Any) -> dict:
    """{objects, places} of one kind; ``entry`` is the table's entry, or None for a kind no pack names (the default)."""
    declared = entry.get("footprint") if isinstance(entry, Mapping) else None
    declared = declared if isinstance(declared, Mapping) else {}
    return {k: _names(declared.get(k), DEFAULT[k]) for k in ("objects", "places")}


def kinds(pack: Any, names: Iterable) -> dict:
    """Every kind of ``names`` -> its resolved footprint, in the order of ``names``."""
    table = pack.table(TABLE)
    return {str(n): footprint(table.get(n)) for n in names}


def declared(pack: Any) -> bool:
    """Does the robot's OWN pack declare a ``fleet:`` entry? A schema-1 manifest (no pack name) and a pack that only builds
    on the standard one never do."""
    own = getattr(pack, "name", "")
    return bool(own) and own in ((getattr(pack, "origin", None) or {}).get(TABLE) or {}).values()


__all__ = ["TABLE", "DEFAULT", "footprint", "kinds", "declared"]
