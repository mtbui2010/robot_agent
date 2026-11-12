"""Cancel audit: every long blocking call a run can sit in, with a slow fake. ``POST /agent/cancel`` (``run_control.request_cancel``) must
end the run with a cancelled ``done`` in about a second (not after the slow call), start no later step, and leave nothing behind for the
next run (no stale cancel flag, no pending listen slot)."""
import re
import threading
import time

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

DS, DT = "desk@study room", "dining table@kitchen"
TASK = "Put the mug on the dining table."
PLAN = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
SLOW_S = 8.0                                       # the slow call; the run must not wait for it
LIMIT_S = 2.5                                      # cancel after 0.4 s, done within this (a loaded CI machine included)


def understood(frm=""):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "on", "object": "mug", "from": frm, "to": "dining table"}]}


class SlowLLM(FakeLLM):
    """Answers like FakeLLM, but a call whose prompt matches ``rx`` (or the ``nth`` call) takes SLOW_S seconds."""

    def __init__(self, *a, nth=1, rx=None, **kw):
        super().__init__(*a, **kw)
        self.nth, self.rx, self.n, self.release, self.slowed = nth, rx, 0, threading.Event(), []

    def chat(self, prompt="", format=None, **kw):
        self.n += 1
        if (re.search(self.rx, prompt) if self.rx else self.n == self.nth):
            self.slowed.append(prompt[:60])
            self.release.wait(SLOW_S)
        return super().chat(prompt=prompt, format=format, **kw)


class SlowPlanner(ScriptedPlanner):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.release = threading.Event()

    def generate_plan(self, task, obs, visible_objects):
        self.release.wait(SLOW_S)
        return super().generate_plan(task, obs, visible_objects)


class SlowAskRobot(FakeRobot):
    """The Ask skill hears nothing for SLOW_S seconds (a person who does not answer)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.release = threading.Event()

    def _exec(self, skill, o, params):
        if skill == "ask":
            self.release.wait(SLOW_S)
            return {"isdone": False, "text": ""}
        return super()._exec(skill, o, params)


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start(TASK, user_id="alice", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


def cancelled_run(make_assist, env, llm=None, planner=None, robot=None, lang="en", task=TASK, **opts):
    robot = robot or FakeRobot(env, {"mug": DS})
    loop = make_assist(robot, llm or FakeLLM(default=understood()), planner or ScriptedPlanner(PLAN), **opts)
    threading.Timer(0.4, run_control.request_cancel).start()
    t0 = time.time()
    events = run_task(loop, task, lang=lang, max_events=3000)
    for slow in (llm, planner, robot):
        getattr(slow, "release", threading.Event()).set()
    return robot, events, time.time() - t0


def ended_cancelled(events):
    d = [e for e in events if e.get("event") == "done"]
    return bool(d) and d[-1].get("status") in ("aborted", "cancelled")


def test_cancel_in_the_understand_call(run, make_assist, env):
    robot, events, took = cancelled_run(make_assist, env, llm=SlowLLM(default=understood(), nth=1))
    assert took < LIMIT_S and ended_cancelled(events) and robot.trace == [], (took, events[-1])


def test_cancel_in_the_planner_call(run, make_assist, env):
    robot, events, took = cancelled_run(make_assist, env, planner=SlowPlanner(PLAN))
    assert took < LIMIT_S and ended_cancelled(events) and robot.trace == [], (took, events[-1])


def test_cancel_in_the_translation_of_a_non_english_request(run, make_assist, env):
    llm = SlowLLM(default=understood(), nth=1)
    robot, events, took = cancelled_run(make_assist, env, llm=llm, lang="ko", input_pivot="translate", task="머그를 식탁에 놓아 주세요.")
    assert took < LIMIT_S and ended_cancelled(events) and robot.trace == [], (took, events[-1])


def test_cancel_while_the_robot_waits_for_an_answer(run, make_assist, env):
    robot = SlowAskRobot(env, {"mug": DS})
    _r, events, took = cancelled_run(make_assist, env, robot=robot, ask_source="1")
    assert took < LIMIT_S and ended_cancelled(events) and robot.trace == [], (took, events[-1])


def test_the_next_run_starts_clean_after_a_cancel(run, make_assist, env):
    _robot, events, _t = cancelled_run(make_assist, env, llm=SlowLLM(default=understood(), nth=1))
    assert ended_cancelled(events)
    from robot_agent import utils
    assert not utils._LISTEN_PENDING                                    # no listen slot left waiting
    reset_manager()
    run_control._CANCEL.clear()
    run_control.manager().start(TASK, user_id="alice", role="member")
    run_control.begin_run()
    assert not run_control.cancel_requested()
    robot = FakeRobot(env, {"mug": DS})
    events = run_task(make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(PLAN)), TASK, max_events=3000)
    assert [e for e in events if e.get("event") == "done"][-1]["status"] == "success"


def test_cancel_in_the_translation_of_what_the_robot_says(run, make_assist, env):
    """The robot's question to a Korean speaker is written in Korean by the translator model."""
    llm = SlowLLM(default=understood(), rx="Translate into natural polite")
    robot, events, took = cancelled_run(make_assist, env, llm=llm, lang="ko", task="머그를 식탁에 놓아 주세요.", ask_source="1")
    assert llm.slowed, "the scenario never reached the output translation"
    assert took < LIMIT_S and ended_cancelled(events) and robot.trace == [], (took, events[-1])


def test_a_listen_that_starts_after_the_stop_does_not_wait_for_its_timeout(monkeypatch):
    """A skill that opens its dashboard listen after Stop was pressed (a retry loop): no one is left to release it."""
    from robot_agent import skills, utils
    run_control._CANCEL.set()
    monkeypatch.setattr(skills, "has_emitter", lambda: True)
    monkeypatch.setattr(skills, "log_data", lambda d: None)
    t0 = time.time()
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            utils.listen_dashboard("Where?", lang="en", timeout=8.0)
    finally:
        run_control._CANCEL.clear()
    assert time.time() - t0 < 1.0 and not utils._LISTEN_PENDING
