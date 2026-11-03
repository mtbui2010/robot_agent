"""People in the ENV: who the speaker is, where a delivery to a person goes, how people are named (SAGE-v2).

The optional ENV contract (docs/env.md): an entry may carry

* ``person: <id>``: the entry is a person, not a place;
* ``speaker: true``: the person who gives the task (without it: the person entry whose aliases contain a
  speaker word, "me" / 나한테 / tôi);
* ``seat: <place key>``: where the person sits or lies, when the robot's perception knows it;
* ``spot: <place key> | null``: a reachable support surface beside the person; ``null`` = the robot looked and
  found none (a hand-over then); ABSENT = the robot pack has no such perception (the nearest support place
  within ``person_seat_m`` of the person, else a hand-over);
* ``loc``: the person's LAST-SEEN position. A person entry without ``loc`` is not tracked (the robot does not
  know where they are): it asks.

Place entries may carry ``surface: true | false`` (a support surface things can be put on: a table, a shelf; a
seat, a bed or the floor is not one). Without the field anywhere, every non-openable place counts.

Robot-agnostic: everything comes from the ENV; an ENV without these fields keeps the earlier behaviour.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from . import lexicon

# the speaker's own words for themself as a destination (EN, KO, VI), compared after normalising
SPEAKER_FORMS = {"me", "myself", "here", "user", "the user", "you", "person", "나", "저", "나한테", "저한테", "나에게",
                 "저에게", "내게", "제게", "여기", "여기로", "tôi", "mình", "đây", "cho tôi"}
PERSON_SEAT_M = 1.0          # a priori: one arm's length from where the person sits / stands (not tuned)


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").split())


def is_person(places, key) -> bool:
    return bool((places.env.get(key) or {}).get("person"))


def person_keys(places) -> list:
    return [k for k in places.keys if is_person(places, k)]


def is_speaker_word(v) -> bool:
    w = _norm(v)
    return w in SPEAKER_FORMS or re.sub(r"^(?:the|a|an|my|your)\s+", "", w) in SPEAKER_FORMS


def speaker_key(places) -> Optional[str]:
    """The ENV entry of the person who gives the task (``speaker: true``, else a person entry whose aliases
    hold a speaker word), None when the ENV names none."""
    ps = person_keys(places)
    flagged = [k for k in ps if (places.env[k] or {}).get("speaker")]
    if flagged:
        return flagged[0]
    by_alias = [k for k in ps if any(is_speaker_word(a) for a in (places.env[k].get("aliases") or []))]
    return by_alias[0] if len(by_alias) == 1 else None


def tracked(places, key) -> bool:
    """Does the robot know where person ``key`` is (an ENV ``loc``, last seen)?"""
    loc = (places.env.get(key) or {}).get("loc") or {}
    return "x" in loc and "y" in loc


def _xy(places, key):
    loc = (places.env.get(key) or {}).get("loc") or {}
    return (float(loc["x"]), float(loc["y"])) if "x" in loc and "y" in loc else None


def has_surface_field(places) -> bool:
    return any("surface" in (v or {}) for v in places.env.values())


def is_support(places, key) -> bool:
    """Can things be put down on ``key``? Its ``surface`` flag; without the field anywhere, any place that is
    not a person and does not open."""
    e = places.env.get(key) or {}
    if e.get("person"):
        return False
    if has_surface_field(places):
        return bool(e.get("surface"))
    return not places.openable(key)


def nearest_support(places, key, radius: float, same_room: bool = True) -> Optional[str]:
    """The support place nearest to ``key``'s position within ``radius`` (in its room when the keys have rooms),
    else None. ``key`` itself when it is a support place."""
    if key in places.env and is_support(places, key):
        return key
    xy = _xy(places, key)
    if xy is None:
        return None
    room = places.room(key) if hasattr(places, "room") else ""
    best = None
    for k in places.keys:
        if k == key or not is_support(places, k) or (same_room and room and places.room(k) not in ("", room)):
            continue
        p = _xy(places, k)
        if p is None:
            continue
        d = math.dist(p, xy)
        if d <= radius + 1e-9 and (best is None or d < best[0]):
            best = (d, k)
    return best[1] if best else None


def seat_person(places, key, radius: float = PERSON_SEAT_M) -> Optional[str]:
    """The person entry an answer naming the seat / bed ``key`` refers to: the entry whose ``seat`` is ``key``,
    else the one whose position is within ``radius`` of it; None."""
    ps = person_keys(places)
    by_seat = [p for p in ps if (places.env[p] or {}).get("seat") == key]
    if by_seat:
        return by_seat[0]
    xy = _xy(places, key)
    if xy is None:
        return None
    near = [(math.dist(_xy(places, p), xy), p) for p in ps if tracked(places, p)]
    near = [x for x in near if x[0] <= radius + 1e-9]
    return sorted(near)[0][1] if near else None


def wants_hand(text: str, robot=None) -> bool:
    """Do the words ask for a hand-over (lexicon.HAND, plus the robot's i18n ``hand_words``)?"""
    return lexicon.has(lexicon.HAND, text, robot, "hand_words")


def delivery(places, key, text: str, robot=None, radius: float = PERSON_SEAT_M) -> tuple:
    """How an ``on`` goal to the person entry ``key`` is delivered: ("hand", key) or ("place", surface key).
    The words ask for the hand -> hand; ``spot`` given -> place there (the robot pack's ENV entry stands at it);
    ``spot: null`` -> hand; no ``spot`` field -> the support place nearest to the person within ``radius``,
    else hand."""
    e = places.env.get(key) or {}
    if wants_hand(text, robot):
        return "hand", key
    if "spot" in e:
        return ("place", key) if e.get("spot") else ("hand", key)
    s = nearest_support(places, key, radius, same_room=False) if tracked(places, key) else None
    return ("place", s) if s else ("hand", key)


def place_in_answer(places, text: str) -> Optional[str]:
    """The ENV place (or ``@room``) an answer such as "I'm on the sofa" / 침실에 있어요 names, None when it names
    none or several. Person entries are not places here."""
    from .understand import place_mentions
    keys = set()
    for _s, _e, _p, ks, _src in place_mentions(text, places):
        ks = {k for k in ks if not is_person(places, k)}
        if len(ks) == 1:
            keys |= ks
    if len(keys) == 1:
        return next(iter(keys))
    k = places.ground(text, text) if hasattr(places, "ground") else None
    if k and not is_person(places, k):
        return k
    t = _norm(text)
    rooms = [r for r in (places.rooms() if hasattr(places, "rooms") else ()) if r and re.search(
        r"(?<!\w)" + re.escape(_norm(r)) + r"(?!\w)", t)]
    return "@" + rooms[0] if len(rooms) == 1 else None


def surface_near(places, at: str, radius: float) -> Optional[str]:
    """Where to put something down for a person who said they are at ``at``: ``at`` itself when it is a support
    surface, else the support place nearest to it within ``radius`` in its room; None (a hand-over)."""
    if not at or at.startswith("@"):
        return None
    return nearest_support(places, at, radius, same_room=True)


__all__ = ["SPEAKER_FORMS", "PERSON_SEAT_M", "is_person", "person_keys", "is_speaker_word", "speaker_key",
           "tracked", "is_support", "nearest_support", "seat_person", "wants_hand", "delivery", "place_in_answer",
           "surface_near"]
