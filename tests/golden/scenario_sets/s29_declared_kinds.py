"""S29 goldens (feature cassettes, recorded on the step's production commit): a goal kind declared with no rules callable.

A made-up pack on ``fakeline`` declares the kind ``mount`` (object + station) with no ``rules``: its ``holds`` list is
``placed(?object, ?to)`` and ``fastened(?object)`` (a fact that the declared effect of Fasten adds), its ``observe`` verifier is
the standard end-of-run look (go to the station, Find the part), ``unknown_is: failure`` and the phrase key ``rb_mount``.
Done by the holds list and the verifier; the verifier cannot tell (the camera fails at the end check) -> the goal is unmet
with ``perception_unavailable`` and the run fails; a plan without Fasten is refuted at the gate (one line per predicate);
the read-back says the pack's phrase. Every fact here is made up (assumed)."""

from scenario_sets.s01_kinds import BIN, JIG, line

PACK = {"schema": 2, "name": "s29_line_mount", "extends": "household", "assumed": True,
        "predicates": {"fastened": {"kind": "fact", "args": ["object"], "scopes": ["effect", "goal"], "assumed": True}},
        "actions": {"Fasten": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                               "effects": {"add": ["fastened(?part)"]}, "assumed": True}},
        "goal_kinds": {"mount": {"needs": ["Pick", "Place", "Fasten"], "understood": "capable", "planned_by": "planner",
                                 "holds": ["placed(?object, ?to)", "fastened(?object)"], "observe": "std.find_at_destination",
                                 "unknown_is": "failure", "phrase": "rb_mount", "conflicts": "none",
                                 "prompt": '"mount" = fasten a part at a station: object = the part, from "", to = the station.',
                                 "assumed": True}},
        "phrases": {"rb_mount": {"slots": ["object", "place"], "en": "mount the {object} on the {place}", "assumed": True}}}
GOAL = [("mount", "bracket", "", "jig")]
PLAN = ["MoveTo " + BIN, "Find bracket", "Pick bracket", "MoveTo " + JIG, "Fasten bracket", "Place " + JIG]
NO_FASTEN = [s for s in PLAN if not s.startswith("Fasten")]
CAMERA_DOWN = [{"skill": "find", "obj": "bracket", "nth": 2, "kind": "fail", "msg": "camera stream lost"}]
TREE = {"bt_goal": {"trees": {"mount": {"tree": ["sequence", ["act", "Find", "{object}"], ["act", "Pick", "{object}"],   # no Fasten
                                                 ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"]],
                                        "assumed": True}}}}

SCENARIOS = [
    (line("s29_done_by_holds_and_verifier", "Mount the bracket on the jig.", GOAL, plan=PLAN, pack=PACK),
     ["success", "fakeline"], "a mount goal (no rules callable): the plan reaches it by simulation (Place, and Fasten's "
     "declared effect), the holds list is true in the belief at the end, the end check's Find sees the part: success"),
    (line("s29_verifier_cannot_tell_fails", "Mount the bracket on the jig.", GOAL, plan=PLAN, pack=PACK,
          robot={"faults": CAMERA_DOWN}),
     ["failure", "fakeline"], "the holds list is true in the belief, but the end check cannot look (the camera fails at "
     "its Find): unknown_is failure makes the goal unmet with perception_unavailable, no new plan, the run fails"),
    (line("s29_plan_refuted_at_gate", "Mount the bracket on the jig.", GOAL, plan=NO_FASTEN, pack=PACK),
     ["gate", "failure", "fakeline"], "a plan with no Fasten leaves fastened(bracket) false after the steps: the gate says "
     "the goal is not reached (one line per refuted predicate), the plan is not certified and nothing moves"),
    (line("s29_readback_phrase_key", "Mount the bracket on the jig.", GOAL, plan=PLAN, pack=PACK,
          options={"confirm_task": True}, robot={"answers": ["yes"]}),
     ["question", "success", "fakeline"], "the read-back of a mount goal says the pack's phrase rb_mount ('mount the "
     "bracket on the jig in the cell a'), answered yes, then the run as in s29_done_by_holds_and_verifier"),
    (line("s29_tree_leaves_holds_false", "Mount the bracket on the jig.", GOAL, options={"bt_goal": "1"}, pack=PACK,
          pack_options=TREE),
     ["bt_goal", "failure", "fakeline"], "a tree (not gated) places the bracket but never fastens it: at the plan's end "
     "fastened(bracket) is false in the belief, so the goal does not hold and the run does not say done"),
]
