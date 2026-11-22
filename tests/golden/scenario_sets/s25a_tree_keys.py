"""S25a goldens, the opt-in keys of a bt_goal tree entry (Step S25a, docs/bt_goal.md "Tree keys"): ``ladder: false`` (the tree's own retry
decides, the engine's ladder does not run), the same tree with no key (the ladder retries in place), a tree with no way left under
``ladder: false``, ``events: true`` (each node is a ``tree_node`` event), and a tree that branches on the choice words of a question of the
robot pack (answered / not answered). The two choice runs also take their trees from the pack's ``trees`` table (scenario key ``pack``).
Made-up trees on the made-up flat, as in s01_trees.py. These are FEATURE goldens: they are recorded on the step's production commit
(tests/golden/commits/s25a.json); a tree with none of the keys runs as before (the bt_goal cassettes of earlier sets are unchanged)."""

import copy

from scenarios import BT_PACK, SHELF, flat

PICK_FAILS_ONCE = [{"skill": "pick", "kind": "fail", "nth": [1], "msg": "grasp failed: it slipped"}]
PICK_FAILS = [{"skill": "pick", "kind": "fail", "nth": "all", "msg": "grasp failed: it slipped"}]


def _fetch(pick, **keys):
    """A made-up ``on`` entry: go to the source, find the object, ``pick`` it, go to the destination, put it down."""
    return {"assumed": True, "tree": [
        "fallback", ["cond", "placed", "{object}", "{to}"],
        ["sequence", ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], pick, ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"],
         ["cond", "placed", "{object}", "{to}"]]], **keys}


def _options(entry):
    pack = copy.deepcopy(BT_PACK)
    pack["bt_goal"]["trees"]["on"] = entry
    return pack


PICK = ["act", "Pick", "{object}"]
FIND_PICK = ["sequence", ["act", "Find", "{object}"], PICK]   # the engine's ladder would look again before a second grasp; under ladder: false the tree does
PICK_TWICE = ["retry", 2, FIND_PICK]
PICK_ONCE = ["retry", 1, FIND_PICK]
# the pack of the choice runs: its trees and its question (the choice ``slow`` has English words)
CHOICE_TREE = {"assumed": True, "tree": [
    "fallback", ["cond", "placed", "{object}", "{to}"],
    ["sequence", ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"],
     ["fallback", ["sequence", ["ask", "q_ask_next_pick", "grip.slow"], PICK], ["cond", "holding", "{object}"]],
     ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"]]]}
CHOICE_PACK = {"schema": 2, "name": "s25a_pack", "extends": "household", "source": "made up for the S25a goldens",
               "trees": {"on": CHOICE_TREE},
               "questions": {"grip": {"kind": None, "assumed": True, "answer": {"type": "choice", "choices": {
                   "slow": {"words": {"en": ["slowly", "gently"]}, "implies": "yes"}}}}}}
MENU_ONLY = {"bt_goal": {"menu": BT_PACK["bt_goal"]["menu"]}}


def _choice(name, answers):
    return dict(flat(name, answers=answers), pack=CHOICE_PACK, pack_options=MENU_ONLY)


SCENARIOS = [
    (dict(flat("bt_ladder_on_retry", faults=PICK_FAILS_ONCE), pack_options=_options(_fetch(PICK))),
     ["bt_goal", "success"], "a tree with no key: Pick fails once, the engine's ladder retries it in place, the tree sees one success"),
    (dict(flat("bt_ladder_off", faults=PICK_FAILS_ONCE), pack_options=_options(_fetch(PICK_TWICE, ladder=False))),
     ["bt_goal", "success"], "ladder: false: Pick fails once, no ladder runs, the tree's own retry looks again and picks again"),
    (dict(flat("bt_ladder_off_no_way_left", faults=PICK_FAILS, answers=[""]),
          pack_options=_options(_fetch(PICK_ONCE, ladder=False))),
     ["bt_goal", "failure"], "ladder: false and a tree with one try: Pick fails, no ladder runs, the tree has no way left: the menu "
     "question (silence), then the run stops"),
    (dict(flat("bt_node_events", objects={"gizmo": SHELF}, options={"trace": "1"}), pack_options=_options(_fetch(PICK, events=True)), capture=["trace"]),
     ["bt_goal", "success"], "events: true: every node of the tree is also a tree_node event of the run, after the usual events"),
    (_choice("bt_choice_answer", ["slowly please"]),
     ["bt_goal", "question", "success"], "the trees and the question come from the robot pack: the answer holds a word of the choice slow, "
     "the tree goes on to Pick"),
    (_choice("bt_choice_other_answer", ["whatever", ""]),
     ["bt_goal", "question", "failure"], "the answer holds no word of the choice slow: the tree's fallback finds the hand empty, the menu "
     "question (silence), the run stops"),
]
