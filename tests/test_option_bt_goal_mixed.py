"""Option ``bt_goal`` key ``mixed`` (default off): goals WITH a tree run as trees, goals WITHOUT one are planned by SAGE, in the reader's order.
Made-up flat (a den and a loft), made-up objects (``gizmo``, ``widget``). The trees declared are those of ``go`` only, so a ``go`` goal is a
tree goal ("bt") and an ``on`` goal is a SAGE goal ("no bt"). Four orders of three goals, two tests each, all through the real AssistLoop:
bt - no bt - bt, no bt - bt - no bt, bt - bt - no bt, no bt - no bt - bt. A planner double gives one plan per planner call, so the number of
planner calls and the world each call saw are checked."""
import json

import pytest

from fakes import FakeLLM, FakeRobot, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.options import bt_goal as B

SHELF, BENCH, CRATE = "shelf@loft", "bench@den", "crate@den"
ENV = {SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 6.0, "y": 1.0}, "surface": True},
       CRATE: {"aliases": ["crate"], "loc": {"x": 6.5, "y": 2.0}, "surface": True}}
GO = {"tree": ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]], "assumed": True}
TREES = {"go": GO}
MENU = {"phrases": {"MoveTo": "q_ask_next_move"}, "retry": ["ask_next.retry", "dialogue.yes"], "stop": ["ask_next.stop", "dialogue.stop"], "assumed": True}
SAY = {SHELF: "shelf", BENCH: "bench", CRATE: "crate"}


def task_of(goals):
    """The request in words, one sentence per goal, in the goals' order."""
    return " Then ".join(f"Go to the {SAY[g['to']]}." if g["type"] == "go" else
                         f"Take the {g['object']} from the {SAY[g['from']]} and put it on the {SAY[g['to']]}." for g in goals)


@pytest.fixture
def env():
    return dict(ENV)


def go(place):
    return {"type": "go", "object": "", "to": place}


def move_on(obj, src, dst):
    return {"type": "on", "object": obj, "from": src, "to": dst}


GIZMO_TO_BENCH = move_on("gizmo", SHELF, BENCH)
WIDGET_TO_SHELF = move_on("widget", CRATE, SHELF)
CARRY_GIZMO = [f"MoveTo {SHELF}", "Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}"]
CARRY_WIDGET = [f"MoveTo {CRATE}", "Find widget", "Pick widget", f"MoveTo {SHELF}", f"Place {SHELF}"]


class PlanPerCall:
    """Planner double: the next scripted plan for each ``generate_plan`` call (the obs it was given is kept), ``replans`` for each replan."""

    def __init__(self, plans, replans=()):
        self.plans, self.replans, self.calls, self.obs = [steps(*p) for p in plans], [steps(*r) for r in replans], [], []

    def generate_plan(self, task, obs, visible_objects):
        self.calls.append("generate_plan")
        self.obs.append(obs)
        return [dict(s) for s in (self.plans.pop(0) if self.plans else [])], {"method": "scripted"}

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        self.calls.append("replan")
        return [dict(s) for s in (self.replans.pop(0) if self.replans else [])], {"method": "scripted"}


def _run(make_assist, goals, plans, mixed=True, trees=TREES, faults=(), replans=(), objects=None, start=CRATE):
    und = {"status": "clear", "reason": "", "question": "", "goals": [dict(g) for g in goals]}
    robot = FakeRobot(ENV, objects or {"gizmo": SHELF, "widget": CRATE}, faults=list(faults), start=start)
    planner = PlanPerCall(plans, replans)
    opts = {"bt_goal": "1", **({"bt_goal.mixed": "1"} if mixed else {})}
    loop = make_assist(robot, FakeLLM(default=und), planner, detector_miss_rate=0, search_legs=1, health_wait_s=0, **opts)
    loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"bt_goal": {"trees": trees, "menu": MENU}}}
    return loop, robot, planner, run_task(loop, task_of(goals), max_events=3000)


