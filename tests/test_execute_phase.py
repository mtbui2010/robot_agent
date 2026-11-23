"""Step S22: the execute stage (_execute_phase, _refind_after_pause, _plan_ended, _coverage_gap, _look_at_untouched, _precondition,
_guard_arrived, _guard, _run_step) moved verbatim from assist_loop.py to the ExecutePhase mixin (phases/execute.py). These tests prove
nothing changed: (1) the mixin is in AssistLoop's method resolution order and serves the names (nothing earlier shadows them), the
loop file no longer defines them, _sense / _reconcile / _save_world / _post_check / _step_failed / _effect stay in the loop file, every
global name the moved bodies read is bound to the same object as in the reference copy; (2) the method bodies are the same syntax trees
(and the same text but for one relative import) as the reference copy (tests/legacy_ref/s22_execute.py, the old text at 50bafd5);
(3) differential tests against that copy: the old and the new class give the same events, values, calls to their collaborators and
changes to the run state over grids of made-up steps, worlds, goals, options and scripted collaborators. Made-up places only (the
fakebot robot, the sim ENV of tests/fakes.py)."""

import ast
import builtins
import itertools
import json
import types
from pathlib import Path

import pytest

from legacy_ref import s22_execute as old
from robot_agent.contract import Unbindable
from robot_agent.core.planning import assist_loop, loop
from robot_agent.core.planning import reconcile as sensed
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.phases import PHASE_MIXINS, execute
from robot_agent.core.planning.records import StepRecord, VerifyResult
from robot_agent.core.planning.robot import RobotView

NAMES = ("_execute_phase", "_refind_after_pause", "_plan_ended", "_coverage_gap", "_look_at_untouched", "_precondition",
         "_guard_arrived", "_guard", "_run_step")
STAY = ("_sense", "_reconcile", "_save_world", "_post_check", "_step_failed", "_effect")
LOOP_FILE = Path(assist_loop.__file__)
DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"
NEXT, STOP, DONE = loop._NEXT, loop._STOP, loop._DONE


class OldLoop(old.RefExecute, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert execute.ExecutePhase in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(execute.ExecutePhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(AssistLoop), n
        assert n in vars(execute.ExecutePhase), n
        assert getattr(AssistLoop, n) is vars(execute.ExecutePhase)[n], n       # no class before it in the MRO shadows it


def test_the_loop_file_does_not_define_them_any_more():
    src = LOOP_FILE.read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n


def test_what_stays_is_still_in_the_loop_file():
    src = LOOP_FILE.read_text()
    for n in STAY:
        assert f"def {n}(" in src and n in vars(AssistLoop), n


def test_guard_and_run_step_stay_callable_through_the_class(monkeypatch):
    assert AssistLoop._guard is execute.ExecutePhase._guard and AssistLoop._run_step is execute.ExecutePhase._run_step
    monkeypatch.setattr(AssistLoop, "_precondition", lambda self, r, st: "patched")
    assert AssistLoop.__new__(AssistLoop)._precondition(None, None) == "patched"


def test_the_moved_module_is_about_the_designed_size():
    assert len(Path(execute.__file__).read_text().splitlines()) <= 300      # 246 verbatim lines + docstring and imports


def test_the_end_markers_and_helpers_are_the_loops_own_objects():
    assert execute._NEXT is loop._NEXT and execute._STOP is loop._STOP and execute._DONE is loop._DONE
    assert execute._vr_list is loop._vr_list and old._vr_list is loop._vr_list
    assert assist_loop._DONE is loop._DONE and assist_loop._NEXT is loop._NEXT       # still re-exported (tests import them)


def _free_globals(fn) -> set:
    loads = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    local = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)}
    local |= {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}
    local |= {(a.asname or a.name) for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) for a in n.names}
    local |= {n.name for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler) and n.name}
    return {x for x in loads - local if x not in dir(builtins)}


def test_every_global_name_of_the_moved_bodies_is_the_same_object_as_in_the_reference():
    new = _methods(execute.__file__, "ExecutePhase")
    seen = set()
    for n in NAMES:
        for name in _free_globals(new[n]):
            assert hasattr(execute, name) and getattr(execute, name) is getattr(old, name), (n, name)
            if hasattr(assist_loop, name):
                assert getattr(assist_loop, name) is getattr(execute, name), (n, name)
            seen.add(name)
    assert {"run_skill", "find_precheck", "verdict", "symbolic_check", "ask_json", "sensed", "StepRecord", "_vr_list"} <= seen


