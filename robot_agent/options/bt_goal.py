"""Option ``bt_goal`` (default ON, and it does nothing for a robot that declares no ``trees``): each goal is a small declared system, a behaviour tree given as data for its kind, not a SAGE plan.

What: service ``runs_goals`` says yes when a goal has a usable tree (``tree_for``; key ``mixed``, default ON, below; with it off: when EVERY goal has one); the planner is then not called for those goals. Hook ``on_run_goals`` ticks
the trees in goal order (``core/planning/behavior_tree.py``): ``act`` = one engine step WITH the engine's own recovery ladder (``try_step`` ladder:
retry, wait schedule, search legs, refind, its help question); only the ladder's planner rung is held back (service ``no_replan``) and fails the
Act. A no-retry code ends the run at once. A tree with no way left: ``exhausted_action`` on the failed code's rule of the same declared table.
Key ``exhausted``: ``ask_or_stop`` (default) asks once per goal (``menu`` phrases) when a person can help and ticks the tree once more on a yes or
a place, else reports and stops; ``sage_once`` hands the remaining goals and the facts to the SAGE planner once where A would stop.
Key ``mixed`` (default ON): goals of a kind WITHOUT a tree no longer make SAGE plan the whole run: the goals go in the reader's order; a run of
goals with trees is ticked, a run of goals without trees is planned by SAGE once, from the world as it is then (service ``plan_goals``), and its
steps run through the engine's own step path and ladder; a step with no way left hands the goals left to SAGE (``_to_sage``). Not one goal has
a tree: SAGE plans the run as when off.
Why: a declared control flow per goal kind, the engine's own recovery inside it, no planner call unless the policy says so (docs/bt_goal.md).
Data: ``planner.options.bt_goal.trees`` (else the pack's ``trees`` table) {type: {tree, skip_if, needs_openable, ladder, events, source | assumed}
or a list, first usable}, ``menu``. Tree keys (next to ``tree``, per entry; none = as above): ``ladder: false`` runs each ``act`` without the engine's
ladder (the tree's own Retry / Fallback decide), ``events: true`` sends each node as a ``tree_node`` event; the ``ask`` leaf also reads the words of
``<question>.<choice>`` of the robot pack's ``questions`` (``core/planning/tree_host.py``).
Switch off: key ``enabled`` (run option ``bt_goal=0``), on by default. Event: lab flag ``bt_goal``: {goal, status} ("claimed", per goal, the
exhausted action) and {goal, node, status} per node.
"""

from __future__ import annotations

from typing import Optional

from robot_agent.contract.recovery import Recovery
from robot_agent.core.options import NEXT, STOP, Key, Option, OptionSpec, register
from robot_agent.core.pack import predicates
from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import names, pack_data, speech, tree_host
from robot_agent.core.run_control import yes_no

FLAG = "bt_goal"
SLOTS = ("object", "from", "to")               # the goal fields a tree may name (the engine's goal contract)
DELIVERY = "delivery"                          # slot {delivery}: the robot's phrase the engine's deliver template says (goals.compile_steps)
ASKS = ("ask", "confirm")
POLICIES = ("ask_or_stop", "sage_once")
CHANGED = "changed"                            # a tree status of its own: a goal change said meanwhile ends the trees
HEALTH = (Recovery.WAIT_HEALTH, Recovery.HEALTH_CHECK)   # rules about the robot's own health: no person can help
SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", True, aliases=(FLAG,)), Key("trees", "dict", {}, sources=("manifest",)),
          Key("exhausted", "str", POLICIES[0], choices=POLICIES), Key("menu", "dict", {}, sources=("manifest",)),
          Key("mixed", "bool", True)),
    hooks=("on_run_goals", "on_step_failed"),
    services=("runs_goals", "no_replan"),
    order=5,
    enabled="enabled",
    doc="Each goal of a kind with a declared behaviour tree is run by that tree with the engine's own recovery ladder; no way left: ask or stop (or SAGE once).",
)


