"""Step S17: the understand rounds (_understand_phase, _take_given_goals, _understand_round, _two_places, _take_understanding,
_ask_views, _compare_views, _reask_abstention, _search_when_unsure and the texts _FIND_NOTE, _UNSURE_NOTE) moved verbatim from
assist_loop.py to the UnderstandRounds mixin in phases/understand_rounds.py. These tests prove nothing changed: (1) the mixin is in
AssistLoop's method resolution order, serves the eleven names, the loop file no longer defines them, what the rounds call
(``_refused_reading``, ``_unsure_data``, ``_agree_state`` ...) stays in the loop file, a name patched on the class still wins, and
the moved module stays small; (2) the method bodies are the same syntax trees as the reference copy
(tests/legacy_ref/s17_understand_rounds.py) apart from the named import seams; (3) a differential test against that copy: the old
and the new class give the same events, the same return values, the same calls to their collaborators and the same changes to the
run state over grids of made-up statuses, goals, dialogues, options and scripted collaborators; (4) the grids reach the interesting
branches, so the comparison bites. Made-up places and words only."""

import ast
import itertools
import json
import types
from pathlib import Path

from legacy_ref import s17_understand_rounds as old
from robot_agent.core.planning import assist_loop, goal_fix, speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places, _DONE, _NEXT, _STOP
from robot_agent.core.planning.phases import PHASE_MIXINS, understand_rounds
from robot_agent.core.planning.understand import LLMBudgetExceeded, LLMUnavailable

NAMES = ("_understand_phase", "_take_given_goals", "_understand_round", "_two_places", "_take_understanding", "_ask_views",
         "_compare_views", "_reask_abstention", "_search_when_unsure")
CONSTS = ("_FIND_NOTE", "_UNSURE_NOTE")
STAY = ("_refused_reading", "_unsure_data", "_agree_state", "_cancelled", "_goal_readback", "_read_back", "_complete_override")
LOOP_FILE = Path(assist_loop.__file__)
MIXIN_FILE = Path(understand_rounds.__file__)


class OldLoop(old.RefRounds, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert understand_rounds.UnderstandRounds in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(understand_rounds.UnderstandRounds) < mro.index(assist_loop.WhichOne)
    for n in NAMES + CONSTS:
        assert n not in vars(AssistLoop), n
        assert n in vars(understand_rounds.UnderstandRounds), n
        assert getattr(AssistLoop, n) is not None


def test_the_loop_file_does_not_define_them_any_more():
    src = LOOP_FILE.read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n
    for n in CONSTS:
        assert f"    {n} = " not in src, n


def test_what_the_rounds_call_stays_in_the_loop_file():
    src = LOOP_FILE.read_text()
    for n in STAY:
        assert n in vars(AssistLoop) or any(n in vars(m) for m in AssistLoop.__mro__), n
    for n in ("_refused_reading", "_unsure_data", "_agree_state", "_cancelled"):
        assert f"def {n}(" in src, n


def test_a_name_patched_on_the_class_still_wins(monkeypatch):
    monkeypatch.setattr(AssistLoop, "_two_places", lambda self, r, q: ("patched", q))
    assert AssistLoop.__new__(AssistLoop)._two_places(None, "q") == ("patched", "q")


def test_the_moved_module_is_small():
    assert len(MIXIN_FILE.read_text().splitlines()) <= 300


def test_the_texts_equal_the_reference():
    for c in CONSTS:
        assert getattr(AssistLoop, c) == getattr(OldLoop, c) == getattr(old.RefRounds, c)
        assert isinstance(getattr(AssistLoop, c), str) and "{find}" in getattr(AssistLoop, c)


def test_the_done_marker_is_the_loops_own_value():
    assert understand_rounds._DONE is _DONE and understand_rounds._STOP is _STOP and old._DONE is _DONE and old._STOP is _STOP


# -- (2) the bodies are the reference's bodies ---------------------------------------------------------------------------------
def _methods(path, cls):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}


