"""SAGE-v2 round 3, step 4 (person destinations and mentions): F2.1 "me" is the speaker's ENV entry, F2.2 a
hand-over satisfies a delivery to a person, F2.3 place or hand from the words and the ``spot`` contract, F2.4
people are not searched, F3.1 speaker / covered person mentions are not issues, F3.2 Hangul word boundaries,
and the untracked person (``people_tracking`` off: no ENV position): "Where are you?", then the map.

Classes (gate30 round 1): C2 "me" not grounded (EN 005 011 018, KO 005 010 011), C1d a hand-over / place on a
person not accepted (EN 017 021, KO 023), C3a "You mentioned the me", C6 people searched (KO 017 019, EN 027).
A synthetic flat written for the test (no benchmark house, names or text).
"""
import copy

import pytest

from fakes import FakeLLM, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import persons
from robot_agent.core.planning.assist_loop import AssistLoop, Memory, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.understand import find_issues, place_mentions

ENV = {
    "armchair@lounge": {"loc": {"x": 0.0, "y": 0.0}, "aliases": ["armchair", "안락의자"], "surface": False},
    "lamp table@lounge": {"loc": {"x": 0.8, "y": 0.2}, "aliases": ["lamp table", "램프 탁자"], "surface": True},
    "bunk@kids room": {"loc": {"x": 6.0, "y": 0.0}, "aliases": ["bunk", "이층 침대"], "surface": False},
    "worktop@galley": {"loc": {"x": 3.0, "y": 3.0}, "aliases": ["worktop", "조리대"], "surface": True},
    "larder@galley": {"loc": {"x": 3.5, "y": 3.0}, "aliases": ["larder", "식료품 저장고"], "surface": False,
                      "openable": True},
    "auntie": {"loc": {"x": 0.8, "y": 0.2}, "aliases": ["auntie", "이모", "me", "나한테", "저한테"], "person": "p1",
               "speaker": True, "seat": "armchair@lounge", "spot": "lamp table@lounge"},
    "nephew": {"loc": {"x": 6.2, "y": 0.4}, "aliases": ["nephew", "조카"], "person": "p2", "seat": "bunk@kids room",
               "spot": None},
}


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def bare_loop(robot=None):
    lp = AssistLoop.__new__(AssistLoop)
    from robot_agent.core.planning.search import SearchConfig
    lp.cfg = {"person_seat_m": 1.0, "person_surface_m": 1.2, "search": SearchConfig(enabled=False)}
    if robot is not None:
        lp.robot = robot
    lp._where_asked = None
    return lp


def ground(env, goals, task, latest="", lp=None, tmp_path=None):
    lp = lp or bare_loop()
    mem = Memory(tmp_path / "m.json") if tmp_path else Memory("/nonexistent/m.json")
    return lp._ground_goals(goals, Places(env), mem, task, latest=latest)


@pytest.fixture
def robot():
    from robot_agent.core.planning.robot import RobotView
    return RobotView.load("fakebot")


# ── F2.1 ──
@pytest.mark.parametrize("to, task", [("me", "Bring me the mug."), ("나한테", "머그잔 나한테 가져다줘요"),
                                      ("here", "Bring the mug here.")])
def test_me_grounds_to_speaker_entry(to, task):
    goals, q = ground(ENV, [G("on", "mug", "worktop@galley", to)], task)
    assert q is None and goals[0]["to"] == "auntie" and not goals[0].get("hand")


def test_speaker_by_alias_without_flag():
    env = copy.deepcopy(ENV)
    env["auntie"].pop("speaker")
    assert persons.speaker_key(Places(env)) == "auntie"
    goals, q = ground(env, [G("on", "mug", "worktop@galley", "me")], "Bring me the mug.")
    assert q is None and goals[0]["to"] == "auntie"


def test_no_speaker_entry_asks_where():
    env = {k: v for k, v in ENV.items() if not v.get("person")}
    goals, q = ground(env, [G("on", "mug", "worktop@galley", "me")], "Bring me the mug.")
    assert q == "Where are you? Which place should I bring it to?"


def test_env_without_person_fields_unchanged(env):
    """An ENV with no person / speaker / seat / spot fields: today's grounding and questions exactly."""
    goals, q = ground(env, [G("on", "apple", "desk", "me")], "Bring me the apple.")
    assert q == "Where are you? Which place should I bring it to?"
    goals, q = ground(env, [G("on", "apple", "desk", "me")], "Bring me the apple, I am at the sofa.")
    assert q is None and goals[0]["to"] == "sofa@living room"
    goals, q = ground(env, [G("on", "apple", "desk", "dining table")], "Put the apple on the dining table.")
    assert q is None and goals[0]["to"] == "dining table@kitchen" and "hand" not in goals[0]


