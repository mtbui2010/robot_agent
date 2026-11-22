"""S01 goldens, recovery ladders and the end check (needed by S12, S13, S14): the MoveTo help question outside a tree, a drop
refound on a floor place, the search legs of a Find miss, a line action that fails and goes to the planner, a tree with a
condition no table knows (the planner plans), and an end check that does not see the object. Recorded on the old code."""

from scenarios import BT_PACK, CT, DS, DT, HOUSE, flat, house
from scenario_sets.s01_kinds import BIN, JIG, line

import copy
import json

FETCH = ["MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT]
APPLE = ("on", "apple", "desk", "dining table")
SIM = json.loads((__import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "env_sim_house.json").read_text())
FLOORS = dict(SIM, **{"floor@kitchen": {"aliases": ["kitchen floor"], "loc": {"x": -0.5, "y": -1.5}, "height": 0.0},
                      "floor@study room": {"aliases": ["study floor"], "loc": {"x": 3.0, "y": 2.0}, "height": 0.0}})
LINE_PLAN = ["MoveTo " + BIN, "Find bracket", "Pick bracket", "MoveTo " + JIG, "Fasten bracket", "Place " + JIG]
UNKNOWN_COND = copy.deepcopy(BT_PACK)
UNKNOWN_COND["bt_goal"]["trees"]["on"] = {"tree": ["fallback", ["cond", "clamped", "{object}"],
                                                   ["sequence", ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"],
                                                    ["act", "Pick", "{object}"], ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"]]],
                                          "assumed": True}

SCENARIOS = [
    (house("move_help", "Bring the apple from the desk to the dining table.", [APPLE], options={"ask_help": True},
           robot={"faults": [{"skill": "move", "obj": DT, "kind": "fail", "nth": [1, 2], "msg": "path blocked"}],
                  "answers": ["yes"]}),
     ["question", "success"], "a MoveTo that fails twice outside a tree: the engine's retry, then its help question "
     "(HELP_MOVE) answered yes, the move succeeds"),
    (dict(house("drop_refind", "Bring the apple from the desk to the dining table.", [APPLE],
                robot={"drops": [{"nth": 2, "to": "floor@kitchen"}]}), env=FLOORS),
     ["success"], "the apple falls on the kitchen floor on the way (a floor place of the ENV): the drop, the REFIND rung "
     "finds it there, picks it up, success"),
    (house("search_legs", "Bring the apple from the desk to the dining table.", [APPLE], objects=dict(HOUSE, apple=CT),
           options={"search_legs": "1"}, llm_script=[["Rank the places where the apple", {"places": [CT]}]]),
     ["success"], "the apple is not on the desk: the Find miss goes on a search leg to the counter (kind-less), no replan"),
    (line("custom_action", "Put the bracket on the jig and fasten it.", [("on", "bracket", "bin", "jig")], plan=LINE_PLAN,
          replans=[["Fasten bracket", "Place " + JIG]],
          robot={"faults": [{"skill": "fasten", "kind": "fail", "nth": 1, "msg": "clamp did not close"}]}),
     ["replan", "fakeline"], "fakeline's Fasten fails: no ladder of its own, the failure goes to the planner's replan"),
    (dict(flat("custom_tree_unknown_condition"), pack_options=UNKNOWN_COND),
     ["bt_goal", "success"], "a declared tree with a condition no table knows (clamped): not usable, the planner plans"),
    (house("goal_absent", "Bring the apple from the desk to the dining table.", [APPLE],
           robot={"faults": [{"skill": "place", "kind": "place_fall", "nth": 1}]}),
     ["failure"], "the place reports success but the apple fell: the end check does not see it on the dining table"),
]
