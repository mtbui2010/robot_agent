"""Roadmap steps 4 + 5: the planning loops reach the robot only through its RobotAdapter
(``contract.load_adapter`` -> ``RobotView``), verify with the manifest's VerifySpecs, normalise skill
results once (``run_skill``) and decide stop / retry from the recovery policy by FailureCode."""

import re
import subprocess
import types
from pathlib import Path

import pytest

from fakes import FakeLLM, FakeRobot, FakeSkillRegistry, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import FailureCode, Manifest, NamemapAdapter, SkillResult
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.robot import RobotView, run_skill
from robot_agent.core.planning.verify import StepVerifier, effects_check

ROOT = Path(__file__).resolve().parents[1]
DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"
HOUSE = {"apple": DS, "mug": DS, "orange": DT, "sponge": CT}
BASIC = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
APPLE = {"status": "clear", "reason": "", "question": "",
         "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
TASK = "Bring the apple from the desk to the dining table."


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


@pytest.fixture
def robot():
    return RobotView.load("fakebot")


@pytest.fixture(autouse=True)
def _clear_cancel():
    yield
    from robot_agent.core.run_control import begin_run
    begin_run()


# ── acceptance A: no robot names in the core ──
def test_core_names_no_robot():
    hits = subprocess.run(["grep", "-rnE", r"grasp_succeed|mobile_pose|kcare|kaair|MOBILE_HEIGHT",
                           "robot_agent/core", "robot_agent/interact", "robot_agent/contract", "--include=*.py"],
                          cwd=ROOT, capture_output=True, text=True).stdout
    assert hits == "", hits
    heur = subprocess.run(["grep", "-rnE", r"\"drawer\" in |'drawer' in |\b0\.78\b|\"0\.3\"|\"ko\"\s*\)",
                           "robot_agent/core/planning", "--include=*.py"], cwd=ROOT, capture_output=True, text=True).stdout
    assert heur == "", heur


def test_lint_core_is_blocking():
    mk = (ROOT / "Makefile").read_text()
    assert re.search(r"^LINT_STRICT \?= 1$", mk, re.M)
    assert "LINT_CORE_DIRS ?= core interact contract" in mk


# ── B: the loops get the robot from load_adapter ──
def test_loops_use_the_adapter(make_assist, env):
    from fakebot.configs.adapter import Adapter
    loop = make_assist(FakeRobot(env, HOUSE), FakeLLM(APPLE), ScriptedPlanner(BASIC))
    assert isinstance(loop.robot, RobotView) and isinstance(loop.robot.adapter, Adapter)
    assert loop.robot.bind({"action": "MoveTo", "object": DS}, W())[0] == "move"
    assert loop._bind({"action": "Find", "object": "apple"}, W()) == ("find", {"inputs": "apple", "min_conf": 0.55})
    assert loop.cfg["arrive_tol"] == 0.3 and loop.cfg["reach_lateral"] == 0.78 and loop.cfg["reach_far"] == 1.0


def test_namemap_only_robot_still_works():
    """A robot package with only grace_namemap (no manifest / adapter) gets the shim: no sensors,
    so its post-step physical check is UNKNOWN, never a silent pass."""
    from fakebot.configs import grace_namemap
    r = RobotView(NamemapAdapter(grace_namemap))
    assert r.holding_sensor() is None and r.pose_sensor() is None and r.ask_spec() is None
    v = StepVerifier(False, set(), robot=r)
    out = v.verify({"action": "Pick", "object": "cup"}, {"isdone": True}, W(found="cup"), None)
    assert [(x.layer, x.ok, x.unknown) for x in out] == [("isdone", True, False), ("symbolic", True, False),
                                                       ("sensor", True, True)]


def test_ask_goes_through_the_manifest_ask_skill(make_assist, env):
    robot = FakeRobot(env, HOUSE, answers=["the dining table"])
    loop = make_assist(robot, FakeLLM(APPLE), ScriptedPlanner([]))
    assert loop._ask("Where?", None, None, "en") == "the dining table"
    assert robot.asked == ["Where?"]
    loop.robot.adapter.manifest.skills = [s for s in loop.robot.manifest.skills if s.action != "Ask"]
    try:
        assert loop._ask("Where?", None, None, "en") == ""          # no Ask skill: no channel
    finally:
        loop.robot.adapter.manifest = Manifest.from_yaml(ROOT / "tests" / "fakebot" / "configs" / "manifest.yaml")


# ── D: after a reported success, effects + VerifySpecs, never the preconditions ──
def test_effects_check_does_not_reject_on_preconditions():
    # Pick without a Find in the belief: the guard would refuse it, the post-step check does not
    assert effects_check({"action": "Pick", "object": "cup"}, W(), ["holding(?inputs)", "not hand_empty()"]) == (True, "")
    ok, why = effects_check({"action": "MoveTo", "object": "a"}, W(), ["arrived(?inputs)", "opened(?inputs)"])
    assert not ok and "opened(?inputs)" in why


def test_sensor_verify_specs(robot):
    calls = []

    def sensor(skill):
        calls.append(skill)
        return SkillResult.from_legacy({"isdone": holding[0]})

    holding = [True]
    v = StepVerifier(False, set(), robot=robot, sensor=sensor, locate=lambda k: (0.0, 0.0))
    out = v.verify({"action": "Pick", "object": "cup"}, {"isdone": True}, W(found="cup"), None)
    assert out[-1].layer == "sensor" and out[-1].ok and calls == ["grasp_succeed"]
    holding[0] = False
    out = v.verify({"action": "Pick", "object": "cup"}, {"isdone": True}, W(found="cup"), None)
    assert not out[-1].ok and out[-1].detail == "pick reported success but the gripper is empty"
    # Place: the same sensor must read an empty gripper (expect: false)
    out = v.verify({"action": "Place", "object": DT}, {"isdone": True}, W(arrived=DT, holding="cup"), None)
    assert out[-1].ok
    # Find declares no sensor: UNKNOWN, visible, not a failure
    out = v.verify({"action": "Find", "object": "cup"}, {"isdone": True}, W(), None)
    assert out[-1].unknown and out[-1].ok and "find declares no sensor" in out[-1].detail


def test_pose_sensor_measures_the_arrival(robot):
    v = StepVerifier(False, set(), robot=robot, locate=lambda k: (1.0, 0.0),
                     sensor=lambda s: SkillResult.from_legacy({"isdone": True, "pose": [1.5, 0.0, 0.0]}))
    out = v.verify({"action": "MoveTo", "object": DS}, {"isdone": True}, W(), None, params={"inputs": DS})
    assert (out[-1].layer, out[-1].ok, out[-1].detail) == ("localization", False, f"stopped 0.50 m from {DS}")

    def broken(s):
        raise RuntimeError("no /odom")
    out = StepVerifier(False, set(), robot=robot, sensor=broken, locate=lambda k: (1.0, 0.0)).verify(
        {"action": "MoveTo", "object": DS}, {"isdone": True}, W(), None)
    assert out[-1].ok and out[-1].unknown and "no /odom" in out[-1].detail


def test_closed_loop_keeps_a_reported_success(agent_state, tmp_path, monkeypatch, env):
    """ClosedLoop's old post-check re-ran the preconditions and rejected a successful Pick the belief
    had no Find for; now the step stands and its effect is applied."""
    import robot_agent.skill_configs as sc
    import robot_agent.core.planning.loop as LP
    monkeypatch.setattr(sc, "ENV", env)
    monkeypatch.setattr(LP, "reconcile_world", lambda node, world, threshold=0.8: world)

    class Bot(FakeRobot):
        def _exec(self, skill, o, params):
            if skill == "pick":                      # the robot had seen it already
                self.holding, self.obj["apple"] = "apple", "robot"
                return {"isdone": True, "msg": "grasped"}
            return super()._exec(skill, o, params)

    bot = Bot(env, HOUSE)
    agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(bot), device_manager=types.SimpleNamespace(_ros_node=None),
                                  _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
    loop = LP.ClosedLoop(agent, log_dir=str(tmp_path / "runs"), live_path=str(tmp_path / "l.jsonl"),
                         mute_skill_tts=False, speak_backend=False, vlm_enabled=False)
    loop._make_planner = lambda: ScriptedPlanner(steps("Pick apple"))
    evs = run_task(loop, "pick the apple")
    assert done(evs)["status"] == "success"
    assert agent_state.world.holding == "apple"
    assert not any(e["event"] == "replan" for e in evs)


# ── C: one normalisation, codes, policy ──
def test_run_skill_is_the_one_normalisation():
    class SR:
        def __init__(self, ret):
            self.ret = ret

        def execute(self, skill, params, node=None, log_fn=None):
            if isinstance(self.ret, Exception):
                raise self.ret
            return self.ret

    r = run_skill(SR(RuntimeError("TypeError: x() got an unexpected keyword argument 'y'")), "pick", {})
    assert not r.ok and r.code is FailureCode.SOFTWARE
    assert run_skill(SR({"isdone": False, "msg": "apple not found"}), "find", {}).code is FailureCode.ABSENT
    assert not run_skill(SR({"note": "no verdict"}), "find", {}).ok               # strict: no verdict = failure
    assert run_skill(SR({"isdone": True, "ins": {"a": 1}}), "find", {}).to_legacy()["ins"] == {"a": 1}
    with pytest.raises(RuntimeError):
        run_skill(SR(RuntimeError("down")), "grasp_succeed", {}, raise_errors=True)


def test_estop_is_not_retried(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "find", "kind": "fail", "nth": 1, "msg": "e-stop pressed"}])
    planner = ScriptedPlanner(BASIC, replans=[BASIC])
    d = done(run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK))
    assert d["status"] == "failed" and d["msg"] == "estop in the find skill (not retried): e-stop pressed"
    assert [c for c in planner.calls if c[0] == "replan"] == []


