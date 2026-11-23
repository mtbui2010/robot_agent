"""The sub-goal phrase of each goal kind, which the loop gives a planner that takes ``subgoals`` (``goals.subgoal_text``, option
``sage_subgoals=goals``, the default), keeps today's text (review of S01, 2026-11-23: "into" -> "in" passed every test, and the
harness's planner took no ``subgoals``). The design's "Goal kinds" section makes the phrase a per-kind field whose household
default is this text; the set cassettes ``subgoals_*`` record the list the planner gets."""

import pytest

from robot_agent.core.planning import goals

DT, DR, DS = "dining table@kitchen", "drawer@kitchen", "desk@study room"


@pytest.mark.parametrize("goal, want", [
    ({"type": "on", "object": "apple", "to": DT}, f"put the apple on {DT}"),
    ({"type": "on", "object": "apple", "from": DS, "to": DT}, f"put the apple (it is at {DS}) on {DT}"),
    ({"type": "on", "object": "apple", "from_any": [DS, "counter@kitchen"], "to": DT},
     f"put the apple (look for it at {DS} or counter@kitchen) on {DT}"),
    ({"type": "on", "object": "mug", "to": "me", "hand": True}, "hand the mug to me"),
    ({"type": "in", "object": "orange", "to": DR}, f"put the orange into {DR}"),
    ({"type": "in", "object": "orange", "from": DT, "to": DR}, f"put the orange (it is at {DT}) into {DR}"),
    ({"type": "open", "object": DR}, f"open {DR}"),
    ({"type": "close", "object": DR}, f"close {DR}"),
    ({"type": "go", "to": "sofa@living room"}, "go to sofa@living room"),
    ({"type": "say", "object": "dinner is ready", "to": "kitchen"}, 'say "dinner is ready" at kitchen'),
    ({"type": "say", "object": "hello", "person": "lena"}, 'say "hello" at lena'),
    ({"type": "say", "object": "hello"}, 'say "hello"'),
    ({"type": "deliver", "object": "parcel", "to": "station"}, "deliver the parcel to station"),
    ({"type": "deliver", "object": "", "to": "station"}, "deliver what the robot carries to station"),
    ({"type": "inspect", "object": "weld", "from": "jig"}, "inspect weld from jig"),     # a kind with no phrase: describe()
])
def test_each_kind_has_its_subgoal_phrase(goal, want):
    assert goals.subgoal_text(goal) == want
