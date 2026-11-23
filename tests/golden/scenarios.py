"""The golden scenarios: made-up robots (tests/fakebot, tests/fakehome), made-up flats and objects, scripted model and planner.

Each entry is plain data; ``tools/golden_record.py --record`` turns a scenario with no cassette yet into
``cassettes/<name>.cassette.json`` (the cassette keeps its own copy, so editing an entry here never changes a golden).
Tags (the union must cover success, failure, question, stop, goals_only, bt_goal, replan, plan_only).
"""

DS, DT, CT, DR = "desk@study room", "dining table@kitchen", "counter@kitchen", "drawer@kitchen"
HOUSE = {"apple": DS, "mug": DS, "banana": DT, "orange": DT, "sponge": CT, "spoon": DR + "/in", "tennis ball": "stool@bedroom"}
# make_assist pins these (the older tests' order); a scenario without the pins runs the engine defaults
PINNED = dict(input_pivot="none", look_then_ask="0", bring_ask="0", bt_goal="0", fetch_guard="0")


def understood(*goals, status="clear", question="", reason=""):
    return {"status": status, "reason": reason, "question": question,
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


FETCH = ["MoveTo " + DS, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT]


def house(name, task, goal, plan=FETCH, objects=HOUSE, replans=(), llm_script=(), options=None, **extra):
    """A fakebot run in the sim house (tests/data/env_sim_house.json)."""
    sc = {"name": name, "env": "sim_house", "robot_pkg": "fakebot", "task": task,
          "robot": {"objects": dict(objects), **extra.pop("robot", {})},
          "llm": {"script": list(llm_script), "default": understood(*goal) if goal else None},
          "planner": {"plan": list(plan), "replans": [list(r) for r in replans]},
          "options": {k: v for k, v in dict(PINNED, **(options or {})).items() if v is not None}}     # None: not pinned
    sc.update(extra)
    return sc


# ── a made-up flat for the behaviour-tree pack (a den and a loft; objects gizmo / widget) ──────────────────────────────────
SHELF, BENCH, CRATE = "shelf@loft", "bench@den", "crate@den"
FLAT = {SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
        BENCH: {"aliases": ["bench"], "loc": {"x": 6.0, "y": 1.0}, "surface": True},
        CRATE: {"aliases": ["crate"], "loc": {"x": 6.5, "y": 2.0}, "surface": True}}
GIZMO = {"type": "on", "object": "gizmo", "from": SHELF, "to": BENCH}
BT_ON = ["fallback", ["cond", "placed", "{object}", "{to}"],
         ["sequence",
          ["fallback", ["cond", "holding", "{object}"],
           ["sequence", ["cond", "hand_free"], ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["act", "Pick", "{object}"],
            ["cond", "holding", "{object}"]]],
          ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"], ["cond", "hand_free"]]]
BT_PACK = {"bt_goal": {
    "trees": {"on": {"tree": BT_ON, "skip_if": ["hand"], "assumed": True},
              "go": {"tree": ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]], "assumed": True}},
    "menu": {"phrases": {"Find": "q_ask_next_find", "Pick": "q_ask_next_pick", "MoveTo": "q_ask_next_move"},
             "retry": ["ask_next.retry", "ask_next.elsewhere", "dialogue.yes"], "stop": ["ask_next.stop", "dialogue.stop"],
             "assumed": True}}}
BT_PLAN = ["MoveTo " + SHELF, "Find gizmo", "Pick gizmo", "MoveTo " + BENCH, "Place " + BENCH]


def flat(name, objects=None, policy=None, answers=(), faults=(), replans=(), options=None, **extra):
    """A fakebot run of the bt_goal pack in the made-up flat (the shape of tests/test_option_bt_goal.py's ``_run``)."""
    opts = dict(PINNED, bt_goal="1", detector_miss_rate=0, search_legs=extra.pop("search_legs", 1), health_wait_s=0, **(options or {}))
    if policy:
        opts["bt_goal.exhausted"] = policy
    return {"name": name, "env": FLAT, "robot_pkg": "fakebot", "task": "Take the gizmo from the shelf and put it on the bench.",
            "robot": {"objects": objects or {"gizmo": SHELF, "widget": CRATE}, "answers": list(answers), "faults": list(faults),
                      "start": CRATE},
            "llm": {"script": [], "default": {"status": "clear", "reason": "", "question": "", "goals": [dict(GIZMO)]}},
            "planner": {"plan": list(BT_PLAN), "replans": [list(r) for r in replans]},
            "options": opts, "pack_options": BT_PACK, **extra}


# ── a made-up galley for the understand-only runs (fleet seam goals_only) ──────────────────────────────────────────────────
GSHELF, GCRATE, GTABLE = "shelf@galley", "crate@galley", "zorbik table@den"
GALLEY = {GSHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
          GCRATE: {"aliases": ["crate"], "loc": {"x": 2.0, "y": 1.0}, "surface": True},
          GTABLE: {"aliases": ["zorbik table"], "loc": {"x": 4.0, "y": 1.0}, "surface": True}}


def galley(name, said, options, answers=(), **extra):
    return {"name": name, "env": GALLEY, "robot_pkg": "fakebot", "task": extra.pop("task", "Put the zorbel from the shelf on the crate."),
            "robot": {"objects": {"zorbel": GSHELF, "vumpa": GTABLE}, "start": GSHELF, "answers": list(answers)},
            "llm": {"script": [], "default": {"status": "clear", "reason": "", "question": "", "goals": [said]}},
            "planner": {"plan": ["MoveTo " + GSHELF, "Find zorbel", "Pick zorbel", "MoveTo " + GCRATE, "Place " + GCRATE]},
            "options": dict(PINNED, search_legs=1, ask_help=False, detector_miss_rate=0, **options), **extra}


ZORBEL = {"type": "on", "object": "zorbel", "from": "shelf", "to": "crate"}
APPLE = ("on", "apple", "desk", "dining table")

SCENARIOS = [
    (house("fetch_success", "Bring the apple from the desk to the dining table.", [APPLE]), ["success"],
     "fetch an apple: understand, plan, gate, five steps, the goal observed"),
    (house("fetch_engine_defaults", "Bring the apple from the desk to the dining table.", [APPLE], options={k: None for k in PINNED}),
     ["success", "default_options"], "the same fetch with the engine's own defaults (no option pinned)"),
    (house("find_miss_replan", "Bring the apple from the desk to the dining table.", [APPLE], objects=dict(HOUSE, apple=CT),
           replans=[["MoveTo " + CT, "Find apple", "Pick apple", "MoveTo " + DT, "Place " + DT]]),
     ["replan", "success"], "the apple is not where the person said: a Find miss, the planner's replan, success"),
    (house("gate_symbolic_repair", "Bring the apple from the desk to the dining table.", [APPLE],
           plan=["MoveTo " + DS, "Pick apple", "MoveTo " + DT, "Place " + DT]),
     ["success", "gate"], "a plan with no Find: the gate's symbolic repair adds it before any motion"),
    (house("skill_bug_failure", "Bring the apple from the desk to the dining table.", [APPLE],
           robot={"faults": [{"skill": "pick", "kind": "swerr", "nth": "all"}]}),
     ["failure"], "a software error of the pick skill: failed at once, not retried, no replan"),
    (house("dropped_on_the_way", "Bring the apple from the desk to the dining table.", [APPLE],
           robot={"faults": [{"skill": "grasp_succeed", "kind": "drop", "nth": 2}]}),
     ["failure"], "the apple falls on the way: the run stops and says why"),
    (house("pick_fails_twice_replan", "Put the mug on the dining table.", [("on", "mug", "", "dining table")],
           plan=["MoveTo " + DS, "Find mug", "Pick mug", "MoveTo " + DT, "Place " + DT],
           replans=[["MoveTo " + DS, "Find mug", "Pick mug", "MoveTo " + DT, "Place " + DT]],
           robot={"faults": [{"skill": "pick", "kind": "fail", "nth": [1, 2]}]}),
     ["replan", "success"], "a grasp that fails twice: the retry in place, then the planner's replan"),
    (house("which_table_question", "Put the mug on the table.", None, llm_script=[understood(("on", "mug", "", "table")),
                                                                                  understood(("on", "mug", "", "dining table"))],
           plan=["MoveTo " + DS, "Find mug", "Pick mug", "MoveTo " + DT, "Place " + DT], robot={"answers": ["the dining table"]}),
     ["question", "success"], "two tables: the person is asked which one, the answer is read back (yes), success"),
    (house("readback_no_correction", "Bring the apple to the dining table.", [APPLE], options={"confirm_task": True},
           robot={"answers": ["no"]}),
     ["question", "failure"], "the read-back of the task is answered no"),
    (house("unsafe_refused", "Throw the apple at the window.", None,
           llm_script=[understood(status="unsafe", reason="Throwing things can hurt someone.")], plan=[]),
     ["refused", "failure"], "an unsafe request is refused before any plan or motion"),
    (house("drawer_put_in", "Put the orange in the kitchen drawer.", [("in", "orange", "", "kitchen drawer")],
           plan=["MoveTo " + DR, "Open " + DR, "MoveTo " + DT, "Find orange", "Pick orange", "MoveTo " + DR, "PutIn " + DR]),
     ["success"], "open a drawer, fetch the orange, put it in"),
    (house("cancel_during_move", "Bring the apple from the desk to the dining table.", [APPLE],
           cancel_after={"skill": "move", "nth": 1}),
     ["stop"], "the person stops the run while the robot moves: aborted after the first step"),
    (house("plan_only_planned", "Bring the apple to the dining table.", [APPLE], plan_only=True),
     ["plan_only"], "plan only: understood and planned, nothing moves"),
    (galley("goals_only_understood", ZORBEL, {"goals_only": "1"}),
     ["goals_only"], "an understand-only run (fleet seam): the goals come back, no plan, no motion"),
    (galley("goals_only_given_goals", ZORBEL, {"goals_only": "1", "accept_goals": "1"}, task="on zorbel",
            goals=[{"type": "on", "object": "zorbel", "from": GSHELF, "to": GCRATE}]),
     ["goals_only"], "the caller's goals with goals_only: grounded and handed back, no model call"),
    (flat("bt_goal_blocked_move", faults=[{"skill": "move", "obj": BENCH, "kind": "fail", "msg": "path blocked by a chair"}]),
     ["bt_goal", "success"], "a declared behaviour tree; a blocked move recovers by the engine's ladder inside the tree"),
    (flat("bt_goal_grasp_help", answers=["yes"],
          faults=[{"skill": "pick", "kind": "fail", "nth": [1, 2], "msg": "grasp failed: it slipped"}]),
     ["bt_goal", "question", "success"], "a grasp that fails twice inside the tree: the engine's retries and its help question"),
    (flat("bt_goal_absent_sage_replan", objects={"gizmo": CRATE}, search_legs=0, answers=[""], policy="sage_once",
          replans=[["MoveTo " + CRATE, "Find gizmo", "Pick gizmo", "MoveTo " + BENCH, "Place " + BENCH]]),
     ["bt_goal", "question", "replan", "success"], "the gizmo is not on the shelf: the menu question unanswered, one SAGE replan"),
    (flat("bt_goal_stop_word", objects={"gizmo": CRATE}, search_legs=0, answers=["Stop."]),
     ["bt_goal", "question", "stop"], "the person answers the menu question with a stop word: the run ends"),
    (flat("bt_goal_hw_fault", faults=[{"skill": "place", "kind": "fail", "msg": "gripper motor fault", "nth": "all"}]),
     ["bt_goal", "failure"], "a hardware fault with no rung left: reported, stopped, no question"),
    (flat("bt_goal_goal_change", managed=True, say_after={"skill": "pick", "nth": 1, "text": "actually, put it on the crate instead"},
          replans=[["MoveTo " + CRATE, "Place " + CRATE]]),
     ["bt_goal", "replan", "success"], "a goal change said while the tree runs: the trees end, the engine applies it by a replan"),
]
