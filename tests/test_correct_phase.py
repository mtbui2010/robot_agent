"""Step S19: corrections, patches and the grounding of an understanding (_complete_override, _ground_understood, _ground_v2, _canon,
_unverified_objects, _local_names, _correction_texts, _correction_edit, _patch_goals, and the constants _OVERRIDE_NOTE and
_NAMES_PROMPT) moved verbatim from assist_loop.py to the CorrectPhase mixin in phases/correct.py. These tests prove nothing changed:
(1) the mixin is in AssistLoop's method resolution order, serves the nine names and two constants, the loop file no longer defines
them, what stays (``_refused_reading``, ``_unsure_data``, ``_agree_state``, ``_cancelled``) is still in the loop file, a name patched on
the class still wins, and the moved module stays small; (2) the method bodies are the same syntax trees, and the class body the same
text, as the reference copy (tests/legacy_ref/s19_correct.py); (3) differential tests against that copy: the old and the new class give
the same events, return values, calls to their collaborators and changes to the run state over grids of made-up goals, dialogues,
options and scripted collaborators (the real ``goal_fix``, ``goal_filter`` and ``dialog_converge`` rules run in both); (4) the grids
reach the interesting branches, so the comparison bites. Made-up places and words only."""

import ast
import itertools
import json
import types
from pathlib import Path

from legacy_ref import s19_correct as old
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.assist_loop import AssistLoop, Places, _STOP
from robot_agent.core.planning.phases import PHASE_MIXINS, correct
from robot_agent.core.planning.understand import LLMBudgetExceeded, LLMUnavailable

NAMES = ("_complete_override", "_ground_understood", "_ground_v2", "_canon", "_unverified_objects", "_local_names",
         "_correction_texts", "_correction_edit", "_patch_goals")
CONSTS = ("_OVERRIDE_NOTE", "_NAMES_PROMPT")
STAY = ("_refused_reading", "_unsure_data", "_agree_state", "_cancelled")
LOOP_FILE = Path(assist_loop.__file__)
MIXIN_FILE = Path(correct.__file__)