def test_the_hooks_and_services_are_still_called_by_the_moved_code():
    src = Path(execute.__file__).read_text()
    for call in ('self.options.query("belief_table", default={})', 'self.options.decide_flow("on_run_goals", r)',
                 'self.options.decide_flow("on_step", r, st)'):
        assert call in src, call


# -- (2) the bodies are the reference's bodies ---------------------------------------------------------------------------------
def _methods(path, cls):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}


def _dump(fn):
    node = ast.parse(ast.unparse(fn))
    for n in ast.walk(node):                                   # line numbers are not behaviour; an import's package level is the module's depth
        for f in ("lineno", "end_lineno", "col_offset", "end_col_offset", "level"):
            if hasattr(n, f):
                setattr(n, f, 0)
        if isinstance(n, ast.ImportFrom):
            n.module = None                                    # the names imported are compared, not the module path
    return ast.dump(node)


def test_method_bodies_are_the_reference_bodies():
    new, rf = _methods(execute.__file__, "ExecutePhase"), _methods(old.__file__, "RefExecute")
    assert set(NAMES) == set(new) == set(rf)
    for n in NAMES:
        assert _dump(new[n]) == _dump(rf[n]), n


def test_the_moved_text_is_the_reference_text():
    """Comments and docstrings included: the class bodies are the same lines, but for the section comment the mixin starts with and
    the one relative import inside _coverage_gap (one package level deeper; absolute in the reference)."""
    def body(path, name):
        lines = Path(path).read_text().splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith(f"class {name}")) + 1
        return [x for x in lines[start:]]
    new, rf = body(execute.__file__, "ExecutePhase"), body(old.__file__, "RefExecute")
    assert new[0].lstrip().startswith("# ── 3. execute")
    new = new[1:]
    assert len(new) == len(rf)
    diff = [(a, b) for a, b in zip(new, rf) if a != b]
    assert diff == [("        from ...run_control import call_cancellable, is_cancel",
                     "        from robot_agent.core.run_control import call_cancellable, is_cancel")]


def test_the_reference_is_the_old_loop_text():
    ref = Path(old.__file__).read_text().splitlines()
    for line in ("    def _execute_phase(self, r):", "        return \"\" if ok_pre else why_",
                 "    def _guard_arrived(self, r, st):", "        return (yield from self._step_failed(r, st, skill, rec, res, reason))"):
        assert line in ref, line


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
_ROBOT = RobotView.load("fakebot")


def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(str(v) for v in x)
    if isinstance(x, (StepRecord, VerifyResult)):
        return {type(x).__name__: _plain(vars(x))}
    if isinstance(x, WorldState):
        return {"World": _plain(x.to_dict())}
    if isinstance(x, types.SimpleNamespace):                  # r.run: the step records it keeps
        return {"ns": _plain(vars(x))}
    return x


def jd(x):
    return json.dumps(_plain(x), sort_keys=True, default=lambda o: type(o).__name__)


class Opts:
    """The option dispatcher stub: queries and flow decisions answer from a script; every call is recorded."""

    def __init__(self, T, queries=None, flows=None):
        self.T, self.queries, self.flows = T, queries or {}, flows or {}

    def query(self, name, *a, **k):
        self.T.append(("query", name))
        return self.queries.get(name, k.get("default"))

    def decide_flow(self, name, r, *a):
        self.T.append(("decide_flow", name, jd(a)))
        if False:
            yield
        v = self.flows.get(name)
        return v(*a) if callable(v) else v


def gen_of(value):
    """A collaborator that is a generator: yields one marker event, returns ``value`` (a callable gets the call's arguments)."""
    def g(*a, **k):
        yield {"event": "stub"}
        return value(*a, **k) if callable(value) else value
    return g


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def env_():
    from fakes import load_env
    return load_env()


class Mem:
    def __init__(self, T):
        self.T = T

    def see(self, *a):
        self.T.append(("memory.see", jd(a)))


