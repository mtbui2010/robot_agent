"""Option ``confirm_look`` (default OFF), through the real AssistLoop: the goal check's first look at the destination misses and the second one
sees the object (a false sighting: the mug really fell to the floor). Off: the second look is believed and the run says success. On: a third
look decides, finds nothing, and the run does not say success. A real sighting (the object is there) is also confirmed, at the cost of one look."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

DS, DT = "desk@study room", "dining table@kitchen"
TASK = "Put the mug on the dining table."
UNDERSTOOD = {"status": "clear", "reason": "", "question": "",
              "goals": [{"type": "on", "object": "mug", "from": "desk", "to": "dining table"}]}
PLAN = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start(TASK, user_id="alice", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


def go(make_assist, env, faults, **opts):
    robot = FakeRobot(env, {"mug": DS}, faults=faults)
    loop = make_assist(robot, FakeLLM(default=UNDERSTOOD), ScriptedPlanner(PLAN), detector_miss_rate=0, **opts)
    return robot, run_task(loop, TASK, max_events=3000)


FALL_THEN_FALSE_SIGHTING = [{"skill": "place", "kind": "place_fall", "nth": 1},
                            {"skill": "find", "obj": "mug", "kind": "false_positive", "nth": 3}]       # finds: desk, 1st check, 2nd check


def test_registered_and_off_by_default_the_second_look_is_believed(run, make_assist, env):
    from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
    assert "confirm_look" in ASSIST_OPTIONS
    robot, events = go(make_assist, env, FALL_THEN_FALSE_SIGHTING)
    assert robot.obj["mug"] == "floor" and done(events)["status"] == "success"      # the wrong-done this option exists for


def test_on_a_third_look_does_not_confirm_a_sighting_that_followed_a_miss(run, make_assist, env):
    robot, events = go(make_assist, env, FALL_THEN_FALSE_SIGHTING, confirm_look="1")
    assert robot.obj["mug"] == "floor" and done(events)["status"] != "success"


def test_on_a_real_sighting_after_a_camera_miss_is_confirmed(run, make_assist, env):
    faults = [{"skill": "find", "obj": "mug", "kind": "fail", "nth": 2}]             # the first check misses; the mug is on the table
    robot, events = go(make_assist, env, faults, confirm_look="1")
    assert robot.obj["mug"] == DT and done(events)["status"] == "success"
