"""Deterministic fixes of grounded goals from the person's words and the ENV (SAGE-v2, no LLM call).

* F1.1 ``kind_from_destination``: the goal kind follows its destination and the words. An ``on`` goal to an
  openable container that the words put the object INTO ("put the milk in the fridge", 냉장고에 넣어 주세요)
  becomes ``in``; an ``in`` goal to a place that is not a container becomes ``on``. "on top of" / 위에 keeps
  ``on``; "put it away on the shelf" stays ``on`` ("away" is not an into-word, lexicon.PUTIN).
* F3.4 ``drop_empty``: a move goal the model gave no object is dropped (it made "Where should I put the ?").
* F4.4 ``drop_invented``: an ``on`` / ``in`` goal whose OBJECT is an ENV place (an appliance is not carried) is
  dropped, or becomes the open / close goal the words ask for; a ``go`` goal to a place the words never name is
  dropped when other goals remain.

Robot-agnostic: places are the ENV keys (``assist_loop.Places``), the words lists are lexicon.py's.
"""

from __future__ import annotations

import re

from . import lexicon
from .understand import _norm, place_mentions

_EN_INTO = re.compile(r"\b(?:in|into|inside)\s+(?:the\s+|my\s+|your\s+|a\s+|our\s+|her\s+|his\s+)?(?:[\w'-]+\s+){0,2}$")
_EN_ONTOP = re.compile(r"\bon\s+top\s+of\s+(?:the\s+|my\s+|your\s+)?(?:[\w'-]+\s+){0,2}$|\bonto\s+(?:the\s+)?(?:[\w'-]+\s+){0,2}$")
_KO_INTO = re.compile(r"^\s*(?:의\s*)?(?:안|속|안쪽)|^(?:에|에다|에다가)?\s*(?:\S+\s+){0,2}?\S*넣")
_KO_ONTOP = re.compile(r"^\s*(?:의\s*)?위")
_VI_INTO = re.compile(r"\b(?:vào|trong)\s+(?:\S+\s+){0,2}$")


def _mentions_of(key: str, text: str, places) -> list:
    t = _norm(text)
    return [(s, e) for s, e, _p, keys, _src in place_mentions(t, places) if key in keys], t


def says_into(key: str, text: str, places) -> bool:
    """Do the words put something INTO ``key`` (and not on top of it)?"""
    spans, t = _mentions_of(key, text, places)
    for s, e in spans:
        before, after = t[max(0, s - 32):s], t[e:e + 16]
        if _EN_ONTOP.search(before) or _KO_ONTOP.search(after):
            continue
        if _EN_INTO.search(before) or _VI_INTO.search(before) or _KO_INTO.search(after):
            return True
    return False


def kind_from_destination(goals: list, text: str, places, can_putin: bool = True) -> tuple:
    """(goals, notes): F1.1 (see the module doc)."""
    out, notes = [], []
    for g in goals:
        g = dict(g)
        to = g.get("to")
        if g.get("type") == "on" and to in places.env and places.openable(to) and can_putin \
                and says_into(to, text, places):
            g["type"] = "in"
            notes.append(f"on -> in: the words put the {g.get('object')} into {to}")
        elif g.get("type") == "in" and to in places.env and not places.openable(to) \
                and not (places.env.get(to) or {}).get("person"):
            g["type"] = "on"
            notes.append(f"in -> on: {to} is not a container")
        out.append(g)
    return out, notes


def drop_empty(goals: list) -> tuple:
    """(goals, dropped): F3.4."""
    keep = [g for g in goals if g.get("type") not in ("on", "in") or str(g.get("object") or "").strip()]
    return keep, [g for g in goals if g not in keep]


_OPEN = re.compile(r"\bopen\b|열어|열어\s*줘|mở", re.I)
_CLOSE = re.compile(r"\b(?:close|shut)\b|닫아|đóng", re.I)


def drop_invented(goals: list, text: str, places) -> tuple:
    """(goals, dropped): F4.4 (see the module doc)."""
    t = _norm(text)
    said = set().union(*(m[3] for m in place_mentions(t, places))) if place_mentions(t, places) else set()
    out, dropped = [], []
    for g in goals:
        typ, obj = g.get("type"), str(g.get("object") or "")
        if typ in ("on", "in") and obj:
            k = places.ground(obj, obj)
            if k and k in places.env and not (places.env.get(k) or {}).get("person"):
                verb = "open" if _OPEN.search(t) else ("close" if _CLOSE.search(t) else None)
                if verb and not any(h.get("type") == verb and h.get("object") == k for h in goals):
                    out.append({"type": verb, "object": k, "from": "", "to": k})
                dropped.append(g)
                continue
        out.append(g)
    others = [g for g in out if g.get("type") != "go"]
    if others:
        keep = []
        for g in out:
            room = _norm(places.room(g.get("to") or ""))
            if g.get("type") == "go" and g.get("to") in places.env and g.get("to") not in said \
                    and not (room and re.search(r"(?<!\w)" + re.escape(room) + r"(?!\w)", t)):
                dropped.append(g)
                continue
            keep.append(g)
        out = keep
    return out, dropped


__all__ = ["says_into", "kind_from_destination", "drop_empty", "drop_invented"]
