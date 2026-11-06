"""Lab round 3, V1 (language layer + dialog_unstick), through the real AssistLoop.run_blocking against the symbolic
FakeRobot: the read-back of a restated reading is built from the ordinary read-back phrase (``q_confirm``), so a pack
that has ``q_confirm`` says the summary without a translator call; and the language layer's last resort keeps a
question's kind (a read-back never becomes "say the task in other words").

Synthetic ENV / phrases / requests written for these tests (Korean, and Vietnamese as a third language)."""
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places

ENV = {
    "table@kitchen": {"aliases": ["bàn", "table", "식탁"], "loc": {"x": 3.0, "y": 1.0}},
    "sofa@living room": {"aliases": ["ghế", "sofa", "소파"], "loc": {"x": 1.0, "y": 1.0}},
}
PLAN = steps("MoveTo table@kitchen")
READ = {"status": "clear", "reason": "", "question": "",
        "goals": [{"type": "go", "object": "", "from": "", "to": "table@kitchen"}]}
TASKS = {"ko": "그쪽으로 바로 이동해 주세요", "vi": "Hãy đi tới đó ngay bây giờ nhé"}
RESTATE = {"ko": "제가 말했잖아요 그쪽으로 바로 이동해 주세요 식탁으로", "vi": "Tôi đã nói Hãy đi tới đó ngay bây giờ nhé ở cái bàn"}
PACK = {
    "ko": {"q_confirm": "{summary} 맞나요?", "rb_will": "{what}.", "sep_then": ", 그다음 ", "rb_go": "{place} 쪽으로 갈게요",
           "q_unclear_task": "죄송해요, 잘 이해하지 못했어요. 다른 말로 말씀해 주시겠어요?", "q_which_part": "어느 부분이 맞지 않나요?", "q_what_do": "죄송해요. 그럼 뭘 할까요?",
           "say_untranslated": "죄송해요, 지금은 그 말씀을 드리기 어려워요."},
    "vi": {"q_confirm": "{summary} Đúng không?", "rb_will": "{what}.", "sep_then": ", rồi ", "rb_go": "đi tới {place}",
           "q_unclear_task": "Xin lỗi, tôi chưa hiểu. Bạn nói lại bằng cách khác được không?", "q_which_part": "Phần nào chưa đúng?", "q_what_do": "Xin lỗi. Vậy tôi nên làm gì?",
           "say_untranslated": "Xin lỗi, bây giờ tôi chưa nói được điều đó."},
}
EXPECT = {"ko": "식탁 쪽으로 갈게요. 맞나요?", "vi": "đi tới bàn. Đúng không?"}


class EnglishTranslator(FakeLLM):
    """Understands the request (default answer) and 'translates' every robot line badly: Korean gets English words back
    (the output guard rejects a non-Hangul line); Vietnamese (a Latin script, the guard cannot tell) gets a translator
    that is down. Either way no line of the robot can be said by the model."""

    down = False

    def chat(self, prompt="", format=None, **kw):
        if "Translate into natural" in prompt:
            self.calls.append((prompt, format))
            if self.down:
                raise ConnectionError("translator down")
            return "I understood it the same way again. I will go to the table. Is that right?"
        return super().chat(prompt=prompt, format=format, **kw)


@pytest.fixture
def env():
    return ENV


def run(make_assist, lang, extra=None, **opts):
    phrases = {k: {lang: v} for k, v in PACK[lang].items()}
    phrases.update({k: {lang: v} for k, v in (extra or {}).items()})
    llm = EnglishTranslator(default=READ)
    llm.down = lang == "vi"
    robot = FakeRobot(ENV, {}, answers=["no", RESTATE[lang], "yes"])
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN), health_wait_s=0, confirm_task=True, dialog_unstick="1", **opts)
    loop.robot.i18n.phrases.update(phrases)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(TASKS[lang], lang=lang)]
    return loop, robot, llm, evs


