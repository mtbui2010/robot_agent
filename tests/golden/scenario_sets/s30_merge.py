"""S30 goldens (feature cassettes, recorded on the commit the step starts from: the step adds no production code, the merge of
``extends`` and ``remove`` existed since S02 and is proven here to take effect in a RUN): a pack that extends the standard pack,
removes the goal kinds ``open`` and ``close`` and replaces the kind ``say`` whole (its own prompt line).

* ``s30_remove_replace``: the run of the cassette ``template_say_deliver`` (fakeline) with that pack. The run itself is the same
  (the goals, the steps, the events); the understand call differs: its prompt by the replaced ``say`` line, its JSON format by
  the two enum values that are gone (tests/test_pack_merge.py pins both differences line by line).
* ``s30_removed_kind_refused``: the run of the cassette ``open_goal`` (fakebot) with a pack that only removes ``open`` and
  ``close`` (fakebot has no Say, so it cannot replace ``say``): the model still says ``open`` (the
  scripted answer), the kind is no longer in the pack's table, so the goal needs an action nobody has and the run is refused
  before any plan is made.
"""

from scenarios import DR, house, understood

# the made-up assembly line of ``fakeline`` (tests/fakeline), as in s01_kinds.py
BIN, JIG, OUT = "bin@cell a", "jig@cell a", "outfeed@cell b"
LINE = {BIN: {"aliases": ["bin"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
        JIG: {"aliases": ["jig"], "loc": {"x": 2.0, "y": 1.0}, "surface": True},
        OUT: {"aliases": ["outfeed"], "loc": {"x": 6.0, "y": 3.0}, "surface": True}}
LINE_SKILLS = {"say": {"isdone": True, "msg": "said"}, "wait_for_unload": {"isdone": True, "msg": "tray empty"},
               "inspect": {"isdone": True, "msg": "inspected"}, "fasten": {"isdone": True, "msg": "fastened"}}
PINNED = dict(input_pivot="none", look_then_ask="0", bring_ask="0", bt_goal="0", fetch_guard="0")

SAY_LINE = ('"say" = pass a message on: object = the message in the user\'s words; from ""; '
            'to = the place or the person it is for, or "".')
PACK = {"schema": 2, "name": "s30_merge", "extends": "household", "assumed": True,
        "remove": {"goal_kinds": ["open", "close"]},
        "goal_kinds": {"say": {"rules": "std.say", "needs": ["Say"], "understood": "capable", "planned_by": "template",
                               "conflicts": "default", "prompt": SAY_LINE,
                               "subgoal": '{who?}say "{object}" at {who}|say "{object}"'}}}

REMOVE_ONLY = {"schema": 2, "name": "s30_remove", "extends": "household", "assumed": True,
               "remove": {"goal_kinds": ["open", "close"]}}

SCENARIOS = [
    ({"name": "s30_remove_replace", "env": LINE, "robot_pkg": "fakeline",
      "task": "Tell the operator the batch is ready at the jig, then deliver the bracket to the outfeed.",
      "robot": {"objects": {"bracket": BIN, "bolt": JIG}, "start": BIN, "skills": dict(LINE_SKILLS)},
      "llm": {"script": [], "default": understood(("say", "The batch is ready.", "", "jig"), ("deliver", "bracket", "", "outfeed"))},
      "planner": {"plan": [], "replans": []}, "options": dict(PINNED), "capture": ["prompts"], "pack": PACK},
     ["success", "template", "fakeline"], "the say / deliver run of template_say_deliver with a pack that removes open and close "
     "and replaces say: the same run; the understand prompt differs by the say line, the format by the two enum values"),
    (dict(house("s30_removed_kind_refused", "Open the kitchen drawer.", [("open", "kitchen drawer", "", "")],
                plan=["MoveTo " + DR, "Open " + DR]), pack=REMOVE_ONLY),
     ["refused", "failure"], "an open goal with a pack that removed the kinds open and close: the goal needs an action nobody has, the run is "
     "refused before any plan"),
]
