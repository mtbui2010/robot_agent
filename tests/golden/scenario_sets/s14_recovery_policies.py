"""S14 goldens (feature cassettes, recorded on the step's production commit): recovery policies chosen by name.

Made-up packs on ``fakeline`` (extends the standard pack): a custom action (Fasten) with a registered policy of the robot's
own package (``s14_line.fasten_again`` in tests/fakeline/s14_recovery.py: the same step again once, then the planner); a goal
kind with the builtin ``stop`` (``by_kind: {on: stop}``: a failed Pick ends the run, no grasp retry); an action sent to the
builtin ``planner`` (``by_action: {Pick: planner}``: a failed Pick is replanned at once). Every fact here is made up
(assumed)."""

from scenario_sets.s01_kinds import BIN, JIG, line

PLAN = ["MoveTo " + BIN, "Find bracket", "Pick bracket", "MoveTo " + JIG, "Fasten bracket", "Place " + JIG]
GOAL = [("on", "bracket", "bin", "jig")]
TASK = "Put the bracket on the jig and fasten it."
OWN = {"schema": 2, "name": "s14_line", "extends": "household", "assumed": True, "callables": "fakeline.s14_recovery",
       "recovery": {"policies": {"s14_line.fasten_again": {"callable": "s14_line.fasten_again", "times": 1, "assumed": True}},
                    "by_action": {"Fasten": "s14_line.fasten_again"}}}
STOP_ON = {"schema": 2, "name": "s14_line_stop", "extends": "household", "assumed": True, "recovery": {"by_kind": {"on": "stop"}}}
PICK_PLANNER = {"schema": 2, "name": "s14_line_planner", "extends": "household", "assumed": True,
                "recovery": {"by_action": {"Pick": "planner"}}}
PICK_FAIL = {"skill": "pick", "obj": "bracket", "kind": "fail", "nth": 1, "msg": "could not grasp the bracket"}

SCENARIOS = [
    (line("s14_registered_policy", TASK, GOAL, plan=PLAN, pack=OWN,
          robot={"faults": [{"skill": "fasten", "kind": "fail", "nth": 1, "msg": "clamp did not close"}]}),
     ["success", "fakeline"], "fakeline's Fasten fails once; the pack's registered policy for Fasten runs it again in place "
     "(no replan, unlike custom_action) and the run succeeds"),
    (line("s14_by_kind_stop", TASK, GOAL, plan=PLAN, pack=STOP_ON, robot={"faults": [PICK_FAIL]}),
     ["failure", "fakeline"], "by_kind on: stop; the Pick fails once and the run ends at once (no grasp retry, no "
     "help question, no replan)"),
    (line("s14_by_action_planner", TASK, GOAL, plan=PLAN, pack=PICK_PLANNER, robot={"faults": [PICK_FAIL]},
          replans=[["Find bracket", "Pick bracket", "MoveTo " + JIG, "Fasten bracket", "Place " + JIG]]),
     ["replan", "success", "fakeline"], "by_action Pick: planner; the failed Pick skips the pick ladder (no look again in "
     "place) and goes to the planner's replan, then the run succeeds"),
]