def holds(name: str, args, world, placed) -> bool:
    """The belief predicate ``name`` (pure), read from the pack's predicate table (core/pack/predicates.py; the standard pack:
    holding o, hand_free, at p, placed o p, opened c, closed c); False for an unknown name or a wrong number of arguments."""
    return predicates.holds_in_tree(name, args, world, placed)


CONDITIONS = predicates.names(predicates.TREE)


def declared_openable(env, key, rule) -> bool:
    """Is ``key`` a place the robot declares openable (pure): its ENV entry carries an ``openable`` field and the robot's rule (``rule(key)``,
    the adapter's or the field) says yes. Not an ENV key, no field (state unknown): False, so an entry that needs it is not used."""
    entry = (env or {}).get(key) if key else None
    return isinstance(entry, dict) and "openable" in entry and bool(rule(key))


def pick(goal: dict, trees, actions, can_ask: bool, has_phrase, source=None, extra=None, openable=None) -> Optional[tuple]:
    """``(tree, keys)`` that run ``goal`` (pure): the tree with its slots filled and the entry's opt-in keys (``tree_host.tree_keys``); None when no
    usable tree is declared for it. ``source``: where the object is when the goal names no ``from``, e.g. the engine's trusted object memory;
    ``extra``: more slot values, e.g. ``delivery``. A kind may declare a list of entries: the first one the goal and the robot can use (e.g. a
    ``say`` with a place, then one without; an entry with ``needs_openable`` slots only when ``openable(place)`` says yes for each, e.g. Open
    the source before Find, then one without)."""
    entries = trees.get(goal.get("type")) if isinstance(trees, dict) else None
    for entry in entries if isinstance(entries, list) else [entries]:
        tree = _filled(entry, goal, actions, can_ask, has_phrase, {**(extra or {}), "from": goal.get("from") or source}, openable)
        if tree is not None:
            return tree, tree_host.tree_keys(entry)
    return None


def tree_for(goal: dict, trees, actions, can_ask: bool, has_phrase, source=None, extra=None, openable=None) -> Optional[list]:
    """The tree that runs ``goal``, its slots filled (``pick`` without the keys); None when no usable tree is declared for it."""
    found = pick(goal, trees, actions, can_ask, has_phrase, source, extra, openable)
    return found[0] if found else None


def _filled(entry, goal, actions, can_ask, has_phrase, values, openable=None) -> Optional[list]:
    """One declared entry filled for ``goal``, or None when the robot, the language or the goal cannot use it."""
    if not pack_data.labelled(entry) or not bt.valid(entry.get("tree")) or any(goal.get(f) for f in entry.get("skip_if") or ()):
        return None
    if any(not (openable and openable({**goal, **values}.get(s))) for s in entry.get("needs_openable") or ()):
        return None
    tree = bt.fill(entry["tree"], {**values, **{k: goal.get(k) for k in SLOTS if k != "from"}})
    for leaf in bt.leaves(tree) if tree else ():
        kind, arg = leaf[0], leaf[1]
        if (kind == "act" and arg not in actions or kind == "cond" and arg not in CONDITIONS or kind in ASKS and not can_ask
                or kind in ASKS + ("reply",) and not has_phrase(arg)):
            return None
    return tree


def plan_picks(goals, trees, actions, can_ask: bool, has_phrase, source=lambda obj: None, extra=None, openable=None, mixed: bool = False) -> Optional[list]:
    """One ``pick`` per goal, in goal order; None when any goal has none (then SAGE plans the whole run). ``mixed``: a goal without a tree is
    None in the list (SAGE plans it), and the result is None only when NO goal has a tree."""
    out = [pick(g, trees, actions, can_ask, has_phrase, source(g.get("object")), extra, openable) for g in goals or ()]
    if mixed:
        return out if any(t is not None for t in out) else None
    return out if out and all(t is not None for t in out) else None


