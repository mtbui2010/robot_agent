"""Domain profiles (core/domain.py, robot_agent/domains/<name>/): loading, the household default, the delivery
profile, and the manifest key that selects one."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from robot_agent.contract import Manifest
from robot_agent.contract.manifest import ManifestError
from robot_agent.core import domain
from robot_agent.core.planning import goals, lexicon, understand
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.safety import check_goal
from robot_agent.core.safety.place_class import place_class

ALL_ACTIONS = {"MoveTo", "Say", "Pick", "Place", "PutIn", "Open", "Close", "WaitForUnload"}
PLACES = SimpleNamespace(env={"fridge": {}, "table": {}})
BLEACH = {"type": "in", "object": "bleach", "to": "fridge"}


def robot(dom=None):
    d = {"robot": "r"}
    if dom is not None:
        d["domain"] = dom
    return SimpleNamespace(manifest=Manifest.from_dict(d))


def test_profiles_are_listed_and_complete():
    assert {"household", "delivery"} <= set(domain.available())
    for n in domain.available():
        d = domain.load(n)
        assert d.name == n and d.goals.get("kinds") and set(d.template_kinds) <= set(d.goals["kinds"])
        assert d.readback and d.lexicon is not None and d.refusal_mark("safety") and d.refusal_mark("capability")


def test_unknown_domain_is_an_error():
    with pytest.raises(domain.DomainError, match="unknown domain"):
        domain.load("nope")
    with pytest.raises(domain.DomainError):
        domain.load("../household")


def test_default_is_household_and_module_names_are_its_tables():
    assert domain.of(None).name == domain.of(robot()).name == "household"
    assert goals.MANIPULATION == ("on", "in", "open", "close")
    assert goals.TEMPLATE_KINDS == ("go", "say", "deliver")
    assert list(goals.GOAL_KINDS) == ["on", "in", "open", "close", "go", "say", "deliver"]
    assert goals.GOAL_KINDS["in"].needs == ("Pick", ("PutIn", "Place"))
    assert place_class(PLACES, "fridge") == "food_storage"
    assert check_goal(BLEACH, PLACES) == "cleaning chemicals do not go with food"
    assert lexicon.PUTIN["en"] and lexicon.KO_PARTICLES[0] == "이랑"
    assert set(understand._MARK) == {"safety", "capability"}


def test_household_kinds_for_a_robot_with_and_without_actions():
    assert goals.kinds_for(ALL_ACTIONS) == ["on", "in", "open", "close", "go", "say", "deliver"]
    assert goals.kinds_for({"MoveTo"}) == ["on", "in", "open", "close", "go"]


def test_delivery_has_no_manipulation_kinds():
    d = domain.load("delivery")
    assert d.manipulation == ()
    assert goals.kinds_for(ALL_ACTIONS, d) == ["go", "say", "deliver"]
    assert goals.is_template([{"type": "go"}], d) and not goals.is_template([{"type": "on"}], d)
    assert goals.missing_actions({"type": "on"}, ALL_ACTIONS, d) == ["on"]       # not a kind of this profile
    assert goals.prompt_lines(["go", "on"], d).count("\n") == 1


def test_readback_phrases_are_the_same_in_both_profiles():
    g = [{"type": "go", "to": "kitchen"}, {"type": "say", "object": "hi", "to": "kitchen"},
         {"type": "say", "object": "hi", "person": "mom"}, {"type": "deliver", "object": "tray", "to": "bedroom"},
         {"type": "deliver", "to": "den@home"}]
    want = ['go to the kitchen', 'say "hi" in the kitchen', 'say "hi" to mom', "deliver the tray to the bedroom",
            "deliver what I carry to the den in the home"]
    for dom in (None, domain.load("household"), domain.load("delivery")):
        assert [goals.summary(x, dom) for x in g] == want


def test_hazard_rules_belong_to_the_profile():
    assert check_goal(BLEACH, PLACES, (), "core", domain.load("household"))
    assert check_goal(BLEACH, PLACES, (), "core", domain.load("delivery")) is None
    assert place_class(PLACES, "fridge", domain.load("delivery")) == ""
    pack = [{"objects": [r"\bbleach\b"], "places": [r"fridge"], "say": "no"}]       # a pack's own rule still works
    assert check_goal(BLEACH, PLACES, pack, "core", domain.load("delivery")) == "no"


def test_lexicon_follows_the_robots_profile():
    text = "put it in the box"
    assert lexicon.has(lexicon.PUTIN, text, robot())
    assert not lexicon.has(lexicon.PUTIN, text, robot("delivery"))
    assert lexicon.has(lexicon.RECIPIENT, "bring me the tray", robot("delivery"))
    assert lexicon.has(lexicon.TELL, "tell mom", robot("delivery"))
    assert not lexicon.has(lexicon.HAND, "hand me the cup", robot("delivery"))
    assert lexicon.has(lexicon.HAND, "hand me the cup", robot())


def test_refusal_words_follow_the_profile():
    heard = dict(status="infeasible", reason="no arm", missing=None, answer_heard=True)
    assert understand.typed_refusal(words="pick up the cup", dom=domain.load("delivery"), **heard).kind == "capability"
    assert understand.typed_refusal(words="pick up the cup", **heard).misheard       # household: not a capability word
    assert understand.typed_refusal(words="pick up the cup", dom=domain.load("household"), **heard).misheard


def test_manifest_domain_key():
    assert Manifest.from_dict({"robot": "r"}).domain == "" and "domain" not in Manifest.from_dict({"robot": "r"}).to_dict()
    m = Manifest.from_dict({"robot": "r", "domain": "delivery"})
    assert m.domain == "delivery" and m.to_dict()["domain"] == "delivery" and not m.extra
    assert domain.name_of(m) == "delivery" and domain.of(m).name == "delivery"
    for bad in ("nope", 3, ""):
        with pytest.raises(ManifestError, match="domain"):
            Manifest.from_dict({"robot": "r", "domain": bad})


def test_assist_loop_uses_the_robots_profile():
    loop = object.__new__(AssistLoop)
    assert loop.domain.name == "household"              # no robot yet
    loop._robot = robot("delivery")
    loop._robot.plan_actions = ALL_ACTIONS
    assert loop.domain.name == "delivery"
    assert loop._understand_schema()["properties"]["goals"]["items"]["properties"]["type"]["enum"] == \
        ["go", "say", "deliver"]
    g = [{"type": "deliver", "object": "tray", "to": "bedroom"}]
    assert str(loop.summary(g)) == str(AssistLoop.summary(g)) == "I will deliver the tray to the bedroom."
