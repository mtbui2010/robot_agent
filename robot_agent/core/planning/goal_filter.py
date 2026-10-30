"""Understood goals the person's words do not ask for, removed before the goal validator (deterministic).

The understand model runs at temperature 0 (docs/dialog.md), so a misreading of a sentence is no longer
a coin flip that a re-sample hides: it comes back every time, and the read-back then asks the person a
question nobody answers (T2 H02, H07 after the v2 dialog stream). Three readings are corrected here from
the person's own words, without an LLM call:

* **a stated location is not a goal**: "The spoon is in the kitchen drawer. Bring it to the sofa." The
  model made ``in spoon -> drawer`` a goal next to ``on spoon -> sofa`` and the validator asked "Where
  should the spoon go in the end?". When the words say "<object> is in / on <place>" and the object has
  another destination, that goal is the object's source: it is dropped and the other goal gets it as its
  ``from`` (when it has none);
* **a negated object is not a goal**: "... but don't touch the mug" / "except the plate" / "not the
  orange": a move goal for an object the words name only inside a negation is dropped (the coverage
  check already ignores negated words, ``understand._active``);
* **a 'go' to where a move goal puts something** is the same trip: dropped.

Robot-agnostic: places are the ENV keys, phrases come from ``understand.place_mentions``.
"""

from __future__ import annotations

import re

from .understand import _active, _find, _norm, place_mentions

_STATED = r"(?:is|are|was|lies|sits|stays)\s+(?:still\s+|now\s+|already\s+)?(?:in|on|inside|at|in\s+the\s+middle\s+of)\s+"
_MOVES = ("on", "in")


def _stated_at(obj: str, text: str, places) -> set:
    """ENV keys of the places the words say ``obj`` is at ("the spoon is in the kitchen drawer")."""
    t = _norm(text)
    out = set()
    mentions = place_mentions(t, places)
    for _s, e in _find(_norm(obj), t):
        m = re.match(r"\s+" + _STATED, t[e:])
        if not m:
            continue
        start = e + m.end()
        for s2, _e2, _p, keys, _src in mentions:
            if start <= s2 <= start + 12:              # "the", "my", a room word before the place
                out |= set(keys)
                break
    return out


_DEST = re.compile(r"\b(?:in|into|inside|on|onto|to)\s+(?:the\s+|my\s+|your\s+)?(?:[a-z']+\s+){0,2}$")


def _named_as_destination(key: str, text: str, places) -> bool:
    """Do the words put something in / on ``key`` ("put it back in the drawer"), not only take from it
    ("out of the drawer") or name it ("close the drawer")?"""
    t = _norm(text)
    for s, _e, _p, keys, is_src in place_mentions(t, places):
        before = t[max(0, s - 30):s]
        if key in keys and not is_src and _DEST.search(before) and not re.search(_STATED + r"(?:the\s+)?\S*\s*$", before):
            return True
    return False


def stated_sources(goals: list, text: str, places) -> tuple:
    """(goals, notes): a move goal to where the object is, while the object has another destination,
    becomes that destination goal's source. "Where it is": the words say so ("the spoon is in the
    drawer"), or another goal takes it from there and the words never name the place as a destination
    ("take the spoon out of the drawer, put it on the counter, and close the drawer")."""
    goals, notes = [dict(g) for g in goals], []
    for g in list(goals):
        o, k = g.get("object"), g.get("to")
        if g.get("type") not in _MOVES or not o or not k:
            continue
        others = [h for h in goals if h is not g and h.get("type") in _MOVES and h.get("object") == o
                  and h.get("to") and h.get("to") != k]
        taken = any(h.get("from") == k for h in others) and not _named_as_destination(k, text, places)
        if others and (taken or k in _stated_at(o, text, places)):
            goals.remove(g)
            for h in others:
                if not h.get("from"):
                    h["from"] = k
            notes.append(f"the {o} is at {k} (said): its source, not a goal")
    return goals, notes


def negated(goals: list, english: str) -> tuple:
    """(goals, notes): move goals whose object the English words name only inside a negation."""
    t, act = _norm(english), _active(english)
    keep, notes = [], []
    for g in goals:
        o = _norm(g.get("object"))
        if g.get("type") in _MOVES and o and _find(o, t) and not _find(o, act):
            notes.append(f"the {o} is named only in a negation: not a goal")
            continue
        keep.append(g)
    return keep, notes


def same_trip(goals: list) -> tuple:
    """(goals, notes): a 'go' goal to the destination of a move goal is dropped."""
    dest = {g.get("to") for g in goals if g.get("type") in _MOVES and g.get("to")}
    keep = [g for g in goals if not (g.get("type") == "go" and g.get("to") in dest)]
    return keep, [f"go to {g.get('to')}: the same trip as a move goal" for g in goals if g not in keep]


def _keep_some(goals: list, fn) -> tuple:
    g2, notes = fn(goals)
    return (g2, notes) if g2 else (goals, [])


def before_grounding(goals: list, english: str) -> tuple:
    """The negation filter on the model's goals (raw names): (goals, notes). Nothing is dropped when
    it would leave no goal."""
    return _keep_some(list(goals), lambda g: negated(g, english))


def after_grounding(goals: list, words: str, places) -> tuple:
    """The stated-location and same-trip filters on grounded goals: (goals, notes). ``words``:
    everything the person said."""
    out, notes = _keep_some(list(goals), lambda g: stated_sources(g, words, places))
    out, n2 = _keep_some(out, same_trip)
    return out, notes + n2


__all__ = ["stated_sources", "negated", "same_trip", "before_grounding", "after_grounding"]
