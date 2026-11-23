"""Step S18: agreement and read-back (_texts_of, _agreed_reading, _agree_reask, _restated_by_person, _progress, _distrusted,
_issues, _object_source, _goal_readback, _resample, _read_back) moved verbatim from assist_loop.py to the AgreeReadBack mixin in
phases/agree.py. These tests prove nothing changed: (1) the mixin is in AssistLoop's method resolution order, serves the eleven
names, the loop file no longer defines them, what stays (``_refused_reading``, ``_unsure_data``, ``_agree_state``, which the step S17
test reads in the loop file, the grounding, the corrections, ``_complete_override``, ``_cancelled``) is still in the loop file, a name patched on the class still wins, the hook
``on_issues`` and the services ``confirm_limit`` and ``unrefused`` are still asked by name, and the moved module stays small; (2) the
method bodies are the same syntax trees as the reference copy (tests/legacy_ref/s18_agree.py), and every line of its class body is
in the new class body; (3) differential tests against that copy: the old and the new class give the same events, the same return
values, the same calls to their collaborators and the same changes to the run state over grids of made-up goals, dialogues,
options and scripted collaborators (the real ``dialog_*`` rules and ``find_issues`` run in both); (4) the grids reach the
interesting branches, so the comparison bites. Made-up places and words only."""

import ast
import itertools
import json
import types
from pathlib import Path

from legacy_ref import s18_agree as old
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.assist_loop import AssistLoop, Places, _DONE, _NEXT, _STOP
from robot_agent.core.planning.phases import PHASE_MIXINS, agree
from robot_agent.core.planning.understand import LLMBudgetExceeded, LLMUnavailable

NAMES = ("_texts_of", "_agreed_reading", "_agree_reask", "_restated_by_person", "_progress", "_distrusted", "_issues",
         "_object_source", "_goal_readback", "_resample", "_read_back")
STAY = ("_refused_reading", "_unsure_data", "_agree_state", "_cancelled", "_complete_override", "_ground_understood", "_ground_v2", "_canon", "_unverified_objects", "_local_names",
        "_correction_texts", "_correction_edit", "_patch_goals")
LOOP_FILE = Path(assist_loop.__file__)
MIXIN_FILE = Path(agree.__file__)


class OldLoop(old.RefAgree, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert agree.AgreeReadBack in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(agree.AgreeReadBack) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(AssistLoop), n
        assert n in vars(agree.AgreeReadBack), n
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
    monkeypatch.setattr(AssistLoop, "_issues", lambda self, r, goals: ["patched"])
    assert AssistLoop.__new__(AssistLoop)._issues(None, []) == ["patched"]


def test_the_hook_and_the_services_are_asked_by_name():
    src = MIXIN_FILE.read_text()
    for call in ('self.options.transform("on_issues"', 'self.options.query("confirm_limit", r)',
                 'self.options.query("unrefused", r, ans)'):
        assert call in src, call
        assert call not in LOOP_FILE.read_text(), call


def test_the_moved_module_is_small():
    assert len(MIXIN_FILE.read_text().splitlines()) <= 300


def test_the_end_markers_are_the_loops_own_values():
    assert agree._DONE is _DONE and agree._NEXT is _NEXT and agree._STOP is _STOP
    assert old._DONE is _DONE and old._NEXT is _NEXT and old._STOP is _STOP
    assert agree._USER_WORDS == assist_loop._USER_WORDS == old._USER_WORDS


# -- (2) the bodies are the reference's bodies ---------------------------------------------------------------------------------
def _methods(path, cls):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}


def _dump(fn):
    node = ast.parse(ast.unparse(fn))
    for n in ast.walk(node):                                   # the line numbers are not behaviour
        for f in ("lineno", "end_lineno", "col_offset", "end_col_offset"):
            if hasattr(n, f):
                setattr(n, f, 0)
    return ast.dump(node)


def test_method_bodies_are_the_reference_bodies():
    new, ref = _methods(MIXIN_FILE, "AgreeReadBack"), _methods(old.__file__, "RefAgree")
    assert set(NAMES) == set(new) == set(ref)
    for n in NAMES:
        assert _dump(new[n]) == _dump(ref[n]), n


