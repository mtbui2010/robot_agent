"""RunManager + run_control shims: the interrupt scenarios of the proposal (6)."""

import threading
import time

import pytest

from robot_agent.core import run_control
from robot_agent.interact.intent_router import Intent, IntentRouter
from robot_agent.interact.run_manager import Role, RunCancelled, RunState, reset_manager


@pytest.fixture
def mgr():
    m = reset_manager()
    run_control._CANCEL.clear()
    yield run_control.manager()
    reset_manager()
    run_control._CANCEL.clear()


class Executive:
    """A fake plan loop with the same cooperative points as the real ones."""

    def __init__(self, steps=200, dt=0.005):
        self.steps, self.dt = steps, dt
        self.done_steps = 0
        self.status = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        run_control.begin_run()
        for _ in range(self.steps):
            run_control.wait_if_paused()
            if run_control.cancel_requested():
                self.status = 'aborted'
                return
            time.sleep(self.dt)            # a skill
            self.done_steps += 1
        self.status = 'success'

    def start(self):
        self.thread.start()
        return self


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.002)
    return False


def test_stop_mid_run(mgr):
    run = mgr.start('bring the cup', user_id='alice').run
    ex = Executive().start()
    assert _wait(lambda: ex.done_steps >= 3)
    t0 = time.time()
    res = mgr.inject(run.run_id, 'Stop!', user_id='alice')
    assert res.action == 'cancelled' and res.decision.source == 'keyword'
    ex.thread.join(1.0)
    assert ex.status == 'aborted' and time.time() - t0 < 0.5
    assert run.state == RunState.CANCELLING
    mgr.finish(run.run_id, ok=False)
    assert run.state == RunState.DONE and run.outcome == 'aborted'
    assert mgr.active() is None


def test_two_clients_one_run(mgr):
    a = mgr.start('bring the cup', user_id='alice')
    b = mgr.start('bring me water', user_id='bob')
    assert a.started and not b.started and b.decision == 'busy'
    assert b.run is a.run
    res = mgr.inject(a.run.run_id, 'bring me water', user_id='bob')
    assert res.action == 'queued'
    assert [p['text'] for p in a.run.take_pending()] == ['bring me water']
    assert len([r for r in mgr.list() if r.active]) == 1


def test_pause_resume(mgr):
    run = mgr.start('tidy up').run
    ex = Executive().start()
    assert _wait(lambda: ex.done_steps >= 2)
    assert mgr.inject(run.run_id, 'wait').action == 'paused'
    time.sleep(0.03)
    held = ex.done_steps
    time.sleep(0.05)
    assert ex.done_steps <= held + 1            # at most the step in flight finishes
    assert run.state == RunState.PAUSED
    assert mgr.inject(run.run_id, 'continue').action == 'resumed'
    assert _wait(lambda: ex.done_steps > held + 3)
    mgr.cancel(run.run_id)
    ex.thread.join(1.0)
    assert ex.status == 'aborted'


def test_cancel_wakes_paused_run(mgr):
    run = mgr.start('tidy up').run
    ex = Executive().start()
    assert _wait(lambda: ex.done_steps >= 1)
    mgr.pause(run.run_id)
    time.sleep(0.02)
    mgr.cancel(run.run_id)
    ex.thread.join(1.0)
    assert ex.status == 'aborted'


def test_lower_role_cannot_override(mgr):
    run = mgr.start('bring the pills', user_id='alice', role='owner').run
    for text in ('stop', 'pause', 'bring a cookie instead'):
        res = mgr.inject(run.run_id, text, user_id='kid', role='guest')
        assert res.action == 'refused' and not res.ok
        assert 'owner' in res.reason and 'guest' in res.reason
    assert run.state == RunState.RUNNING and not run.cancel_requested
    assert run.pending == []
    refused = [e for e in run.events if e['event'] == 'run_refused']
    assert len(refused) == 3 and all(e['user_id'] == 'kid' for e in refused)
    # STATUS is open to everyone
    st = mgr.inject(run.run_id, 'what are you doing?', user_id='kid', role='guest')
    assert st.action == 'status' and st.status['goal'] == 'bring the pills'
    # same or higher role may
    assert mgr.pause(run.run_id, role='owner', user_id='bob').action == 'paused'


def test_higher_role_overrides_lower(mgr):
    run = mgr.start('play music', user_id='kid', role='guest').run
    assert mgr.inject(run.run_id, 'stop', user_id='mom', role=Role.MEMBER).action == 'cancelled'


