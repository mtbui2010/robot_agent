"""D15: the deterministic reading of MODIFY / ADD / DROP / NEW utterances (core/planning/goal_change.py)."""

import pytest

from robot_agent.core.planning import goal_change

DS, DT, CT, SF, DR = "desk@study room", "dining table@kitchen", "counter@kitchen", "sofa@living room", "drawer@kitchen"


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


APPLE = [G("on", "apple", DS, DT)]
TWO = [G("on", "mug", DS, "chest@bedroom"), G("on", "apple", DS, DT)]


def test_modify_pronoun_is_the_current_goal(places):
    ch = goal_change.read("actually, put it on the counter instead", "modify", APPLE, places)
    assert ch.goals == [G("on", "apple", DS, CT)]
    assert ch.changed == [(APPLE[0], G("on", "apple", DS, CT))] and not ch.added and not ch.removed


def test_modify_bare_destination(places):
    ch = goal_change.read("no, the counter instead", "modify", APPLE, places)
    assert ch.goals == [G("on", "apple", DS, CT)]


def test_it_is_the_held_object(places):
    ch = goal_change.read("put it on the sofa", "modify", TWO, places, holding="apple")
    assert ch.goals[1]["to"] == SF and ch.goals[0]["to"] == "chest@bedroom"


def test_it_is_the_first_goal_not_reached(places):
    ch = goal_change.read("put it on the sofa", "modify", TWO, places, reached=lambda g: g["object"] == "mug")
    assert [g["to"] for g in ch.goals] == ["chest@bedroom", SF]


def test_add_merges_a_goal(places):
    ch = goal_change.read("also bring the mug to the dining table too", "add", APPLE, places)
    assert ch.goals == [APPLE[0], G("on", "mug", "", DT)] and ch.added == [G("on", "mug", "", DT)]


def test_add_into_an_openable_place_is_in(env):
    from robot_agent.core.planning.assist_loop import Places
    places = Places(env, openable=lambda k, v: k.startswith("drawer"))
    ch = goal_change.read("also put the spoon in the kitchen drawer", "add", APPLE, places)
    assert ch.added == [G("in", "spoon", "", DR)]


def test_drop_removes_the_named_goal(places):
    ch = goal_change.read("forget the apple", "drop", TWO, places)
    assert ch.goals == [TWO[0]] and ch.removed == [TWO[1]]


def test_drop_and_move_of_the_same_object_is_a_move(places):
    ch = goal_change.read("actually don't bring it, put the apple back on the desk", "drop", APPLE, places,
                          holding="apple")
    assert ch.goals == [G("on", "apple", "", DS)] and not ch.removed


def test_new_task(places):
    ch = goal_change.read("bring the banana to the sofa", "new", [], places)
    assert ch.goals == [G("on", "banana", "", SF)]


@pytest.mark.parametrize("text, intent", [
    ("not the apple, the banana", "modify"),        # an object swap: the LLM reads it
    ("put it somewhere nice", "modify"),            # no known place
    ("forget the banana", "drop"),                  # not a goal
    ("bring it to the sofa", "new"),                # a new task needs an object
    ("사과 말고 바나나", "modify"),                     # another language
    ("also bring the mug to the dining table and the banana to the sofa", "add"),
])
def test_unreadable_is_none(places, text, intent):
    assert goal_change.read(text, intent, APPLE, places) is None
