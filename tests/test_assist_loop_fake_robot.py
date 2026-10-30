"""The real AssistLoop.run_blocking against the symbolic FakeRobot, with a scripted
LLM (task understanding) and a scripted planner (no embodiplan, no ollama)."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"
HOUSE = {"apple": DS, "mug": DS, "banana": DT, "orange": DT, "sponge": CT, "spoon": DR + "/in",
         "tennis ball": "stool@bedroom"}


def understood(*goals, status="clear", question="", reason=""):
    return {"status": status, "reason": reason, "question": question,
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def kinds(evs):
    return [e["event"] for e in evs]


def test_simple_task_succeeds(make_assist, env, tmp_path):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner)
    evs = run_task(loop, "Bring the apple from the desk to the dining table.")
    d = done(evs)
    assert d["status"] == "success", d
    assert robot.obj["apple"] == DT and robot.holding is None
    assert [(s, o) for s, o, ok, _ in robot.trace] == [("move", DS), ("find", "apple"), ("pick", "apple"),
                                                       ("move", DT), ("place", DT)]
    assert kinds(evs)[:4] == ["task_start", "status", "understand", "status"]
    gate = next(e for e in evs if e["event"] == "gate")
    assert gate["problems"] == []
    # the understand prompt lists the ENV places and asks for JSON
    prompt, fmt = llm.calls[0]
    assert "- dining table@kitchen" in prompt and fmt["required"] == ["status", "reason", "question", "goals"]
    # one JSONL run log with a final snapshot
    logs = list((tmp_path / "runs").glob("*.jsonl"))
    assert len(logs) == 1
    rows = [json.loads(x) for x in logs[0].read_text().splitlines()]
    assert rows[-1]["kind"] == "snapshot" and rows[-1]["record"]["status"] == "success"
    assert [s["status"] for s in rows[-1]["record"]["steps"]] == ["success"] * 5


def test_find_miss_goes_to_planner_replan(make_assist, env):
    house = dict(HOUSE, apple=CT)                       # not where the user said
    robot = FakeRobot(env, house)
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(
        steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"),
        replans=[steps(f"MoveTo {CT}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")])
    evs = run_task(make_assist(robot, llm, planner), "Bring the apple from the desk to the dining table.")
    assert done(evs)["status"] == "success"
    assert robot.obj["apple"] == DT
    rp = [c for c in planner.calls if c[0] == "replan"]
    assert len(rp) == 1 and rp[0][1]["action"] == "Find" and "apple not found" in rp[0][2]
    assert "replan" in kinds(evs)


def test_unsafe_task_is_refused_without_moving(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM(understood(status="unsafe", reason="Throwing things can hurt someone."))
    planner = ScriptedPlanner([])
    d = done(run_task(make_assist(robot, llm, planner), "Throw the apple at the window."))
    assert d["status"] == "refused" and d["msg"] == "Throwing things can hurt someone."
    assert robot.trace == [] and planner.calls == []


def test_ambiguous_place_asks_then_reads_back(make_assist, env):
    robot = FakeRobot(env, HOUSE, answers=["the dining table"])
    llm = FakeLLM(understood(("on", "mug", "", "table")),
                  understood(("on", "mug", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner), "Put the mug on the table.")
    assert done(evs)["status"] == "success"
    assert robot.asked[0] == ("Which table do you mean: side table in the living room or dining table in the "
                              "kitchen?")
    assert robot.asked[1].endswith("Is that right?")    # an answer is read back (confirm_answers)
    assert robot.obj["mug"] == DT


def test_dropped_on_the_way_stops_and_says_why(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "grasp_succeed", "kind": "drop", "nth": 2}])
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner), "Bring the apple from the desk to the dining table."))
    assert d["status"] == "failed"
    assert d["msg"].startswith("I dropped the apple on the way to dining table in the kitchen.")
    assert robot.obj["apple"] == "floor"


def test_skill_bug_is_not_retried(make_assist, env):
    robot = FakeRobot(env, HOUSE, faults=[{"skill": "pick", "kind": "swerr", "nth": "all"}])
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner), "Bring the apple from the desk to the dining table."))
    assert d["status"] == "failed" and d["msg"].startswith("internal error in the pick skill (not retried)")
    assert [c for c in planner.calls if c[0] == "replan"] == []


def test_drawer_task_opens_then_puts_in(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM(understood(("in", "orange", "", "kitchen drawer")))
    planner = ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DT}", "Find orange", "Pick orange",
                                    f"MoveTo {DR}", f"PutIn {DR}"))
    d = done(run_task(make_assist(robot, llm, planner), "Put the orange in the kitchen drawer."))
    assert d["status"] == "success"
    assert robot.obj["orange"] == DR + "/in" and DR in robot.opened


def test_plan_only_does_not_move(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner), "Bring the apple to the dining table.", plan_only=True)
    assert done(evs)["status"] == "planned" and robot.trace == []
    plan = next(e for e in evs if e["event"] == "plan")
    assert plan["plan_text"].splitlines() == [f"move::{DS}", "find::apple", "pick::apple", f"move::{DT}",
                                              f"place::{DT}"]


def test_cancel_during_a_step_aborts(make_assist, env):
    from robot_agent.core.run_control import request_cancel

    class CancelAfterMove(FakeRobot):
        def _exec(self, skill, o, params):
            r = super()._exec(skill, o, params)
            if skill == "move":
                request_cancel()
            return r

    robot = CancelAfterMove(env, HOUSE)
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner), "Bring the apple from the desk to the dining table."))
    assert d["status"] == "aborted"
    assert [s for s, *_ in robot.trace] == ["move"]


@pytest.fixture(autouse=True)
def _clear_cancel():
    yield
    from robot_agent.core.run_control import begin_run
    begin_run()
