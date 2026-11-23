"""S26 goldens (feature cassettes, recorded on the step's production commit): the declared rules of pack actions at work.

A made-up pack on ``fakeline`` declares the fact ``inspected(object)``, the line action Inspect with the effect
``inspected(?part)`` and Fasten with the precondition ``inspected(?part)`` (both ``rules: declared``). With no Inspect in the
plan the gate finds ``pre_inspected`` and nothing moves; a declared tree (not gated) that runs Fasten with no Inspect meets
the pre-step guard; with an Inspect first, its effect makes the fact true (``world.facts`` of the events after it) and the run
succeeds. On ``fakehome`` a pack declares the typed argument ``mode`` of MoveTo, which the move skill's params declare: the
plan steps' ``args`` reach the skill. Every fact here is made up (assumed)."""

from scenarios import understood
from scenario_sets.s01_kinds import BIN, JIG, line

LINE_PACK = {"schema": 2, "name": "s26_line", "extends": "household", "assumed": True,
             "predicates": {"inspected": {"kind": "fact", "args": ["object"], "scopes": ["effect", "pre"]}},
             "actions": {"Inspect": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                                     "effects": {"add": ["inspected(?part)"]}},
                         "Fasten": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                                    "pre": ["inspected(?part)"]}}}
GOAL = [("on", "bracket", "bin", "jig")]
NO_INSPECT = ["MoveTo " + BIN, "Find bracket", "Pick bracket", "MoveTo " + JIG, "Fasten bracket", "Place " + JIG]
WITH_INSPECT = ["MoveTo " + BIN, "Find bracket", "Pick bracket", "MoveTo " + JIG, "Inspect bracket", "Fasten bracket",
                "Place " + JIG]
TREE = {"bt_goal": {"trees": {"on": {"assumed": True,
                                     "tree": ["sequence", ["act", "Find", "{object}"], ["act", "Pick", "{object}"],
                                              ["act", "MoveTo", "{to}"], ["act", "Fasten", "{object}"],
                                              ["act", "Place", "{to}"]]}}}}

# fakehome (tests/fakehome): its move skill's params declare `mode`; the pack's MoveTo is the standard entry plus `args`
KC, DK = "counter@kitchen", "desk@study"
FLAT = {KC: {"aliases": ["counter"], "loc": {"x": 6.0, "y": 1.0}, "surface": True},
        DK: {"aliases": ["desk"], "loc": {"x": 0.5, "y": 6.0}, "surface": True}}
HOME_PACK = {"schema": 2, "name": "s26_home", "extends": "household", "assumed": True,
             "actions": {"MoveTo": {"class": "move", "arg": {"name": "place", "type": "place"}, "verb": "move to",
                                    "ability": "move to a place", "describe": "move to a place",
                                    "args": {"mode": {"type": "enum", "values": ["front", "left", "right"]}}}}}
PINNED = dict(input_pivot="none", look_then_ask="0", bring_ask="0", bt_goal="0", fetch_guard="0")

SCENARIOS = [
    (line("s26_pre_fails_at_gate", "Put the bracket on the jig and fasten it.", GOAL, plan=NO_INSPECT, pack=LINE_PACK),
     ["failure", "fakeline"], "Fasten declares the precondition inspected(?part) and the plan has no Inspect: the gate finds "
     "pre_inspected in every plan the planner gives, the plan is not certified and nothing moves"),
    (line("s26_pre_fails_at_guard", "Put the bracket on the jig and fasten it.", GOAL, options={"bt_goal": "1"},
          pack=LINE_PACK, pack_options=TREE),
     ["bt_goal", "failure", "fakeline"], "a declared tree runs Fasten with no Inspect (a tree is not gated): the pre-step guard "
     "finds pre_inspected before Fasten moves"),
    (line("s26_effect_makes_fact_true", "Put the bracket on the jig, inspect it and fasten it.", GOAL, plan=WITH_INSPECT,
          pack=LINE_PACK),
     ["success", "fakeline"], "Inspect's declared effect makes inspected(bracket) true, in the gate's simulation and in the "
     "belief (world.facts of the events after it), so Fasten passes the gate and the guard and the run succeeds"),
    (line("s26_stale_fact_cleared", "Put the bracket on the jig and fasten it.", GOAL, plan=NO_INSPECT, pack=LINE_PACK,
          world={"facts": {"inspected": [["bracket"]]}}),
     ["failure", "fakeline"], "the belief holds inspected(bracket) from before the run (an edit or an earlier run): the run start "
     "clears the facts, so the plan with no Inspect still fails at the gate, as in s26_pre_fails_at_gate"),
    ({"name": "s26_typed_args_reach_skill", "env": FLAT, "robot_pkg": "fakehome", "task": "Put the mug on the desk.",
      "robot": {"objects": {"mug": KC}, "start": DK},
      "llm": {"script": [], "default": understood(("on", "mug", "counter", "desk"))},
      "planner": {"plan": [{"action": "MoveTo", "object": KC, "args": {"mode": "left"}}, "Find mug", "Pick mug",
                           {"action": "MoveTo", "object": DK, "args": {"mode": "right"}}, "Place " + DK], "replans": []},
      "options": dict(PINNED, search_legs=1, ask_help=False, detector_miss_rate=0), "pack": HOME_PACK},
     ["success", "fakehome"], "the pack declares MoveTo's typed argument mode (enum front / left / right); each MoveTo step's "
     "args reach the move skill as its param mode (the skill's own params declare it)"),
]
