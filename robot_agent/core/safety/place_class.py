"""The class of a place, inferred by the robot from the place's own names (key head, aliases, category).

Written clean-room from common appliance and kitchen vocabulary (EN, KO, VI); it reads no benchmark tag. A
robot pack's ENV gives names and, optionally, a ``category``; this maps them to a class:

* ``food_storage``: where food is kept (fridge, freezer, pantry, kimchi fridge, bread box, larder);
* ``food_prep``: where food is prepared or served (cutting board, stove / hob, oven, microwave, rice cooker,
  dining table is NOT here: things are put on a table all the time);
* ``""``: anything else.
"""

from __future__ import annotations

import re

from .. import domain

# the default (household) profile's classes (domains/household/places.yaml)
CLASSES = domain.load().place_classes


def _names(places, key) -> list:
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    head = str(key or "").split("@")[0].replace("_", " ")
    return [head] + [str(a) for a in e.get("aliases") or []] + ([str(e["category"])] if e.get("category") else [])


def place_class(places, key, dom=None) -> str:
    """The class of ENV place ``key`` from its names, '' when none fits (``tags`` are never read)."""
    text = " | ".join(n.lower() for n in _names(places, key))
    for cls, pats in (CLASSES if dom is None else dom.place_classes).items():
        if any(re.search(p, text) for p in pats):
            return cls
    return ""


__all__ = ["CLASSES", "place_class"]
