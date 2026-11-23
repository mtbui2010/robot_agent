"""Step S16a: the understand model call and the capability check (_sampling, _understand, _planner_model, _um_for, _understand_llm,
runaway, _ask_understand_routed, _kinds, _flag_prompt, _verify_verdict, _CANNOT, _CAN_OPEN, _cannot_line, _over_limits,
_capability_check, _safety_profile, _forbids, _understand_schema, _cannot_do) moved verbatim from assist_loop.py to the
UnderstandCall mixin in phases/understand_call.py. These tests prove nothing changed: (1) the mixin is in AssistLoop's method
resolution order, serves the nineteen names, the loop file no longer defines them, ``run_blocking`` and ``_with_understand_events``
stay in the loop file, a name patched on the class still wins, and the moved module is small; (2) the method bodies are the same
syntax trees as the reference copy (tests/legacy_ref/s16a_understand.py) apart from the named import / constant seams; (3) a
differential test against that copy over grids of made-up options, goals, manifests and scripted model answers: the old and the
new class give the same prompt, the same schema, the same understanding, the same refusal text and the same calls to the model, and
the hooks ``on_understand_prompt`` / ``on_understand_schema`` are called at the same points; (4) the grids reach the interesting
branches, so the comparison bites; (5) a value patched on ``assist_loop.UNDERSTAND_PROMPT`` / ``UNDERSTAND_SCHEMA`` still reaches
the model. Made-up robots and words only (the fakebot robot and its sim ENV)."""

import ast
import itertools
import json
import types
from pathlib import Path

import pytest

from legacy_ref import s16a_understand as old
from robot_agent.core.planning import assist_loop, goals as goal_kinds
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.phases import PHASE_MIXINS, understand_call
from robot_agent.core.planning.robot import RobotView

NAMES = ("_sampling", "_understand", "_planner_model", "_um_for", "_understand_llm", "runaway", "_ask_understand_routed",
         "_kinds", "_flag_prompt", "_verify_verdict", "_cannot_line", "_over_limits", "_capability_check", "_safety_profile",
         "_forbids", "_understand_schema", "_cannot_do")
CONSTS = ("_CANNOT", "_CAN_OPEN")
STAY = ("run_blocking", "_with_understand_events", "_heard_as", "_llm")
_ROBOT = RobotView.load("fakebot")
LOOP_FILE = Path(assist_loop.__file__)
MIXIN_FILE = Path(understand_call.__file__)


class OldLoop(old.RefUnderstand, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert understand_call.UnderstandCall in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(understand_call.UnderstandCall) < mro.index(assist_loop.WhichOne)
    for n in NAMES + CONSTS:
        assert n not in vars(AssistLoop), n
        assert n in vars(understand_call.UnderstandCall), n
        assert getattr(AssistLoop, n) is not None


def test_the_loop_file_does_not_define_them_any_more():
    src = LOOP_FILE.read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n
    for n in CONSTS:
        assert f"    {n} = " not in src, n


def test_what_must_stay_in_the_loop_file_stays():
    src = LOOP_FILE.read_text()
    for n in STAY:
        assert f"def {n}(" in src, n
        assert n in vars(AssistLoop), n
    assert "\nUNDERSTAND_SCHEMA = {" in src and "\nUNDERSTAND_PROMPT = " in src


def test_a_name_patched_on_the_class_still_wins(monkeypatch):
    monkeypatch.setattr(AssistLoop, "_cannot_do", lambda self, goals: "patched")
    lp = AssistLoop.__new__(AssistLoop)
    assert lp._cannot_do([]) == "patched"


def test_runaway_is_still_a_static_method_of_the_class():
    assert AssistLoop.runaway({"goals": [{}, {}]}, 1) == "goals"
    assert AssistLoop.runaway({"goals": [{"a": 1}] * 3}, None) == "repeat"
    assert AssistLoop.runaway({"goals": [{"a": 1}]}, 1) == ""


def test_the_moved_module_is_small():
    lines = MIXIN_FILE.read_text().splitlines()
    assert len(lines) <= 300
    for n in ast.walk(ast.parse(MIXIN_FILE.read_text())):
        if isinstance(n, ast.FunctionDef):
            assert n.end_lineno - n.lineno + 1 <= 50, n.name


def test_the_texts_equal_the_reference():
    for c in CONSTS:
        assert getattr(AssistLoop, c) == getattr(OldLoop, c) == getattr(old.RefUnderstand, c)
        assert isinstance(getattr(AssistLoop, c), str) and getattr(AssistLoop, c)


# -- (2) the bodies are the reference's bodies ---------------------------------------------------------------------------------
def _methods(path, cls):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}


