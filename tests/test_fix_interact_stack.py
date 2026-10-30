"""D15: the task stack, RunManager.suspend / resume_task, on_resume hooks."""

import pytest

from robot_agent.core import run_control
from robot_agent.interact.run_manager import TaskFrame, TaskStack, reset_manager


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


# ── D15: TaskStack, suspend / resume_task, on_resume ────────────────────────
def test_task_stack_lifo_per_run():
    st = TaskStack()
    st.push("r", TaskFrame("a", "t1", [1]))
    st.push("r", TaskFrame("a", "t2", [2]))
    assert st.peek("r").task == "t2"
    assert st.pop("r", "other") is None                  # not that run's frame
    assert st.pop("r", "a").task == "t2" and st.pop("r").task == "t1" and st.pop("r") is None
    st.push("r", TaskFrame("b", "t3", []))
    assert st.drop_run("r", "b") == 1 and st.frames("r") == []


def test_suspend_resume_task_runs_on_resume(mgr):
    run = _bound_run(mgr)
    seen, evs = [], []
    mgr.on_resume.append(seen.append)
    run.subscribe(evs.append)
    f = run_control.suspend_task("bring the cup", [{"type": "on"}], [], {"arrived": "desk"}, reason="new first")
    assert mgr.task_stack.peek(run.robot_id) is f and f.run_id == run.run_id
    back = run_control.resume_task()
    assert back is f and seen == [run] and run_control.resume_task() is None
    assert [e["event"] for e in evs] == ["task_suspended", "task_resumed"]


def test_resume_after_pause_runs_on_resume(mgr):
    run = _bound_run(mgr)
    seen = []
    mgr.on_resume.append(seen.append)
    mgr.pause(run.run_id)
    assert mgr.resume(run.run_id).action == "resumed" and seen == [run]
    mgr.resume(run.run_id)                    # idempotent: not a second resume
    assert seen == [run]


def test_finished_run_drops_its_frames(mgr):
    run = _bound_run(mgr)
    run_control.suspend_task("t", [], [], {})
    mgr.finish(run.run_id)
    assert mgr.task_stack.frames(run.robot_id) == []


def test_outranked_by(mgr):
    run = mgr.start("t", user_id="m", role="member").run
    assert run.outranked_by("owner") and not run.outranked_by("member") and not run.outranked_by("guest")
