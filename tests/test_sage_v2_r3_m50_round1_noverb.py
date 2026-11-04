"""A request whose verb was lost to the microphone is asked about, once (synthetic sentences and places)."""
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.understand import describe, issue_question, verb_lost
import types

ENV = {"cot@attic": {"aliases": ["cot"]}}


def test_verb_lost_pattern():
    assert verb_lost("Could you the lamp on the cot for me?")
    assert verb_lost("can you please the lamp on the cot")
    assert not verb_lost("Could you put the lamp on the cot?")
    assert not verb_lost("The lamp is on the cot, thanks.")


def r(task, dialogue=()):
    return types.SimpleNamespace(task=task, dialogue=list(dialogue), places=Places(ENV), replaced=())


def test_issue_is_raised_for_the_first_hearing_only():
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg = {"goal_readback": True}
    lp.original = None
    lp._object_source = lambda rr, answers: rr.task
    goals = [{"type": "on", "object": "lamp", "from": "cot@attic", "to": "person_x"}]
    iss = lp._issues(r("Could you the lamp on the cot for me?"), goals)
    assert iss and iss[0]["kind"] == "noverb"
    assert "What should I do" in str(issue_question(iss, Places(ENV)))
    assert describe(iss[0])
    again = lp._issues(r("Could you the lamp on the cot for me?", [("robot", "?"), ("user", "put it on the cot")]), goals)
    assert not any(i["kind"] == "noverb" for i in again)
