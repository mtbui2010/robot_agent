"""S25 goldens (feature cassettes, recorded on the step's production commit): typed ``act`` arguments in a tree and ``[ask, <pack
question>]`` with the answer kept as a fact.

On ``fakehome`` a pack declares the typed argument ``mode`` of MoveTo (the move skill's own params declare it too, as in the S26
cassette) and a tree gives its two MoveTo leaves ``{mode: ...}``: the arguments reach the skill, and a value the pack does not allow
fails the step (nothing moves). On ``fakebot`` (the made-up flat of scenarios.py) a pack declares the question ``route`` (a phrase, the
choice ``shortcut`` with ``keep_text``) and the tree asks it with ``[ask, route]``, then branches on ``cond answered route shortcut``:
an answer that names the choice skips the detour over the crate, any other answer takes the detour, and the facts (``answered``,
``answer_text``) show in ``world.facts`` of the events. Every fact here is made up (assumed)."""

import copy

from scenarios import BENCH, BT_PACK, CRATE, SHELF, flat, understood
from scenario_sets.s26_action_rules import HOME_PACK, DK, FLAT as HOME_FLAT, KC, PINNED

# ── typed arguments (fakehome) ──────────────────────────────────────────────────────────────────────────────────────────────────
HOME_PLAN = ["MoveTo " + KC, "Find mug", "Pick mug", "MoveTo " + DK, "Place " + DK]


def _home_tree(mode_from, mode_to):
    return {"bt_goal": {"trees": {"on": {"tree": [
        "sequence", ["act", "MoveTo", KC, {"mode": mode_from}], ["act", "Find", "{object}"], ["act", "Pick", "{object}"],
        ["act", "MoveTo", "{to}", {"mode": mode_to}], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"]],
        "skip_if": ["hand"], "assumed": True}}}}


def _home(name, tree):
    return {"name": name, "env": HOME_FLAT, "robot_pkg": "fakehome", "task": "Put the mug on the desk.",
            "robot": {"objects": {"mug": KC}, "start": DK},
            "llm": {"script": [], "default": understood(("on", "mug", "counter", "desk"))},
            "planner": {"plan": HOME_PLAN, "replans": []},
            "options": dict(PINNED, bt_goal="1", search_legs=1, ask_help=False, detector_miss_rate=0, health_wait_s=0),
            "pack": HOME_PACK, "pack_options": tree}


# ── a question of the pack, answered by a person (fakebot) ─────────────────────────────────────────────────────────────────────────
# the robot says ``q_where_go`` itself (its phrase files); the pack declares the key for its question (the pack check wants a phrase of the
# robot's language, fakebot's manifest language is ko)
ROUTE_PACK = {"schema": 2, "name": "route_s25", "assumed": True,
              "phrases": {"q_where_go": {"en": "Where should I go?", "ko": "어디로 갈까요?", "assumed": True}},
              "questions": {"route": {"phrase": "q_where_go", "kind": "route",
                                      "answer": {"type": "choice", "keep_text": True,
                                                 "choices": {"shortcut": {"words": {"en": ["shortcut", "direct way"]}},
                                                             "detour": {"words": {"en": ["detour", "crate"]}}}}}}}
# take the detour over the crate unless the person chose the shortcut: the branch reads the fact the ask leaf kept
ROUTE_TREE = ["sequence", ["ask", "route"], ["fallback", ["cond", "answered", "route", "shortcut"], ["act", "MoveTo", CRATE]],
              ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["act", "Pick", "{object}"], ["act", "MoveTo", "{to}"],
              ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"]]


def _route(name, answer):
    pack = copy.deepcopy(BT_PACK)
    pack["bt_goal"]["trees"]["on"] = {"tree": ROUTE_TREE, "skip_if": ["hand"], "assumed": True}
    sc = flat(name, objects={"gizmo": SHELF}, pack_options=pack, answers=[answer], pack=ROUTE_PACK)
    return sc


SCENARIOS = [
    (_home("s25_typed_arg_reaches_skill", _home_tree("left", "right")),
     ["bt_goal", "success", "fakehome"], "a tree gives each MoveTo leaf a typed argument `mode`: the step carries `args`, they reach the "
     "move skill as its param `mode` (left to the counter, right to the desk)"),
    (_home("s25_typed_arg_wrong_value", _home_tree("up", "right")),
     ["bt_goal", "failure", "fakehome"], "the same tree with a `mode` the pack does not allow (enum front / left / right): the step is "
     "refused when it is bound, nothing moves and the run stops"),
    (_route("s25_branch_on_answer_shortcut", "Take the direct way, please."),
     ["bt_goal", "question", "success"], "`[ask, route]` puts the pack question with its kind, the answer names the choice `shortcut` "
     "(a phrase of two words): the fact answered(route, shortcut) and the raw text are kept, the branch skips the detour over the crate"),
    (_route("s25_branch_on_answer_detour", "Go via the crate then."),
     ["bt_goal", "question", "success"], "the same tree, an answer that names the choice `detour`: answered(route, detour) is kept and "
     "the tree takes the detour over the crate"),
    (_route("s25_branch_on_answer_both", "The direct way, not via the crate."),
     ["bt_goal", "question", "success"], "an answer that says the words of two choices: the first choice in the declaration order "
     "(`shortcut`) is the one read, so the detour is skipped"),
    (_route("s25_branch_on_answer_unclear", "Hmm, I do not know."),
     ["bt_goal", "question", "failure"], "the same tree, an answer that names no choice: the ask leaf fails (no fact answered, the raw text "
     "is kept) and the tree has no way left"),
]
