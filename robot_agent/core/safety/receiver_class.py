"""The class of a person entry of the ENV, inferred by the robot from the entry's own names (option ``hazard_receiver``).

Clean-room: no benchmark tag or text is read. The class ``vulnerable_person`` is a person who must not be handed a hazardous
product (a young child). Its words are language-pack data (``patterns.person_vulnerable`` of every pack, one regex each);
an ENV entry may also declare ``age_group`` (any text, matched against the same words). An entry that is not a person, a
language with no pack and an ENV with neither names nor field give ``""`` (fail open).
"""

from __future__ import annotations

import re


def _names(places, key) -> list:
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    head = str(key or "").split("@")[0].replace("_", " ")
    return [head] + [str(a) for a in e.get("aliases") or []] + [str(e[f]) for f in ("age_group", "category") if e.get(f)]


def receiver_class(places, key) -> str:
    """``vulnerable_person`` when ENV person entry ``key`` is named (key head, aliases, ``age_group``) by a word of the packs'
    ``person_vulnerable`` list, else ''."""
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    if not e.get("person"):
        return ""
    from robot_agent.core.planning import langdata
    pats = langdata.pattern_list("person_vulnerable")
    text = " | ".join(n.lower() for n in _names(places, key))
    return "vulnerable_person" if any(re.search(p, text) for p in pats) else ""


__all__ = ["receiver_class"]
