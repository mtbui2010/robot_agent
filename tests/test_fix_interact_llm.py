"""D19: a stop does not wait for an LLM / planner call (run_control.call_cancellable, AssistLoop)."""

import threading
import time

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import RunCancelled, reset_manager


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


# ── D19: a stop does not wait for an LLM call ───────────────────────────────
def test_call_cancellable_raises_on_stop(mgr):
    run = _bound_run(mgr)
    threading.Timer(0.05, lambda: mgr.cancel(run.run_id)).start()
    t0 = time.time()
    with pytest.raises(RunCancelled):
        run_control.call_cancellable(lambda: time.sleep(1.0) or "late")
    assert time.time() - t0 < 0.2
    assert run_control.is_cancel(RunCancelled("x")) and not run_control.is_cancel(ValueError())


def test_call_cancellable_passes_results_and_errors(mgr):
    _bound_run(mgr)
    assert run_control.call_cancellable(lambda a, b=0: a + b, 1, b=2) == 3
    with pytest.raises(ValueError):
        run_control.call_cancellable(lambda: (_ for _ in ()).throw(ValueError("llm down")))
    reset_manager()
    assert run_control.call_cancellable(lambda: "no run") == "no run"

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


def test_stop_during_the_understand_call(make_assist, env, run):
    import threading

    class Slow(FakeLLM):
        def chat(self, prompt="", format=None, **kw):
            time.sleep(1.0)
            return super().chat(prompt=prompt, format=format, **kw)
    robot = FakeRobot(env, HOUSE)
    threading.Timer(0.1, lambda: run_control.manager().inject(run.run_id, "stop")).start()
    t0 = time.time()
    evs = run_task(make_assist(robot, Slow(APPLE), ScriptedPlanner(PLAN)), TASK)
    assert done(evs)["status"] == "aborted" and robot.trace == []
    assert time.time() - t0 < 0.5
