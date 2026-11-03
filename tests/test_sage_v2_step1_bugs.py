"""SAGE-v2 round 3, step 1 (correctness bugs, docs/env.md "SAGE-v2 changes"):

* F0.1 a ``go`` goal that is the last goal is judged where the plan ENDS: a robot that starts at its dock has
  not reached "go back and charge" once the plan leaves the dock (false successes, the gate truncated the final
  MoveTo as "after the goals are reached");
* F0.3 a number in a step field is text (a ``Wait 5`` step crashed every ``.strip()``);
* F0.4 closing a container the plan itself opened is not a critical finding; a Close that breaks an ``open``
  goal still is.
Synthetic places only (the fakebot sim house ENV), no benchmark text.
"""
import pytest

from fakes import steps
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import plan_tidy
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState

DR, DT, DS, ENT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "entrance"


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


@pytest.fixture
def robot():
    from robot_agent.core.planning.robot import RobotView
    return RobotView.load("fakebot")


@pytest.fixture
def places(env, robot):
    return Places(env, openable=robot.openable)


@pytest.fixture
def loop(robot):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = robot
    lp._namemap = robot.adapter.namemap
    return lp


# ── F0.1 ──
def test_go_goal_final_position(loop):
    goals = [G("on", "apple", DS, DT), G("go", "", "", ENT)]
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    start = W(arrived=ENT)                               # the robot starts where the go goal sends it
    assert not loop._reaches(plan, start, goals)
    assert loop._missing(plan, start, goals) == ["go to the entrance"]
    full = plan + steps(f"MoveTo {ENT}")
    assert loop._reaches(full, start, goals) and loop._missing(full, start, goals) == []
    # never left: still there at the end
    assert goal_kinds.reached(G("go", "", "", ENT), [], ENT, last=True)
    assert not goal_kinds.reached(G("go", "", "", ENT), steps(f"MoveTo {DT}"), ENT, last=True)
    # an earlier go goal (not the last one) is reached by passing there, as before
    assert goal_kinds.reached(G("go", "", "", DT), steps(f"MoveTo {DT}", f"MoveTo {ENT}"), ENT)


def test_goals_hold_last_go_uses_the_current_position(loop):
    goals = [G("go", "", "", ENT)]
    assert not loop._goals_hold(goals, W(arrived=DT), {}, done=steps(f"MoveTo {DT}"), at=ENT)
    assert loop._goals_hold(goals, W(arrived=ENT), {}, done=steps(f"MoveTo {DT}", f"MoveTo {ENT}"), at=ENT)


def test_plan_tidy_keeps_the_final_moveto(loop, robot, places):
    goals = [G("on", "apple", DS, DT), G("go", "", "", ENT)]
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}", f"MoveTo {ENT}")
    ctx = dict(capabilities=robot.model_capabilities(), actions=robot.plan_actions, openable=places.openable,
               containers=places.openable)
    out, notes = plan_tidy.tidy(plan, W(arrived=ENT), goals, loop._reaches, ctx)
    assert out == plan and notes == []


# ── F0.3 ──
def test_step_number_fields_are_text(loop, places):
    st = loop._ground_step({"action": "Wait", "object": 5}, places, [], "wait a bit")
    assert st["object"] == "5" and st["action"] == "Wait"
    assert loop._check([st], W(), places, []) is not None          # no crash


# ── F0.4 ──
def test_close_after_putin_ok(loop, places):
    goals = [G("in", "spoon", DS, DR)]
    plan = steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find spoon", "Pick spoon", f"MoveTo {DR}",
                 f"PutIn {DR}", f"Close {DR}")
    probs = loop._check(plan, W(), places, goals)
    close = [p for p in probs if p[1].startswith("step 8 'Close")]
    assert close and close[0][2] is False                       # mentioned, not critical
    assert not [p for p in probs if p[2]]


def test_close_breaks_open_goal_still_critical(loop, places):
    goals = [G("open", DR, "", DR)]
    plan = steps(f"MoveTo {DR}", f"Open {DR}", f"Close {DR}")
    probs = loop._check(plan, W(), places, goals)
    assert any(p[2] for p in probs if p[1].startswith("step 3 'Close"))


def test_close_not_opened_by_plan_still_critical(loop, places):
    """A Close of something the plan did not open and no goal asks for stays critical (as before)."""
    probs = loop._check(steps(f"MoveTo {DR}", f"Close {DR}"), W(opened=[DR]), places, [G("on", "apple", DS, DT)])
    assert any(p[2] for p in probs if p[1].startswith("step 2 'Close"))


def test_plan_tidy_keeps_closing_what_it_opened(loop, robot, places):
    goals = [G("in", "spoon", DS, DR)]
    plan = steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find spoon", "Pick spoon", f"MoveTo {DR}",
                 f"PutIn {DR}", f"Close {DR}")
    ctx = dict(capabilities=robot.model_capabilities(), actions=robot.plan_actions, openable=places.openable,
               containers=places.openable)
    assert plan_tidy.tidy(plan, W(), goals, loop._reaches, ctx)[0] == plan
    # a repeat of the task after the goals is still cut
    again = plan + steps(f"Open {DR}")
    assert plan_tidy.tidy(again, W(), goals, loop._reaches, ctx)[0] == plan[:7]
