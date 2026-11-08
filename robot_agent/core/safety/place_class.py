"""The class of a place, inferred by the robot from the place's own names (key head, aliases, category).

The classes and their words are data of the robot's safety profile (``profile.py``); this is only the matcher. It reads no
benchmark tag. A robot pack's ENV gives names and, optionally, a ``category``; the first class (profile order) whose words
match the names is the class, ``""`` when none does or the profile has no place classes (fail open).
"""

from __future__ import annotations

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


__all__ = ["place_class"]
