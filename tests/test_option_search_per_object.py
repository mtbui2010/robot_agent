"""Option ``search_per_object`` (default OFF), through the real AssistLoop: two objects, the apple found only after three missed
places, the towel named at a place where it is not, ``search_total`` 4. One budget for the run: the apple's three misses are charged to the towel, whose first miss is then the budget's last look. Per object: the second search has the whole budget."""
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS

DT, SF, CT, DS, SD = "dining table@kitchen", "shelf@kitchen", "counter@kitchen", "desk@study room", "side table@living room"
TASK = "Put the apple and the towel on the dining table."
UNDERSTOOD = {"status": "clear", "reason": "", "question": "",
              "goals": [{"type": "on", "object": "apple", "from": "", "to": "dining table"},
                       {"type": "on", "object": "towel", "from": "counter", "to": "dining table"}]}
PLAN = steps(f"MoveTo {SF}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}",
             f"MoveTo {CT}", "Find towel", "Pick towel", f"MoveTo {DT}", f"Place {DT}")


def go(make_assist, env, **opts):
    robot = FakeRobot(env, {"apple": DS, "towel": DS})
    llm = FakeLLM(UNDERSTOOD, default={"ranked": [SF, CT, DS, SD]})
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN), search_legs=1, search_rank="prior", search_max=9, search_total=4,
                       ask_help=False, detector_miss_rate=0, **opts)
    return robot, run_task(loop, TASK, max_events=4000)


def test_registered_and_off_by_default_the_run_has_one_budget(make_assist, env):
    assert "search_per_object" in ASSIST_OPTIONS
    robot, evs = go(make_assist, env)
    assert done(evs)["status"] == "failed", done(evs)                 # the second search ran out of the run's budget


def test_on_each_object_has_its_own_budget(make_assist, env):
    robot, evs = go(make_assist, env, search_per_object="1")
    assert robot.obj["apple"] == DT and robot.obj["towel"] == DT and done(evs)["status"] == "success", done(evs)

