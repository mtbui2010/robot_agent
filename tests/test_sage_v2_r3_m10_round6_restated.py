"""A request the person said again (the first hearing garbled) is checked against the restatement, not the garble.

Synthetic words written for this test."""
import types

from robot_agent.core.planning.assist_loop import AssistLoop, Places

ENV = {"table@kitchen": {"aliases": ["table"]}, "shelf@hall": {"aliases": ["shelf"]}}
GARBLED = "please bring the chicken bring and put it on the table in the hall"
CLEAN = "please bring the blue pitcher and put it on the table in the hall"
GOALS = [{"type": "on", "object": "blue pitcher", "from": "", "to": "table@kitchen"}]


def loop():
    lp = AssistLoop.__new__(AssistLoop)
    lp.original = None
    lp.cfg = {"goal_readback": True}
    return lp


def run(dialogue):
    r = types.SimpleNamespace(task=GARBLED, dialogue=dialogue, places=Places(ENV), replaced=(), refused_parts=[])
    return loop()._issues(r, GOALS)


def test_garbled_first_hearing_is_an_issue_before_any_answer():
    assert any(i["kind"] == "object" for i in run([]))


def test_restating_answer_replaces_the_garble():
    d = [("robot", "Sorry, could you say that again?"), ("user", CLEAN)]
    assert not [i for i in run(d) if i["kind"] == "object"]


def test_short_answer_does_not_replace_it():
    d = [("robot", "Which one?"), ("user", "the pitcher")]
    assert any(i["kind"] == "object" for i in run(d))