class _Seams(ast.NodeTransformer):
    """The one seam the move changed, on the NEW tree: the relative import of ``Refusal`` gained a dot."""

    def visit_ImportFrom(self, node):
        return ast.ImportFrom(module=node.module.split(".")[-1] if node.module else None, names=node.names, level=0)


def _dump(fn):
    node = _Seams().visit(ast.parse(ast.unparse(fn)))
    for n in ast.walk(node):                                   # the docstring and the line numbers are not behaviour
        for f in ("lineno", "end_lineno", "col_offset", "end_col_offset"):
            if hasattr(n, f):
                setattr(n, f, 0)
    return ast.dump(node)


def test_method_bodies_are_the_reference_bodies():
    new, ref = _methods(MIXIN_FILE, "UnderstandRounds"), _methods(old.__file__, "RefRounds")
    assert set(NAMES) == set(new) == set(ref)
    for n in NAMES:
        assert _dump(new[n]) == _dump(ref[n]), n


def test_the_moved_text_is_the_reference_text():
    """Beyond the syntax trees: every line of the reference class body, comments included, is in the new class body."""
    def body(path, cls):
        lines = Path(path).read_text().splitlines()
        return lines[next(i for i, l in enumerate(lines) if l.startswith(f"class {cls}")) + 1:]
    new, ref = body(MIXIN_FILE, "UnderstandRounds"), body(old.__file__, "RefRounds")
    assert [l for l in new if "import Refusal" not in l] == [l for l in ref if "import Refusal" not in l]


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
ENV = {"shelf@hall": {"aliases": ["shelf"]}, "bench@porch": {"aliases": ["bench"]}, "owner": {
    "person": "owner", "speaker": True, "aliases": ["me"], "loc": {"x": 0.0, "y": 0.0}}}
GOAL = {"type": "on", "object": "cup", "from": "", "to": "shelf@hall"}
GOAL2 = {"type": "on", "object": "cup", "from": "", "to": "bench@porch"}      # with GOAL: the same object, two destinations
GOALS = [[], [GOAL], [GOAL, GOAL2], [{"type": "say", "object": "hello", "from": "", "to": ""}]]


def jd(x):
    return json.dumps(x, sort_keys=True, default=lambda o: type(o).__name__)      # an object is its type (no memory address)


class Opts:
    """The option dispatcher stub: flows and queries answer from a script, every call is recorded."""

    def __init__(self, T, flows=None, queries=None):
        self.T, self.flows, self.queries = T, flows or {}, queries or {}

    def decide_flow(self, hook, r):
        self.T.append(("flow", hook))
        return self.flows.get(hook)
        yield                                                    # noqa: unreachable - makes this a generator

    def query(self, name, *a):
        self.T.append(("query", name))
        return self.queries.get(name)


class Lang:
    def __init__(self, T, pivot="none", english="the cup", trusted=True):
        self.T, self.pivot, self.english, self.trusted = T, pivot, english, trusted

    def hear(self, text, pivot=None):
        self.T.append(("hear", text, pivot))
        return types.SimpleNamespace(english=self.english, trusted=self.trusted)


def make_run(**kw):
    T = kw.pop("T")
    r = types.SimpleNamespace(task="put the cup on the shelf", lang="en", dialogue=[], places=Places(ENV), memory="MEM", world="W",
                              llm_budget="B", filtered=[], repaired=[], goals=[], run=types.SimpleNamespace(obs=""),
                              robot=types.SimpleNamespace(plan_actions=["Find", "Pick", "Place", "MoveTo"],
                                                          text=lambda k, lang="en", **a: f"TEXT:{k}:{sorted(a.items())}"))
    r.ev = lambda name, **a: {"event": name, **a}
    r.__dict__.update(kw)
    return r


