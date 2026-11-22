"""The action table as pack data: which actions the engine's copies mean, and the words about each action.

What: pure readers of the ``actions`` section of a pack. ``default_table()`` is the table of a robot that declares no pack of
its own (the root pack, then the standard pack: robot_agent/packs/household/actions.yaml), read once. ``names(table, cls=...,
arg=..., plan_step=..., releases=...)`` is the one query: the names of the actions (in table order) whose class is one of
``cls``, whose argument type is one of ``arg``, whose ``plan_step`` / ``releases`` flag has that value; a field that is not
given is not looked at. ``words(table, field)`` is {action: text} of a words field (``verb``, ``ability``, ``describe``);
``prompt_text(table)`` is the planner prompt's {action: (the word for its argument, what it does)}.
Why: "the argument is a place" was written in five places with five different sets (assist_loop, reconcile, mapper, the legacy
adapter, search), the words of an action in three tables, and the sensors in a fourth set (docs/proposals/generalize_core_design.md,
"Actions", Step S10). Each reader now asks the table with ITS OWN query, so no set is unified: an action added to the table
joins every set whose query it matches, and a reader whose query matches nothing does nothing.
How: data in, answer out; nothing here names an action. ``default_table`` is the only function that reads files.
Off: a pack that declares no ``actions`` has the standard table (a robot's own table is not read by the copies yet: every copy
reads the default one, as the goal-kind view does); an entry without a field is skipped by a query on that field.
"""

from __future__ import annotations

import functools
from typing import Mapping, Optional

from . import loader
from .phrases import default_chain

TABLE = "actions"


@functools.lru_cache(maxsize=None)
def default_table(root=None) -> dict:
    """{action: entry as declared} of the packs a robot with no pack of its own gets, merged as the loader merges a chain.
    Read once; callers must not change it."""
    return loader.merge(list(default_chain(root)))[0][TABLE]


def _arg_type(entry: Mapping) -> Optional[str]:
    arg = entry.get("arg")
    return arg.get("type") if isinstance(arg, dict) else None


def names(table: Mapping, cls=(), arg=(), plan_step: Optional[bool] = None, releases: Optional[bool] = None) -> tuple:
    """The actions of ``table`` (in its order) that match every query given: class in ``cls``, argument type in ``arg``,
    ``plan_step`` equal to the flag (an entry without it is a plan step), ``releases`` is exactly true (True) or not (False)."""
    out = []
    for name, e in table.items():
        e = e if isinstance(e, dict) else {}
        if cls and e.get("class") not in cls:
            continue
        if arg and _arg_type(e) not in arg:
            continue
        if plan_step is not None and e.get("plan_step", True) is not plan_step:
            continue
        if releases is not None and (e.get("releases") is True) is not releases:
            continue
        out.append(name)
    return tuple(out)


def words(table: Mapping, field: str) -> dict:
    """{action: text} of every entry that has a non-empty text in ``field``, in the table's order."""
    return {n: e[field] for n, e in table.items() if isinstance(e, dict) and isinstance(e.get(field), str) and e[field]}


def prompt_text(table: Mapping) -> dict:
    """{action: (the word for its argument, what it does)} of every entry with an argument name and a ``describe`` text."""
    out = {}
    for n, e in table.items():
        arg = e.get("arg") if isinstance(e, dict) else None
        if isinstance(arg, dict) and arg.get("name") and isinstance(e.get("describe"), str) and e["describe"]:
            out[n] = (arg["name"], e["describe"])
    return out


__all__ = ["TABLE", "default_table", "names", "words", "prompt_text"]
