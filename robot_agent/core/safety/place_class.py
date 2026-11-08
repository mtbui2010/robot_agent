"""The class of a place, inferred by the robot from the place's own names (key head, aliases, category).

Written clean-room from common appliance and kitchen vocabulary (EN, KO); it reads no benchmark tag. A
robot pack's ENV gives names and, optionally, a ``category``; this maps them to a class:

* ``food_storage``: where food is kept (fridge, freezer, pantry, kimchi fridge, bread box, larder);
* ``food_prep``: where food is prepared or served (cutting board, stove / hob, oven, microwave, rice cooker,
  dining table is NOT here: things are put on a table all the time);
* ``""``: anything else.
"""

from __future__ import annotations

import re

# The words are language-pack data (``patterns.place_food_storage`` / ``place_food_prep`` of every pack, one regex each);
# ``CLASSES()`` reads them on use. A language with no pack adds none: its place names are then classed through the names the
# ENV also holds in a language that has (the English head of the key).
_KEYS = {"food_storage": "place_food_storage", "food_prep": "place_food_prep"}


def CLASSES() -> dict:                                                  # noqa: N802 (kept as the old table's name)
    from robot_agent.core.planning import langdata
    return {cls: langdata.pattern_list(key) for cls, key in _KEYS.items()}


def _names(places, key) -> list:
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    head = str(key or "").split("@")[0].replace("_", " ")
    return [head] + [str(a) for a in e.get("aliases") or []] + ([str(e["category"])] if e.get("category") else [])


def place_class(places, key) -> str:
    """The class of ENV place ``key`` from its names, '' when none fits (``tags`` are never read)."""
    text = " | ".join(n.lower() for n in _names(places, key))
    for cls, pats in CLASSES().items():
        if any(re.search(p, text) for p in pats):
            return cls
    return ""


__all__ = ["CLASSES", "place_class"]
