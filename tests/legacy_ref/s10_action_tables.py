"""The action tables of the core as they were at robot_agent c04e6ee (before Step S10), copied verbatim.

A reference for tests/test_pack_actions.py (the standard pack's ``actions`` and ``constants`` files must equal it); never
imported by the engine. One table per old place; the names are the old ones with the file in the comment.
"""

LOC_ACTIONS = {"MoveTo", "Place", "PutIn", "Open", "Close"}                                  # assist_loop.py:83
AT_PLACE_ACTIONS = frozenset({"Find", "Pick", "Place", "PutIn", "Open", "Close"})            # reconcile.py:31
MAPPER_LOCATION_ACTIONS = {"MoveTo", "Place", "PutIn"}                                       # mapper.py:45
LEGACY_ADAPTER_LOCATION_ACTIONS = {"MoveTo", "Place", "PutIn"}                               # contract/legacy_adapter.py:30
SEARCH_BETWEEN = {"Open", "Close"}                                                           # search.py:31
SEARCH_AT_PLACE = {"Place", "PutIn", "Open", "Close"}                                        # search.py:33
RELEASES = ("Place", "PutIn")                                                                # plan_repair.py:52

# core/planning/robot.py:21-45
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
NOT_PLAN_STEPS = {"Observe", "Verify", "Ask", "Query"}
ABILITY = {"MoveTo": "move to a place", "Find": "find an object", "Pick": "pick it up",
           "Place": "place it on a surface", "Open": "open / close a container",
           "PutIn": "put an object in an open container", "Say": "say something to someone",
           "WaitForUnload": "carry an item to someone, who takes it"}

# core/planning/robot_prompt.py:22-37
ORDER = ("MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close", "Say", "WaitForUnload", "Dock")
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

# core/planning/announcer.py:25-39 (the English verbs)
VERBS_EN = {
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
}
