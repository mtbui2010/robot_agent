"""S01 goldens, the planner's inputs and the task stack (needed by S10, S11, S20, S21): the gate's findings in the refine call
(capture ``planner_io``), the observation and visible list of every planner call, the run-start reconcile not stubbed
(capture ``reconcile``), a goal change said mid-run and a new command that goes first. Recorded on the old code."""

from scenarios import CT, DS, DT, HOUSE, house, understood
from scenario_sets.s01_kinds import JIG, line

APPLE = ("on", "apple", "desk", "dining table")
FETCH = ["MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT]

SCENARIOS = [
    (house("gate_findings", "Bring the apple from the desk to the dining table.", [APPLE],
           plan=["MoveTo " + CT, "Pick apple", "MoveTo " + CT, "Place " + CT], capture=["planner_io"]),
     ["gate", "failure"], "a plan with three problems (no Find, the wrong place, the goal not reached): the findings in the "
     "refine call's observation (planner_io), not certified"),
    (house("planner_visible", "Bring the apple from the desk to the dining table.", [APPLE], objects=dict(HOUSE, apple=CT),
           replans=[["MoveTo " + CT, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT]], capture=["planner_io"]),
     ["replan", "success"], "the observation and the visible list of the plan and of the replan call (planner_io)"),
    (line("real_reconcile", "Go to the outfeed.", [("go", "", "", "outfeed")], robot_pkg="fakeline.cart",
          capture=["reconcile", "prompts"], options={"reconcile": "0"}, world={"arrived": JIG, "holding": None}),
     ["success", "reconcile", "fakeline"], "the legacy run-start reconcile (option reconcile off: loop.reconcile_world, not "
     "stubbed) with the pack's own pose reader (the cart's name map robot_xy): the belief says jig, the reader says bin"),
    (house("goal_change", "Bring the apple from the desk to the dining table.", [APPLE], managed=True,
           say_after={"skill": "pick", "nth": 1, "text": "actually, put it on the counter instead"},
           llm_script=[understood(APPLE)], replans=[["MoveTo " + CT, "Place " + CT]],
           robot={"answers": ["yes"]}),
     ["replan", "goal_change"], "a goal change said while the robot holds the apple: read, applied by a replan"),
    (house("new_task_first", "Bring the apple from the desk to the dining table.", [APPLE], managed=True,
           say_after={"skill": "move", "nth": 1, "text": "first put the mug on the counter"},
           llm_script=[understood(APPLE)], replans=[["MoveTo " + DS, "Find mug", "Pick mug", "MoveTo " + CT, "Place " + CT], FETCH],
           robot={"answers": ["yes"]}),
     ["task_stack", "question", "success"], "a new command said during the run: asked, the task stack (suspend, the new task "
     "first, resume the first)"),
]
