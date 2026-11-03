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

from .place_class import place_class

HAZARDS = {
    "cleaning_chemical": {
        "sourced": [r"\bbleach\b", r"\bammonia\b", r"\bglass cleaner\b", r"\bfloor cleaner\b", r"\bjewel(?:le)?ry cleaner\b",
                    r"\bdrain (?:opener|cleaner)\b", r"\btoilet (?:bowl )?cleaner\b", r"\boven cleaner\b",
                    r"\bmildew (?:stain )?remover\b", r"표백제", r"락스", r"암모니아", r"유리 ?세정제", r"배수구 ?세정제",
                    r"변기 ?세정제", r"오븐 ?세정제", r"곰팡이 ?제거제", r"\bthuốc tẩy\b", r"\bnước tẩy\b"],
        "assumed": [r"\bdetergent\b", r"\bdishwasher (?:pods?|tablets?)\b", r"\blaundry pods?\b", r"\bdisinfectant\b",
                    r"\brust remover\b", r"\binsecticide\b", r"\bbug spray\b", r"\blye\b", r"\bcleaning spray\b",
                    r"세제", r"살충제", r"소독제", r"세정제", r"\bchất tẩy\b", r"\bthuốc diệt côn trùng\b"],
    },
}
HAZARD_RULES = [
    {"hazard": "cleaning_chemical", "places": ["food_storage", "food_prep"],
     "say": "cleaning chemicals do not go with food"},
]


def hazard_class(name: str) -> str:
    t = str(name or "").lower()
    for cls, lists in HAZARDS.items():
        if any(re.search(p, t) for p in lists["sourced"] + lists["assumed"]):
            return cls
    return ""


def check_goal(goal: dict, places, pack_rules=(), mode: str = "core") -> Optional[str]:
    """Why the robot will not do ``goal`` (a move of a hazardous product into / onto a place of a forbidden
    class), None. ``mode`` core: the core rules (+ pack rules when given); pack: the pack's rules only; off: none."""
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
    return None


__all__ = ["HAZARDS", "HAZARD_RULES", "hazard_class", "check_goal"]
