"""Step S20: generate-and-certify (_plan_phase, _generate_and_certify, _plan_call, _subgoals, _lab_subgoals, _has_blocks, _gate_repair,
_restore_repair, _gate_repair_run, _repair_suffix, _tidy, _blocking, _untouched, _hard_findings) moved verbatim from assist_loop.py to
the CertifyPhase mixin in phases/certify.py. These tests prove nothing changed: (1) the mixin is in AssistLoop's method resolution
order, serves the fourteen names, the loop file no longer defines them, what stays (``_obs``, ``_ground_all``, ``_refine_plan``,
``_check``) is still in the loop file, a name patched on the class still wins; (2) the method bodies are the same syntax trees
(the relative imports inside two bodies differ only by one package level), and the class body the same text, as the reference copy
(tests/legacy_ref/s20_certify.py); (3) differential tests against that copy: the old and the new class give the same events, values,
calls to their collaborators and changes to the run state over grids of made-up plans, worlds, goals, options and scripted
collaborators (the real checker, ``plan_tidy`` and ``plan_repair`` run in both); (4) the grids reach the interesting branches, so the
comparison bites, and a deliberate edit of the moved code is caught. Made-up places and words only (the fakebot robot, the sim ENV)."""

import ast
import copy
import itertools
import json
import random
import types
from pathlib import Path

import pytest

from legacy_ref import s20_certify as old
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.phases import PHASE_MIXINS, certify
from robot_agent.core.planning.robot import RobotView
from robot_agent.core.planning.understand import LLMBudgetExceeded

NAMES = ("_plan_phase", "_generate_and_certify", "_plan_call", "_subgoals", "_lab_subgoals", "_has_blocks", "_gate_repair",
         "_restore_repair", "_gate_repair_run", "_repair_suffix", "_tidy", "_blocking", "_untouched", "_hard_findings")
STAY = ("_obs", "_ground_all", "_refine_plan", "_check")
LOOP_FILE = Path(assist_loop.__file__)
MIXIN_FILE = Path(certify.__file__)
DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"


