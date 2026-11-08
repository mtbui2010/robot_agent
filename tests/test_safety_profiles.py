"""Round 19: what is hazardous is the ROBOT's safety profile (data), not the core's. The same engine, three profiles
(household fixture, illustrative factory fixture, none) give different decisions for the same goal; a language the engine has
never seen works when the profile has words for it; a profile with no data refuses nothing; a bad profile is rejected by field
path. The household fixture reproduces the golden decision table made from the r17 code (core tables) before the move."""
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import safety_golden as SG
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, safety_profile, steps
from robot_agent.contract.manifest import Manifest, ManifestError
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.safety import EMPTY, check_goal, find_rule, hazard_class, load_profile, place_class, profile_of, receiver_class

HERE = Path(__file__).parent
FIX = HERE / "fixtures" / "safety"
RECV = ("hazard.receivers",)
FACTORY = safety_profile("factory_illustrative")
PLACES = Places({
    "fridge@kitchen": {"aliases": ["fridge"]}, "welding bay@shop": {"aliases": []}, "emergency exit@hall": {"aliases": []},
    "table@kitchen": {"aliases": []}, "child": {"person": "child", "aliases": ["the kid"]},
    "visitor": {"person": "visitor", "aliases": []}})


def G(t, o, to, **kw):
    return {"type": t, "object": o, "from": "", "to": to, **kw}


# ── the golden table of the r17 core tables ──
def test_the_household_profile_reproduces_the_r17_decisions(household):
    gold = json.loads((HERE / "golden" / "safety_household_r17.json").read_text(encoding="utf-8"))
    t = SG.table(lambda g, ps, pack, mode, rec: check_goal(g, ps, household, pack, mode, RECV if rec else ()))
    assert (t["n"], t["sha256"]) == (gold["n"], gold["sha256"])
    assert t["hits"] == gold["hits"]


# ── same engine, three profiles ──
@pytest.mark.parametrize("goal, enabled, household_hit, factory_hit", [
    (G("in", "bleach", "fridge@kitchen"), (), "chem_food", None),
    (G("on", "bleach", "child"), RECV, "chem_child", None),
    (G("on", "bleach", "child"), (), None, None),                                     # the receiver rule is off unless asked
    (G("on", "acetone", "welding bay@shop"), (), None, "solvent_heat"),
    (G("on", "box", "emergency exit@hall"), (), None, "keep_exits_clear"),
    (G("on", "acetone", "visitor"), RECV, None, "solvent_visitor"),
    (G("on", "acetone", "visitor"), (), None, None),
    (G("on", "milk", "table@kitchen"), RECV, None, None),
])
def test_one_engine_three_profiles(household, goal, enabled, household_hit, factory_hit):
    rule = lambda prof: (lambda v: v.rule if v else None)(find_rule(goal, PLACES, prof, (), "profile", enabled))   # noqa: E731
    assert rule(household) == household_hit and rule(FACTORY) == factory_hit
    assert rule(EMPTY) is None and check_goal(goal, PLACES, EMPTY, (), "profile", enabled) is None      # no profile: no rule


def test_no_profile_means_no_class_and_no_rule():
    assert hazard_class("bleach", EMPTY) == "" and place_class(PLACES, "fridge@kitchen", EMPTY) == ""
    assert receiver_class(PLACES, "child", EMPTY) == "" and EMPTY.mark("safety") is None
    assert find_rule(G("in", "bleach", "fridge@kitchen"), PLACES) is None


def test_the_legacy_manifest_list_still_applies_without_a_profile():
    legacy = [{"objects": ["bleach"], "places": ["fridge"], "say": "no bleach there"}]
    assert check_goal(G("in", "bleach", "fridge@kitchen"), PLACES, EMPTY, legacy) == "no bleach there"
    assert check_goal(G("in", "bleach", "fridge@kitchen"), PLACES, EMPTY, legacy, "off") is None
    assert check_goal(G("in", "bleach", "fridge@kitchen"), PLACES, FACTORY, legacy, "pack") == "no bleach there"


def test_checks_decide_which_goal_kinds_are_looked_at(household):
    assert check_goal(G("at", "bleach", "fridge@kitchen"), PLACES, household) is None
    wide = replace(household, checks=("on", "in", "at"))
    assert check_goal(G("at", "bleach", "fridge@kitchen"), PLACES, wide)


def test_a_made_up_language_is_classified_by_its_own_profile_words(household):
    xx = safety_profile("xx_language")
    ps = Places({"blikzone@a": {"aliases": []}, "fridge@kitchen": {"aliases": []}})
    assert check_goal(G("in", "zorbium", "blikzone@a"), ps, xx) == "zorbium stays out of a blik zone"
    assert check_goal(G("in", "zorbium", "fridge@kitchen"), ps, xx) is None
    assert check_goal(G("in", "zorbium", "blikzone@a"), ps, household) is None      # words of another profile do not apply
    assert check_goal(G("in", "bleach", "fridge@kitchen"), ps, xx) is None         # and nothing of the household is built in
    assert xx.mark("safety").search("ZORBIUM") and xx.mark("capability") is None


