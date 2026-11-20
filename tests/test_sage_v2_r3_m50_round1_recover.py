"""Recovery by the contract: a dropped object is found again and picked up (rule REFIND), a miss where the object
was just lost is looked at twice, and a search with legs ends on its look total, not on a count of misses."""
import types

from robot_agent.contract.recovery import DEFAULT_RECOVERY
from robot_agent.contract.types import FailureCode
from robot_agent.core.planning.assist_loop import AssistLoop, Places

ENV = {"floor@den": {"aliases": ["floor"]}, "shelf@den": {"aliases": ["shelf"]},
       "table@hall": {"aliases": ["table"]}, "person_k": {"person": "k", "spot": "table@hall", "aliases": ["Kim"]}}


def run(steps, i, arrived="shelf@den"):
    return types.SimpleNamespace(
        steps=list(steps), i=i, places=Places(ENV), refinds={}, found_at={}, relooked=set(),
        world=types.SimpleNamespace(arrived=arrived, found="vase"))


def test_dropped_object_is_refound_on_the_floor_of_its_room():
    lp = AssistLoop.__new__(AssistLoop)
    plan = [{"action": "Pick", "object": "vase"}, {"action": "MoveTo", "object": "shelf@den"},
            {"action": "Place", "object": "shelf@den"}]
    r = run(plan, 2)
    rule = DEFAULT_RECOVERY[FailureCode.DROPPED]
    assert lp._refind_dropped(r, plan[2], rule) is True
    assert [(s["action"], s["object"]) for s in r.steps[2:]] == [
        ("MoveTo", "floor@den"), ("Find", "vase"), ("Pick", "vase"), ("MoveTo", "shelf@den"), ("Place", "shelf@den")]
    assert r.world.found is None and r.found_at["vase"] == "floor@den"


def test_refind_is_bounded_by_the_rule_and_needs_a_pick():
    lp = AssistLoop.__new__(AssistLoop)
    rule = DEFAULT_RECOVERY[FailureCode.DROPPED]
    plan = [{"action": "Pick", "object": "vase"}, {"action": "Place", "object": "shelf@den"}]
    r = run(plan, 1)
    assert lp._refind_dropped(r, plan[1], rule) is True
    r.i = 5
    assert lp._refind_dropped(r, {"action": "Place", "object": "shelf@den"}, rule) is False      # second time: no
    assert lp._refind_dropped(run([{"action": "Place", "object": "shelf@den"}], 0), {"action": "Place"}, rule) is False


def test_refind_in_a_persons_spot_uses_the_spot_room_and_needs_a_floor():
    lp = AssistLoop.__new__(AssistLoop)
    plan = [{"action": "Pick", "object": "vase"}, {"action": "Place", "object": "person_k"}]
    rule = DEFAULT_RECOVERY[FailureCode.DROPPED]
    r = run(plan, 1, arrived=None)
    assert lp._refind_dropped(r, plan[1], rule) is False          # the spot's room (hall) has no floor entry
    ENV["floor@hall"] = {"aliases": ["floor"]}
    try:
        r = run(plan, 1, arrived=None)
        r.places = Places(ENV)
        assert lp._refind_dropped(r, plan[1], rule) is True
        assert r.steps[1] == {"action": "MoveTo", "object": "floor@hall"}
    finally:
        del ENV["floor@hall"]


