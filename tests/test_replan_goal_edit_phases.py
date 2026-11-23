"""Step S21: replan (_sage_replan, _replan_suffix, _no_replan, _no_replan_end, _replan_or_fail) and goal change with the task stack
(_apply_pending, _goal_edit, _llm_goal_change, _held_dropped, _new_command, _llm_new_task, _suspend_for, _resume_suspended,
_repair_goals) moved verbatim from assist_loop.py to the ReplanPhase mixin (phases/replan.py) and the GoalEditPhase mixin
(phases/goal_edit.py). These tests prove nothing changed: (1) both mixins are in AssistLoop's method resolution order and serve the
names, the loop file no longer defines them, ``_refine_plan`` (pinned by the S20 test) stays; (2) the method bodies are the same
syntax trees (and the same text but for the relative imports) as the reference copy (tests/legacy_ref/s21_replan_goal_edit.py);
(3) differential tests against that copy: the old and the new class give the same events, values, calls to their collaborators and
changes to the run state over grids of made-up goals, worlds, words, options and scripted collaborators (the real goal-change reader
runs in both); (4) a deliberate edit of the moved code is caught. Made-up places and words only (the fakebot robot, the sim ENV)."""

import ast
import copy
import inspect
import itertools
import json
import types
from pathlib import Path

import pytest

from legacy_ref import s21_replan_goal_edit as old
from robot_agent.core import run_control
from robot_agent.interact.run_manager import RunCancelled
from robot_agent.core.planning import assist_loop, goal_change, loop
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.phases import PHASE_MIXINS, goal_edit, replan
from robot_agent.core.planning.robot import RobotView

REPLAN_NAMES = ("_sage_replan", "_replan_suffix", "_no_replan", "_no_replan_end", "_replan_or_fail")
EDIT_NAMES = ("_apply_pending", "_goal_edit", "_llm_goal_change", "_held_dropped", "_new_command", "_llm_new_task", "_suspend_for",
              "_resume_suspended", "_repair_goals")
NAMES = REPLAN_NAMES + EDIT_NAMES
STAY = ("_refine_plan", "_obs", "_ground_all", "_check", "_execute_phase", "_finish", "_llm_failed")
LOOP_FILE = Path(assist_loop.__file__)
DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"


