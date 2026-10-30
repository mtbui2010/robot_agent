"""D18: a stop during a manifest interruptible=false skill waits for it to end (run_control)."""


import threading
import time

import pytest

from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager


@pytest.fixture
def mgr():
    reset_manager()
    run_control._CANCEL.clear()
    yield run_control.manager()
    reset_manager()
    run_control._CANCEL.clear()


class Node:
    """The CustomNode surface run_control touches."""

    def __init__(self):
        self.cancels = []

    def cancel_all(self):
        self.cancels.append(time.time())
        return {"cancelled": ["arm"]}

    def clear_cancel(self):
        pass


@pytest.fixture
def node(monkeypatch):
    n = Node()
    monkeypatch.setattr(run_control, "_node", lambda node=None: n)
    return n


def _bound_run(mgr, goal="bring the cup", role="owner"):
    run = mgr.start(goal, user_id="alice", role=role).run
    run_control.begin_run()                 # this thread is the run's executive
    return run


# ── D18: stop during a non-interruptible skill ──────────────────────────────
def test_stop_waits_for_a_non_interruptible_skill(mgr, node):
    run = _bound_run(mgr)
    evs = []
    run.subscribe(evs.append)
    inside = threading.Event()

    def pick():
        with run_control.uninterruptible("pick"):
            inside.set()
            time.sleep(0.2)
    t = threading.Thread(target=lambda: (run_control.manager().bind_thread(run), pick()))
    t.start()
    assert inside.wait(1.0)
    t_stop = time.time()
    assert mgr.inject(run.run_id, "stop").action == "cancelled"
    assert run.cancel_requested and node.cancels == []           # the run stops now, the arm does not
    t.join(2.0)
    assert len(node.cancels) == 1 and node.cancels[0] - t_stop >= 0.1
    assert any(e["event"] == "run_stop_deferred" and e["skill"] == "pick" for e in evs)


def test_stop_cuts_an_interruptible_skill_at_once(mgr, node):
    run = _bound_run(mgr)
    with run_control.uninterruptible("move", interruptible=True):
        mgr.cancel(run.run_id)
        assert len(node.cancels) == 1
    assert len(node.cancels) == 1


def test_deferred_stop_is_bounded(mgr, node, monkeypatch):
    monkeypatch.setenv("ROBOT_AGENT_UNINTERRUPTIBLE_MAX_S", "0.05")
    run = _bound_run(mgr)
    with run_control.uninterruptible("pick"):
        mgr.cancel(run.run_id)
        time.sleep(0.2)                         # the skill hangs: the stop cuts it after max_s
        assert len(node.cancels) == 1
    assert len(node.cancels) == 1               # and is not sent twice


def test_dashboard_stop_stays_immediate(mgr, node):
    run = _bound_run(mgr)
    with run_control.uninterruptible("pick"):
        run_control.request_cancel()
        assert len(node.cancels) == 1 and run.cancel_requested


def test_run_skill_marks_the_manifest_flag(mgr, node):
    from robot_agent.core.planning.robot import run_skill
    run = _bound_run(mgr)
    seen = []

    class SR:
        def execute(self, skill, params, node=None, log_fn=None):
            seen.append(run.critical)
            return {"isdone": True}
    run_skill(SR(), "pick", {}, interruptible=False)
    run_skill(SR(), "move", {})
    assert seen == ["pick", None] and run.critical is None
