"""``AssistLoop.lab_log`` (grounding chain, re-ask, inventory): what the opt-in lab options did in a run (flag name plus a few facts), read by a harness after
the run. Through the real AssistLoop.run_blocking against the symbolic FakeRobot and a scripted LLM; synthetic requests
written for these tests. Off options leave the log empty; the event stream is not touched."""
import json
import time

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps

DT, DS = "dining table@kitchen", "desk@study room"
ENV = {
    "sofa@living room": {"aliases": ["sofa"], "loc": {"x": 1.0, "y": 1.0}},
    "table@kitchen": {"aliases": ["table"], "loc": {"x": 3.0, "y": 1.0}},
    "fridge@kitchen": {"aliases": ["fridge"], "openable": True, "loc": {"x": 4.0, "y": 1.0}},
    "shelf@hall": {"aliases": ["shelf"], "loc": {"x": 6.0, "y": 1.0}},
    "ben@kitchen": {"person": True, "aliases": ["ben"], "loc": {"x": 3.2, "y": 1.0}},
}


def U(*goals, status="clear", question=""):
    return {"status": status, "reason": "", "question": question,
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def flags(loop):
    return [e["flag"] for e in loop.lab_log]


def run_understand(make_assist, tmp_path, task, llm, memory=None, answers=(), **opts):
    if memory is not None:
        (tmp_path / "object_memory.json").write_text(json.dumps({"objects": memory}))
    robot = FakeRobot(ENV, {"cup": "table@kitchen"}, answers=answers)
    loop = make_assist(robot, llm, ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup")), confirm_task=False, **opts)
    list(loop.run_blocking(task))
    return loop


@pytest.fixture
def env():
    return ENV


def G(t, o="", f="", to=""):
    return {"type": t, "object": o, "from": f, "to": to}


def test_go_steps_and_kind_anchor_log_what_they_dropped(make_assist, tmp_path):
    loop = run_understand(make_assist, tmp_path, "Tell Ben that dinner is ready.",
                          FakeLLM(U(("say", "dinner is ready", "", "ben@kitchen"), ("go", "", "", "ben@kitchen"))),
                          go_steps="1")
    assert flags(loop) == ["go_steps"]
    loop = run_understand(make_assist, tmp_path, "Take the cup from the table to the shelf.",
                          FakeLLM(U(("open", "fridge@kitchen", "", "fridge@kitchen"), ("on", "cup", "table@kitchen", "shelf@hall"))),
                          kind_anchor="1")
    assert flags(loop) == ["kind_anchor"]


def test_reask_abstain_logs_whether_the_new_reading_was_taken(make_assist, tmp_path):
    llm = FakeLLM((r"finds objects itself", U(("on", "cup", "", "shelf@hall"))), U(status="ambiguous", question="Where is the cup?"))
    loop = run_understand(make_assist, tmp_path, "Put the cup on the shelf.", llm, reask_abstain="1")
    assert loop.lab_log == [{"flag": "reask_abstain", "taken": True}]


def test_inventory_ask_logs_the_question_about_which(make_assist, tmp_path):
    mem = {n: {"place": DS, "ts": time.time(), "how": "seen"} for n in ("blue mug", "red mug")}
    loop = run_understand(make_assist, tmp_path, "Put the mug on the table.", FakeLLM(U(("on", "mug", "", "table@kitchen"))),
                          memory=mem, answers=["the red mug"], inventory_ask="1")
    assert loop.lab_log[0]["flag"] == "inventory_ask" and loop.lab_log[0]["asked_which"] is True
    loop = run_understand(make_assist, tmp_path, "Put the mug on the table.", FakeLLM(U(("on", "mug", "", "table@kitchen"))),
                          memory=mem, answers=["the red mug"])
    assert loop.lab_log == []
