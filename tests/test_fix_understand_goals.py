"""Fix stream `understand`, D29: the understood goals are read back against the person's words; a dropped /
changed goal is sampled again and, without agreement, asked about instead of planned."""

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DS, DT, CT, DR = "desk@study room", "dining table@kitchen", "counter@kitchen", "drawer@kitchen"
SR, SD = "shelf@restroom", "side table@living room"
HOUSE = {"apple": DS, "mug": DS, "orange": DT, "banana": DT, "remote control": "sofa@living room"}


def U(*goals, status="clear"):
    return {"status": status, "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


# ── D29: coverage ──
@pytest.mark.parametrize("task, goals, want", [
    ("Put the mug on the table.", [G("on", "mug", "", DS)], "did not say the desk"),
    ("If the kitchen drawer is open, put the orange in it; otherwise put the orange on the counter.",
     [G("in", "orange", "", DR)], "counter"),
    ("Take the spoon out of the kitchen drawer, put it on the dining table, then close the drawer.",
     [G("on", "spoon", DR, DT)], "close"),
    ("Move the plate and the bowl from the dining table to the counter.", [G("on", "plate", DT, CT)], "bowl"),
    ("Put the remote control on the side table and the apple on the desk.", [G("on", "apple", "", DS)], "side table"),
])
def test_coverage_finds_a_dropped_or_changed_goal(places, task, goals, want):
    from robot_agent.core.planning.understand import coverage
    issues = coverage(goals, task, places, english=task)
    assert any(want in i for i in issues), issues


@pytest.mark.parametrize("task, goals", [
    ("Bring the apple from the desk to the dining table.", [G("on", "apple", DS, DT)]),
    ("If the kitchen drawer is open, put the orange in it; otherwise put the orange on the counter.",
     [G("on", "orange", "", CT)]),
    ("Put the remote control on the side table, the tennis ball on the stool... no wait, on the sofa.",
     [G("on", "remote control", "", SD), G("on", "tennis ball", "", "sofa@living room")]),
    ("Put the apple on the dining table, but don't touch the mug.", [G("on", "apple", "", DT)]),
    ("Take the spoon out of the open kitchen drawer and put it on the counter.", [G("on", "spoon", DR, CT)]),
    ("Bring all the cups to the dining table.", [G("on", "red cup", "", DT), G("on", "blue cup", "", DT)]),
    ("책상 위에 있는 머그컵을 식탁으로 옮겨 줘. Move the mug on the desk to the dining table.", [G("on", "mug", DS, DT)]),
])
def test_coverage_accepts_a_faithful_reading(places, task, goals):
    from robot_agent.core.planning.understand import coverage
    assert coverage(goals, task, places, english=task) == []


def test_consensus_needs_agreeing_issue_free_samples():
    from robot_agent.core.planning.understand import consensus
    a, b = [G("on", "mug", "", DT)], [G("on", "mug", "", CT)]
    assert consensus([(a, []), (a, [])]) == a
    assert consensus([(a, []), (b, [])]) is None
    assert consensus([(a, ["x"]), (a, ["x"])]) is None


# ── D29: the loop ──
TWO = "Put the apple on the dining table and the mug on the counter."
PLAN2 = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}",
              f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {CT}", f"Place {CT}")


def test_a_dropped_goal_is_resampled_and_the_agreed_reading_taken(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    full = U(("on", "apple", "", "dining table"), ("on", "mug", "", "counter"))
    llm = FakeLLM(U(("on", "apple", "", "dining table")), full, full)
    evs = run_task(make_assist(robot, llm, ScriptedPlanner(PLAN2)), TWO)
    chk = next(e for e in evs if e["event"] == "understand_check")
    assert chk["agreed"] and any("counter" in i for i in chk["issues"])
    assert done(evs)["status"] == "success" and robot.asked == [] and robot.obj["mug"] == CT


def test_a_dropped_goal_without_agreement_is_asked_not_planned(make_assist, env):
    robot = FakeRobot(env, HOUSE)                          # nobody answers
    planner = ScriptedPlanner(PLAN2)
    llm = FakeLLM(default=U(("on", "apple", "", "dining table")))     # deterministic: the same reading each time
    d = done(run_task(make_assist(robot, llm, planner), TWO))
    assert d["status"] == "failed" and planner.calls == [] and robot.asked
    assert "counter" in robot.asked[0] and "is that right" not in robot.asked[0].lower()   # an open question
    assert len([c for c in llm.calls if c[1]]) == 3          # 1 understand + understand_samples (2)


def test_a_changed_place_is_asked_and_the_answer_used(make_assist, env):
    """'the table' read as the desk (a place the person never said): asked which table, not planned."""
    robot = FakeRobot(env, HOUSE, answers=["the dining table"])
    plan = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
    desk = U(("on", "mug", "", "desk"))
    llm = FakeLLM(desk, desk, desk, default=U(("on", "mug", "", "dining table")))
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner(plan)), "Put the mug on the table."))
    assert robot.asked[0].startswith("Which table do you mean") and "dining table" in robot.asked[0]
    assert d["status"] == "success" and robot.obj["mug"] == DT


def test_after_one_question_the_read_back_names_the_doubt(make_assist, env):
    robot = FakeRobot(env, HOUSE, answers=["just do it"])
    llm = FakeLLM(default=U(("on", "apple", "", "dining table")))
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner(plan)), TWO))
    assert "counter" in robot.asked[0]
    assert robot.asked[1].startswith("I am not sure I understood") and robot.asked[1].endswith("Is that right?")
    assert d["status"] == "success"                       # the person confirmed the reading


def test_goal_check_can_be_turned_off(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM(default=U(("on", "apple", "", "dining table")))
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner(plan), goal_check=False), TWO))
    assert d["status"] == "success" and robot.asked == []