class OldLoop(old.RefCertify, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert certify.CertifyPhase in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(certify.CertifyPhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(AssistLoop), n
        assert n in vars(certify.CertifyPhase), n
        assert getattr(AssistLoop, n) is not None


def test_the_loop_file_does_not_define_them_any_more():
    src = LOOP_FILE.read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n


def test_what_stays_is_still_in_the_loop_file():
    src = LOOP_FILE.read_text()
    for n in STAY:
        assert f"def {n}(" in src, n
        assert n in vars(AssistLoop), n


def test_a_name_patched_on_the_class_still_wins(monkeypatch):
    monkeypatch.setattr(AssistLoop, "_tidy", lambda self, r, steps: ("patched", steps))
    assert AssistLoop.__new__(AssistLoop)._tidy(None, [1]) == ("patched", [1])


def test_the_moved_module_is_about_the_designed_size():
    assert len(MIXIN_FILE.read_text().splitlines()) <= 330       # 291 verbatim lines + the docstring and imports


def test_the_two_hooks_are_still_called_by_the_moved_code():
    src = MIXIN_FILE.read_text()
    assert 'self.options.transform("on_plan", steps, r)' in src
    assert 'self.options.transform("on_plan_findings", self._hard_findings(r, steps), r, steps)' in src


def test_the_loc_actions_are_the_loops_own_value():
    assert certify._LOC_ACTIONS == assist_loop._LOC_ACTIONS == old._LOC_ACTIONS


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
    new, ref = _methods(MIXIN_FILE, "CertifyPhase"), _methods(old.__file__, "RefCertify")
    assert set(NAMES) == set(new) == set(ref)
    for n in NAMES:
        assert _dump(new[n]) == _dump(ref[n]), n


def test_the_moved_text_is_the_reference_text():
    """Beyond the syntax trees: the reference class body, comments and docstrings included, is the new class body; the only
    differing lines are the relative imports inside the method bodies (one package level deeper)."""
    def body(path, cls):
        lines = Path(path).read_text().splitlines()
        return lines[next(i for i, l in enumerate(lines) if l.startswith(f"class {cls}")) + 1:]
    new, ref = body(MIXIN_FILE, "CertifyPhase"), body(old.__file__, "RefCertify")
    assert len(new) == len(ref)
    diff = [(a, b) for a, b in zip(new, ref) if a != b]
    assert len(diff) >= 5 and all("import" in a and "import" in b for a, b in diff), diff


def test_the_moved_text_is_the_text_of_the_loop_before_the_step():
    """The reference body is itself the old loop text (pinned: the first and the last line of the moved block)."""
    ref = Path(old.__file__).read_text().splitlines()
    assert "    def _plan_phase(self, r):" in ref and "        return out" in ref


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
_ROBOT = RobotView.load("fakebot")


def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(str(v) for v in x)
    return x


def jd(x):
    return json.dumps(_plain(x), sort_keys=True, default=lambda o: type(o).__name__)


class Opts:
    """The option dispatcher stub: queries answer from a script, transforms record and return the steps; every call is recorded."""

    def __init__(self, T, queries=None, extra=None):
        self.T, self.queries, self.extra = T, queries or {}, extra or {}

    def query(self, name, *a, **k):
        self.T.append(("query", name, jd(a[1:]) if a else ""))
        return self.queries.get(name, k.get("default"))

    def transform(self, name, value, *a):
        self.T.append(("transform", name, jd(value)))
        return self.extra.get(name, lambda v: v)(value)


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def make_run(env, T, **kw):
    places = Places(env, openable=_ROBOT.openable)
    r = types.SimpleNamespace(task="put the apple on the table", lang="en", dialogue=[], places=places, world=W(), goals=[],
                              steps=[], planner=None, llm_budget=types.SimpleNamespace(used=0, take=lambda what="": T.append(("take", what))),
                              restated="put the apple on the table", goal_txt="g", run=types.SimpleNamespace(plan_meta=None),
                              robot=_ROBOT, plan_only=False, certified=False, gate_findings=[], searched=[], seen_far=[],
                              uncertified=[], announcer=types.SimpleNamespace(announce=lambda k: "say:" + k))
    r.ev = lambda name, **a: {"event": name, **a}
    r.__dict__.update(kw)
    return r


def make_loop(cls, T, cfg=None, queries=None, extra=None, **script):
    lp = cls.__new__(cls)
    lp.cfg = {"plan_tidy": True, **(cfg or {})}
    lp.options = Opts(T, queries, extra)
    lp.robot, lp._namemap = _ROBOT, _ROBOT.adapter.namemap
    lp._lab = lambda flag, **info: T.append(("lab", flag, jd(info)))
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


STEP_POOL = [
    ("MoveTo", DR), ("MoveTo", DT), ("MoveTo", DS), ("MoveTo", CT), ("MoveTo", "@kitchen"), ("MoveTo", "nowhere"),
    ("Open", DR), ("Open", CT), ("Close", DR), ("Find", "apple"), ("Find", "orange"), ("Pick", "apple"), ("Pick", "orange"),
    ("Place", DT), ("Place", DS), ("Place", "@kitchen"), ("PutIn", DR), ("PutIn", DT), ("Teleport", "apple"), ("Wait", ""),
]
WORLDS = [{}, {"holding": "apple"}, {"arrived": DR}, {"arrived": DT}, {"opened": [DR]}, {"holding": "apple", "arrived": DT}]
GOAL_SETS = [
    [], [G("on", "apple", "", DT)], [G("on", "apple", DS, DT)], [G("open", DR, "", DR)], [G("in", "orange", "", DR)],
    [G("on", "apple", "", DT), G("in", "orange", "", DR)], [G("on", "cup", DR, DS)], [dict(G("on", "cup", "", DT), from_any=True)],
]


def _plans(rng, n):
    for _ in range(n):
        plan = []
        for _ in range(rng.randint(0, 7)):
            a, o = rng.choice(STEP_POOL)
            st = {"action": a, "object": o}
            if rng.random() < 0.08:
                st["grounded"] = False
            if rng.random() < 0.04:
                st = {"skill": "custom_skill"}
            plan.append(st)
        yield plan


def test_untouched_and_hard_findings_equal_the_reference(env):
    rng, seen = random.Random(5), set()
    for plan in _plans(rng, 500):
        for world, goals in itertools.product(rng.sample(WORLDS, 2), rng.sample(GOAL_SETS, 2)):
            assert old.RefCertify._untouched(goals, plan) == AssistLoop._untouched(goals, plan)
            for start in (0, 2):
                def build(cls, T):
                    return make_loop(cls, T), make_run(env, T, world=W(**world), goals=copy.deepcopy(goals))
                a, b = both(build, lambda lp, r: lp._hard_findings(r, copy.deepcopy(plan), start))
                assert a == b, (plan, world, start)
                seen.update(t.split(": ", 1)[-1][:22] for t in json.loads(b[1]))
    assert len(seen) >= 4, sorted(seen)                          # no action / not a place / unbindable all reached


def test_blocking_and_tidy_equal_the_reference(env):
    rng, seen = random.Random(7), set()
    for plan in _plans(rng, 300):
        for world, goals, per_goal, plan_tidy_on in itertools.product(
                rng.sample(WORLDS, 2), rng.sample(GOAL_SETS, 3), (False, True), (True, False)):
            def build(cls, T):
                return make_loop(cls, T, cfg={"plan_tidy": plan_tidy_on}), make_run(env, T, world=W(**world), goals=copy.deepcopy(goals))
            a, b = both(build, lambda lp, r: lp._blocking(r, copy.deepcopy(plan), per_goal))
            assert a == b, (plan, world, goals, per_goal)
            a, b = both(build, lambda lp, r: lp._tidy(r, copy.deepcopy(plan)))
            assert a == b, (plan, world, goals, plan_tidy_on)
            seen.add((len(json.loads(b[1])[0]) if json.loads(b[1])[0:1] else 0, len(json.loads(b[1])[1:] and json.loads(b[1])[1])))
    assert len(seen) >= 3


def test_the_hooks_see_the_same_arguments_and_their_result_is_used(env):
    extra = {"on_plan": lambda steps: [s for s in steps if s.get("action") != "Find"], "on_plan_findings": lambda f: f + ["extra finding"]}
    plan = [{"action": "Find", "object": "apple"}, {"action": "Pick", "object": "apple"}, {"action": "Teleport", "object": "x"}]

    def build(cls, T):
        return make_loop(cls, T, extra=extra), make_run(env, T, goals=[G("on", "apple", "", DT)])
    a, b = both(build, lambda lp, r: lp._blocking(r, copy.deepcopy(plan)))
    assert a == b and "extra finding" in b[1]
    a, b = both(build, lambda lp, r: lp._tidy(r, copy.deepcopy(plan)))
    assert a == b and "Find" not in b[1]
    assert [t[1] for t in b[2] if t[0] == "transform"] == ["on_plan"]


def test_plan_call_equals_the_reference(env):
    seen = set()
    for hooked, subgoals, kwsub, extra_calls, raises in itertools.product((True, False), (None, [], ["a", "b"]), (True, False),
                                                                          (1, 3, None), (False, True)):
        def build(cls, T):
            class Planner:
                def generate_plan(self, *a, **k):
                    T.append(("generate_plan", jd(a), jd(k)))
                    if raises:
                        raise RuntimeError("down")
                    return ["raw"], ({"llm_calls": extra_calls} if extra_calls else "meta")
            if hooked and kwsub:
                Planner.generate_plan = lambda self, restated, obs, edits, subgoals=None, budget=None, **kw: (
                    T.append(("gp", jd((restated, obs, edits, subgoals, budget is not None)))) or (["raw"], {"llm_calls": extra_calls}))
            elif hooked:
                Planner.generate_plan = lambda self, restated, obs, edits, budget=None: (
                    T.append(("gp", jd((restated, budget is not None)))) or (["raw"], {"llm_calls": extra_calls}))
            elif kwsub:
                Planner.generate_plan = lambda self, restated, obs, edits, subgoals=None: (
                    T.append(("gp", jd((restated, subgoals)))) or (["raw"], {"llm_calls": extra_calls}))
            return make_loop(cls, T), make_run(env, T, planner=Planner(), llm_budget=types.SimpleNamespace(
                used=0, take=lambda what="": T.append(("take", what))))
        a, b = both(build, lambda lp, r: lp._plan_call(r, "generate_plan", "restated", "obs", [], what="plan", subgoals=subgoals))
        assert a == b, (hooked, subgoals, kwsub, extra_calls, raises)
        seen.add((hooked, kwsub, b[1][:20]))
    assert len(seen) >= 6


def test_subgoals_and_has_blocks_equal_the_reference(env):
    seen = set()
    sets = GOAL_SETS[1:] + [[{"type": "go", "object": "", "from": "", "to": DS}, G("on", "apple", "", DT)],
                            [{"type": "say", "object": "hello", "from": "", "to": "owner"}],
                            [{"type": "go", "object": "", "from": "", "to": DS}, {"type": "say", "object": "hi", "from": "", "to": "nowhere"}]]
    for goals, sage, person, fixed in itertools.product(sets, ("goals", "off"), (True, False), (True, False)):
        def build(cls, T):
            planner = types.SimpleNamespace(fixed_subgoals=fixed)
            return make_loop(cls, T, cfg={"sage_subgoals": sage, "person_goals": person}), make_run(
                env, T, goals=copy.deepcopy(goals), planner=planner)
        a, b = both(build, lambda lp, r: lp._subgoals(r))
        assert a == b, (goals, sage, person, fixed)
        a, b = both(build, lambda lp, r: lp._lab_subgoals(r))
        assert a == b, (goals, person)
        seen.add(b[1][:30])
    assert len(seen) >= 6
    for planner in (None, types.SimpleNamespace(blocks=lambda: [1], join_blocks=lambda b: b, repair_block=lambda b: b),
                    types.SimpleNamespace(blocks=lambda: [], join_blocks=lambda b: b, repair_block=lambda b: b),
                    types.SimpleNamespace(blocks=lambda: 1 / 0, join_blocks=lambda b: b, repair_block=lambda b: b)):
        r = types.SimpleNamespace(planner=planner)
        assert old.RefCertify._has_blocks(r) == AssistLoop._has_blocks(r)


def test_gate_repair_and_restore_equal_the_reference(env):
    seen = set()
    for strict, first_ok, second_ok, blocks in itertools.product((False, True), (True, False), (True, False), (True, False)):
        def build(cls, T):
            blk = [types.SimpleNamespace(steps=["b1"]), types.SimpleNamespace(steps=["b2"])]
            planner = types.SimpleNamespace(blocks=lambda: blk, join_blocks=lambda b: [], repair_block=lambda b: b) if blocks else None

            def run(r, per_goal=False):
                T.append(("run", per_goal))
                r.steps = ["per-goal"] if per_goal else ["first"]
                for b in blk:
                    b.steps = ["x" + str(per_goal)]
                r.repaired_by_op = {"op": 1 + per_goal}
                return ["n2"] if per_goal else ["n1"]
            lp = make_loop(cls, T, cfg={"gate_strict": strict}, _gate_repair_run=run,
                           _blocking=lambda r, steps, per_goal=False: [] if (steps == ["first"] and first_ok) or (
                               steps == ["per-goal"] and second_ok) else ["bad"])
            return lp, make_run(env, T, steps=["pre"], planner=planner, repaired_by_op={})
        a, b = both(build, lambda lp, r: lp._gate_repair(r))
        assert a == b, (strict, first_ok, second_ok, blocks)
        seen.add((b[1], len(b[2])))
    assert len(seen) >= 3
    r1, r2 = types.SimpleNamespace(), types.SimpleNamespace()
    blk1, blk2 = [types.SimpleNamespace(steps=[])], [types.SimpleNamespace(steps=[])]
    sn = (["a"], [["b"]], {"k": 1})
    old.RefCertify._restore_repair(r1, blk1, sn)
    AssistLoop._restore_repair(r2, blk2, sn)
    assert vars(r1) == vars(r2) and blk1[0].steps == blk2[0].steps == ["b"]


def test_gate_repair_run_and_repair_suffix_equal_the_reference(env):
    rng, seen = random.Random(3), set()
    for plan in _plans(rng, 20):
        for world, goals, complete, person in itertools.product(rng.sample(WORLDS, 2), rng.sample(GOAL_SETS, 2), ("0", "1"), (True, False)):
            def build(cls, T):
                return make_loop(cls, T, cfg={"gate_complete": complete, "person_goals": person}), make_run(
                    env, T, world=W(**world), goals=copy.deepcopy(goals), steps=copy.deepcopy(plan))
            for per_goal in (False, True):
                a, b = both(build, lambda lp, r: lp._gate_repair_run(r, per_goal))
                assert a == b, (plan, world, goals, complete, person, per_goal)
                seen.add(b[1][:30])
            a, b = both(build, lambda lp, r: lp._repair_suffix(r, copy.deepcopy(plan)))
            assert a == b, (plan, world, goals)
            seen.add("s" + b[1][:30])
    assert len(seen) >= 6


def test_generate_and_certify_equals_the_reference(env):
    seen = set()
    PLAN = [{"action": "Find", "object": "apple"}, {"action": "Pick", "object": "apple"}, {"action": "MoveTo", "object": DT},
            {"action": "Place", "object": DT}]
    for goals, person, probs0, symbolic, edited, edited_none, block_edits, blocking_seq, plan_err in itertools.product(
            ([G("on", "apple", "", DT)], [{"type": "go", "object": "", "from": "", "to": DS}], []), (True, False),
            ([], [(1, "p", False)], [(1, "p", True)]), (False, True), (False, True), (False, True), (0, 1),
            ([[], ["a"]], [["a"], ["a"], ["a"]], [["a"], [], []], [["a", "b"], ["b"], []]), (None, RuntimeError("down"), LLMBudgetExceeded("b"))):
        def build(cls, T):
            it = iter(blocking_seq)
            probs = [[p for p in probs0]]

            def blocking(r, steps, per_goal=False):
                return list(next(it, []))
            planned = (lambda *a, **k: (_ for _ in ()).throw(plan_err)) if plan_err else (lambda *a, **k: (copy.deepcopy(PLAN), {"m": 1}))

            def repair_plan(planner, task, obs, ground, world, places, goals, goal_txt, budget=None):
                if edited_none:
                    raise LLMBudgetExceeded("e")
                return (copy.deepcopy(PLAN[:2]) if edited else None), ["sub-goal 1 repaired", "sub-goal 2 emptied", "x"]
            lp = make_loop(cls, T, cfg={"person_goals": person}, _plan_call=planned, _obs="OBS", _subgoals=None,
                           _ground_all=lambda r, s: [dict(x) for x in s], _check=lambda *a: list(probs0),
                           _gate_repair=lambda r: ["op edit"], _blocking=blocking, _repair_plan=repair_plan,
                           _refine_plan=lambda r, f: [dict(PLAN[0])])
            lp._tidy = lambda r, steps: (list(steps), ["tidy note"] if len(steps) > 3 else [])
            r = make_run(env, T, goals=copy.deepcopy(goals), planner=object(), block_edits=block_edits)
            return lp, r
        a, b = both(build, lambda lp, r: lp._generate_and_certify(r))
        assert a == b, (goals, person, probs0, symbolic, edited, edited_none, block_edits, blocking_seq, plan_err)
        seen.add((len(json.loads(a[0])), b[3][:0] + str(blocking_seq), type(plan_err).__name__))
    assert len(seen) >= 12


def test_plan_phase_equals_the_reference(env):
    seen = set()
    for plan_only, runs_goals, steps, certified, err, goals, meta_err in itertools.product(
            (False, True), (None, True), ([], [{"action": "MoveTo", "object": DT}], [{"skill": "custom", "action": "X", "object": ""}]),
            (True, False), (None, RuntimeError("d"), LLMBudgetExceeded("b")), ([], [G("on", "apple", "", DT)]), ("", "e")):
        def build(cls, T):
            def generate(r):
                r.steps, r.certified, r.gate_findings, r.plan_error = list(steps), certified, ["f1", "f2", "f3", "f4"], err
                r.run.plan_meta = {"error": meta_err} if meta_err else {}
                yield r.ev("gate", round=0)
            lp = make_loop(cls, T, queries={"runs_goals": runs_goals}, _search_start=lambda r: iter(()), _make_planner=lambda: "P",
                           _hook_progress=None, _generate_and_certify=generate,
                           _llm_failed=lambda r, e, what="": {"event": "done", "why": what},
                           _finish=lambda r, status, msg="", **kw: {"event": "done", "status": status, "msg": msg, **kw})
            return lp, make_run(env, T, plan_only=plan_only, goals=copy.deepcopy(goals), dialogue=[("user", "go"), ("robot", "x")])
        a, b = both(build, lambda lp, r: lp._plan_phase(r))
        assert a == b, (plan_only, runs_goals, steps, certified, err, goals, meta_err)
        seen.add((b[1], len(json.loads(a[0])), b[0][-50:]))
    assert len(seen) >= 6 and any(s[0] == "true" for s in seen) and any(s[0] == "false" for s in seen)


# -- (4) deliberate edits of the moved code are caught -------------------------------------------------------------------------
def test_an_edit_of_the_moved_code_is_caught(monkeypatch, env):
    """The comparison bites: a changed ``_untouched`` (it ignores the picked objects) differs from the reference."""
    plan = [{"action": "Pick", "object": "apple"}]
    goals = [G("on", "apple", "", DT)]
    assert old.RefCertify._untouched(goals, plan) == AssistLoop._untouched(goals, plan) == []
    monkeypatch.setattr(certify.CertifyPhase, "_untouched", staticmethod(lambda goals, steps: list(goals)))
    assert old.RefCertify._untouched(goals, plan) != AssistLoop._untouched(goals, plan)


@pytest.mark.parametrize("name", NAMES)
def test_every_moved_name_has_the_same_signature_as_the_reference(name):
    import inspect
    sig = lambda f: [(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()]       # noqa: E731 (annotations are strings here)
    assert sig(getattr(old.RefCertify, name)) == sig(getattr(certify.CertifyPhase, name))
