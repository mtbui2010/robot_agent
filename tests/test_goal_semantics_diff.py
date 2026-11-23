"""Step S05 chose the goal-reach rules by kind: ``AssistLoop._reaches``, ``_missing``, ``_goals_hold``, ``_on_reached``,
``_simulate_goals`` and ``_releases`` now ask the rules object each goal kind names in the robot's pack
(robot_agent/core/pack/goal_semantics.py; the household bodies: robot_agent/packs/household/callables/goals.py). These
tests prove nothing changed for the standard pack: (1) its data equals the old constants (the kinds the old if-chains knew,
in the order of goals.GOAL_KINDS, the template kinds of goals.TEMPLATE_KINDS); (2) a differential grid: the new methods and
the reference copy of the old ones (tests/legacy_ref/goal_reach.py) give the same value, or raise the same exception,
over made-up plans, worlds, goals, loops and beliefs; the grid has the UNKNOWN-kind rows (still vacuously reached and
holding, no text); (3) the seams stay class attributes that the goal kinds go through (a patch of one still changes the
result); (4) opt-in: a pack's own rules object decides its kind, and a kind the pack removes becomes vacuous."""
import itertools

import pytest

from fakes import steps
from legacy_ref.goal_reach import Old
from robot_agent.core.pack import Registry, default_pack
from robot_agent.core.pack import goal_semantics as gs
from robot_agent.core.pack.loader import Pack
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState

DR, DT, CT, P = "drawer@kitchen", "dining table@kitchen", "counter@kitchen", "auntie"
ENV = {DT: {"surface": True}, CT: {"surface": True}, DR: {"openable": True}, P: {"person": "p1"}}


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


def G(t, o="", to="", **kw):
    return dict({"type": t, "object": o, "from": "", "to": to}, **kw)


class HandRobot:
    """A robot whose Handover releases the held object (no pack: the standard one is used)."""

    def release_actions(self):
        return ["Handover"]


class BrokenRobot:
    def release_actions(self):
        raise RuntimeError("no manifest")


def _loop(robot=None, places=False, handed=None, view=False):
    lp = AssistLoop.__new__(AssistLoop)
    if view:
        from robot_agent.core.planning.robot import RobotView
        lp.robot = RobotView.load("fakebot")
        lp._namemap = lp.robot.adapter.namemap
    elif robot is not None:
        lp.robot = robot
    if places:
        lp._env_places = Places(ENV)
    if handed is not None:
        lp._handed = set(handed)
    lp._reaches = lambda steps, world, goals, loop=None: AssistLoop._reaches(steps, world, goals, lp)   # as __init__ does
    return lp


LOOPS = {"bare": lambda: _loop(), "hand": lambda: _loop(HandRobot(), places=True, handed={"mug"}),
         "hand_no_handed": lambda: _loop(HandRobot(), places=True), "broken": lambda: _loop(BrokenRobot()),
         "fakebot": lambda: _loop(view=True, places=True)}
WORLDS = [W(), W(holding="mug", arrived=DT), W(opened=[DR], arrived=CT), W(arrived=P, holding="mug")]
PLANS = [[], steps(f"MoveTo {DT}", "Find mug", "Pick mug", f"MoveTo {CT}", f"Place {CT}"),
         steps(f"MoveTo {DR}", f"Open {DR}", "Pick mug", f"PutIn {DR}", f"Close {DR}"), steps(f"MoveTo {DR}", f"Open {DR}"),
         steps("Pick mug", f"MoveTo {P}", f"Handover {P}"), steps("Pick mug", f"MoveTo {P}", f"Place {P}"),
         steps(f"MoveTo {CT}"), steps("Say hello", f"MoveTo {CT}", "Say hello", f"WaitForUnload {CT}"),
         steps("Pick cell phone", f"Place {DT}"), steps("Pick mug", "Handover")]
SINGLE = [G("on", "mug", CT), G("on", "mug", DT), G("on", "phone", DT), G("on", "mug", P), G("on", "mug", P, hand=True),
          G("in", "mug", DR), G("open", DR), G("close", DR), G("go", to=CT), G("go", to=DT), G("say", "hello", CT),
          G("say", "hello"), G("deliver", "mug", CT), G("deliver", "", CT),
          G("polish", "apple", DT), G("give", "mug", P),                       # kinds no pack declares: vacuous
          {"type": "on", "object": "mug"}, {"type": "in", "object": "mug", "to": DR}]
