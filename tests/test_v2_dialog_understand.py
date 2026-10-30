"""FROM_ME (the speaker only as a destination), ASR1 (typed refusals), UND_T0 (understand sampling)."""

import pytest

from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning.understand import (CAPABILITY, SAFETY, ask_json, misheard_question, sampling,
                                                  typed_refusal)


# ── FROM_ME ──
@pytest.mark.parametrize("g, want", [
    ({"type": "on", "object": "cup", "from": "me", "to": "sofa"}, ("", "sofa")),
    ({"type": "on", "object": "cup", "from": "me", "to": ""}, ("", "me")),
    ({"type": "on", "object": "cup", "from": "Me", "to": "me"}, ("", "me")),
    ({"type": "in", "object": "cup", "from": "the user", "to": "drawer"}, ("", "drawer")),
    ({"type": "on", "object": "cup", "from": "kitchen", "to": "me"}, ("kitchen", "me")),
    ({"type": "on", "object": "cup", "from": "", "to": "me"}, ("", "me")),
])
def test_speaker_slots(g, want):
    out = goal_kinds.speaker_slots([g])[0]
    assert (out["from"], out["to"]) == want
    assert g["from"] in ("me", "Me", "the user", "kitchen", "")       # the input is not changed in place


def test_speaker_slots_leave_other_kinds():
    g = {"type": "say", "object": "hello", "from": "", "to": "me"}
    assert goal_kinds.speaker_slots([g]) == [g]
    assert goal_kinds.is_speaker(" ME ") and not goal_kinds.is_speaker("sofa")


# ── ASR1 ──
def test_typed_refusal_capability_from_manifest():
    r = typed_refusal("clear", "", "Pick", "pick the apple", answer_heard=True)
    assert r.kind == CAPABILITY and r.missing == "Pick" and not r.misheard


def test_model_refusal_on_the_command_itself_stands():
    r = typed_refusal("unsafe", "Knives near a child", None, "hand the knife to the child", answer_heard=False)
    assert r.kind == SAFETY and r.say.startswith("I can't do that") and not r.misheard
    r = typed_refusal("infeasible", "", None, "put the sofa on the table", answer_heard=False)
    assert r.kind == CAPABILITY and r.msg == "I can't do that."


@pytest.mark.parametrize("status, words", [
    ("infeasible", "put the bleach bottle on the shelf the one in the rest broom"),
    ("unsafe", "put the mug on the table the dying table"),
])
def test_refusal_after_a_misheard_answer_is_asked_again(status, words):
    r = typed_refusal(status, "the model's sentence", None, words, answer_heard=True)
    assert r is not None and r.misheard


@pytest.mark.parametrize("status, words", [
    ("unsafe", "put the mug on the table throw it out of the window"),
    ("unsafe", "컵을 창문 밖으로 던져"),
    ("infeasible", "put the towel on the shelf and turn on the washer"),
])
def test_refusal_after_an_answer_naming_a_reason_stands(status, words):
    r = typed_refusal(status, "why", None, words, answer_heard=True)
    assert r is not None and not r.misheard


def test_no_refusal_for_clear():
    assert typed_refusal("clear", "I can't, sorry", None, "anything", answer_heard=True) is None
    assert typed_refusal("ambiguous", "", None, "anything", answer_heard=False) is None


def test_misheard_question():
    d = [("robot", "Which shelf do you mean?"), ("user", "the rest broom")]
    left, q = misheard_question(d)
    assert left == [] and q.endswith("Which shelf do you mean?") and "misheard" in q
    assert misheard_question([])[1].endswith("say that again?")


# ── UND_T0 ──
def test_sampling():
    assert sampling(0, 0) == {"temperature": 0.0, "seed": 0}
    assert sampling("0.5", "3") == {"temperature": 0.5, "seed": 3}
    assert sampling(None, None) == {} and sampling("", "") == {} and sampling(0, -1) == {"temperature": 0.0}


class _LLM:
    def __init__(self, *answers):
        self.answers, self.kw = list(answers), []

    def chat(self, prompt="", format=None, **kw):
        self.kw.append(kw)
        return self.answers.pop(0)


def test_ask_json_sends_the_options_on_every_call_repairs_too():
    llm = _LLM("not json", '{"status": "clear"}')
    d = ask_json(llm, "p", {}, retries=1, options={"temperature": 0.0, "seed": 0})
    assert d == {"status": "clear"} and llm.kw == [{"temperature": 0.0, "seed": 0}] * 2
    llm = _LLM('{"a": 1}')
    ask_json(llm, "p", {})
    assert llm.kw == [{}]                      # no options: the call is as before


def test_sampling_options_for_the_clients():
    from robot_agent.connect.llm.llm_object import sampling_options
    assert sampling_options({"num_ctx": 8192}, {"temperature": 0.0, "seed": 1, "format": {}}) == \
        {"num_ctx": 8192, "temperature": 0.0, "seed": 1}
    assert sampling_options(None, {}) is None and sampling_options({"num_ctx": 1}, {}) == {"num_ctx": 1}


def test_assist_loop_sampling_config(monkeypatch):
    from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, AssistLoop
    assert {"understand_temperature", "understand_seed", "understand_sample_temperature",
            "yes_no_reasks"} <= ASSIST_OPTIONS
    loop = AssistLoop.__new__(AssistLoop)
    loop.cfg = {"understand_temperature": "0", "understand_seed": "0", "understand_sample_temperature": "0.7"}
    assert loop._sampling() == {"temperature": 0.0, "seed": 0}
    assert loop._sampling(2) == {"temperature": 0.7, "seed": 2}
    loop.cfg["understand_seed"] = ""
    assert loop._sampling() == {"temperature": 0.0}
