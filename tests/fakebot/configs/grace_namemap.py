"""A frozen, KAAIR-like ``grace_namemap`` for the tests (robot package ``fakebot``).

Copied from kcare_robot's configs/grace_namemap.py (tables + param adapters
only, no ROS / perception hooks) so the core can be tested without the robot
package. Change it only when the contract changes, not to follow kcare.
"""

from __future__ import annotations

import re

LOCATIONS: dict[str, str] = {
    "LivingRoom":   "living room",
    "Bedroom":      "bedroom",
    "Kitchen":      "kitchen",
    "Restroom":     "restroom",
    "Bathroom":     "restroom",
    "Table":        "table@living room",
    "DiningTable":  "dining table",
    "SophaTable":   "sopha table",
    "SofaTable":    "sopha table",
    "BedTable":     "bed table",
    "Shelf":        "shelf",
    "Sink":         "sink",
    "Drawer":       "drawer",
    "Home":         "home",
}

OBJECTS: dict[str, str] = {
    "Cup":           "cup",
    "Mug":           "mug",
    "Can":           "can",
    "Bottle":        "bottle",
    "WaterBottle":   "water bottle",
    "Apple":         "apple",
    "Coke":          "coke",
    "Coffee":        "coffee",
    "CoffeeMachine": "coffee machine",
    "Towel":         "towel",
    "Phone":         "phone",
    "Controller":    "controller",
    "Remote":        "controller",
    "Toothpaste":    "toothpaste",
    "Pen":           "pen",
    "Light":         "light",
    "Drawer":        "drawer",
    "Handle":        "handle",
}

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _camel_split(name: str) -> list[str]:
    s = (name or "").strip().replace("_", " ").replace("-", " ")
    if not s:
        return []
    out: list[str] = []
    for tok in s.split():
        out.extend(p for p in _CAMEL_BOUNDARY.split(tok) if p)
    return out or [s]


def _camel_to_snake(name: str) -> str:
    return "_".join(_camel_split(name)).lower()


def _camel_to_words(name: str) -> str:
    return " ".join(_camel_split(name)).lower()


def to_loc(name: str) -> str:
    if not name:
        return ""
    return LOCATIONS.get(name, _camel_to_snake(name))


def to_obj(name: str) -> str:
    if not name:
        return ""
    return OBJECTS.get(name, _camel_to_words(name))


def _to_obj_or_loc(name: str) -> str:
    if not name:
        return ""
    if name in LOCATIONS:
        return LOCATIONS[name]
    if name in OBJECTS:
        return OBJECTS[name]
    return _camel_to_words(name)


SKILL_MAP: dict[str, str] = {
    "MoveTo": "move",
    "Find":   "find",
    "Pick":   "pick",
    "Place":  "place",
    "PutIn":  "place",
    "Open":   "open_drawer",
    "Close":  "close_drawer",
}

NOOP_ACTIONS: set[str] = {"Sit", "LieOn", "Serve", "Wait"}
SUPPORTED_ACTIONS: set[str] = set(SKILL_MAP.keys()) | NOOP_ACTIONS
VLM_ACTIONS: set[str] = {"Pick", "Place", "PutIn", "Open", "Close"}


def build_params(action: str, obj_camel: str, world) -> dict:
    found = getattr(world, "found", None) if world is not None else None
    if action == "MoveTo":
        return {"inputs": to_loc(obj_camel)}
    if action == "Find":
        return {"inputs": to_obj(obj_camel)}
    if action == "Pick":
        return {"inputs": to_obj(found or obj_camel), "type": "fine_move"}
    if action in ("Place", "PutIn"):
        return {"inputs": _to_obj_or_loc(obj_camel)}
    if action in ("Open", "Close"):
        return {"inputs": to_loc(obj_camel)}
    return {}
