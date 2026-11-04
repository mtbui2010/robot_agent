"""SAGE-v2 round 3, milestone-10 round-1 fixer (failure classes, not tasks).

* Clarification hygiene: a question after which the person said the whole request again is not a spent round (once
  per run); the run that still gives up says one plain sentence in the user's language, not its internal message.
* Language: the questions and doubts built from find_issues are robot phrases (LocalText), said in the user's
  language without a translation call.
* Hazards: household brands of cleaning chemicals are named by the core list too (a model that translates the
  person's word says a brand).
* Search: the ranking prompt shows the places grouped by room, so a model reasons over the whole home.

Synthetic words and places written for these tests.
"""
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.search import SearchBelief
from robot_agent.core.planning.understand import describe, issue_question
from robot_agent.core.safety import hazard_class


class R:
    task = "please move the red pot onto the shelf and then fetch my scarf from the closet"


def loop(original=None):
    lp = AssistLoop.__new__(AssistLoop)
    lp.original = original
    return lp


def test_restating_answer_is_detected():
    lp = loop()
    assert lp._restates(R, "Could you move the red pot onto the shelf and then fetch my scarf from the closet?")
    assert not lp._restates(R, "I don't know.")
    assert not lp._restates(R, "the scarf")                      # short answers are answers, not a restatement


def test_find_issue_questions_are_robot_phrases():
    issues = [{"kind": "place", "what": "terrace", "keys": []}, {"kind": "unsaid", "object": "scarf", "what": "scarf"}]
    q = issue_question(issues, None)
    assert isinstance(q, speech.LocalText) and q.key == "_join"
    assert [p.key for p in q.parts] == ["q_mentioned", "q_where_put"]
    d = describe(issues[0])
    assert isinstance(d, speech.LocalText) and d.key == "iss_place" and "terrace" in d


def test_unsure_doubts_render_in_a_robot_language():
    class Robot:
        class i18n:
            phrases = {"iss_place": {"ko": "{what} 말씀하셨는데 계획에 없어요"},
                       "rb_unsure": {"ko": "잘못 알아들었을 수 있어요 ({issues})."}, "sep_semi": {"ko": "; "}}
    u = [describe({"kind": "place", "what": "베란다", "keys": []})]
    t = speech.LocalText("rb_unsure", {"issues": speech.LocalText.seq(u, "sep_semi")})
    assert speech.render(t, "ko", Robot) == "잘못 알아들었을 수 있어요 (베란다 말씀하셨는데 계획에 없어요)."


def test_household_brands_of_cleaning_chemicals_are_hazards():
    for name in ("lysol", "Clorox bottle", "window cleaner windex", "라이솔"):
        assert hazard_class(name) == "cleaning_chemical", name
    assert hazard_class("water bottle") == ""


def test_rank_prompt_groups_places_by_room():
    b = SearchBelief(["shelf@den", "bin@den", "rack@yard"])
    p = b.rank_prompt("scarf")
    assert "den: shelf@den, bin@den" in p and "yard: rack@yard" in p
    assert "whole home" in p


def test_dialogue_give_up_messages_are_phrases_not_internal_text():
    lp = AssistLoop.__new__(AssistLoop)
    # the finish picks a robot phrase for the two messages that carry the last question
    import inspect
    src = inspect.getsource(AssistLoop._finish)
    assert "say_no_answer" in src and "say_not_confirmed" in src
    assert speech.CORE_PHRASES["say_no_answer"] and speech.CORE_PHRASES["say_not_confirmed"]


def test_unsaid_issue_without_an_object_asks_nothing():
    q = issue_question([{"kind": "unsaid", "object": "", "what": ""}], None)
    assert q == ""


def _loop_with_people():
    from robot_agent.core.planning.assist_loop import Places
    env = {"you": {"person": True, "speaker": True, "aliases": ["me"]},
           "sofa@den": {"surface": False, "loc": {"x": 1.0, "y": 1.0}},
           "side@den": {"surface": True, "loc": {"x": 1.5, "y": 1.0}},
           "desk@den": {"surface": True, "loc": {"x": 3.0, "y": 3.0}}}
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg = {"person_seat_m": 1.0, "person_surface_m": 1.2}
    lp.robot, lp._where_asked = None, None
    return lp, Places(env)


def test_an_answer_to_another_question_does_not_place_the_person():
    lp, places = _loop_with_people()
    g = {"type": "on", "object": "cup", "to": "you"}
    # the request said again names the sofa, but "Where are you?" was never asked: the robot asks
    q = lp._person_destination(g, places, "bring me the cup", "I put the cup on the sofa please")
    assert isinstance(q, speech.LocalText) and q.key == "q_where_are_you"
    # after the question, the answer names where the person is: the surface beside it, never the seat
    g = {"type": "on", "object": "cup", "to": "you"}
    assert lp._person_destination(g, places, "bring me the cup", "I am on the sofa") is None
    assert g["to"] == "side@den" and g["near_person"] == "you"


def test_a_sentence_with_a_korean_alias_grounds_to_the_longest_alias_said():
    from robot_agent.core.planning.assist_loop import Places
    p = Places({"table@den": {"aliases": ["탁자", "table"]}, "box@den": {"aliases": ["상자"]},
                "big_box@den": {"aliases": ["큰상자"]}})
    assert p.ground("탁자 위에 있어요.", "탁자 위에 있어요.") == "table@den"
    assert p.ground("큰상자 안에 있어요", "큰상자 안에 있어요") == "big_box@den"      # not the shorter 상자
    assert p.ground("I do not know", "I do not know") is None