def make_run(T, **kw):
    r = types.SimpleNamespace(task="put the apple on the table", lang="en", dialogue=[("user", "go")], world=W(), goals=[],
                              steps=[], completed=[], placed={}, world_at_start=W(), i=0, replans=0, node="n", suspended=0,
                              seen_fail={}, memory=Mem(T), robot=_ROBOT, goal_txt="g", replan_error=None, confirms=0,
                              llm_budget=types.SimpleNamespace(used=0), run=types.SimpleNamespace(steps=[]))
    r.places = Places(env_(), openable=_ROBOT.openable)
    r.ev = lambda name, **a: {"event": name, **a}
    r.__dict__.update(kw)
    return r


def make_loop(cls, T, cfg=None, queries=None, flows=None, **script):
    lp = cls.__new__(cls)
    lp.cfg = {"reconcile": False, "arrive_tol": 0.3, "sensor_health": False, **(cfg or {})}
    lp.options = Opts(T, queries, flows)
    lp.robot, lp.sr, lp.original = _ROBOT, "sr", None
    for name, val in script.items():
        def fn(*a, _n=name, _v=val, **k):
            T.append((_n, jd(a[1:] if a and hasattr(a[0], "ev") else a), jd(k)))
            if isinstance(_v, Exception):
                raise _v
            return _v(*a, **k) if callable(_v) else _v
        setattr(lp, name, fn)
    return lp


def drive(gen):
    if not hasattr(gen, "__next__"):
        return [], gen
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as e:
        return evs, e.value
    except Exception as e:                                       # noqa: BLE001 - the error class is part of the result
        return evs, ("raised", type(e).__name__, str(e))


def both(build, call):
    """Run the same scenario on the old and the new class; the two results (events, value, trace, run state)."""
    out = []
    for cls in (OldLoop, AssistLoop):
        T = []
        lp, r = build(cls, T)
        try:
            evs, val = drive(call(lp, r))
        except Exception as e:                                   # noqa: BLE001
            evs, val = [], ("raised", type(e).__name__, str(e))
        state = {k: v for k, v in sorted(vars(r).items()) if k not in ("ev", "places", "robot", "memory", "llm_budget")}
        out.append((jd(evs), jd(val), T, jd(state)))
    return out


@pytest.fixture
def frozen(monkeypatch):
    """The clock and the robot calls the moved bodies read as module globals: the same stubs in the old and the new module."""
    import time
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    box = {"T": [], "pre": None, "res": None}

    def precheck(robot, node, st, on):
        box["T"].append(("find_precheck", st.get("action"), on))
        return box["pre"]

    def run(sr, skill, params, node, log_fn=None, interruptible=True):
        box["T"].append(("run_skill", skill, jd(params), interruptible))
        return box["res"]
    for mod in (old, execute):
        monkeypatch.setattr(mod, "find_precheck", precheck)
        monkeypatch.setattr(mod, "run_skill", run)
    return box


STEPS = [("MoveTo", DT), ("Place", DT), ("PutIn", DR), ("Open", DR), ("Close", DR), ("Pick", "apple"), ("Find", "apple"),
         ("Open", CT), ("Say", "hello")]
WORLDS = [dict(arrived=DT), dict(arrived=DT, holding="apple"), dict(arrived=DR, holding="apple"), dict(), dict(arrived=DR),
          dict(arrived=DT, found="apple")]


def test_precondition_equals_the_reference():
    seen = set()
    for (a, o), w in itertools.product(STEPS, WORLDS):
        def build(cls, T):
            return make_loop(cls, T), make_run(T, world=W(**w))
        x, y = both(build, lambda lp, r: lp._precondition(r, {"action": a, "object": o}))
        assert x == y, (a, o, w)
        seen.add(y[1] == '""')
    assert seen == {True, False}


def test_look_at_untouched_and_refind_after_pause_equal_the_reference():
    goals = [[G("on", "apple", "", DT)], [G("on", "apple", "", DT), G("on", "cup", "", CT)], [G("in", "apple", "", DR)],
             [G("on", "apple", DS, DT)], [G("on", "apple", "", "nowhere")]]
    seen = set()
    for gs, w, placed, looked, completed in itertools.product(goals, WORLDS, ({}, {"apple": DT}), (None, {("apple", DT)}),
                                                              ([], [{"action": "Pick", "object": "apple"}])):
        def build(cls, T):
            r = make_run(T, goals=gs, world=W(**w), placed=dict(placed), completed=list(completed), i=1,
                         steps=[{"action": "MoveTo", "object": DS}, {"action": "Pick", "object": "apple"}])
            if looked is not None:
                r.looked_goals = set(looked)
            return make_loop(cls, T), r
        x, y = both(build, lambda lp, r: lp._look_at_untouched(r))
        assert x == y, (gs, w, placed, looked, completed)
        seen.add(y[1])
        x, y = both(build, lambda lp, r: lp._refind_after_pause(r))
        assert x == y, (gs, w, placed, looked, completed)
        seen.add(len(json.loads(y[0])))
    assert {"true", "false", 0, 1} <= seen


