"""Step S31: ``questions_default_kind`` gives a kind-less ``clarify`` event the kind a pack declares (opt-in).

The decisions (core/pack/questions.py) are pure; the one place that applies them is ``RunContext.ev`` (core/planning/loop.py).
Here: the default data is the old behaviour (no pack declares the key), the pure functions, and a differential test of
``RunContext.ev`` against a copy of its old body (a reference kept in this file). Made-up robots and packs only."""

import time
from types import SimpleNamespace

import pytest

from robot_agent.contract import load_adapter
from robot_agent.core.pack import default_pack, questions
from robot_agent.core.pack.loader import index, shipped
from robot_agent.core.planning.loop import RunContext
from robot_agent.core.planning.robot import RobotView

FAKES = ["fakebot", "fakehome", "fakeline"]
PACK = {"schema": 2, "name": "line", "extends": "household", "assumed": True, "questions_default_kind": "line.question"}


# ── the default data is the old behaviour ─────────────────────────────────────────────────────────────────────────────
def test_no_shipped_pack_and_no_made_up_robot_declares_a_default_kind():
    assert default_pack().questions_default_kind is None
    for name in index()["packs"]:
        assert shipped(name).questions_default_kind is None, name
    for pkg in FAKES:
        assert RobotView(load_adapter(pkg)).pack.questions_default_kind is None, pkg


# ── the pure decisions ────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value, want", [(None, None), ("", None), ("   ", None), ("name", None), (7, None), ("line.question", "line.question")])
def test_default_kind_is_a_non_empty_literal_or_none(value, want):
    assert questions.default_kind(SimpleNamespace(questions_default_kind=value)) == want
    assert questions.default_kind(object()) is None


@pytest.mark.parametrize("kind", [None, "", "missing"])
def test_a_kind_less_clarify_gets_the_default_kind(kind):
    ev = {"event": "clarify", "question": "q", "round": 1}
    if kind != "missing":
        ev["kind"] = kind
    got = questions.with_kind(ev, "line.question")
    assert got == {"event": "clarify", "question": "q", "round": 1, "kind": "line.question"}
    assert ev.get("kind") == (None if kind == "missing" else kind)         # the input is not changed


def test_a_kind_the_question_site_gave_and_every_other_event_are_never_changed():
    given = {"event": "clarify", "question": "q", "round": 1, "kind": "confirm"}
    other = {"event": "plan", "steps": []}
    assert questions.with_kind(given, "line.question") is given
    assert questions.with_kind(other, "line.question") is other
    kindless = {"event": "clarify", "question": "q", "round": 0}
    assert questions.with_kind(kindless, None) is kindless


def test_for_run_reads_the_pack_only_for_a_kind_less_clarify_and_survives_a_robot_without_one():
    class Boom:
        @property
        def pack(self):
            raise AssertionError("the pack was read")
    for ev in ({"event": "plan"}, {"event": "clarify", "kind": "confirm"}):
        assert questions.for_run(ev, Boom()) is ev
    ev = {"event": "clarify", "question": "q"}
    assert questions.for_run(ev, Boom()) is ev                              # a pack that cannot be read: as before
    assert questions.for_run(ev, object()) is ev                            # no pack view at all
    robot = SimpleNamespace(pack=SimpleNamespace(questions_default_kind="k"))
    assert questions.for_run(ev, robot) == {"event": "clarify", "question": "q", "kind": "k"}


# ── RunContext.ev against a copy of its old body ──────────────────────────────────────────────────────────────────────
class Log:
    def __init__(self):
        self.rows = []

    def event(self, event, **fields):
        self.rows.append((event, {k: v for k, v in fields.items() if k != "t"}))


def old_ev(logger, speak, event, say=None, **fields):
    """The body of RunContext.ev before Step S31 (core/planning/loop.py at 814d212)."""
    d = {"event": event, **fields}
    if say is not None:
        d["say"] = speak(say) if speak is not None and isinstance(say, str) else say
    try:
        logger.event(event, t=time.time(), **{k: v for k, v in d.items() if k != "event"})
    except Exception:
        pass
    return d


def context(robot, speak=None):
    r = object.__new__(RunContext)
    r.robot, r.speak, r.logger = robot, speak, Log()
    return r


CALLS = [("clarify", {"question": "q", "round": 1}), ("clarify", {"question": "q", "round": 0, "kind": "which_one"}),
         ("clarify", {"question": "q", "round": 2, "kind": None}), ("plan", {"steps": [1]}), ("done", {"status": "success"}),
         ("clarify", {"question": "q", "round": 1}, "Where to?"), ("understand", {"status": "clear", "goals": []})]


def run(ctx, ev_fn, call):
    event, fields, *say = call
    return ev_fn(event, say[0] if say else None, **fields)


@pytest.mark.parametrize("pkg", FAKES)
def test_ev_is_identical_to_the_old_body_when_the_pack_declares_no_default_kind(pkg):
    robot = RobotView(load_adapter(pkg))
    new, old = context(robot, str.upper), Log()
    for call in CALLS:
        got = run(new, new.ev, call)
        want = run(None, lambda e, s, **f: old_ev(old, str.upper, e, s, **f), call)
        assert got == want
    assert new.logger.rows == old.rows


def test_ev_with_a_declared_default_kind_differs_only_in_the_kind_of_the_kind_less_clarify_events():
    robot = RobotView(load_adapter("fakebot"))
    robot.use_pack(dict(PACK))
    assert robot.pack.questions_default_kind == "line.question"
    new, old = context(robot), Log()
    for call in CALLS:
        got = run(new, new.ev, call)
        want = run(None, lambda e, s, **f: old_ev(old, None, e, s, **f), call)
        if call[0] == "clarify" and not call[1].get("kind"):
            assert got == {**want, "kind": "line.question"}
        else:
            assert got == want
    assert [kind for _e, f in new.logger.rows for kind in [f.get("kind")]] == \
        ["line.question", "which_one", "line.question", None, None, "line.question", None]


def test_a_pack_that_asks_for_the_deferred_value_name_is_refused_at_load():
    robot = RobotView(load_adapter("fakebot"))
    with pytest.raises(Exception, match="name"):
        robot.use_pack(dict(PACK, questions_default_kind="name"))
