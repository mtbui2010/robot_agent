"""The class of a person entry of the ENV, inferred by the robot from the entry's own names.

Clean-room: no benchmark tag or text is read. The classes, their words and the ENV fields matched (default ``age_group``,
``category``) are data of the robot's safety profile (``profile.py``). An entry that is not a person, and a profile with no
receiver classes, give ``""`` (fail open).
"""

from __future__ import annotations

import re

from .profile import EMPTY


def _names(places, key, fields) -> list:
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    head = str(key or "").split("@")[0].replace("_", " ")
    return [head] + [str(a) for a in e.get("aliases") or []] + [str(e[f]) for f in fields if e.get(f)]


def receiver_class(places, key, profile=EMPTY) -> str:
    """The first receiver class (profile order) whose words name ENV person entry ``key`` (key head, aliases, the class's
    ``fields``), else ''."""
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    if not e.get("person"):
        return ""
    for cls, pats, fields in profile.receiver_classes:
        text = " | ".join(n.lower() for n in _names(places, key, fields))
        if any(re.search(p, text) for p in pats):
            return cls
    return ""


__all__ = ["receiver_class"]
