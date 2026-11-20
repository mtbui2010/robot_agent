"""Option ``look_then_ask`` (default ON), through the real AssistLoop: a goal with no source is looked for where the robot stands first;
seen = no question; not seen = ONE question "I don't see the mug here. Where is it?"; a place in the answer is the source; "I don't know"
leaves the search to the memory and the ranking and the question is not asked again; a stated source, a remembered object, or the option
off asks nothing here."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

DS, DT = "desk@study room", "dining table@kitchen"
TASK = "Put the mug on the dining table."


def understood(frm=""):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "on", "object": "mug", "from": frm, "to": "dining table"}]}


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


def go(make_assist, env, answers=(), frm="", task=TASK, house=None, start="entrance", **opts):
    robot = FakeRobot(env, {"mug": DS} if house is None else house, answers=list(answers), start=start)
    loop = make_assist(robot, FakeLLM(default=understood(frm)), ScriptedPlanner(PLAN), look_then_ask="1", **opts)
    return robot, run_task(loop, task, max_events=3000)


def questions(events):
    return [str(e.get("question")) for e in events if e.get("event") == "clarify"]


def test_not_seen_here_asks_once_and_uses_the_place_in_the_answer(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["on the desk in the study room"])
    qs = questions(events)
    assert qs and "I don't see the mug here. Where is it?" in qs[0]
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_i_do_not_know_is_not_asked_again_after_misses(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I do not know"])
    assert sum("Where is it?" in q or "Where is the mug" in q for q in questions(events)) == 1


def test_seen_here_asks_nothing(run, make_assist, env):
    _robot, events = go(make_assist, env, house={"mug": DS}, start=DS)
    assert not any("Where is" in q for q in questions(events))


def test_a_stated_source_is_not_asked_about(run, make_assist, env):
    _robot, events = go(make_assist, env, frm="desk", task="Put the mug from the desk on the dining table.")
    assert not any("I don't see" in q for q in questions(events))


def test_off_the_robot_does_not_look_first_or_ask(run, make_assist, env):
    robot = FakeRobot(env, {"mug": DS}, answers=[], start="entrance")
    loop = make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(PLAN), look_then_ask="0")
    events = run_task(loop, TASK, max_events=3000)
    assert not any("I don't see" in q for q in questions(events))


def test_ask_source_does_not_ask_the_same_object_again(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I do not know", "I do not know"], ask_source="1")
    assert sum("Where is the mug" in q or "I don't see the mug" in q for q in questions(events)) == 1
