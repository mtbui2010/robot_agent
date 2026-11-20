"""Option ``translate_retry`` (default OFF): when the input translation loses a place name the person said, it is made again once with the
name already in English; the retry counts only when it keeps the name. Off: the sentence is untrusted (the robot asks to repeat)."""
from fakes import FakeLLM
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.option_services import option_dispatcher
from test_sage_v2_r3_language_layer import loop_with

SAID = "그걸 욕실 수납장에 넣어 주세요."
LOSES = (r"그걸 욕실", "Put it in the cabinet.")                  # the first translation loses "bath cabinet"
KEEPS = (r"그걸 bath cabinet", "Put it in the bath cabinet.")      # the retry has the name


def with_option(llm, on):
    lp = loop_with(llm)
    lp._lab = lambda *a, **k: None
    lp.options = option_dispatcher(lp, {"translate_retry": "1"} if on else {}, {})
    return lp


def test_off_by_default_the_lost_name_makes_the_sentence_untrusted():
    from robot_agent.core.options import REGISTRY  # noqa: F401  (registration)
    lp = with_option(FakeLLM(LOSES, KEEPS), on=False)
    assert "욕실 수납장" in lp.language.untrusted(SAID)
    assert len(lp._llm_client.calls) == 1


def test_on_the_retry_with_the_english_name_is_trusted():
    lp = with_option(FakeLLM(LOSES, KEEPS), on=True)
    assert lp.language.untrusted(SAID) is None
    assert lp.language.hear(SAID).english == "Put it in the bath cabinet."
    assert len(lp._llm_client.calls) == 2


def test_on_a_retry_that_still_loses_the_name_changes_nothing():
    lp = with_option(FakeLLM(LOSES, (r"그걸 bath cabinet", "Put it in the cabinet.")), on=True)
    assert "욕실 수납장" in lp.language.untrusted(SAID)


# ── through the real AssistLoop ──
import pytest  # noqa: E402

from fakes import FakeRobot, ScriptedPlanner, done, run_task, steps  # noqa: E402

DS, DT = "desk@study room", "dining table@kitchen"
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
UNDERSTOOD = {"status": "clear", "reason": "", "reason_type": "", "question": "",
              "goals": [{"type": "on", "object": "apple", "from": "", "to": "dining table"}]}
TASK = "사과를 식탁에 놔 주세요."


def go(make_assist, env, **opts):
    robot = FakeRobot(env, {"apple": DS}, answers=[TASK])
    llm = FakeLLM((r"사과를 식탁", "Put the apple on the table."), (r"사과를 dining table", "Put the apple on the dining table."),
                  (r"Translate into natural", "다시 말씀해 주시겠어요?"), default=UNDERSTOOD)
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN), input_pivot="both", object_check="0", **opts)
    return robot, run_task(loop, TASK, lang="ko")


def test_through_the_loop_off_asks_to_repeat_and_on_does_not(make_assist, env):
    robot, _evs = go(make_assist, env)
    assert any("다시" in q or "Sorry" in q for q in robot.asked)                       # the lost name made the robot ask again
    robot, evs = go(make_assist, env, translate_retry="1")
    assert not any("다시" in q or "Sorry" in q for q in robot.asked) and done(evs)["status"] == "success"


def test_the_loops_own_language_layer_replaces_the_plain_translation_only_for_sage_with_a_pivot(monkeypatch):
    from robot_agent.core.unified_agent import _loop_translates
    monkeypatch.delenv("ROBOT_AGENT_INPUT_PIVOT", raising=False)
    monkeypatch.delenv("ROBOT_AGENT_PLANNER", raising=False)
    assert _loop_translates("sage", {}) is False                       # no pivot: the plain translation, as before
    assert _loop_translates("sage", {"input_pivot": "both"}) is True
    assert _loop_translates("grace", {"input_pivot": "both"}) is False  # only SAGE's loop has the language layer
    monkeypatch.setenv("ROBOT_AGENT_INPUT_PIVOT", "translate")
    assert _loop_translates("sage", {}) is True
