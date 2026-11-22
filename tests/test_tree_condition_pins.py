"""The six tree conditions of option bt_goal are checked on both sides (review of S01, 2026-11-23: ``hand_free`` always true and
``holding o`` true for any held object passed every cassette and test). Step S06 moves these conditions into the pack's predicate
table; these rows and S06's own tree runs (a full hand when ``hand_free`` is evaluated, another object held when ``holding o`` is)
are its proof."""

from types import SimpleNamespace as W

import pytest

from robot_agent.options import bt_goal


@pytest.mark.parametrize("name, args, world, placed, want", [
    ("hand_free", (), W(holding=None), {}, True),
    ("hand_free", (), W(holding="cup"), {}, False),                     # a full hand
    ("holding", ("cup",), W(holding="cup"), {}, True),
    ("holding", ("cup",), W(holding="plate"), {}, False),               # another object held
    ("holding", ("cup",), W(holding=None), {}, False),
    ("at", ("bench@den",), W(arrived="bench@den"), {}, True),
    ("at", ("bench@den",), W(arrived="shelf@loft"), {}, False),
    ("placed", ("cup", "bench@den"), W(holding=None), {"cup": "bench@den"}, True),
    ("placed", ("cup", "bench@den"), W(holding="cup"), {"cup": "bench@den"}, False),
    ("placed", ("cup", "bench@den"), W(holding=None), {"cup": "crate@den"}, False),
    ("opened", ("drawer",), W(opened=["drawer"]), {}, True),
    ("opened", ("drawer",), W(opened=[]), {}, False),
    ("closed", ("drawer",), W(opened=[]), {}, True),
    ("closed", ("drawer",), W(opened=["drawer"]), {}, False),
    ("unknown", (), W(), {}, False),
    ("holding", (), W(holding="cup"), {}, False),                       # a wrong arity is false
])
def test_each_condition_is_true_and_false_where_it_should_be(name, args, world, placed, want):
    assert bt_goal.holds(name, args, world, placed) is want


def test_the_condition_names_are_the_six_of_today():
    assert bt_goal.CONDITIONS == ("holding", "hand_free", "at", "placed", "opened", "closed")