class OldLoop(old.RefCorrect, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert correct.CorrectPhase in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(correct.CorrectPhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES + CONSTS:
        assert n not in vars(AssistLoop), n
        assert n in vars(correct.CorrectPhase), n
        assert getattr(AssistLoop, n) is not None


def test_the_loop_file_does_not_define_them_any_more():
    src = LOOP_FILE.read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n
    for n in CONSTS:
        assert f"    {n} = " not in src, n


def test_what_stays_is_still_in_the_loop_file():
    src = LOOP_FILE.read_text()
    for n in STAY:
        assert f"def {n}(" in src, n
        assert n in vars(AssistLoop), n


def test_a_name_patched_on_the_class_still_wins(monkeypatch):
    monkeypatch.setattr(AssistLoop, "_canon", lambda self, r, goals: ("patched", goals))
    assert AssistLoop.__new__(AssistLoop)._canon(None, []) == ("patched", [])


def test_the_moved_module_is_small():
    assert len(MIXIN_FILE.read_text().splitlines()) <= 300


def test_the_stop_marker_is_the_loops_own_value():
    assert correct._STOP is _STOP and old._STOP is _STOP


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
    new, ref = _methods(MIXIN_FILE, "CorrectPhase"), _methods(old.__file__, "RefCorrect")
    assert set(NAMES) == set(new) == set(ref)
    for n in NAMES:
        assert _dump(new[n]) == _dump(ref[n]), n


def test_the_moved_text_is_the_reference_text():
    """Beyond the syntax trees: every line of the reference class body, comments and docstrings included, is in the new class body."""
    def body(path, cls):
        lines = Path(path).read_text().splitlines()
        return lines[next(i for i, l in enumerate(lines) if l.startswith(f"class {cls}")) + 1:]
    assert body(MIXIN_FILE, "CorrectPhase") == body(old.__file__, "RefCorrect")


def test_the_constants_are_the_reference_constants():
    assert correct.CorrectPhase._OVERRIDE_NOTE == old.RefCorrect._OVERRIDE_NOTE
    assert correct.CorrectPhase._NAMES_PROMPT == old.RefCorrect._NAMES_PROMPT


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
ENV = {"shelf@hall": {"aliases": ["shelf"]}, "bench@porch": {"aliases": ["bench"]}, "owner": {
    "person": "owner", "speaker": True, "aliases": ["me"], "loc": {"x": 0.0, "y": 0.0}}}
GOAL = {"type": "on", "object": "cup", "from": "", "to": "shelf@hall"}
GOAL2 = {"type": "on", "object": "cup", "from": "", "to": "bench@porch"}
GOAL3 = {"type": "on", "object": "bowl", "from": "", "to": "shelf@hall"}
GOAL4 = {"type": "on", "object": "cup", "from": "", "to": "owner", "hand": True}
OPEN = {"type": "open", "object": "shelf@hall", "from": "", "to": ""}
GOALS = [[], [GOAL], [GOAL2], [GOAL, GOAL3], [GOAL4, GOAL3], [OPEN, GOAL]]


def _plain(x):
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
    """The option dispatcher stub: queries answer from a script, every call is recorded."""

    def __init__(self, T, queries=None):
        self.T, self.queries = T, queries or {}

    def query(self, name, *a):
        self.T.append(("query", name, jd(a[1:])))
        return self.queries.get(name)

    def decide(self, name, *a):
        self.T.append(("decide", name))
        return None

    def provides(self, name):
        self.T.append(("provides", name))
        return bool(self.queries.get("provides_" + name))


class Lang:
    """The language layer stub: ``hear``, the heard table, declared names, the translator call, all recorded."""

    def __init__(self, T, english="the cup", trusted=True, heard=None, declared=None, chat=None, plausible=None, take=True):
        self.T, self.english, self.trusted, self.heard = T, english, trusted, heard or {}
        self.declared, self.chat, self.plausible, self.take = declared or {}, chat, plausible, take
        self.names_cache = {}

    def hear(self, text, pivot=None):
        self.T.append(("hear", text))
        return types.SimpleNamespace(english=self.english, trusted=self.trusted)

    def dialog_model(self, lg):
        self.T.append(("dialog_model", lg))
        return "dm"

    def declared_name(self, o, lg):
        self.T.append(("declared", o, lg))
        return self.declared.get(o)

    def _take(self):
        self.T.append(("take",))
        return self.take

    def chat_out(self, lg, prompt):
        self.T.append(("chat_out", lg, prompt))
        if isinstance(self.chat, Exception):
            raise self.chat
        return self.chat or ""

    def plausible_name(self, n, o, lg):
        self.T.append(("plausible", n, o, lg))
        return self.plausible


class Mem:
    def __init__(self, objects=None):
        self.data = {"objects": objects or {}}


def make_run(**kw):
    T = kw.pop("T")
    r = types.SimpleNamespace(task="put the cup on the shelf", lang="en", dialogue=[], places=Places(ENV), memory=Mem(), world="W",
                              llm_budget="B", goals=[], confirms=0, pin_to=None, filtered=[])
    r.ev = lambda name, **a: {"event": name, **a}
    r.__dict__.update(kw)
    return r


def _stub(T, name, val):
    def fn(*a, **k):
        T.append((name, jd(a[1:] if a and hasattr(a[0], "ev") else a)))
        if isinstance(val, Exception):
            raise val
        return val(*a, **k) if callable(val) else val
    return fn


def make_loop(cls, T, cfg=None, queries=None, language=None, original=None, converge=True, planner=None, **script):
    lp = cls.__new__(cls)
    lp.cfg = {"goal_filter": False, "understand_samples": 1, **(cfg or {})}
    lp.original = original
    lp.options = Opts(T, queries)
    lp.__dict__["_language"] = language or Lang(T)
    lp._lab = lambda flag, **info: T.append(("lab", flag, jd(info)))
    lp._converge = lambda: converge
    lp.robot = types.SimpleNamespace(i18n=None, manifest=types.SimpleNamespace(planner=None), plan_actions=["Find", "Pick", "PutIn"])
    lp._manifest_planner = lambda k, d=None: planner if planner is not None else d
    lp._remembered = lambda memory, obj: "shelf@hall" if obj == "cup" else None
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
    """Run the same scenario on the old and the new class; returns the two results (events, value, trace, run state, caches)."""
    out = []
    for cls in (OldLoop, AssistLoop):
        T = []
        lp, r = build(cls, T)
        try:
            evs, val = drive(call(lp, r))
        except Exception as e:                                   # noqa: BLE001
            evs, val = [], ("raised", type(e).__name__, str(e))
        state = {k: v for k, v in sorted(vars(r).items()) if k not in ("ev", "places", "memory")}
        out.append((jd(evs), jd(val), T, jd(state), jd(lp.__dict__.get("_tr_cache")), jd(vars(lp.language).get("names_cache"))))
    return out


def check(build, call, label=None):
    a, b = both(build, call)
    assert a == b, label
    return b


def test_correction_texts_and_edit_equal_the_reference():
    seen = set()
    for lang, english, rest, goals, conv, place in itertools.product(
            ("en", "ko"), ("", "the cup", "put the cup on the bench"), ("the cup on the bench", "the cup to me", "no idea", ""),
            GOALS, (True, False), ("shelf@hall", "bench@porch")):
        def build(cls, T):
            return make_loop(cls, T, converge=conv, language=Lang(T, english=english)), make_run(
                T=T, lang=lang, goals=[dict(g) for g in goals])
        a, b = both(build, lambda lp, r: lp._correction_texts(r, rest))
        assert a == b, (lang, english, rest)
        a, b = both(build, lambda lp, r: lp._correction_edit(r, rest))
        assert a == b, (lang, english, rest, goals, conv)
        seen.add(b[1][:24])
    assert any("edit" in s for s in seen) and any("same" in s for s in seen) and any("null" in s for s in seen)


def test_patch_goals_equals_the_reference():
    seen = set()
    for goals, edits, said, q, nplaced in itertools.product(
            GOALS[1:], ([(0, "bench@porch")], [(0, "@speaker")], [(0, "shelf@hall"), (1, "bench@porch")], []),
            ("the cup on the bench", ""), (None, "where?"), (1, 2)):
        if any(i >= len(goals) for i, _d in edits):
            continue

        def build(cls, T):
            def ground(gs, places, memory, text, latest=""):
                return ([dict(g, grounded=True) for g in gs][:nplaced if len(gs) > 1 else None], q)
            return make_loop(cls, T, _ground_goals=ground), make_run(T=T, goals=[dict(g) for g in goals],
                                                                    dialogue=[("user", "put it away")])
        a, b = both(build, lambda lp, r: lp._patch_goals(r, said, edits))
        assert a == b, (goals, edits, said, q, nplaced)
        seen.add(b[1][:12] + str(len(json.loads(b[0]))))
    assert len(seen) >= 2 and any(s.endswith("1") for s in seen) and any(s.endswith("0") for s in seen)         # patched (one event) and not patched (none)


def test_complete_override_equals_the_reference():
    seen = set()
    for goals, samples, u2, took_q, cannot, issue_kinds in itertools.product(
            GOALS[1:], (0, 1), ("ok", "unsafe", "budget", "down"), (None, "q"), (None, "cannot"), (("object",), ("place", "verb"), ("kind",), ())):
        issues = [{"kind": k, "what": "cup"} for k in issue_kinds]

        def build(cls, T):
            def understand(*a, **k):
                if u2 == "budget":
                    raise LLMBudgetExceeded("b")
                if u2 == "down":
                    raise LLMUnavailable("d")
                return {"status": "unsafe" if u2 == "unsafe" else "clear", "goals": [dict(GOAL2)]}
            return make_loop(cls, T, cfg={"understand_samples": samples}, _issues=(lambda r, g: issues if g is r.goals else issues[:1]),
                             _understand=understand, _ground_understood=([dict(GOAL2)], took_q),
                             _cannot_do=cannot, _llm_failed=lambda r, e: {"event": "done", "why": str(e)}), make_run(
                T=T, goals=[dict(g) for g in goals], dialogue=[("user", "go")])
        a, b = both(build, lambda lp, r: lp._complete_override(r))
        assert a == b, (goals, samples, u2, took_q, cannot, issue_kinds)
        seen.add((b[1][:14], len(json.loads(b[0])), b[3].count("sampled")))
    assert len(seen) >= 4 and any(s[1] == 1 for s in seen) and any(s[0] == jd(_STOP) for s in seen)


GROUND_CFGS = ({}, {"goal_filter": True}, {"understand_give_kind": True}, {"no_shared_listener": True},
               {"go_steps": True}, {"kind_anchor": True}, {"kind_anchor": True, "kind_anchor_ask": True}, {"name_canon": True},
               {"inventory_ask": True}, {"go_steps": True, "name_canon": True, "goal_filter": True})
RAWS = ([], [GOAL], [GOAL, GOAL3], [OPEN], [{"type": "on", "object": "", "from": "", "to": "shelf@hall"}],
        [dict(GOAL, said="mug")], [dict(GOAL, content="hello", type="say")], [GOAL4])


def test_ground_understood_equals_the_reference():
    seen = set()
    for cfg, raws, task, answers, pin, q in itertools.product(
            GROUND_CFGS, RAWS, ("put the cup on the shelf", "open the shelf", "tell me hello"),
            ([], [("user", "the shelf")], [("robot", "where?"), ("user", "on the bench")]), (None, "bench@porch"), (None, "which?")):
        def build(cls, T):
            def ground(raw, places, memory, words_, latest=""):
                return [dict(g) for g in raw], q
            return make_loop(cls, T, cfg=cfg, original="put the cup there", _ground_goals=ground,
                             queries={"goal_guard": None, "goal_keep": [], "provides_on_listener_rule": bool(pin)}), make_run(
                T=T, task=task, dialogue=list(answers), pin_to=pin)
        u = {"goals": [dict(g, **{"from": "shelf@hall"} if (pin and g.get("type") == "on") else {}) for g in raws], "status": "clear"}
        a, b = both(build, lambda lp, r: lp._ground_understood(r, {**u, "goals": [dict(g) for g in u["goals"]]}))
        assert a == b, (cfg, raws, task, answers, pin, q)
        seen.add((b[1][:40], len(b[2])))
    assert len(seen) >= 12


def test_ground_v2_and_canon_equal_the_reference():
    seen = set()
    mems = ({}, {"mug": {"loc": "shelf@hall"}}, {"cup": {"loc": "bench@porch"}, "mug": {"loc": "shelf@hall"}})
    for cfg, goals, q, lang, mem, heard, planner in itertools.product(
            GROUND_CFGS, RAWS, (None, "q?"), ("en", "ko"), mems, ({}, {"put the cup on the shelf": True}), (None, "en", "fr")):
        def build(cls, T):
            hd = {"put the cup on the shelf": types.SimpleNamespace(english="the mug", trusted=True)} if heard else {}
            return make_loop(cls, T, cfg=cfg, language=Lang(T, heard=hd), planner=planner,
                             queries={"shared_names": None}), make_run(T=T, goals=[dict(g) for g in goals], lang=lang,
                                                                      memory=Mem(mem), kind_doubts=None)
        a, b = both(build, lambda lp, r: lp._ground_v2(r, [dict(g) for g in goals], q, "put the cup on the shelf"))
        assert a == b, (cfg, goals, q, lang, mem, heard, planner)
        a, b = both(build, lambda lp, r: lp._canon(r, [dict(g) for g in goals]))
        assert a == b, (cfg, goals, lang, mem, heard, planner)
        seen.add((b[1][:30], len(b[2])))
    assert len(seen) >= 6


KO_CUP, KO_BOWL = "컵", "그릇"
KO_REQ = ("컵을 선반에 놓아 줘", "그릇을 선반에 놓아 줘",
          "put the cup on the shelf", "")
KO_GOALS = ([dict(GOAL)], [dict(GOAL), dict(GOAL3)], [dict(GOAL, said=KO_CUP)], [dict(GOAL), dict(OPEN)], [dict(GOAL, object=KO_CUP)])


def test_unverified_objects_and_local_names_equal_the_reference():
    seen = set()
    for cfg_oc, lang, task, goals, declared, chat, plaus, take, dlg in itertools.product(
            ("1", "0", "false"), ("ko", "en", "xx"), KO_REQ, KO_GOALS, ({}, {"cup": KO_CUP}),
            (None, f"{KO_CUP}\n{KO_BOWL}", "1. " + KO_CUP, RuntimeError("down")), (None, "bad"), (True, False),
            ([], [("user", "x"), ("user", KO_BOWL)])):
        def build(cls, T):
            lg = Lang(T, declared=declared, chat=chat, plausible=plaus, take=take)
            return make_loop(cls, T, cfg={"object_check": cfg_oc, "translate_model": "tm"}, language=lg,
                             original=task), make_run(T=T, lang=lang, task=task, goals=[dict(g) for g in goals], dialogue=list(dlg))
        a, b = both(build, lambda lp, r: lp._unverified_objects(r))
        assert a == b, (cfg_oc, lang, task, goals, declared, chat, plaus, take, dlg)
        seen.add((b[1][:30], len(b[2]), b[3].count("name_local")))
    assert len(seen) >= 5 and any(s[1] > 0 for s in seen)
    for objs, lg, chat, take in itertools.product((["cup"], ["cup", "bowl"], []), ("ko", "en"), (None, "x\ny", RuntimeError("e")),
                                                   (True, False)):
        def build(cls, T):
            return make_loop(cls, T, cfg={}, language=Lang(T, chat=chat, take=take)), make_run(T=T)
        check(build, lambda lp, r: lp._local_names(objs, lg), (objs, lg, chat, take))


# -- (4) deliberate edits of the moved code are caught -------------------------------------------------------------------------
def test_an_edit_of_the_moved_code_is_caught(monkeypatch):
    """The comparison bites: a changed ``_correction_texts`` (the correction without its English) differs from the reference."""
    def build(cls, T):
        return make_loop(cls, T, language=Lang(T, english="the cup")), make_run(T=T, lang="ko")
    ok = check(build, lambda lp, r: lp._correction_texts(r, "x"))
    assert len(json.loads(ok[1])) == 2
    monkeypatch.setattr(correct.CorrectPhase, "_correction_texts", lambda self, r, rest: [rest])
    a, b = both(build, lambda lp, r: lp._correction_texts(r, "x"))
    assert a != b
