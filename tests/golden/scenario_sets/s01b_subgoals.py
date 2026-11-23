"""S01b goldens (review of S01, 2026-11-23): the sub-goal phrases the loop gives a planner that takes ``subgoals`` (option
``sage_subgoals=goals``, the default; ``goals.subgoal_text``). The harness's planner took no ``subgoals``, so no cassette held them;
here the planner key ``takes_subgoals`` records the list of every planner call. Needed by S07 (goals.py) and the "Goal kinds"
phrase field. Recorded on the old code (tests/golden/commits/s01b.json)."""

from scenarios import DR, DS, DT, house
from scenario_sets.s01_kinds import LR

ORANGE_IN = ("in", "orange", "", "kitchen drawer")
APPLE_ON = ("on", "apple", "desk", "dining table")
GO_SOFA = ("go", "", "", "sofa")


def subgoal_run(*a, **k) -> dict:
    """``house(...)`` whose planner takes ``subgoals``."""
    sc = house(*a, **k)
    sc["planner"]["takes_subgoals"] = True
    return sc


SCENARIOS = [
    (subgoal_run("subgoals_in_on_go",
                 "Put the orange in the kitchen drawer, bring the apple from the desk to the dining table and go to the sofa.",
                 [ORANGE_IN, APPLE_ON, GO_SOFA],
                 plan=["MoveTo " + DR, "Open " + DR, "MoveTo " + DT, "Find orange", "Pick orange", "MoveTo " + DR, "PutIn " + DR,
                       "MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT, "MoveTo " + LR]),
     ["success", "subgoals"], "an in, an on and a go goal in one request: the planner gets one sub-goal phrase per goal "
     "('put the orange into ...', 'put the apple (it is at ...) on ...', 'go to ...')"),
    (subgoal_run("subgoals_close_replan", "Close the kitchen drawer and bring the apple from the desk to the dining table.",
                 [("close", "kitchen drawer", "", ""), APPLE_ON],
                 plan=["MoveTo " + DR, "Close " + DR, "MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT],
                 robot={"opened": [DR], "faults": [{"skill": "pick", "kind": "fail", "nth": [1, 2]}]}, world={"opened": [DR]},
                 replans=[["MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT]]),
     ["replan", "subgoals"], "a close and an on goal; the pick fails, so the replan call shows what the loop passes there"),
]
