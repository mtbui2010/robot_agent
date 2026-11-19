"""Option ``bring_ask`` (default ON): "Bring me the mug." asks one combined question, "Where are you? Or where should I put it?", also when
the speaker is tracked; a place in the answer decides the put-down; no place is a hand-over; asked once; off, or a request that asks for a
hand-over, is the engine's own order."""
import copy

from test_sage_lab_r15_reach_delivery import DK, KC, ME, SO, ENV, bare, ground
from robot_agent.core.planning import speech

TASK = "Bring me the mug."
GOALS = [{"type": "on", "object": "mug", "from": KC, "to": "me"}]


def ask(lp, env, task=TASK, latest=""):
    from robot_agent.core.planning.assist_loop import Memory, Places
    return lp._ground_goals(copy.deepcopy(GOALS), Places(env), Memory("/nonexistent/m.json"), task, latest=latest)


def text(q):
    return str(q)


def test_on_by_default_in_the_engine_registry():
    from robot_agent.options import bring_ask, look_then_ask
    assert bring_ask.SPEC.keys[0].default is True and look_then_ask.SPEC.keys[0].default is True


def test_asks_one_combined_question_even_for_a_tracked_person():
    lp = bare(True, False, bring="1")
    goals, q = ask(lp, copy.deepcopy(ENV))                  # ME has a loc: tracked
    assert q is not None and text(q) == "Where are you? Or where should I put it?" and lp._where_asked == ME


def test_the_answer_is_a_place_they_are_at_and_the_object_goes_to_a_surface_near_it():
    lp = bare(True, False, bring="1")
    ask(lp, copy.deepcopy(ENV))
    goals, q = ask(lp, copy.deepcopy(ENV), latest="I am on the sofa")
    assert q is None and goals[0].get("user_at") == SO and goals[0]["to"] != ME


def test_an_answer_with_no_place_is_a_handover_and_the_question_is_not_asked_again():
    lp = bare(True, False, bring="1")
    ask(lp, copy.deepcopy(ENV))
    goals, q = ask(lp, copy.deepcopy(ENV), latest="just hand it to me please")
    assert q is None and goals[0].get("hand") is True


def test_off_a_tracked_person_is_not_asked():
    lp = bare(True, False, bring="0")
    goals, q = ask(lp, copy.deepcopy(ENV))
    assert q is None and lp._where_asked is None


def test_a_request_for_a_hand_over_is_not_asked():
    lp = bare(True, False, bring="1")
    goals, q = ask(lp, copy.deepcopy(ENV), task="Hand me the mug.")
    assert q is None


def test_the_phrase_is_a_pack_phrase_with_its_own_key():
    assert speech.CORE_PHRASES["q_where_bring"] == "Where are you? Or where should I put it?"


def test_through_the_loop_the_combined_question_is_asked_and_a_no_place_answer_hands_over(make_assist, monkeypatch):
    import robot_agent.skill_configs as sc
    monkeypatch.setattr(sc, "ENV", copy.deepcopy(ENV))                  # the loop's own map holds a speaker who is tracked
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
    from test_sage_lab_r15_reach_delivery import understood
    robot = FakeRobot(copy.deepcopy(ENV), {"mug": KC}, answers=["just hand it to me"])
    plan = steps(f"MoveTo {KC}", "Find mug", "Pick mug", f"MoveTo {ME}", f"Place {ME}")
    loop = make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(plan), bring_ask="1")
    events = run_task(loop, TASK, max_events=3000)
    qs = [str(e.get("question")) for e in events if e.get("event") == "clarify"]
    assert qs and qs[0] == "Where are you? Or where should I put it?" and sum("Where are you" in q for q in qs) == 1


def test_through_the_loop_a_place_in_the_answer_is_not_asked_about_again(make_assist, monkeypatch):
    import robot_agent.skill_configs as sc
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
    from test_sage_lab_r15_reach_delivery import understood
    monkeypatch.setattr(sc, "ENV", copy.deepcopy(ENV))
    robot = FakeRobot(copy.deepcopy(ENV), {"mug": KC}, answers=["I am on the sofa"])
    plan = steps(f"MoveTo {KC}", "Find mug", "Pick mug", f"MoveTo {ME}", f"Place {ME}")
    loop = make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(plan), bring_ask="1")
    events = run_task(loop, TASK, max_events=3000)
    qs = [str(e.get("question")) for e in events if e.get("event") == "clarify"]
    assert qs[0] == "Where are you? Or where should I put it?"
    assert not any("You mentioned" in q or "Where are you" in q for q in qs[1:]), qs


def test_the_place_the_person_said_is_not_the_objects_source():
    lp = bare(True, False, bring="1")
    ask(lp, copy.deepcopy(ENV))
    g = [{"type": "on", "object": "mug", "from": SO, "to": "me"}]      # the model read "I am on the sofa" as where the mug is
    from robot_agent.core.planning.assist_loop import Memory, Places
    out, q = lp._ground_goals(copy.deepcopy(g), Places(copy.deepcopy(ENV)), Memory("/nonexistent/m.json"), TASK + "  I am on the sofa", latest="I am on the sofa")
    assert q is None and out[0].get("from") != SO


def test_a_source_the_request_names_is_kept():
    lp = bare(True, False, bring="1")
    task = "Bring me the mug from the sofa."
    ask(lp, copy.deepcopy(ENV), task=task)
    g = [{"type": "on", "object": "mug", "from": SO, "to": "me"}]
    from robot_agent.core.planning.assist_loop import Memory, Places
    out, _q = lp._ground_goals(copy.deepcopy(g), Places(copy.deepcopy(ENV)), Memory("/nonexistent/m.json"), task + "  I am on the sofa", latest="I am on the sofa")
    assert out[0].get("from") == SO
