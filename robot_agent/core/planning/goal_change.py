"""What a MODIFY / ADD / DROP / NEW said during a run does to the goals (proposal 3.3).

The executive (``AssistLoop``) takes the run's pending events at its step boundaries and turns
each one into a :class:`GoalChange` here: the new goal set, and the goal diff (added, removed,
changed). This module is the cheap, deterministic reading of plain English edits; anything it
cannot read with confidence (another language, an object swap, a long sentence) returns None and
the executive asks its understand LLM instead, then tells the person when the change could not
be applied. Nothing robot-specific: places are grounded on the live ENV through ``places``
(``assist_loop.Places``: ``candidates(name, hint)``, ``openable(key)``).

Readings, per clause (split on commas, ``;`` and "but"):

  * a move: "put / bring / take / move / carry X [back] [from P] to / on / in Q" -> X to Q;
  * a drop: "forget / skip / don't bring / never mind / leave X";
  * a bare destination with MODIFY: "the counter instead", "no, on the counter" -> the current goal.

``it`` / ``that`` / ``this`` / ``them`` is the current goal's object: the one in the hand, else the
first goal not reached yet. A move and a drop of the same object in one utterance ("don't bring
it, put the apple back on the desk") is a move.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_PRONOUNS = {"it", "that", "this", "them", "one", "that one", "this one", "those", "these"}
_FILL = r"(?:\s+(?:instead|too|as well|also|please|now|for me|then))*"
_MOVE = re.compile(
    r"\b(?:put|bring|take|move|carry|place|get|leave)\s+(?:the |a |an |my |your )?(?P<obj>[a-z][a-z' ]*?)"
    r"(?:\s+back)?(?:\s+from\s+(?:the |my )?(?P<src>[a-z][a-z ]*?))?(?:\s+back)?"
    r"\s+(?P<prep>on|onto|to|into|in|at)\s+(?:the |my )?(?P<dst>[a-z][a-z ]*?)" + _FILL + r"\s*$")
_DROP = re.compile(
    r"\b(?:forget(?:\s+about)?|skip|never\s+mind|leave\s+out|drop|don'?t\s+(?:bring|take|move|put|get)|"
    r"do\s+not\s+(?:bring|take|move|put|get)|no\s+need\s+(?:for|to\s+(?:bring|move|take))|"
    r"(?:i\s+)?(?:don'?t|no\s+longer)\s+need)\s+(?:the |my |a )?(?P<obj>[a-z][a-z' ]*?)" + _FILL + r"\s*$")
_DEST = re.compile(r"^(?:no\s+|not\s+there\s+|actually\s+|rather\s+|instead\s+)*(?:(?P<prep>on|onto|to|into|in|at)\s+)?"
                   r"(?:the |my )?(?P<dst>[a-z][a-z ]*?)" + _FILL + r"\s*$")
_LEAD = re.compile(r"^(?:(?:actually|oh|ok|okay|um|uh|and|also|then|please|robot|hey|wait|sorry|no|so|"
                   r"and then|after that|one more thing|can you|could you|would you|you can|i want you to)\s*)+")


@dataclass
class GoalChange:
    """The goals after one utterance, and how they differ."""
    goals: list
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    changed: list = field(default_factory=list)          # (old goal, new goal)
    how: str = "parsed"                                   # parsed | llm

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def _norm(text: str) -> str:
    t = str(text or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z' ,;.!?]", " ", t)
    return " ".join(t.split())


def clauses(text: str) -> list:
    """The utterance cut into clauses, leading fillers dropped."""
    out = []
    for c in re.split(r"[,;.!?]|\bbut\b|\band then\b|\bthen\b", _norm(text)):
        c = _LEAD.sub("", c.strip()).strip()
        if c:
            out.append(c)
    return out


def current_goal(goals: list, holding: Optional[str], reached) -> Optional[dict]:
    """The goal being worked on: the held object's, else the first one not reached."""
    for g in goals:
        if holding and g.get("object") == holding:
            return g
    return next((g for g in goals if not reached(g)), goals[0] if goals else None)