class OldLoop(old.RefReplan, old.RefGoalEdit, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixins_are_listed_and_serve_the_names():
    assert replan.ReplanPhase in PHASE_MIXINS and goal_edit.GoalEditPhase in PHASE_MIXINS
    mro = AssistLoop.__mro__
    for mixin, names in ((replan.ReplanPhase, REPLAN_NAMES), (goal_edit.GoalEditPhase, EDIT_NAMES)):
        assert mro.index(mixin) < mro.index(assist_loop.WhichOne)
        for n in names:
            assert n not in vars(AssistLoop), n
            assert n in vars(mixin), n
            assert getattr(AssistLoop, n) is not None


def test_the_loop_file_does_not_define_them_any_more():
    src = LOOP_FILE.read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n


def test_what_stays_is_still_in_the_loop_file():
    src = LOOP_FILE.read_text()
    for n in STAY:
        assert f"def {n}(" in src or n in vars(AssistLoop) or n in dir(AssistLoop), n
    assert "def _refine_plan(" in src and "_refine_plan" in vars(AssistLoop)


def test_a_name_patched_on_the_class_still_wins(monkeypatch):
    monkeypatch.setattr(AssistLoop, "_no_replan", staticmethod(lambda r, m: "patched"))
    monkeypatch.setattr(AssistLoop, "_held_dropped", lambda self, r, ch: "patched-too")
    lp = AssistLoop.__new__(AssistLoop)
    assert lp._no_replan(None, "m") == "patched" and lp._held_dropped(None, None) == "patched-too"


def test_the_moved_modules_are_about_the_designed_size():
    assert len(Path(replan.__file__).read_text().splitlines()) <= 120      # 72 verbatim lines + docstring and imports
    assert len(Path(goal_edit.__file__).read_text().splitlines()) <= 220   # 171 verbatim lines + docstring and imports


def test_the_end_markers_are_the_loops_own_objects():
    assert replan._NEXT is loop._NEXT and replan._STOP is loop._STOP
    assert goal_edit._NEXT is loop._NEXT and goal_edit._STOP is loop._STOP and goal_edit._DONE is loop._DONE
    assert assist_loop._NEXT is loop._NEXT and old._NEXT is loop._NEXT and old._DONE is loop._DONE


def test_the_hooks_and_services_are_still_called_by_the_moved_code():
    src = Path(replan.__file__).read_text() + Path(goal_edit.__file__).read_text()
    for call in ('self.options.query("no_replan", r, failed)', 'self.options.transform("on_replan_findings"',
                 'self.options.observe("on_goal_edit", r, text)', 'self.options.observe("on_goal_reread"',
                 'self.options.decide_flow("on_goal_unapplied", r, turn)', 'self.options.decide("on_goal_replan", r)'):
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


@pytest.mark.parametrize("mixin,ref,names", [(replan, "RefReplan", REPLAN_NAMES), (goal_edit, "RefGoalEdit", EDIT_NAMES)])
def test_method_bodies_are_the_reference_bodies(mixin, ref, names):
    cls = "ReplanPhase" if mixin is replan else "GoalEditPhase"
    new, rf = _methods(mixin.__file__, cls), _methods(old.__file__, ref)
    assert set(names) == set(new) == set(rf)
    for n in names:
        assert _dump(new[n]) == _dump(rf[n]), n


@pytest.mark.parametrize("mixin,ref,cls", [(replan, "RefReplan", "ReplanPhase"), (goal_edit, "RefGoalEdit", "GoalEditPhase")])
def test_the_moved_text_is_the_reference_text(mixin, ref, cls):
    """Beyond the syntax trees: the reference class body, comments and docstrings included, is the new class body; the only differing
    lines are the relative imports inside the method bodies (one package level deeper) and one section comment."""
    def body(path, name):
        lines = Path(path).read_text().splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith(f"class {name}")) + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("class ")), len(lines))
        return lines[start:end]
    new, rf = body(mixin.__file__, cls), body(old.__file__, ref)
    if cls == "GoalEditPhase":
        assert new[0].lstrip().startswith("# ──") and "what the person says" in new[0]
        new = new[1:]
    new, rf = [x for x in new], [x for x in rf]
    while rf and not rf[-1].strip():
        rf.pop()
    while new and not new[-1].strip():
        new.pop()
    assert len(new) == len(rf)
    diff = [(a, b) for a, b in zip(new, rf) if a != b]
    assert len(diff) >= 1 and all("import" in a and "import" in b for a, b in diff), diff


def test_the_reference_is_the_old_loop_text():
    """The reference bodies are the old loop text (pinned: the first and the last line of each moved block)."""
    ref = Path(old.__file__).read_text().splitlines()
    for line in ("    def _sage_replan(self, r, failed, reason) -> Optional[dict]:", "        return _NEXT",
                 "    def _apply_pending(self, r):", "        return out"):
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
    if isinstance(x, goal_change.GoalChange):
        return {"GoalChange": _plain(vars(x))}
    return x


def jd(x):
    return json.dumps(_plain(x), sort_keys=True, default=lambda o: type(o).__name__)