def plan_trees(goals, trees, actions, can_ask: bool, has_phrase, source=lambda obj: None, extra=None, openable=None, mixed: bool = False) -> Optional[list]:
    """One filled tree per goal (``plan_picks`` without the keys); a goal without a tree is None in the list under ``mixed``."""
    picks = plan_picks(goals, trees, actions, can_ask, has_phrase, source, extra, openable, mixed)
    return None if picks is None else [p[0] if p else None for p in picks]


def exhausted_action(rule, policy: str, asked: bool) -> str:
    """What follows when a goal's tree has no way left (pure), from the failed code's rule of the declared recovery table (None: no step
    failed, a condition did): "stop" for a code never retried (the engine has ended the run already); "ask" once (``asked`` False) when a
    person can help (the rule is no robot-health rule); else "sage" under policy ``sage_once``, "stop" under ``ask_or_stop``."""
    if rule is not None and not rule.retry_allowed:
        return "stop"
    if rule is not None and not asked and rule.action not in HEALTH:
        return "ask"
    return "sage" if policy == "sage_once" else "stop"


def read_menu(answer: str, words: dict, place: Optional[str], lang: str) -> str:
    """The menu answer (pure): "stop" (a stop word: the person's own stop, under every policy), "place" (it names a place), "retry" (a retry
    word), else "none" (silence, not understood)."""
    if not answer:
        return "none"
    for kind, ok in (("stop", True), ("place", bool(place)), ("retry", True)):
        if ok and (kind == "place" or names.says([answer], words.get(kind) or (), (lang,))):
            return kind
    return "none"


def source_place(place: Optional[str], goal: dict) -> Optional[str]:
    """The place a menu answer names as where the goal's object is (pure): None for the goal's own destination (an answer that repeats where
    the object should go says nothing about where it is; read as a source it would be a false sighting)."""
    return None if place and place == goal.get("to") else place


def pack_words(lang: str, refs, questions=None) -> list:
    """The words of the lists ``refs`` (``block.key``, e.g. ``ask_next.retry``) of the language pack in ``lang``; [] for a list it lacks, unless
    ``questions`` (a function giving the robot pack's ``questions`` table) knows the ref as ``<question>.<choice>`` (``tree_host.pack_words``)."""
    return tree_host.pack_words(lang, refs, questions)


def label(node) -> str:
    """A node as the trace names it: a leaf with its arguments, a composite by its type."""
    return node[0] if node[0] in bt.COMPOSITES else f"retry {node[1]}" if node[0] == "retry" else " ".join(map(str, node))