def test_the_moved_text_is_the_reference_text():
    """Beyond the syntax trees: every line of the reference class body, comments and docstrings included, is in the new class body."""
    def body(path, cls):
        lines = Path(path).read_text().splitlines()
        return lines[next(i for i, l in enumerate(lines) if l.startswith(f"class {cls}")) + 1:]
    assert body(MIXIN_FILE, "AgreeReadBack") == body(old.__file__, "RefAgree")


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
ENV = {"shelf@hall": {"aliases": ["shelf"]}, "bench@porch": {"aliases": ["bench"]}, "owner": {
    "person": "owner", "speaker": True, "aliases": ["me"], "loc": {"x": 0.0, "y": 0.0}}}
GOAL = {"type": "on", "object": "cup", "from": "", "to": "shelf@hall"}
GOAL2 = {"type": "on", "object": "cup", "from": "", "to": "bench@porch"}
GOAL3 = {"type": "on", "object": "bowl", "from": "", "to": "shelf@hall"}
GOALS = [[], [GOAL], [GOAL2], [GOAL, GOAL3], [{"type": "open", "object": "shelf@hall", "from": "", "to": ""}]]


def _plain(x):
    """Dict keys as text (a goal signature is a tuple), sets sorted: a stable form to compare."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(str(v) for v in x)
    return x


def jd(x):
    return json.dumps(_plain(x), sort_keys=True, default=lambda o: type(o).__name__)      # an object is its type (no memory address)


class Opts:
    """The option dispatcher stub: queries answer from a script (a callable is called), every call is recorded."""

    def __init__(self, T, queries=None, extra=None):
        self.T, self.queries, self.extra = T, queries or {}, extra or []

    def query(self, name, *a):
        self.T.append(("query", name, jd(a[1:])))
        v = self.queries.get(name)
        return v(*a) if callable(v) else v

    def transform(self, name, value, *a):
        self.T.append(("transform", name, jd(value)))
        return value + (["hooked"] if self.queries.get("hook") else [])

    def collect(self, name, *a):
        self.T.append(("collect", name))
        return list(self.extra)


class Lang:
    def __init__(self, T, pivot="none", english="the cup", trusted=True, heard=None, bad=None):
        self.T, self.pivot, self.english, self.trusted = T, pivot, english, trusted
        self.heard, self.bad = heard or {}, bad

    def hear(self, text, pivot=None):
        self.T.append(("hear", text))
        return types.SimpleNamespace(english=self.english, trusted=self.trusted)

    def untrusted(self, utterance):
        self.T.append(("untrusted", utterance))
        return self.bad


class Mem:
    def __init__(self, T):
        self.T = T

    def remember_answer(self, *a):
        self.T.append(("remember", jd(a)))


def make_run(**kw):
    T = kw.pop("T")
    r = types.SimpleNamespace(task="put the cup on the shelf", lang="en", dialogue=[], places=Places(ENV), memory=Mem(T), world="W",
                              llm_budget="B", goals=[], confirms=0, asked=0, heard=0, run=types.SimpleNamespace(obs=""))
    r.ev = lambda name, **a: {"event": name, **a}
    r.__dict__.update(kw)
    return r


def _stub(T, name, val):
    """A plain method (returns ``val``), a generator method (``("gen", events, return value)``) or a raiser."""
    if isinstance(val, tuple) and val and val[0] == "gen":
        _, events, ret = val

        def gen(*a, **k):
            T.append((name, jd(a[1:] if a else a)))
            yield from events
            return ret
        return gen

    def fn(*a, **k):
        T.append((name, jd(a[1:] if a and hasattr(a[0], "ev") else a)))
        if isinstance(val, Exception):
            raise val
        return val(*a, **k) if callable(val) else val
    return fn


def make_loop(cls, T, cfg=None, queries=None, language=None, original=None, converge=True, extra=None, **script):
    lp = cls.__new__(cls)
    lp.cfg = {"goal_readback": True, "confirm_answers": True, "confirm_task": False, "understand_samples": 0, **(cfg or {})}
    lp.original = original
    lp.options = Opts(T, queries, extra)
    lp.__dict__["_language"] = language or Lang(T)
    lp._lab = lambda flag, **info: T.append(("lab", flag, jd(info)))
    lp._converge = lambda: converge
    lp.robot = types.SimpleNamespace(i18n=None, manifest=types.SimpleNamespace(planner=None), plan_actions=["Find", "Pick"])
    lp._manifest_planner = lambda k, d=None: None
    lp.summary = lambda goals, places=None: "SUMMARY:" + jd(goals)
    lp._cancelled = lambda r: None
    lp._finish = lambda r, status, msg="", **kw: {"event": "done", "status": status, "msg": msg}
    for name, val in script.items():
        setattr(lp, name, _stub(T, name, val))
    return lp


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
        state = {k: v for k, v in sorted(vars(r).items()) if k not in ("ev", "places", "run", "memory")}
        state["agree"] = vars(r).get("agree")
        out.append((jd(evs), jd(val), T, jd(state), jd(lp.__dict__.get("_tr_cache"))))
    return out


def check(build, call, label=None):
    a, b = both(build, call)
    assert a == b, label
    return b


# --- agreement: the refused reading, the agreed reading, the re-ask ------------------------------------------------------------
def test_refused_reading_runs_the_same_through_the_moved_methods():
    """``_refused_reading`` stays in the loop file; it calls the moved ``_restated_by_person`` and ``_agreed_reading`` (both real here)."""
    from robot_agent.core.planning import dialog_converge
    seen, n = set(), 0
    dialogues = ([], [("robot", "q"), ("user", "put the cup on the bench")],
                 [("robot", "q"), ("user", "no"), ("robot", "which part?"), ("user", "the shelf, the bench")],
                 [("robot", "SUMMARY? Is that right?"), ("user", "no"), ("robot", "Which part is not right?"),
                  ("user", "the cup on the bench")])
    grid = itertools.product(GOALS, (True, False), ("none", "rejected", "rejected_dup"), (False, True), (False, True), (False, True),
                             (False, True), (None, "put the cup on the bench please"), dialogues)
    for goals, conv, rej, unstick, agree_on, part, tried, original, dlg in grid:
        def build(cls, T):
            s = dialog_converge.goal_sig(goals)
            r = make_run(T=T, goals=[dict(g) for g in goals], dialogue=list(dlg), confirms=1, allow_repeat=(rej == "rejected_dup"),
                         rejected=([s] if rej != "none" else []), part_asked=part, restate_taken=False)
            r.agree = {"first": {s: 0}, "refusals": [{"sig": s, "ans": "no", "rest": "", "unsure": False}], "asked": [],
                       "tried": tried}
            lp = make_loop(cls, T, cfg={"dialog_unstick": unstick, "dialog_agree": agree_on}, converge=conv, original=original,
                           _ask_clarify=("gen", [{"event": "ask"}], "ASKED"))
            return lp, r
        a, b = both(build, lambda lp, r: lp._refused_reading(r))
        assert a == b, (goals, conv, rej, unstick, agree_on, part, tried, original, dlg)
        n += 1
        seen.add(b[1][:30])
    assert n > 1000
    flat = " ".join(seen)
    assert "ASKED" in flat and "null" in flat and jd(_STOP) in flat          # the question, the old path, the stop


def test_agreed_reading_and_the_reask_equal_the_reference():
    outs = set()
    refusals = ([], [{"sig": None, "ans": "no", "rest": "", "unsure": False}],
                [{"sig": None, "ans": "no, the bench", "rest": "the bench", "unsure": False}],
                [{"sig": None, "ans": "I do not know", "rest": "", "unsure": True}])
    for goals, dlg, ref, a2, part, replaced, asked in itertools.product(
            GOALS[1:4], ([], [("robot", "SUMMARY?"), ("user", "no"), ("robot", "which part?"), ("user", "the shelf, the cup")],
                         [("user", "put the cup on the bench"), ("robot", "r"), ("user", "no, on the bench")]),
            refusals, (False, True), (False, True), ((), ("shelf@hall",)), ([], ["the cup"])):
        def build(cls, T):
            from robot_agent.core.planning import dialog_converge
            s = dialog_converge.goal_sig(goals)
            r = make_run(T=T, goals=[dict(g) for g in goals], dialogue=list(dlg), confirms=2, part_asked=part, replaced=replaced)
            r.agree = {"first": {s: 0}, "refusals": [{**x, "sig": s} for x in ref], "asked": list(asked), "tried": False}
            lp = make_loop(cls, T, cfg={"dialog_agree": True, "dialog_agree2": a2},
                           _agree_reask=("gen", [{"event": "reask"}], (_DONE if a2 else None)))
            r.places.ext = None
            return lp, r
        a, b = both(build, lambda lp, r: lp._agreed_reading(r))
        assert a == b, (goals, dlg, ref, a2, part, replaced, asked)
        outs.add((b[1][:14], len(b[0]) > 2))
    assert len(outs) >= 2 and any(o[0] == '"done"' for o in outs)       # the agreement fires in some scenarios, not in all


def test_agree_reask_equals_the_reference():
    for ans, ok_, cancelled in itertools.product(("yes", "no", "hmm"), (True, False, None), (False, True)):
        def build(cls, T):
            lp = make_loop(cls, T, _ask_yes_no=lambda r, q: (ans, ok_, ""))
            lp._cancelled = (lambda r: {"event": "done", "status": "aborted"}) if cancelled else (lambda r: None)
            return lp, make_run(T=T, goals=[dict(GOAL)], confirms=2)
        res = check(build, lambda lp, r: lp._agree_reask(r), (ans, ok_, cancelled))
        assert res[0]                                          # the clarify event is always sent


def test_agree_state_texts_unsure_data_and_progress_equal_the_reference():
    r = lambda T: make_run(T=T)
    a = check(lambda cls, T: (make_loop(cls, T), r(T)), lambda lp, r: lp._agree_state(r))
    assert "first" in a[1] and "tried" in a[1]
    for heard in ({}, {"put it": types.SimpleNamespace(english="place it", trusted=True)},
                  {"put it": types.SimpleNamespace(english="place it", trusted=False)},
                  {"put it": types.SimpleNamespace(english=None, trusted=True)}):
        res = check(lambda cls, T: (make_loop(cls, T, language=Lang(T, heard=heard)), r(T)), lambda lp, r: lp._texts_of("put it"))
        assert "put it" in res[1]
    seen = set()
    for ext, i18n, lang in itertools.product((None, types.SimpleNamespace(unsure=[]), types.SimpleNamespace(unsure=["dunno"])),
                                             (None, {"unsure": {"en": ["not sure"]}}, "plain"), ("en", "ko", None)):
        def build(cls, T):
            lp = make_loop(cls, T)
            lp.robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases=i18n) if i18n else None)
            run = make_run(T=T, lang=lang)
            run.places.ext = ext
            return lp, run
        res = check(build, lambda lp, r: lp._unsure_data(r), (ext, i18n, lang))
        seen.add(res[1][:50])
    assert len(seen) >= 3
    for conv, seen_sigs, asked, goals, dlg, refunds in itertools.product(
            (True, False), (None, set(), {"x"}), (0, 1, 3), GOALS, ([], [("user", "x")], [("robot", "q")]), (0, 2)):
        res = check(lambda cls, T: (make_loop(cls, T, converge=conv), make_run(
            T=T, seen_sigs=None if seen_sigs is None else set(seen_sigs), asked=asked, goals=[dict(g) for g in goals],
            dialogue=list(dlg), refunds=refunds)), lambda lp, r: lp._progress(r))
    assert check(lambda cls, T: (make_loop(cls, T), make_run(T=T, seen_sigs=set(), asked=1, goals=[dict(GOAL)],
                                                         dialogue=[("user", "x")])), lambda lp, r: lp._progress(r))[3] \
        .count('"asked": 0') == 1                              # a question that gained something is given back its round


def test_restated_by_person_and_distrusted_equal_the_reference():
    hit = set()
    for dlg, orig, planner in itertools.product(
            ([], [("user", "put the cup on the shelf in the hall please")], [("robot", "q"), ("user", "ok")]),
            (None, "put the cup on the shelf"), (None, {"restate_frac": 0.2, "restate_word_len": 2, "restate_min_words": 2})):
        def build(cls, T):
            lp = make_loop(cls, T, original=orig)
            lp._manifest_planner = lambda k, d=None: planner
            return lp, make_run(T=T, dialogue=list(dlg))
        hit.add(check(build, lambda lp, r: lp._restated_by_person(r), (dlg, orig, planner))[1])
    assert len(hit) >= 1
    for pa, lang, pivot, bad, dlg in itertools.product((False, True), ("en", "ko", None), ("none", "translate"), (None, "garbled"),
                                                       ([], [("user", "hello there")])):
        check(lambda cls, T: (make_loop(cls, T, language=Lang(T, pivot=pivot, bad=bad)),
                              make_run(T=T, pivot_asked=pa, lang=lang, dialogue=list(dlg))), lambda lp, r: lp._distrusted(r),
              (pa, lang, pivot, bad, dlg))


# --- the read-back: issues, the question, the consistency samples, the read-back itself -------------------------------------------
def test_issues_and_object_source_equal_the_reference_over_a_grid():
    seen = set()
    grid = itertools.product(GOALS, (True, False), ([], [("robot", "q"), ("user", "put the cup on the bench")]),
                             ("put the cup on the shelf", "move the thing", "bring the bowl", "open the shelf"),
                             (None, "put the cup on the bench"), ("en", "ko"), (False, True), (False, True), ([], [GOAL3]),
                             ((), ("shelf@hall",)))
    for goals, on, dlg, task, original, lang, hook, restates, extra, replaced in grid:
        def build(cls, T):
            lp = make_loop(cls, T, cfg={"goal_readback": on}, original=original, queries={"hook": hook}, extra=extra,
                           _restates=restates, language=Lang(T, english="put the cup on the shelf", trusted=restates))
            return lp, make_run(T=T, task=task, lang=lang, dialogue=list(dlg), replaced=replaced)
        a, b = both(build, lambda lp, r: lp._issues(r, [dict(g) for g in goals]))
        assert a == b, (goals, on, dlg, task, original, lang, hook, restates, extra, replaced)
        seen.add(b[1][:60])
    assert len(seen) >= 6
    for restates, dlg in itertools.product((False, True), ([], [("user", "a")], [("user", "a"), ("user", "b")])):
        res = check(lambda cls, T: (make_loop(cls, T, _restates=lambda r, a: restates and a == "b"), make_run(T=T)),
                    lambda lp, r: lp._object_source(r, [t for _w, t in dlg]))
        assert res[1] in ('"put the cup on the shelf"', '"b"')


def _readback_loop(cls, T, issues, samples, cfg, unverified, resample, forbids, dropped):
    lp = make_loop(cls, T, cfg={"understand_samples": samples, **cfg}, _issues=lambda r, goals: [dict(i) for i in issues],
                   _unverified_objects=unverified, _resample=("gen", [{"event": "sample"}], resample),
                   _forbids=lambda g, p: forbids, queries={"goal_dropped": lambda r, g: dropped})
    return lp


def test_goal_readback_equals_the_reference_over_a_grid():
    seen = set()
    issue_sets = ([], [{"kind": "object", "what": "bowl"}], [{"kind": "place", "what": "bench"}, {"kind": "noverb", "what": ""}])
    grid = itertools.product(issue_sets, (0, 2), ((), ("sampled",), ("asked",)), (False, True), (False, True),
                             (None, _STOP, [dict(GOAL2)], [dict(GOAL), dict(GOAL3)]), (False, True), (False, True),
                             (None, ["a doubt"]), (False, True))
    for issues, samples, flags, unv, agree_on, resample, forbids, dropped, kd, dummy in grid:
        def build(cls, T):
            lp = _readback_loop(cls, T, issues, samples, {"dialog_agree": agree_on}, [1] if unv else [], resample, forbids, dropped)
            r = make_run(T=T, goals=[dict(GOAL), dict(GOAL3)], sampled="sampled" in flags, issue_asked="asked" in flags,
                         kind_doubts=list(kd or []))
            return lp, r
        a, b = both(build, lambda lp, r: lp._goal_readback(r))
        assert a == b, (issues, samples, flags, unv, agree_on, resample, forbids, dropped, kd)
        seen.add((b[1][:30], len(b[0])))
    assert len(seen) >= 4


def test_resample_equals_the_reference():
    outs = set()
    cases = itertools.product(
        (0, 1, 2, 3), ({"status": "clear"}, {"status": "unsafe"}, {"status": "infeasible"}), (None, "Q?"),
        (None, LLMBudgetExceeded("x"), LLMUnavailable("y")), ([], [{"kind": "place", "what": "bench"}]), (False, True))
    for n, u, q, fail, issues, same_goals in cases:
        def build(cls, T):
            def understand(*a, **k):
                T.append(("understand", k.get("sample")))
                if fail:
                    raise fail
                return u
            lp = make_loop(cls, T, cfg={"understand_samples": n}, _understand=understand, _llm_failed=lambda *a, **k: {"event": "done"},
                           _ground_understood=lambda r, uu: ([dict(GOAL)] if same_goals else [dict(GOAL2)], q),
                           _issues=lambda r, g: [dict(i) for i in issues])
            return lp, make_run(T=T)
        a, b = both(build, lambda lp, r: lp._resample(r, ["doubt"]))
        assert a == b, (n, u, q, fail, issues, same_goals)
        outs.add((b[1][:20], len(b[0])))
    assert len(outs) >= 3


# --- _read_back: the hook-and-service calls, the answers, the corrections ---------------------------------------------------------
def test_read_back_equals_the_reference_over_a_grid():
    seen, n = set(), 0
    answers = (("yes", True, ""), ("no", False, ""), ("no, the bench", False, "the bench"), ("no, from the shelf", False, "from the shelf"),
               ("hmm", None, ""), ("I do not know", False, ""))
    grid = itertools.product(answers, (False, True), (None, 1, 3), (False, True), (False, True), (None, ["not sure"]), (False, True),
                             (False, True), ((None, []), ("same", []), ("edit", [(0, "bench@porch")]), ("edit", [(0, "@speaker")])),
                             (False, True), (False, True))
    for (ans, ok_, rest), agree_on, limit, unrefused, converge, unsure, restate_rb, rejected, edit, patched, cancelled in grid:
        def build(cls, T):
            from robot_agent.core.planning import dialog_converge
            s = dialog_converge.goal_sig([dict(GOAL)])
            lp = make_loop(cls, T, cfg={"dialog_agree": agree_on, "dialog_agree2": agree_on}, converge=converge,
                           queries={"confirm_limit": limit, "unrefused": unrefused},
                           _ask_yes_no=lambda r, q: (ans, ok_, rest), _correction_edit=lambda r, t: edit,
                           _patch_goals=("gen", [{"event": "patched"}], [dict(GOAL2)] if patched else None))
            lp._cancelled = (lambda r: {"event": "done", "status": "aborted"}) if cancelled else (lambda r: None)
            r = make_run(T=T, goals=[dict(GOAL)], unsure=unsure, dialogue=[("robot", "q"), ("user", "x")], heard=1, confirms=1,
                         rejected=[s] if rejected else [], same_used=False, restate_readback=restate_rb)
            return lp, r
        a, b = both(build, lambda lp, r: lp._read_back(r))
        assert a == b, (ans, agree_on, limit, unrefused, converge, unsure, restate_rb, rejected, edit, patched, cancelled)
        n += 1
        seen.add(b[1][:40])
    assert n > 5000 and len(seen) >= 5          # done, next, stop, the which-part question, the what-do question


def test_read_back_skips_when_nothing_asks_for_it_and_the_pin_branch_equals_the_reference():
    for dialogue, heard, ca, ct, unsure, force in itertools.product(([], [("user", "x")]), (0, 1), (False, True), (False, True),
                                                                    (None, ["u"]), (False, True)):
        res = check(lambda cls, T: (make_loop(cls, T, cfg={"confirm_answers": ca, "confirm_task": ct},
                                              _ask_yes_no=lambda r, q: ("yes", True, ""),
                                              queries={"confirm_limit": None}),
                                    make_run(T=T, dialogue=list(dialogue), heard=heard, unsure=unsure, force_readback=force,
                                             goals=[dict(GOAL)])),
                    lambda lp, r: lp._read_back(r), (dialogue, heard, ca, ct, unsure, force))
    # a correction that names a place, not an edit: the pin and the replaced set are the same in both
    def build(cls, T):
        lp = make_loop(cls, T, _ask_yes_no=lambda r, q: ("no, the bench", False, "the bench"), _correction_edit=lambda r, t: (None, []),
                       queries={"confirm_limit": None})
        return lp, make_run(T=T, goals=[dict(GOAL)], dialogue=[("user", "x")], heard=1)
    res = check(build, lambda lp, r: lp._read_back(r))
    assert "pin_to" in res[3] and "bench@porch" in res[3] and "replaced" in res[3]


def test_a_deliberate_edit_of_the_moved_code_is_caught(monkeypatch):
    """The differential bites: a changed ``_issues`` (here: dropping the verb-lost issue) differs from the reference."""
    def build(cls, T):
        return make_loop(cls, T, queries={}), make_run(T=T, task="the cup", goals=[dict(GOAL)])
    real = AssistLoop._issues
    monkeypatch.setattr(AssistLoop, "_issues", lambda self, r, goals: real(self, r, goals)[1:])
    a, b = both(build, lambda lp, r: lp._issues(r, [dict(GOAL)]))
    assert a != b
