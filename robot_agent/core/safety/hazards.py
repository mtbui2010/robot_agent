"""Household hazards the robot refuses to cause, independent of any benchmark (SAGE-v2 F4.3).

Hazardous products. The core list of cleaning chemicals comes from a public poison-control source that was
opened when this list was written: Children's Hospital of Philadelphia Poison Control Center, "Household
Cleaners" (https://chop.edu/centers-programs/poison-control-center/household-cleaners): ammonia-containing
floor / glass / jewelry cleaners, bleach, acid products (drain openers, toilet bowl cleaners, oven cleaners) and
mildew stain removers. Additions not on that page are marked ``assumed`` (general household knowledge:
detergents, dishwasher / laundry pods, disinfectants, rust removers, insecticides, lye). Korean
forms are common translations of the same products. The robot never reads a benchmark's ``toxic`` tags.

Rules: (hazard class) x (place class, ``place_class.py``) -> refused, with a reason the robot says. A robot pack
may add rules in its manifest ``safety.hazards`` ({objects: [regex], places: [class or regex], say}). Option
``hazard_rules`` = core | pack | off (``off``: the model's own ``unsafe`` only, the ablation row).
"""

from __future__ import annotations

import re
from typing import Optional

from .place_class import place_class
from .receiver_class import receiver_class

# The product words are language-pack data (``patterns.hazard_cleaning_sourced`` / ``hazard_cleaning_assumed`` of every
# pack); the English lists carry the CHOP sources and the ``assumed`` marks named above. ``HAZARDS()`` reads them on use.
_LISTS = {"cleaning_chemical": ("hazard_cleaning_sourced", "hazard_cleaning_assumed")}


def HAZARDS() -> dict:                                                  # noqa: N802 (kept as the old table's name)
    from robot_agent.core.planning import langdata
    return {cls: {"sourced": langdata.pattern_list(a), "assumed": langdata.pattern_list(b)}
            for cls, (a, b) in _LISTS.items()}


HAZARD_RULES = [
    {"hazard": "cleaning_chemical", "places": ["food_storage", "food_prep"],
     "say": "cleaning chemicals do not go with food"},
]

# Option ``hazard_receiver`` (default off): a hazardous product must not be handed to a vulnerable person. The combination is
# (hazard class) x (receiver class): neither side is hazardous alone. Source of the rule: the same CHOP page says to keep household
# cleaners out of the reach of children (https://chop.edu/centers-programs/poison-control-center/household-cleaners). The words
# of the receiver class are language-pack data (``patterns.person_vulnerable``, ``assumed``: general vocabulary, no source); a
# person entry may also declare it (``age_group`` of the ENV entry, matched against the same words). No data = no receiver class
# = the rule never fires (fail open).
RECEIVER_RULES = [
    {"hazard": "cleaning_chemical", "receivers": ["vulnerable_person"],
     "say": "cleaning chemicals must not be handed to a child"},
]


def hazard_class(name: str) -> str:
    t = str(name or "").lower()
    for cls, lists in HAZARDS().items():
        if any(re.search(p, t) for p in lists["sourced"] + lists["assumed"]):
            return cls
    return ""


def check_goal(goal: dict, places, pack_rules=(), mode: str = "core", receivers: bool = False) -> Optional[str]:
    """Why the robot will not do ``goal`` (a move of a hazardous product into / onto a place of a forbidden
    class), None. ``receivers`` (option ``hazard_receiver``): also a hazardous product given to a person of a vulnerable class
    (``receiver_class``; the destination, or the person the delivery is near). ``mode`` core: the core rules (+ pack rules when given); pack: the pack's rules only; off: none."""
    if mode == "off" or goal.get("type") not in ("on", "in"):
        return None
    obj = " ".join(str(goal.get(k) or "") for k in ("object", "said"))
    to = goal.get("to") or ""
    pc = place_class(places, to)
    rules = (list(HAZARD_RULES) if mode != "pack" else []) + list(pack_rules or ())
    for r in rules:
        if r.get("hazard"):
            hit = hazard_class(obj) == r["hazard"]
        else:
            hit = any(re.search(p, obj.lower()) for p in r.get("objects") or [])
        if not hit:
            continue
        for p in r.get("places") or []:
            if p == pc or (p not in ("food_storage", "food_prep") and re.search(p, str(to).lower())):
                return r.get("say") or "that is not safe"
    return _receiver_check(goal, obj, places) if receivers else None


def _receiver_check(goal: dict, obj: str, places) -> Optional[str]:
    """The receiver rules (``RECEIVER_RULES``): the goal's destination (or the person it is near) is a person entry of a
    class a rule names, and the object is of the rule's hazard class."""
    for r in RECEIVER_RULES:
        if hazard_class(obj) != r["hazard"]:
            continue
        for key in (goal.get("to"), goal.get("near_person")):
            if key and receiver_class(places, key) in r["receivers"]:
                return r["say"]
    return None


__all__ = ["HAZARDS", "HAZARD_RULES", "RECEIVER_RULES", "hazard_class", "check_goal", "receiver_class"]
