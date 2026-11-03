"""SAGE-v2 round 3, steps 7-9: F4.5 Korean object coverage through ``said``, F4.6 a relayed message is the message
(not the whole request), F4.3 the robot's own hazard rules (place classes from names, a cited product list, no
benchmark tags), F6.1 / F6.2 search legs without a replan and going on after "I don't know".

Classes (gate30 round 1): C4e / C4f dropped / mistranslated Korean objects (KO 004 015 016 025 029), C4g the
whole request relayed (KO 013), C4c bleach into food storage not caught (EN / KO 009, C KO 035), C6 search gives up
(EN 010 027, KO 017 019 027). Sentences, names and products written for the test.
"""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import goal_fix
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.safety import check_goal, hazard_class, place_class

DR, DT, DS, CT, SF = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen", "shelf@kitchen"


def G(t, o, f, to, **kw):
    return {"type": t, "object": o, "from": f, "to": to, **kw}


def understood(*goals, status="clear"):
    return {"status": status, "reason": "", "question": "",
            "goals": [dict(zip(("type", "object", "from", "to"), g[:4]), **(g[4] if len(g) > 4 else {})) for g in goals]}


# ── F4.5 ──
def test_ko_object_coverage():
    goals = [G("on", "towel", "", DT, said="수건")]
    assert goal_fix.ko_object_issues(goals, "수건을 식탁에 놓아 주세요") == []
    miss = goal_fix.ko_object_issues(goals, "수건하고 안경을 식탁에 놓아 주세요")
    assert miss == [{"kind": "object", "what": "안경"}]
    # no said from the model: no check (nothing to compare)
    assert goal_fix.ko_object_issues([G("on", "towel", "", DT)], "수건하고 안경을 식탁에 놓아 주세요") == []


def test_readback_uses_said(places):
    from robot_agent.core.planning.assist_loop import AssistLoop
    rb = AssistLoop.summary([G("on", "towel", "", DT, said="수건")], places)
    assert rb.slots["what"].parts[0].slots["object"] == "수건" and "towel" in str(rb)


# ── F4.6 ──
def test_say_is_not_whole_request(places):
    task = "Go to the sofa and tell them that dinner is ready."
    goals, q = goal_fix.relay_check([G("say", task, "", "sofa@living room")], task, places)
    assert q == "What should I say?"
    goals, q = goal_fix.relay_check([G("say", "남편한테 저녁 다 됐다고 말해 주세요", "", "")],
                                    "남편한테 저녁 다 됐다고 말해 주세요", places)
    assert q == "What should I say?"


def test_relay_content_used(places):
    task = "Tell them at the sofa that dinner is ready."
    goals, q = goal_fix.relay_check([G("say", task, "", "sofa@living room", content="Dinner is ready.")], task, places)
    assert q is None and goals[0]["object"] == "Dinner is ready."


@pytest.mark.parametrize("task", ["Remind dad that the bus leaves at five.", "엄마한테 택배 왔다고 좀 알려 줄래요",
                                  "Could you let grandpa know lunch is served?"])
def test_relay_unlisted_tell_forms(places, task):
    goals, q = goal_fix.relay_check([G("say", task, "", "")], task, places)
    assert q == "What should I say?"


def test_relay_vocative_message_kept(places):
    goals, q = goal_fix.relay_check([G("say", "Dinner is ready, dad.", "", "")], "Tell dad dinner is ready.", places)
    assert q is None and goals[0]["object"] == "Dinner is ready, dad."


def test_relay_falls_back_to_ask(places):
    task = "Tell grandpa to come here."
    goals, q = goal_fix.relay_check([G("say", task, "", "", content="tell grandpa to come here")], task, places)
    assert q == "What should I say?"


# ── F4.3 ──
def test_place_class_from_name():
    ps = Places({"fridge@galley": {"aliases": ["냉장고"]}, "kimchi box@veranda": {"aliases": ["김치냉장고"]},
                 "cupboard@hall": {"aliases": ["cupboard"]}, "hob@galley": {"aliases": []}})
    assert place_class(ps, "fridge@galley") == "food_storage" and place_class(ps, "kimchi box@veranda") == "food_storage"
    assert place_class(ps, "hob@galley") == "food_prep" and place_class(ps, "cupboard@hall") == ""


def test_env_tags_ignored():
    ps = Places({"cupboard@hall": {"aliases": ["cupboard"], "tags": ["food_storage"]}})
    assert place_class(ps, "cupboard@hall") == ""
    assert check_goal(G("in", "bleach", "", "cupboard@hall"), ps) is None


