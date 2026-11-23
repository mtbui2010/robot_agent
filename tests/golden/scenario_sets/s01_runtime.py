"""S01 goldens, the run's settings and hooks (needed by S16a, S22, S24): the role clients and the sampling options of model calls
(capture ``llm_kw``), the refind after a pause, the request-coverage call, an environment variable that sets an option, the
order of the hooks (option ``trace``, capture ``trace``) and the hazard gate with the engine's default rules. Recorded on the
old code."""

import json
from pathlib import Path

from scenarios import CT, HOUSE, house, understood

APPLE = ("on", "apple", "desk", "dining table")
BOARD = "cutting board@kitchen"
SIM = json.loads((Path(__file__).resolve().parents[2] / "data" / "env_sim_house.json").read_text())

SCENARIOS = [
    (house("llm_roles", "Bring the apple from the desk to the dining table.", [APPLE],
           options={"understand_model": "um-small"}, capture=["llm_kw"]),
     ["success", "roles"], "the request read by another model (role client understand): the role and the options of each call"),
    (house("pause_resume", "Bring the apple from the desk to the dining table.", [APPLE], managed=True,
           pause_after={"skill": "find", "nth": 1}),
     ["success", "pause"], "paused and resumed after the Find: the found object is looked for again before the Pick"),
    (dict(house("coverage", "Bring the apple from the desk to the dining table and tell me when you are done.", None,
                options={"request_coverage": "1"}, robot={"answers": ["no"], "answers_in_order": True}),
          llm={"script": [["is about to say it is done", {"unserved": [{"part": "tell me when you are done", "why": "no_goal"}]}]],
               "default": understood(APPLE)}),
     ["question", "success"], "the request-coverage call names a part with no goal (tell me when you are done); asked"),
    (dict(house("env_override", "Bring the apple from the desk to the dining table.", [APPLE], objects=dict(HOUSE, apple=CT),
                options={"search_legs": None}), environ={"ROBOT_AGENT_SEARCH_LEGS": "1"}),
     ["success", "environ"], "an option set by its environment variable only (ROBOT_AGENT_SEARCH_LEGS): the search leg runs"),
    (house("hooks_order", "Bring the apple from the desk to the dining table.", [APPLE], options={"trace": "1"}, capture=["trace"]),
     ["success", "trace"], "option trace on: the rows it writes from the hooks it watches, in their order"),
    (dict(house("hazard_default", "Put the bleach on the cutting board.", [("on", "bleach", "", "cutting board")],
                objects=dict(HOUSE, bleach=CT), plan=["MoveTo " + CT, "Find bleach", "Pick bleach", "MoveTo " + BOARD, "Place " + BOARD]),
          env=dict(SIM, **{BOARD: {"aliases": ["cutting board"], "loc": {"x": -0.4, "y": -1.6}, "height": 0.9}})),
     ["refused", "hazard"], "the hazard gate with the engine's default rules on fakebot's household fixture profile"),
]
