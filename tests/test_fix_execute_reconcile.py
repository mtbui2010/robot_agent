"""Fix D8 (the belief is reconciled through the RobotAdapter and the declared sensors: holding, a container
believed open) and D17 (after a pause: reconcile, and look again before a Pick on a stale Find)."""

import threading
import types

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import Manifest, NamemapAdapter, SkillResult
from robot_agent.core import run_control
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.robot import RobotView

DR, DT, DS = "drawer@kitchen", "dining table@kitchen", "desk@study room"
HOUSE = {"apple": DS, "spoon": DR + "/in"}
APPLE = {"status": "clear", "reason": "", "question": "",
         "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
BASIC = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


class _Adapter(NamemapAdapter):
    def __init__(self, manifest):
        super().__init__(types.ModuleType("nm"), manifest)
        self.calls = []

    def reconcile(self, world, node=None):
        self.calls.append(node)
        world.arrived = "kitchen"
        return world


def _view(manifest_dict=None):
    m = Manifest.from_dict(manifest_dict or {"robot": "r", "skills": [
        {"name": "pick", "action": "Pick", "verify": [{"kind": "sensor", "skill": "grip_check"}]}]})
    return RobotView(_Adapter(m))


def test_reconcile_goes_through_the_adapter_then_the_holding_sensor():
    v = _view()
    w = WorldState(arrived="dock", holding="cup", holding_since=1.0)
    changed = v.reconcile(w, node="N", sense=lambda skill: SkillResult(skill != "grip_check"))
    assert v.adapter.calls == ["N"]
    assert w.arrived == "kitchen" and w.holding is None and w.holding_since is None
    assert changed == {"arrived": ["dock", "kitchen"], "holding": ["cup", None]}


def test_reconcile_keeps_the_belief_when_the_sensor_cannot_be_read():
    v = _view()
    w = WorldState(holding="cup")

    def broken(skill):
        raise RuntimeError("no wrist camera")
    v.reconcile(w, sense=broken)
    assert w.holding == "cup"
    v.reconcile(w, sense=lambda s: SkillResult(True))           # still held
    assert w.holding == "cup"


def test_stale_holding_is_fixed_at_run_start(make_assist, env):
    robot = FakeRobot(env, HOUSE)                                # the gripper is empty
    loop = make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC), world={"holding": "tennis ball"})
    evs = run_task(loop, "Bring the apple from the desk to the dining table.")
    rec = [e for e in evs if e["event"] == "reconcile"]
    assert rec and rec[0]["changed"]["holding"] == ["tennis ball", None]
    assert done(evs)["status"] == "success" and robot.obj["apple"] == DT


def test_find_in_a_container_believed_open_doubts_it(make_assist, env):
    """The drawer is believed open, it is closed: the Find miss there does not count it searched; the replan
    is told to open it (precondition), and the run succeeds."""
    plan = steps(f"MoveTo {DR}", "Find spoon", "Pick spoon", f"MoveTo {DT}", f"Place {DT}")
    fixed = steps(f"Open {DR}", "Find spoon", "Pick spoon", f"MoveTo {DT}", f"Place {DT}")
    planner = ScriptedPlanner(plan, replans=[fixed])
    llm = FakeLLM({"status": "clear", "reason": "", "question": "",
                   "goals": [{"type": "on", "object": "spoon", "from": "kitchen drawer", "to": "dining table"}]})
    robot = FakeRobot(env, HOUSE)
    evs = run_task(make_assist(robot, llm, planner, world={"opened": [DR]}), "Bring the spoon from the kitchen drawer to the dining table.")
    rp = [c for c in planner.calls if c[0] == "replan"]
    assert len(rp) == 1 and "precondition: drawer@kitchen is not open" in rp[0][2]
    assert "it is not at" not in rp[0][2]                          # not counted as searched
    assert done(evs)["status"] == "success" and robot.obj["spoon"] == DT


def test_pause_point_calls_on_resume_only_after_a_real_pause(monkeypatch):
    calls = []
    run = types.SimpleNamespace(paused=False, wait_if_paused=lambda timeout=None: True)
    monkeypatch.setattr(run_control, "current_run", lambda: run)
    assert run_control.pause_point(calls.append) and calls == []
    ev = threading.Event()
    run.paused = True
    run.wait_if_paused = lambda timeout=None: ev.wait(1.0)
    threading.Timer(0.05, ev.set).start()
    assert run_control.pause_point(calls.append)
    assert len(calls) == 1 and calls[0] >= 0.04
    # cancelled while paused: no resume hook
    calls.clear()
    run.wait_if_paused = lambda timeout=None: False
    assert not run_control.pause_point(calls.append) and calls == []


def test_resume_looks_again_before_the_pick(make_assist, env, monkeypatch):
    """Paused right after the Find; the apple is moved meanwhile: the robot looks again before it picks."""
    robot = FakeRobot(env, HOUSE)
    state = {"n": 0}

    def fake_pause_point(on_resume=None):
        state["n"] += 1
        if state["n"] == 3:                                      # before step 2 (Pick apple)
            robot.obj["apple"] = "counter@kitchen"
            on_resume(5.0)
        return True
    monkeypatch.setattr(run_control, "pause_point", fake_pause_point)
    planner = ScriptedPlanner(BASIC, replans=[steps("MoveTo counter@kitchen", "Find apple", "Pick apple",
                                                    f"MoveTo {DT}", f"Place {DT}")])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner), "Bring the apple from the desk to the dining table.")
    after = [s for s, _o, _ok, _m in robot.trace][2:4]
    assert after == ["find", "move"]                             # looked again (missed), no Pick on the old Find
    assert any(e.get("kind") == "refind" for e in evs)
    assert done(evs)["status"] == "success" and robot.obj["apple"] == DT