class Opts:
    """The option dispatcher stub: queries / decisions answer from a script; every call is recorded."""

    def __init__(self, T, queries=None, decisions=None, unapplied=None):
        self.T, self.queries, self.decisions, self.unapplied = T, queries or {}, decisions or {}, unapplied

    def query(self, name, *a, **k):
        self.T.append(("query", name, jd(a[1:]) if a else ""))
        return self.queries.get(name, k.get("default"))

    def transform(self, name, value, *a):
        self.T.append(("transform", name, jd(value)))
        return value

    def observe(self, name, *a):
        self.T.append(("observe", name, jd(a[1:])))

    def decide(self, name, *a):
        self.T.append(("decide", name))
        return self.decisions.get(name)

    def decide_flow(self, name, r, turn):
        self.T.append(("decide_flow", name, jd(turn.text)))
        if self.unapplied is not None:
            turn.change = self.unapplied(turn)
        if False:
            yield
        return self.decisions.get("flow")


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def make_run(env, T, **kw):
    places = Places(env, openable=_ROBOT.openable)
    r = types.SimpleNamespace(task="put the apple on the table", lang="en", dialogue=[("user", "go")], places=places, world=W(),
                              goals=[], steps=[], planner=None, memory={}, restated="r", goal_txt="g", completed=[], placed={},
                              world_at_start=W(), i=1, replans=0, node="n", saved_goals=[], suspended=0,
                              llm_budget=types.SimpleNamespace(used=0), uncertified=[], replan_error=None,
                              run=types.SimpleNamespace(replans=[], obs=""), announcer=types.SimpleNamespace(announce=lambda k: "say:" + k))
    r.ev = lambda name, **a: {"event": name, **a}
    r.__dict__.update(kw)
    return r


def make_loop(cls, T, cfg=None, queries=None, decisions=None, unapplied=None, **script):
    lp = cls.__new__(cls)
    lp.cfg = {"max_replans": 2, **(cfg or {})}
    lp.options = Opts(T, queries, decisions, unapplied)
    lp.robot, lp._namemap = _ROBOT, _ROBOT.adapter.namemap
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
    """Run the same scenario on the old and the new class; the two results (events, value, trace, run state, belief)."""
    out = []
    for cls in (OldLoop, AssistLoop):
        T = []
        lp, r = build(cls, T)
        try:
            evs, val = drive(call(lp, r))
        except Exception as e:                                   # noqa: BLE001
            evs, val = [], ("raised", type(e).__name__, str(e))
        state = {k: v for k, v in sorted(vars(r).items()) if k not in ("ev", "places", "robot", "announcer", "llm_budget")}
        out.append((jd(evs), jd(val), T, jd(state), jd(r.world.to_dict()) if hasattr(r.world, "to_dict") else ""))
    return out


@pytest.fixture
def stack(monkeypatch):
    """The run-control module functions the moved code calls at call time; they record into the trace of the current scenario."""
    cur = {"T": [], "pending": [], "outranks": False, "frame": None, "cancel": False}

    class Run:
        def take_pending(self):
            return list(cur["pending"])

        def outranked_by(self, role):
            cur["T"].append(("outranked_by", role))
            return cur["outranks"]

    run = Run()
    frame = types.SimpleNamespace(goals=[G("on", "cup", "", DT)], to_dict=lambda: {"frame": 1})
    monkeypatch.setattr(run_control, "current_run", lambda: run if cur["pending"] is not None else None)
    monkeypatch.setattr(run_control, "suspend_task", lambda task, goals, achieved, belief, reason="": (
        cur["T"].append(("suspend_task", task, jd(goals), jd(achieved), jd(belief), reason)) or (frame if cur["frame"] else None)))
    monkeypatch.setattr(run_control, "resume_task", lambda: (cur["T"].append(("resume_task",)) or (frame if cur["frame"] else None)))
    monkeypatch.setattr(run_control, "mark_resumed", lambda: cur["T"].append(("mark_resumed",)))
    cur["run"], cur["frame_obj"] = run, frame
    return cur


def both_s(stack, build, call):
    """``both`` where the run-control stubs record into the scenario's trace."""
    def b(cls, T):
        stack["T"] = T
        return build(cls, T)
    return both(b, call)


