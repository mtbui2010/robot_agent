"""The verifiers of a pack, chosen by name: the end-of-run look at a goal, and the checks after a step.

What: ``goal_verifier(pack, kind)`` is the callable that looks at a goal of kind ``kind`` with its declared entry: the kind's
``observe`` name in the pack's ``goal_kinds`` section, an entry of its ``verifiers`` section, whose ``callable`` is
registered in the namespace ``verifier.goal``. ``step_verifiers(pack, action)`` lists the ``verifier.step`` callables that
check a step: the entries that say ``every_step: true`` (they decide themselves which actions they look at, as the loop's
own rules did, so a robot's own release action is checked too), then the names the action lists in ``post``. ``Ctx`` is
what a verifier is given: the loop, the run, its own entry and, for a step verifier, the step's skill, its record and the
verify results so far.
Why: the loop itself decided which goals are looked at after the task and which steps get a second look
(docs/proposals/generalize_core_design.md, "Verifiers", Step S12). The standard pack (robot_agent/packs/household:
``verifiers.yaml``, ``callables/verifiers.py``) now holds those rules as named entries and the loop only asks for them.
How: pure readers of a resolved pack (data in, callables out); nothing is imported here, the pack's callables module has
registered its names. A goal verifier returns ``(True / False / None = cannot tell, evidence text, device)`` (a plain
return: no verifier emits events today); a step verifier returns the step's whole list of verify results after its check
(``ctx.vres`` unchanged, or with results replaced, dropped or added). A declared check with no callable (``kind: sensor`` /
``vlm``) is parsed by the schema but not run yet: it gives no callable here.
Which pack: ``checking_pack``. A robot's own pack when it builds on the default chain (schema 1, or ``extends`` the
standard pack) or declares a verifier itself; a pack with ``extends: null`` and no verifier gets the default pack's
checks, because the loop looked at on / in goals and checked releases and Finds for every robot before this step.
Off: a kind with no ``observe`` (or a name that is not declared or not registered) is not looked at: the goal stays on the
belief, the loop says "no sensor declared for it". A step verifier removed from the ``verifiers`` section (a pack's
``remove: {verifiers: [...]}``) is not run.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, List, Optional, Tuple

from . import loader

GOAL = "verifier.goal"
STEP = "verifier.step"
TABLE = "verifiers"


class Ctx:
    """What a verifier may use: ``loop`` (the AssistLoop), ``run`` (the run's state), ``entry`` (its own declared entry:
    ``params`` and free fields); for a step verifier also ``skill`` (the skill that ran), ``rec`` (the step's record) and
    ``vres`` (the verify results so far)."""
    __slots__ = ("loop", "run", "entry", "skill", "rec", "vres")

    def __init__(self, loop, run, entry=None, skill=None, rec=None, vres=None):
        self.loop, self.run, self.entry = loop, run, entry if isinstance(entry, dict) else {}
        self.skill, self.rec, self.vres = skill, rec, vres


@functools.lru_cache(maxsize=1)
def _default():
    return loader.default_pack()


def checking_pack(pack: Any, default: Any = None) -> Any:
    """The pack whose verifiers check a robot's run: its own when it builds on the packs of the default chain (a schema-1
    robot, a pack that extends the standard one) or declares a verifier itself; else (``extends: null`` and no verifier,
    or no pack) the default pack (``default``, else the pack of a robot that declares nothing), so such a robot keeps the
    checks every robot had before Step S12."""
    default = _default() if default is None else default
    if pack is None:
        return default
    if pack.table(TABLE) or set(default.chain[:-1]) <= set(pack.chain):
        return pack
    return default


def _callable(pack: Any, namespace: str, entry) -> Optional[Callable]:
    ref = entry.get("callable") if isinstance(entry, dict) else None
    return pack.named(namespace, ref) if ref else None


def goal_verifier(pack: Any, kind) -> Tuple[Optional[Callable], Optional[dict]]:
    """(the ``verifier.goal`` callable that looks at a goal of ``kind``, its entry), or (None, None) when the kind names no
    ``observe`` verifier, the name is not in the ``verifiers`` section, or its callable is not registered. ``pack``: the
    pack the checks come from (``checking_pack``)."""
    decl = pack.entry("goal_kinds", kind) if pack is not None and isinstance(kind, str) else None
    name = decl.get("observe") if isinstance(decl, dict) else None
    entry = pack.entry(TABLE, name) if name else None
    fn = _callable(pack, GOAL, entry)
    return (fn, entry) if fn is not None else (None, None)


def step_verifiers(pack: Any, action=None) -> List[Tuple[Callable, dict]]:
    """[(callable, entry)] that check a step of ``action``, in the order they run: the entries of the ``verifiers`` section
    that say ``every_step: true`` (table order), then the names the action's entry lists in ``post``. Only entries whose
    callable is a registered ``verifier.step`` count. ``pack``: the pack the checks come from (``checking_pack``)."""
    table = pack.table(TABLE) if pack is not None else {}
    decl = pack.entry("actions", action) if pack is not None and isinstance(action, str) else None
    names = [n for n, e in table.items() if isinstance(e, dict) and e.get("every_step") is True]
    names += list(decl.get("post") or ()) if isinstance(decl, dict) else []
    out = []
    for name in names:
        entry = table.get(name)
        fn = _callable(pack, STEP, entry)
        if fn is not None:
            out.append((fn, entry))
    return out


def observed_kinds(pack: Any) -> List[str]:
    """The goal kinds of ``pack`` that a verifier looks at after the task (in the table's order)."""
    return [k for k in (pack.table("goal_kinds") if pack is not None else {}) if goal_verifier(pack, k)[0] is not None]


__all__ = ["GOAL", "STEP", "TABLE", "Ctx", "checking_pack", "goal_verifier", "step_verifiers", "observed_kinds"]
