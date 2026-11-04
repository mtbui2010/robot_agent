"""SAGE-v2 round 3, the language layer (core/planning/language.py): Korean (any non-English language) is only an
I/O layer around an English core.

* IN: ``input_pivot`` none | translate | both; one translation call per utterance, cached; the original is kept
  (the prompt, ``said``); a translation that cannot be trusted makes the robot ask to repeat.
* OUT: ONE ``speak()``; a spoken text is never English for a non-English user (the pack's plain phrase when the
  translator cannot help); an English slot in the robot's own phrase is said alone (cached).
* Failure classes: a half-English read-back when the translate budget was used up (the second read-back of a run),
  a clarify cap that ended a delivery before "Where are you?", a repeated question, a refused put whose
  open-the-container goal was still read back.

Synthetic ENV, sentences and phrases written for these tests.
"""
import types

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import language, speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.understand import LLMBudget

DR, DT, DS = "drawer@kitchen", "dining table@kitchen", "desk@study room"
ENV = {
    "bath_cabinet@bathroom": {"aliases": ["bath cabinet", "욕실 수납장"], "openable": True},
    "hall_shelf@hall": {"aliases": ["hall shelf", "현관 선반"]},
    "person_b": {"aliases": ["Minho", "민호"], "person": "b"},
}
KO = {"q_unclear_task": "죄송해요, 다른 말로 말씀해 주시겠어요?", "say_untranslated": "죄송해요, 지금은 말씀드리기 어려워요.",
      "q_where_put": "{object}은 어디에 둘까요?", "q_repeat": "다시 말씀해 주시겠어요?"}


class R:
    i18n = types.SimpleNamespace(phrases={k: {"ko": v} for k, v in KO.items()})

    def text(self, key, lang=None, **kw):
        return key

    def has_text(self, key):
        return False


def loop_with(llm=None, pack_ko=True, **cfg):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = R() if pack_ko else types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={}))
    lp._env_places = Places(ENV)
    lp._llm_cfg = {"model": "planner-8b", "url": "http://127.0.0.1:1"}
    lp.cfg = {"translate_model": "", "translate_url": None, "input_pivot": "both", **cfg}
    lp._llm_client = llm
    lp._llm_unreachable = False
    lp._tr_budget = LLMBudget(cfg.get("translate_budget", 10))
    lp._run_lang = "ko"
    return lp


