"""Option ``bt_goal`` (default off, prototype): each goal is run by a behaviour tree declared as data for its kind, not by a SAGE plan.

What: service ``runs_goals`` says yes when EVERY goal has a usable tree (``tree_for``: a labelled tree for its type, no ``skip_if`` field set, every
slot filled, every ``act`` action and condition known, an Ask skill and a phrase in the run's language for ``ask`` / ``confirm`` / ``reply``); the
planner is then not called. Hook ``on_run_goals`` ticks the trees in goal order (``core/planning/behavior_tree.py``): ``act`` = one engine step
(service ``try_step``: guard, verify, records as always), ``cond`` reads the belief, ``ask`` / ``confirm`` / ``reply`` are language-pack phrases.
While a tree runs, ``on_step_failed`` keeps the engine's recovery out: the tree's Fallback / Retry / Ask decide. A failed tree ends the trees; the
engine's plan end then looks at the goals and gives what does not hold to the SAGE replan (the LLM takes over).
Why: a readable, declared control flow per goal kind and no planner call for it (docs/bt_goal.md).
Data: ``planner.options.bt_goal.trees`` {type: {tree, skip_if, source | assumed}}; none or unlabelled: nothing happens (SAGE, as when off).
Switch off: key ``enabled`` (run option ``bt_goal``), off by default. Event: lab flag ``bt_goal`` {goal, status} ("claimed" once, then per goal).
"""

from __future__ import annotations

from typing import Optional

from robot_agent.core.options import NEXT, STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning import langdata, names, pack_data, speech
from robot_agent.core.run_control import yes_no

FLAG = "bt_goal"
SLOTS = ("object", "from", "to")               # the goal fields a tree may name (the engine's goal contract)
ASKS = ("ask", "confirm")
SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)), Key("trees", "dict", {}, sources=("manifest",))),
    hooks=("on_run_goals", "on_step_failed"),
    services=("runs_goals",),
    order=5,
    enabled="enabled",
    doc="Each goal of a kind with a declared behaviour tree (planner.options.bt_goal.trees) is run by that tree, not by a planner call.",
)


def holds(name: str, args, world, placed) -> bool:
    """The belief predicate ``name`` (pure): holding o, hand_free, at p, placed o p (put at p and not in the hand)."""
    held = getattr(world, "holding", None)
    preds = {"holding": lambda o: held == o, "hand_free": lambda: held is None, "at": lambda p: getattr(world, "arrived", None) == p,
             "placed": lambda o, p: (placed or {}).get(o) == p and held != o}
    try:
        return bool(preds[name](*args))
    except (KeyError, TypeError):
        return False


CONDITIONS = ("holding", "hand_free", "at", "placed")


def tree_for(goal: dict, trees, actions, can_ask: bool, has_phrase, source=None) -> Optional[list]:
    """The tree that runs ``goal``, its slots filled (pure; ``source``: where the object is when the goal names no ``from``, e.g. the
    engine's trusted object memory); None when no usable tree is declared for it."""
    entry = trees.get(goal.get("type")) if isinstance(trees, dict) else None
    if not pack_data.labelled(entry) or not bt.valid(entry.get("tree")) or any(goal.get(f) for f in entry.get("skip_if") or ()):
        return None
    tree = bt.fill(entry["tree"], {**{k: goal.get(k) for k in SLOTS}, "from": goal.get("from") or source})
    for leaf in bt.leaves(tree) if tree else ():
        kind, arg = leaf[0], leaf[1]
        if (kind == "act" and arg not in actions or kind == "cond" and arg not in CONDITIONS or kind in ASKS and not can_ask
                or kind in ASKS + ("reply",) and not has_phrase(arg)):
            return None
    return tree


def plan_trees(goals, trees, actions, can_ask: bool, has_phrase, source=lambda obj: None) -> Optional[list]:
    """One filled tree per goal, in goal order; None when any goal has none (then SAGE plans the whole run)."""
    out = [tree_for(g, trees, actions, can_ask, has_phrase, source(g.get("object"))) for g in goals or ()]
    return out if out and all(t is not None for t in out) else None