def _object_goal(name: str, goals: list, cur: Optional[dict]) -> Optional[dict]:
    n = " ".join(w for w in (name or "").split() if w not in ("the", "a", "an", "my"))
    if n in _PRONOUNS or not n:
        return cur
    for g in goals:
        o = g.get("object") or ""
        if g.get("type") in ("on", "in") and (o == n or n in o.split() or o in n):
            return g
    return None


_JOIN = {"and", "or", "to", "on", "onto", "in", "into", "from", "then", "with", "but"}


def _dest(places, name: str, hint: str) -> Optional[str]:
    """One ENV place the phrase names, else None (a phrase that runs on, "the table and the banana
    to the sofa", is never grounded on one of its words)."""
    words = (name or "").split()
    if not words or _JOIN & set(words):
        return None
    c = places.candidates(name, hint)
    return c[0] if len(c) == 1 and not c[0].startswith("@") and places.said(c[0], name) else None


def read(text: str, intent: str, goals: list, places, holding: Optional[str] = None,
         reached=lambda g: False) -> Optional[GoalChange]:
    """The goals after ``text`` (intent: modify / add / drop / new), or None when the words
    are not read with confidence. For ``new`` the result's goals are the new task's alone."""
    goals = [dict(g) for g in goals]
    cur = current_goal(goals, holding, reached)
    moves, drops = [], []
    for c in clauses(text):
        m = _MOVE.search(c)
        if m:
            moves.append(m)
            continue
        d = _DROP.search(c)
        if d:
            drops.append(d.group("obj"))
            continue
        if intent == "modify" and cur is not None:
            m = _DEST.match(c)
            k = _dest(places, m.group("dst"), text) if m else None
            if k:
                moves.append({"obj": "it", "src": None, "prep": m.group("prep") or "on", "dst": m.group("dst")})
                continue
        return None                                       # a clause it cannot read: ask the LLM
    if not moves and not drops:
        return None
    if intent == "new":
        return _new_task(moves, places, text, drops)
    return _edit(goals, cur, moves, drops, places, text, intent)


def _goal_for(obj: str, src: Optional[str], prep: str, dst_key: str, places) -> dict:
    t = "in" if prep in ("in", "into") and places.openable(dst_key) else "on"
    src = (src or "").strip()
    return {"type": t, "object": obj, "from": "" if src == dst_key else src, "to": dst_key}


def _new_task(moves, places, text, drops) -> Optional[GoalChange]:
    if drops:
        return None
    out = []
    for m in moves:
        obj = " ".join(w for w in m["obj"].split() if w not in ("the", "a", "an", "my"))
        k = _dest(places, m["dst"], text)
        if not k or obj in _PRONOUNS:
            return None
        src = _dest(places, m["src"], text) if m["src"] else ""
        out.append(_goal_for(obj, src or "", m["prep"], k, places))
    return GoalChange(goals=out, added=list(out))


def _edit(goals, cur, moves, drops, places, text, intent) -> Optional[GoalChange]:
    ch = GoalChange(goals=goals)
    moved = set()
    for m in moves:
        k = _dest(places, m["dst"], text)
        if not k:
            return None
        g = _object_goal(m["obj"], goals, cur)
        if g is None:
            obj = " ".join(w for w in m["obj"].split() if w not in ("the", "a", "an", "my"))
            if obj in _PRONOUNS or not obj:
                return None
            src = _dest(places, m["src"], text) if m["src"] else ""
            new = _goal_for(obj, src or "", m["prep"], k, places)
            goals.append(new); ch.added.append(new); moved.add(obj)
            continue
        moved.add(g["object"])
        if g.get("to") != k:
            old = dict(g)
            g.update(_goal_for(g["object"], g.get("from"), m["prep"], k, places))
            ch.changed.append((old, dict(g)))
    for name in drops:
        g = _object_goal(name, goals, cur)
        if g is None:
            return None
        if g["object"] in moved:
            continue                                      # "don't bring it, put it back on the desk"
        goals.remove(g); ch.removed.append(g)
    return None if ch.empty and intent != "modify" else ch


__all__ = ["GoalChange", "read", "clauses", "current_goal"]
