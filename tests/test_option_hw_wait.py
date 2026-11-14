"""Option ``hw_wait`` (default OFF): the wait after a hardware fault is the pack's schedule and number of tries for that kind of failure.
Made-up packs (a camera that needs 1 s then 3 s, a link that needs nothing); the apple and the tables are the symbolic FakeRobot's own."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.recovery import DEFAULT_RECOVERY, Recovery, RecoveryRule
from robot_agent.contract.types import FailureCode
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, AssistLoop
from robot_agent.options.hw_wait import SPEC, HwWait, kinds_of, schedule_of, tries_of

DS, DT = "desk@study room", "dining table@kitchen"
CAMERA = {"perception_unavailable": {"schedule_s": [1, 3], "tries": 4, "action": "wait_health"}, "assumed": True}


class WaitRobot(FakeRobot):
    def _exec(self, skill, o, params):
        if skill == "wait":
            return {"isdone": True}
        return super()._exec(skill, o, params)


def make(kinds, run_failures=()):
    from types import SimpleNamespace as NS
    cfg = resolve(SPEC, {"hw_wait": "1"}, {}, None)
    svc = NS(robot=NS(manifest=NS(planner={"options": {"hw_wait": {"kinds": kinds}}})), lab=lambda *a, **k: svc.labs.append((a, k)), labs=[])
    return HwWait(cfg, svc), NS(failures=list(run_failures)), svc


def _fail(code):
    return {"code": code, "evidence": {}}


def _loop(make_assist, env, monkeypatch, kinds, fails=(1,), on=True, wait=(2, 4, 8), msg="find: no image (camera timeout 1 Hz)"):
    from robot_agent.core.planning.robot import RobotView
    monkeypatch.setattr(RobotView, "health", lambda self, node=None: {"ok": None})
    robot = WaitRobot(env, {"apple": DS}, faults=[{"skill": "find", "obj": "apple", "kind": "fail", "nth": list(fails), "msg": msg}])
    loop = make_assist(robot, FakeLLM({"status": "clear", "reason": "", "question": "",
                                       "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}),
                       ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")),
                       health_wait_s=0, wait_on_robot="1", **({"hw_wait": "1"} if on else {}))
    opts = {"wait": {"action": "Wait", "schedule_s": list(wait)}}
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, recovery=opts, **({"options": {"hw_wait": {"kinds": kinds}}} if kinds is not None else {}))
    orig = AssistLoop._bind
    loop._bind = lambda st, world: (("wait", {"inputs": st["object"]}) if st["action"] == "Wait" else orig(loop, st, world))
    return run_task(loop, "Bring the apple from the desk to the dining table."), robot, loop


def waits(robot):
    return [o for s, o, *_ in robot.trace if s == "wait"]


def test_registered_and_off_by_default_the_engine_schedule_stands_off(make_assist, env, monkeypatch):
    assert "hw_wait" in ASSIST_OPTIONS and REGISTRY.get("hw_wait").key("enabled").default is False
    evs, robot, loop = _loop(make_assist, env, monkeypatch, {"perception_unavailable": {"schedule_s": [9]}}, on=False)
    assert done(evs)["status"] == "success" and waits(robot) == ["2", "4", "8"]
    assert not [e for e in loop.lab_log if e["flag"] == "hw_wait"]


def test_a_pack_with_its_own_schedule_for_the_kind_is_used(make_assist, env, monkeypatch):
    evs, robot, loop = _loop(make_assist, env, monkeypatch, CAMERA)
    assert done(evs)["status"] == "success" and waits(robot) == ["1", "3"]
    assert [e for e in loop.lab_log if e["flag"] == "hw_wait" and e.get("schedule_s") == [1.0, 3.0]]


def test_an_empty_pack_keeps_the_engine_schedule_empty(make_assist, env, monkeypatch):
    for kinds in (None, {}, {"assumed": True}, {"not_a_code": {"schedule_s": [9]}}, {"perception_unavailable": {"schedule_s": "x"}}):
        evs, robot, _ = _loop(make_assist, env, monkeypatch, kinds)
        assert done(evs)["status"] == "success" and waits(robot) == ["2", "4", "8"], kinds


def test_a_failure_kind_without_data_uses_the_engine_schedule(make_assist, env, monkeypatch):
    evs, robot, _ = _loop(make_assist, env, monkeypatch, {"hw_fault": {"schedule_s": [9]}})      # data for another kind
    assert done(evs)["status"] == "success" and waits(robot) == ["2", "4", "8"]


def test_an_empty_schedule_sends_no_wait_command(make_assist, env, monkeypatch):
    evs, robot, _ = _loop(make_assist, env, monkeypatch, {"perception_unavailable": {"schedule_s": []}})
    assert done(evs)["status"] == "success" and waits(robot) == []


def test_the_declared_tries_decide_when_the_run_stops(make_assist, env, monkeypatch):
    three = {"perception_unavailable": {"schedule_s": [1], "tries": 1}}
    evs, robot, _ = _loop(make_assist, env, monkeypatch, three, fails=(1, 2, 3))
    assert done(evs)["status"] != "success"                                  # one try only: the second fault stops the run
    evs, robot, _ = _loop(make_assist, env, monkeypatch, {"perception_unavailable": {"schedule_s": [1], "tries": 4}}, fails=(1, 2, 3))
    assert done(evs)["status"] == "success" and waits(robot) == ["1", "1", "1"]
    evs, robot, _ = _loop(make_assist, env, monkeypatch, None, fails=(1, 2, 3))      # no data: the policy table's own two retries
    assert done(evs)["status"] != "success"


def test_the_declared_action_and_the_bad_entries_pure():
    opt, run, svc = make({"timeout": {"tries": 3, "action": "health_check"}, "hw_fault": {"tries": 0, "action": "dance"}, "assumed": True})
    rule = RecoveryRule(Recovery.WAIT_HEALTH, retries=1, then=Recovery.STOP)
    got = opt.recovery_rule(None, "timeout", rule)
    assert (got.retries, got.action, got.then) == (3, Recovery.HEALTH_CHECK, Recovery.STOP) and rule.retries == 1
    assert opt.recovery_rule(None, FailureCode.HW_FAULT, rule) is None        # tries 0 and an unknown action: nothing declared
    assert opt.recovery_rule(None, "absent", rule) is None
    assert opt.recovery_rule(None, "not a code", rule) is None
    assert DEFAULT_RECOVERY[FailureCode.HW_FAULT].retries == 1                # the table itself is untouched


def test_schedule_tries_and_kinds_readers_empty():
    assert schedule_of({"schedule_s": [1, 2.5]}) == [1.0, 2.5] and schedule_of({"schedule_s": []}) == []
    for bad in (None, {}, {"schedule_s": [0]}, {"schedule_s": [-1]}, {"schedule_s": ["a"]}, {"schedule_s": 3}):
        assert schedule_of(bad) is None
    assert tries_of({"tries": 2}) == 2 and all(tries_of(b) is None for b in (None, {"tries": 0}, {"tries": True}, {"tries": "2"}))
    assert kinds_of(None) == {} and kinds_of({"source": {"tries": 1}, "hw_fault": 3, "x": {}}) == {}
    assert list(kinds_of({"HW_FAULT": {"tries": 2}, "source": "label"})) == ["hw_fault"]


def test_no_failure_noted_yet_leaves_the_schedule_empty():
    opt, run, svc = make(CAMERA)
    assert opt.wait_schedule(run, [2.0]) is None and not svc.labs
    opt, run, svc = make(CAMERA, [_fail("perception_unavailable")])
    assert opt.wait_schedule(run, [2.0]) == [1.0, 3.0] and svc.labs
    opt, run, svc = make(CAMERA, [_fail("hw_fault")])
    assert opt.wait_schedule(run, [2.0]) is None
