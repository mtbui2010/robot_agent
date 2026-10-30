"""T2 fix: plan_tidy removes the steps the action model proves do nothing, before the gate (T2 S02, M05,
M07, H02, M04: the planner was asked to delete them, did not, and the run ended without a plan)."""

from fakes import steps

from robot_agent.core.planning import action_model
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.plan_tidy import tidy

DR, DT, DS, CT, SF = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen", "sofa@living room"
CTX = dict(capabilities=action_model.Capabilities(hands=1, find_reaches=False), actions=None,
           openable=lambda k: k == DR, containers=lambda k: k == DR)


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def run(plan, goals, world=None):
    st, notes = tidy(steps(*plan), world or WorldState(), goals, AssistLoop._reaches, CTX)
    return [f"{s['action']} {s['object']}" for s in st], notes


def test_find_and_pick_of_what_is_in_the_hand():
    got, notes = run([f"MoveTo {DT}", "Find plate", "Pick plate", "Find plate", "Pick plate", f"MoveTo {CT}",
                      f"Place {CT}"], [G("on", "plate", DT, CT)])
    assert got == [f"MoveTo {DT}", "Find plate", "Pick plate", f"MoveTo {CT}", f"Place {CT}"] and len(notes) == 2


def test_steps_after_the_goals_are_reached():
    got, notes = run([f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}",
                      f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"],
                     [G("on", "apple", "", DT)])
    assert got == [f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"]
    assert "5 steps after them removed" in notes[-1]


def test_a_room_waypoint_before_a_move():
    st = steps(f"MoveTo @kitchen", f"MoveTo {DR}", f"Open {DR}", "Find spoon", "Pick spoon", f"MoveTo {SF}",
               f"Place {SF}")
    out, _n = tidy(st, WorldState(), [G("on", "spoon", DR, SF)], AssistLoop._reaches, CTX)
    assert [s["object"] for s in out][:2] == [DR, DR] and len(out) == 6


def test_open_of_an_open_drawer_and_close_of_a_closed_one():
    w = WorldState()
    w.opened.add(DR)
    got, _n = run([f"MoveTo {DR}", f"Open {DR}", "Find spoon", "Pick spoon", f"MoveTo {SF}", f"Place {SF}"],
                  [G("on", "spoon", DR, SF)], world=w)
    assert f"Open {DR}" not in got
    got, _n = run([f"MoveTo {DR}", f"Close {DR}", "Find apple"], [])
    assert got == [f"MoveTo {DR}", "Find apple"]


def test_real_errors_are_kept_for_the_gate():
    plan = [f"MoveTo {DT}", "Pick plate", f"MoveTo {CT}", f"Place {CT}"]           # no Find: a real error
    got, notes = run(plan, [G("on", "plate", DT, CT)])
    assert got == plan and notes == []
    plan = [f"MoveTo {DT}", "Find plate", "Pick plate", "Find bowl", "Pick bowl"]   # holding the plate: an error
    assert run(plan, [])[0] == plan


def test_plan_tidy_off(make_assist, env):
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task
    apple = {"status": "clear", "reason": "", "question": "",
             "goals": [G("on", "apple", "", "dining table")]}
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", "Find apple", f"MoveTo {DT}", f"Place {DT}")
    evs = run_task(make_assist(FakeRobot(env, {"apple": DS}), FakeLLM(apple, default=apple), ScriptedPlanner(plan),
                               plan_tidy=False), "Put the apple on the dining table.")
    g0 = next(e for e in evs if e["event"] == "gate")
    assert "tidied" not in g0 and any("already done" in p for p in g0["problems"])
