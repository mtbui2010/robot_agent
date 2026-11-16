"""Option ``drop_search`` (default off): an object the robot carried or let go of and lost is looked for on the floors it passed.

What: (1) a failed hand-over (the robot's hand action) while the belief holds an object and the hand sensor READS empty (a sensor call
that failed for a device reason is no reading): the belief lets go, the engine's REFIND rung looks on the floor place of the person's
room, else of the rooms of the moves since the Pick and of the Pick's place (all of them first in the search, so a miss goes on to the
next), then picks it up and hands over again; once per object. (2) key ``goal_check``: the end-of-run check of an object this run put at
its goal place that does not see it there looks once at the floor of that room; seen there, the goal is unmet and the replan reason
names the goal and where the object is.
Why (classes K4, K6, S2): a hand-over that failed with an empty hand was repeated as is until "the same failure thrice"; an object lost
after a release was never looked for on the floor (the engine's REFIND knows only the room of the place it stands at, never a person's).
Data ``planner.options.drop_search.floors``: ``{"head": "<head of the map's floor places>", "source" | "assumed": true}``; none: nothing.
Switch off: key ``enabled`` (legacy ``drop_search``), off by default. Event: lab flag ``drop_search`` ``{part, object, floors}``.
"""

from __future__ import annotations

from typing import Optional

from robot_agent.contract import FailureCode, SkillResult
from robot_agent.core.options import NEXT, Key, Option, OptionSpec, register
from robot_agent.core.planning import pack_data, persons

FLAG = "drop_search"
# a sensor call that failed for one of these reasons read nothing (contract semantics of the codes, not a robot fact)
NO_READING = frozenset({FailureCode.HW_FAULT, FailureCode.PERCEPTION_UNAVAILABLE, FailureCode.TIMEOUT, FailureCode.SOFTWARE,
                        FailureCode.ESTOP, FailureCode.CANCELLED})


def reading(raw) -> Optional[bool]:
    """The hand sensor's raw result: True (holding: ``isdone``), False (it read empty), None (no result, or it failed for a device reason)."""
    if not isinstance(raw, dict):
        return None
    if raw.get("isdone"):
        return True
    return None if SkillResult.from_legacy({**raw, "isdone": False}).code in NO_READING else False


def floor_head(data) -> Optional[str]:
    """The declared head of the map's floor places (``floor@<room>``), or None: not labelled or no name."""
    head = data.get("head") if pack_data.labelled(data) else None
    return head.strip().lower() if isinstance(head, str) and head.strip() else None


def room_of(places, key, radius: float) -> str:
    """The room of a place key, or of a person entry: its ``spot``, its ``seat``, else the support place nearest its position; '' unknown."""
    key = str(key or "")
    entry = places.env.get(key) or {}
    near = persons.nearest_support(places, key, radius, same_room=False) if persons.is_person(places, key) else None
    for k in (key, entry.get("spot"), entry.get("seat"), near):
        if k and places.room(str(k)):
            return places.room(str(k))
    return ""


def drop_floors(places, head: str, target, completed, radius: float, start=None) -> list:
    """The floor places (head ``head``) of the rooms the object may have fallen in, most likely first: the room of ``target`` (where the
    release failed), of each MoveTo since the last Pick (latest first), and of the place of that Pick (the MoveTo before it, else ``start``)."""
    picks = [i for i, s in enumerate(completed) if s.get("action") == "Pick"]
    last = picks[-1] if picks else -1
    after = [s.get("object") for s in completed[last + 1:] if s.get("action") == "MoveTo"]
    before = [s.get("object") for s in completed[:last + 1] if s.get("action") == "MoveTo"][-1:] or [start]
    rooms = []
    for k in [target] + after[::-1] + before:
        rm = room_of(places, k, radius)
        if rm and rm not in rooms:
            rooms.append(rm)
    return [k for rm in rooms for k in places.keys if places.room(k) == rm and places.head(k).lower() == head]


SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)), Key("goal_check", "bool", False, aliases=("drop_search_goal",)),
          Key("floors", "dict", {}, sources=("manifest",))),
    hooks=("on_step_failed", "on_goal_missed"),
    order=36,
    enabled="enabled",
    doc="A failed hand-over with an empty hand looks for the object on the floors of the rooms it passed; key goal_check: a released object the "
        "end check misses is looked for on that room's floor (needs planner.options.drop_search.floors).",
)


class DropSearch(Option):
    def _head(self) -> Optional[str]:
        return floor_head(pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "floors"))

    def _done(self, run, part) -> set:
        return run.__dict__.setdefault("opt", {}).setdefault(FLAG, {}).setdefault(part, set())

    def on_step_failed(self, run, st, code, reason, a, o):
        """The hand-over failed and the hand reads empty: let go, REFIND on the first floor of ``drop_floors`` (the rest first in the
        search). NEXT when spliced; None: not a hand-over, nothing held, no data or floor, not read as empty, or done for this object."""
        head, held = self._head(), run.world.holding
        if not head or not held or a != self.svc.hand_action() or held in self._done(run, "hand_over"):
            return None
        start = getattr(getattr(run, "world_at_start", None), "arrived", None)
        floors = drop_floors(run.places, head, o, run.completed, persons.PERSON_SEAT_M, start)
        if not floors or reading(self.svc.hand_sensor(run)) is not False:
            return None
        self._done(run, "hand_over").add(held)
        self.svc.let_go(run, "dropped")
        self.svc.prefer_now(run, held, floors)
        self.svc.refind_on(run, st, held, floors[0])
        self.svc.lab(FLAG, part="hand_over", object=held, floors=floors)
        yield run.ev("recover", at_index=run.i, kind=FLAG, msg=f"the {held} is not in the hand after {a} {o}: looking on {', '.join(floors)}")
        return NEXT

    def on_goal_missed(self, run, obj, place):
        """Key ``goal_check``: the run put ``obj`` at ``place`` and the end check does not see it there: one look at the floor of that
        room. Seen: (False, evidence naming the goal and the floor, skill), the floor first in a search for it; else None."""
        head, move = self._head(), self.svc.role("move")
        if not self.cfg.get("goal_check") or not head or not move or (run.placed or {}).get(obj) != place or obj in self._done(run, "goal"):
            return None
        floor = next((k for k in run.places.keys if run.places.room(k) == run.places.room(place) and run.places.head(k).lower() == head), None)
        if not floor or floor == place:
            return None
        self._done(run, "goal").add(obj)
        res = self.svc.run_step(run, {"action": move, "object": floor, "grounded": True})
        seen, skill = self.svc.look_for(run, obj) if res is not None and res.ok else (False, None)
        self.svc.lab(FLAG, part="goal_check", object=obj, floors=[floor], seen=bool(seen))
        if not seen:
            return None
        self.svc.prefer_now(run, obj, [floor])
        return False, f"the goal {obj} at {place} is not met: the {obj} is not there, it was seen on {floor}", skill


register(SPEC, DropSearch)
__all__ = ["FLAG", "SPEC", "DropSearch", "NO_READING", "reading", "floor_head", "room_of", "drop_floors"]