def make_loop(cls, T, cfg=None, flows=None, queries=None, **script):
    """A loop whose collaborators are stubs that record their call and answer from ``script`` (name -> value / generator)."""
    lp = cls.__new__(cls)
    lp.cfg, lp.original, lp._answer_source = dict(cfg or {}), script.pop("original", None), script.pop("answer_source", "robot")
    lp.options = Opts(T, flows, queries)
    lp.__dict__["_language"] = script.pop("language", Lang(T))
    lp._lab = lambda flag, **info: T.append(("lab", flag, jd(info)))
    lp.robot = types.SimpleNamespace(plan_actions=["Find", "Pick", "Place"])
    lp._manifest_planner = lambda k, d=None: None
    for name, val in script.items():
        setattr(lp, name, _stub(T, name, val))
    return lp


def _stub(T, name, val):
    """A plain method (returns ``val``), a generator method (script entry is a list: the events then the return value) or a raiser."""
    if isinstance(val, tuple) and val and val[0] == "gen":
        _, events, ret = val

        def gen(*a, **k):
            T.append((name, jd(a[1:] if a else a)))
            for e in events:
                yield e
            if isinstance(ret, Exception):
                raise ret
            return ret
        return gen

    def fn(*a, **k):
        T.append((name, jd(a[1:] if a and hasattr(a[0], "ev") else a)))
        if isinstance(val, Exception):
            raise val
        return val(*a, **k) if callable(val) else val
    return fn


def drive(gen):
    if not hasattr(gen, "__next__"):                             # a plain method: its value is the result
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
    """Run the same scenario on the old and the new class; returns the two results (events, value, trace, run state)."""
    out = []
    for cls in (OldLoop, AssistLoop):
        T = []
        lp, r = build(cls, T)
        evs, val = drive(call(lp, r))
        state = {k: v for k, v in sorted(vars(r).items()) if k not in ("ev", "places", "robot", "run")}
        state["run"] = {k: v for k, v in vars(r.run).items() if k != "ended_at"}     # the finish time is the clock's
        out.append((jd(evs), jd(val), T, jd(state)))
    return out


def same(build, call):
    a, b = both(build, call)
    assert a == b
    return b


# --- _understand_round: the refusal, the grounding, the question or the read-back -------------------------------------------------
def test_understand_round_equals_the_reference_over_a_grid():
    seen, n = set(), 0
    statuses = ("clear", "ambiguous", "unsafe", "infeasible")
    grid = itertools.product(statuses, GOALS, ([], [("robot", "which?"), ("user", "the shelf")]), ("robot", "typed"),
                             (None, "repeat", "override"), (None, "too heavy"), (None, "Pour"), (None, _STOP),
                             (None, "Q?", _STOP), (None, "OUT"), ((None, "Q2"), ("OUT2", None)))
    for status, goals, dlg, src, how, over, cannot, ongoals, readback, refused, rb in grid:
        u = {"status": status, "reason": "I cannot do that" if status != "clear" else "", "goals": goals,
             "question": "Which one?" if status == "ambiguous" else None}

        def build(cls, T):
            r = make_run(T=T, dialogue=list(dlg), repeat_asked=False)
            lp = make_loop(cls, T, flows={"on_goals": ongoals}, answer_source=src,
                           _distrusted=None, _take_understanding=("gen", [], (json.loads(json.dumps(u)), None)), _progress=None,
                           _capability_check=how, _complete_override=("gen", [], None),
                           _over_limits=over, _cannot_do=cannot, _goal_readback=("gen", [], readback),
                           _refused_reading=("gen", [], refused), _read_back=("gen", [], rb),
                           _ask_clarify=("gen", [{"event": "ask"}], "ASKED"), _safety_profile=None,
                           _finish=lambda r, status, **kw: {"event": "done", "status": status, **kw})
            return lp, r
        def call(lp, r, goals=goals):
            r.goals = [dict(g) for g in goals]
            return lp._understand_round(r)
        a, b = both(build, call)
        assert a == b, (status, goals, dlg, src, how, over, cannot, ongoals, readback, refused, rb)
        n += 1
        ev_names = tuple(json.loads(b[0])[k]["event"] for k in range(len(json.loads(b[0]))))
        seen.add((ev_names[:3], b[1][:80]))
    assert n > 10000
    flat = " ".join(f"{x}" for x in seen)
    assert not any("raised" in s[1] for s in seen)
    for word in ("understand_check", "ASKED", jd(_STOP), "done", "OUT2"):
        assert word in flat, word