# ---- replan ----
def test_sage_replan_equals_the_reference():
    seen = set()
    PL = [{"action": "MoveTo", "object": DT}, {"action": "Place", "object": DT}]
    for no_replan, replans, compiled, bad_first, bad_second, err, check_probs in itertools.product(
            (False, True), (0, 1, 2), (False, True), ([], ["x"]), ([], ["y"]), (None, RuntimeError("down")),
            ([], [(1, "p1", True), (2, "p2", False)])):
        def build(cls, T):
            calls = iter([(copy.deepcopy(PL), "e1", list(bad_first)), (copy.deepcopy(PL[:1]), "e2", list(bad_second))])

            def suffix(r, failed, ers):
                out = next(calls)
                if err is not None:
                    r.replan_error = err
                return out
            lp = make_loop(cls, T, queries={"no_replan": no_replan}, _replan_suffix=suffix, _check=lambda *a: list(check_probs))
            r = make_run(env_(), T, goals=[G("on", "apple", "", DT)], steps=[{"action": "Find", "object": "apple"}] * 4,
                         replans=replans, completed=[{"action": "MoveTo", "object": DS}], world_at_start=W(), compiled=compiled,
                         goal_txt="goal text", world=W(holding="apple", arrived=DS))
            return lp, r
        a, b = both(build, lambda lp, r: lp._sage_replan(r, {"action": "Pick", "object": "apple"}, "grasp lost"))
        assert a == b, (no_replan, replans, compiled, bad_first, bad_second, err, check_probs)
        st = json.loads(b[3])
        seen.add((b[1][:40], len(b[2]), st["replans"], len(st["steps"])))
    assert len(seen) >= 12


def env_():
    from fakes import load_env
    return load_env()


def test_replan_suffix_equals_the_reference():
    seen = set()
    for raises, suffix, cancel in itertools.product((False, True), ([], [{"action": "Find", "object": "apple"}]), (False, True)):
        def build(cls, T):
            def plan_call(r, name, *a, **k):
                if raises:
                    raise (RunCancelled("c") if cancel else RuntimeError("down"))
                return copy.deepcopy(suffix), {}
            lp = make_loop(cls, T, _plan_call=plan_call, _obs="OBS", _tidy=lambda r, s: (list(s), ["n"]),
                           _search_steer=lambda r, s: list(s), _ground_all=lambda r, s: [dict(x, g=1) for x in s],
                           _repair_suffix=lambda r, s: list(s) + [{"action": "Wait", "object": ""}],
                           _hard_findings=lambda r, s: ["f"] if len(s) > 1 else [])
            return lp, make_run(env_(), T, goals=[G("on", "apple", "", DT)], completed=[{"action": "Find", "object": "x"}])
        a, b = both(build, lambda lp, r: lp._replan_suffix(r, {"action": "Pick", "object": "apple"}, "E"))
        assert a == b, (raises, suffix, cancel)
        seen.add((b[1][:30], b[3][-40:]))
    assert len(seen) >= 3


def test_no_replan_family_equals_the_reference():
    seen = set()
    for bad, err, msg in itertools.product((None, [], ["a"], ["a", "b", "c", "d"]), (None, RuntimeError("down")), ("m", "")):
        r = types.SimpleNamespace(uncertified=bad, replan_error=err) if bad is not None else types.SimpleNamespace(replan_error=err)
        assert old.RefReplan._no_replan(r, msg) == AssistLoop._no_replan(r, msg)

        def build(cls, T):
            return (make_loop(cls, T, _llm_failed=lambda r, e, what="": {"event": "done", "why": what, "e": str(e)},
                              _finish=lambda r, status, msg="", **kw: {"event": "done", "status": status, "msg": msg}),
                    make_run(env_(), T, uncertified=bad, replan_error=err))
        a, b = both(build, lambda lp, r: lp._no_replan_end(r, msg))
        assert a == b
        seen.add(b[1][:50])
    assert len(seen) >= 4


