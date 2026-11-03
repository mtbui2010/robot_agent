"""SAGE-v2 round 3, step 6 (language): F5.1 no raw ENV key in anything said, F5.2 fixed questions and
read-backs from the robot's own phrases (no LLM call when the robot has the phrase), F5.3 ``translate_model``
(its own client, guard and budget), F5.4 Say texts in the user's language.

Classes (gate30 round 1): C7 Korean question / read-back / raw ids (KO 006 007 012 018 + 15 contributing; the KO
structured_mismatch 28 / 66 questions), "Where are you?" -> "여기 있어요?" by the 8B planner model. Synthetic ENV
keys / phrases written for the test.
"""
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places

ENV = {
    "bath_cabinet@bathroom": {"aliases": ["bath cabinet", "욕실 수납장"], "openable": True},
    "hall_shelf@hall": {"aliases": ["hall shelf", "현관 선반"]},
    "person_b": {"aliases": ["Minho", "민호"], "person": "b"},
    "spot by you": {"aliases": ["me", "나한테"], "person": "u", "speaker": True, "spot": "hall_shelf@hall"},
}
KO = {"q_where_are_you": "지금 어디 계세요?", "rb_will": "{what} 할게요.", "sep_then": ", 그다음 ",
      "rb_in": "{object}을 {place}에 넣기", "rb_to_you": "{object} 가져다드리기", "q_confirm": "{summary} 맞을까요?",
      "q_where_put": "{object}은 어디에 둘까요?"}


def G(t, o, f, to, **kw):
    return {"type": t, "object": o, "from": f, "to": to, **kw}


class R:
    """A robot view with Korean phrases (its i18n) and no LLM."""
    i18n = types.SimpleNamespace(phrases={k: {"ko": v} for k, v in KO.items()})

    def text(self, key, lang=None, **kw):
        return key

    def has_text(self, key):
        return False


def loop_with(llm=None, **cfg):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = R()
    lp._env_places = Places(ENV)
    lp._llm_cfg = {"model": "planner-8b", "url": "http://127.0.0.1:1"}
    lp.cfg = {"translate_model": "", "translate_url": None, **cfg}
    lp._llm_client = llm
    lp._llm_unreachable = False
    from robot_agent.core.planning.understand import LLMBudget
    lp._tr_budget = LLMBudget(cfg.get("translate_budget", 10))
    return lp


def test_no_raw_ids_in_said_text():
    ps = Places(ENV)
    goals = [G("in", "towel", "hall_shelf@hall", "bath_cabinet@bathroom"), G("on", "book", "", "person_b"),
             G("on", "cup", "", "spot by you"), G("on", "key", "", "person_b", hand=True)]
    texts = [str(AssistLoop.summary(goals, ps)),
             str(speech.LocalText("q_where_is_person", {"person": "person_b"},
                                  text=f"Where is {speech.say_name(ps, 'person_b')}?"))]
    for t in texts:
        assert not any(k in t for k in ENV), t
    assert "bring you the cup" in texts[0] and "hand the key to Minho" in texts[0]
    assert speech.say_name(ps, "bath_cabinet@bathroom", "ko") == "욕실 수납장"
    assert speech.say_name(ps, "person_b", "ko") == "민호"
    lp = loop_with()
    assert lp._humanize("could not get to bath_cabinet@bathroom") == "could not get to bath cabinet in the bathroom"


def test_localtext_keeps_english():
    q = speech.LocalText("q_where_put", {"object": "towel"})
    assert q == "Where should I put the towel?" and q.key == "q_where_put" and isinstance(q, str)
    j = speech.LocalText.join(q, speech.LocalText("q_where_are_you"))
    assert str(j) == "Where should I put the towel? Where are you?"


