"""Step S23b: the finish block (_finish, _say_of_msg, _humanize, _llm_failed, _deciding_failure, _note_failure) moved verbatim from
assist_loop.py to the FinishPhase mixin (phases/finish.py); _cancelled stays in the loop file (two older tests pin it there). These
tests prove nothing changed: (1) the mixin is in
AssistLoop's method resolution order and serves the names (nothing earlier shadows them), the loop file no longer defines them,
_run_blocking and _refused_reading stay in the loop file, _finish is a real method (its source names the two phrase keys), every
global name the moved bodies read is the same object as in the reference copy; (2) the method bodies are the same syntax trees (and
the same text but for one relative import) as the reference copy (tests/legacy_ref/s23b_finish.py, the old text at ca561f6);
(3) differential tests against that copy over grids of made-up statuses, messages, failures and collaborators. Made-up places only
(the fakebot robot, the sim ENV of tests/fakes.py)."""

import ast
import builtins
import inspect
import itertools
import json
import types
from pathlib import Path

import pytest

from legacy_ref import s23b_finish as old
from robot_agent.contract import FailureCode
from robot_agent.core import run_control
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.phases import PHASE_MIXINS, finish
from robot_agent.core.planning.robot import RobotView
from robot_agent.core.planning.understand import LLMBudgetExceeded, LLMUnavailable

NAMES = ("_finish", "_say_of_msg", "_humanize", "_llm_failed", "_deciding_failure", "_note_failure")
STAY = ("_run_blocking", "_cancelled", "_refused_reading", "_unsure_data", "_step_failed", "_sense", "_reconcile")
LOOP_FILE = Path(assist_loop.__file__)
_ROBOT = RobotView.load("fakebot")


class OldLoop(old.RefFinish, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert finish.FinishPhase in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(finish.FinishPhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(AssistLoop), n
        assert n in vars(finish.FinishPhase), n
        mine = vars(finish.FinishPhase)[n]
        assert getattr(AssistLoop, n) is getattr(mine, "__func__", mine), n      # no class before it in the MRO shadows it


def test_the_loop_file_does_not_define_them_any_more_and_the_rest_stays():
    src = LOOP_FILE.read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n
    for n in STAY:
        assert f"def {n}(" in src and n in vars(AssistLoop), n


def test_finish_is_a_real_method_not_a_stub():
    src = inspect.getsource(AssistLoop._finish)           # tests/test_sage_v2_r3_m10_round1b.py reads it for the two phrase keys
    assert "say_no_answer" in src and "say_not_confirmed" in src and "on_finish" in src
    assert AssistLoop._finish.__qualname__ == "FinishPhase._finish"


def test_the_moved_module_is_about_the_designed_size():
    assert len(Path(finish.__file__).read_text().splitlines()) <= 300


def _methods(path, cls):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}


def _free_globals(fn) -> set:
    loads = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    local = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)}
    local |= {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}
    local |= {(a.asname or a.name) for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) for a in n.names}
    local |= {n.name for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler) and n.name}
    return {x for x in loads - local if x not in dir(builtins)}


def test_every_global_name_of_the_moved_bodies_is_the_same_object_as_in_the_reference():
    new = _methods(finish.__file__, "FinishPhase")
    seen = set()
    for n in NAMES:
        for name in _free_globals(new[n]):
            assert hasattr(finish, name) and getattr(finish, name) is getattr(old, name), (n, name)
            seen.add(name)
    assert {"time", "re", "FailureCode", "speech", "classify_legacy_failure", "budget_end", "LLMBudgetExceeded",
            "LLMUnavailable"} <= seen


# -- (2) the bodies are the reference's bodies ---------------------------------------------------------------------------------
def _dump(fn):
    node = ast.parse(ast.unparse(fn))
    for n in ast.walk(node):                                   # line numbers are not behaviour; an import's package level is the module's depth
        for f in ("lineno", "end_lineno", "col_offset", "end_col_offset", "level"):
            if hasattr(n, f):
                setattr(n, f, 0)
        if isinstance(n, ast.ImportFrom):
            n.module = None
    return ast.dump(node)