def test_seat_answer_to_person():
    ps = Places(ENV)
    assert persons.seat_person(ps, "armchair@lounge") == "auntie"               # the entry's seat field
    env = copy.deepcopy(ENV)
    env["auntie"].pop("seat")
    env["auntie"]["loc"] = {"x": 0.3, "y": 0.0}
    assert persons.seat_person(Places(env), "armchair@lounge") == "auntie"      # within person_seat_m
    env["auntie"]["loc"] = {"x": 3.0, "y": 0.0}
    assert persons.seat_person(Places(env), "armchair@lounge") is None


# ── F2.3 ──
HAND_EN = ["Hand me the mug.", "Could you hand it to me?", "Please pass it straight to me.", "Put it in my hand.",
           "Give it to me directly.", "Hand the keys over to me.", "Can you place it into my hands?",
           "Pass me the remote.", "Hand them to me when you come.", "Hand nephew his book."]
HAND_KO = ["제 손에 바로 쥐여 줄래요", "손에 쥐여 주세요", "저한테 건네주세요", "직접 건네줘요", "손으로 받을게요",
           "조카한테 책 건네 줄래요", "이모 손에 쥐어 드려요", "그거 제 손으로 주세요", "리모컨 건네줄래요", "컵 건네 주세요"]
NOT_HAND = ["Bring me the mug.", "Put the hand towel on the worktop.", "머그잔 가져다줘요", "Leave it near me.",
            "수건을 조리대에 놓아 주세요"]


def test_hand_words_choose_handover():
    goals, q = ground(ENV, [G("on", "mug", "worktop@galley", "me")], "Hand me the mug.")
    assert q is None and goals[0]["to"] == "auntie" and goals[0]["hand"] is True


def test_hand_words_heldout_paraphrases(capsys):
    """Phrasings written for this test (not from any generator): the recall is reported; most must be read."""
    hits = [t for t in HAND_EN + HAND_KO if persons.wants_hand(t)]
    recall = len(hits) / len(HAND_EN + HAND_KO)
    print(f"hand-word recall on held-out paraphrases: {recall:.2f}")
    assert recall >= 0.8
    assert not [t for t in NOT_HAND if persons.wants_hand(t)]


def test_spot_null_handover():
    goals, q = ground(ENV, [G("on", "book", "worktop@galley", "nephew")], "Bring the book to my nephew.")
    assert q is None and goals[0]["to"] == "nephew" and goals[0]["hand"] is True


def test_spot_absent_fallback_nearest_surface():
    env = copy.deepcopy(ENV)
    env["auntie"].pop("spot")
    goals, _q = ground(env, [G("on", "mug", "worktop@galley", "me")], "Bring me the mug.")
    assert goals[0]["to"] == "lamp table@lounge" and not goals[0].get("hand")
    env["auntie"]["loc"] = {"x": 9.0, "y": 9.0}                                  # no surface within reach
    goals, _q = ground(env, [G("on", "mug", "worktop@galley", "me")], "Bring me the mug.")
    assert goals[0]["to"] == "auntie" and goals[0]["hand"] is True


def test_default_place_near_person():
    goals, _q = ground(ENV, [G("on", "mug", "worktop@galley", "auntie")], "Bring the mug to auntie.")
    assert goals[0]["to"] == "auntie" and not goals[0].get("hand")        # the entry stands at its spot


# ── untracked people (people_tracking off): ask, then the map ──
def untracked():
    env = copy.deepcopy(ENV)
    for k in ("auntie", "nephew"):
        for f in ("loc", "seat", "spot"):
            env[k].pop(f, None)
    return env


@pytest.mark.parametrize("answer, to", [("I'm in the armchair.", "lamp table@lounge"),
                                        ("안락의자에 있어요", "lamp table@lounge"),
                                        ("I'm on the bunk", None)])
def test_untracked_speaker_asks_where_then_maps(answer, to):
    lp = bare_loop()
    goals, q = ground(untracked(), [G("on", "mug", "worktop@galley", "me")], "Bring me the mug.", lp=lp)
    assert q == "Where are you?"
    # the answer, with the model's goal still "me" ...
    goals, q = ground(untracked(), [G("on", "mug", "worktop@galley", "me")], "Bring me the mug.", latest=answer, lp=lp)
    assert q is None
    if to:
        assert goals[0]["to"] == to and goals[0]["near_person"] == "auntie"
    else:                                          # no support surface beside the bunk: a hand-over
        assert goals[0]["to"] == "auntie" and goals[0]["hand"] is True
    # ... or the model put the answer's seat as the destination: never the seat itself
    goals, q = ground(untracked(), [G("on", "mug", "worktop@galley", answer.split()[-1].strip(".") if answer.isascii()
                                      else "armchair")], "Bring me the mug.", latest=answer, lp=lp)
    assert goals[0]["to"] != "armchair@lounge" and goals[0]["to"] != "bunk@kids room"


