"""Option ``recipient_keep`` (default OFF): a delivery's recipient moves to the speaker only on evidence (a first-person recipient word of the
language pack), never because the person is named once. Made-up people and objects, a made-up language ``zq`` (``komi`` = me, ``poto`` = bring)
and the shipped English pack; no benchmark text."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import goal_fix, langdata
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.recipient_keep import SPEC, RecipientKeep, speaker_evidence
from test_sage_lab_r2_grounding import G, U, first_understand

ENV = {"desk@den": {"aliases": ["desk"], "loc": {"x": 1.0, "y": 1.0}},
       "vexa@den": {"person": True, "speaker": True, "aliases": ["me", "vexa"]},
       "tolo@den": {"person": True, "aliases": ["tolo"]}}
HOUSE = {"urn": "desk@den"}
SAY, GIVE = G("say", "come over", "", "tolo@den"), G("on", "urn", "", "tolo@den", hand=True)
ZQ = {"words": {"recipient": [r"\bkomi\b", r"\bpoto\b"], "first_person": [r"\bkomi\b"]}}


@pytest.fixture
def env():
    return ENV


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)


def make(ask=True, phrase=True):
    labs = []
    svc = NS(robot=None, lab=lambda *a, **k: labs.append((a, k)), labs=labs)
    if phrase:
        svc.goal_phrase = lambda g, places: f"give {g['object']} to {g['to']}"
    cfg = resolve(SPEC, {"recipient_keep": "1", "recipient_keep.ask_when_unsure": ask}, {}, None)
    opt, run = RecipientKeep(cfg, svc), NS(places=Places(ENV), dialogue=[], goals=[], asked=0)
    opt.on_start(run)
    return opt, run, svc


def run_it(text, goals=None, **kw):
    """(goals, notes, conflicts) of the engine's rule with the option's evidence rule installed."""
    opt, run, svc = make(**kw)
    got, notes = goal_fix.shared_listener([dict(g) for g in (goals or [SAY, GIVE])], text, run.places)
    return got, notes, run.recipient_conflicts, svc


def recipient(goals):
    return [g["to"] for g in goals if g["type"] == "on"]


def test_registered_and_off_by_default_the_old_rule_stands_off(make_assist, env):
    assert "recipient_keep" in ASSIST_OPTIONS and REGISTRY.get("recipient_keep").key("enabled").default is False
    loop = make_assist(FakeRobot(env, {}), FakeLLM(default={}), ScriptedPlanner([]))
    assert "recipient_keep" not in loop.cfg or not loop.cfg["recipient_keep"]
    places = Places(ENV)
    old, notes = goal_fix.shared_listener([dict(SAY), dict(GIVE)], "Bring the urn over, and tell tolo to come", places)
    assert recipient(old) == ["vexa@den"] and notes                                   # the base rule, unchanged: no rule on the places


def test_a_made_up_speaker_word_moves_the_recipient_once_named(zq):
    goals, notes, conflicts, svc = run_it("zq words: tell tolo come, poto the urn komi")
    assert recipient(goals) == ["vexa@den"] and notes and not conflicts
    assert [a for a, k in svc.labs if k.get("rule") == "evidence"]


def test_a_delivery_verb_or_a_pronoun_alone_is_no_evidence_the_recipient_stays(zq):
    for text in ("tell tolo to come, poto him the urn", "bring him the urn and let him know", "tell tolo to come, poto the urn tolo"):
        goals, notes, conflicts, _ = run_it(text)
        assert recipient(goals) == ["tolo@den"] and not notes and not conflicts, text
    assert not speaker_evidence("bring the urn here, give it to him") and speaker_evidence("bring me the urn") and speaker_evidence("get me the urn")


def test_two_mentions_keep_the_named_person_and_note_the_two_readings():
    goals, notes, conflicts, _ = run_it("tell tolo to come and bring tolo the urn, not me")
    assert recipient(goals) == ["tolo@den"] and not notes and conflicts == [("urn", "tolo@den")]


def test_one_mention_and_a_first_person_word_move_the_recipient_as_the_old_rule_did():
    goals, notes, conflicts, _ = run_it("tell tolo to come over, and bring me the urn")
    assert recipient(goals) == ["vexa@den"] and notes and not conflicts


def _go(make_assist, task, answers=(), **opts):
    reader = FakeLLM(default=U(SAY, GIVE))
    loop = make_assist(FakeRobot(ENV, dict(HOUSE), answers=list(answers)), reader, ScriptedPlanner(steps("MoveTo desk@den", "Find urn")), confirm_task=False, **opts)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task)]
    return evs, [g["to"] for g in first_understand(evs)["goals"] if g["type"] == "on"]


def test_give_to_a_person_named_once_keeps_the_recipient_through_the_loop(make_assist, env, tmp_path):
    task = "Give the urn to tolo and tell him to come over."
    assert _go(make_assist, task)[1] == ["vexa@den"]                                    # off: the old rule moved it to the speaker
    assert _go(make_assist, task, recipient_keep="1")[1] == ["tolo@den"]                # on: the reader's recipient stays


def _ask(answer, ask=True, dialogue=()):
    """Run the option's ``on_goals`` after the evidence rule found two readings; (events, goals, questions asked)."""
    opt, run, svc = make(ask=ask)
    run.dialogue, run.ev = list(dialogue), lambda typ, **kw: {"type": typ, **kw}
    run.goals = [dict(SAY), dict(GIVE)]
    asked = []
    svc.ask = lambda r, q: asked.append(str(q)) or answer
    svc.cancelled = lambda r: None
    goal_fix.shared_listener(run.goals, "tell tolo to come and bring tolo the urn, not me", run.places)
    evs = list(opt.on_goals(run))
    return evs, run.goals, asked


def test_the_two_readings_are_asked_once_and_the_answer_decides_by_evidence():
    evs, goals, asked = _ask("to tolo")
    assert len(asked) == 1 and "two ways" in asked[0] and recipient(goals) == ["tolo@den"] and evs[0]["kind"] == "recipient_keep"
    evs, goals, asked = _ask("bring it to me")
    assert len(asked) == 1 and recipient(goals) == ["vexa@den"]
    assert _ask("to tolo", ask=False)[2] == [] and _ask("to me", dialogue=[("user", "x")])[2] == []        # key off, or an answer already given


def test_an_empty_or_foreign_pack_keeps_the_reader_recipient_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "xx", {"words": {}})
    goals, notes, conflicts, _ = run_it("tell tolo to come and bring the urn over")     # no speaker word in the text
    assert recipient(goals) == ["tolo@den"] and not notes and not conflicts
    opt, run, _ = make()
    run.places = Places({"desk@den": {}, "tolo@den": {"person": True}})                 # no speaker in the world: nothing to move to
    opt.on_start(run)
    assert goal_fix.shared_listener([dict(SAY), dict(GIVE)], "bring me the urn", run.places) == ([SAY, GIVE], [])
    assert goal_fix.shared_listener([], "bring me", run.places) == ([], [])
    assert speaker_evidence("", None) is False and speaker_evidence("xyz", None) is False
