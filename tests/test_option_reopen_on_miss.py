"""Option ``reopen_on_miss`` (default OFF), through the real AssistLoop: the fridge opens only part way (the robot says ok, the mug inside is
not visible), the Find after the Open misses. Off: the miss is a search miss. On, with the pack's data ``planner.recovery.reopen_on_miss``:
Close, Open, and the Find is made again, once; the pack that declares nothing gets nothing."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS

DR, DT = "fridge@kitchen", "dining table@kitchen"
ENV = {DR: {"aliases": ["fridge"], "loc": {"x": 3.0, "y": 1.5}, "openable": True},
       DT: {"aliases": ["dining table"], "loc": {"x": 1.0, "y": 1.0}},
       "anna@kitchen": {"person": True, "speaker": True, "aliases": ["me", "anna"], "loc": {"x": 1.2, "y": 1.0}}}
TASK = "Take the mug from the fridge to the dining table."
UNDERSTOOD = {"status": "clear", "reason": "", "question": "",
              "goals": [{"type": "on", "object": "mug", "from": DR, "to": DT}]}
PLAN = steps(f"MoveTo {DR}", f"Open {DR}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")


@pytest.fixture
def env():
    return ENV


PARTIAL = [{"skill": "open_drawer", "nth": 1, "kind": "partial_open"}]


def go(make_assist, env, faults=PARTIAL, data=True, where=DR + "/in", **opts):
    robot = FakeRobot(env, {"mug": where}, faults=faults)
    loop = make_assist(robot, FakeLLM(UNDERSTOOD, default="{}"), ScriptedPlanner(PLAN), detector_miss_rate=0, ask_help=False, **opts)
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo", "find": "Find", "open": "Open", "close": "Close"}
    if data:
        pl["recovery"] = {"reopen_on_miss": ["Close", "Open"]}
    return robot, loop, run_task(loop, TASK, max_events=3000)


def lab(loop):
    return [e for e in loop.lab_log if e["flag"] == "reopen_on_miss"]


def test_registered_and_off_by_default_the_partial_open_is_a_miss(make_assist, env):
    assert "reopen_on_miss" in ASSIST_OPTIONS
    robot, loop, evs = go(make_assist, env)
    assert not lab(loop) and robot.obj["mug"] != DT


def test_on_the_container_is_closed_and_opened_again_and_the_mug_is_found(make_assist, env):
    robot, loop, evs = go(make_assist, env, reopen_on_miss="1")
    assert [e["container"] for e in lab(loop)] == [DR]
    assert robot.obj["mug"] == DT and done(evs)["status"] == "success", done(evs)


def test_on_but_the_pack_declares_nothing_does_nothing(make_assist, env):
    robot, loop, evs = go(make_assist, env, data=False, reopen_on_miss="1")
    assert not lab(loop) and robot.obj["mug"] != DT


def test_once_per_container_when_the_mug_is_really_not_there(make_assist, env):
    robot, loop, evs = go(make_assist, env, where="nowhere", reopen_on_miss="1")
    assert len(lab(loop)) == 1                                  # the second miss is a miss: no third Open