def test_guard_arrived_equals_the_reference(monkeypatch):
    fixes = [None, {"old": DT, "new": DS, "source": "operator", "detail": "1.0 m"},
             {"old": DT, "new": None, "source": "belief", "detail": "none"}]
    for on, fix in itertools.product((False, True), fixes):
        monkeypatch.setattr(sensed, "guard_arrived", lambda *a, _f=fix: dict(_f) if _f else None)

        def build(cls, T):
            return make_loop(cls, T, cfg={"reconcile": on}, _sense=lambda r: "sense", _save_world=None), make_run(T, world=W(arrived=DT))
        x, y = both(build, lambda lp, r: lp._guard_arrived(r, {"action": "Place", "object": DT}))
        assert x == y, (on, fix)


def test_guard_equals_the_reference():
    seen = set()
    for (a, o), w, hand, refind, prior, replan in itertools.product(
            STEPS[:6], WORLDS, (True, False), (True, False), (0, 1), ({"event": "replan"}, None)):
        def build(cls, T):
            lp = make_loop(cls, T, _holding=hand, _refind_dropped=refind, _sage_replan=replan,
                           _no_replan_end=lambda r, m: {"event": "no_replan_end", "msg": m},
                           _finish=lambda r, s, **k: {"event": "done", "status": s, **k})
            r = make_run(T, world=W(**w), i=2)
            if prior:
                r.seen_fail = {k: prior for k in [("pre", a, o, lp._precondition(r, {"action": a, "object": o}))]}
            return lp, r
        x, y = both(build, lambda lp, r: lp._guard(r, {"action": a, "object": o}))
        assert x == y, (a, o, w, hand, refind, prior, replan)
        seen.add(y[1])
    assert {"null", jd(NEXT), jd(STOP)} <= seen


class Res:
    def __init__(self, ok, device=None):
        self.ok, self.device = ok, device

    def to_legacy(self):
        return {"ok": self.ok}


def test_run_step_equals_the_reference(frozen):
    seen = set()
    binds = [None, Unbindable("no skill"), ("move_to", {"x": 1})]
    for (a, o), bind, flow, pre, res_ok, stop, vok, health in itertools.product(
            [("MoveTo", DT), ("Pick", "apple"), ("Find", "apple")], binds, (None, {"action": "Find", "object": "cup"}),
            (None, Res(False, "camera")), (True, False), (None, {"event": "done", "status": "aborted"}), (True, False), (False, True)):
        def build(cls, T):
            frozen["T"], frozen["pre"], frozen["res"] = T, pre, Res(res_ok)
            lp = make_loop(cls, T, cfg={"sensor_health": health}, flows={"on_step": flow}, _bind=bind, _cancelled=stop,
                           _announce="say", _log_fn="log", _effect=None,
                           _post_check=lambda *a_: [VerifyResult("sensor", vok, "seen" if vok else "not seen")],
                           _replan_or_fail=gen_of(STOP), _step_succeeded=gen_of(NEXT), _step_failed=gen_of(STOP))
            return lp, make_run(T, world=W(arrived=DT), i=1)
        x, y = both(build, lambda lp, r: lp._run_step(r, {"action": a, "object": o}))
        assert x == y, (a, o, bind, flow, pre, res_ok, stop, vok, health)
        seen.add((y[1], len(json.loads(y[0]))))
    assert len(seen) >= 4


