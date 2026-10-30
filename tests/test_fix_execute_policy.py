"""Fix D5 (the recovery policy applied by FailureCode: WAIT_HEALTH, HEALTH_CHECK, stop once the retries are
used), D13 (classifier gaps), D25 (a failed run ends in RunState failed) and D26 (one run id per run)."""

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import FailureCode, classify_legacy_failure

DT, DS = "dining table@kitchen", "desk@study room"
HOUSE = {"apple": DS}
APPLE = {"status": "clear", "reason": "", "question": "",
         "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
BASIC = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
TASK = "Bring the apple from the desk to the dining table."
NO_IMAGE = "find: no image (camera timeout 1 Hz)"


def _find_fails(nth):
    return [{"skill": "find", "kind": "fail", "obj": "apple", "nth": nth, "msg": NO_IMAGE}]


def test_camera_down_waits_and_looks_again_in_place(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=_find_fails([1, 2]))
    planner = ScriptedPlanner(BASIC)
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner, health_wait_s=0), TASK)
    assert done(evs)["status"] == "success"
    assert [c for c in planner.calls if c[0] == "replan"] == []         # no replan, nothing "searched"
    assert [e["kind"] for e in evs if e["event"] == "recover"] == ["wait_health", "wait_health"]
    assert sum(1 for e in evs if e["event"] == "health") == 2


def test_camera_down_stops_after_the_policy_retries(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=_find_fails("all"))
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC), health_wait_s=0), TASK)
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "perception_unavailable" and d["device"] == "find"
    assert [s for s, *_ in robot.trace].count("find") == 3                # 1 + rule.retries (2), then STOP
    assert "health" in d["evidence"]


def test_unhealthy_robot_stops_on_a_hardware_fault(make_assist, env, monkeypatch):
    from robot_agent.core.planning.robot import RobotView
    monkeypatch.setattr(RobotView, "health", lambda self, node=None: {"ok": False, "error": "arm servo 3 overheated"})
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "pick", "kind": "fail", "nth": "all", "msg": "servo fault"}])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC)), TASK)
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "hw_fault" and "overheated" in d["msg"]
    assert [s for s, *_ in robot.trace].count("pick") == 1


def test_timeout_checks_health_then_recovers_as_before(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "pick", "kind": "raise", "nth": 1,
                                           "msg": "action server /pick timeout after 60s"}])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC)), TASK)
    assert done(evs)["status"] == "success"
    assert any(e["event"] == "health" for e in evs)


@pytest.mark.parametrize("msg, code", [
    ("apple is no longer in the gripper", FailureCode.DROPPED),
    ("open_drawer failed: drawer stuck", FailureCode.BLOCKED),
    ("the drawer is jammed", FailureCode.BLOCKED),
    ("place failed and the object is no longer in the hand: x", FailureCode.DROPPED),
    ("nothing in the gripper", FailureCode.GRASP_FAILED),
])
def test_classifier_gaps(msg, code):
    assert classify_legacy_failure(msg) is code


@pytest.mark.parametrize("done_ev, ok", [
    ({"event": "done", "status": "success"}, True),
    ({"event": "done", "status": "aborted"}, True),
    ({"event": "done", "status": "planned"}, True),
    ({"event": "done", "status": "failed"}, False),
    ({"event": "done", "status": "refused"}, False),
    ({"event": "done", "success": False}, False),                 # the open-loop path
    ({"event": "done", "success": True}, True),
    ({"event": "done"}, True),
])
def test_done_ok_reads_the_status(done_ev, ok):
    pytest.importorskip("fastapi")
    from robot_agent.api.agent import _done_ok
    assert _done_ok(done_ev) is ok


def test_loop_takes_the_run_managers_run_id(make_assist, env):
    from robot_agent.core import run_control
    from robot_agent.interact.run_manager import reset_manager
    mgr = reset_manager()
    try:
        run = mgr.start(goal=TASK, user_id="u").run
        run_control.begin_run()
        evs = run_task(make_assist(FakeRobot(env, HOUSE), FakeLLM(APPLE), ScriptedPlanner(BASIC)), TASK)
        assert {e.get("run_id") for e in evs if "run_id" in e} == {run.run_id}
        mgr.finish(run.run_id)
        evs = run_task(make_assist(FakeRobot(env, HOUSE), FakeLLM(APPLE), ScriptedPlanner(BASIC)), TASK)
        assert done(evs)["run_id"] != run.run_id                   # no run: a time-based id as before
    finally:
        reset_manager()


def test_closed_loop_failed_done_says_code_and_why(agent_state, env, monkeypatch, tmp_path):
    import types
    import robot_agent.core.planning.loop as LP
    import robot_agent.skill_configs as sc
    from fakes import FakeSkillRegistry
    from robot_agent.core.run_control import begin_run
    monkeypatch.setattr(sc, "ENV", env)
    begin_run()
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "move", "kind": "fail", "nth": 1,
                                           "msg": "base stopped: emergency stop pressed"}])
    agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(robot),
                                  device_manager=types.SimpleNamespace(_ros_node=None),
                                  _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
    loop = LP.ClosedLoop(agent, log_dir=str(tmp_path / "runs"), live_path=str(tmp_path / "l.jsonl"),
                         mute_skill_tts=False, speak_backend=False, vlm_enabled=False)
    loop._make_planner = lambda: ScriptedPlanner(steps(f"MoveTo {DS}"))
    d = done(run_task(loop, "go to the desk"))
    assert d["status"] == "failed" and d["code"] == "estop" and d["device"] == "move"
    assert "emergency stop" in d["msg"] and d["failed_step"] == {"action": "MoveTo", "object": DS}
