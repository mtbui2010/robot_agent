"""Fix D7: done only after every goal is looked at (no false success when a Place lost the object or a
person undid a finished goal), and D12: a failed run's done event carries FailureCode, device, evidence."""

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DR, DT, DS, CH = "drawer@kitchen", "dining table@kitchen", "desk@study room", "chest@bedroom"
HOUSE = {"apple": DS, "mug": DS, "orange": DT}
BASIC = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
TASK = "Bring the apple from the desk to the dining table."


def U(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


APPLE = U(("on", "apple", "desk", "dining table"))


def checks(evs):
    return [(e["goal"]["object"], e["ok"]) for e in evs if e["event"] == "goal_check"]


def test_place_that_lost_the_object_is_not_success(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "place", "kind": "place_fall", "nth": 1}])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC)), TASK)
    d = done(evs)
    assert robot.obj["apple"] == "floor"
    assert d["status"] == "failed" and d["code"] == "dropped"
    assert checks(evs) == [("apple", False)]
    assert "not on the dining table" in d["msg"] and d["say"] and d["evidence"]["goal"].startswith("on apple")


def test_goal_undone_by_a_person_is_seen(make_assist, env):
    """mug -> chest, then apple -> table; a person puts the mug back on the desk meanwhile: the robot goes
    back to the chest to look, and does not report success."""
    plan = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {CH}", f"Place {CH}",
                 f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "move", "kind": "move_object", "obj": DS, "nth": 2,
                                           "what": "mug", "to": DS}])
    llm = FakeLLM(U(("on", "mug", "desk", "chest"), ("on", "apple", "desk", "dining table")))
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan)), "Take the mug from the desk to the chest, then the apple from the desk to the dining table.")
    d = done(evs)
    assert checks(evs) == [("apple", True), ("mug", False)]          # where it stands first, then the chest
    assert robot.trace[-2:] == [("move", CH, True, f"arrived at {CH}"), ("find", "mug", False, "mug not found")]
    assert d["status"] == "failed" and d["code"] == "absent"


def test_success_is_looked_at(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC)), TASK)
    assert done(evs)["status"] == "success" and checks(evs) == [("apple", True)]
    assert "code" not in done(evs)


def test_goal_check_belief_keeps_the_old_behaviour(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "place", "kind": "place_fall", "nth": 1}])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC), goal_check="belief"), TASK)
    assert done(evs)["status"] == "success" and checks(evs) == []


def test_object_in_a_closed_drawer_is_unknown_not_failed(make_assist, env):
    plan = steps(f"MoveTo {DR}", "Open drawer@kitchen", f"MoveTo {DT}", "Find orange", "Pick orange",
                 f"MoveTo {DR}", f"PutIn {DR}", f"Close {DR}")
    llm = FakeLLM(U(("in", "orange", "dining table", "kitchen drawer"), ("close", "kitchen drawer", "", "")))
    robot = FakeRobot(env, HOUSE)
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan)), "Orange in the drawer, then close it.")
    assert done(evs)["status"] == "success"
    assert [e["ok"] for e in evs if e["event"] == "goal_check"] == [None, None]     # belief, said so


def test_failed_run_carries_code_device_evidence(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "pick", "kind": "swerr", "nth": 1}])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC)), TASK)
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "software" and d["device"] == "pick"
    assert d["evidence"]["step"] == "Pick apple" and "TypeError" in d["evidence"]["reason"]


def test_run_code_is_the_first_failure_not_recovered(make_assist, env):
    """Pick fails (gripper empty after the grasp, the apple fell), the re-look finds nothing: the run's code is
    the grasp failure that started it, not the Find miss after it."""
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "grasp_succeed", "kind": "drop", "obj": "apple", "nth": 1}])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC), max_replans=0, ask_help=False), TASK)
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "grasp_failed"
