"""The verifiers of a pack, chosen by name: the end-of-run look at one goal, and the checks after a step.

What: ``goal_verifier(pack, kind)`` gives the ``verifier.goal`` callable (and its entry) that looks at a goal of kind
``kind``: the kind's ``observe`` name in ``goal_kinds``, an entry of the ``verifiers`` section whose ``callable`` is
registered. ``step_verifiers(pack, action)`` lists the ``verifier.step`` callables that check a step, in the order they
run: the entries that say ``every_step: true`` (table order; such a verifier decides itself which steps it looks at, as
the loop's own rules did, so a robot's own release action is checked too), then the names the action lists in ``post``.
``Ctx`` is what a verifier is given: the run, its own entry, and the few loop services it may call.
Why: the loop decided itself which goals are looked at after the task and which steps get a second look
(docs/proposals/generalize_core_design.md, "Verifiers", Step S12). The standard pack (``robot_agent/packs/household``:
``verifiers.yaml``, ``callables/verifiers.py``) holds those rules as named entries; the loop only asks for them.
How: pure readers of a resolved pack (data in, callables out); the pack's callables module registered the names. A goal
verifier returns ``(True / False / None = cannot tell, evidence text, device)`` directly (no goal verifier emits events
yet, so the generator form of the registry note is not used); a step verifier returns the step's whole list of verify
results after its check (``ctx.vres`` itself, or a list that replaces it). A declared check with no callable
(``kind: sensor`` / ``vlm``) is parsed by the schema but not run yet: it gives no callable here.
Compatibility: what a pack does not declare is the default pack's (the standard one): a kind whose entry has no
``observe`` field, or that the pack does not have (``extends: null``, ``remove``), is looked at as the default pack says;
a pack that does not build on the default chain and declares no verifier gets the default pack's step verifiers. So
every pack written before Step S12 (none
declared ``observe``) keeps exactly the old checks: on / in looked at, a release looked at twice, a far Find failed.
Off: ``observe: null`` on a kind (or a name not declared, or not registered): the goal is not looked at, it stays on the
belief and the loop says "no sensor declared for it". A pack that removes a step verifier
(``remove: {verifiers: [std.find_reach]}``) does not run it.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, List, Optional, Tuple

from . import loader

GOAL = "verifier.goal"
STEP = "verifier.step"
TABLE = "verifiers"


class Ctx:
    """What a verifier may use: ``run`` (the run's state), ``entry`` (its own declared entry), for a step verifier also
    ``skill`` (the skill that ran), ``rec`` (the step's record) and ``vres`` (the verify results so far); and the loop's
    services below. ``loop`` is kept for those services only: a verifier never sets an attribute on it."""
    __slots__ = ("loop", "run", "entry", "skill", "rec", "vres")

    def __init__(self, loop, run, entry=None, skill=None, rec=None, vres=None):
        self.loop, self.run, self.entry = loop, run, entry if isinstance(entry, dict) else {}
        self.skill, self.rec, self.vres = skill, rec, vres

    def handed(self) -> set:
        """The objects this run handed over (a release action of the contract that succeeded)."""
        return getattr(self.loop, "_handed", set())

    def decide(self, hook: str, *args):
        """An option's verdict at ``hook`` (``options.decide(hook, run, *args)``); None = no option decides."""
        return self.loop.options.decide(hook, self.run, *args)

    def look(self, step: dict):
        """Run a MoveTo / Find of the check (its effect applied on success): (SkillResult or None, skill)."""
        return self.loop._look(self.run, step)

    def second_look_seen(self, obj, place, nice, skill):
        """The check's first Find missed and the second saw ``obj``: (ok, evidence, skill), an option may ask for more."""
        return self.loop._second_look_seen(self.run, obj, place, nice, skill)

    def holding(self, default: bool) -> bool:
        """One reading of the gripper sensor (``default`` when the robot has none)."""
        return self.loop._holding(self.run.node, default=default)

    def release_actions(self):
        """The robot's own actions that release the held object (a hand-over)."""
        return self.loop.robot.model_capabilities().release_actions

    def setting(self, key: str):
        """The loop's resolved setting ``key`` (manifest, environment and overrides already applied)."""
        return self.loop.cfg[key]

    def found_point(self, obj):
        """The base-frame point of the found ``obj`` in the step's result, or None."""
        return self.loop._found_point(self.rec.result, obj)

    def place_of_far_point(self, p3):
        """The place near a far point ``p3``, or None."""
        r = self.run
        return self.loop._place_of_far_point(p3, r.node, r.places, r.world)


@functools.lru_cache(maxsize=1)
def _default():
    return loader.default_pack()


def _callable(pack: Any, namespace: str, entry) -> Optional[Callable]:
    ref = entry.get("callable") if isinstance(entry, dict) else None
    return pack.named(namespace, ref) if ref else None


def observe_source(pack: Any, kind, default: Any = None) -> Tuple[Any, Optional[str]]:
    """(the pack that says how a goal of ``kind`` is observed, the ``observe`` name or None). The robot's pack when its
    entry of the kind carries the ``observe`` field (even ``null``); else the default pack (``default``, else the pack of
    a robot that declares nothing): a pack that does not declare ``observe`` for a kind, or does not have the kind
    (``extends: null``, ``remove``), keeps the look every robot had before Step S12."""
    decl = pack.entry("goal_kinds", kind) if pack is not None and isinstance(kind, str) else None
    if not (isinstance(decl, dict) and "observe" in decl):
        pack = _default() if default is None else default
        decl = pack.entry("goal_kinds", kind) if isinstance(kind, str) else None
    name = decl.get("observe") if isinstance(decl, dict) else None
    return pack, (name if isinstance(name, str) and name else None)


def goal_verifier(pack: Any, kind, default: Any = None) -> Tuple[Optional[Callable], Optional[dict]]:
    """(the ``verifier.goal`` callable that looks at a goal of ``kind``, its entry), or (None, None) when the kind names no
    ``observe`` verifier, the name is not in the ``verifiers`` section, or its callable is not registered."""
    src, name = observe_source(pack, kind, default)
    entry = src.entry(TABLE, name) if name else None
    fn = _callable(src, GOAL, entry)
    return (fn, entry) if fn is not None else (None, None)


def step_verifiers(pack: Any, action=None, default: Any = None) -> List[Tuple[Callable, dict]]:
    """[(callable, entry)] that check a step of ``action``, in the order they run: the ``every_step: true`` entries of the
    ``verifiers`` section (table order), then the names the action's entry lists in ``post``. Only entries whose callable
    is a registered ``verifier.step`` count. A pack that neither builds on the default pack's chain nor declares a
    verifier (``extends: null``) gets the default pack's, so it keeps the checks every robot had before Step S12; a pack
    that builds on it keeps its own section, also when it removed every entry."""
    default = _default() if default is None else default
    if pack is None or not (pack.table(TABLE) or set(default.chain[:-1]) <= set(pack.chain)):
        pack = default
    table = pack.table(TABLE)
    decl = pack.entry("actions", action) if isinstance(action, str) else None
    names = [n for n, e in table.items() if isinstance(e, dict) and e.get("every_step") is True]
    names += [n for n in (decl.get("post") or ()) if n not in names] if isinstance(decl, dict) else []
    out = []
    for name in names:
        entry = table.get(name)
        fn = _callable(pack, STEP, entry)
        if fn is not None:
            out.append((fn, entry))
    return out


def observed_kinds(pack: Any, default: Any = None) -> List[str]:
    """The goal kinds of ``pack`` that a verifier looks at after the task, in the table's order."""
    return [k for k in (pack.table("goal_kinds") if pack is not None else {}) if goal_verifier(pack, k, default)[0] is not None]


__all__ = ["GOAL", "STEP", "TABLE", "Ctx", "observe_source", "goal_verifier", "step_verifiers", "observed_kinds"]
