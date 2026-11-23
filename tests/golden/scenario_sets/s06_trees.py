"""S06 goldens, the two tree conditions that no earlier run evaluated in the case where they are false (review of S01, 2026-11-23):
``hand_free`` with an object in the hand, and ``holding o`` while ANOTHER object is held. Step S06 moves the six tree conditions
into the pack's predicate table; these runs and tests/test_tree_condition_pins.py kill the mutations ``hand_free_always_true``
and ``holding_any_object``. Made-up trees on the made-up flat, as in s01_trees.py: the tree picks the gizmo up first, so the
belief holds it when the condition is read. Recorded on the old code."""

import copy

from scenarios import BENCH, BT_PACK, SHELF, flat, understood

TAKE = [["act", "MoveTo", SHELF], ["act", "Find", "gizmo"], ["act", "Pick", "gizmo"]]
HAND_FULL = copy.deepcopy(BT_PACK)
HAND_FULL["bt_goal"]["trees"]["go"] = {"assumed": True, "tree": [
    "sequence", *TAKE, ["fallback", ["cond", "hand_free"], ["act", "MoveTo", "{to}"]], ["cond", "at", "{to}"]]}
OTHER_HELD = copy.deepcopy(BT_PACK)
OTHER_HELD["bt_goal"]["trees"]["go"] = {"assumed": True, "tree": [
    "sequence", *TAKE, ["fallback", ["cond", "holding", "widget"], ["act", "MoveTo", "{to}"]], ["cond", "at", "{to}"]]}


def _go(name, pack):
    """A go goal whose tree takes the gizmo, then tests a condition while the hand holds it."""
    return dict(flat(name, objects={"gizmo": SHELF}), task="Go to the bench.", pack_options=pack,
                llm={"script": [], "default": understood(("go", "", "", BENCH))},
                planner={"plan": ["MoveTo " + BENCH], "replans": []})


SCENARIOS = [
    (_go("bt_hand_full", HAND_FULL), ["bt_goal", "success"],
     "a go tree that takes the gizmo, then moves unless the hand is free: the hand is full, so hand_free is false and MoveTo runs"),
    (_go("bt_holding_other_object", OTHER_HELD), ["bt_goal", "success"],
     "a go tree that takes the gizmo, then moves unless it holds the widget: another object is held, so holding is false and "
     "MoveTo runs"),
]
