"""v2 verify-budget, P1.12: sensor health before a Find / after a MoveTo (core/planning/sensor_health.py)."""

from robot_agent.contract import FailureCode, Geometry, classify_legacy_failure
from robot_agent.core.planning.records import VerifyResult
from robot_agent.core.planning.sensor_health import (HealthLimits, arrival_check, camera_problem, find_precheck,
                                                     pose_problem)

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DS, DT = "desk@study room", "dining table@kitchen"
LIM = HealthLimits(frame_max_age_s=1.0, min_fps=5.0, pose_max_age_s=1.0, need_depth=True)


class StubRobot:
    def __init__(self, health, limits=LIM):
        self.h, self.lim, self.calls = health, limits, 0

    def health_limits(self):
        return self.lim

    def health(self, node=None):
        self.calls += 1
        return dict(self.h)


def test_limits_from_the_manifest_geometry():
    g = Geometry.from_dict({"arrive_tol_m": 0.3, "frame_max_age_s": 2, "pose_max_age_s": "1.5", "min_fps": None})
    lim = HealthLimits.from_geometry(g)
    assert (lim.frame_max_age_s, lim.pose_max_age_s, lim.min_fps, lim.need_depth) == (2.0, 1.5, None, False)
    assert lim.camera and lim.pose
    none = HealthLimits.from_geometry(Geometry.from_dict({}))
    assert not none.camera and not none.pose


def test_camera_and_pose_problems():
    assert camera_problem({"frame_age_s": 3.0}, LIM).startswith("no fresh frame")
    assert camera_problem({"frame_age_s": 0.1, "fps": 2.0}, LIM).startswith("no fresh frame")
    assert camera_problem({"frame_age_s": 0.1, "fps": 30, "depth": False}, LIM) == "no depth image from the camera"
    assert camera_problem({"frame_age_s": 0.1, "fps": 30, "depth": True}, LIM) is None
    assert camera_problem({}, LIM) is None                         # not known = not stale
    assert pose_problem({"pose_age_s": 4.0}, LIM).startswith("no fresh pose")
    assert pose_problem({"pose_age_s": None}, LIM) is None


def test_the_reasons_classify_as_perception_unavailable():
    for why in (camera_problem({"frame_age_s": 3.0}, LIM), pose_problem({"pose_age_s": 4.0}, LIM)):
        assert classify_legacy_failure(why) is FailureCode.PERCEPTION_UNAVAILABLE


def test_find_precheck():
    rb = StubRobot({"frame_age_s": 9.0})
    r = find_precheck(rb, None, {"action": "Find", "object": "apple"})
    assert not r.ok and r.code is FailureCode.PERCEPTION_UNAVAILABLE and "9.0 s old" in r.msg
    assert find_precheck(rb, None, {"action": "Pick", "object": "apple"}) is None
    assert find_precheck(rb, None, {"action": "Find", "object": "apple"}, enabled=False) is None
    assert find_precheck(StubRobot({"frame_age_s": 0.1, "fps": 30, "depth": True}), None, {"action": "Find"}) is None
    quiet = StubRobot({"frame_age_s": 99.0}, limits=HealthLimits())
    assert find_precheck(quiet, None, {"action": "Find"}) is None and quiet.calls == 0   # no limits: not even read


def test_arrival_check():
    ok = [VerifyResult("isdone", True), VerifyResult("localization", True)]
    bad = arrival_check(StubRobot({"pose_age_s": 5.0}), None, {"action": "MoveTo", "object": DS}, ok)
    assert [(v.layer, v.ok) for v in bad] == [("localization", False)] and "arrival not confirmed" in bad[0].detail
    assert arrival_check(StubRobot({"pose_age_s": 0.2}), None, {"action": "MoveTo"}, ok) == []
    assert arrival_check(StubRobot({"pose_age_s": 5.0}), None, {"action": "MoveTo"}, [VerifyResult("isdone", False)]) == []
    assert arrival_check(StubRobot({"pose_age_s": 5.0}), None, {"action": "Find"}, ok) == []


# ── through AssistLoop (fakebot, with health limits and a health() the test controls) ──
TASK = {"status": "clear", "reason": "", "question": "",
        "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


def _loop(make_assist, env, health, **kw):
    robot = FakeRobot(env, {"apple": DS})
    loop = make_assist(robot, FakeLLM(default=TASK), ScriptedPlanner(PLAN), health_wait_s=0.05, **kw)
    loop.robot.geometry.extra.update(frame_max_age_s=1.0, pose_max_age_s=1.0)
    loop.robot.adapter.health = health
    return robot, loop


def test_stale_camera_waits_then_finds(make_assist, env):
    # read 1: the run-start reconcile's pose age (an adapter without pose()), 2: the MoveTo, 3: the Find (stale)
    seq = iter([{"frame_age_s": 0.1, "pose_age_s": 0.1}, {"frame_age_s": 0.1, "pose_age_s": 0.1},
                {"frame_age_s": 5.0, "pose_age_s": 0.1, "ok": False}])
    health = lambda node=None: next(seq, {"frame_age_s": 0.1, "pose_age_s": 0.1, "ok": True})   # noqa: E731
    robot, loop = _loop(make_assist, env, health)
    evs = run_task(loop, "Bring the apple from the desk to the dining table.")
    assert done(evs)["status"] == "success"
    assert any(e["event"] == "recover" and e.get("kind") == "wait_health" for e in evs)
    first = next(k for k, t in enumerate(robot.trace) if t[0] == "pick")
    assert [t[0] for t in robot.trace[:first]].count("find") == 1


def test_camera_stays_stale(make_assist, env):
    robot, loop = _loop(make_assist, env, lambda node=None: {"frame_age_s": 5.0, "pose_age_s": 0.1, "ok": False})
    d = done(run_task(loop, "Bring the apple from the desk to the dining table."))
    assert d["status"] == "failed" and d["code"] == "perception_unavailable"
    assert not [t for t in robot.trace if t[0] == "find"]


def test_stale_pose_is_not_an_arrival(make_assist, env):
    robot, loop = _loop(make_assist, env, lambda node=None: {"frame_age_s": 0.1, "pose_age_s": 5.0, "ok": False})
    evs = run_task(loop, "Bring the apple from the desk to the dining table.")
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "perception_unavailable"
    assert not any(e["event"] == "step_done" and e.get("status") == "success" for e in evs)


def test_health_checks_can_be_turned_off(make_assist, env):
    robot, loop = _loop(make_assist, env, lambda node=None: {"frame_age_s": 5.0, "pose_age_s": 5.0, "ok": False},
                        sensor_health=False)
    assert done(run_task(loop, "Bring the apple from the desk to the dining table."))["status"] == "success"
