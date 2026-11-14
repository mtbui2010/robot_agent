"""Option ``asr_guard`` (default OFF): an empty request, or one with a hole the language pack declares (an article directly followed by a function
word), is asked again once, before the reader is called. Made-up objects ("zibble") and a made-up language (``zv``); the hook-level cases of the
shipped English pack are in ``test_option_name_recover.py`` (the layer that turns both options on). No benchmark text."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import langdata
from robot_agent.options import asr_guard
from robot_agent.options.asr_guard import SPEC, AsrGuard

GOOD = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "zibble", "from": "counter", "to": "dining table"}]}
PLAN = steps("MoveTo counter@kitchen", "Find zibble", "Pick zibble", "MoveTo dining table@kitchen", "Place dining table@kitchen")
HOLED = "Put the to the dining table."
REPEATED = "Put the zibble on the dining table."


def guard(task, lang="en", dialogue=(), repeat_asked=False):
    """(the question asked through the clarify step or None, the lab events)."""
    svc = NS(labs=[], asked=[])
    svc.lab = lambda *a, **k: svc.labs.append((a, k))

    def ask_clarify(run, q):
        svc.asked.append(q)
        return "next"
        yield                                                                     # a flow service is a generator
    svc.ask_clarify = ask_clarify
    run = NS(task=task, dialogue=list(dialogue), repeat_asked=repeat_asked, lang=lang, ev=lambda etype, **kw: (etype, kw))
    gen = AsrGuard(resolve(SPEC, {"asr_guard": "1"}, {}, None), svc).on_heard(run)
    try:
        while True:
            next(gen)
    except StopIteration:
        pass
    return (svc.asked[0] if svc.asked else None), svc.labs


def _run(make_assist, env, heard, answers, on):
    robot = FakeRobot(env, {"zibble": "counter@kitchen"}, start="counter@kitchen", answers=list(answers))
    llm = FakeLLM(("zibble", GOOD), default={"status": "clear", "reason": "", "question": "", "goals": []})
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN), detector_miss_rate=0, **({"asr_guard": "1"} if on else {}))
    return done(run_task(loop, heard, max_events=3000)), robot, llm, loop


def _repeats(robot) -> int:
    return sum("say it again" in str(q) for q in robot.asked)


def test_asr_guard_registered_default_off_and_the_loop_reads_the_holed_request_as_heard_off(make_assist, env):
    assert REGISTRY.get("asr_guard").key("enabled").default is False and not resolve(SPEC, {}, {}, None)["enabled"]
    d, robot, llm, loop = _run(make_assist, env, HOLED, [REPEATED], on=False)
    assert _repeats(robot) == 0 and HOLED in llm.calls[0][0]                          # off: the reader read the holed request
    assert not [e for e in loop.lab_log if e["flag"] == "asr_guard"]
    assert "say it again" not in robot.asked[0] and d["status"] == "success"         # the engine's own question path, as before


def test_asr_guard_empty_a_language_with_no_hole_data_gets_only_the_empty_check_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zv", {})
    monkeypatch.setitem(langdata._CACHE, "zw", {"lang": "zw"})
    monkeypatch.setattr(langdata, "_PROMPT_LANG", "zw")                             # the prompt language declares no hole either
    assert asr_guard.hole_patterns(("zv", "zw")) == []
    assert guard("bo ka to", lang="zv") == (None, [])                                # no data: nothing is asked, nothing logged
    q, labs = guard("  ", lang="zv")
    assert q is not None and labs == [(("asr_guard",), {"what": "empty"})]           # the empty check needs no data


def test_a_made_up_languages_hole_comes_from_its_own_pack(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zv", {"asr": {"hole": [r"\bka\s+(?:to|mi)\b"], "assumed": True}})
    monkeypatch.setitem(langdata._CACHE, "zw", {"lang": "zw"})
    monkeypatch.setattr(langdata, "_PROMPT_LANG", "zw")
    q, labs = guard("bo ka to", lang="zv")
    assert q is not None and labs == [(("asr_guard",), {"what": "hole"})]
    assert guard("bo ka zibble to", lang="zv")[0] is None and guard("bo ka to", lang="zv", repeat_asked=True)[0] is None


def test_loop_a_holed_request_is_asked_again_and_the_final_state_holds_the_repeated_goal(make_assist, env):
    d, robot, llm, loop = _run(make_assist, env, HOLED, [REPEATED], on=True)
    assert d["status"] == "success" and robot.obj["zibble"] == "dining table@kitchen"
    assert _repeats(robot) == 1 and REPEATED in llm.calls[0][0]                      # the first reader call already holds the repeated request
    assert [e["what"] for e in loop.lab_log if e["flag"] == "asr_guard"] == ["hole"]


@pytest.mark.parametrize("heard", ["", "   "])
def test_loop_two_silences_ask_only_once_then_the_reader_reads_final(make_assist, env, heard):
    """Regression shape (one question per lost request): the guard asks once; the second silence is the engine's own silence path (it says
    it did not hear and repeats its last question), never a second guard question; the reader is never called on nothing and nothing moves."""
    d, robot, llm, loop = _run(make_assist, env, heard, ["", ""], on=True)
    assert [e["what"] for e in loop.lab_log if e["flag"] == "asr_guard"] == ["empty"] and "say it again" in robot.asked[0]
    assert llm.calls == [] and robot.obj["zibble"] == "counter@kitchen" and d["status"] != "success"