def test_inject_while_awaiting_answer(mgr):
    run = mgr.start('bring a cup').run
    got = {}
    t = threading.Thread(target=lambda: got.setdefault('a', run.ask('Which cup?', timeout=2)))
    t.start()
    assert _wait(lambda: run.awaiting == 'Which cup?')
    res = mgr.inject(run.run_id, 'the red one')
    assert res.action == 'answered' and res.decision.intent == Intent.ANSWER
    t.join(1.0)
    assert got['a'] == 'the red one' and run.awaiting is None
    # an answer with no question pending is a goal change, not lost
    assert mgr.inject(run.run_id, 'the blue one').action == 'queued'


def test_stop_while_awaiting_answer(mgr):
    run = mgr.start('bring a cup').run
    got = {}
    t = threading.Thread(target=lambda: got.setdefault('a', run.ask('Which cup?', timeout=2)))
    t.start()
    assert _wait(lambda: run.awaiting is not None)
    t0 = time.time()
    res = mgr.inject(run.run_id, '멈춰')
    assert res.action == 'cancelled'
    t.join(1.0)
    assert got['a'] is None and time.time() - t0 < 0.5


def test_repeated_interrupts(mgr):
    run = mgr.start('clean').run
    assert mgr.inject(run.run_id, 'pause').action == 'paused'
    assert mgr.inject(run.run_id, 'hold on').action == 'paused'     # idempotent
    assert mgr.inject(run.run_id, 'stop').action == 'cancelled'
    assert mgr.inject(run.run_id, 'stop').action == 'cancelled'
    assert mgr.inject(run.run_id, 'resume').action == 'ignored'     # no resume out of a cancel
    states = [e['state'] for e in run.events if e['event'] == 'run_state']
    assert states == ['running', 'paused', 'cancelling']            # each change once
    mgr.finish(run.run_id)
    late = mgr.inject(run.run_id, 'stop')
    assert late.action == 'refused' and late.code == 'not_active'
    assert mgr.inject('nope', 'stop').code == 'not_found'


def test_misheard_stop_not_matched(mgr):
    run = mgr.start('bring the cup').run
    for text in ('shop', 'stopwatch', 'the bus stop', 'top shelf', "don't stop", '멈추지 마'):
        res = mgr.inject(run.run_id, text)
        assert res.action != 'cancelled', text
    assert not run.cancel_requested


def test_cancel_hook_stops_robot(mgr):
    calls = []
    mgr.on_cancel.append(lambda r: calls.append(r.run_id))
    run = mgr.start('x').run
    mgr.inject(run.run_id, 'stop')
    assert calls == [run.run_id]


# ---- run_control shims ------------------------------------------------
def test_begin_run_does_not_wipe_other_runs_cancel(mgr):
    run = mgr.start('a').run
    ex = Executive(dt=0.02).start()
    assert _wait(lambda: run.begun)
    out = run_control.request_cancel()
    assert out['run_id'] == run.run_id and run.cancel_requested
    # a skill command (api/skills.py) starts while that run is still finishing
    threading.Thread(target=run_control.begin_run).start()
    time.sleep(0.01)
    assert run.cancel_requested and run_control._CANCEL.is_set()
    ex.thread.join(1.0)
    assert ex.status == 'aborted'


def test_new_run_starts_clear(mgr):
    a = mgr.start('a').run
    run_control.request_cancel()
    mgr.finish(a.run_id)
    b = mgr.start('b').run
    assert not b.cancel_requested
    ex = Executive(steps=3, dt=0.001).start()
    ex.thread.join(1.0)
    assert ex.status == 'success'


def test_legacy_without_run(mgr):
    assert not run_control.cancel_requested()
    run_control.request_cancel()
    assert run_control.cancel_requested()
    with pytest.raises(RunCancelled):
        run_control.check_cancel()
    run_control.begin_run()
    assert not run_control.cancel_requested()
    assert run_control.wait_if_paused() is True


def test_router_llm_injectable(mgr):
    seen = []

    def llm(text, ctx):
        seen.append((text, ctx['goal']))
        return {'intent': 'modify', 'confidence': 0.9}

    mgr.router = IntentRouter(llm)
    run = mgr.start('bring the cup').run
    res = mgr.inject(run.run_id, 'make it the mug')
    assert res.decision.intent == Intent.MODIFY and res.decision.source == 'llm'
    assert seen == [('make it the mug', 'bring the cup')]
    # STOP never reaches the LLM
    assert mgr.inject(run.run_id, 'stop').action == 'cancelled' and len(seen) == 1


def test_runs_listing(mgr):
    a = mgr.start('a').run
    mgr.finish(a.run_id, ok=False)
    b = mgr.start('b').run
    assert [r.run_id for r in mgr.list()] == [b.run_id, a.run_id]
    assert a.state == RunState.FAILED and a.outcome == 'failed'
    d = b.to_dict(events=True)
    assert d['state'] == 'running' and d['events'][0]['run_id'] == b.run_id
