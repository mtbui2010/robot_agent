"""Option ``effect_check`` (default off): what a failed release left in the hand is READ from the robot's declared hand sensor, not guessed.

What: (1) a release step (an action the pack declares a release, the hand-over too) that FAILED while the belief holds an object makes the
robot read its holding sensor once (engine service ``hand_holds``). Empty: the belief lets go of the object and the contract's DROPPED rule
looks for it on the floor place of the room (engine service ``let_go``, the engine's own refind). Holding, or cannot be read: the belief keeps
the object and the failure goes on as before. The failure's code is never read as "the hand is empty". (2) Every read of the holding sensor the
engine makes (``_holding``) is three-valued through the service ``hand_reading``: a call that failed with a code the pack declares as "cannot
read" keeps the caller's default instead of reading as "empty". (3) The goal check of an object this run released at a place puts the floor place
of that place's room first in the search for it (``prefer_now``): when the check misses it there, the next look is that floor.
Why (classes drop_recovery, grasp_misread, silent_drop): a hand-over that failed with an empty hand was retried as is while the belief still held
the object; a fault of the grasp sensor read as "empty", so the robot searched for the object in its own hand; an object that fell when it was
put down was searched for everywhere but the floor next to its place. A failed hand-over more often means the person did not take it (the object
is still held), so only the sensor decides, never the code.
How to switch it off: key ``enabled`` (legacy ``effect_check``), off by default. With no labelled ``planner.options.effect_check.effects`` it does
nothing; a part that is not declared (no sensor skill, or one that is not the robot's holding sensor; no releases; no floor name) does nothing.

Pack data ``planner.options.effect_check.effects``::

    {"sensor": {"skill": "<the robot's holding sensor>", "unknown": ["<failure code that means: cannot read>", ...]},
     "releases": ["<Action>", ...], "floor": "<head of the map's floor places, '<head>@<room>'>", "source": "<label>"}   # or "assumed": true

The result of any other skill (a speech step's listeners, a hand-over's own fields) is not read. Hooks ``on_start``, ``on_step_failed``,
``on_goal_check`` (order 88), service ``hand_reading``. Lab flag ``effect_check``: ``{part, ...}`` (``release`` with the reading, ``unreadable``,
``floor``), once per distinct event in a run.
"""

from __future__ import annotations

from robot_agent.contract import FailureCode, SkillResult
from robot_agent.core.options import NEXT, Key, Option, OptionSpec, register
from robot_agent.core.options.config import resolve_key

READING = {True: "holding", False: "empty", None: "unknown"}


def _names(v) -> tuple:
    return tuple(x.strip() for x in v if isinstance(x, str) and x.strip()) if isinstance(v, (list, tuple)) else ()


def effects_of(data) -> dict:
    """``{sensor, unknown, releases, floor}`` from the declared data; {} when it has no label (source or assumed) or is no mapping."""
    if not isinstance(data, dict) or not (isinstance(data.get("source"), str) and data["source"].strip() or data.get("assumed") is True):
        return {}
    sensor = data.get("sensor") if isinstance(data.get("sensor"), dict) else {}
    floor = data.get("floor")
    return {"sensor": sensor.get("skill") if isinstance(sensor.get("skill"), str) and sensor["skill"].strip() else None,
            "unknown": {c for c in map(FailureCode.try_parse, _names(sensor.get("unknown"))) if c is not None},
            "releases": set(_names(data.get("releases"))),
            "floor": floor.strip().lower() if isinstance(floor, str) and floor.strip() else None}


def reading(raw, unknown) -> object:
    """The sensor's answer in a result dict: True (holding: ``isdone``), None (cannot read: it failed with a code in ``unknown``), False (empty)."""
    if not isinstance(raw, dict):
        return None
    if raw.get("isdone"):
        return True
    return None if SkillResult.from_legacy({**raw, "isdone": False}).code in unknown else False


SPEC = OptionSpec(
    name="effect_check",
    keys=(Key("enabled", "bool", False, aliases=("effect_check",)), Key("effects", "dict", {}, sources=("manifest",))),
    hooks=("on_start", "on_step_failed", "on_goal_check"),
    services=("hand_reading",),
    order=88,
    enabled="enabled",
    doc="A failed release reads the declared hand sensor (empty: let go, refind on the room's floor; holding or unreadable: keep it); a sensor fault is no 'empty'; "
        "an object released here and missed at the goal check is looked for on that room's floor first.",
)


class EffectCheck(Option):
    def _data(self) -> dict:
        """The declared data as it is now (read again each time: a manifest overlay merged after the loop was built is seen)."""
        planner = getattr(getattr(self.svc.robot, "manifest", None), "planner", None)
        return effects_of(self.cfg.get("effects") or resolve_key(SPEC, SPEC.key("effects"), {}, {}, planner))

    def _tell(self, part, **info) -> None:
        """One lab event per distinct event in a run (``_holding`` is read several times per step)."""
        key = (part, tuple(sorted(info.items())))
        told = self.__dict__.setdefault("_told", set())
        if key not in told:
            told.add(key)
            self.svc.lab(SPEC.name, part=part, **info)

    def on_start(self, run) -> None:
        self._told = set()

    def hand_reading(self, skill, raw, default):
        """Service: the declared sensor's reading of ``raw`` (True / False), ``default`` when it cannot be read; None for another skill."""
        d = self._data()
        if not d or not d["sensor"] or skill != d["sensor"]:
            return None
        v = reading(raw, d["unknown"])
        if v is None:
            self._tell("unreadable", skill=skill)
            return default
        return v

    def on_step_failed(self, run, st, code, reason, a, o):
        """A declared release failed while the belief holds an object: the sensor decides. Empty: ``let_go`` (NEXT when its refind was spliced in, else
        None: the belief is synchronised and the failure goes on); holding or unknown: None (the belief keeps the object)."""
        d, held = self._data(), run.world.holding
        if not d or a not in d["releases"] or not held or not self._is_holding_sensor(d):
            return None
        now = self.svc.hand_holds(run)
        self._tell("release", object=held, step=f"{a} {o}", reading=READING[now])
        if now is not False:
            return None
        spliced = self.svc.let_go(run, st)
        yield run.ev("recover", at_index=run.i, kind=SPEC.name, reading="empty",
                     msg=f"the {held} is no longer in the hand after {a} {o} failed" + ("; looking for it on the floor here" if spliced else ""))
        return NEXT if spliced else None

    def _is_holding_sensor(self, d) -> bool:
        robot = self.svc.robot
        return bool(d["sensor"]) and callable(getattr(robot, "holding_sensor", None)) and robot.holding_sensor() == d["sensor"]

    def on_goal_check(self, run, obj):
        """The goal check of an object this run released at a place: the floor place of that room comes first in a search for it. Never a verdict."""
        d = self._data()
        at = (getattr(run, "placed", None) or {}).get(obj)
        floor = self._floor(run.places, at, d["floor"]) if d and d["floor"] and isinstance(at, str) else None
        if floor:
            self.svc.prefer_now(run, obj, [floor])
            self._tell("floor", object=obj, place=at, floor=floor)
        return None

    @staticmethod
    def _floor(places, at, head):
        """The one place of ``at``'s room whose head is the declared floor name (not ``at`` itself), or None."""
        room = places.room(at)
        hits = [k for k in places.keys if room and places.room(k) == room and places.head(k).lower() == head] if room else []
        return hits[0] if len(hits) == 1 and hits[0] != at else None


register(SPEC, EffectCheck)
