"""Fix stream `understand`, D2: a place's own room decides ('shelf in the restroom'), not every room the task
mentions elsewhere."""

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task

SR, DR, DS, DT = "shelf@restroom", "drawer@kitchen", "desk@study room", "dining table@kitchen"
HOUSE = {"mug": DS, "orange": DT}


def U(*goals, status="clear"):
    return {"status": status, "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def test_room_said_with_the_place_wins_over_other_rooms(places):
    task = "Put the mug on the shelf in the restroom and the orange in the kitchen drawer."
    assert places.candidates("shelf", task) == [SR]
    assert places.candidates("shelf", "Put the mug on the kitchen shelf, then go to the restroom.") == ["shelf@kitchen"]


def test_shelf_in_the_restroom_is_not_asked_again(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    task = "Put the mug on the shelf in the restroom and the orange in the kitchen drawer."
    llm = FakeLLM(default=U(("on", "mug", "", "shelf in the restroom"), ("in", "orange", "", "kitchen drawer")))
    loop = make_assist(robot, llm, ScriptedPlanner([]))
    loop.cfg["goal_check"] = False
    evs = run_task(loop, task, plan_only=True)
    und = next(e for e in evs if e["event"] == "understand")
    assert und["question"] is None and [g["to"] for g in und["goals"]] == [SR, DR] and robot.asked == []