def test_question_from_phrase_no_llm():
    llm = FakeLLM()                                                 # any call would raise (no script)
    lp = loop_with(llm)
    assert lp._in_lang(speech.LocalText("q_where_are_you"), "ko") == "지금 어디 계세요?"
    rb = AssistLoop.summary([G("on", "커피잔", "", "spot by you")], Places(ENV))
    assert lp._in_lang(speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?"), "ko") \
        == "커피잔 가져다드리기 할게요. 맞을까요?"
    assert llm.calls == []


def test_english_slot_goes_to_the_translator():
    llm = FakeLLM(("Translate", "수건은 어디에 둘까요?"))
    lp = loop_with(llm)
    out = lp._in_lang(speech.LocalText("q_where_put", {"object": "towel"}), "ko")
    assert out == "수건은 어디에 둘까요?" and len(llm.calls) == 1


def test_translation_guard_rejects_cjk():
    assert speech.guard("这是 테스트예요", "ko") is not None
    assert speech.guard("Where are you?", "ko") is not None
    assert speech.guard("지금 어디 계세요?", "ko") is None
    assert speech.guard("민호한테 전해 줄게요", "ko", keep=["민호"]) is None
    assert speech.guard("그 사람한테 전해 줄게요", "ko", keep=["민호"]) is not None
    llm = FakeLLM(("Translate", "你在哪里?"), ("Translate", "어디에 계세요?"))
    lp = loop_with(llm)
    assert lp._in_lang("Where are you hiding?", "ko") == "어디에 계세요?" and len(llm.calls) == 2
    llm2 = FakeLLM(("Translate", "Where?"), ("Translate", "Where?"))
    lp2 = loop_with(llm2)
    assert lp2._in_lang("Where are you hiding?", "ko") == "Where are you hiding?" and lp2._say_not_in_lang == 1


def test_translate_budget_separate():
    llm = FakeLLM(default="번역이에요")
    lp = loop_with(llm, translate_budget=1)
    assert lp._in_lang("one", "ko") == "번역이에요"
    assert lp._in_lang("two", "ko") == "two" and lp._say_not_in_lang == 1   # its own cap, not the planner's
    assert len(llm.calls) == 1


def test_translator_client_separate(monkeypatch):
    import robot_agent.connect.llm as ra_llm
    made = []
    tr = FakeLLM(default="번역이에요")
    monkeypatch.setattr(ra_llm, "init_llm_client", lambda cfg=None, **k: made.append(dict(cfg or {})) or tr)
    planner_llm = FakeLLM()
    lp = loop_with(planner_llm, translate_model="translator-30b")
    assert lp._in_lang("Where is it?", "ko") == "번역이에요"
    assert made and made[0]["model"] == "translator-30b" and planner_llm.calls == []
    # the planner's own model, or none: the planner's client
    lp2 = loop_with(FakeLLM(default="번역이에요"), translate_model="planner-8b")
    assert lp2._translator() is lp2._llm_client


def test_translate_model_option(make_assist, env, monkeypatch):
    from robot_agent.contract import manifest as M
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]))
    assert loop.cfg["translate_model"] == ""
    orig = M.Manifest.from_dict.__func__

    def with_tm(cls, d, source=None):
        return orig(cls, {**d, "planner": {**(d.get("planner") or {}), "translate_model": "tm-from-manifest",
                                           "translate_budget": 4}}, source)
    monkeypatch.setattr(M.Manifest, "from_dict", classmethod(with_tm))
    lp = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]))
    assert lp.cfg["translate_model"] == "tm-from-manifest" and lp.cfg["translate_budget"] == 4
    monkeypatch.setenv("ROBOT_AGENT_TRANSLATE_MODEL", "tm-from-env")
    assert make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([])).cfg["translate_model"] == "tm-from-env"
    assert make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]),
                       translate_model="tm-option").cfg["translate_model"] == "tm-option"


def test_say_text_translated():
    llm = FakeLLM(("Translate", "저녁 준비됐어요"))
    lp = loop_with(llm)
    lp._run_lang = "ko"
    lp.robot = types.SimpleNamespace(bind=lambda st, w: ("say", {"inputs": st["object"]}), i18n=R.i18n)
    lp._find_params = set()
    lp.cfg.update(find_min_conf=0, find_verify=False)
    skill, p = lp._bind({"action": "Say", "object": "Dinner is ready"}, None)
    assert p["inputs"] == "저녁 준비됐어요"
    skill, p = lp._bind({"action": "Say", "object": "밥 먹어요"}, None)          # already Korean: as is
    assert p["inputs"] == "밥 먹어요" and len(llm.calls) == 1


def test_giveup_in_lang_after_budget(make_assist, env):
    """The run's planning budget is used up: the give-up is the robot's phrase (budget_end), not a silent English
    text and not a call past the cap."""
    robot = FakeRobot(env, {"apple": "nowhere"})
    llm = FakeLLM(default={"status": "clear", "reason": "", "question": "",
                           "goals": [{"type": "on", "object": "apple", "from": "", "to": "dining table"}]})
    loop = make_assist(robot, llm, ScriptedPlanner([]), llm_budget=1)
    d = done(run_task(loop, "Put the apple on the dining table.", lang="ko"))
    assert d["status"] == "failed" and d.get("code") == "budget_exceeded"
    # one planning call (the cap); the give-up's translation is the translator's own budget, separately
    assert len([p for p, _f in llm.calls if not p.startswith("Translate")]) == 1
