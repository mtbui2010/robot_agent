"""Option ``end_state`` (default OFF), through the real AssistLoop: the pack declares the state a task ends in (``planner.end_state``: places and
the action that makes it true). The reader put the ``go`` goal in the middle of the goal list, the plan ended elsewhere, and the run said done.
On: before the goals are checked the robot goes there and runs the declared action (a stand-in action of the fake robot: Close); when that fails the
run does not say success."""
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS

FR, DT, CT = "drawer@kitchen", "dining table@kitchen", "counter@kitchen"
TASK = "Go to the drawer, then take the mug from the dining table to the counter."
UNDERSTOOD = {"status": "clear", "reason": "", "question": "",
              "goals": [{"type": "go", "object": "", "from": "", "to": FR},
                        {"type": "on", "object": "mug", "from": DT, "to": CT}]}
PLAN = steps(f"MoveTo {FR}", f"MoveTo {DT}", "Find mug", "Pick mug", f"MoveTo {CT}", f"Place {CT}")


def go(make_assist, env, faults=(), data=True, **opts):
    robot = FakeRobot(env, {"mug": DT}, faults=faults)
    loop = make_assist(robot, FakeLLM(UNDERSTOOD, default="{}"), ScriptedPlanner(PLAN), detector_miss_rate=0, ask_help=False, **opts)
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo"}
    if data:
        pl["end_state"] = {"places": ["drawer"], "action": "Close"}
    return robot, loop, run_task(loop, TASK, max_events=3000)


def closes(robot):
    return [t for t in robot.trace if t[0] == "close_drawer"]


def test_registered_and_off_by_default_the_run_ends_where_the_plan_left_it(make_assist, env):
    assert "end_state" in ASSIST_OPTIONS
    robot, loop, evs = go(make_assist, env)
    assert not closes(robot) and robot.at == CT


def test_on_the_robot_goes_to_the_place_and_runs_the_declared_action(make_assist, env):
    robot, loop, evs = go(make_assist, env, end_state="1")
    assert robot.at == FR and closes(robot)[-1][2] and done(evs)["status"] == "success", done(evs)
    assert [(e["place"], e["ok"]) for e in loop.lab_log if e["flag"] == "end_state"] == [(FR, True)]


def test_on_but_the_action_fails_the_run_does_not_say_success(make_assist, env):
    robot, loop, evs = go(make_assist, env, faults=[{"skill": "close_drawer", "nth": "all", "kind": "fail"}], end_state="1")
    assert done(evs)["status"] == "failed", done(evs)


def test_on_without_the_pack_data_does_nothing(make_assist, env):
    robot, loop, evs = go(make_assist, env, data=False, end_state="1")
    assert not closes(robot) and not [e for e in loop.lab_log if e["flag"] == "end_state"]

