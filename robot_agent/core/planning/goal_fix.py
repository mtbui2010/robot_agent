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


def _tokens(s: str) -> list:
    return [w for w in re.findall(r"[\w']+", _norm(s)) if len(w) > 1]


def _is_request(text: str, task: str, listener_names) -> bool:
    """Is a say goal's text the request itself ("tell dad to come ..." relayed whole), not the message?"""
    t = _norm(text)
    if not t:
        return True
    task_t = set(_tokens(task))
    toks = set(_tokens(t))
    if task_t and len(toks & task_t) >= 0.9 * len(task_t):
        return True
    for n in listener_names:
        n = _norm(n)
        if not n or n.startswith("@"):
            continue
        # the listener as the addressee of a tell ("to dad", "tell dad", 아빠한테 / 아빠에게): the request itself;
        # a vocative ("Dinner is ready, dad.") is part of a message
        if re.search(r"\b(?:to|tell|ask|remind|let)\s+(?:the\s+|my\s+|your\s+)?" + re.escape(n) + r"\b", t) or \
                re.search(r"(?<!\w)" + re.escape(n) + r"\s*(?:한테|에게|께|더러)", t):
            return True
    return lexicon.has(lexicon.TELL, t)


def relay_check(goals: list, task: str, places) -> tuple:
    """(goals, question): F4.6. A say goal whose text is the whole request (high overlap with the instruction,
    the listener's name in it, a tell / ask verb) takes the model's ``content`` (the reported message only) when
    that passes the same check, else the robot asks what to say. A priori threshold 0.9 of the instruction tokens, not tuned on any benchmark."""
    out, q = [], None
    for g in goals:
        if g.get("type") != "say":
            out.append(g)
            continue
        g = dict(g)
        who = g.get("to") or g.get("person") or ""
        names = [who] + list((places.env.get(who) or {}).get("aliases") or []) if who else []
        if _is_request(g.get("object"), task, names):
            c = str(g.get("content") or "").strip()
            if c and not _is_request(c, task, names):
                g["object"] = c
            else:
                q = "What should I say?"
        out.append(g)
    return out, q


_KO_ACC = re.compile(r"([\uac00-\ud7a3][\uac00-\ud7a3 ]*?[\uac00-\ud7a3])(?:을|를)(?=\s|$|[.,!?])")
_KO_AND = re.compile(r"\s*(?:과|와|하고|이랑|랑|,)\s*")


def ko_object_issues(goals: list, text: str) -> list:
    """F4.5: every Korean accusative noun group (을 / 를, with 과 / 와 / 하고 / 이랑 chains) must be covered by a
    goal's ``said`` (the person's own words for its object, from the same understand call). Only when the goals
    carry ``said`` (the model filled it); a miss is an ``object`` issue (re-sample, then ask)."""
    saids = [_norm(g.get("said")) for g in goals if g.get("said")]
    if not saids or not re.search(r"[\uac00-\ud7a3]", str(text or "")):
        return []
    out = []
    for m in _KO_ACC.finditer(_norm(text)):
        for part in _KO_AND.split(m.group(1)):
            noun = part.strip().split()[-1] if part.strip() else ""
            if len(noun) < 2:
                continue
            if not any(noun in s or s in noun for s in saids):
                out.append({"kind": "object", "what": noun})
    return [x for k, x in enumerate(out) if x not in out[:k]]


__all__ = ["says_into", "kind_from_destination", "drop_empty", "drop_invented", "relay_check", "ko_object_issues"]