def test_method_bodies_are_the_reference_bodies():
    new, rf = _methods(finish.__file__, "FinishPhase"), _methods(old.__file__, "RefFinish")
    assert set(NAMES) == set(new) and set(NAMES) | {"_cancelled"} == set(rf)
    for n in NAMES:
        assert _dump(new[n]) == _dump(rf[n]), n
    here = _methods(LOOP_FILE, "AssistLoop")["_cancelled"]                 # the one that stayed in the loop file
    assert _dump(here) == _dump(rf["_cancelled"])


def test_the_cancelled_method_in_the_loop_file_is_the_reference_text():
    lines = LOOP_FILE.read_text().splitlines()
    i = next(k for k, x in enumerate(lines) if x.startswith("    def _cancelled("))
    ref = Path(old.__file__).read_text().splitlines()
    j = next(k for k, x in enumerate(ref) if x.startswith("    def _cancelled("))
    assert lines[i:i + 3] == [ref[j], ref[j + 1].replace("robot_agent.core.run_control", "..run_control"), ref[j + 2]]


def test_the_moved_text_is_the_reference_text():
    """Comments and docstrings included: the moved methods are the reference's lines, in the same order (the reference goes on with
    _cancelled, which stayed in the loop file)."""
    def body(path, name):
        lines = Path(path).read_text().splitlines()
        return lines[next(i for i, x in enumerate(lines) if x.startswith(f"class {name}")) + 1:]
    new, rf = body(finish.__file__, "FinishPhase"), body(old.__file__, "RefFinish")
    assert new == rf[:len(new)]
    assert rf[len(new):][0].startswith("    def _cancelled(") or rf[len(new):][1].startswith("    def _cancelled(")


def test_the_reference_is_the_old_loop_text():
    ref = Path(old.__file__).read_text().splitlines()
    for line in ("    def _finish(self, r, status, msg=\"\", **kw) -> dict:", "    def _cancelled(self, r) -> Optional[dict]:",
                 "        return self._finish(r, \"aborted\", msg=\"Cancelled by user\") if cancel_requested() else None"):
        assert line in ref, line


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
def jd(x):
    return json.dumps(x, sort_keys=True, default=lambda o: getattr(o, "value", None) or type(o).__name__)


class Opts:
    def __init__(self, T):
        self.T = T

    def observe(self, name, *a):
        self.T.append(("observe", name, jd(a[1:])))


class Announcer:
    def __init__(self, T):
        self.T = T

    def announce(self, kind, **kw):
        self.T.append(("announce", kind))
        return f"<{kind}>"


def make_run(T, failures=(), lang="en"):
    r = types.SimpleNamespace(lang=lang, run_id="r1", world=WorldState(), robot=_ROBOT, failures=[dict(f) for f in failures],
                              llm_budget=types.SimpleNamespace(used=3, limit=3), announcer=Announcer(T),
                              run=types.SimpleNamespace(status="running", ended_at=None, failure=None))
    r.ev = lambda name, **a: {"event": name, **a}
    return r


def make_loop(cls, T, places=True):
    lp = cls.__new__(cls)
    lp.options, lp.robot = Opts(T), _ROBOT
    lp._llm_unreachable = False
    lp._in_lang = lambda text, lang: f"[{lang}]{text}"
    if places:
        from fakes import load_env
        lp._env_places = Places(load_env(), openable=_ROBOT.openable)
    return lp


def both(call, **run_kw):
    """The same call on the old and the new class: (result, trace, the run's final state), as JSON text."""
    out = []
    for cls in (OldLoop, AssistLoop):
        T = []
        lp, r = make_loop(cls, T), make_run(T, **run_kw)
        try:
            val = call(lp, r)
        except Exception as e:                                   # noqa: BLE001 - the error class is part of the result
            val = ("raised", type(e).__name__, str(e))
        state = {"status": r.run.status, "failure": r.run.failure, "failures": r.failures, "ended": r.run.ended_at is not None}
        out.append((jd(val), T, jd(state)))
    return out


MSGS = ["", "Cancelled by user", "no answer to: where is it?", "the user did not confirm the task", "internal error: boom",
        "plan finished but the goals do not hold", "Place the apple: the robot is at the desk", "Pick failed again the same way: x",
        "the apple is not here (searched 3 places)", "I dropped it (hand_empty)", "Find apple failed (not_found) and more",
        "step 3: the plan does not reach", "the cup is not at drawer@kitchen"]
CODES = [None, "cancelled", "not_found", FailureCode.HW_FAULT, FailureCode.CANCELLED]


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: 1000.0)


