"""Option ``hazard`` key ``ask`` with the language-pack phrase ``q_closed_can_microwave`` (docs/metal_microwave.md "Ask: a bare can"): data only,
no engine change. A made-up ``ask`` entry (a bare "zorb can" x a microwave place, made-up open words) asks "is it still sealed?" once; the
engine's fail-safe answer rule: a clear no or an open word goes on, a yes, a negated open word, "never mind" refuse, silence stops."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, scrub, steps
from robot_agent.core.planning import langdata, speech
from test_safety_metal_ask import ENV, OVEN, SHELF, goal, read

W0, W1 = r"(?<![a-z0-9])", r"(?![a-z0-9])"
SAY = "a closed zorb can can burst in a microwave oven"
CAN = {"ask": "q_closed_can_microwave", "objects": [W0 + r"cans?" + W1], "places": [r"^microwave(?:@|$)"], "say": SAY,
       "unless": [r"^(?!.*(?:\bnot\b|n't\b)).*" + W0 + r"(?:open|empty)" + W1], "assumed": True}
Q = r"still sealed\?"


@pytest.fixture
def env():
    return dict(ENV)


def _run(make_assist, legacy, answer, obj="zorb can", **opts):
    robot = FakeRobot(ENV, {obj: SHELF}, start=SHELF, reply_rules=[{"if": Q, "then": answer}])
    planner = ScriptedPlanner(steps(f"MoveTo {SHELF}", f"Find {obj}", f"Pick {obj}", f"MoveTo {OVEN}", f"Place {OVEN}"))
    loop = make_assist(robot, FakeLLM(read(goal(obj)), default="{}"), planner, search_legs=1, ask_help=False, detector_miss_rate=0,
                       **opts)
    if legacy is not None:
        loop.robot.manifest.safety = {**(loop.robot.manifest.safety or {}), "hazards": legacy}
    return loop, robot, run_task(loop, f"Put the {obj} on the microwave.", max_events=3000)


@pytest.mark.parametrize("lang, word", [("en", "sealed"), ("ko", "밀봉")])
def test_closed_can_ask_phrase_is_pack_data_in_en_and_ko_with_the_object_slot(lang, word):
    q = speech.phrase(None, "q_closed_can_microwave", lang).format(object="zorb")
    assert "zorb" in q and word in q and "{" not in q
    assert langdata.block(lang, "speech").get("assumed") is True


def test_closed_can_ask_loop_final_bare_can_yes_is_refused_with_the_can_reason(make_assist):
    """The failure class: in(<bare can> -> microwave) was neither refused (no closed word) nor asked; now one question, a yes refuses."""
    loop, robot, evs = _run(make_assist, [CAN], "yes", hazard_ask=True)
    d = done(evs)
    assert sum("still sealed?" in q for q in robot.asked) == 1 and "Is the zorb can still sealed?" in robot.asked[0]
    assert d["status"] == "refused" and SAY in d["say"] and robot.obj["zorb can"] == SHELF


@pytest.mark.parametrize("answer, status", [("no", "success"), ("No, it's open.", "success"), ("it is empty", "success"),
                                            ("it's not open", "refused"), ("never mind", "refused"), ("I don't know", "refused")])
def test_closed_can_ask_loop_final_answers_follow_the_fail_safe_rule(make_assist, answer, status):
    loop, robot, evs = _run(make_assist, [CAN], answer, hazard_ask=True)
    assert done(evs)["status"] == status and (robot.obj["zorb can"] == OVEN) is (status == "success")


def test_closed_can_ask_loop_final_an_open_word_decides_without_a_question(make_assist):
    loop, robot, evs = _run(make_assist, [CAN], "yes", obj="open zorb can", hazard_ask=True)
    assert not any("still sealed?" in q for q in robot.asked) and done(evs)["status"] == "success"


def _bytes(make_assist, legacy, **opts):
    loop, robot, evs = _run(make_assist, legacy, "yes", **opts)
    return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log


def test_closed_can_ask_loop_final_key_off_is_byte_identical_off(make_assist):
    assert _bytes(make_assist, [CAN]) == _bytes(make_assist, None)


def test_closed_can_ask_loop_final_no_ask_entry_is_byte_identical_empty(make_assist):
    assert _bytes(make_assist, [], hazard_ask=True) == _bytes(make_assist, None)
