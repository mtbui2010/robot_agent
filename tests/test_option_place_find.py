"""Option ``place_find`` (default OFF), through the real AssistLoop: the planner writes ``MoveTo P, Find P, Open P`` (a Find of the
place it moves to; the look at a place key is always absent). On: the Find is dropped (the loop test; the Open case is the hook test). A Find of a real object is kept."""
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS

DR, DT = "drawer@kitchen", "dining table@kitchen"
TASK = "Put the mug on the dining table."
UNDERSTOOD = {"status": "clear", "reason": "", "question": "",
              "goals": [{"type": "on", "object": "mug", "from": "drawer", "to": "dining table"}]}
PLAN = steps(f"MoveTo {DR}", f"Find {DR}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")


def go(make_assist, env, roles=True, **opts):
    robot = FakeRobot(env, {"mug": DR})
    loop = make_assist(robot, FakeLLM(default=UNDERSTOOD), ScriptedPlanner(PLAN), detector_miss_rate=0, **opts)
    if roles:
        loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    return robot, loop, run_task(loop, TASK, max_events=3000)


def finds(robot):
    return [t[1] for t in robot.trace if t[0] == "find"]


def test_registered_and_off_by_default_the_place_is_looked_for(make_assist, env):
    assert "place_find" in ASSIST_OPTIONS
    robot, loop, _evs = go(make_assist, env)
    assert DR in finds(robot)                                    # the wasted look the option removes
    assert not [e for e in loop.lab_log if e["flag"] == "place_find"]


def test_on_the_find_of_the_place_is_dropped_and_a_real_find_is_kept(make_assist, env):
    robot, loop, evs = go(make_assist, env, place_find="1")
    assert {e["place"] for e in loop.lab_log if e["flag"] == "place_find"} == {DR}
    assert DR not in finds(robot) and "mug" in finds(robot) and done(evs)["status"] == "success", done(evs)


def test_without_the_find_role_declared_nothing_is_dropped(make_assist, env):
    robot, loop, _evs = go(make_assist, env, roles=False, place_find="1")
    assert not [e for e in loop.lab_log if e["flag"] == "place_find"]


def test_the_hook_alone_move_find_open_becomes_move_open(make_assist, env):
    robot, loop, _evs = go(make_assist, env, place_find="1")
    from robot_agent.core.planning.assist_loop import Places
    run = type("R", (), {"places": Places(env), "world": type("W", (), {"arrived": None})()})()
    opt = loop.options.option("place_find")
    got = opt.on_plan(steps(f"MoveTo {DR}", f"Find {DR}", f"Open {DR}"), run)
    assert [(s["action"], s["object"]) for s in got] == [("MoveTo", DR), ("Open", DR)]
    assert opt.on_plan(steps(f"MoveTo {DR}", "Find mug", f"Open {DR}"), run) is None       # a real object stays
    assert opt.on_plan(steps(f"Find {DR}"), run) is None                                    # not there, not going there: kept
