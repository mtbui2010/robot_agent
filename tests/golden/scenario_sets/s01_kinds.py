"""S01 goldens, goal kinds (needed by S05, S07, S08, S12): the template kinds (go, say, deliver), open / close goals, the
read-back of non-``on`` kinds, a hand-over to a person (``fakehome``), and a caller's goal of a kind no table knows (today:
vacuously done). Recorded on the old code; see tests/golden/scenario_sets/__init__.py."""

from scenarios import CT, DR, DS, DT, house, understood

LR = "sofa@living room"
# the made-up assembly line of ``fakeline`` (tests/fakeline): stations, parts
BIN, JIG, OUT, TOTE = "bin@cell a", "jig@cell a", "outfeed@cell b", "tote@cell b"
LINE = {BIN: {"aliases": ["bin"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
        JIG: {"aliases": ["jig"], "loc": {"x": 2.0, "y": 1.0}, "surface": True},
        OUT: {"aliases": ["outfeed"], "loc": {"x": 6.0, "y": 3.0}, "surface": True}}
LINE_SKILLS = {"say": {"isdone": True, "msg": "said"}, "wait_for_unload": {"isdone": True, "msg": "tray empty"},
               "inspect": {"isdone": True, "msg": "inspected"}, "fasten": {"isdone": True, "msg": "fastened"}}
# a made-up flat with a person entry (the shape of tests/test_sage_lab_r15_reach_delivery.py): lena, no surface beside her
KC, DK, ME = "counter@kitchen", "desk@study", "spot near you"
PFLAT = {KC: {"aliases": ["counter"], "loc": {"x": 6.0, "y": 1.0}, "surface": True},
         DK: {"aliases": ["desk"], "loc": {"x": 0.5, "y": 6.0}, "surface": True},
         ME: {"person": "lena", "speaker": True, "aliases": ["me", "lena"], "loc": {"x": 0.5, "y": 6.0}, "seat": DK,
             "spot": DK, "surface": False}}
PINNED_LINE = dict(input_pivot="none", look_then_ask="0", bring_ask="0", bt_goal="0", fetch_guard="0")


def line(name, task, goals, plan=(), replans=(), options=None, robot=None, llm_script=(), **extra):
    """A ``fakeline`` run on the made-up line (stations bin, jig, outfeed; parts bracket, bolt)."""
    rb = {"objects": {"bracket": BIN, "bolt": JIG}, "start": BIN, "skills": dict(LINE_SKILLS)}
    rb.update(robot or {})
    sc = {"name": name, "env": LINE, "robot_pkg": "fakeline", "task": task, "robot": rb,
          "llm": {"script": list(llm_script), "default": understood(*goals) if goals else None},
          "planner": {"plan": list(plan), "replans": [list(r) for r in replans]},
          "options": dict(PINNED_LINE, **(options or {}))}
    sc.update(extra)
    return sc


SCENARIOS = [
    (house("template_go", "Go to the dining table.", [("go", "", "", "dining table")], plan=[], capture=["prompts"]),
     ["success", "template"], "a go goal: compiled to one MoveTo without the planner; goals.reached on the last go"),
    (line("template_say_deliver", "Tell the operator the batch is ready at the jig, then deliver the bracket to the outfeed.",
          [("say", "The batch is ready.", "", "jig"), ("deliver", "bracket", "", "outfeed")], capture=["prompts"]),
     ["success", "template", "fakeline"], "say and deliver goals on fakeline: the say / deliver prompt lines, Say, the "
     "delivery phrase, WaitForUnload"),
    (house("close_goal", "Close the kitchen drawer.", [("close", "kitchen drawer", "", "")],
           plan=["MoveTo " + DR, "Close " + DR], robot={"opened": [DR]}, world={"opened": [DR]}),
     ["success"], "a close goal: the container grounded, MoveTo and Close, the drawer ends closed"),
    (house("open_goal", "Open the kitchen drawer.", [("open", "kitchen drawer", "", "")], plan=["MoveTo " + DR, "Open " + DR]),
     ["success"], "an open goal: the container grounded, MoveTo and Open, the drawer ends open"),
    (house("readback_kinds", "Put the orange in the kitchen drawer, open it and then go to the sofa.",
           [("in", "orange", "", "kitchen drawer"), ("open", "kitchen drawer", "", ""), ("go", "", "", "sofa")],
           plan=["MoveTo " + DR, "Open " + DR, "MoveTo " + DT, "Find orange", "Pick orange", "MoveTo " + DR, "PutIn " + DR,
                 "MoveTo " + LR],
           options={"confirm_task": True}, robot={"answers": ["yes"]}),
     ["question", "success"], "the read-back of an in, an open and a go goal (the phrase of each kind), answered yes"),
    ({"name": "handover", "env": PFLAT, "robot_pkg": "fakehome", "task": "Bring me the mug.",
      "robot": {"objects": {"mug": KC}, "start": KC, "skills": {"handover": {"isdone": True, "msg": "handed",
                                                                         "release_to": "person:lena"}}},
      "llm": {"script": [understood(("on", "mug", KC, "me"))], "default": "{}"},
      "planner": {"plan": ["MoveTo " + KC, "Find mug", "Pick mug", "MoveTo " + ME, "Handover " + ME]},
      "options": dict(PINNED_LINE, search_legs=1, ask_help=False, detector_miss_rate=0), "capture": ["prompts"]},
     ["success", "fakehome"], "a hand-over to the person on fakehome: the person's ENV entry (no surface), the Handover "
     "release action, the goal check of a person destination"),
    (house("given_goals_unknown_kind", "polish the apple", None, plan=["MoveTo desk@study room", "Find apple"],
           options={"accept_goals": "1"},
           goals=[{"type": "polish", "object": "apple", "from": "desk@study room", "to": ""}]),
     ["failure", "given_goals"], "a caller's goal of a kind no table knows: today it is vacuously reached (the gate says the "
     "goals are done before the first step; the plan is not certified, nothing moves)"),
    (house("on_wrong_place_gate", "Bring the apple from the desk to the dining table.", [("on", "apple", "desk", "dining table")],
           plan=["MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + CT, "Place " + CT]),
     ["gate", "failure"], "every plan puts the apple on the counter, not on the dining table: the gate's findings name the "
     "wrong Place and the missing one; not certified, nothing moves"),
    (line("in_goal_armless_refused", "Put the bracket in the tote.", [("in", "bracket", "bin", "tote")],
          robot_pkg="fakeline.cart", env=dict(LINE, **{TOTE: {"aliases": ["tote"], "loc": {"x": 6.5, "y": 3.0}, "openable": True}})),
     ["refused", "failure", "fakeline"], "an in goal on the armless cart of the line: refused before any plan, with the "
     "actions it lacks (the in kind's needs)"),
]