# --- the pieces: the places question, the views, the re-asks, the phase driver ------------------------------------------------------
def test_two_places_equals_the_reference():
    hit = set()
    for goals, q, agree in itertools.product(GOALS, (None, "Which one?"), (False, True)):
        def build(cls, T):
            return make_loop(cls, T, cfg={"dialog_agree": agree}), make_run(T=T, goals=[dict(g) for g in goals])
        res = same(build, lambda lp, r: lp._two_places(r, q))
        hit.add((len(goals), q is None, agree, res[1][:40]))
    assert any("two" in str(h) or "destination" in str(h) or "put" in str(h) for h in hit) or len({h[3] for h in hit}) > 3


def test_take_given_goals_and_the_phase_driver_equal_the_reference():
    rounds = [[_NEXT], [_NEXT, _NEXT, _DONE], [_STOP], [_NEXT, _STOP], [_DONE]]
    for given, seq, conv in itertools.product((None, [GOAL], [GOAL, GOAL2]), rounds, (False, True)):
        for q in (None, "Where?"):
            def build(cls, T, seq=seq):
                it = iter(seq)

                def rnd(*a):
                    T.append(("round",))
                    yield {"event": "round"}
                    return next(it)
                lp = make_loop(cls, T, _understand_round=rnd, _converge=conv,
                               _ground_goals=lambda *a: ([dict(g) for g in (a[0] or [])], q), original="orig")
                return lp, make_run(T=T, given_goals=given, memory="MEM")
            a, b = both(build, lambda lp, r: lp._understand_phase(r))
            assert a == b
    assert "unasked" in both(lambda cls, T: (make_loop(cls, T, _converge=False, original="o",
                             _ground_goals=lambda *a: ([], "Where?")), make_run(T=T, given_goals=[GOAL])),
                             lambda lp, r: lp._understand_phase(r))[1][0]


def test_take_understanding_equals_the_reference_over_the_options():
    outs = set()
    flags = ("reask_abstain", "reask_anchor", "dialog_unstick", "understand_views")
    for bits, patched, dlg, fail, vq in itertools.product(itertools.product((False, True), repeat=len(flags)),
                                                            (None, [GOAL]), ([], [("user", "x")]),
                                                            (None, LLMUnavailable("down"), LLMBudgetExceeded("spent")),
                                                            (None, "VIEWQ", _STOP)):
        cfg = dict(zip(flags, bits))
        u1 = {"status": "clear", "goals": [GOAL], "question": None}

        def build(cls, T):
            def understand(*a, **k):
                T.append(("understand", jd(a[1:2])))
                if fail:
                    raise fail
                return u1
            lp = make_loop(cls, T, cfg=cfg, _understand=understand, _llm_failed=lambda *a, **k: {"event": "done", "fail": 1},
                           _reask_abstention=("gen", [], u1), _search_when_unsure=("gen", [], u1),
                           _compare_views=("gen", [], vq), _ground_understood=lambda r, u: ([dict(GOAL)], "q"))
            return lp, make_run(T=T, patched=patched, dialogue=list(dlg))
        a, b = both(build, lambda lp, r: lp._take_understanding(r))
        assert a == b, (cfg, patched, dlg, fail, vq)
        outs.add((b[1][:12], b[0][:60]))
    assert len(outs) >= 3