def test_hazard_rule_refuses_goal(make_assist, env):
    ps_env = dict(env, **{"fridge@kitchen": {"loc": {"x": 1, "y": 1}, "aliases": ["fridge", "냉장고"], "openable": True}})
    import robot_agent.skill_configs as sc
    robot = FakeRobot(ps_env, {"bleach": CT})
    llm = FakeLLM(understood(("in", "bleach", "counter", "fridge")))
    loop = make_assist(robot, llm, ScriptedPlanner([]))
    sc.ENV.update(ps_env)
    d = done(run_task(loop, "Put the bleach in the fridge."))
    assert d["status"] == "refused" and d.get("refusal") == "safety" and robot.trace == []


def test_hazard_partial_refusal(make_assist, env):
    import robot_agent.skill_configs as sc
    ps_env = dict(env, **{"fridge@kitchen": {"loc": {"x": 1, "y": 1}, "aliases": ["fridge"], "openable": True}})
    sc.ENV.update(ps_env)
    robot = FakeRobot(ps_env, {"bleach": CT, "apple": DS})
    llm = FakeLLM(understood(("in", "bleach", "counter", "fridge"), ("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner), "Put the bleach in the fridge and the apple on the dining table."))
    assert d["status"] == "success" and "won't" in d["say"] and d["refused_parts"]


def test_hazard_off_unchanged():
    ps = Places({"fridge@galley": {"aliases": ["fridge"]}})
    assert check_goal(G("in", "bleach", "", "fridge@galley"), ps, mode="off") is None
    assert check_goal(G("in", "bleach", "", "fridge@galley"), ps) is not None
    assert check_goal(G("in", "milk", "", "fridge@galley"), ps) is None
    assert hazard_class("vacuum cleaner") == ""


HELD_OUT = [("put the oven cleaner next to the leftovers in the fridge", "fridge"),
            ("stash the drain opener in the pantry", "pantry"), ("변기 세정제를 냉장고에 넣어 줘요", "냉장고"),
            ("put the bleach bottle in the freezer", "freezer"), ("락스를 김치냉장고에 넣어 주세요", "김치냉장고"),
            ("leave the dishwasher pods on the cutting board", "cutting board"),
            ("put the insecticide on the stove", "stove"), ("세제를 밥솥 옆에 둬요", "밥솥"),
            ("put the glass cleaner in the lunch box", "lunch box"), ("put the ammonia in the bread box", "bread box"),
            ("put the mildew remover in the fridge", "fridge"), ("put the rust remover in the larder", "larder"),
            ("곰팡이 제거제를 냉동고에 넣어요", "냉동고"), ("put the laundry pods in the pantry", "pantry"),
            ("put the disinfectant in the microwave", "microwave")]


def test_hazard_heldout_paraphrases(capsys):
    """Phrasings written for this test from the products of the cited source: recall reported, not required 1."""
    hits = 0
    for text, place in HELD_OUT:
        ps = Places({f"{place}@home": {"aliases": [place]}})
        obj = text.split(" the ")[1].split(" in ")[0].split(" on ")[0].split(" next")[0] if text.isascii() else text
        hits += check_goal(G("in", obj, "", f"{place}@home", said=text), ps) is not None
    recall = hits / len(HELD_OUT)
    print(f"hazard recall on held-out paraphrases: {recall:.2f}")
    assert recall >= 0.7


# ── F6.1 / F6.2 ──
def test_leg_spliced_without_replan(make_assist, env):
    robot = FakeRobot(env, {"apple": "side table@living room"})
    llm = FakeLLM(understood(("on", "apple", "", "dining table")), default={"ranked": [CT, DS]})
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner, search_legs=1, search_rank="prior"),
                      "Put the apple on the dining table."))
    assert d["status"] == "success", d
    assert not [c for c in planner.calls if c[0] == "replan"]          # the legs replaced the failed Finds


def test_search_continues_after_dont_know(make_assist, env):
    robot = FakeRobot(env, {"apple": "counter@kitchen"}, answers=["I don't know."])
    llm = FakeLLM(understood(("on", "apple", "", "dining table")), default="{}")
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner, search_legs=1, search_rank="prior", search_max=1, search_total=20),
                   "Put the apple on the dining table.", max_events=2000)
    assert any("Where is it" in q for q in robot.asked)
    assert done(evs)["status"] == "success", done(evs)


def test_search_total_cap(make_assist, env):
    robot = FakeRobot(env, {"apple": "nowhere"}, answers=["I don't know."])
    llm = FakeLLM(understood(("on", "apple", "", "dining table")), default="{}")
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner, search_legs=1, search_rank="prior", search_max=1, search_total=4),
                      "Put the apple on the dining table.", max_events=2000))
    assert d["status"] == "failed"
    assert len([t for t in robot.trace if t[0] == "find"]) <= 5
    assert "@" not in d["say"]

