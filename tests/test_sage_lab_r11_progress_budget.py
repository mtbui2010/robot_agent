"""Round 11: progress announcements use the robot's declared phrase and spend no translator call. In a long Korean run the
translate budget (a declared manifest number) was spent on "<name> 찾기를 진행합니다" lines (a declared pack phrase with an English
name in it), so the questions and the final message that came later had the English name inside them or the stock sentence.
A step milestone is now said as the robot's own phrase with the name as a slot (declared name, else the run's cached name,
else the English name in the sentence). Through the real loop with a scripted translator. Synthetic data."""
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.planning import speech
from test_sage_lab_r2_grounding import ENV  # noqa: F401  (the fixture env of the sibling tests)
from test_sage_v2_r3_language_layer import DS, DT, loop_with, understood

PACK = {"step_start": "{object} {verb}{eul} 진행합니다", "step_success": "{object} {verb}{eul} 완료했습니다",
        "step_fail": "{object} {verb}에 실패했습니다. {reason}", "verb.Find": "찾기", "verb.Pick": "집기",
        "verb.MoveTo": "이동", "verb.Place": "놓기", "q_where_put": "{object}은 어디에 둘까요?"}


class Mock(FakeLLM):
    """A translator: a name call gets a Korean noun per line, a sentence its 'translation'; every call is kept."""

    def __init__(self, **kw):
        super().__init__(default="번역이에요", **kw)

    def chat(self, prompt="", format=None, **kw):
        if "household object" in prompt and not prompt.startswith("Give"):          # one name, as a noun
            self.calls.append((prompt, format))
            return {"mug": "머그컵", "coffee mug": "머그컵", "dinner plate": "접시", "orange pill bottle": "약병"}.get(prompt.rsplit("\n", 1)[-1].strip(), "그릇")
        if prompt.startswith("Give the usual"):
            self.calls.append((prompt, format))
            return "\n".join("머그컵" for _ in prompt.strip().splitlines()[1:])
        return super().chat(prompt=prompt, format=format, **kw)


def translations(llm):
    return [p for p, _f in llm.calls if p.startswith("Translate into natural") and "household object" not in p]


def test_progress_lines_spend_no_translator_call_through_the_real_loop(make_assist, env):
    robot = FakeRobot(env, {"coffee mug": DS, "dinner plate": DS}, answers=["식탁"])
    llm = Mock()
    llm.default = understood(("on", "coffee mug", "desk", "dining table"), ("on", "dinner plate", "desk", "dining table"))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find coffee mug", "Pick coffee mug", f"MoveTo {DT}", f"Place {DT}",
                                    f"MoveTo {DS}", "Find dinner plate", "Pick dinner plate", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, input_pivot="none", object_check="0")
    loop.robot.i18n.phrases.update({k: {"ko": v} for k, v in PACK.items()})
    evs = run_task(loop, "머그와 접시를 옮겨 주세요.", lang="ko")
    said = [e["say"] for e in evs if e["event"] in ("step_start", "step_done") and e.get("say")]
    assert any("진행합니다" in s for s in said) and any("완료했습니다" in s for s in said), said
    assert not any("I will" in s or "Done:" in s for s in said)                       # never English
    objs = [p for p in translations(llm) if ("찾기를" in p or "집기를" in p or "놓기를" in p)]
    assert not objs, objs                                                             # the phrase is declared: no call


def test_a_long_english_name_stays_in_the_declared_sentence_when_no_call_is_left():
    llm = Mock()
    lp = loop_with(llm, translate_budget=1)
    assert lp.language._take() and not lp.language._take()                           # the budget is used up
    lp.robot.i18n.phrases.update({k: {"ko": v} for k, v in PACK.items()})
    t = speech.LocalText("step_start", {"object": "orange pill bottle", "verb": "찾기", "eul": "를"},
                         text="I will find orange pill bottle")
    assert lp._in_lang(t, "ko") == "orange pill bottle 찾기를 진행합니다"             # not the stock "say it in other words"
    assert llm.calls == []


def test_distinct_calls_only_a_repeated_sentence_and_name_cost_once():
    llm = Mock()
    lp = loop_with(llm, translate_budget=8)
    lp.robot.i18n.phrases.update({k: {"ko": v} for k, v in PACK.items()})
    left = lp._tr_budget.left()
    for _ in range(5):
        lp._in_lang(speech.LocalText("q_where_put", {"object": "coffee mug"}), "ko")
        lp._in_lang(speech.LocalText("step_start", {"object": "coffee mug", "verb": "찾기", "eul": "를"}, text="x"), "ko")
    assert left - lp._tr_budget.left() == 1                                          # the name, once; no sentence call


def test_the_checked_name_is_the_name_the_robot_says():
    llm = Mock()
    lp = loop_with(llm, translate_budget=8)
    assert lp._local_names(["mug"]) == {"mug": "머그컵"}
    n = len(llm.calls)
    assert lp.language.local_name("mug", "ko") == "머그컵" and len(llm.calls) == n    # no second call for the same name


def test_a_step_with_no_declared_verb_is_translated_as_before(make_assist, env):
    robot = FakeRobot(env, {"mug": DS}, answers=["식탁"])
    llm = Mock()
    llm.default = understood(("on", "mug", "desk", "dining table"))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, input_pivot="none", object_check="0")
    ph = {k: v for k, v in PACK.items() if not k.startswith("verb.")}
    loop.robot.i18n.phrases.update({k: {"ko": v} for k, v in ph.items()})
    run_task(loop, "머그를 옮겨 주세요.", lang="ko")
    assert any("진행합니다" in p for p in translations(llm))          # the English verb is not declared data: the old path
