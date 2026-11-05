"""Dialogue convergence (SAGE-v2, R4b item 2, failure class "clarify / read-back loops that give up").

What the loops of the 50-task fresh slice had in common (root causes, not task ids):

* **A correction rewrote every goal.** "No, put the fruit on the desk" after a read-back with two goals was
  applied as "the destination is the desk" to ALL move goals (``pin_to``), or the whole task was understood
  again with the correction added and the model moved the other object too. The person fixed one goal and
  broke another, three read-backs in a row, then the cap ended the run.
* **The same reading was read back again after it was rejected** (the request said again gave the same goals),
  so the person said "no" to the same sentence twice.
* **The cap counted every question**, also the ones that changed the goals. Three questions that each brought
  the robot closer ended the run like three that brought nothing.
* **Near-identical questions** ("What do you mean by X?" / "Could you clarify what you mean by X?") passed the
  word-for-word check.

This module holds the pure helpers (no LLM, no ENV mutation); ``AssistLoop`` uses them:

* ``goal_sig(goals)``: a hashable key of what a goal set does, to tell a new reading from a repeated one.
* ``correction_edit(goals, texts, places)``: the destination change a short correction asks for ("the white
  glass to me", "the mug on the counter"), as an edit of ONE goal; ``("same", [])`` when the goal already has
  that destination (the complaint is about something else), ``("none", [])`` when the words do not name exactly
  one object and one destination (the old path stands).
* ``same_question(a, b)``: two questions that ask the same thing in other words.

Robot-agnostic: objects are the goals' own names (and the user-language names the loop attached), places are the
ENV (``assist_loop.Places``); the speaker's words are persons.SPEAKER_FORMS. No benchmark text.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from . import persons

_HANGUL = re.compile(r"[가-힣]")
# words that make a repeated question a different wording only (politeness, hedges, the sorry prefix)
_FILLER = {"sorry", "please", "clarify", "again", "i", "may", "have", "misheard", "you", "could", "would", "can",
           "the", "a", "an", "did", "do", "does", "exactly", "just", "so", "me", "us", "your", "to", "is", "are"}
_KO_SPEAKER = tuple(w for w in persons.SPEAKER_FORMS if _HANGUL.search(w) and len(w) > 1)
_EN_SPEAKER = {w for w in persons.SPEAKER_FORMS if w.isascii() and " " not in w} - {"user", "person", "you", "here"}


def goal_sig(goals) -> tuple:
    """What a goal set asks for, order kept: (type, object, from, to, hand, near, said). Two goal sets with the
    same key read back to the same sentence."""
    out = []
    for g in goals or []:
        out.append((str(g.get("type") or ""), str(g.get("object") or "").lower(), str(g.get("from") or ""),
                    str(g.get("to") or ""), bool(g.get("hand")), str(g.get("near_person") or ""),
                    str(g.get("said") or "").lower()))
    return tuple(out)


def _content(q) -> frozenset:
    return frozenset(w for w in re.findall(r"[^\W_]+", str(q or "").lower()) if w not in _FILLER)


def same_question(a, b) -> bool:
    """Do two questions ask the same thing? They hold the same words once the fillers are taken out (a question
    that names another object or place is another question). Needs at least three content words each."""
    ca, cb = _content(a), _content(b)
    return len(ca) >= 3 and ca == cb


# ── which goal a correction is about, and where it sends the object ──────────────────────────────────────────

def _toks(s) -> list:
    return re.findall(r"[a-z0-9]+|[가-힣]+", str(s or "").lower())


def _close(a: str, b: str) -> bool:
    """A word the user said and a name word of a goal: equal, or one letter off (a heard 'grass' for 'glass')."""
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8


def _ko_head_hit(name: str, text: str) -> bool:
    """The last Hangul word of ``name`` in the Hangul ``text`` (particles attach, so a prefix of a word counts; a
    one-syllable slip of the recognizer is allowed for a name of three or more syllables)."""
    heads = _toks(" ".join(t for t in _toks(name) if _HANGUL.search(t)))
    if not heads:
        return False
    head = heads[-1]
    for w in _toks(text):
        if not _HANGUL.search(w):
            continue
        if head in w:
            return True
        if len(head) >= 3 and len(w) >= len(head) and SequenceMatcher(None, head, w[:len(head)]).ratio() >= 0.66:
            return True
    return False


def _score(goal: dict, texts) -> int:
    """How strongly ``texts`` name the object of a move goal: 0 = not named, 2 = its head noun, +1 per
    modifier word (a colour). English names are compared by word, Korean names by the head syllables."""
    if goal.get("type") not in ("on", "in") or not goal.get("object"):
        return 0
    obj = _toks(goal["object"])
    best = 0
    for t in texts:
        words = [w for w in _toks(t) if not _HANGUL.search(w)]
        sc = 0
        if obj and any(_close(obj[-1], w) for w in words):
            sc = 2 + sum(1 for m in obj[:-1] if any(_close(m, w) for w in words))
        for key in ("said", "name_local"):
            if goal.get(key) and _HANGUL.search(str(goal[key])) and _ko_head_hit(goal[key], t):
                sc = max(sc, 2 + sum(1 for m in _toks(goal[key])[:-1] if _HANGUL.search(m) and m in str(t)))
        best = max(best, sc)
    return best


def named_goals(goals, texts) -> list:
    """Indexes of the move goals the correction names: those with the best score (>= 2), all of one object
    name. [] when nothing is named or when it names two different objects (one destination cannot tell)."""
    sc = [(_score(g, texts), i) for i, g in enumerate(goals)]
    top = max((s for s, _i in sc), default=0)
    if top < 2:
        return []
    idx = [i for s, i in sc if s == top]
    if len({str(goals[i].get("object")).lower() for i in idx}) != 1:
        return []
    others = [i for s, i in sc if s >= 2 and i not in idx and
              str(goals[i].get("object")).lower() != str(goals[idx[0]].get("object")).lower()]
    return [] if others else idx


def speaker_said(texts) -> bool:
    for t in texts:
        if _EN_SPEAKER & set(_toks(t)) or any(w in str(t) for w in _KO_SPEAKER):
            return True
    return False


def place_said(texts, places) -> Optional[str]:
    """The one ENV place the words name (a person entry is no place), else None."""
    found = set()
    for t in texts:
        c = [k for k in places.candidates(t, t) if not k.startswith("@") and not persons.is_person(places, k)]
        found.update(c)
    return next(iter(found)) if len(found) == 1 else None


def correction_edit(goals, texts, places) -> tuple:
    """The edit a short correction asks for. ``texts``: the correction as the person said it (and its English).

    * ``("edit", [(i, dest), ...])``: the move goals ``i`` go to ``dest`` (an ENV place key, or ``"@speaker"``).
    * ``("same", [])``: the correction names an object and a destination the read-back already had: what is
      wrong is something else.
    * ``("none", [])``: no single object and single destination can be read from the words."""
    texts = [t for t in texts if t]
    idx = named_goals(goals, texts)
    if not idx:
        return "none", []
    me = speaker_said(texts)
    place = place_said(texts, places)
    if me == (place is not None):                  # neither, or both: not a single destination
        return "none", []
    sk = persons.speaker_key(places)
    if me:
        if sk is None:
            return "none", []
        dest = "@speaker"
        todo = [i for i in idx if sk not in (goals[i].get("to"), goals[i].get("near_person"))]
    else:
        dest = place
        todo = [i for i in idx if goals[i].get("to") != place]
    if not todo:
        return "same", []
    return "edit", [(i, dest) for i in todo]
