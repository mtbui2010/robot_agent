"""S30 golden (a feature cassette, recorded on the step's code): the merge semantics of ``extends`` and ``remove`` in a run. The
run of the cassette ``template_say_deliver`` (a say and a deliver goal on ``fakeline``) with a pack that extends the standard
pack, removes the kinds ``open`` and ``close`` and replaces the kind ``deliver`` by an entry with no ``prompt``: the run log is
the same (the goals are still done by their own rules), but the understand prompt has no ``deliver`` line and the schema enum
of the goal types has no ``open`` / ``close`` (the prompt and format hashes differ from the base cassette, the prompt head does
not). A replaced entry is replaced whole: the base entry's prompt does not come back."""

from scenarios import understood

BIN, JIG, OUT = "bin@cell a", "jig@cell a", "outfeed@cell b"
LINE = {BIN: {"aliases": ["bin"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
        JIG: {"aliases": ["jig"], "loc": {"x": 2.0, "y": 1.0}, "surface": True},
        OUT: {"aliases": ["outfeed"], "loc": {"x": 6.0, "y": 3.0}, "surface": True}}
SKILLS = {"say": {"isdone": True, "msg": "said"}, "wait_for_unload": {"isdone": True, "msg": "tray empty"},
          "inspect": {"isdone": True, "msg": "inspected"}, "fasten": {"isdone": True, "msg": "fastened"}}
PINNED = dict(input_pivot="none", look_then_ask="0", bring_ask="0", bt_goal="0", fetch_guard="0")
PACK = {"schema": 2, "name": "s30_merge", "extends": "household", "assumed": True,
        "remove": {"goal_kinds": ["open", "close"]},
        "goal_kinds": {"deliver": {"rules": "std.deliver", "needs": ["MoveTo", "WaitForUnload"], "understood": "capable",
                                   "planned_by": "template", "conflicts": "default", "subgoal": "deliver to {to}",
                                   "assumed": True}}}

SCENARIOS = [
    ({"name": "s30_remove_replace", "env": LINE, "robot_pkg": "fakeline",
      "task": "Tell the operator the batch is ready at the jig, then deliver the bracket to the outfeed.",
      "robot": {"objects": {"bracket": BIN, "bolt": JIG}, "start": BIN, "skills": dict(SKILLS)},
      "llm": {"script": [], "default": understood(("say", "The batch is ready.", "", "jig"), ("deliver", "bracket", "", "outfeed"))},
      "planner": {"plan": [], "replans": []}, "options": dict(PINNED), "capture": ["prompts"], "pack": PACK},
     ["success", "template", "fakeline"], "the pack removes open and close and replaces deliver (no prompt line): the run log is as "
     "in template_say_deliver; the understand prompt loses the deliver line and the schema enum loses open and close"),
]
