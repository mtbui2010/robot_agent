"""Fix stream `plan`, D1: a goal that already holds at the start is not a plan error. The plan may leave
it out; the run looks at the destination at the end and the goal holds by observation."""

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DT, DS, ST, CH = "dining table@kitchen", "desk@study room", "stool@bedroom", "chest@bedroom"


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def test_goal_already_true_is_seen_not_moved(make_assist, env):
    house = {"apple": DS, "banana": DT}
    robot = FakeRobot(env, house)
    llm = FakeLLM(default=understood(("on", "apple", "", "dining table"), ("on", "banana", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner), "Put the apple and the banana on the dining table.")
    assert done(evs)["status"] == "success"
    assert ("pick", "banana") not in [(s, o) for s, o, _ok, _m in robot.trace]
    assert [(s, o) for s, o, ok, _m in robot.trace][-1] == ("find", "banana")       # seen there
    assert not [c for c in planner.calls if c[0] == "replan"]


def test_goal_left_out_and_not_true_is_replanned(make_assist, env):
    house = {"apple": DS, "banana": ST}                    # the banana is NOT on the dining table
    robot = FakeRobot(env, house)
    llm = FakeLLM(default=understood(("on", "apple", "", "dining table"), ("on", "banana", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"),
                              replans=[steps(f"MoveTo {ST}", "Find banana", "Pick banana", f"MoveTo {DT}", f"Place {DT}")])
    evs = run_task(make_assist(robot, llm, planner), "Put the apple and the banana on the dining table.")
    assert done(evs)["status"] == "success" and robot.obj["banana"] == DT
    rp = [c for c in planner.calls if c[0] == "replan"]
    assert len(rp) == 1 and "banana" in rp[0][2]


def test_all_goals_already_true_with_an_empty_plan(make_assist, env):
    robot = FakeRobot(env, {"banana": DT})
    llm = FakeLLM(default=understood(("on", "banana", "", "dining table")))
    evs = run_task(make_assist(robot, llm, ScriptedPlanner([])), "Put the banana on the dining table.")
    assert done(evs)["status"] == "success"
    assert [(s, o) for s, o, _ok, _m in robot.trace] == [("move", DT), ("find", "banana")]


def test_empty_plan_for_a_known_source_still_fails(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    llm = FakeLLM(default=understood(("on", "apple", "desk", "dining table")))
    evs = run_task(make_assist(robot, llm, ScriptedPlanner([])), "Bring the apple from the desk to the dining table.")
    assert done(evs)["status"] == "failed" and robot.trace == []
