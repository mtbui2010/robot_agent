"""D16: the robot's questions are on the run: a typed answer from any client answers them."""

import threading
import time

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact import dialog
from robot_agent.interact.run_manager import reset_manager


@pytest.fixture
def mgr():
    reset_manager()
    run_control._CANCEL.clear()
    yield run_control.manager()
    reset_manager()
    run_control._CANCEL.clear()


def _bound_run(mgr, goal="bring the cup", role="owner"):
    run = mgr.start(goal, user_id="alice", role=role).run
    run_control.begin_run()                 # this thread is the run's executive
    return run


# ── D16: the robot's question is on the run ─────────────────────────────────
def test_typed_answer_wins_over_the_robots_listen(mgr):
    run = _bound_run(mgr)
    released = []

    def io(q):                               # the robot's mic hears nothing for a while
        time.sleep(0.3)
        return ""
    threading.Timer(0.05, lambda: mgr.inject(run.run_id, "the one in the restroom")).start()
    t0 = time.time()
    ans = dialog.ask(run, "Which shelf?", io, release=lambda: released.append(1), join_s=0)
    assert ans.text == "the one in the restroom" and ans.source == "typed"
    assert time.time() - t0 < 0.25 and released == [1] and run.awaiting is None


def test_heard_answer_and_the_inject_route(mgr):
    run = _bound_run(mgr)
    routed = []
    ans = dialog.ask(run, "Which shelf?", lambda q: routed.append(run.awaiting) or "the kitchen one")
    assert ans.text == "the kitchen one" and ans.source == "robot" and routed == ["Which shelf?"]
    assert run.awaiting is None
    assert mgr.inject(run.run_id, "the restroom").action == "queued"     # no question any more


def test_stop_while_asking_returns_at_once(mgr):
    run = _bound_run(mgr)
    threading.Timer(0.05, lambda: mgr.inject(run.run_id, "stop")).start()
    t0 = time.time()
    ans, n = run_control.ask_person("Which shelf?", lambda q: time.sleep(1.0) or "x", silence_retries=2)
    assert not ans and n == 1 and time.time() - t0 < 0.3


def test_ask_heard_asks_again_on_silence(mgr):
    asked = []
    ans, n = dialog.ask_heard(None, "Where?", lambda q: asked.append(q) or ("desk" if len(asked) == 2 else ""))
    assert ans.text == "desk" and n == 2 and asked[1].startswith("Sorry")


# ── in AssistLoop ──
DS, DT, CT, SF = "desk@study room", "dining table@kitchen", "counter@kitchen", "sofa@living room"
HOUSE = {"apple": DS, "mug": DS, "banana": DT}
TASK = "Bring the apple from the desk to the dining table."
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


def understood(*goals, status="clear"):
    return {"status": status, "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


APPLE = understood(("on", "apple", "desk", "dining table"))


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    mgr = run_control.manager()
    r = mgr.start(TASK, user_id="alice", role="member").run
    run_control.begin_run()                         # this test thread is the run's executive
    yield r
    reset_manager()
    run_control._CANCEL.clear()


def test_typed_answer_is_not_read_back(make_assist, env, run):
    class Typed(FakeRobot):
        def execute(self, skill, params, node=None, log_fn=None):
            if skill == "ask" and not self.asked:
                self.asked.append(params.get("inputs"))
                run_control.manager().inject(run_control.current_run().run_id, "the dining table")
                time.sleep(0.05)
                return {"isdone": False, "text": ""}
            return super().execute(skill, params, node, log_fn)
    robot = Typed(env, HOUSE)
    llm = FakeLLM(understood(("on", "mug", "", "table")), understood(("on", "mug", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner), "Put the mug on the table.")
    assert done(evs)["status"] == "success" and robot.obj["mug"] == DT
    assert len(robot.asked) == 1 and not any("Is that right" in q for q in robot.asked)