def test_replan_or_fail_equals_the_reference():
    seen = set()
    for out in (None, {"event": "replan"}):
        def build(cls, T):
            return (make_loop(cls, T, _sage_replan=out, _no_replan_end=lambda r, m: {"event": "done", "msg": m}),
                    make_run(env_(), T))
        a, b = both(build, lambda lp, r: lp._replan_or_fail(r, {"action": "x"}, "why", "fail msg"))
        assert a == b
        seen.add((a[0], b[1]))
    assert len(seen) == 2


# ---- goal change ----
TEXTS = ["put the apple on the counter", "forget the apple", "actually the counter instead", "put it back on the desk", "bring the cup to the desk",
         "and also bring the cup to the dining table", "blah blah", "", "put the apple in the drawer", "no need for the orange, put the apple on the desk",
         "please bring the cup to the study", "take the pear to the dining table"]
GOALS = [[G("on", "apple", "", DT)], [G("on", "apple", DS, DT), G("in", "orange", "", DR)], [], [G("on", "cup", "", DT)]]


def test_goal_edit_equals_the_reference():
    seen = set()
    for text, intent, goals, holding, drop_held, reach, llm, unapplied in itertools.product(
            TEXTS, ("modify", "add", "drop", None), GOALS, ("", "apple"), ("put_back", "place_here"), (False, True),
            (None, "gc"), (None, "fix")):
        if (intent == "add" and unapplied) or (llm and unapplied):
            continue

        def build(cls, T):
            def llm_fn(r, t):
                return goal_change.GoalChange(goals=[G("on", "apple", "", CT)], added=[G("on", "apple", "", CT)], how="llm") if llm else None
            fix = (lambda turn: goal_change.GoalChange(goals=[G("on", "pear", "", DT)], added=[G("on", "pear", "", DT)], how="ask")) if unapplied else None
            lp = make_loop(cls, T, cfg={"drop_held": drop_held}, unapplied=fix, _goals_hold=lambda *a, **k: reach, _llm_goal_change=llm_fn,
                           _in_lang=lambda t, lang: "L:" + t)
            return lp, make_run(env_(), T, goals=copy.deepcopy(goals), world=W(holding=holding, arrived=DS),
                                completed=[{"action": "MoveTo", "object": DT}])
        a, b = both(build, lambda lp, r: lp._goal_edit(r, {"intent": intent, "text": text}))
        assert a == b, (text, intent, goals, holding, drop_held, reach, llm, unapplied)
        seen.add((json.loads(a[0])[0]["applied"] if json.loads(a[0]) else None, "put_back" in a[3] or "drop" in a[0], b[1][:12]))
    assert len(seen) >= 4


def test_goal_edit_unapplied_flow_stop_equals_the_reference():
    def build(cls, T):
        return make_loop(cls, T, decisions={"flow": loop._STOP}, _llm_goal_change=None, _in_lang=lambda t, lang: t), make_run(
            env_(), T, goals=copy.deepcopy(GOALS[0]))
    a, b = both(build, lambda lp, r: lp._goal_edit(r, {"intent": "modify", "text": "blah blah"}))
    assert a == b and b[1] == jd(loop._STOP)


def test_held_dropped_equals_the_reference():
    seen = set()
    for holding, drop_held, kept, removed, completed, arrived in itertools.product(
            ("", "apple"), ("put_back", "place_here", None), (False, True), ([], [G("on", "apple", DS, DT)], [G("on", "apple", "", DT)],
                                                                         [G("on", "pear", "", DT)]),
            ([], [{"action": "MoveTo", "object": DR}]), ("", DS)):
        def build(cls, T):
            lp = make_loop(cls, T, cfg={"drop_held": drop_held})
            return lp, make_run(env_(), T, world=W(holding=holding, arrived=arrived), completed=completed)
        ch = goal_change.GoalChange(goals=[G("on", "apple", "", CT)] if kept else [], removed=removed)
        a, b = both(build, lambda lp, r: lp._held_dropped(r, ch))
        assert a == b
        seen.add(b[1])
    assert len(seen) >= 3


