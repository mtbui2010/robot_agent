"""Reference copy for Step S10: the action tables the engine kept in several copies, as they were at commit c04e6ee (the tip
of generalize-core before S10; the same values as 8002347), copied verbatim from robot_agent/core/planning/robot.py lines 20-45
(``CORE_TEXT``, ``NOT_PLAN_STEPS``, ``_ABILITY``), announcer.py lines 25-40 (``VERBS``), robot_prompt.py lines 26-37
(``ACTION_TEXT``), reconcile.py line 31, mapper.py line 45, contract/legacy_adapter.py line 30, search.py lines 31-33,
plan_repair.py line 52 and assist_loop.py line 83. The names of the sets differ per file, so each gets a distinct name here.
Never edited: it is the old behaviour that tests/test_pack_actions.py compares the pack's actions table with."""

# what the core says when the robot's i18n has no phrase for a key (English, {placeholders})
CORE_TEXT = {
    "verify_failed": "{skill} reported success but the {sensor} check says otherwise",
    "verify_unknown": "unknown: {skill} declares no sensor that checks it",
    "sensor_unknown": "unknown: the {sensor} check could not run ({why})",
    "robot_description": "The robot can: {abilities}.",
    # a goal kind needs an action the robot has no skill for (the robot's i18n `cannot_do` wins)
    "cannot_do": "I can't do that: I have no {missing} skill.",
    # what a delivering robot says when it arrives (deliver goals)
    "delivery": "I have a delivery for you. Please take it.",
    # the run used its LLM-call budget (budget.budget_end)
    "llm_budget": "I have to stop: I could not work this task out within my limit of tries.",
    # an unclear answer to a yes / no question ("yet", noise) is asked again with this (interact.dialog)
    "yes_or_no": "Sorry, I didn't catch that. Please answer yes or no.",
}

# manifest actions that are not plan steps: sensors (Observe / Verify), the dialog (Ask, the core's
# own) and queries to another model (Query)
NOT_PLAN_STEPS = {"Observe", "Verify", "Ask", "Query"}

# abstract action -> how the task-understanding prompt describes it (robots override the
# whole sentence with the i18n key "robot_description")
_ABILITY = {"MoveTo": "move to a place", "Find": "find an object", "Pick": "pick it up",
            "Place": "place it on a surface", "Open": "open / close a container",
            "PutIn": "put an object in an open container", "Say": "say something to someone",
            "WaitForUnload": "carry an item to someone, who takes it"}

VERBS: dict[str, dict[str, str]] = {
    'en': {
        'MoveTo': 'move to', 'GoTo': 'move to', 'Navigate': 'move to',
        'Find': 'find', 'Search': 'find', 'Detect': 'find',
        'Pick': 'pick', 'PickUp': 'pick', 'Grasp': 'pick',
        'Place': 'place', 'PlaceAt': 'place', 'PutDown': 'place', 'PutIn': 'place',
        'Open': 'open', 'Close': 'close',
        # direct-path skills
        'navigate': 'move to', 'move': 'move to', 'moveto': 'move to',
        'find': 'find', 'detect': 'find', 'detect_object': 'find',
        'pick': 'pick', 'grasp': 'pick',
        'place': 'place', 'put': 'place',
        'open': 'open', 'close': 'close',
    },
}


ACTION_TEXT = {
    "MoveTo": ("place", "move to a place"),
    "Find": ("object", "look for an object where the robot is"),
    "Pick": ("object", "pick up the found object"),
    "Place": ("place", "put the held object on a place the robot is at"),
    "PutIn": ("container", "put the held object into an open container the robot is at"),
    "Open": ("container", "open a container the robot is at"),
    "Close": ("container", "close a container the robot is at"),
    "Say": ("text", "say a sentence to the person"),
    "WaitForUnload": ("item", "wait until a person takes the carried item"),
    "Dock": ("dock", "drive onto the charging dock"),
}

# assist_loop.py:83 (_LOC_ACTIONS)
LOOP_LOC_ACTIONS = {"MoveTo", "Place", "PutIn", "Open", "Close"}
# reconcile.py:31
RECONCILE_AT_PLACE = frozenset({"Find", "Pick", "Place", "PutIn", "Open", "Close"})
# mapper.py:45
MAPPER_LOCATION_ACTIONS = {"MoveTo", "Place", "PutIn"}
# legacy_adapter.py:30
ADAPTER_LOCATION_ACTIONS = {"MoveTo", "Place", "PutIn"}
# search.py:31-33
SEARCH_BETWEEN = {"Open", "Close"}
SEARCH_AT_PLACE = {"Place", "PutIn", "Open", "Close"}
# plan_repair.py:52
PLAN_REPAIR_RELEASES = ("Place", "PutIn")
