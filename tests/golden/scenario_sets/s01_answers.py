"""S01 goldens, answers (needed by S15, S19, S25a): an unclear answer (silence, then a near-miss word), a read-back answered
with a correction ("no, the other table"), and the bt_goal menu answered with a retry word and then with a place. Recorded
on the old code."""

from scenarios import CRATE, DS, DT, HOUSE, flat, house, understood

APPLE = ("on", "apple", "desk", "dining table")

SCENARIOS = [
    (house("unclear_answer", "Bring the apple from the desk to the dining table.", [APPLE], options={"confirm_task": True},
           robot={"answers": ["", "yeah sure"], "answers_in_order": True}),
     ["question", "success"], "the read-back answered with silence, then with a near-miss word: the silence and the unclear "
     "branches of yes_no"),
    (dict(house("readback_correction", "Put the mug on the dining table.", None, options={"confirm_task": True},
                plan=["MoveTo " + DS, "Find mug", "Pick mug", "MoveTo counter@kitchen", "Place counter@kitchen"],
                robot={"answers": ["no, the counter", "yes"], "answers_in_order": True}),
          llm={"script": [understood(("on", "mug", "desk", "dining table"))], "default": understood(("on", "mug", "desk", "counter"))}),
     ["question", "success"], "the read-back answered with a correction: the rest of the answer is read as the new goal"),
    (flat("menu_retry_place", objects={"gizmo": CRATE}, search_legs=0, answers=["the crate"]),
     ["bt_goal", "question", "success"], "the bt_goal menu question answered with a place: the tree runs again from there"),
    (flat("menu_retry_word", objects={"gizmo": CRATE}, search_legs=0, answers=["try again"]),
     ["bt_goal", "question", "failure"], "the bt_goal menu question answered with a retry word: the tree runs again, fails, stops"),
    (house("help_silence", "Bring the apple from the desk to the dining table.", [APPLE], options={"ask_help": True},
           robot={"faults": [{"skill": "move", "obj": DT, "kind": "fail", "nth": "all", "msg": "path blocked"}],
                  "answers": [""], "answers_in_order": True}),
     ["question", "failure"], "the MoveTo help question answered with silence: read as no answer (not as yes), the run stops"),
]