GOAL_LISTS = [[]] + [[g] for g in SINGLE] + [
    [G("go", to=CT), G("go", to=DT)], [G("go", to=DT), G("go", to=CT)], [G("on", "mug", CT), G("go", to=CT)],
    [G("open", DR), G("close", DR)], [G("polish", "apple"), G("on", "mug", CT)], [G("polish", "apple"), G("polish", "pear")],
    [G("say", "hello", CT), G("deliver", "mug", CT)]]
MALFORMED = [[{"type": "on"}], [{"object": "mug"}], [G("open", DR), {"type": "close"}]]
PLACED = [{}, {"mug": CT}, {"mug": DR}, {"mug": P}]
ATS = [None, DT, CT]


def outcome(fn, *a):
    """The value of ``fn(*a)``, or the name of the exception it raises (old and new must raise the same)."""
    try:
        return ("value", fn(*a))
    except Exception as e:                     # noqa: BLE001
        return ("raises", type(e).__name__)


# ── (1) the data equals the old constants ──
def test_the_standard_pack_names_one_rules_object_for_each_kind_the_old_chains_knew_in_the_old_order():
    p = default_pack()
    assert list(p.table("goal_kinds")) == list(goal_kinds.GOAL_KINDS) == ["on", "in", "open", "close", "go", "say", "deliver"]
    assert all(gs.rules_of(p, k) is not None for k in goal_kinds.GOAL_KINDS), "every old kind has a registered rules object"
    template = type(gs.rules_of(p, "say"))
    assert {k for k in goal_kinds.GOAL_KINDS if isinstance(gs.rules_of(p, k), template)} == set(goal_kinds.TEMPLATE_KINDS)
    assert {k for k in goal_kinds.GOAL_KINDS if not isinstance(gs.rules_of(p, k), template)} == set(goal_kinds.MANIPULATION)
    assert all(gs.rules_of(p, k) is None for k in [*goal_kinds.OPTIONAL_KINDS, "polish", "", None, 3])
    assert all(e.get("source") for e in p.table("goal_kinds").values()), "every entry says where it comes from"


# ── (2) the differential grid ──
@pytest.mark.parametrize("name", list(LOOPS) + [None])
def test_reaches_equals_the_old_code(name):
    loop = LOOPS[name]() if name else None
    for world, plan, goals in itertools.product(WORLDS, PLANS, GOAL_LISTS + MALFORMED):
        assert outcome(AssistLoop._reaches, plan, world, goals, loop) == outcome(Old._reaches, plan, world, goals, loop), \
            (name, plan, goals)
        if loop is not None:
            assert outcome(loop._reaches, plan, world, goals) == outcome(Old._reaches, plan, world, goals, loop), (plan, goals)


@pytest.mark.parametrize("name", list(LOOPS))
def test_missing_equals_the_old_code(name):
    loop = LOOPS[name]()
    for world, plan, goals in itertools.product(WORLDS, PLANS, GOAL_LISTS + MALFORMED):
        assert outcome(loop._missing, plan, world, goals) == outcome(Old._missing, loop, plan, world, goals), (name, plan, goals)


@pytest.mark.parametrize("name", list(LOOPS) + [None])
def test_goals_hold_equals_the_old_code(name):
    loop = LOOPS[name]() if name else None
    for world, placed, done, at, goals in itertools.product(WORLDS, PLACED, PLANS[:2] + PLANS[6:8], ATS, GOAL_LISTS + MALFORMED):
        new = outcome(AssistLoop._goals_hold, loop, goals, world, placed, done, at)
        assert new == outcome(Old._goals_hold, loop, goals, world, placed, done, at), (name, world, placed, done, at, goals)
    assert outcome(AssistLoop._goals_hold, loop, [G("on", "mug", CT)], W(), {"mug": CT}) == ("value", True), "defaults of done / at"


