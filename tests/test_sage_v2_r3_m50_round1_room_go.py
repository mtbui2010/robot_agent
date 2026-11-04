"""A go goal to a whole room is not an 'unsaid place' (synthetic places)."""
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.planning.understand import find_issues

ENV = {"cot@attic": {"aliases": ["cot"]}, "chest@attic": {"aliases": ["chest"]},
       "bench@yard": {"aliases": ["bench"]}}


def test_go_to_a_named_room_needs_no_place_word():
    ps = Places(ENV)
    goals = [{"type": "go", "object": "", "from": "", "to": "cot@attic"}]
    assert find_issues(goals, "please wait in the attic", ps) == []


def test_go_to_a_place_of_another_room_is_still_unsaid():
    ps = Places(ENV)
    goals = [{"type": "go", "object": "", "from": "", "to": "bench@yard"}]
    assert any(i["kind"] == "unsaid" for i in find_issues(goals, "please wait in the attic", ps))


def test_a_delivery_into_a_room_still_asks_where():
    ps = Places(ENV)
    goals = [{"type": "on", "object": "mug", "from": "", "to": "cot@attic"}]
    assert any(i["kind"] == "unsaid" for i in find_issues(goals, "put the mug in the attic", ps))