class ObsPlanner(ScriptedPlanner):
    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        self.calls.append(("obs", obs))
        return super().replan(task, completed, failed_step, failure_reason, obs, visible_objects)


@pytest.mark.parametrize("msg, searched", [("find: no fresh frame from the head camera", False),   # could not look
                                           ("apple not found", True)])                            # absent
def test_only_an_absence_marks_the_place_searched(make_assist, env, msg, searched):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "find", "kind": "fail", "nth": 1, "msg": msg}])
    planner = ObsPlanner(BASIC, replans=[BASIC])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK)
    assert done(evs)["status"] == "success"
    obs = next(c[1] for c in planner.calls if c[0] == "obs")
    assert ("Already looked (object not there): " + DS in obs) is searched


def test_capabilities_reach_the_planner(make_assist, env, monkeypatch):
    import robot_agent.core.planning.loop as LP
    seen = {}
    monkeypatch.setattr(LP, "get_planner", lambda name, **kw: seen.update(name=name, **kw) or ScriptedPlanner([]))
    loop = make_assist(FakeRobot(env, HOUSE), FakeLLM(APPLE), ScriptedPlanner([]))
    del loop._make_planner                                  # the real one
    loop._make_planner()
    assert seen["name"] == "grace" and seen["capabilities"]["find_reaches"] is False
    assert seen["capabilities"]["hands"] == ["right"]