# ── the engine holds no domain data ──
def test_the_engine_names_no_class_or_rule_of_any_profile():
    names = set()
    for f in FIX.glob("*.yaml"):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        names |= set(d.get("hazard_classes", {})) | set(d.get("place_classes", {})) | set(d.get("receiver_classes", {}))
        names |= {r["id"] for r in d.get("rules", [])}
    src = "\n".join(p.read_text(encoding="utf-8") for p in (HERE.parent / "robot_agent" / "core" / "safety").glob("*.py"))
    assert len(names) >= 10 and not [n for n in sorted(names) if re.search(rf"\b{re.escape(n)}\b", src)]


def test_the_language_packs_hold_no_safety_keys():
    keys = ("hazard_cleaning", "place_food", "person_vulnerable", "refusal_safety", "refusal_capability")
    for f in (HERE.parent / "robot_agent" / "core" / "planning" / "lang").glob("*.json"):
        text = f.read_text(encoding="utf-8")
        assert not [k for k in keys if k in text], f.name


# ── the schema and its validator ──
def _base(**kw):
    d = yaml.safe_load((FIX / "factory_illustrative.yaml").read_text(encoding="utf-8"))
    d.update(kw)
    return d


@pytest.mark.parametrize("change, fragment", [
    ({"surprise": 1}, "safety.profile.surprise: unknown key"),
    ({"profile": ""}, "safety.profile.profile: required"),
    ({"version": "one"}, "version: must be an integer"),
    ({"hazard_classes": {"x": {"words": {"en": {"assumed": ["("]}}}}}, "bad regex"),
    ({"hazard_classes": {"x": {"words": {"en": {"sourced": ["a"]}}}}}, "sourced words but no `sources`"),
    ({"hazard_classes": {"x": {"words": {"en": {"sourced": ["a"]}}, "sources": ["nope"]}}}, "unknown source id 'nope'"),
    ({"hazard_classes": {"x": {"words": {"English": {"assumed": ["a"]}}}}}, "not a language code"),
    ({"place_classes": {"p": {"words": {"en": ["a"]}, "provenance": {"source": "nope"}}}}, "provenance"),
    ({"rules": [{"id": "r", "hazard": "ghost", "places": ["ignition_source"], "say": "s"}]}, "not a declared hazard class"),
    ({"rules": [{"id": "r", "hazard": "flammable_solvent", "say": "s"}]}, "exactly one of `places` and `receivers`"),
    ({"rules": [{"id": "r", "hazard": "flammable_solvent", "places": ["a"], "receivers": ["visitor"], "say": "s"}]}, "exactly one of `places` and `receivers`"),
    ({"rules": [{"id": "r", "places": ["a"], "say": "s"}]}, "exactly one of `hazard`"),
    ({"rules": [{"id": "r", "hazard": "flammable_solvent", "receivers": ["ghost"], "say": "s"}]}, "not a declared receiver class"),
    ({"rules": [{"id": "r", "hazard": "flammable_solvent", "places": ["a"]}]}, "say: required"),
    ({"rules": [{"id": "r", "hazard": "flammable_solvent", "places": ["a"], "say": "s", "enabled_by": "Bad Key"}]}, "enabled_by"),
    ({"rules": [{"hazard": "flammable_solvent", "places": ["a"], "say": "s"}]}, "id: required"),
    ({"refusal_marks": {"danger": {"en": ["a"]}}}, "refusal_marks.danger: unknown key"),
    ({"checks": []}, "checks: must be a non-empty list"),
])
def test_the_validator_rejects_a_bad_profile_by_field_path(change, fragment):
    prof, errs = load_profile(_base(**change))
    assert prof is EMPTY and any(fragment in e and e.startswith("safety.profile") for e in errs), errs


def test_a_good_profile_reports_its_counts_and_a_digest(household):
    s = household.summary()
    assert s["rules"] == 2 and s["hazard_classes"] == 1 and s["place_classes"] == 2 and s["receiver_classes"] == 1
    assert s["sourced_words"] > 0 and s["assumed_words"] > s["sourced_words"] and len(s["sha256"]) == 64 and not household.warnings