def test_untracked_other_person_asks_where_they_are():
    lp = bare_loop()
    _g, q = ground(untracked(), [G("on", "book", "worktop@galley", "nephew")], "Take the book to my nephew.", lp=lp)
    assert q == "Where is nephew?"


# ── F2.2 ──
def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


class HandRobot:
    """The bits of RobotView the goal bookkeeping reads: a robot whose Handover releases the object."""

    def release_actions(self):
        return ["Handover"]


def test_handover_reaches_person_goal():
    lp = bare_loop(HandRobot())
    lp._env_places = Places(ENV)
    plan = steps("MoveTo worktop@galley", "Find mug", "Pick mug", "MoveTo auntie", "Handover auntie")
    assert lp._missing(plan, W(), [G("on", "mug", "worktop@galley", "auntie")]) == []
    assert lp._missing(plan, W(), [dict(G("on", "mug", "worktop@galley", "auntie"), hand=True)]) == []
    # a hand delivery is not reached by a put-down
    put = steps("MoveTo worktop@galley", "Find mug", "Pick mug", "MoveTo auntie", "Place auntie")
    assert lp._missing(put, W(), [dict(G("on", "mug", "worktop@galley", "auntie"), hand=True)])
    assert lp._missing(put, W(), [G("on", "mug", "worktop@galley", "auntie")]) == []


def test_handover_elsewhere_not_reached():
    lp = bare_loop(HandRobot())
    lp._env_places = Places(ENV)
    plan = steps("MoveTo worktop@galley", "Find mug", "Pick mug", "MoveTo lamp table@lounge",
                 "Handover lamp table@lounge")
    assert lp._missing(plan, W(), [G("on", "mug", "worktop@galley", "lamp table@lounge")])


def test_release_actions_from_the_manifest(robot):
    """fakebot has no hand-over skill: no release action (other robots unchanged)."""
    assert robot.release_actions() == [] and robot.capabilities()["release_actions"] == []


# ── F2.4 ──
def test_search_skips_person_entries():
    from robot_agent.core.planning import search as search_belief
    from robot_agent.core.planning.search_hooks import SearchHooks

    class R:
        places = Places(ENV)
        robot = type("Rb", (), {"adapter": None, "plan_actions": {"MoveTo", "Find", "Pick", "Open"}})()
        goals, memory = [], Memory("/nonexistent/m.json")

    class L(SearchHooks):
        cfg = {"search": search_belief.SearchConfig(enabled=True, rank="prior"), "live_path": None}
    r = R()
    list(L()._search_start(r))
    assert "auntie" not in r.search.order("mug") and "nephew" not in r.search.order("mug")


# ── F3.1 / F3.2 ──
def test_issue_skips_user_words():
    ps = Places(ENV)
    goals = [G("on", "mug", "worktop@galley", "auntie")]
    assert find_issues(goals, "Bring me the mug from the worktop.", ps, user_words={"me"}) == []
    assert find_issues(goals, "조리대에서 머그잔 나한테 가져다줘요", ps, user_words={"me"}) == []


def test_issue_listener_covered():
    ps = Places(ENV)
    goals = [{"type": "say", "object": "dinner is ready", "from": "", "to": "nephew"}]
    assert not [i for i in find_issues(goals, "Tell my nephew that dinner is ready.", ps) if i["kind"] == "place"]
    # a person named and in no goal is still an issue
    assert [i for i in find_issues([G("on", "mug", "", "worktop@galley")], "Put the mug on the worktop for my "
                                                                           "nephew.", ps) if i["kind"] == "place"]


def test_hangul_mention_boundaries():
    env = {"fridge@galley": {"aliases": ["냉장고"]}, "kimchi fridge@galley": {"aliases": ["김치냉장고"]},
           "me": {"aliases": ["나"], "person": "p"}}
    ps = Places(env)
    keys = lambda t: [m[3] for m in place_mentions(t, ps)]                     # noqa: E731
    assert keys("하나 주세요") == [] and keys("나중에 할게요") == []
    assert keys("김치냉장고에 넣어 주세요") == [{"kimchi fridge@galley"}]
    assert keys("냉장고에 넣어요") == [{"fridge@galley"}]
    assert keys("나 좀 도와줘요") == [{"me"}]
