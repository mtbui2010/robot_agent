"""The class of a place, inferred by the robot from the place's own names (key head, aliases, category).

The classes and their words are data of the robot's safety profile (``profile.py``); this is only the matcher. It reads no
benchmark tag. A robot pack's ENV gives names and, optionally, a ``category``; the first class (profile order) whose words
match the names is the class, ``""`` when none does or the profile has no place classes (fail open). ``near_class``: a place of
given classes within a rule's ``within_m`` on the map (the ENV's ``loc``), for a rule that keeps things away from a place.
"""

from __future__ import annotations

import math
import re

from .profile import EMPTY


def _names(places, key) -> list:
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    head = str(key or "").split("@")[0].replace("_", " ")
    return [head] + [str(a) for a in e.get("aliases") or []] + ([str(e["category"])] if e.get("category") else [])


def place_class(places, key, profile=EMPTY) -> str:
    """The class of ENV place ``key`` from its names, '' when none fits (``tags`` are never read)."""
    text = " | ".join(n.lower() for n in _names(places, key))
    for cls, pats in profile.place_classes:
        if any(re.search(p, text) for p in pats):
            return cls
    return ""



def _xy(places, key):
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    loc = e.get("loc") or {}
    return None if e.get("person") or "x" not in loc or "y" not in loc else (float(loc["x"]), float(loc["y"]))


def _room(key) -> str:
    return str(key or "").split("@")[1] if "@" in str(key or "") else ""


def near_class(places, key, classes, radius: float, profile=EMPTY) -> str:
    """An ENV place of one of ``classes`` LESS than ``radius`` metres from place ``key`` on the robot's map (``loc`` x / y; the
    same room when both keys name one; persons are neither), '' when none, no radius or no position (fail open)."""
    xy = _xy(places, key) if radius else None
    if xy is None:
        return ""
    for k in (getattr(places, "env", {}) or {}):
        p = _xy(places, k)
        if k == key or p is None or (_room(k) and _room(key) and _room(k) != _room(key)):
            continue
        if math.dist(xy, p) < radius and place_class(places, k, profile) in classes:
            return k
    return ""


__all__ = ["near_class", "place_class"]