def test_ask_views_equals_the_reference():
    res = same(lambda cls, T: (make_loop(cls, T, _ask_clarify=("gen", [{"event": "ask"}], "A")), make_run(T=T, view_question="Q?")),
               lambda lp, r: lp._ask_views(r))
    assert "views" in res[0] and "A" in res[1]


def _views_loop(cls, T, u2, g1, g2, forbids=None, trusted=True, fail=None, pivot="none", english="the cup"):
    grounded = iter([(g1, ""), (g2, "")])

    def understand(*a, **k):
        T.append(("understand", k.get("pivot")))
        if fail:
            raise fail
        return u2
    return make_loop(cls, T, language=Lang(T, pivot, english, trusted), _understand=understand,
                     _ground_understood=lambda r, u: next(grounded), _forbids=forbids,
                     _llm_failed=lambda *a, **k: {"event": "done"}, _goal_phrase=AssistLoop._goal_phrase)


def test_compare_views_equals_the_reference():
    u1 = {"status": "clear", "goals": [GOAL], "question": None}
    outs = set()
    cases = itertools.product(("ko", "en", "zz"), (u1, {**u1, "goals": []}, {**u1, "status": "unsafe"}),
                              ({"status": "clear", "goals": [GOAL2]}, {"status": "clear", "goals": [GOAL]},
                               {"status": "unsafe", "goals": [GOAL2]}, {"status": "clear", "goals": []}),
                              (None, "no"), (True, False), (None, LLMBudgetExceeded("x"), LLMUnavailable("y")),
                              ("none", "translate"))
    for lang, first, u2, forbids, trusted, fail, pivot in cases:
        g1, g2 = [dict(g) for g in first.get("goals", [])], [dict(g) for g in u2["goals"]]

        def build(cls, T):
            return _views_loop(cls, T, u2, g1, g2, forbids=lambda g, p: forbids, trusted=trusted, fail=fail,
                               pivot=pivot), make_run(T=T, lang=lang)
        a, b = both(build, lambda lp, r: lp._compare_views(r, first))
        assert a == b, (lang, first, u2, forbids, trusted, fail, pivot)
        outs.add(b[1][:24])
    assert any("two ways" in o for o in outs) or any("q_which_reading" in o for o in outs) or len(outs) >= 2


def test_reask_and_search_when_unsure_equal_the_reference():
    outs = set()
    abstain = {"status": "ambiguous", "goals": [], "question": "Where is the cup?"}
    takes = [{"status": "clear", "goals": [GOAL2]}, {"status": "unsafe", "goals": [GOAL2]}, {"status": "clear", "goals": []}]
    for anchor, gate_off, actions, u2, fail, dlg in itertools.product(
            (False, True), (None, True), (["Find"], ["Pick"]), takes, (None, LLMBudgetExceeded("x"), LLMUnavailable("y")),
            ([], [("robot", "where?"), ("user", "I do not know")], [("robot", "where?"), ("user", "the bench")])):
        def build(cls, T):
            def understand(*a, **k):
                T.append(("understand", k.get("note", "")[:30]))
                if fail:
                    raise fail
                return u2
            lp = make_loop(cls, T, cfg={"reask_anchor": anchor, "dialog_agree2": True}, queries={"gate_off": gate_off},
                           _understand=understand, _llm_failed=lambda *a, **k: {"event": "done"}, _unsure_data=None)
            lp.robot = types.SimpleNamespace(plan_actions=actions)
            return lp, make_run(T=T, dialogue=list(dlg), lang="en")
        for method in ("_reask_abstention", "_search_when_unsure"):
            a, b = both(build, lambda lp, r, m=method: getattr(lp, m)(r, dict(abstain)))
            assert a == b, (method, anchor, gate_off, actions, u2, fail, dlg)
            outs.add((method, b[1][:20], len(b[2])))
    assert len({o[1] for o in outs}) >= 2 and any(o[2] > 1 for o in outs)
    assert goal_fix.abstains_on_location(abstain)                 # the made-up abstention really is one
    assert speech.LocalText("q_repeat")
