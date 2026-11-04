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