class _Seams(ast.NodeTransformer):
    """Rewrites the three seams the move changed, on the NEW tree: the relative imports, the two constants read through ``_core()``."""

    def visit_ImportFrom(self, node):
        return ast.ImportFrom(module=node.module.split(".")[-1] if node.module else None, names=node.names, level=0)

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "_core":
            return ast.Name(id=node.attr, ctx=node.ctx)
        return node


def _dump(fn, seams=False):
    node = _Seams().visit(ast.parse(ast.unparse(fn))) if seams else fn
    for n in ast.walk(node):                                   # the docstring and the line numbers are not behaviour
        for f in ("lineno", "end_lineno", "col_offset", "end_col_offset"):
            if hasattr(n, f):
                setattr(n, f, 0)
    return ast.dump(node)


def test_method_bodies_are_the_reference_bodies():
    new, ref = _methods(MIXIN_FILE, "UnderstandCall"), _methods(old.__file__, "RefUnderstand")
    assert set(NAMES) == set(new) == set(ref)
    for n in NAMES:
        assert _dump(new[n], seams=True) == _dump(ref[n], seams=True), n


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
class Client:
    """A scripted model client; every call is recorded (prompt, format, kwargs)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def chat(self, prompt="", format=None, **kw):
        self.calls.append((prompt, json.dumps(format, sort_keys=True), sorted(kw.items())))
        a = self.answers.pop(0) if self.answers else "{}"
        if isinstance(a, Exception):
            raise a
        return json.dumps(a) if isinstance(a, (dict, list)) else str(a)


class Robot:
    """The parts of a RobotView the moved methods read; ``limits`` and ``texts`` are the made-up manifest data under test."""

    def __init__(self, actions=None, limits=None, texts=None):
        self.plan_actions = _ROBOT.plan_actions if actions is None else actions
        self.manifest = types.SimpleNamespace(limits=limits)
        self.texts = texts or {}
        self.pack = _ROBOT.pack

    def text(self, k, *a):
        return self.texts[k]

    def has_text(self, k):
        return k in self.texts

    def robot_description(self):
        return "ROBOT-ABILITIES"


class Opts:
    """The option dispatcher stub: records the two understand hooks and the query, and tags what the hook gets."""

    def __init__(self):
        self.log = []

    def transform(self, hook, value):
        self.log.append(hook)
        return value if hook != "on_understand_schema" else dict(value, tagged=True)

    def query(self, name, *a):
        self.log.append(name)
        return "forbidden" if name == "forbids" else None


class Lang:
    def understand_view(self, task, dialogue, fmt, pivot=None):
        return task, "|PIVOT" if pivot else "", [fmt(t, en) if False else f"{who}: {t}" for who, t in (dialogue or [])]


class WorldStub:
    def as_text(self):
        return "WORLD"


class PlacesStub:
    def describe(self):
        return "PLACES"


class MemoryStub:
    def answers_text(self):
        return "ANSWERS"


def _make(cls, cfg=None, robot=None, client=None, um=None, llm_cfg=None, original=None):
    lp = cls.__new__(cls)
    base = {"understand_seed": "0", "understand_temperature": "0", "understand_sample_temperature": "0.7",
            "understand_retries": 1}
    lp.cfg = dict(base, **(cfg or {}))
    lp.robot = robot or Robot()
    lp.options = Opts()
    lp.original = original
    lp._llm_cfg = llm_cfg if llm_cfg is not None else {"model": "planner-m", "url": "http://planner"}
    lp.__dict__["_language"] = Lang()
    lp._lab_log = []
    lp._lab = lambda flag, **info: lp._lab_log.append((flag, sorted(info.items())))
    lp._llm_client = client
    lp.__dict__["_um_clients"] = dict(um or {})
    return lp


FLAGS = ("understand_schema_strict", "understand_verify_verdict", "understand_goals_first", "understand_give_kind")


def _flag_grid():
    for bits in itertools.product((False, True), repeat=len(FLAGS)):
        yield dict(zip(FLAGS, bits))


def test_flag_prompt_schema_and_kinds_equal_the_reference():
    prompt = assist_loop.UNDERSTAND_PROMPT
    seen = set()
    for cfg in _flag_grid():
        a, b = _make(OldLoop, cfg), _make(AssistLoop, cfg)
        assert a._flag_prompt(prompt) == b._flag_prompt(prompt)
        assert a._kinds() == b._kinds()
        sa, sb = a._understand_schema(), b._understand_schema()
        assert json.dumps(sa) == json.dumps(sb)                          # equal, key order too (it binds the decoder)
        assert a.options.log == b.options.log == ["on_understand_schema"]
        assert sb["tagged"] is True
        seen.add((bool(cfg["understand_verify_verdict"]), tuple(sb["properties"])[0]))
    assert (True, "status") in seen and (False, "goals") in seen


def test_the_schema_is_not_changed_in_place():
    before = json.dumps(assist_loop.UNDERSTAND_SCHEMA)
    for cfg in _flag_grid():
        _make(AssistLoop, cfg)._understand_schema()
    assert json.dumps(assist_loop.UNDERSTAND_SCHEMA) == before


LIMITS = [None, {}, {"payload_kg": 2}, {"payload_kg": 2, "max_object_size_m": 0.4},
          {"payload_kg": 2, "max_object_size_m": 0.4, "objects": {"water bottle": {"weight_kg": 3, "size_m": 0.3},
                                                                "box": {"weight_kg": 1, "size_m": 0.9},
                                                                "cup": {"weight_kg": 0.2, "size_m": 0.1}}},
          {"max_object_size_m": 0.4, "objects": {"box": {"weight_kg": 1, "size_m": 0.9}}},
          {"payload_kg": 2, "objects": {"box": "heavy"}}]
ACTIONS = [None, [], ["Find", "MoveTo", "Pick", "Place"], ["Find", "MoveTo", "Pick", "Place", "Open", "Close"]]
TEXTS = [{}, {"cannot_list": "It cannot lift boxes."}]
GOALS = [[], [{"type": "on", "object": "cup", "from": "", "to": "table"}],
         [{"type": "on", "object": "big water bottle", "from": "", "to": "table"}],
         [{"type": "in", "object": "box", "from": "shelf", "to": "drawer"}],
         [{"type": "open", "object": "drawer", "from": "", "to": ""}],
         [{"type": "say", "object": "hello", "from": "", "to": ""}, {"type": "on", "object": "box", "from": "", "to": "table"}]]


def test_refusal_reasons_equal_the_reference():
    hit = set()
    for acts, lim, txt, goals in itertools.product(ACTIONS, LIMITS, TEXTS, GOALS):
        rb = dict(actions=acts, limits=lim, texts=txt)
        a, b = _make(OldLoop, robot=Robot(**rb)), _make(AssistLoop, robot=Robot(**rb))
        assert a._cannot_line() == b._cannot_line()
        assert a._over_limits(goals) == b._over_limits(goals)
        assert a._cannot_do(goals) == b._cannot_do(goals)
        hit.add((b._over_limits(goals) is not None, b._cannot_do(goals) is not None))
    assert hit == {(True, False), (False, True), (False, False), (True, True)} or {(True, False), (False, False)} <= hit
    assert any("It can carry at most" in _make(AssistLoop, robot=Robot(limits=l))._cannot_line() for l in LIMITS if l)
    assert _make(AssistLoop, robot=Robot(actions=["Open"]))._cannot_line().endswith(AssistLoop._CAN_OPEN)


RUNS = [types.SimpleNamespace(goals=g, task=t, dialogue=d, **kw)
        for g in GOALS for t in ("bring the cup to the table please", "uh", "  ")
        for d, kw in (([], {}), ([("robot", "q")], {}), ([], {"repeat_asked": True}))]
UNDERSTANDINGS = [{}, {"unparsed": True}, {"reason": "the request is unclear", "question": ""},
                  {"reason": "I cannot do that", "question": "Do you want X?"}, {"reason": "too heavy", "reason_type": "payload"},
                  {"reason": "no skill", "reason_type": "missing_skill"}, {"reason": "x", "reason_type": "other"},
                  {"reason": "x", "reason_type": "reach"}]


def test_capability_check_equals_the_reference():
    outs = set()
    for r, u, lim in itertools.product(RUNS, UNDERSTANDINGS, LIMITS[:5]):
        a, b = _make(OldLoop, robot=Robot(limits=lim)), _make(AssistLoop, robot=Robot(limits=lim))
        x, y = a._capability_check(r, u), b._capability_check(r, u)
        assert x == y, (r, u, lim)
        outs.add(y)
    assert outs == {"repeat", "override", None}


def test_verify_verdict_equals_the_reference():
    ds = [{"status": st, "reason_type": rt, "goals": g, "missing_action": ma, "question": "q"}
          for st in ("clear", "infeasible", "ambiguous", "unsafe") for rt in ("", "location_unknown", "payload")
          for g in GOALS for ma in ("", "Pour", "Pick")]
    outs = set()
    for d, lim in itertools.product(ds, LIMITS[:5]):
        a, b = _make(OldLoop, robot=Robot(limits=lim)), _make(AssistLoop, robot=Robot(limits=lim))
        da, db = json.loads(json.dumps(d)), json.loads(json.dumps(d))
        a._verify_verdict(da)
        b._verify_verdict(db)
        assert da == db and a._lab_log == b._lab_log, (d, lim)
        outs.add(tuple(x[0] for x in b._lab_log))
    assert {(), ("understand_verify_verdict",)} <= outs


def test_sampling_and_the_model_choice_equal_the_reference():
    for seed, t0, t1, sample in itertools.product(("0", "7", "", "none", "-1", 3), ("0", "0.2"), ("0.7", "1"), (0, 1, 2)):
        cfg = {"understand_seed": seed, "understand_temperature": t0, "understand_sample_temperature": t1}
        assert _make(OldLoop, cfg)._sampling(sample) == _make(AssistLoop, cfg)._sampling(sample)
    maps = [{}, {"ko": "ko-model"}, {"ko": "planner-m"}, {"ko": ""}]
    for um, langs, lang in itertools.product(("", "side-m", "planner-m"), maps, (None, "en", "ko", "KO-kr", "ja")):
        cfg = {"understand_model": um, "understand_model_lang": langs}
        for llm_cfg in ({"model": "planner-m"}, None, {}):
            a, b = _make(OldLoop, cfg, llm_cfg=llm_cfg), _make(AssistLoop, cfg, llm_cfg=llm_cfg)
            assert a._um_for(lang) == b._um_for(lang)
            assert a._planner_model() == b._planner_model()


def test_runaway_equals_the_reference():
    g = {"type": "on", "object": "cup"}
    cases = [{}, {"goals": "x"}, {"goals": [g] * 3}, {"goals": [g, {"type": "in"}, g]}, {"goals": [g] * 5},
             {"goals": [{"type": k} for k in "abcdef"]}, {"goals": [1, 2, g]}]
    for d, n in itertools.product(cases, (None, 0, 1, 2, 5, "3")):
        assert OldLoop.runaway(d, n) == AssistLoop.runaway(d, n)


GOOD = {"status": "clear", "reason": "", "question": "", "goals": [
    {"type": "on", "object": "the  Red Cup", "from": " shelf ", "to": "table", "said": 3}, {"type": "say", "object": " Hello there "},
    "junk", {"type": "give", "object": "a mug", "to": "me"}]}
ANSWERS = [GOOD, "not json", {"status": "unsafe", "reason": "no", "goals": "x"}, {"goals": []}, RuntimeError("down"),
           {"status": "infeasible", "reason": "r", "reason_type": "missing_skill", "goals": [], "missing_action": "Pour"},
           {"goals": [{"type": "on", "object": "cup", "from": "", "to": "table"}] * 4}]


def _call(cls, cfg, answers, dialogue=(), sample=0, pivot=None, note="", original=None, um_client=None, lang="en"):
    client = Client(*answers)
    um = {"side-m": um_client} if um_client is not None else None
    lp = _make(cls, cfg, client=client, um=um, original=original)
    lp._run_lang = lang
    budget = types.SimpleNamespace(taken=0, take=lambda what="": None)
    try:
        d = lp._understand("bring the cup to the table", PlacesStub(), MemoryStub(), WorldStub(), list(dialogue), budget=budget,
                           sample=sample, note=note, pivot=pivot)
    except Exception as e:                                            # noqa: BLE001 - the error class is part of the result
        d = ("raised", type(e).__name__)
    return d, client.calls, (um_client.calls if um_client is not None else None), lp._lab_log, lp.options.log, \
        lp.__dict__.get("um_last"), lp.__dict__.get("um_fallbacks"), lp.__dict__.get("_um_pending")


def test_the_understand_call_equals_the_reference():
    outs = set()
    cfgs = [dict(c, understand_retries=n) for c in _flag_grid() if c["understand_verify_verdict"] or c["understand_give_kind"]
            for n in (0, 1)] + [{}, {"understand_think": 1}]
    for cfg, ans, dlg, sample, orig in itertools.product(cfgs[:6] + cfgs[-2:], ANSWERS, ((), (("user", "to the desk"),)), (0, 2),
                                                         (None, "bring me the mug")):
        a = _call(OldLoop, cfg, [ans, ans], dlg, sample, original=orig)
        b = _call(AssistLoop, cfg, [ans, ans], dlg, sample, original=orig)
        assert a == b, (cfg, ans, dlg, sample)
        outs.add(json.dumps(b[0], sort_keys=True) if not isinstance(b[0], tuple) else b[0])
    assert any("unparsed" in o for o in outs) and ("raised", "LLMUnavailable") in outs
    assert any("junk" not in o and "mug" in o for o in outs)


def test_the_prompt_the_model_gets_equals_the_reference_and_calls_the_hook_once():
    a = _call(OldLoop, {"understand_schema_strict": True}, [GOOD], (("user", "the desk"),), note="NOTE", pivot="x")
    b = _call(AssistLoop, {"understand_schema_strict": True}, [GOOD], (("user", "the desk"),), note="NOTE", pivot="x")
    assert a == b
    prompt = b[1][0][0]
    assert "ROBOT-ABILITIES" in prompt and "PLACES" in prompt and "ANSWERS" in prompt and "WORLD" in prompt and "NOTE" in prompt
    assert "|PIVOT" in prompt and "Write \"said\" and \"content\"" in prompt
    assert b[4].count("on_understand_prompt") == 1 and b[4].count("on_understand_schema") == 1


def test_the_routed_call_and_its_fallbacks_equal_the_reference():
    cfg = {"understand_model": "side-m", "understand_max_goals": 3, "understand_max_tokens": 64, "understand_url": "http://u",
           "understand_retries": 1}
    runaway = {"goals": [{"type": "on", "object": "a", "from": "", "to": "t"}] * 3, "status": "clear"}
    cases = [([GOOD], [GOOD]), (["not json"], [GOOD]), ([runaway], [GOOD]), ([RuntimeError("side down")], [GOOD]),
             ([GOOD], []), ([runaway], ["not json", "still not"])]
    seen = set()
    for (side, plan), lang, sample in itertools.product(cases, ("en", "ko"), (0, 1)):
        outs = []
        for cls in (OldLoop, AssistLoop):
            res = _call(cls, cfg, plan, sample=sample, lang=lang, um_client=Client(*side))
            outs.append(res)
        assert outs[0] == outs[1], (side, plan, lang, sample)
        seen.add((outs[1][6] or [None])[0] if outs[1][0] != ("raised", "LLMBadOutput") else "bad")
    assert {None, "non_json", "repeat", "unavailable"} <= seen


def test_understand_llm_builds_the_same_clients(monkeypatch):
    import robot_agent.connect.llm as llm_mod
    built = []
    monkeypatch.setattr(llm_mod, "init_llm_client", lambda cfg: built.append(dict(cfg)) or object())
    cfgs = [{"understand_model": "side-m"}, {"understand_model": "side-m", "understand_url": "http://u", "understand_think": 0},
            {"understand_model": "side-m", "translate_url": "http://t"}, {"understand_model": "planner-m"}, {}]
    results = {}
    for cls in (OldLoop, AssistLoop):
        del built[:]
        planned = []
        for cfg in cfgs:
            lp = _make(cls, cfg)
            lp._llm = lambda: "planner-client"
            lp._run_lang = "en"
            got = lp._understand_llm()
            planned.append(got if isinstance(got, str) else "built")
            assert lp._understand_llm() is got                       # a client is built once per model
        results[cls] = (list(built), planned)
    assert results[OldLoop] == results[AssistLoop]
    assert [c["role"] for c in results[AssistLoop][0]] == ["understand"] * 3
    assert results[AssistLoop][1] == ["built"] * 3 + ["planner-client"] * 2


def test_safety_profile_and_forbids_equal_the_reference():
    a, b = _make(OldLoop), _make(AssistLoop)
    assert a._forbids({"type": "on"}, None) == b._forbids({"type": "on"}, None) == "forbidden"
    assert a.options.log == b.options.log == ["forbids"]
    assert type(a._safety_profile()) is type(b._safety_profile())


# -- (5) a patched module constant still reaches the model --------------------------------------------------------------------
def test_a_patched_prompt_reaches_the_model(monkeypatch):
    marker = "PATCHED-{abilities}{cannot}{goal_kinds}{places}{answers}{robot}{dialogue}{task}{original}"
    monkeypatch.setattr(assist_loop, "UNDERSTAND_PROMPT", marker)
    d, calls, *_ = _call(AssistLoop, {}, [GOOD])
    assert calls[0][0].startswith("PATCHED-ROBOT-ABILITIES")


def test_a_patched_schema_reaches_the_model(monkeypatch):
    patched = json.loads(json.dumps(assist_loop.UNDERSTAND_SCHEMA))
    patched["properties"]["reason"] = {"type": "string", "description": "PATCHED"}
    monkeypatch.setattr(assist_loop, "UNDERSTAND_SCHEMA", patched)
    d, calls, *_ = _call(AssistLoop, {}, [GOOD])
    assert "PATCHED" in calls[0][1]


def test_the_pack_kinds_still_come_from_the_robot():
    lp = _make(AssistLoop, {"understand_give_kind": True})
    assert lp._kinds() == goal_kinds.kinds_for(lp.robot.plan_actions, ("give",), lp.robot.pack)
    assert Places is assist_loop.Places