# ── the manifest ──
def _manifest(tmp_path, safety, name="m.yaml"):
    (tmp_path / "safety").mkdir(exist_ok=True)
    for f in FIX.glob("*.yaml"):
        (tmp_path / "safety" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    p = tmp_path / name
    p.write_text(yaml.safe_dump({"robot": "toy", "skills": [], "safety": safety}), encoding="utf-8")
    return p


def test_the_manifest_loads_a_profile_by_relative_path_or_inline(tmp_path):
    m = Manifest.from_yaml(_manifest(tmp_path, {"profile": "safety/factory_illustrative.yaml"}))
    assert m.safety_profile.name == "factory_illustrative" and m.safety["profile"] == "safety/factory_illustrative.yaml"
    assert m.to_dict()["safety"]["profile"] == "safety/factory_illustrative.yaml"
    m2 = Manifest.from_dict({"robot": "toy", "safety": {"profile": _base()}})
    assert m2.safety_profile.name == "factory_illustrative"
    assert Manifest.from_dict({"robot": "toy"}).safety_profile is None
    assert profile_of(type("R", (), {"manifest": m})()) is m.safety_profile and profile_of(object()) is EMPTY


@pytest.mark.parametrize("safety, fragment", [
    ({"profile": "safety/ghost.yaml"}, "safety.profile: no such file"),
    ({"profile": {"profile": "p", "rules": [{"id": "r", "hazard": "ghost", "places": ["a"], "say": "s"}]}}, "safety.profile.rules[0].hazard"),
    ({"profile": 7}, "safety.profile: must be a path or a mapping"),
])
def test_a_bad_profile_makes_the_manifest_invalid(tmp_path, safety, fragment):
    with pytest.raises(ManifestError) as e:
        Manifest.from_yaml(_manifest(tmp_path, safety))
    assert any(fragment in x for x in e.value.errors), e.value.errors
    with pytest.raises(ManifestError):
        Manifest.from_dict({"robot": "toy", "safety": {"profile": "relative.yaml"}})            # no manifest file to be relative to


def test_check_manifest_strict_fails_on_a_source_still_to_verify(tmp_path):
    from robot_agent.contract.check_manifest import check
    pkg = tmp_path / "toy"
    (pkg / "configs").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    d = _base(sources={"cpsc": {"url": "https://example.org/cpsc", "quote": "to be opened", "status": "verify"}})
    (pkg / "configs" / "manifest.yaml").write_text(yaml.safe_dump({"robot": "toy", "skills": [], "safety": {"profile": d}}), encoding="utf-8")
    rep = check(pkg)
    assert rep.ok and any("status: verify" in w for w in rep.warnings) and any("safety.profile factory_illustrative" in n for n in rep.notes)


# ── through the real AssistLoop ──
OBJ_ENV = {"fridge@kitchen": {"aliases": ["fridge"], "loc": {"x": 1, "y": 2}, "openable": True},
           "welding bay@shop": {"aliases": ["welding bay"], "loc": {"x": 3, "y": 2}}}


def _run(make_assist, profile, goal, task, monkeypatch):
    import robot_agent.skill_configs as sc
    robot = FakeRobot({**sc.ENV, **OBJ_ENV}, {"bleach": "counter@kitchen", "acetone": "counter@kitchen"})
    sc.ENV.update(OBJ_ENV)
    llm = FakeLLM({"status": "clear", "reason": "", "question": "", "goals": [goal]})
    loop = make_assist(robot, llm, ScriptedPlanner(steps()))
    monkeypatch.setattr(loop.robot.manifest, "safety_profile", profile)
    return done(run_task(loop, task)), robot


@pytest.mark.parametrize("prof, goal, task, refused, rule", [
    ("household_fixture", {"type": "in", "object": "bleach", "from": "counter", "to": "fridge"}, "Put the bleach in the fridge.", True, "chem_food"),
    ("factory_illustrative", {"type": "in", "object": "bleach", "from": "counter", "to": "fridge"}, "Put the bleach in the fridge.", False, None),
    ("factory_illustrative", {"type": "on", "object": "acetone", "from": "counter", "to": "welding bay"}, "Put the acetone on the welding bay.", True, "solvent_heat"),
    ("household_fixture", {"type": "on", "object": "acetone", "from": "counter", "to": "welding bay"}, "Put the acetone on the welding bay.", False, None),
])
def test_the_loop_follows_the_robots_profile(make_assist, monkeypatch, prof, goal, task, refused, rule):
    d, robot = _run(make_assist, safety_profile(prof), goal, task, monkeypatch)
    assert (d.get("status") == "refused" and d.get("refusal") == "safety") == refused
    if refused:
        assert robot.trace == [] and [x["rule"] for x in d["refusals"]] == [rule] and d["refusals"][0]["kind"] == "safety"
        assert d["refusals"][0]["goal"]["object"] == goal["object"] and d["refusals"][0]["why"]
    else:
        assert not d.get("refusals")


def test_a_loop_whose_robot_declares_no_profile_refuses_nothing_for_hazards(make_assist, monkeypatch):
    d, _robot = _run(make_assist, None, {"type": "in", "object": "bleach", "from": "counter", "to": "fridge"},
                     "Put the bleach in the fridge.", monkeypatch)
    assert d.get("refusal") != "safety" and not d.get("refusals")