def _skills(robot):
    return [t[:2] for t in robot.trace if t[0] in ("move", "find", "pick", "place")]


def _flags(loop):
    return [e for e in loop.lab_log if e["flag"] == B.FLAG and "node" not in e]


def _where(robot, obj):
    return robot.obj[obj]


# ── the data: a tree for some goals only ───────────────────────────────────────────────────────────────────────────────────────
def test_registered_mixed_is_off_by_default():
    assert REGISTRY.get("bt_goal").key("mixed").default is False


def test_plan_trees_mixed_keeps_the_gaps_and_is_none_only_when_no_goal_has_a_tree_empty():
    args = (TREES, {"MoveTo", "Find", "Pick", "Place"}, False, lambda k: True)
    goals = [go(SHELF), GIZMO_TO_BENCH, go(BENCH)]
    got = B.plan_trees(goals, *args, mixed=True)
    assert [t is not None for t in got] == [True, False, True]
    assert B.plan_trees(goals, *args) is None                                       # all or nothing, as when mixed is off
    assert B.plan_trees([GIZMO_TO_BENCH, WIDGET_TO_SHELF], *args, mixed=True) is None
    assert B.plan_trees([], *args, mixed=True) is None
    assert B.plan_trees(goals, {}, *args[1:], mixed=True) is None                   # no trees declared


# ── order 1: bt - no bt - bt ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_order_bt_nobt_bt_the_middle_goal_is_one_sage_call_planned_where_the_first_tree_left_the_robot(make_assist):
    loop, robot, planner, evs = _run(make_assist, [go(SHELF), GIZMO_TO_BENCH, go(CRATE)], [[f"Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}"]])
    assert done(evs)["status"] == "success" and _where(robot, "gizmo") == BENCH
    assert planner.calls == ["generate_plan"] and f"{SHELF}" in planner.obs[0].split("Robot state:")[1]      # SAGE saw the robot at the shelf
    assert _skills(robot)[:6] == [("move", SHELF), ("find", "gizmo"), ("pick", "gizmo"), ("move", BENCH), ("place", BENCH), ("move", CRATE)]
    assert [f["status"] for f in _flags(loop) if f.get("status") in ("claimed", "sage", B.bt.SUCCESS)] == ["claimed", B.bt.SUCCESS, "sage", B.bt.SUCCESS, B.bt.SUCCESS]


def test_order_bt_nobt_bt_a_blocked_move_inside_the_sage_part_is_recovered_by_the_engine_ladder_without_a_replan(make_assist):
    loop, robot, planner, evs = _run(make_assist, [go(SHELF), GIZMO_TO_BENCH, go(CRATE)], [[f"Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}"]],
                                     faults=[{"skill": "move", "obj": BENCH, "kind": "fail", "msg": "path blocked"}])
    assert done(evs)["status"] == "success" and _where(robot, "gizmo") == BENCH and planner.calls == ["generate_plan"]


# ── order 2: no bt - bt - no bt ───────────────────────────────────────────────────────────────────────────────────────────────
def test_order_nobt_bt_nobt_two_separate_sage_calls_the_second_sees_where_the_tree_left_the_robot(make_assist):
    loop, robot, planner, evs = _run(make_assist, [GIZMO_TO_BENCH, go(CRATE), WIDGET_TO_SHELF], [CARRY_GIZMO[1:], CARRY_WIDGET[1:]], start=SHELF)
    assert done(evs)["status"] == "success" and (_where(robot, "gizmo"), _where(robot, "widget")) == (BENCH, SHELF)
    assert planner.calls == ["generate_plan", "generate_plan"]
    assert f"arrived={SHELF}" in planner.obs[0] and f"arrived={CRATE}" in planner.obs[1]       # the tree moved the robot between the two calls
    assert _skills(robot)[:9] == [("find", "gizmo"), ("pick", "gizmo"), ("move", BENCH), ("place", BENCH), ("move", CRATE),
                                  ("find", "widget"), ("pick", "widget"), ("move", SHELF), ("place", SHELF)]


