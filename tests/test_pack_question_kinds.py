"""Step S31: ``questions_default_kind`` gives a kind-less ``clarify`` event the kind a pack declares (core/pack/questions.py, applied
in ``RunContext.ev``, the one place every event is built). These tests prove the move changed nothing for a pack that does not
set the key: the default data is "no default kind" (the old behaviour), no shipped pack sets the key, and the new ``ev`` builds
the same event as a reference copy of the old ``ev`` over a grid of events. With the key set, only a kind-less clarify changes."""

import json
import types

import pytest

from robot_agent.core.pack import default_pack, loader, questions as Q
from robot_agent.core.planning import loop as L
from robot_agent.core.planning.robot import RobotView

FAKES = ("fakebot", "fakehome", "fakeline")
RAW = {"schema": 2, "name": "kind_s31", "extends": "household", "assumed": True, "questions_default_kind": "line.question"}


class Log:
    """A run logger that keeps what it is given (the JSONL line of the run)."""

    def __init__(self):
        self.rows = []

    def event(self, name, **fields):
        self.rows.append((name, fields))


def old_ev(speak, logger, event, say=None, **fields):
    """A reference copy of ``RunContext.ev`` as it was before Step S31."""
    import time
    d = {"event": event, **fields}
    if say is not None:
        d["say"] = speak(say) if speak is not None and isinstance(say, str) else say
    try:
        logger.event(event, t=time.time(), **{k: v for k, v in d.items() if k != "event"})
    except Exception:
        pass
    return d


def ctx_of(robot, logger=None):
    c = object.__new__(L.RunContext)
    c.robot, c.speak, c.logger = robot, None, logger or Log()
    return c


EVENTS = [("clarify", {"question": "q?", "round": 0}), ("clarify", {"question": "q?", "round": 1, "kind": "confirm"}),
          ("clarify", {"question": "q?", "round": 1, "kind": None}), ("clarify", {"question": "q?", "kind": ""}),
          ("status", {"msg": "hi"}), ("done", {"ok": True}), ("plan", {"steps": []}), ("step_start", {"kind": ""}),
          ("clarify", {})]


# ── the default data is the old behaviour ────────────────────────────────────────────────────────────────────────────
def test_the_default_data_is_no_default_kind():
    assert default_pack().questions_default_kind is None
    for name in loader.index()["packs"]:
        assert loader.shipped(name).questions_default_kind is None, f"the shipped pack {name} sets the key"
    for pkg in FAKES:
        assert RobotView.load(pkg).pack.questions_default_kind is None


@pytest.mark.parametrize("pkg", FAKES)
@pytest.mark.parametrize("event,fields", EVENTS)
def test_the_new_ev_builds_the_event_of_the_old_ev_when_the_pack_sets_nothing(pkg, event, fields):
    robot = RobotView.load(pkg)
    new_log, old_log = Log(), Log()
    got = ctx_of(robot, new_log).ev(event, say="hello", **fields)
    want = old_ev(None, old_log, event, say="hello", **fields)
    assert got == want and list(got) == list(want)
    assert [(n, {k: v for k, v in f.items() if k != "t"}) for n, f in new_log.rows] == \
           [(n, {k: v for k, v in f.items() if k != "t"}) for n, f in old_log.rows]


def test_a_stub_robot_or_a_robot_whose_pack_fails_gets_the_old_event():
    for robot in (None, object(), types.SimpleNamespace(pack=None), types.SimpleNamespace(pack=types.SimpleNamespace())):
        assert ctx_of(robot).ev("clarify", question="q?", round=0) == {"event": "clarify", "question": "q?", "round": 0}

    class Broken:
        @property
        def pack(self):
            raise RuntimeError("cannot resolve")
    assert Q.declared_default(Broken()) is None
    assert ctx_of(Broken()).ev("clarify", question="q?") == {"event": "clarify", "question": "q?"}


def test_only_a_clarify_event_reads_the_pack():
    class Counting:
        reads = 0

        @property
        def pack(self):
            Counting.reads += 1
            return types.SimpleNamespace(questions_default_kind="x")
    for event, fields in EVENTS:
        if event != "clarify" or fields.get("kind"):
            ctx_of(Counting()).ev(event, **fields)
    assert Counting.reads == 0


# ── the key set ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_with_the_key_set_a_kind_less_clarify_carries_the_declared_kind():
    robot = RobotView.load("fakeline")
    robot.use_pack(json.loads(json.dumps(RAW)))
    assert robot.pack.questions_default_kind == "line.question"
    log = Log()
    c = ctx_of(robot, log)
    assert c.ev("clarify", question="q?", round=0) == {"event": "clarify", "question": "q?", "round": 0, "kind": "line.question"}
    assert c.ev("clarify", question="q?", kind=None)["kind"] == "line.question"
    assert c.ev("clarify", question="q?", kind="")["kind"] == "line.question"
    assert c.ev("clarify")["kind"] == "line.question"
    assert log.rows[0][1] == {"question": "q?", "round": 0}               # the run log is written as before: the default kind is for the stream only


def test_with_the_key_set_every_other_event_and_every_kind_stays_as_it_was():
    robot = RobotView.load("fakeline")
    robot.use_pack(json.loads(json.dumps(RAW)))
    for event, fields in EVENTS:
        got, want = ctx_of(robot).ev(event, **fields), old_ev(None, Log(), event, **fields)
        if event == "clarify" and not fields.get("kind"):
            assert got == dict(want, kind="line.question")
        else:
            assert got == want
    assert ctx_of(robot).ev("clarify", question="q?", kind="confirm")["kind"] == "confirm"
    assert ctx_of(robot).ev("clarify", question="q?", kind="x", say="s") == old_ev(None, Log(), "clarify", question="q?", kind="x", say="s")


def test_the_pure_rule():
    ev = {"event": "clarify", "question": "q?"}
    assert Q.with_default_kind(ev, None) is ev and Q.with_default_kind(ev, "") is ev and Q.with_default_kind(ev, 3) is ev
    assert Q.with_default_kind(ev, "k") == {"event": "clarify", "question": "q?", "kind": "k"} and ev == {"event": "clarify", "question": "q?"}
    assert Q.with_default_kind({"event": "done"}, "k") == {"event": "done"}
    assert Q.with_default_kind({"event": "clarify", "kind": "a"}, "k")["kind"] == "a"


def test_the_value_name_is_refused_as_deferred():
    robot = RobotView.load("fakeline")
    with pytest.raises(Exception, match="questions_default_kind"):
        robot.use_pack(dict(RAW, questions_default_kind="name"))
    assert robot.pack.questions_default_kind is None


def test_the_module_is_small_and_names_no_robot():
    import inspect
    src = inspect.getsource(Q)
    assert len(src.splitlines()) <= 300 and "fakeline" not in src
