"""S25a goldens, the tree keys of bt_goal (feature cassettes, recorded on the step's own code): ``ladder: false`` (a failed step of the
tree does not run the engine's recovery ladder: the tree's ``retry`` decides), ``events: true`` (every node is a public ``tree_node``
event) and a tree that branches on a choice answer read with the words of a question of the robot's pack (scenario key ``pack``).
Made-up trees on the made-up flat of scenarios.py (the ``fakebot`` robot); the same run with no key is the cassette ``bt_go_tree`` /
``menu_retry_place`` family, unchanged."""

import copy

from scenarios import BT_ON, BT_PACK, CRATE, SHELF, flat

# fetch the gizmo with a retry around the Pick, then put it on the bench
RETRY_ON = ["sequence", ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["retry", 2, ["act", "Pick", "{object}"]],
            ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"]]
# the same, but a failed Pick is followed by a new Find inside the retry (the engine's guard wants a Find before a Pick)
REFIND_ON = ["sequence", ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"],
             ["fallback", ["act", "Pick", "{object}"], ["sequence", ["act", "Find", "{object}"], ["act", "Pick", "{object}"]]],
             ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"]]
# take the detour over the crate unless the person chose the shortcut (the choice ``shortcut`` of the pack question ``route``)
ROUTE_ON = ["sequence", ["fallback", ["ask", "q_ask_next_move", "route.shortcut"], ["act", "MoveTo", CRATE]],
            ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["act", "Pick", "{object}"], ["act", "MoveTo", "{to}"],
            ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"]]
ROUTE_PACK = {"schema": 2, "name": "route_s25a", "assumed": True,
              "questions": {"route": {"phrase": None, "kind": "route",
                                      "answer": {"type": "choice", "choices": {"shortcut": {"words": {"en": ["shortcut", "direct"]}}}}}}}


def _pack(tree, **keys):
    pack = copy.deepcopy(BT_PACK)
    pack["bt_goal"]["trees"]["on"] = {"tree": tree, "skip_if": ["hand"], "assumed": True, **keys}
    return pack


PICK_FAILS_ONCE = {"faults": [{"skill": "pick", "kind": "fail", "nth": 1}]}


def _run(name, pack_options, robot=None, **extra):
    sc = flat(name, objects={"gizmo": SHELF}, pack_options=pack_options, **extra)
    sc["robot"] = {**sc["robot"], **(robot or {})}
    return sc


SCENARIOS = [
    (_run("bt_ladder_off", _pack(RETRY_ON, ladder=False), PICK_FAILS_ONCE),
     ["bt_goal", "failure", "recovery"], "a tree with `ladder: false`: the first Pick fails and the engine's ladder does not run; the "
     "tree's retry runs the Pick again, but the engine's guard wants a Find first, so the tree has no way left and the run stops"),
    (_run("bt_ladder_off_refind", _pack(REFIND_ON, ladder=False), PICK_FAILS_ONCE),
     ["bt_goal", "success", "recovery"], "a tree with `ladder: false` whose own fallback looks again and picks again: the recovery "
     "is written in the tree, no `recover` event of the engine"),
    (_run("bt_ladder_on_retry", _pack(RETRY_ON), PICK_FAILS_ONCE),
     ["bt_goal", "success", "recovery"], "the same tree with no key: the engine's own ladder retries the Pick inside the Act (the "
     "default, as today)"),
    (_run("bt_node_events", _pack(BT_ON, events=True)),
     ["bt_goal", "success", "events"], "a tree with `events: true`: every node of the tree is a `tree_node` event, in order"),
    (_run("bt_choice_answer", _pack(ROUTE_ON), {"answers": ["the direct way please"]}, pack=ROUTE_PACK),
     ["bt_goal", "question", "success"], "a tree that asks which way and branches on the choice `shortcut` of a pack question: the "
     "answer names it, so the detour over the crate is skipped"),
    (_run("bt_choice_other_answer", _pack(ROUTE_ON), {"answers": ["whatever you like"]}, pack=ROUTE_PACK),
     ["bt_goal", "question", "success"], "the same tree, an answer that names no choice: the tree takes the detour over the crate"),
]
