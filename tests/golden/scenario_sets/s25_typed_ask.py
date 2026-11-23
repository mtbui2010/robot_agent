"""S25 goldens (feature cassettes, recorded on the step's production commit): typed ``act`` arguments in a bt_goal tree, and
``[ask, <pack question>]`` with the answer kept as a fact.

Typed arguments (``fakehome``): a pack declares the argument ``mode`` of MoveTo (the move skill's own params declare it too, as in the S26
cassette) and a tree gives its two MoveTo leaves ``{mode: ...}``: the arguments reach the skill, and a value the pack does not allow
fails the step (nothing moves).

Pack question (``fakeline``, the made-up assembly line): a pack declares the question ``check`` (a phrase of the core, the choices ``inspect`` and
``skip`` with words, ``keep_text``); the tree asks it with ``[ask, check]`` and branches on ``[cond, answered, check, inspect]``: an answer
that names ``inspect`` runs the Inspect skill before the part is moved, one that names ``skip`` does not, an answer that names neither
fails the leaf and the tree has no way left. The facts (``answered``, ``answer_text``) show in the events' ``world.facts``. Every fact
here is made up (assumed)."""

import copy

from scenarios import understood
from scenario_sets.s01_kinds import BIN, JIG, line
from scenario_sets.s26_action_rules import DK, FLAT as HOME_FLAT, HOME_PACK, KC, PINNED

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


# ── a question of the pack, answered by a person (fakeline) ─────────────────────────────────────────────────────────────────────────
# the question says a phrase the robot already has (the core's English phrase q_what_exactly: "What should I do exactly?"); a phrase the
# pack declares itself is not said by the engine yet (speech.phrase reads the robot's i18n, the language pack and the core)
CHECK_PACK = {"schema": 2, "name": "s25_line_check", "extends": "household", "assumed": True,
              "questions": {"check": {"phrase": "q_what_exactly", "kind": "check",
                                      "answer": {"type": "choice", "keep_text": True,
                                                 "choices": {"inspect": {"words": {"en": ["inspect", "look it over"]}},
                                                             "skip": {"words": {"en": ["skip", "no need"]}}}}}}}
GOAL = [("on", "bracket", "bin", "jig")]
# ask first; inspect only when the person chose it (skip: nothing); then the usual fetch and put
CHECK_TREE = ["sequence", ["ask", "check"],
              ["fallback", ["sequence", ["cond", "answered", "check", "inspect"], ["act", "Inspect", "{object}"]],
               ["cond", "answered", "check", "skip"]],
              ["act", "Find", "{object}"], ["act", "Pick", "{object}"], ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"],
              ["cond", "placed", "{object}", "{to}"]]
CHECK_OPTIONS = {"bt_goal": {"trees": {"on": {"tree": CHECK_TREE, "skip_if": ["hand"], "assumed": True}}}}


def _check(name, answer):
    return line(name, "Put the bracket on the jig.", GOAL, options={"bt_goal": "1"}, robot={"answers": [answer]},
                pack=copy.deepcopy(CHECK_PACK), pack_options=copy.deepcopy(CHECK_OPTIONS))


SCENARIOS = [
    (_home("s25_typed_arg_reaches_skill", _home_tree("left", "right")),
     ["bt_goal", "success", "fakehome"], "a tree gives each MoveTo leaf a typed argument `mode`: the step carries `args`, they reach the "
     "move skill as its param `mode` (left to the counter, right to the desk)"),
    (_home("s25_typed_arg_wrong_value", _home_tree("up", "right")),
     ["bt_goal", "failure", "fakehome"], "the same tree with a `mode` the pack does not allow (enum front / left / right): the step is "
     "refused when it is bound, nothing moves and the run stops"),
    (_check("s25_branch_on_answer_inspect", "Yes, look it over first."),
     ["bt_goal", "question", "success", "fakeline"], "`[ask, check]` puts the pack question with its kind; the answer names the choice "
     "`inspect` (a phrase of three words): answered(check, inspect) and the raw text are kept and the tree runs Inspect"),
    (_check("s25_branch_on_answer_skip", "No need, just move it."),
     ["bt_goal", "question", "success", "fakeline"], "the same tree, an answer that names the choice `skip`: answered(check, skip) is "
     "kept and the tree never calls the Inspect skill"),
    (_check("s25_branch_on_answer_both", "Skip it, or rather inspect it."),
     ["bt_goal", "question", "success", "fakeline"], "an answer that says the words of two choices: the first choice in the declaration "
     "order (`inspect`) is the one read, so Inspect runs"),
    (_check("s25_branch_on_answer_unclear", "Hmm, I do not know."),
     ["bt_goal", "question", "failure", "fakeline"], "the same tree, an answer that names no choice: the ask leaf fails (no fact answered, "
     "the raw text is kept) and the tree has no way left"),
]
