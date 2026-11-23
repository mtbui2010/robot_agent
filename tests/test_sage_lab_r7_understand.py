"""Lab round 7 (all flags default OFF, off = the earlier behaviour): ``reask_anchor`` (the abstention re-reading is taken only
when every place in it is in the person's words), ``no_shared_listener``, ``understand_views`` (the original and the
translator's English are read separately; agree = act, differ = one "which one?" question) and ``understand_goals_first``
(schema order). Through the real AssistLoop.run_blocking with the symbolic FakeRobot and a scripted LLM. Languages are DATA:
the places' aliases and the language pack; a made-up language "zz" has aliases and no pack. Synthetic ENV and requests written
for these tests (no benchmark text)."""
import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import goal_fix
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from test_sage_lab_r2_grounding import ENV as ENV2, HOUSE, G, U, first_understand, shape

# the people are not tracked (no position): a delivery to one is a hand-over to them
ENV = {k: ({kk: vv for kk, vv in v.items() if kk != "loc"} if v.get("person") else v) for k, v in ENV2.items()}

FLAGS = ("reask_anchor", "no_shared_listener", "understand_views", "understand_goals_first")
ZZ_ENV = {**ENV, "shelf@hall": {"aliases": ["shelf", "estante"], "loc": {"x": 6.0, "y": 1.0}}}
ABSTAIN = U(status="ambiguous", question="Where is the cup?")
TO_SHELF = U(G("on", "cup", "", "shelf@hall"))
NOTE = r"finds objects itself"
IN = "Translate what this person said"


@pytest.fixture
def env():
    return ENV


def go(make_assist, tmp_path, task, llm, lang="en", answers=(), **opts):
    robot = FakeRobot(ENV, dict(HOUSE), answers=list(answers))
    loop = make_assist(robot, llm, ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup")), confirm_task=False, **opts)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return evs, robot, loop


# ── registration ──
def test_flags_are_registered_and_off_by_default(make_assist, monkeypatch):
    for f in FLAGS:
        monkeypatch.delenv("ROBOT_AGENT_" + f.upper(), raising=False)
        assert f in ASSIST_OPTIONS and f in PLANNER_KEYS
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]))
    assert not any(loop.cfg[f] for f in FLAGS)
    assert list(loop._understand_schema()["properties"])[0] == "status"


# ── reask_anchor ──
def test_anchored_helper_reads_aliases_of_any_language():
    places = Places(ZZ_ENV)
    goals = [G("on", "cup", "", "shelf@hall")]
    assert goal_fix.anchored(goals, "Pon la taza en el estante.", places)          # a made-up request, alias from the ENV
    assert goal_fix.anchored(goals, "컵을 선반에 놔 줘.", Places(ENV))              # Korean alias, particle
    assert not goal_fix.anchored(goals, "Pon la taza en su sitio.", places)        # the place is not in the words
    assert not goal_fix.anchored([G("on", "cup", "", "nowhere")], "Pon la taza en el estante.", places)
    assert goal_fix.anchored([G("on", "cup", "", "anna@living room")], "Bring me the cup.", places)    # people exempt
    assert goal_fix.anchored([G("open", "fridge")], "Open it.", places)                                # nothing to anchor
    assert not goal_fix.anchored(goals, "Pon la taza.", Places({"shelf@hall": {}}) if False else Places({}))   # no data


def test_the_anchored_reading_is_taken(make_assist, tmp_path):
    llm = FakeLLM((NOTE, TO_SHELF), ABSTAIN)
    evs, _r, loop = go(make_assist, tmp_path, "Put the cup on the shelf.", llm, reask_anchor="1")
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")] and len(llm.calls) == 2
    assert loop.lab_log[0] == {"flag": "reask_anchor", "taken": True}


def test_a_place_the_person_did_not_name_is_not_taken(make_assist, tmp_path):
    task = "Put the cup away."
    old = go(make_assist, tmp_path, task, FakeLLM((NOTE, TO_SHELF), ABSTAIN), reask_abstain="1", answers=["on the table"])[0]
    assert shape(first_understand(old)["goals"]) == [("on", "cup", "shelf@hall")]      # the earlier option takes it
    evs, _r, loop = go(make_assist, tmp_path, task, FakeLLM((NOTE, TO_SHELF), ABSTAIN), reask_anchor="1",
                       answers=["on the table"])
    u = first_understand(evs)
    assert not u["goals"] and u["question"] == "Where is the cup?"
    assert loop.lab_log[0] == {"flag": "reask_anchor", "taken": False}
    assert any(e.get("reask_taken") is False for e in evs if e["event"] == "understand_check")


def test_reask_anchor_in_korean_through_the_aliases(make_assist, tmp_path):
    llm = FakeLLM((NOTE, TO_SHELF), ABSTAIN, default=TO_SHELF)
    evs, *_ = go(make_assist, tmp_path, "컵을 선반에 놔 줘.", llm, lang="ko", reask_anchor="1")
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")]