def test_plan_ended_equals_the_reference():
    seen = set()
    for holds, unmet, cov, gap, replan, err, more, looked in itertools.product(
            (True, False), (None, [], [{"code": "absent", "evidence": {"observed": "no apple on the table"}}]), (False, True),
            (None, NEXT), ({"event": "replan"}, None), (None, RuntimeError("down")), (False, True), (False, True)):
        def build(cls, T):
            def sage(r, failed, reason):
                if more:
                    r.steps.append({"action": "Find", "object": "apple"})
                return replan
            lp = make_loop(cls, T, cfg={"request_coverage": cov}, _goals_hold=holds, _observe_goals=gen_of(unmet),
                           _coverage_gap=gen_of(gap), _missing=["apple on table"], _sage_replan=sage,
                           _no_replan_end=lambda r, m: {"event": "no_replan_end"}, _no_replan=lambda r, m: "no replan: " + m,
                           _finish=lambda r, s, **k: {"event": "done", "status": s, **k})
            r = make_run(T, goals=[G("on", "apple", "", DT), G("on", "cup", DS, CT)], world=W(arrived=DS), i=1,
                         steps=[{"action": "MoveTo", "object": DT}], replan_error=err)
            if looked:
                r.looked_goals = {("apple", DT)}                  # the untouched goal was looked at already: a replan instead
            return lp, r
        x, y = both(build, lambda lp, r: lp._plan_ended(r))
        assert x == y, (holds, unmet, cov, gap, replan, err, more, looked)
        seen.add(y[1])
    assert {jd(NEXT), jd(STOP), jd(DONE)} <= seen


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_coverage_gap_equals_the_reference(monkeypatch, lang):
    seen = set()
    answers = [RuntimeError("model down"), {"unserved": []}, {"unserved": [{"part": "water the plants", "why": "no_goal"}]}]
    for checked, ans, yn, stop, edit in itertools.product((False, True), answers, (("yes", True, ""), ("no", False, ""), ("", None, "")),
                                                         (None, {"event": "done", "status": "aborted"}), (NEXT, None)):
        def fake_ask(*a, _v=ans, **k):
            if isinstance(_v, Exception):
                raise _v
            return dict(_v)
        for mod in (old, execute):
            monkeypatch.setattr(mod, "ask_json", fake_ask)

        def build(cls, T):
            lp = make_loop(cls, T, _llm="llm", _sampling={}, _lab=None, _ask_yes_no=yn, _cancelled=stop,
                           _goal_edit=gen_of(edit), _repair_goals=gen_of(NEXT))
            r = make_run(T, task="put the apple on the table and water the plants", lang=lang, goals=[G("on", "apple", "", DT)],
                         completed=[{"action": "Place", "object": DT}])
            if checked:
                r.coverage_checked = True
            return lp, r
        x, y = both(build, lambda lp, r: lp._coverage_gap(r))
        assert x == y, (checked, ans, yn, stop, edit)
        seen.add(y[1])
    assert "null" in seen and (lang != "en" or {jd(NEXT), jd(STOP)} <= seen)


def test_execute_phase_equals_the_reference():
    seen = set()
    scripts = [[NEXT, DONE], [NEXT, NEXT, STOP], [DONE], [NEXT, "suspend", DONE], [STOP]]
    for run_goals, paused, cancel_at, pending, outs, resumed in itertools.product(
            (None, STOP), (False, True), (None, 1), (None, NEXT), scripts, (DONE, STOP)):
        def build(cls, T):
            seq, calls = list(outs), {"n": 0, "pending": 0}

            def apply_pending(r):
                calls["pending"] += 1
                return pending if calls["pending"] == 1 else None            # what the person said: once, before the first step

            def step(lp_, r, *a):
                v = seq.pop(0) if seq else DONE
                r.i += 1
                if v == "suspend":
                    r.suspended, v = 1, DONE
                return v

            def cancelled(r):
                calls["n"] += 1
                return {"event": "done", "status": "aborted"} if cancel_at == calls["n"] else None
            lp = make_loop(cls, T, flows={"on_run_goals": run_goals}, _pause_point=gen_of(paused), _cancelled=cancelled,
                           _apply_pending=gen_of(apply_pending), _resume_suspended=gen_of(resumed))
            lp._plan_ended = gen_of(lambda r: step(lp, r))
            lp._run_step = gen_of(lambda r, st: step(lp, r))
            r = make_run(T, world=W(arrived=DS, found="apple"), steps=[{"action": "Pick", "object": "apple"},
                                                                     {"action": "Place", "object": DT}],
                         pre_helped={"x"}, pre_fails={"apple": 1}, pre_doubted={DR})
            return lp, r
        x, y = both(build, lambda lp, r: lp._execute_phase(r))
        assert x == y, (run_goals, paused, cancel_at, pending, outs, resumed)
        seen.add(y[1])
    assert {"true", "false"} <= seen