class _Leaves:
    """The leaves of one goal's tree over the engine services (the host of ``behavior_tree.tick``)."""

    def __init__(self, svc, run, goal, idx, keys=None) -> None:
        self.svc, self.run, self.goal, self.idx, self.lang = svc, run, goal, idx, getattr(run, "lang", None) or "en"
        self.keys = {**tree_host.DEFAULT_KEYS, **(keys or {})}   # the entry's opt-in keys ``ladder`` / ``events``
        self.changed = False                         # a goal change was said: the tree gave the run back before its next step

    def note(self, path, node, status):
        self.svc.lab(FLAG, goal=self.idx, node=f"{path} {label(node)}", status=status)
        if self.keys["events"]:                      # key ``events``: the node goes to the public event stream too
            self.svc.emit(self.run, tree_host.EVENT, **tree_host.node_event(self.idx, path, label(node), status))

    def cond(self, name, *args):
        return holds(name, args, self.run.world, getattr(self.run, "placed", None))

    def act(self, action, obj):
        waits = getattr(self.svc, "changes_waiting", None)
        if callable(waits) and waits(self.run):      # the engine applies the change at its next step boundary, as between two plan steps
            self.changed = True
            return None
        return (yield from self.svc.try_step(self.run, {"action": action, "object": obj}, ladder=self.keys["ladder"]))

    def ask(self, key, *refs):
        ans = yield from self.put(self._phrase(key))
        return None if ans is None else names.says([ans], pack_words(self.lang, refs, self._questions), (self.lang,))

    def _questions(self) -> dict:
        """The questions table of the robot's pack ({} for a stub robot with no pack)."""
        pack = getattr(self.svc.robot, "pack", None)
        return pack.table("questions") if pack is not None else {}

    def confirm(self, key):
        ans = yield from self.put(self._phrase(key))
        return None if ans is None else yes_no(ans)[0] is True

    def reply(self, key):
        text = self._phrase(key)
        yield self.run.ev("status", msg=str(text), say=text)
        return True

    def _phrase(self, key):
        obj = self.svc.said_of(self.run, self.goal.get("object")) if self.goal.get("object") else ""
        return speech.pack_phrase(self.svc.robot, key, self.lang, object=obj, place=self.goal.get("to") or "")

    def put(self, q):
        """Flow: the question ``q`` (a clarify event, the engine's ``ask``): the answer ('' on silence), None when the run was stopped."""
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
        return run.__dict__.setdefault("opt", {}).setdefault(FLAG, {"trees": None, "active": False, "failed": None})

    def _key(self, name):
        return pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), name)

    def _declared(self):
        """The trees to run: the key (run option, manifest), else the robot pack's ``trees`` table (``tree_host.declared_trees``)."""
        pack = getattr(self.svc.robot, "pack", None)
        return tree_host.declared_trees(self._key("trees"), (lambda: pack.table("trees")) if pack is not None else None)

    def _picks(self, run, goals, mixed=False):
        robot, lang = self.svc.robot, getattr(run, "lang", None) or "en"
        spec, text, places = getattr(robot, "ask_spec", None), getattr(robot, "text", None), getattr(run, "places", None)
        return plan_picks(goals, self._declared(), set(getattr(robot, "plan_actions", None) or ()), callable(spec) and spec() is not None,
                          lambda k: speech.pack_phrase(robot, k, lang, object="", place="") is not None,
                          lambda obj: self.svc.remembered(run, obj) if obj else None,
                          {DELIVERY: text(DELIVERY, lang) if callable(text) else ""},
                          lambda k: places is not None and declared_openable(getattr(places, "env", None), k, places.openable), mixed)

    def runs_goals(self, run):
        """Service: True when every goal of ``run`` has a usable tree (they are kept for ``on_run_goals``), else None (SAGE plans)."""
        picks = self._picks(run, list(getattr(run, "goals", None) or ()), mixed=self.cfg.get("mixed", True) is not False)    # not ``_key``: it reads a False (a run option set to 0) as empty and gives the default
        found = None if picks is None else [p[0] if p else None for p in picks]
        self._state(run)["trees"] = found
        self._state(run)["keys"] = None if picks is None else [p[1] if p else None for p in picks]
        if found:
            self.svc.lab(FLAG, goal=len(found), status="claimed")
        return True if found else None

    def no_replan(self, run, step):
        """Service: while a tree runs, the ladder's planner rung is held back (the Act fails, the tree decides); else None."""
        s = self._state(run)
        if not s["active"]:
            return None
        s["failed"] = {**(s["failed"] or {"code": None, "reason": ""}), "step": dict(step), "rung": "planner"}
        return True

    def on_step_failed(self, run, st, code, reason, action, obj):
        """Flow: a step of a tree failed: its facts are kept (code, step, reason); None: the engine's own recovery runs."""
        if self._state(run)["active"]:
            self._state(run)["failed"] = {"code": code, "step": dict(st), "reason": str(reason), "rung": "ladder"}
        return None
        yield                                            # a flow hook: a generator that yields nothing

    def _tick(self, run, idx, goal, tree, keys=None):
        """Flow: one pass of ``tree`` (its opt-in ``keys``) for goal number ``idx``: SUCCESS, FAILURE, STOPPED or CHANGED (a goal change waits: the run goes back)."""
        s = self._state(run)
        s["active"], s["failed"] = True, None
        s.setdefault("from", {})[idx] = goal.get("from") or (self.svc.remembered(run, goal["object"]) if goal.get("object") else None)
        host = _Leaves(self.svc, run, goal, idx, keys)
        try:
            status = yield from bt.tick(tree, host)
            return CHANGED if host.changed else status
        finally:
            s["active"] = False

    def on_run_goals(self, run):
        """Flow: tick each goal's tree in order; a tree with no way left goes to ``_exhausted``. STOP: the run ended; NEXT: the plan's end."""
        goals, trees, keys = list(run.goals), self._state(run)["trees"], self._state(run).get("keys") or []
        idx = 0
        while idx < len(goals) and idx < len(trees or ()):
            goal, tree, n = goals[idx], trees[idx], 1
            if tree is None:                                 # key mixed: a run of goals without a tree, planned by SAGE at this point
                while idx + n < min(len(goals), len(trees)) and trees[idx + n] is None:
                    n += 1
                status = yield from self._sage_goals(run, idx, goals[idx:idx + n], goals[idx:])
            else:
                status = yield from self._tick(run, idx, goal, tree, keys[idx] if idx < len(keys) else None)
                if status == bt.FAILURE:
                    status = yield from self._exhausted(run, idx, goal, goals[idx:])
            self.svc.lab(FLAG, goal=f"{goal.get('type')} {goal.get('object')} {goal.get('to')}", status=status)
            if status in (bt.STOPPED, STOP):
                return STOP
            if status in (NEXT, CHANGED):
                return NEXT                                  # SAGE took the remaining goals over / the engine applies the change
            idx += n
        return NEXT if trees else None

    def _sage_goals(self, run, idx, part, left):
        """Flow (key ``mixed``): the goals ``part`` (no tree) planned by SAGE now, from the world as it is, and run step by step through the engine's
        own path and ladder; the planner rung is held back, so a step with no way left hands ``left`` to SAGE once (``_to_sage``). A tree status."""
        s = self._state(run)
        steps = self.svc.plan_goals(run, part)
        self.svc.lab(FLAG, goal=idx, status="sage", steps=[f"{x.get('action')} {x.get('object')}" for x in steps], count=len(part))
        waits = getattr(self.svc, "changes_waiting", None)
        for st in steps:
            if callable(waits) and waits(run):
                return CHANGED
            s["active"], s["failed"] = True, None
            try:
                ok = yield from self.svc.try_step(run, st, ladder=True)
            finally:
                s["active"] = False
            if ok is None:
                return bt.STOPPED
            if not ok:
                return (yield from self._to_sage(run, s["failed"] or {}, left))
        return bt.SUCCESS

    def _exhausted(self, run, idx, goal, left):
        """Flow: the goal's tree has no way left: one more tick when the object memory now names another place (a person's answer to the
        ladder's question, a sighting) or the menu answer asks for it, else the policy's action (stop, SAGE once). A tree status, NEXT or STOP."""
        facts = self._state(run)["failed"] or {}
        rule = self.svc.robot.policy.rule_for(facts.get("code")) if facts else None
        policy = self._key("exhausted") if self._key("exhausted") in POLICIES else POLICIES[0]
        act = exhausted_action(rule, policy, asked=False)
        moved = self._moved(run, idx, goal)
        again, said = ({**goal, "from": moved}, "memory") if moved and act != "stop" else (None, None)
        if again is None and act == "ask":
            again, said = yield from self._ask_menu(run, goal, facts)
        if said == "cancelled":
            return STOP
        tree, keys = ((self._picks(run, [again]) or [None])[0] or (None, None)) if again is not None else (None, None)
        self.svc.lab(FLAG, goal=idx, status="exhausted", action=said or act, code=getattr(facts.get("code"), "value", None), rung=facts.get("rung"))
        if tree is not None:
            status = yield from self._tick(run, idx, again, tree, keys)
            if status != bt.FAILURE:
                return status
            facts = self._state(run)["failed"] or facts
        act = "stop" if said == "stop" or act == "stop" else exhausted_action(rule, policy, asked=True)
        self.svc.lab(FLAG, goal=idx, status="exhausted", action=act)
        if act == "sage":
            return (yield from self._to_sage(run, facts, left))
        return (yield from self._stop(run, goal, facts, said_stop=said == "stop"))

    def _moved(self, run, idx, goal):
        """The place the object memory names now for the goal's object when it is not the one its tree went to, else None."""
        obj, went = goal.get("object"), (self._state(run).get("from") or {}).get(idx)
        now = self.svc.remembered(run, obj) if obj and went else None
        return now if now and now != went else None

    def _ask_menu(self, run, goal, facts):
        """Flow: the menu question of the failed action: (the goal to tick again or None, what the answer said)."""
        menu, lang = self._key("menu"), getattr(run, "lang", None) or "en"
        step, obj = facts.get("step") or {}, goal.get("object")
        key = ((menu.get("phrases") or {}).get(step.get("action")) if pack_data.labelled(menu) else None)
        spec = getattr(self.svc.robot, "ask_spec", None)
        what = obj or step.get("object")
        q = speech.pack_phrase(self.svc.robot, key, lang, object=self.svc.said_of(run, what) if what else "") if key else None
        if q is None or not (callable(spec) and spec() is not None):
            return None, "no question"
        ans = yield from _Leaves(self.svc, run, goal, -1).put(q)
        if ans is None:
            return None, "cancelled"
        place = source_place(self.svc.ground_place(run, ans), goal)
        said = read_menu(ans, {k: pack_words(lang, menu.get(k)) for k in ("stop", "retry")}, place, lang)
        if said == "place" and obj:
            self.svc.note_answer(run, q, ans, obj, place)
        src = (place if said == "place" else None) or (self.svc.remembered(run, obj) if obj else None)
        return ({**goal, "from": src or goal.get("from")} if said in ("place", "retry") else None), said

    def _stop(self, run, goal, facts, said_stop=False):
        """Flow: report and stop: ``done failed`` with the failed code (the person said stop: ``aborted``, as option ask_next ends it); holding
        an object, the pack phrase says so."""
        step, code, held = facts.get("step") or {}, facts.get("code"), getattr(run.world, "holding", None)
        msg = f"{step.get('action', goal.get('type'))} {step.get('object', goal.get('object'))} failed ({FLAG}: no way left): {facts.get('reason') or ''}"
        lang = getattr(run, "lang", None) or "en"
        say = speech.pack_phrase(self.svc.robot, "say_bt_goal_stop_held", lang, object=self.svc.said_of(run, held)) if held else None
        say = say or (speech.LocalText("say_stopped") if said_stop else None)
        yield self.svc.finish(run, "aborted" if said_stop else "failed", msg=msg, **({"code": code} if code else {}), **({"say": say} if say else {}))
        return STOP

    def _to_sage(self, run, facts, left):
        """Flow: the remaining goals and the facts to the SAGE planner once (``replan_or_fail``): NEXT (its plan runs) or STOP."""
        step = facts.get("step") or {"action": "Check", "object": "goals"}
        code = getattr(facts.get("code"), "value", facts.get("code")) or "no way left"
        reason = f"{code}: {facts.get('reason') or ''}; goals left: {goal_kinds.goal_text(left)}"
        out = yield from self.svc.replan_or_fail(run, step, reason, f"{step.get('action')} {step.get('object')} failed and no new plan ({FLAG})")
        return STOP if out == STOP else NEXT


register(SPEC, BtGoal)
