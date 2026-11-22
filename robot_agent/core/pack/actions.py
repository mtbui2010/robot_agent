"""The action table as pack data: the sets of actions the engine's call sites ask for, and the words of the table.

What: pure readers of the ``actions`` section of a pack (``packs/household/actions.yaml`` for a robot that declares none).
``used_for(use)`` gives the set of action names one call site means (``USES``: which classes or argument types it wants); each
call site keeps ITS OWN set, none is unified with another. ``not_plan_steps`` (``plan_step: false``), ``releasing``
(``releases: true``, in table order), ``abilities``, ``descriptions`` and ``verbs`` give the words and flags; ``core_text`` is
the engine's default run-event text (the ``constants`` section, plus the budget sentence of the phrases).
Why: "the argument is a place" was written in five places, the verbs, the abilities and the planner-prompt words were three
more tables, and the default run texts a ninth, all closed constants (docs/proposals/generalize_core_design.md, "Actions", Step
S10); a robot pack can now declare its own actions, and the standard pack's file holds the old values character for character.
How: every reader takes an optional table (a resolved pack's ``actions`` section, see ``table_of``); with none it reads the
default one, once. Nothing here changes an object, and nothing here names a robot or an action.
Off: a pack that declares no actions has no sets (empty), no words and no non-plan steps; the engine's call sites read the
default table unless a pack is given.
"""

from __future__ import annotations

import functools
from typing import Optional

from robot_agent.contract import pack as schema

from . import loader, phrases
from .phrases import default_chain

TABLE = "actions"
CONSTANTS = "constants"
CORE_TEXT_PREFIX = "core_text."
BUDGET_KEY = "llm_budget"                         # the one default text that is a phrase of the core pack (text.llm_budget)

# call site -> what it means: the classes of the action, or the types of its main argument (design, "Actions": `class`, `arg`)
def _names(words: str) -> tuple:
    return tuple(words.split())


# call site -> what it means: the classes of the action, or the types of its main argument (design, "Actions": `class`, `arg`).
# The classes are written as words in one string: they are action classes (schema `class`), not goal kinds.
USES = {
    "place_arg": {"arg_type": _names("place container")},                              # the loop: the argument is a place to bind
    "to_loc": {"class": _names("move release")},                                       # the action mappers: the param is a place
    "at_place": {"class": _names("look grasp release open close")},                    # reconcile: where the robot stands matters
    "act_here": {"class": _names("release open close")},                               # the search: a step done at the robot's place
    "between": {"class": _names("open close")},                                        # the search: a step between a MoveTo and a Find
}


@functools.lru_cache(maxsize=None)
def default_table(root=None) -> dict:
    """{action: entry as declared} of the packs a robot with no pack of its own gets, merged as the loader merges a chain.
    Read once; callers must not change it."""
    return loader.merge(list(default_chain(root)))[0][TABLE]


def table_of(pack=None) -> dict:
    """The ``actions`` section of a resolved ``pack``; the default table for no pack or a pack that declares none."""
    return (pack.table(TABLE) if pack is not None else None) or default_table()


def _rows(table):
    return [(n, e) for n, e in (table if table is not None else default_table()).items() if isinstance(e, dict)]


def used_for(use: str, table: Optional[dict] = None) -> frozenset:
    """The action names a call site means: those whose class is in the use's classes, or whose argument type is in its types."""
    want = USES[use]
    classes, types = want.get("class", ()), want.get("arg_type", ())
    return frozenset(n for n, e in _rows(table)
                     if e.get("class") in classes or (e.get("arg") or {}).get("type") in types)


def not_plan_steps(table: Optional[dict] = None) -> frozenset:
    """The actions that are not steps of a plan (``plan_step: false``; the schema's default for an entry is true)."""
    return frozenset(n for n, e in _rows(table) if not e.get("plan_step", schema.default(TABLE, "plan_step")))


def releasing(table: Optional[dict] = None) -> tuple:
    """The actions that put the held object down (``releases: true``), in the table's order."""
    return tuple(n for n, e in _rows(table) if e.get("releases") is True)


def abilities(table: Optional[dict] = None) -> dict:
    """{action: how the understand prompt describes it}, in the table's order (the order of the description)."""
    return {n: e["ability"] for n, e in _rows(table) if e.get("ability")}


def descriptions(table: Optional[dict] = None) -> dict:
    """{action: (the word of its argument, what it does)} for the planner prompt; an entry with no ``describe`` is left out."""
    return {n: ((e.get("arg") or {}).get("name", ""), e["describe"]) for n, e in _rows(table) if e.get("describe")}


def verbs(table: Optional[dict] = None) -> dict:
    """{action: the verb of its progress line} (English)."""
    return {n: e["verb"] for n, e in _rows(table) if e.get("verb")}


@functools.lru_cache(maxsize=None)
def core_text(root=None) -> dict:
    """The engine's default run-event texts, {key: text with {placeholders}}: the ``core_text.<key>`` constants of the default
    chain, and the budget sentence (the core pack's ``text.llm_budget``). Read once; copy it before changing it."""
    consts = loader.merge(list(default_chain(root)))[0][CONSTANTS]
    out = {k[len(CORE_TEXT_PREFIX):]: e["value"] for k, e in consts.items() if k.startswith(CORE_TEXT_PREFIX)}
    out[BUDGET_KEY] = phrases.text(BUDGET_KEY, root=root)
    return out


__all__ = ["TABLE", "USES", "default_table", "table_of", "used_for", "not_plan_steps", "releasing", "abilities",
           "descriptions", "verbs", "core_text"]
