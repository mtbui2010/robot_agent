"""Reference copy for Step S06: ``holds`` and ``CONDITIONS`` as they were in robot_agent/options/bt_goal.py lines 50-61 and
``_PRED`` and ``_holds`` as they were in robot_agent/core/planning/verify.py lines 50-70, at commit 2d57c99 (the same code as
8002347), copied verbatim. Never edited: it is the old behaviour that tests/test_pack_predicates.py compares the pack's
predicate table with."""

import re
from typing import Optional


def holds(name: str, args, world, placed) -> bool:
    """The belief predicate ``name`` (pure): holding o, hand_free, at p, placed o p (put at p and not in the hand), opened c, closed c."""
    held, opened = getattr(world, "holding", None), set(getattr(world, "opened", None) or ())
    preds = {"holding": lambda o: held == o, "hand_free": lambda: held is None, "at": lambda p: getattr(world, "arrived", None) == p,
             "placed": lambda o, p: (placed or {}).get(o) == p and held != o, "opened": lambda c: c in opened, "closed": lambda c: c not in opened}
    try:
        return bool(preds[name](*args))
    except (KeyError, TypeError):
        return False


CONDITIONS = ("holding", "hand_free", "at", "placed", "opened", "closed")

_PRED = re.compile(r"^(not\s+|!)?([A-Za-z_]\w*)\s*(?:\(([^()]*)\))?$")


def _holds(world, pred: str, bind) -> Optional[bool]:
    """One manifest predicate on ``world``; None when it is not one the world model tracks."""
    m = _PRED.match(pred.strip())
    if not m:
        return None
    neg, name, arg = m.group(1), m.group(2), (m.group(3) or "").strip()
    val = None if arg in ("", "*") else bind(arg)
    if name == "hand_empty":
        r = world.holding is None
    elif name in ("arrived", "found", "holding"):
        cur = getattr(world, name, None)
        r = (cur == val) if val is not None else (cur is not None)
    elif name in ("opened", "on"):
        cur = set(getattr(world, name, ()) or ())
        r = (val in cur) if val is not None else bool(cur)
    else:
        return None
    return (not r) if neg else r