def test_finish_equals_the_reference_over_a_grid():
    seen = set()
    open_failure = [{"sig": ("Pick", "apple"), "code": "not_found", "device": "camera", "evidence": {"step": "Pick apple"},
                     "recovered": False}]
    for status, msg, code, say, localized, fails in itertools.product(
            ("success", "failed", "aborted", "refused"), MSGS, CODES, (None, "I said it", "go to drawer@kitchen"), (False, True),
            ((), open_failure, [{**open_failure[0], "recovered": True}])):
        kw = {k: v for k, v in (("code", code), ("say", say), ("localized", localized or None)) if v is not None}
        x, y = both(lambda lp, r: lp._finish(r, status, msg=msg, **kw), failures=fails)
        assert x == y, (status, msg, kw, fails)
        seen.add((status, json.loads(y[2])["failure"] is not None))
    assert len(seen) == 4                                      # a failed run keeps its failure record, the others do not


def test_finish_in_another_language_equals_the_reference():
    for lang, say in itertools.product(("en", "ko", "es"), (None, "ok")):
        kw = {"say": say} if say else {}
        x, y = both(lambda lp, r: lp._finish(r, "failed", msg="the apple is not here", **kw), lang=lang)
        assert x == y, (lang, say)


def test_say_of_msg_and_humanize_equal_the_reference():
    texts = MSGS + ["go to drawer@kitchen", "put it on dining table@kitchen and desk@study room", "Move to the kitchen", "a_b c"]
    for t in texts:
        for fn in ("_say_of_msg", "_humanize"):
            x, y = both(lambda lp, r: getattr(lp, fn)(t))
            assert x == y, (fn, t)
    for fn in ("_say_of_msg", "_humanize"):
        T = []
        a, b = make_loop(OldLoop, T, places=False), make_loop(AssistLoop, T, places=False)
        for t in texts:
            assert jd(getattr(a, fn)(t)) == jd(getattr(b, fn)(t)), (fn, t)


def test_llm_failed_equals_the_reference():
    errors = [LLMBudgetExceeded("used"), LLMUnavailable(ConnectionError("down")), LLMUnavailable(ValueError("bad")),
              ConnectionError("refused"), TimeoutError("timed out"), ValueError("oops"), RuntimeError("boom")]
    for e, what, lang in itertools.product(errors, ("language model", "planner"), ("en", "ko")):
        x, y = both(lambda lp, r: lp._llm_failed(r, e, what), lang=lang)
        assert x == y, (e, what, lang)
        flags = []
        for cls in (OldLoop, AssistLoop):
            lp = make_loop(cls, [])
            lp._llm_failed(make_run([], lang=lang), e, what)
            flags.append(lp._llm_unreachable)
        assert flags[0] == flags[1], (e, what)


def test_deciding_failure_and_note_failure_equal_the_reference():
    fails = [[], [{"sig": ("Pick", "apple"), "code": "not_found", "device": "cam", "evidence": {"a": 1}, "recovered": False}],
             [{"sig": ("Pick", "apple"), "code": "hw_fault", "device": "arm", "evidence": {}, "recovered": True}]]
    kws = [{}, {"code": "timeout"}, {"code": FailureCode.TIMEOUT, "device": "arm"}, {"evidence": {"k": "v"}}, {"device": "cam"}]
    for f, kw, msg in itertools.product(fails, kws, MSGS):
        res = []
        for cls in (OldLoop, AssistLoop):
            r, k = make_run([], failures=f), dict(kw)
            res.append((jd(cls._deciding_failure(r, msg, k)), jd(k), jd(r.run.failure)))
        assert res[0] == res[1], (f, kw, msg)
    for code, dev, ev in itertools.product(CODES, (None, "cam"), ({}, {"x": 1})):
        x, y = both(lambda lp, r: lp._note_failure(r, {"action": "Pick", "object": "apple"}, code, dev, "r" * 400, **ev))
        assert x == y, (code, dev, ev)
        assert len(json.loads(y[2])["failures"]) == 1


def test_cancelled_equals_the_reference(monkeypatch):
    for flag in (True, False):
        monkeypatch.setattr(run_control, "cancel_requested", lambda f=flag: f)
        x, y = both(lambda lp, r: lp._cancelled(r))
        assert x == y, flag
        assert (json.loads(y[0]) is None) == (not flag)
        assert (json.loads(y[2])["status"] == "aborted") == flag
