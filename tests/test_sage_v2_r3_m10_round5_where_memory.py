"""A place the person said they are at survives a later answer (another question's) in the same run."""
from robot_agent.core.planning.assist_loop import AssistLoop, Places


def _loop():
    env = {"you": {"person": True, "speaker": True, "aliases": ["me"]},
           "sofa@den": {"surface": False, "loc": {"x": 1.0, "y": 1.0}},
           "side@den": {"surface": True, "loc": {"x": 1.5, "y": 1.0}},
           "desk@den": {"surface": True, "loc": {"x": 3.0, "y": 3.0}}}
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg = {"person_seat_m": 1.0, "person_surface_m": 1.2}
    lp.robot, lp._where_asked, lp._person_at = None, None, {}
    return lp, Places(env)


def test_the_where_answer_is_not_forgotten_after_a_later_answer():
    lp, places = _loop()
    g = {"type": "on", "object": "cup", "to": "you"}
    assert lp._person_destination(g, places, "bring me the cup", "").key == "q_where_are_you"
    g = {"type": "on", "object": "cup", "to": "you"}
    assert lp._person_destination(g, places, "bring me the cup", "I am on the sofa") is None
    # a later answer (to a different question) is the latest one: the place is still known
    g = {"type": "on", "object": "cup", "to": "you"}
    assert lp._person_destination(g, places, "bring me the cup", "just bring it please") is None
    assert g["to"] == "side@den" and g["near_person"] == "you"


def test_without_a_where_question_a_remembered_place_is_not_invented():
    lp, places = _loop()
    lp._person_at = {"you": "sofa@den"}
    g = {"type": "on", "object": "cup", "to": "you"}
    q = lp._person_destination(g, places, "bring me the cup", "just bring it")
    assert q is not None and q.key == "q_where_are_you"
