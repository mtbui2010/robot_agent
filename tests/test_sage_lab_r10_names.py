"""Round 10: a bare object name is never sent ALONE to the translator as free text (014 KO: "mug" came back as an apology).
Order: a declared name (robot pack i18n ``names``, an ENV entry) -> one constrained translation that a plausibility guard
(script, length, not a stock phrase of the pack) must accept -> the English name inside the Korean sentence. Per-run cache.
A language with no data does nothing. Through the real loop / LanguageAdapter with a scripted translator. Synthetic data."""
import types

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.understand import LLMBudget
from test_sage_lab_r2_grounding import ENV
from test_sage_v2_r3_language_layer import DS, DT, loop_with, understood

APOLOGY = "미안해요"


class Mock(FakeLLM):
    """A translator that answers a bare word with an apology (the 014 KO bug) and a sentence with its own translation."""

    def __init__(self, bare=APOLOGY, **kw):
        super().__init__(default="번역이에요", **kw)
        self.bare = bare

    def chat(self, prompt="", format=None, **kw):
        tail = prompt.rsplit("\n", 1)[-1]
        if prompt.startswith("Give the usual"):
            n = len(prompt.strip().splitlines()) - 1
            self.calls.append((prompt, format))
            return "\n".join([self.bare] * n)
        if len(tail.split()) <= 2 and "\n" in prompt and "household object" in prompt:
            self.calls.append((prompt, format))
            return self.bare
        return super().chat(prompt=prompt, format=format, **kw)


def pack(lp, **phrases):
    lp.robot.i18n.phrases.update({k: {"ko": v} for k, v in phrases.items()})
    return lp


Q = speech.LocalText("q_where_put", {"object": "mug"})


def test_bare_name_with_an_unrelated_answer_stays_english_in_the_sentence():
    llm = Mock()
    lp = pack(loop_with(llm), q_silence="미안해요, 못 들었어요.")
    out = lp._in_lang(Q, "ko")
    assert APOLOGY not in out and "mug" in out and out.endswith("은 어디에 둘까요?")
    assert len(llm.calls) == 1 and "household object" in llm.calls[0][0]
    assert lp._in_lang(Q, "ko") == out and len(llm.calls) == 1               # cached (also the refusal)


def test_a_plausible_name_is_used_and_cached():
    llm = Mock(bare="머그컵")
    lp = pack(loop_with(llm), q_silence="미안해요, 못 들었어요.")
    assert lp._in_lang(Q, "ko") == "머그컵은 어디에 둘까요?"
    assert lp._in_lang(speech.LocalText("q_where_put", {"object": "Mug"}), "ko") == "머그컵은 어디에 둘까요?"
    assert len(llm.calls) == 1


def test_declared_names_need_no_model():
    llm = Mock()
    lp = pack(loop_with(llm))
    lp.robot.i18n.phrases["names"] = {"ko": {"mug": "머그컵"}}
    assert lp._in_lang(Q, "ko") == "머그컵은 어디에 둘까요?" and llm.calls == []
    assert lp.language.local_name("hall shelf", "ko") == "현관 선반" and llm.calls == []      # an ENV entry


def test_guard_rejects_by_pack_data_not_by_a_list_in_code():
    lp = pack(loop_with(Mock()), q_silence="죄송해요, 못 들었어요.", hello="안녕하세요 반가워요")
    ad = lp.language
    assert ad.plausible_name("죄송합니다", "mug", "ko")          # shares its first syllables with the pack's apology
    assert ad.plausible_name("안녕하세요", "mug", "ko")
    assert ad.plausible_name("컵이 어디 있어요?", "mug", "ko") and ad.plausible_name("mug", "mug", "ko")
    assert ad.plausible_name("머그컵", "mug", "ko") is None and ad.plausible_name("우유 팩", "milk carton", "ko") is None
    assert ad.plausible_name("아주아주긴이름이에요", "mug", "ko")                # too long
    bare = loop_with(Mock(), pack_ko=False)                    # no stock phrase declared: script and length only
    assert bare.language.plausible_name("머그컵", "mug", "ko") is None


def test_object_check_names_are_guarded_and_declared_ones_skip_the_call():
    llm = Mock()
    lp = pack(loop_with(llm), q_silence="미안해요, 못 들었어요.")
    lp.robot.i18n.phrases["names"] = {"ko": {"scissors": "가위"}}
    lp.__dict__.pop("_tr_cache", None)
    assert lp._local_names(["scissors"]) == {"scissors": "가위"} and llm.calls == []
    assert lp._local_names(["mug", "scissors"]) == {"scissors": "가위"}     # the apology is no name: left out (fail open)
    assert len(llm.calls) == 1


def test_made_up_language_does_nothing_and_fails_open():
    llm = Mock()
    lp = loop_with(llm)
    lp._run_lang = "zz"
    ad = lp.language
    assert ad.local_name("mug", "zz") is None and ad.declared_name("mug", "zz") is None and llm.calls == []
    assert ad.local_slot("mug", "object") == "mug"
    lp.robot.i18n.phrases["q_where_put"] = {"zz": "{object}?"}
    assert lp._in_lang(Q, "zz") is not None                    # no pack for zz: the old path, no name call
    assert not any("household object" in p for p, _f in llm.calls)


def test_english_is_untouched():
    llm = Mock()
    lp = loop_with(llm)
    lp._run_lang = "en"
    assert lp.language.local_slot("mug", "object") == "mug" and lp.language.local_name("mug", "en") is None
    assert llm.calls == []


def test_through_the_real_loop_the_question_never_says_the_apology(make_assist, env):
    robot = FakeRobot(env, {"mug": DS}, answers=["식탁"])
    llm = Mock()
    llm.default = understood(("on", "mug", "", ""))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, input_pivot="none", object_check="0")
    loop.robot.i18n.phrases.update({"q_where_put": {"ko": "{object}은 어디에 둘까요?"},
                                      "q_silence": {"ko": "미안해요, 못 들었어요."}})
    run_task(loop, "머그를 옮겨 주세요.", lang="ko")
    spoken = " ".join(loop.language.spoken)
    assert APOLOGY not in spoken.replace("미안해요, 못 들었어요.", "")
    assert "mug은 어디에 둘까요?" in spoken or "mug" in spoken      # the English name inside the Korean sentence