@pytest.mark.parametrize("name", list(LOOPS) + [None])
def test_on_reached_simulate_and_releases_equal_the_old_code(name):
    loop = LOOPS[name]() if name else None
    gots = [None, (), ("Place", CT), ("Place", P), ("release", P), ("release", CT), ("PutIn", DR), ("Pick", CT)]
    for g, got in itertools.product(SINGLE[:5] + [G("polish", "apple", CT), G("in", "mug", CT)], gots):
        assert outcome(AssistLoop._on_reached, loop, g, got) == outcome(Old._on_reached, loop, g, got), (name, g, got)
    for world, plan in itertools.product(WORLDS, PLANS):
        assert outcome(AssistLoop._simulate_goals, loop, plan, world) == outcome(Old._simulate_goals, loop, plan, world)
    assert AssistLoop._releases(loop) == Old._releases(loop)


def test_an_unknown_kind_keeps_the_old_vacuous_result():
    loop, polish = LOOPS["hand"](), [G("polish", "apple", DT)]
    assert AssistLoop._reaches([], W(), polish) and loop._reaches([], W(), polish), "a goal of an unknown kind is reached"
    assert loop._missing([], W(), polish) == [] and loop._goals_hold(polish, W(), {})
    assert not AssistLoop._reaches([], W(), []), "no goal and no step: not reached (as before)"


# ── (3) the seams stay class attributes that every kind goes through ──
def test_a_patched_seam_still_changes_the_answer(monkeypatch):
    loop, on, plan = LOOPS["hand"](), [G("on", "mug", CT)], PLANS[1]
    assert loop._reaches(plan, W(), on) and loop._missing(plan, W(), on) == []
    monkeypatch.setattr(AssistLoop, "_on_reached", lambda self, g, got: False)
    assert not loop._reaches(plan, W(), on) and not AssistLoop._reaches(plan, W(), on)
    assert loop._missing(plan, W(), on) == ["Place the mug on counter@kitchen"]
    monkeypatch.undo()
    monkeypatch.setattr(AssistLoop, "_simulate_goals", lambda self, s, w: (set(), {}))
    assert not loop._reaches(plan, W(), on)
    monkeypatch.undo()
    monkeypatch.setattr(goal_kinds, "reached", lambda g, steps, at, last=False: True)
    assert loop._reaches([], W(), [G("go", to=CT)]) and loop._goals_hold([G("deliver", "mug", CT)], W(), {})


# ── (4) opt-in: the pack decides ──
class Mount:
    """A made-up kind's rules: reached when the plan has a step ``Seat <object>``."""

    def reached(self, goal, steps, world, ctx):
        return any(s.get("action") == "Seat" and s.get("object") == goal["object"] for s in steps)

    def holds(self, goal, belief, ctx):
        return goal["object"] in belief.placed

    def missing(self, goal, steps, world, ctx):
        return None if self.reached(goal, steps, world, ctx) else f"Seat the {goal['object']}"


class PackRobot:
    def __init__(self, pack):
        self.pack = pack

    def release_actions(self):
        return []


def _robot_pack(drop=()):
    reg, std = Registry(), default_pack()
    reg.register("goal.rules", "line.mount", Mount(), module="line_callables")
    for k in goal_kinds.GOAL_KINDS:
        reg.register("goal.rules", f"std.{k}", gs.rules_of(std, k), module="std_callables")
    kinds = {k: e for k, e in std.table("goal_kinds").items() if k not in drop}
    return Pack(schema=2, name="line", tables={"goal_kinds": dict(kinds, mount={"rules": "line.mount"})}, registry=reg)


def test_a_pack_s_own_rules_object_decides_its_kind_and_a_removed_kind_is_vacuous():
    loop = _loop(PackRobot(_robot_pack(drop=("in",))))
    mount, into = [G("mount", "pipe", "wall")], [G("in", "mug", DR)]
    assert not loop._reaches([], W(), mount) and loop._missing([], W(), mount) == ["Seat the pipe"]
    assert loop._reaches(steps("Seat pipe"), W(), mount) and loop._goals_hold(mount, W(), {"pipe": "wall"})
    assert loop._reaches([], W(), into) and loop._missing([], W(), into) == [] and loop._goals_hold(into, W(), {}), \
        "a kind the pack removed is vacuous, as an unknown kind always was"
    assert loop._missing([], W(), [G("on", "mug", CT)]) == ["Place the mug on counter@kitchen"], "the kept kinds keep their rules"
    assert _loop(PackRobot(_robot_pack()))._missing([], W(), into) == ["PutIn the mug into drawer@kitchen"]
