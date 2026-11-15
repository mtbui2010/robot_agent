"""The rule engine of the safety layer (SAGE-v2 F4.3). It holds NO domain data: classes, words, rules and the sentences the
robot says are in the robot's safety profile (``profile.py``, docs/safety.md).

A rule is (object: a hazard class or regexes) x (place: a place class or a regex on the place key, OR receiver: a receiver
class). ``find_rule`` returns the first rule that fires for a goal, ``check_goal`` the sentence of it.

Evaluation (``mode``): ``off`` none; ``profile`` (alias ``core``) the profile's place rules, then the legacy manifest list
``safety.hazards`` ({objects | hazard, places, say}); ``pack`` the legacy list only. Receiver rules (the destination or the
person a delivery is near is a person entry of a receiver class) run in every mode but ``off``. A rule with ``enabled_by`` runs
only when that option key is in ``enabled``. A goal kind not in ``profile.checks`` is not looked at. An object is the goal's
``object`` and ``said`` text, lower case; the hazard class words are the union over all languages of the profile.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from .place_class import place_class
from .profile import EMPTY, Rule
from .receiver_class import receiver_class

NOT_SAFE = "that is not safe"


class Verdict(NamedTuple):
    say: str                     # why, in the prompt language
    rule: str                    # id of the profile rule, "" for a legacy manifest rule


def hazard_class(name: str, profile=EMPTY) -> str:
    """The first hazard class (profile order) whose words match ``name`` (any language of the profile), '' when none."""
    t = str(name or "").lower()
    for cls, pats in profile.hazard_classes:
        if any(re.search(p, t) for p in pats):
            return cls
    return ""


def _object_hit(rule: Rule, obj: str, profile) -> bool:
    if rule.hazard:
        return hazard_class(obj, profile) == rule.hazard
    return any(re.search(p, obj.lower()) for p in rule.objects)


def _place_hit(rule: Rule, to, pc: str, declared) -> bool:
    return any(p == pc or (p not in declared and re.search(p, str(to).lower())) for p in rule.places)


def _receiver_hit(rule: Rule, goal: dict, places, profile) -> bool:
    return any(key and receiver_class(places, key, profile) in rule.receivers for key in (goal.get("to"), goal.get("near_person")))


def find_rule(goal: dict, places, profile=EMPTY, pack_rules=(), mode: str = "profile", enabled=()) -> Optional[Verdict]:
    """The first rule that forbids ``goal`` as a :class:`Verdict`, None. ``pack_rules``: the manifest's legacy list."""
    if mode == "off" or goal.get("type") not in profile.checks:
        return None
    obj = " ".join(str(goal.get(k) or "") for k in ("object", "said"))
    to = goal.get("to") or ""
    pc = place_class(places, to, profile)
    declared = profile.place_names()
    legacy = [Rule.legacy(d) for d in pack_rules or ()]
    on = lambda r: not r.enabled_by or r.enabled_by in enabled                        # noqa: E731
    place_rules = [r for r in (list(profile.rules) if mode != "pack" else []) + legacy if r.places and on(r)]
    for r in place_rules:
        if _object_hit(r, obj, profile) and _place_hit(r, to, pc, declared):
            return Verdict(r.say or NOT_SAFE, r.id)
    for r in profile.rules:
        if r.receivers and on(r) and _object_hit(r, obj, profile) and _receiver_hit(r, goal, places, profile):
            return Verdict(r.say or NOT_SAFE, r.id)
    return None


def check_goal(goal: dict, places, profile=EMPTY, pack_rules=(), mode: str = "profile", enabled=()) -> Optional[str]:
    """Why the robot will not do ``goal`` (the sentence of the first rule that fires, see :func:`find_rule`), None."""
    v = find_rule(goal, places, profile, pack_rules, mode, enabled)
    return v.say if v else None


__all__ = ["NOT_SAFE", "Verdict", "check_goal", "find_rule", "hazard_class", "receiver_class"]
