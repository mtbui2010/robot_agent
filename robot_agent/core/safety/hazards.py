"""Household hazards the robot refuses to cause, independent of any benchmark (SAGE-v2 F4.3).

Hazardous products. The core list of cleaning chemicals comes from a public poison-control source that was
opened when this list was written: Children's Hospital of Philadelphia Poison Control Center, "Household
Cleaners" (https://chop.edu/centers-programs/poison-control-center/household-cleaners): ammonia-containing
floor / glass / jewelry cleaners, bleach, acid products (drain openers, toilet bowl cleaners, oven cleaners) and
mildew stain removers. Additions not on that page are marked ``assumed`` (general household knowledge:
detergents, dishwasher / laundry pods, disinfectants, rust removers, insecticides, lye). Korean / Vietnamese
forms are common translations of the same products. The robot never reads a benchmark's ``toxic`` tags.

Rules: (hazard class) x (place class, ``place_class.py``) -> refused, with a reason the robot says. A robot pack
may add rules in its manifest ``safety.hazards`` ({objects: [regex], places: [class or regex], say}). Option
``hazard_rules`` = core | pack | off (``off``: the model's own ``unsafe`` only, the ablation row).
"""

from __future__ import annotations

import re
from typing import Optional

from .. import domain
from .place_class import place_class

_H = domain.load()
# the default (household) profile's tables (domains/household/hazards.yaml); a robot that selects another
# profile is served by the ``dom`` argument below
HAZARDS = _H.hazard_classes
HAZARD_RULES = _H.hazard_rules


def _place_class_names(dom=None):
    """The place-class names of the profile (a rule's place that is not one of them is a regex on the place's name)."""
    return tuple((dom or domain.load()).place_classes)


def hazard_class(name: str, dom=None) -> str:
    t = str(name or "").lower()
    for cls, lists in (HAZARDS if dom is None else dom.hazard_classes).items():
        if any(re.search(p, t) for p in lists["sourced"] + lists["assumed"]):
            return cls
    return ""


def check_goal(goal: dict, places, pack_rules=(), mode: str = "core", dom=None) -> Optional[str]:
    """Why the robot will not do ``goal`` (a move of a hazardous product into / onto a place of a forbidden
    class), None. ``mode`` core: the core rules (+ pack rules when given); pack: the pack's rules only; off: none."""
    if mode == "off" or goal.get("type") not in ("on", "in"):
        return None
    obj = " ".join(str(goal.get(k) or "") for k in ("object", "said"))
    to = goal.get("to") or ""
    pc = place_class(places, to, dom)
    rules = ((list(HAZARD_RULES) if dom is None else dom.hazard_rules) if mode != "pack" else []) + list(pack_rules or ())
    for r in rules:
        if r.get("hazard"):
            hit = hazard_class(obj, dom) == r["hazard"]
        else:
            hit = any(re.search(p, obj.lower()) for p in r.get("objects") or [])
        if not hit:
            continue
        for p in r.get("places") or []:
            if p == pc or (p not in (_place_class_names(dom)) and re.search(p, str(to).lower())):
                return r.get("say") or "that is not safe"
    return None


__all__ = ["HAZARDS", "HAZARD_RULES", "hazard_class", "check_goal"]
