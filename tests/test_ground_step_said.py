"""A goal renamed by canon_inventory keeps the person's words in ``said``; a plan step that still uses those words
('grin book') is mapped to the goal's object ('book'), so the gate does not reject it for naming an unknown object."""
import types

from robot_agent.core.planning.assist_loop import AssistLoop

GOAL = {"type": "on", "object": "book", "said": "grin book", "from": "", "to": "table@kitchen"}


def _ground(step, goals):
    lp = AssistLoop.__new__(AssistLoop)
    places = types.SimpleNamespace(candidates=lambda o, h="": [])
    return lp._ground_step(step, places, goals, "bring the grin book")


def test_said_name_maps_to_the_renamed_object():
    assert _ground({"action": "Find", "object": "grin book"}, [GOAL])["object"] == "book"
    assert _ground({"action": "Pick", "object": "the grin book"}, [GOAL])["object"] == "book"


def test_other_names_are_left_alone():
    assert _ground({"action": "Find", "object": "cup"}, [GOAL])["object"] == "cup"
    assert _ground({"action": "Find", "object": "book"}, [GOAL])["object"] == "book"
    assert _ground({"action": "Find", "object": "grin book"}, [{**GOAL, "said": ""}])["object"] == "grin book"   # nothing said: no mapping
