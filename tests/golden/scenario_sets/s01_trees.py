"""S01 goldens, tree conditions and effect predicates (needed by S06): a go goal through its tree (``at``), an in goal and a
close goal through trees with ``opened`` / ``closed`` / ``at``, and the two line actions of ``fakeline`` whose declared effects
are a predicate the belief tracks (``on``) and one it does not (``fastened``). Recorded on the old code."""

import copy

from scenarios import BENCH, BT_ON, BT_PACK, CRATE, FLAT, SHELF, flat, understood
from scenario_sets.s01_kinds import BIN, JIG, line

DRAWER = "drawer@den"
DFLAT = dict(FLAT, **{DRAWER: {"aliases": ["drawer"], "loc": {"x": 5.0, "y": 2.0}, "surface": True, "openable": True}})
GO_TO = ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]]
OPENED = ["fallback", ["cond", "opened", "{to}"],
          ["sequence", ["cond", "hand_free"], GO_TO, ["act", "Open", "{to}"], ["cond", "opened", "{to}"]]]
FETCH = ["fallback", ["cond", "holding", "{object}"],
         ["sequence", ["cond", "hand_free"], ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["act", "Pick", "{object}"],
          ["cond", "holding", "{object}"]]]
IN_TREE = ["fallback", ["cond", "placed", "{object}", "{to}"],
           ["sequence", OPENED, FETCH, GO_TO, ["act", "PutIn", "{to}"], ["cond", "placed", "{object}", "{to}"], ["cond", "hand_free"]]]
CLOSE_TREE = ["fallback", ["cond", "closed", "{object}"],
              ["sequence", ["cond", "hand_free"], ["fallback", ["cond", "at", "{object}"], ["act", "MoveTo", "{object}"]],
               ["act", "Close", "{object}"], ["cond", "closed", "{object}"]]]
DRAWER_PACK = copy.deepcopy(BT_PACK)
# a made-up staging tree: put the object down on the crate, pick it up again, then go on only when it is no longer "placed" there
STAGING = ["sequence", FETCH, ["fallback", ["cond", "at", CRATE], ["act", "MoveTo", CRATE]], ["act", "Place", CRATE],
           ["act", "Find", "{object}"], ["act", "Pick", "{object}"],
           ["fallback", ["cond", "placed", "{object}", CRATE], ["act", "MoveTo", "{to}"]], ["act", "Place", "{to}"],
           ["cond", "placed", "{object}", "{to}"]]
STAGING_PACK = copy.deepcopy(BT_PACK)
STAGING_PACK["bt_goal"]["trees"]["on"] = {"tree": STAGING, "assumed": True}
DRAWER_PACK["bt_goal"]["trees"].update({"in": {"tree": IN_TREE, "assumed": True}, "close": {"tree": CLOSE_TREE, "assumed": True}})


def _said(*goals):
    return {"script": [], "default": understood(*goals)}


SCENARIOS = [
    (dict(flat("bt_go_tree", objects={"gizmo": SHELF}), task="Go to the bench.", llm=_said(("go", "", "", BENCH)),
          planner={"plan": ["MoveTo " + BENCH], "replans": []}),
     ["bt_goal", "success"], "a go goal through its declared tree: the condition at is false, MoveTo, then it holds"),
    (dict(flat("bt_drawer_conditions", objects={"gizmo": SHELF}), env=DFLAT, pack_options=DRAWER_PACK,
          task="Take the gizmo from the shelf, put it in the drawer and close the drawer.",
          llm=_said(("in", "gizmo", SHELF, DRAWER), ("close", DRAWER, "", "")),
          planner={"plan": ["MoveTo " + DRAWER, "Open " + DRAWER, "MoveTo " + SHELF, "Find gizmo", "Pick gizmo", "MoveTo " + DRAWER,
                            "PutIn " + DRAWER, "Close " + DRAWER], "replans": []}),
     ["bt_goal", "success"], "an in goal and a close goal through trees: the conditions opened, closed, at, placed, hand_free"),
    (line("effect_untracked_predicate", "Put the bracket on the jig and fasten it.", [("on", "bracket", "bin", "jig")],
          plan=["MoveTo " + BIN, "Find bracket", "Pick bracket", "MoveTo " + JIG, "Fasten bracket", "Place " + JIG]),
     ["success", "fakeline"], "fakeline's Fasten declares the effect fastened(bracket), a predicate the belief does not track: "
     "not checked"),
    (line("effect_on_predicate", "Put the bracket on the jig and inspect it.", [("on", "bracket", "bin", "jig")],
          plan=["MoveTo " + BIN, "Find bracket", "Inspect bracket", "Pick bracket", "MoveTo " + JIG, "Place " + JIG]),
     ["success", "fakeline"], "fakeline's Inspect declares the effect not on(bracket): the on predicate of the belief is read"),
    (dict(flat("bt_placed_in_hand", objects={"gizmo": SHELF}), pack_options=STAGING_PACK),
     ["bt_goal", "success"], "a made-up staging tree: the gizmo is put on the crate, picked up again, and the condition "
     "placed(gizmo, crate) is false while it is in the hand, so the robot moves on to the bench"),
]