def test_llm_goal_change_and_new_task_equal_the_reference():
    seen = set()
    for status, q, goals_out, raises in itertools.product(("clear", "ambiguous"), (None, "which one?"), (
            [G("on", "apple", "", CT)], [G("on", "apple", "", DT)], []), (None, RuntimeError("x"))):
        def build(cls, T):
            def understand(*a, **k):
                if raises:
                    raise raises
                return {"goals": ["raw"], "status": status}
            lp = make_loop(cls, T, _understand=understand, _ground_goals=lambda *a, **k: (copy.deepcopy(goals_out), q))
            return lp, make_run(env_(), T, goals=copy.deepcopy(GOALS[0]), dialogue=[("user", "go")])
        a, b = both(build, lambda lp, r: lp._llm_goal_change(r, "put it on the counter"))
        assert a == b
        c, d = both(build, lambda lp, r: lp._llm_new_task(r, "bring the cup to the desk"))
        assert c == d
        seen.add((b[1][:40], d[1][:40]))
    assert len(seen) >= 3


def test_new_command_equals_the_reference(stack):
    seen = set()
    for text, goals, outranks, answer, cancelled, role in itertools.product(
            ["bring the cup to the desk", "blah", "put the apple on the dining table", "forget it"], GOALS[:2] + [[]], (False, True),
            ("yes", "no", "do it first", "right away", ""), (False, True), ("owner", None)):
        stack["outranks"], stack["frame"] = outranks, True

        def build(cls, T):
            lp = make_loop(cls, T, _llm_new_task=None, _ask=lambda *a, **k: answer, _log_fn=lambda i: "log", _in_lang=lambda t, lang: "L:" + t,
                           _cancelled=lambda r: {"event": "done", "status": "stopped"} if cancelled else None,
                           _suspend_for=lambda r, new, text: iter([r.ev("goal_change", suspended=True, goals=new)]))
            lp.yes_no = lambda ans: (True if ans == "yes" else (False if ans == "no" else None), "x")
            return lp, make_run(env_(), T, goals=copy.deepcopy(goals))
        a, b = both_s(stack, build, lambda lp, r: lp._new_command(r, stack["run"], {"intent": "new", "text": text, "role": role}))
        assert a == b, (text, goals, outranks, answer, cancelled, role)
        seen.add((b[1][:12], len(json.loads(a[0])), len(a[2])))
    assert len(seen) >= 5


def test_suspend_and_resume_equal_the_reference(stack):
    seen = set()
    new = [G("on", "cup", "", DT)]
    for frame, holding, here, reach, text in itertools.product((False, True), ("", "apple"), ("", DS, DR), (False, True), ("a", "b")):
        stack["frame"] = frame

        def build(cls, T):
            lp = make_loop(cls, T, _goals_hold=lambda *a, **k: reach, _in_lang=lambda t, lang: "L:" + t)
            return lp, make_run(env_(), T, goals=[G("on", "apple", "", DT), G("in", "orange", "", DR)], world=W(holding=holding, arrived=here))
        a, b = both_s(stack, build, lambda lp, r: lp._suspend_for(r, new, text))
        assert a == b, (frame, holding, here, reach)
        seen.add((a[0][:60], len(a[2])))

        def build2(cls, T):
            lp = make_loop(cls, T, _goals_hold=lambda *a, **k: reach, _in_lang=lambda t, lang: "L:" + t,
                           _reconcile=lambda r, when: {"event": "reconciled"} if here else None,
                           _repair_goals=lambda r, said: iter([{"event": "repaired", "said": said}]))
            return lp, make_run(env_(), T, goals=new, world=W(holding=holding, arrived=here), saved_goals=[[G("on", "pear", "", DT)]], suspended=1)
        c, d = both_s(stack, build2, lambda lp, r: lp._resume_suspended(r))
        assert c == d, (frame, holding, here, reach)
        seen.add(("r", d[1][:20], len(d[2]), len(json.loads(d[0]))))
    assert len(seen) >= 5