def pack_words(lang: str, refs) -> list:
    """The words of the language-pack lists ``refs`` (``block.key``, e.g. ``ask_next.retry``) of ``lang``; [] for a list it lacks."""
    out = []
    for ref in refs:
        blk, _dot, key = ref.partition(".")
        words = langdata.block(lang, blk).get(key)
        out += [w for w in words if isinstance(w, str) and w.strip()] if isinstance(words, list) else []
    return out


class _Leaves:
    """The leaves of one goal's tree over the engine services (the host of ``behavior_tree.tick``)."""

    def __init__(self, svc, run, goal) -> None:
        self.svc, self.run, self.goal, self.lang = svc, run, goal, getattr(run, "lang", None) or "en"

    def cond(self, name, *args):
        return holds(name, args, self.run.world, getattr(self.run, "placed", None))

    def act(self, action, obj):
        return (yield from self.svc.try_step(self.run, {"action": action, "object": obj}))

    def ask(self, key, *refs):
        ans = yield from self._ask(key)
        return None if ans is None else names.says([ans], pack_words(self.lang, refs), (self.lang,))

    def confirm(self, key):
        ans = yield from self._ask(key)
        return None if ans is None else yes_no(ans)[0] is True

    def reply(self, key):
        text = self._phrase(key)
        yield self.run.ev("status", msg=str(text), say=text)
        return True

    def _phrase(self, key):
        obj = self.svc.said_of(self.run, self.goal.get("object")) if self.goal.get("object") else ""
        return speech.pack_phrase(self.svc.robot, key, self.lang, object=obj, place=self.goal.get("to") or "")

    def _ask(self, key):
        """Flow: the question ``key`` (a clarify event, the engine's ``ask``): the answer ('' on silence), None when the run was stopped."""
        q = self._phrase(key)
        yield self.run.ev("clarify", question=q, round=0, kind=FLAG)
        ans = self.svc.ask(self.run, q) or ""
        stop = self.svc.cancelled(self.run)
        if stop:
            yield stop
            return None
        return ans


class BtGoal(Option):
    spec = SPEC

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(FLAG, {"trees": None, "active": False})

    def runs_goals(self, run):
        """Service: True when every goal of ``run`` has a usable tree (they are kept for ``on_run_goals``), else None (SAGE plans)."""
        trees = pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "trees")
        robot, lang = self.svc.robot, getattr(run, "lang", None) or "en"
        spec = getattr(robot, "ask_spec", None)
        found = plan_trees(list(getattr(run, "goals", None) or ()), trees, set(getattr(robot, "plan_actions", None) or ()),
                           callable(spec) and spec() is not None, lambda k: speech.pack_phrase(robot, k, lang, object="", place="") is not None,
                           lambda obj: self.svc.remembered(run, obj) if obj else None)
        self._state(run)["trees"] = found
        if found:
            self.svc.lab(FLAG, goal=len(found), status="claimed")
        return True if found else None

    def on_run_goals(self, run):
        """Flow: tick each goal's tree in order; a failed one ends the trees (the plan end and the SAGE replan follow). STOP: the run ended."""
        s = self._state(run)
        for goal, tree in zip(list(run.goals), s["trees"] or ()):
            s["active"] = True
            try:
                status = yield from bt.tick(tree, _Leaves(self.svc, run, goal))
            finally:
                s["active"] = False
            self.svc.lab(FLAG, goal=f"{goal.get('type')} {goal.get('object')} {goal.get('to')}", status=status)
            if status == bt.STOPPED:
                return STOP
            if status != bt.SUCCESS:
                break
        return NEXT if s["trees"] else None

    def on_step_failed(self, run, st, code, reason, action, obj):
        """Flow: a step of a tree failed: the tree decides (NEXT, no engine recovery); outside a tree None (the engine's path)."""
        return NEXT if self._state(run)["active"] else None
        yield                                            # a flow hook: a generator that yields nothing


register(SPEC, BtGoal)
