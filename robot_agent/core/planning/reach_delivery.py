"""Option ``reach_delivery`` (default off): a delivery to a person ends within their reach, or in their hand.

The problem (round 14, dev 033 EN, dev 047 KO): a person entry of the ENV (``persons.py``) is a SNAPSHOT: ``loc`` is where
the robot last saw the person and ``spot`` the support surface beside them then. The plan puts the object down at the spot
(``Place <person>``), the put-down succeeds, the goal check finds the object at the spot, and the run says done; the person
had left the spot meanwhile (to another place in the room, to another room), so the object is not within their reach. Nothing
the robot can read says so: the ENV of a person is not updated while the robot works, and a put-down is a success on its own
terms.

What the option does, all from data (the ENV entry and the pack's manifest ``planner.person_snapshot``; no name of an object,
place or person, no word list):

* a snapshot is trusted for ``trust_s`` seconds (pack data; none declared = no limit, as before). A put-down for a person
  whose snapshot is older than that is done by the robot's hand-over action instead (a hand-over reaches the person wherever
  they are in the room); a robot with no hand-over action keeps the put-down;
* a person entry with a position but no ``spot`` field (the pack cannot say where a reachable surface is) is a hand-over
  too, never "the support surface nearest to them", when the robot can hand over;
* the end-of-run goal check reads the person's entry again: when the entry no longer has the spot the object was put down
  at (a pack that updates its entries) or has none, the object is not within reach and the goal is NOT met.

``planner.person_snapshot`` (a mapping, may be empty; absent = the option does nothing): ``trust_s`` (seconds, > 0: how long a
person entry is trusted).
"""

from __future__ import annotations

from typing import Optional

from . import persons

FLAG = "reach_delivery"
DATA = "person_snapshot"         # the pack's manifest planner.person_snapshot: {trust_s: seconds}


def config(planner_data) -> Optional[dict]:
    """The pack's declaration, ``{"trust_s": float | None}``, or None when it declares none (the option then does nothing)."""
    v = (planner_data or {}).get(DATA) if isinstance(planner_data, dict) else None
    if not isinstance(v, dict):
        return None
    try:
        t = float(v.get("trust_s"))
    except (TypeError, ValueError):
        t = None
    return {"trust_s": t if t is not None and t > 0 else None}


def recipient(places, step: dict, goals) -> Optional[str]:
    """The person entry a put-down step is for: the step's own object when it is a person entry, else the person
    of the ``on`` goal that the put-down at a place serves (``near_person``). None when the step is for nobody."""
    if step.get("action") not in ("Place",):
        return None
    o = step.get("object")
    if persons.is_person(places, o):
        return o
    for g in goals or ():
        if g.get("type") == "on" and g.get("to") == o and g.get("near_person") and persons.is_person(places, g["near_person"]):
            return g["near_person"]
    return None


def stale(cfg: Optional[dict], elapsed_s: float) -> bool:
    """Is a person snapshot taken ``elapsed_s`` ago too old to put an object down by? Never without a declared ``trust_s``."""
    return bool(cfg) and cfg["trust_s"] is not None and elapsed_s > cfg["trust_s"]


def spot_of(places, key) -> Optional[str]:
    """The reachable surface the person's entry declares now (None: it declares none or has no such field)."""
    return (places.env.get(key) or {}).get("spot") or None


def has_spot_field(places, key) -> bool:
    return "spot" in (places.env.get(key) or {})


def hand_step(release_action: str, key: str) -> dict:
    return {"action": release_action, "object": key, "grounded": True, "reach_hand": True}


def left_spot(places, key, put_at: Optional[str]) -> Optional[str]:
    """Why a delivery put down at ``put_at`` (the person's declared spot at that time) no longer holds, from the
    person's entry now; None when nothing says it does not hold (no spot field: nothing to compare)."""
    if put_at is None or not has_spot_field(places, key):
        return None
    now = spot_of(places, key)
    if now == put_at:
        return None
    return (f"their reachable surface is now {now}, not {put_at}" if now else
            f"they have no reachable surface now (it was {put_at})")


__all__ = ["FLAG", "DATA", "config", "recipient", "stale", "spot_of", "has_spot_field", "hand_step", "left_spot"]
