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

CLASSES = {
    "food_storage": [r"\bfridge\b", r"\brefrigerator\b", r"\bfreezer\b", r"\bpantry\b", r"\blarder\b",
                     r"\bbread ?box\b", r"\bkimchi fridge\b", r"\bice ?box\b", r"\bfood (?:cabinet|storage|container)\b",
                     r"\blunch ?box\b", r"냉장고", r"냉동고", r"김치냉장고", r"팬트리", r"식료품", r"찬장", r"반찬통",
                     r"도시락", r"\btủ lạnh\b", r"\btủ đông\b"],
    "food_prep": [r"\bcutting board\b", r"\bchopping board\b", r"\bstove\b", r"\bhob\b", r"\boven\b",
                  r"\bmicrowave\b", r"\brice cooker\b", r"\btoaster\b", r"도마", r"가스레인지", r"레인지", r"오븐",
                  r"전자레인지", r"밥솥", r"\bbếp\b", r"\blò\b", r"\bnồi cơm\b"],
}


def _names(places, key) -> list:
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    head = str(key or "").split("@")[0].replace("_", " ")
    return [head] + [str(a) for a in e.get("aliases") or []] + ([str(e["category"])] if e.get("category") else [])


def place_class(places, key) -> str:
    """The class of ENV place ``key`` from its names, '' when none fits (``tags`` are never read)."""
    text = " | ".join(n.lower() for n in _names(places, key))
    for cls, pats in CLASSES.items():
        if any(re.search(p, text) for p in pats):
            return cls
    return ""


__all__ = ["CLASSES", "place_class"]