def test_a_miss_where_the_grasp_just_failed_is_looked_at_again(make_assist, env):
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
    ds, dt = "desk@study room", "dining table@kitchen"
    robot = FakeRobot(env, {"apple": ds}, faults=[{"skill": "pick", "kind": "slip", "obj": "apple", "nth": 1},
                                                  {"skill": "find", "kind": "fail", "obj": "apple", "nth": 2,
                                                  "msg": "apple not found"}])
    llm = FakeLLM({"status": "clear", "reason": "", "question": "",
                   "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
    plan = steps(f"MoveTo {ds}", "Find apple", "Pick apple", f"MoveTo {dt}", f"Place {dt}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan), max_replans=0, ask_help=False),
                   "Bring the apple from the desk to the dining table.")
    assert [e.get("kind") for e in evs if e["event"] == "recover"].count("relook") == 1
    assert done(evs)["status"] == "success"


def test_a_pick_the_skill_reported_and_two_of_three_readings_confirm_stands(make_assist, env):
    """The check after the pick read an empty gripper (a noisy reading); the next readings say it holds the
    apple: the pick stands, no 'please take it from me' and no second pick."""
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
    ds, dt = "desk@study room", "dining table@kitchen"
    robot = FakeRobot(env, {"apple": ds})
    reads = {"n": 0}
    real = robot._exec

    def exec_(skill, o, params):
        if skill == "grasp_succeed":
            reads["n"] += 1
            return {"isdone": reads["n"] != 1}          # the first reading (the pick's check) is wrong
        return real(skill, o, params)

    robot._exec = exec_
    llm = FakeLLM({"status": "clear", "reason": "", "question": "",
                   "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
    plan = steps(f"MoveTo {ds}", "Find apple", "Pick apple", f"MoveTo {dt}", f"Place {dt}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan), max_replans=0, ask_help=False),
                   "Bring the apple from the desk to the dining table.")
    assert any("2 of 3" in str(e.get("msg")) for e in evs if e["event"] == "recover")
    assert [t[0] for t in robot.trace].count("pick") == 1


def test_a_lone_full_reading_after_an_empty_check_is_not_a_pick_and_not_an_abort(make_assist, env):
    """Check empty, then empty, then full: two of three say empty. The pick is retried; the run does not stop with
    'please take it from me' on one noisy reading."""
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
    ds, dt = "desk@study room", "dining table@kitchen"
    robot = FakeRobot(env, {"apple": ds}, faults=[{"skill": "pick", "kind": "slip", "obj": "apple", "nth": 1}])
    seq = iter([False, False, True])
    real = robot._exec

    def exec_(skill, o, params):
        if skill == "grasp_succeed":
            try:
                return {"isdone": next(seq)}
            except StopIteration:
                return real(skill, o, params)
        return real(skill, o, params)

    robot._exec = exec_
    llm = FakeLLM({"status": "clear", "reason": "", "question": "",
                   "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
    plan = steps(f"MoveTo {ds}", "Find apple", "Pick apple", f"MoveTo {dt}", f"Place {dt}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan), max_replans=0, ask_help=False),
                   "Bring the apple from the desk to the dining table.")
    assert "take it from me" not in str(done(evs).get("msg"))
    assert [t[0] for t in robot.trace].count("pick") == 2


def test_the_goal_check_looks_twice_where_the_robot_put_the_object(make_assist, env):
    """One camera miss at the final look is not 'the goal does not hold' when a verified release put the object
    there: the check looks again. A second miss still fails the goal."""
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
    ds, dt = "desk@study room", "dining table@kitchen"
    llm = FakeLLM({"status": "clear", "reason": "", "question": "",
                   "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
    plan = steps(f"MoveTo {ds}", "Find apple", "Pick apple", f"MoveTo {dt}", f"Place {dt}")
    for nth, want in (([2], "success"), ([2, 3], "failed")):          # the 2nd Find is the final look
        robot = FakeRobot(env, {"apple": ds}, faults=[{"skill": "find", "kind": "fail", "obj": "apple", "nth": nth,
                                                       "msg": "apple not found"}])
        evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan), max_replans=0, ask_help=False),
                       "Bring the apple from the desk to the dining table.")
        assert done(evs)["status"] == want, (nth, done(evs))


def test_a_release_the_skill_reported_is_not_failed_by_one_noisy_full_reading(make_assist, env):
    """Place succeeded (the gripper is empty); the check right after it reads 'full' once. Two more readings read
    empty, so the release stands: no replan, no second Place."""
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
    ds, dt = "desk@study room", "dining table@kitchen"
    robot = FakeRobot(env, {"apple": ds})
    state = {"wrong": 1}
    real = robot._exec

    def exec_(skill, o, params):
        out = real(skill, o, params)
        if skill == "grasp_succeed" and robot.holding is None and robot.obj.get("apple") == dt and state["wrong"]:
            state["wrong"] -= 1
            return {"isdone": True}                      # the one wrong reading: the check after the release
        return out

    robot._exec = exec_
    llm = FakeLLM({"status": "clear", "reason": "", "question": "",
                   "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
    plan = steps(f"MoveTo {ds}", "Find apple", "Pick apple", f"MoveTo {dt}", f"Place {dt}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan), max_replans=0, ask_help=False),
                   "Bring the apple from the desk to the dining table.")
    assert done(evs)["status"] == "success"
    assert [t[0] for t in robot.trace].count("place") == 1


def test_a_move_that_ends_away_from_its_target_has_its_own_retry(make_assist, env):
    """Blocked twice (retry, then the person is asked and says yes), then a move that reports success but stops
    a metre away (the pose check): that is another failure, retried once, and the run gets there."""
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
    ds, dt = "desk@study room", "dining table@kitchen"
    robot = FakeRobot(env, {"apple": ds}, answers=["yes"], faults=[
        {"skill": "move", "obj": dt, "nth": [1, 2], "kind": "fail", "msg": "path blocked"},
        {"skill": "move", "obj": dt, "nth": 3, "kind": "wrong_pose"}])
    llm = FakeLLM({"status": "clear", "reason": "", "question": "",
                   "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
    plan = steps(f"MoveTo {ds}", "Find apple", "Pick apple", f"MoveTo {dt}", f"Place {dt}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(plan), max_replans=0), "Bring the apple from the desk to the dining table.")
    assert done(evs)["status"] == "success", done(evs)
    assert [t[0] for t in robot.trace].count("move") == 5
