"""S29 goldens (feature cassettes, recorded on the step's production commit): a goal kind declared with no rules callable.

A made-up pack on ``fakeline`` declares the kind ``mount`` with a ``holds`` list (``placed(?object, ?to)`` and the fact
``fastened(?object)``, which the declared action Fasten adds), the goal verifier ``std.find_at_destination``,
``unknown_is: failure`` and the phrase key ``rb_mount``. The plan that places and fastens the bracket is reached by
simulation and the run is done by the list and the verifier; when the verifier cannot look (the camera is down at the end
check) the goal is unmet with the code perception_unavailable and the run fails; the read-back says the phrase of the key.
Every fact here is made up (assumed)."""

from scenarios import understood
from scenario_sets.s01_kinds import BIN, JIG, line

PACK = {"schema": 2, "name": "s29_line_mount", "extends": "household", "assumed": True,
        "predicates": {"fastened": {"kind": "fact", "args": ["object"], "scopes": ["effect", "pre", "goal"], "assumed": True}},
        "actions": {"Fasten": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                               "effects": {"add": ["fastened(?part)"]}, "assumed": True}},
        "phrases": {"rb_mount": {"en": "mount the {object} on the {place}", "slots": ["object", "place"], "assumed": True}},
        "goal_kinds": {"mount": {"holds": ["placed(?object, ?to)", "fastened(?object)"], "needs": ["Pick", "Place", "Fasten"],
                                 "observe": "std.find_at_destination", "unknown_is": "failure", "phrase": "rb_mount",
                                 "prompt": '"mount" = put a part on a station and fasten it there: object = the part, '
                                           'to = the station.',
                                 "assumed": True}}}
GOAL = [("mount", "bracket", "bin", "jig")]
PLAN = ["MoveTo " + BIN, "Find bracket", "Pick bracket", "MoveTo " + JIG, "Place " + JIG, "Fasten bracket"]
TASK = "Mount the bracket on the jig."

SCENARIOS = [
    (line("s29_done_by_holds_and_verifier", TASK, GOAL, plan=PLAN, pack=PACK),
     ["success", "fakeline"], "a mount goal (holds list, no rules): the plan is reached by simulation (Place, then Fasten's "
     "declared effect), the list holds in the belief at the end and the goal verifier sees the bracket on the jig: success"),
    (line("s29_verifier_cannot_tell_fails", TASK, GOAL, plan=PLAN, pack=PACK,
          robot={"faults": [{"skill": "find", "obj": "bracket", "nth": 2, "kind": "fail", "msg": "camera is down"}]}),
     ["failure", "fakeline"], "the list holds in the belief, but at the end check the camera is down (the verifier cannot "
     "tell); unknown_is: failure makes the goal unmet with the code perception_unavailable, the planner has no new plan and "
     "the run fails"),
    (line("s29_readback_phrase_key", TASK, GOAL, plan=PLAN, pack=PACK, options={"confirm_task": True},
          robot={"answers": ["yes"]}, llm_script=[understood(*GOAL)]),
     ["question", "success", "fakeline"], "the read-back of a mount goal says the pack's phrase rb_mount (the object and the "
     "station), answered yes, then the run is done"),
]