def test_order_nobt_bt_nobt_mixed_off_is_sage_for_the_whole_run_and_no_tree_runs(make_assist):
    loop, robot, planner, evs = _run(make_assist, [GIZMO_TO_BENCH, go(CRATE), WIDGET_TO_SHELF], [CARRY_GIZMO + CARRY_WIDGET], mixed=False)
    assert planner.calls == ["generate_plan"] and not _flags(loop) and done(evs)["status"] == "success"


# ── order 3: bt - bt - no bt ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_order_bt_bt_nobt_both_trees_run_first_then_one_sage_call_from_where_they_left_the_robot(make_assist):
    loop, robot, planner, evs = _run(make_assist, [go(SHELF), go(BENCH), move_on("gizmo", SHELF, CRATE)],
                                     [[f"MoveTo {SHELF}", "Find gizmo", "Pick gizmo", f"MoveTo {CRATE}", f"Place {CRATE}"]])
    assert done(evs)["status"] == "success" and _where(robot, "gizmo") == CRATE and planner.calls == ["generate_plan"]
    assert BENCH in planner.obs[0].split("Robot state:")[1]
    assert _skills(robot)[:2] == [("move", SHELF), ("move", BENCH)]


def test_order_bt_bt_nobt_a_step_of_the_sage_part_with_no_way_left_hands_the_goals_left_to_one_replan_and_the_run_ends_without_it(make_assist):
    loop, robot, planner, evs = _run(make_assist, [go(SHELF), go(BENCH), move_on("gizmo", SHELF, CRATE)],
                                     [[f"MoveTo {SHELF}", "Find gizmo", "Pick gizmo", f"MoveTo {CRATE}", f"Place {CRATE}"]],
                                     faults=[{"skill": "pick", "obj": "gizmo", "nth": "all", "kind": "fail", "msg": "grasp failed"}])
    assert done(evs)["status"] != "success" and planner.calls[0] == "generate_plan" and planner.calls.count("generate_plan") == 1
    assert "replan" in planner.calls                                                    # the goals left went to SAGE's own repair (_to_sage)
    assert _skills(robot)[:2] == [("move", SHELF), ("move", BENCH)] and _where(robot, "gizmo") == SHELF


# ── order 4: no bt - no bt - bt ───────────────────────────────────────────────────────────────────────────────────────────────
def test_order_nobt_nobt_bt_the_two_neighbours_are_ONE_sage_call_and_the_tree_runs_after_their_steps(make_assist):
    loop, robot, planner, evs = _run(make_assist, [GIZMO_TO_BENCH, WIDGET_TO_SHELF, go(CRATE)], [CARRY_GIZMO + CARRY_WIDGET])
    assert done(evs)["status"] == "success" and (_where(robot, "gizmo"), _where(robot, "widget")) == (BENCH, SHELF)
    assert planner.calls == ["generate_plan"]                                           # two goals, one planner call
    assert _skills(robot)[-1] == ("move", CRATE) and robot.trace[-1][1] == CRATE


def test_order_nobt_nobt_bt_with_no_tree_declared_the_run_is_byte_identical_to_mixed_off_empty(make_assist):
    def run(mixed):
        loop, robot, planner, evs = _run(make_assist, [GIZMO_TO_BENCH, WIDGET_TO_SHELF, go(CRATE)], [CARRY_GIZMO + CARRY_WIDGET + [f"MoveTo {CRATE}"]],
                                         mixed=mixed, trees={})
        return json.dumps([{k: v for k, v in e.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at", "plan_sec")} for e in evs
                           if e["event"] != "reconcile"], sort_keys=True, default=str), robot.trace, planner.calls
    assert run(True) == run(False)