def test_sage_planner_gets_the_robot_body():
    pytest.importorskip("embodiplan")
    from robot_agent.core.planning.backends.pyplanner_grace import GraceBackend
    b = GraceBackend(host="http://127.0.0.1:9", model="x", capabilities={"hands": ["right"], "find_reaches": False},
                     live_path="")
    assert b._p.capabilities.hands == 1 and b._p.capabilities.find_reaches is False
    # with Find only looking, a found drawer is not open-able from afar: the gate wants the MoveTo
    from robot_agent.core.planning import action_model
    codes = [v.code for v in action_model.check({"action": "Open", "object": DR}, W(found=DR),
                                               capabilities=b._p.capabilities)]
    assert "interact_without_find" in codes


def test_announcer_phrases_and_quiet_skills_come_from_the_robot(robot):
    from robot_agent.core.planning.announcer import Announcer
    a = Announcer(lang="ko", speak_backend=False, i18n=robot.i18n, quiet=robot.quiet_skills)
    assert a.announce("task_start") == "작업을 시작합니다"
    assert a.announce("step_start", action="check_fall") == ""
    assert Announcer(lang="ko", speak_backend=False, i18n=None, quiet=()).announce("task_start") == "Starting the task"


def test_verify_spec_expect_in_the_manifest():
    from robot_agent.contract import ManifestError
    d = {"robot": "r", "skills": [{"name": "place", "action": "Place",
                                   "verify": [{"kind": "sensor", "skill": "hand", "expect": False}]}]}
    v = Manifest.from_dict(d).skill_for("Place").verify[0]
    assert v.expect is False and v.expected is False and v.to_dict()["expect"] is False
    d["skills"][0]["verify"][0]["expect"] = "no"
    with pytest.raises(ManifestError, match="expect"):
        Manifest.from_dict(d)
