"""SAGE-v2 per-step guard (AssistLoop._guard, reconcile.guard_arrived; docs/guard.md): a non-sensor `arrived`
that a fresh pose contradicts is stale -> fixed + `recover kind=reconcile`; run-start / resume reconcile with
provenance; the read-back correction replaces the task's place for the goal read-back."""

from robot_agent.core.planning import reconcile as R
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.records import VerifyResult
from robot_agent.core.planning.understand import find_issues, without_places

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from test_v2_reconcile_sensors import loc, view

DS, DT, CT = "desk@study room", "dining table@kitchen", "counter@kitchen"
TASK = {"status": "clear", "reason": "", "question": "",
        "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


# ── reconcile.guard_arrived ──────────────────────────────────────────────
def test_guard_fixes_an_operator_arrived_that_the_pose_contradicts(env):
    x, y = loc(env, DS)
    w = WorldState(arrived=CT)
    w.mark_edited(["arrived"])
    fix = R.guard_arrived(w, view({"x": x, "y": y}), None, None, env, 0.3, {"action": "Find", "object": "apple"})
    assert fix["old"] == CT and fix["new"] == DS and fix["source"] == "operator"
    assert w.arrived == DS and w.provenance_of("arrived")["source"] == "sensor"


def test_guard_leaves_sensor_arrived_and_other_steps_and_no_pose(env):
    x, y = loc(env, DS)
    rv = view({"x": x, "y": y})
    w = WorldState(arrived=CT)
    assert R.guard_arrived(w, rv, None, None, env, 0.3, {"action": "MoveTo", "object": DS}) is None
    w.mark("arrived", "sensor")
    assert R.guard_arrived(w, rv, None, None, env, 0.3, {"action": "Place", "object": CT}) is None
    w2 = WorldState(arrived=CT)
    assert R.guard_arrived(w2, view(None), None, None, env, 0.3, {"action": "Place", "object": CT}) is None
    assert w2.arrived == CT                           # no fresh pose: nothing invented


def test_guard_agreeing_pose_confirms_quietly(env):
    x, y = loc(env, CT)
    w = WorldState(arrived=CT)
    assert R.guard_arrived(w, view({"x": x, "y": y}), None, None, env, 0.3, {"action": "Place", "object": CT}) is None
    assert w.provenance_of("arrived")["source"] == "sensor"


def test_confirm_arrival():
    w = WorldState(arrived=DS)
    R.confirm_arrival(w, [VerifyResult("isdone", True), VerifyResult("localization", True, unknown=True)])
    assert w.provenance_of("arrived")["source"] == "belief"
    R.confirm_arrival(w, [VerifyResult("localization", True, "0.01 m from desk")])
    assert w.provenance_of("arrived")["source"] == "sensor"


# ── through AssistLoop ───────────────────────────────────────────────────
def test_operator_edit_mid_run_is_reconciled_by_the_guard(make_assist, env, agent_state):
    """After the MoveTo to the desk is done the operator 'corrects' arrived to the counter (PUT /agent/world)."""
    robot = FakeRobot(env, {"apple": DS})
    loop = make_assist(robot, FakeLLM(default=TASK), ScriptedPlanner(PLAN))
    evs = []
    for ev in loop.run_blocking("Bring the apple from the desk to the dining table."):
        evs.append(ev)
        if ev["event"] == "step_done" and ev.get("index") == 0:
            agent_state.world.update_from_dict({"arrived": CT})
            agent_state.world.mark_edited(["arrived"])
    rec = [e for e in evs if e["event"] == "recover" and e.get("kind") == "reconcile"]
    assert rec and rec[0]["was"] == CT and rec[0]["now"] == DS and "operator" in rec[0]["msg"]
    assert done(evs)["status"] == "success"
    assert [s for s, *_ in robot.trace].count("move") == 2          # no replan back to the desk


def test_run_start_reconcile_event_has_provenance(make_assist, env, agent_state):
    agent_state.world.update_from_dict({"arrived": DT})
    agent_state.world.mark_edited(["arrived"])
    robot = FakeRobot(env, {"apple": DS})                            # at the entrance
    loop = make_assist(robot, FakeLLM(default=TASK), ScriptedPlanner(PLAN))
    evs = run_task(loop, "Bring the apple from the desk to the dining table.")
    rec = next(e for e in evs if e["event"] == "reconcile")
    assert rec["when"] == "start" and rec["changed"]["arrived"] == [DT, "entrance"]
    assert rec["provenance"]["arrived"]["source"] == "sensor" and rec["sensors"]["arrived"]["pose"]
    assert done(evs)["status"] == "success"


def test_reconcile_off_keeps_the_adapter_only(make_assist, env, agent_state):
    agent_state.world.update_from_dict({"arrived": DT})
    robot = FakeRobot(env, {"apple": DS})
    loop = make_assist(robot, FakeLLM(default=TASK), ScriptedPlanner(PLAN), reconcile=False)
    evs = run_task(loop, "Bring the apple from the desk to the dining table.", plan_only=True)
    assert not [e for e in evs if e["event"] == "reconcile"] and agent_state.world.arrived == DT


def test_unknown_position_is_told_to_the_planner(make_assist, env, agent_state):
    robot = FakeRobot(env, {"apple": DS}, faults=[{"skill": "mobile_pose", "kind": "no_pose", "nth": "all"}])
    seen = []

    class Recording(ScriptedPlanner):
        def generate_plan(self, task, obs, visible_objects):
            seen.append(obs)
            return super().generate_plan(task, obs, visible_objects)
    loop = make_assist(robot, FakeLLM(default=TASK), Recording(PLAN))
    run_task(loop, "Bring the apple from the desk to the dining table.", plan_only=True)
    assert "Robot position: unknown" in seen[0]


# ── the read-back correction replaces the task's place ───────────────────
def test_without_places_cuts_the_replaced_mention(places):
    t = "Put the apple on the dining table. on the kitchen counter"
    assert "dining table" not in without_places(t, places, {DT})
    assert without_places(t, places, ()) == t
    goals = [{"type": "on", "object": "apple", "from": "", "to": CT}]
    assert any(i["kind"] == "place" for i in find_issues(goals, t, places, english=t))
    assert not [i for i in find_issues(goals, without_places(t, places, {DT}), places, english=t)
                if i["kind"] == "place"]