def test_without_a_find_skill_or_hazard_gate_nothing_changes(make_assist, tmp_path):
    infeasible = U(status="infeasible", question="Where is the cup?")
    llm = FakeLLM(infeasible)
    evs, *_ = go(make_assist, tmp_path, "Put the cup on the shelf.", llm, reask_anchor="1", hazard_rules="off",
                 answers=["on the table"])
    assert all("finds objects itself" not in c[0] for c in llm.calls)                  # no gate after it: the refusal stands
    assert not first_understand(evs)["goals"]


# ── no_shared_listener ──
TELL = U(G("say", "come here", "", "ben@kitchen"), G("on", "cup", "", "ben@kitchen", hand=True))


def test_shared_listener_moves_the_recipient_and_the_flag_stops_it(make_assist, tmp_path):
    task = "Tell Ben to come here and bring me the cup."
    on, *_ = go(make_assist, tmp_path, task, FakeLLM(default=TELL))
    off, *_ = go(make_assist, tmp_path, task, FakeLLM(default=TELL), no_shared_listener="1")
    to = lambda evs: [g["to"] for g in first_understand(evs)["goals"] if g["type"] == "on"]      # noqa: E731
    assert to(on) == ["anna@living room"] and to(off) == ["ben@kitchen"]


# ── understand_goals_first ──
def test_goals_first_orders_properties_and_required(make_assist):
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_goals_first="1")
    sc = loop._understand_schema()
    assert list(sc["properties"])[0] == "goals" and sc["required"][0] == "goals"
    assert set(sc["properties"]) == {"status", "reason", "reason_type", "question", "goals"}
    assert set(sc["required"]) == {"status", "reason", "question", "goals"}


# ── understand_views ──
KO_TASK = "컵을 선반에 놔 줘."
CUP_SHELF, MUG_SHELF = U(G("on", "cup", "", "shelf@hall")), U(G("on", "mug", "", "shelf@hall"))
OUT = r"Translate into natural"


def views(make_assist, tmp_path, own, pivot, answers=(), flag="1", **opts):
    llm = FakeLLM((IN, "Put the cup on the shelf."), (r"Task: 컵", own), (r"Task: Put the cup", pivot),
                  (OUT, "어느 쪽이에요?"), default=own)
    evs, robot, loop = go(make_assist, tmp_path, KO_TASK, llm, lang="ko", answers=answers, understand_views=flag,
                          object_check="0", **opts)
    return evs, robot, llm, loop


def n_understand(llm):
    return len([1 for p, _f in llm.calls if "You turn a household robot task into goals" in p])


def test_two_views_that_agree_act_with_no_question(make_assist, tmp_path):
    evs, robot, llm, loop = views(make_assist, tmp_path, CUP_SHELF, CUP_SHELF)
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")] and not robot.asked
    assert n_understand(llm) == 2 and loop.lab_log[0] == {"flag": "understand_views", "agreed": True}


def test_two_views_that_differ_ask_which_one(make_assist, tmp_path):
    evs, robot, llm, loop = views(make_assist, tmp_path, CUP_SHELF, MUG_SHELF, answers=["첫 번째요"])
    q = next(e for e in evs if e["event"] == "clarify")["question"]
    assert "put the cup on the shelf" in q and "put the mug on the shelf" in q and "Which one" in q
    assert robot.asked and any(e.get("views") for e in evs if e["event"] == "understand_check")
    assert loop.lab_log[0] == {"flag": "understand_views", "agreed": False, "asked": True}
    assert n_understand(llm) >= 3                                      # the answer is read once more, the views are not repeated


def test_off_it_is_the_earlier_run_with_one_reading(make_assist, tmp_path):
    evs, robot, llm, _ = views(make_assist, tmp_path, CUP_SHELF, MUG_SHELF, flag="0")
    assert not robot.asked and n_understand(llm) == 1 and not [p for p, _f in llm.calls if IN in p]
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")]


def test_an_english_request_or_a_reading_without_goals_costs_nothing(make_assist, tmp_path):
    llm = FakeLLM(default=CUP_SHELF)
    go(make_assist, tmp_path, "Put the cup on the shelf.", llm, understand_views="1")
    assert n_understand(llm) == 1
    evs, robot, llm2, _ = views(make_assist, tmp_path, U(status="clear"), MUG_SHELF)
    assert not [e for e in evs if e.get("views")] and not any("Which one" in q for q in robot.asked)


def test_a_refusing_second_view_goes_the_usual_way_not_to_a_choice(make_assist, tmp_path):
    unsafe = {**MUG_SHELF, "status": "unsafe", "reason": "no"}
    evs, robot, llm, loop = views(make_assist, tmp_path, CUP_SHELF, unsafe)
    assert not any(e.get("views") for e in evs if e["event"] == "understand_check") and n_understand(llm) == 2


def test_an_untrusted_translation_leaves_the_run_alone(make_assist, tmp_path):
    llm = FakeLLM((IN, "UNCLEAR"), default=CUP_SHELF)
    evs, robot, loop = go(make_assist, tmp_path, KO_TASK, llm, lang="ko", understand_views="1", object_check="0",
                          answers=[KO_TASK])
    assert not any(e.get("views") for e in evs if e["event"] == "understand_check")