def test_repair_goals_equals_the_reference():
    seen = set()
    for reach, decision, out in itertools.product((False, True), (None, ["sub"]), (loop._NEXT, loop._STOP)):
        def build(cls, T):
            def replan_or_fail(r, failed, reason, fail_msg):
                T.append(("sub", jd(r.replan_subgoals)))
                yield {"event": "replan", "reason": reason, "msg": fail_msg, "failed": failed}
                return out
            lp = make_loop(cls, T, decisions={"on_goal_replan": decision}, _goals_hold=lambda *a, **k: reach, _replan_or_fail=replan_or_fail)
            return lp, make_run(env_(), T, goals=copy.deepcopy(GOALS[1]), steps=[1, 2, 3], i=2)
        a, b = both(build, lambda lp, r: lp._repair_goals(r, "said it"))
        assert a == b, (reach, decision, out)
        seen.add((b[1][:20], a[0][:30], b[3][-40:]))
    assert len(seen) >= 3


def test_apply_pending_equals_the_reference(stack):
    seen = set()
    evs_pool = [[], [{"intent": "modify", "text": "x"}], [{"intent": "new", "text": "y"}, {"intent": "drop", "text": "z"}],
                [{"intent": "modify", "text": "a"}, {"intent": "add", "text": "b"}]]
    for events, outs, no_run in itertools.product(evs_pool, itertools.product((None, loop._NEXT, loop._STOP), repeat=2), (False, True)):
        stack["pending"] = None if no_run else list(events)

        def build(cls, T):
            it = iter(outs)

            def edit(r, e):
                T.append(("edit", e["intent"]))
                yield {"event": "goal_change", "text": e["text"]}
                return next(it, None)
            lp = make_loop(cls, T, _goal_edit=edit, _new_command=lambda r, run, e: edit(r, e),
                           _repair_goals=lambda r, said: iter([{"event": "repaired", "said": said}]))
            return lp, make_run(env_(), T)
        a, b = both_s(stack, build, lambda lp, r: lp._apply_pending(r))
        assert a == b, (events, outs, no_run)
        seen.add((b[1][:20], len(json.loads(a[0]))))
    assert len(seen) >= 5


# -- (4) deliberate edits of the moved code are caught -------------------------------------------------------------------------
def test_an_edit_of_the_moved_code_is_caught(monkeypatch):
    """The comparison bites: a changed ``_no_replan`` (it ignores the gate's reasons) differs from the reference."""
    r = types.SimpleNamespace(uncertified=["bad plan"])
    assert old.RefReplan._no_replan(r, "m") == AssistLoop._no_replan(r, "m") != "m"
    monkeypatch.setattr(replan.ReplanPhase, "_no_replan", staticmethod(lambda r, m: m))
    assert old.RefReplan._no_replan(r, "m") != AssistLoop._no_replan(r, "m")


def test_a_changed_held_dropped_is_caught(monkeypatch):
    ch = goal_change.GoalChange(goals=[], removed=[G("on", "apple", DS, DT)])
    r = make_run(env_(), [], world=W(holding="apple", arrived=DR))
    lp_old, lp_new = make_loop(OldLoop, []), make_loop(AssistLoop, [])
    assert lp_old._held_dropped(r, ch) == lp_new._held_dropped(r, ch) is not None
    monkeypatch.setattr(goal_edit.GoalEditPhase, "_held_dropped", lambda self, r, ch: None)
    assert lp_old._held_dropped(r, ch) != lp_new._held_dropped(r, ch)


@pytest.mark.parametrize("name", NAMES)
def test_every_moved_name_has_the_same_signature_as_the_reference(name):
    sig = lambda f: [(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()]       # noqa: E731
    ref = old.RefReplan if name in REPLAN_NAMES else old.RefGoalEdit
    mix = replan.ReplanPhase if name in REPLAN_NAMES else goal_edit.GoalEditPhase
    assert sig(getattr(ref, name)) == sig(getattr(mix, name))
