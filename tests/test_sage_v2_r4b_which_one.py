"""R4b item 3 (duplicate objects): a Find that sees more than one object fitting the words reports ``candidates``;
the robot narrows them by the person's words or asks which one, then looks for / picks up that description.
Synthetic objects and sentences only."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import which_one as W

DS, DT = "desk@study room", "dining table@kitchen"


def understood(obj, said=None):
    g = {"type": "on", "object": obj, "from": "", "to": "dining table"}
    if said:
        g["said"] = said
    return {"status": "clear", "reason": "", "reason_type": "", "question": "", "goals": [g]}


class TwoBooks(FakeRobot):
    """Two books on the desk. A Find / Pick of "book" sees both (the first is found); a description picks one."""

    def __init__(self, env, **k):
        super().__init__(env, {"red book": DS, "blue book": DS}, **k)
        self.queries = []

    def _exec(self, skill, o, params):
        if skill in ("find", "pick"):
            self.queries.append((skill, o))
        if skill == "find" and self.at == DS and o.strip().lower() == "book":
            self.found = "red book"
            return {"isdone": True, "found": "red book", "candidates": ["red book", "blue book"],
                    "ins": {o: {"pose_3d": [0.6, 0.0, 0.8]}}}
        if skill == "pick" and o.strip().lower() == "book":
            return {"isdone": False, "msg": "which one?", "candidates": ["red book", "blue book"]}
        return super()._exec(skill, o, params)


PLAN = steps(f"MoveTo {DS}", "Find book", "Pick book", f"MoveTo {DT}", f"Place {DT}")


def test_pure_helpers():
    assert W.candidate_names(["red mug", "Red  mug", "", None, "blue mug"]) == ["red mug", "blue mug"]
    assert W.candidate_names("red mug") == [] and W.candidate_names(None) == []
    c = ["red mug", "blue mug"]
    assert W.narrow(c, "red mug") == ["red mug"] and W.narrow(c, "mug") == c and W.narrow(c, "green mug") == c
    assert W.choose("the blue one, please", c, "mug") == 1
    assert W.choose("I mean the red mug", c, "mug") == 0
    assert W.choose("the second one", c, "mug") is None and W.choose("mug", c, "mug") is None
    assert W.choose("red and blue", c, "mug") is None            # two candidates named: no decision by words


def test_asks_which_one_and_picks_it(make_assist, env):
    robot = TwoBooks(env, answers=["The blue one."])
    llm = FakeLLM(understood("book"), default="{}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(PLAN)), "Put the book on the dining table.", max_events=2000)
    assert done(evs)["status"] == "success", done(evs)
    qs = [q for q in robot.asked if "more than one" in q]
    assert len(qs) == 1 and "red book" in qs[0] and "blue book" in qs[0] and "_" not in qs[0]
    assert robot.obj["blue book"] == DT and robot.obj["red book"] == DS        # the chosen one, not the first found
    assert ("pick", "blue book") in robot.queries and ("find", "blue book") in robot.queries   # looked again
    assert [e for e in evs if e["event"] == "recover" and e.get("kind") == "which_one"]


def test_words_of_the_person_narrow_without_a_question(make_assist, env):
    robot = TwoBooks(env)
    llm = FakeLLM(understood("book", said="blue book"), default="{}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(PLAN)), "Put the blue book on the dining table.", max_events=2000)
    assert done(evs)["status"] == "success", done(evs)
    assert not [q for q in robot.asked if "more than one" in q]
    assert robot.obj["blue book"] == DT


def test_answer_in_other_words_is_read_by_the_model(make_assist, env):
    robot = TwoBooks(env, answers=["The second one."])
    llm = FakeLLM(understood("book"), ("Which choice", {"choice": 2}), default="{}")
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(PLAN)), "Put the book on the dining table.", max_events=2000)
    assert done(evs)["status"] == "success", done(evs)
    assert robot.obj["blue book"] == DT


def test_an_answer_that_does_not_choose_is_not_asked_again(make_assist, env):
    robot = TwoBooks(env, answers=["Whatever."])
    llm = FakeLLM(understood("book"), ("Which choice", {"choice": 0}), default="{}")
    run_task(make_assist(robot, llm, ScriptedPlanner(PLAN)), "Put the book on the dining table.", max_events=2000)
    assert len([q for q in robot.asked if "more than one" in q]) == 1


def test_no_candidates_no_question(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    llm = FakeLLM({"status": "clear", "reason": "", "reason_type": "", "question": "",
                   "goals": [{"type": "on", "object": "apple", "from": "", "to": "dining table"}]}, default="{}")
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner), "Put the apple on the dining table.", max_events=2000)
    assert done(evs)["status"] == "success" and not robot.asked
