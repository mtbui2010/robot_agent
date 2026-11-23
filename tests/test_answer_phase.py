"""Step S15: the say / ask plumbing (_in_lang, _ask, _ask_io, _ask_heard, yes_no, _ask_yes_no, _ask_clarify, _asks_where, _essential,
_fresh_question, _restates) moved verbatim from assist_loop.py to the AnswerPhase mixin in phases/answer.py. These tests prove
nothing changed: (1) the mixin is in AssistLoop's method resolution order, serves the eleven names, the loop file no longer
defines them, the two static methods are still callable through the class and a name patched on the class still wins (the golden
mutations do that); (2) a differential test against the verbatim reference copy (tests/legacy_ref/s15_answer.py): the old and the
new class give the same answers, the same events, the same state changes and the same calls to the robot's Ask skill over a grid
of made-up questions, answers and options; (3) the grid really reaches the unclear, silent, corrected and capped branches, so the
comparison bites. Made-up places only (the fakebot robot and the sim ENV)."""

import itertools
import random
import types
from pathlib import Path

import pytest

from legacy_ref import s15_answer as old
from robot_agent.core.planning import assist_loop, speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.phases import PHASE_MIXINS, answer
from robot_agent.core.planning.robot import RobotView

NAMES = ("_in_lang", "_ask", "_ask_io", "_ask_heard", "yes_no", "_ask_yes_no", "_ask_clarify", "_asks_where", "_essential",
         "_fresh_question", "_restates")
_ROBOT = RobotView.load("fakebot")