def understood(*goals, status="clear"):
    return {"status": status, "reason": "", "reason_type": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


IN = r"Translate what this person said"
OUT = r"Translate into natural"


# ── IN ──
def test_both_gives_the_original_and_its_english(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    llm = FakeLLM((IN, "Put the apple on the dining table."), understood(("on", "apple", "", "dining table")),
                  default=understood(("on", "apple", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, input_pivot="both", object_check="0")
    run_task(loop, "사과를 식탁에 놔 주세요.", lang="ko")
    p = next(p for p, _f in llm.calls if "Task:" in p)
    assert "Task: 사과를 식탁에 놔 주세요." in p and "(English: Put the apple on the dining table.)" in p
    assert len([1 for p, _f in llm.calls if IN in p]) == 1       # one call per utterance, however often it is read


def test_translate_gives_english_and_keeps_the_original_for_said(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    llm = FakeLLM((IN, "Put the apple on the dining table."), default=understood(("on", "apple", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, input_pivot="translate", object_check="0")
    run_task(loop, "사과를 식탁에 놔 주세요.", lang="ko")
    p = next(p for p, _f in llm.calls if "Task:" in p)
    assert "Task: Put the apple on the dining table." in p
    assert "사과를 식탁에 놔 주세요." in p.split("Task:", 1)[1] and "for \"said\" only" in p


def test_none_is_todays_prompt_and_english_costs_nothing(make_assist, env):
    for lang, task, pivot in (("ko", "사과를 식탁에 놔 주세요.", "none"), ("en", "Put the apple on the dining table.", "both")):
        robot = FakeRobot(env, {"apple": DS})
        llm = FakeLLM(default=understood(("on", "apple", "", "dining table")))
        planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
        loop = make_assist(robot, llm, planner, input_pivot=pivot, object_check="0")
        run_task(loop, task, lang=lang)
        assert not [p for p, _f in llm.calls if IN in p or "(English:" in p]


def test_untrusted_translation_asks_to_repeat_once(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["사과를 식탁에 놔 주세요."])
    llm = FakeLLM((IN, "UNCLEAR"), (IN, "Put the apple on the dining table."), (OUT, "다시 말씀해 주시겠어요?"),
                  default=understood(("on", "apple", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, input_pivot="both", object_check="0")
    evs = run_task(loop, "으아 사과 어쩌 식탁", lang="ko")
    assert robot.asked[0] == "다시 말씀해 주시겠어요?" and sum("Sorry" in q or "다시" in q for q in robot.asked) == 1
    firsts = [i for i, (p, _f) in enumerate(llm.calls) if "Task:" in p]
    assert all("Task: 으아" in llm.calls[i][0] for i in firsts)           # the original stays the task
    assert done(evs)["status"] == "success"


def test_hear_checks_the_translation():
    lp = loop_with(FakeLLM((IN, "UNCLEAR")))
    assert lp.language.untrusted("으아 어쩌") == "the text is not an understandable sentence"
    lp = loop_with(FakeLLM((IN, "물건을 옮겨 주세요")))                  # not English
    assert lp.language.untrusted("테스트 문장") == "the translation is not English"
    lp = loop_with(FakeLLM((IN, "Put it in the cabinet.")))
    assert "욕실 수납장" in lp.language.untrusted("그걸 욕실 수납장에 넣어 주세요.")      # the named place was lost
    lp = loop_with(FakeLLM((IN, "Put it in the bath cabinet.")))
    assert lp.language.untrusted("그걸 욕실 수납장에 넣어 주세요.") is None
    prompt = lp._llm_client.calls[0][0]
    assert "욕실 수납장 = bath cabinet in the bathroom" in prompt         # the ENV alias reaches the translator


def test_hear_leaves_calls_for_what_the_robot_says():
    llm = FakeLLM(default="Put it away.")
    lp = loop_with(llm, translate_budget=2)                              # `reserve` calls are kept for speech
    h = lp.language.hear("그걸 치워 주세요.")
    assert h.english == "" and h.trusted and llm.calls == []
    lp2 = loop_with(FakeLLM(default="Put it away."), translate_budget=3)
    assert lp2.language.hear("그걸 치워 주세요.").english == "Put it away."


def test_english_user_and_none_make_no_call():
    llm = FakeLLM()
    lp = loop_with(llm)
    lp._run_lang = "en"
    assert lp.language.hear("Put it away.").english == "" and lp._in_lang("Hello", "en") == "Hello"
    lp._run_lang = "ko"
    lp.cfg["input_pivot"] = "none"
    assert lp.language.hear("그걸 치워 주세요.").english == "" and llm.calls == []


# ── OUT ──
def test_no_english_reaches_a_korean_user():
    lp = loop_with(FakeLLM(), translate_budget=0)                        # the translate budget is used up
    q = speech.LocalText("q_which_x", {"what": "kimchi fridge", "options": ["a", "b"]},
                         text="Which kimchi fridge do you mean: a or b?")
    assert lp._in_lang(q, "ko") == KO["q_unclear_task"]                  # a question: ask for the task in other words
    assert lp._in_lang("Dinner is ready.", "ko") == KO["say_untranslated"]
    assert lp.language.fallbacks == 2 and not getattr(lp, "_say_not_in_lang", 0)
    lp2 = loop_with(FakeLLM(), pack_ko=False, translate_budget=0)        # a pack without the phrase: counted, as before
    assert lp2._in_lang("Dinner is ready.", "ko") == "Dinner is ready." and lp2._say_not_in_lang == 1


def test_english_slot_is_said_alone_and_cached():
    llm = FakeLLM((OUT, "붉은 통"), default="붉은 통")
    lp = loop_with(llm)
    lp.robot.i18n.phrases["q_where_put"] = {"ko": "{object}은 어디에 둘까요?"}
    for _ in range(2):
        assert lp._in_lang(speech.LocalText("q_where_put", {"object": "red bin"}), "ko") == "붉은 통은 어디에 둘까요?"
    assert len(llm.calls) == 1                                           # the pack's phrase; the slot translated once
    assert "red bin" in llm.calls[0][0] and "Where should" not in llm.calls[0][0]


def test_speak_is_idempotent_and_logs_what_was_said():
    lp = loop_with(FakeLLM((OUT, "안녕하세요")))
    once = lp.language.speak("Hello", "ko")
    assert lp.language.speak(once, "ko") == once == "안녕하세요" and len(lp._llm_client.calls) == 1
    assert lp.language.spoken == [once, once]
    assert not language.localized("I am not sure I understood (you did not say the kimchi fridge). 우유 안에 넣을게요.", "ko")


def test_everything_the_user_hears_passes_speak(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["식탁"])
    llm = FakeLLM((OUT, "어디에 놓을까요?"), (OUT, "작업을 시작합니다"), (OUT, "끝났어요"),
                  understood(("on", "apple", "desk", "")), understood(("on", "apple", "desk", "dining table")),
                  default="번역이에요")
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, input_pivot="none", object_check="0")
    evs = run_task(loop, "책상에 있는 사과를 옮겨 주세요.", lang="ko")
    spoken = set(loop.language.spoken)
    said = [e["say"] for e in evs if isinstance(e.get("say"), str)] + list(robot.asked)
    assert robot.asked and said
    assert all(s in spoken for s in said), [s for s in said if s not in spoken]
    assert not robot.asked[0].isascii()                                  # the question reached the user in Korean


# ── clarification hygiene (m10 round 3) ──
class _Run:
    def __init__(self, asked=0, dialogue=()):
        self.asked, self.dialogue, self.heard = asked, list(dialogue), 0
        self.node, self.lang, self.task = None, "en", "t"
        self.memory = types.SimpleNamespace(remember_answer=lambda *a: None)
        self.events = []

    def ev(self, name, **kw):
        self.events.append((name, kw))
        return {"event": name, **kw}


def _ask(r, q, answers):
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg = {"clarify_rounds": 3}
    lp._answer_source = "robot"
    it = iter(answers)
    lp._ask_heard = lambda *a: (next(it), 1)
    lp._cancelled = lambda r: None
    lp._log_fn = lambda k: None
    lp._restates = lambda r, a: False
    lp._finish = lambda r, status, msg="", **kw: {"event": "done", "status": status, "msg": msg, **kw}
    out = []
    g = lp._ask_clarify(r, q)
    try:
        while True:
            out.append(next(g))
    except StopIteration as e:
        return e.value, out


def test_where_are_you_may_take_one_round_past_the_cap():
    from robot_agent.core.planning.assist_loop import _NEXT, _STOP
    q = speech.LocalText("q_where_are_you")
    r = _Run(asked=3)
    res, evs = _ask(r, q, ["on the sofa"])
    assert res == _NEXT and r.asked == 4 and r.dialogue[-1] == ("user", "on the sofa")
    res2, evs2 = _ask(r, q, ["on the sofa"])                             # only one such round
    assert res2 == _STOP and evs2[-1]["event"] == "done"
    r3 = _Run(asked=3)
    res3, _ = _ask(r3, speech.LocalText("q_what_do"), ["x"])             # an ordinary question: the cap stands
    assert res3 == _STOP


def test_a_question_is_not_asked_twice_word_for_word():
    r = _Run(dialogue=[("robot", "Who is he and what do you mean?"), ("user", "the back")])
    q = AssistLoop._fresh_question(r, "Who is he and what do you mean?")
    assert isinstance(q, speech.LocalText) and q.key == "q_unclear_task"
    assert AssistLoop._fresh_question(r, "Who is he and what do you mean?") == "Who is he and what do you mean?"   # once
    assert AssistLoop._fresh_question(_Run(), "Where?") == "Where?"


def test_open_goal_that_only_served_a_refused_put_is_refused_with_it():
    put = {"type": "in", "object": "x", "from": "", "to": "box"}
    open_ = {"type": "open", "object": "box", "from": "", "to": "box"}
    other = {"type": "on", "object": "y", "from": "", "to": "box"}
    assert AssistLoop._support_goals([open_, put], [put]) == [open_]
    assert AssistLoop._support_goals([open_, put, other], [put]) == []   # a kept goal still uses the box
    assert AssistLoop._support_goals([put, {"type": "open", "object": "lid", "from": "", "to": ""}], [put]) == []


def test_pack_line_with_raw_keys_and_an_english_reason():
    lp = loop_with(FakeLLM())                                            # no translator call may be needed
    out = lp._in_lang("bath_cabinet@bathroom 열기에 실패했습니다. cannot reach the handle of bath_cabinet@bathroom", "ko")
    assert out == "욕실 수납장 열기에 실패했습니다." and lp._llm_client.calls == []
    assert lp._in_lang("hall_shelf@hall 이동을 완료했습니다", "ko") == "현관 선반 이동을 완료했습니다"