@pytest.mark.parametrize("lang", ["ko", "vi"])
def test_restated_readback_without_a_lead_phrase_is_the_plain_readback(make_assist, lang):
    loop, robot, llm, evs = run(make_assist, lang)
    readbacks = [q for q in robot.asked if EXPECT[lang].split(".")[0] in q]
    assert len(readbacks) >= 2, robot.asked
    first, second = readbacks[:2]
    assert first == second == EXPECT[lang]                          # the q_confirm read-back, with its declared meaning
    assert robot.asked[1] == PACK[lang]["q_what_do"] and robot.asked[2] == second   # no, what then, the restated read-back
    spoken = " ".join(robot.asked)
    assert PACK[lang]["q_unclear_task"] not in spoken               # never the question for something else
    assert "same way again" not in spoken and "Is that right" not in spoken      # no English words reach the user
    assert loop.lab_log and loop.lab_log[0]["part"] == "restated_reading"
    # the summary needed no translator call: the only translated line is the lead sentence
    assert not any("I will go" in p for p, _f in llm.calls if "Translate into natural" in p)
    assert done(evs)["status"] == "success"


@pytest.mark.parametrize("lang", ["ko", "vi"])
def test_restated_readback_with_a_lead_phrase_says_the_lead_then_the_readback(make_assist, lang):
    lead = {"ko": "같은 뜻으로 다시 이해했어요.", "vi": "Tôi hiểu lại giống như vậy."}[lang]
    loop, robot, llm, evs = run(make_assist, lang, extra={"rb_restated": lead})
    assert f"{lead} {EXPECT[lang]}" in robot.asked
    said = [p for p, _f in llm.calls if "Translate into natural" in p and ("go to the table" in p or "same way" in p)]
    assert not said                                                 # neither the lead nor the summary went to the translator


def test_readback_fallback_keeps_its_kind_in_the_language_layer():
    """The last resort for a read-back is never q_unclear_task, even when its summary cannot be said."""
    from robot_agent.core.planning.language import LanguageAdapter
    robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={k: {"ko": v} for k, v in PACK["ko"].items()}))
    host = types.SimpleNamespace(robot=robot, cfg={"dialog_unstick": True}, _env_places=Places(ENV), _llm_unreachable=True,
                                 _run_lang="ko", _llm_cfg={})
    lang = LanguageAdapter(host)
    rb = speech.LocalText("rb_will", {"what": "x"}, text="I will fetch the unknown thing.")
    cq = speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?")      # summary slot has no phrase
    out = lang.speak(cq, "ko", "question")
    assert out != PACK["ko"]["q_unclear_task"] and out == PACK["ko"]["say_untranslated"]
    # with every lab option off, the earlier last resort stands (the read-back becomes "say it in other words")
    host.cfg = {}
    assert LanguageAdapter(host).speak(cq, "ko", "question") == PACK["ko"]["q_unclear_task"]
    host.cfg = {"dialog_unstick": True}
    # a question of another kind still falls back to "say it in other words"
    other = lang.speak(speech.LocalText("q_where_put", {"object": "x"}), "ko", "question")
    assert other == PACK["ko"]["q_unclear_task"]
    # a whole-sentence restated phrase falls back to the plain q_confirm with the same summary
    s = speech.LocalText("rb_will", {"what": speech.LocalText.seq([speech.LocalText("rb_go", {"place": "table@kitchen"})])},
                         text="I will go to the table.")
    restated = speech.LocalText("q_confirm_restated", {"summary": s}, text=f"I understood it again. {s} Is that right?")
    assert lang.speak(restated, "ko", "question") == EXPECT["ko"]


def test_english_run_is_unchanged(make_assist):
    robot = FakeRobot(ENV, {}, answers=["no", "I said: please go there right now, to the table"])
    loop = make_assist(robot, FakeLLM(default=READ), ScriptedPlanner(PLAN), health_wait_s=0, confirm_task=True, dialog_unstick="1")
    evs = list(loop.run_blocking("Please go there right now"))
    again = [q for q in robot.asked if "same way again" in q]
    assert len(again) == 1 and again[0].startswith("I understood it the same way again. I will go to") \
        and again[0].endswith("Is that right?")