class OldLoop(old.RefAnswer, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_eleven_names():
    assert answer.AnswerPhase in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(answer.AnswerPhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(AssistLoop), n
        assert n in vars(answer.AnswerPhase), n
        assert getattr(AssistLoop, n) is not None


def test_the_loop_file_does_not_define_them_any_more():
    src = Path(assist_loop.__file__).read_text()
    for n in NAMES:
        assert f"def {n}(" not in src, n


def test_static_methods_are_callable_through_the_class():
    assert AssistLoop.yes_no("yes") == (True, "")
    assert AssistLoop._asks_where(speech.LocalText("q_where_are_you")) is True
    assert AssistLoop._essential(speech.LocalText("q_where_bring")) is True
    r = types.SimpleNamespace(dialogue=[("robot", "what?")], converge=False)
    assert AssistLoop._fresh_question(r, "other") == "other"


def test_a_name_patched_on_the_class_still_wins(monkeypatch):
    monkeypatch.setattr(AssistLoop, "_in_lang", lambda self, text, lang, *a, **k: "patched")
    assert AssistLoop._in_lang(None, "x", "ko") == "patched"


def test_the_moved_module_is_small():
    assert len(Path(answer.__file__).read_text().splitlines()) <= 300


# -- (2) differential test ----------------------------------------------------------------------------------------------------
class Mem:
    def __init__(self):
        self.log = []

    def remember_answer(self, task, q, a):
        self.log.append((task, str(q), a))


class Sr:
    """The skill runner: scripted answers for the Ask skill; every call is recorded."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def execute(self, name, params, node, log_fn=None):
        self.calls.append((name, dict(params), node))
        if not self.script:
            return {"isdone": False}
        a = self.script.pop(0)
        if a is Exception:
            raise RuntimeError("io")
        return {"isdone": True, "text": a} if a != "<answer>" else {"isdone": True, "answer": " an answer "}


class Lang:
    def speak(self, text, lang):
        return f"[{lang}]{text}"


def _make(cls, script, cfg, ask_spec=True, original="bring the apple to the dining table please"):
    lp = cls.__new__(cls)
    spec = types.SimpleNamespace(name="Ask", required_params=["inputs"], param_names=["inputs", "retries", "lang"])
    lp.robot = types.SimpleNamespace(ask_spec=lambda: spec if ask_spec else None, text=lambda k, lg: f"<{k}:{lg}>")
    lp.sr, lp.cfg, lp.original = Sr(script), dict(cfg), original
    lp._humanize = lambda q: str(q).replace("@", " at ")
    lp._log_fn = lambda i: None
    lp._finish = lambda r, status, msg="", **kw: {"finish": status, "msg": msg, "say": str(kw.get("say"))}
    lp._manifest_planner = lambda key, default=None: {"frac": 0.5, "word_len": 3, "min_words": 3} if cfg.get("tuned") else default
    lp.__dict__["_language"] = Lang()
    return lp


def _run_obj(lang="en", dialogue=(), asked=0, converge=False, places=None, **kw):
    r = types.SimpleNamespace(node="n", lang=lang, heard=0, asked=asked, task="bring the apple", dialogue=list(dialogue),
                              memory=Mem(), places=places, converge=converge, replaced=set(),
                              robot=types.SimpleNamespace(text=lambda k, lg: f"<{k}:{lg}>"),
                              ev=lambda event, **f: {"event": event, **f})
    r.__dict__.update(kw)
    return r


def _state(r):
    skip = {"ev", "memory", "places", "robot"}
    d = {k: (sorted(v) if isinstance(v, set) else v) for k, v in vars(r).items() if k not in skip}
    return d, r.memory.log


def _drive(gen):
    """Run a generator method to the end: (events, return value)."""
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as e:
        return evs, e.value


QUESTIONS = ["Which table do you mean?", "Where are you?", speech.LocalText("q_where_are_you"), speech.LocalText("q_where_bring"),
             speech.LocalText("q_unclear_task"), speech.LocalText.join(speech.LocalText("q_where_bring"), "ok?"),
             "Do you want it at the desk@study room?", "which table do you want?"]
SCRIPTS = [["yes"], ["no"], [""], ["", "yes"], ["", ""], ["yet", "yes"], ["yet", "banana", "no"], ["no, the other table"],
           [Exception, "yes"], ["<answer>"], ["bring the apple to the dining table please"], ["the dining table"]]
CFGS = [dict(silence_retries=0, yes_no_reasks=1, clarify_rounds=2),
        dict(silence_retries=1, yes_no_reasks=2, clarify_rounds=1, dialog_unstick=True, dialog_agree=True, tuned=True),
        dict(silence_retries=2, yes_no_reasks=0, clarify_rounds=0, dialog_agree=True)]


def test_in_lang_and_the_pure_helpers_equal_the_reference():
    for cls_a, cls_b in ((OldLoop, AssistLoop),):
        a, b = _make(cls_a, [], CFGS[0]), _make(cls_b, [], CFGS[0])
        for t, lg in itertools.product(("hello", "bring the cup", ""), ("en", "ko", "ja")):
            assert a._in_lang(t, lg) == b._in_lang(t, lg) == f"[{lg}]{t}"
        for q in QUESTIONS + [None, 3]:
            assert OldLoop._asks_where(q) == AssistLoop._asks_where(q)
            assert OldLoop._essential(q) == AssistLoop._essential(q)
        for t in ("", "yes", "no", "no, the other table", "yet", "the restroom one", "yes please", "No."):
            assert OldLoop.yes_no(t) == AssistLoop.yes_no(t)


def test_fresh_question_equals_the_reference():
    asked_before = [[], [("robot", "Which table do you mean?")], [("user", "Which table do you mean?")],
                    [("robot", "which  TABLE do you mean?")], [("robot", "Which table do you mean")]]
    seen = set()
    for dlg, conv, reworded, q in itertools.product(asked_before, (False, True), (False, True), QUESTIONS):
        outs = []
        for cls in (OldLoop, AssistLoop):
            r = _run_obj(dialogue=dlg, converge=conv, reworded=reworded)
            out = cls._fresh_question(r, q)
            outs.append((str(out), getattr(out, "key", ""), _state(r)))
        assert outs[0] == outs[1], (dlg, conv, reworded, q)
        seen.add(outs[0][1])
    assert "q_unclear_task" in seen and "" in seen          # both the reworded and the plain branch are reached


def test_restates_equals_the_reference():
    answers = ["", "yes", "bring the apple to the dining table please", "please bring apple dining table", "the dining table",
               "bring it", "table"]
    hit = set()
    for cfg, orig, ans in itertools.product(CFGS[:2], ("bring the apple to the dining table please", "", "go"), answers):
        a = _make(OldLoop, [], cfg, original=orig)._restates(_run_obj(), ans)
        b = _make(AssistLoop, [], cfg, original=orig)._restates(_run_obj(), ans)
        assert a == b, (cfg, orig, ans)
        hit.add(a)
    assert hit == {True, False}


def test_a_loop_without_a_manifest_uses_the_defaults_like_the_reference():
    for cls in (OldLoop, AssistLoop):
        lp = _make(cls, [], CFGS[0])
        lp._manifest_planner = lambda *a, **k: (_ for _ in ()).throw(AttributeError("bare"))
        assert lp._restates(_run_obj(), "bring the apple to the dining table please") is True


def _ask_calls(cls, script, cfg, q, lang, spec=True):
    lp = _make(cls, script, cfg, ask_spec=spec)
    out = lp._ask_io(q, "n", None, lang)
    return out, lp.sr.calls, lp.__dict__.get("_said_as")


def test_ask_io_equals_the_reference():
    got = set()
    for script, q, lang, spec in itertools.product(SCRIPTS, QUESTIONS[:4], ("en", "ko"), (True, False)):
        a, b = (_ask_calls(c, list(script), CFGS[0], q, lang, spec) for c in (OldLoop, AssistLoop))
        assert a == b, (script, q, lang, spec)
        got.add(a[0])
    assert "" in got and "yes" in got and "an answer" in got


def test_ask_and_ask_heard_equal_the_reference():
    heard = set()
    for script, cfg, q in itertools.product(SCRIPTS, CFGS, QUESTIONS[:5]):
        outs = []
        for cls in (OldLoop, AssistLoop):
            lp = _make(cls, list(script), cfg)
            res = (lp._ask(q, "n", None, "ko"), lp.__dict__.get("_answer_source"), lp.sr.calls)
            lp2 = _make(cls, list(script), cfg)
            res2 = (lp2._ask_heard(q, "n", None, "en"), lp2.__dict__.get("_answer_source"), lp2.sr.calls)
            outs.append((res, res2))
        assert outs[0] == outs[1], (script, cfg, q)
        heard.add(outs[0][1][0][1])                     # how many times the question was asked
    assert len(heard) >= 2                              # a silence retry is reached


def test_ask_yes_no_equals_the_reference():
    seen = set()
    for script, cfg, q in itertools.product(SCRIPTS, CFGS, QUESTIONS[:3] + QUESTIONS[6:7]):
        outs = []
        for cls in (OldLoop, AssistLoop):
            lp, r = _make(cls, list(script), cfg), _run_obj()
            outs.append((lp._ask_yes_no(r, q), _state(r), lp.sr.calls))
        assert outs[0] == outs[1], (script, cfg, q)
        seen.add(outs[0][0][1:])                        # (True | False | None, correction)
    assert {(True, ""), (False, "")} <= seen and any(s[0] is False and s[1] for s in seen) and any(s[0] is None for s in seen)


def test_ask_clarify_equals_the_reference(env):
    kinds = set()
    rng = random.Random(5)
    for script, cfg, q in itertools.product(SCRIPTS, CFGS, QUESTIONS):
        for asked, essential_used, free_used, person_at, lang in rng.sample(
                list(itertools.product((0, 1, 2, 3), (False, True), (False, True), (False, True), ("en", "ko"))), 4):
            outs = []
            for cls in (OldLoop, AssistLoop):
                lp = _make(cls, list(script), cfg)
                lp._where_asked = "pending"
                lp._cancelled = lambda r: None
                if person_at:
                    lp.__dict__["_person_at"] = "grandpa"
                r = _run_obj(lang=lang, asked=asked, essential_used=essential_used, free_round_used=free_used,
                             places=Places(env), dialogue=[("robot", "earlier question")])
                evs, ret = _drive(lp._ask_clarify(r, q))
                outs.append((evs, ret is answer._NEXT, ret is answer._STOP, _state(r), lp.sr.calls, lp._where_asked,
                             lp.__dict__.get("_answer_source")))
            assert outs[0] == outs[1], (script, cfg, q, asked, essential_used, free_used, person_at, lang)
            ev0 = outs[0][0]
            kinds.add("next" if outs[0][1] else "capped" if ev0 and "finish" in ev0[-1] and "after" in str(ev0[-1]) else
                      "stop" if outs[0][2] else "other")
    assert {"next", "capped", "stop"} <= kinds, kinds


def test_ask_clarify_when_the_run_is_cancelled_equals_the_reference(env):
    for cls in (OldLoop, AssistLoop):
        lp = _make(cls, ["yes"], CFGS[0])
        lp._where_asked = None
        lp._cancelled = lambda r: {"cancelled": True}
        r = _run_obj(places=Places(env))
        evs, ret = _drive(lp._ask_clarify(r, "Which table?"))
        assert evs[-1] == {"cancelled": True} and ret is answer._STOP
        assert [e["event"] for e in evs[:-1]] == ["clarify"] and r.heard == 1
