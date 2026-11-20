"""Option ``reach_check`` (default off): a Pick of an object seen beyond the arm's side reach gets a base shift before it.

What: before a Pick, the last Find of its object here (the engine's base-frame point ``seen_at``) is compared with the robot's
declared ``geometry.reach_m`` (the contract's side reach); when the object is farther to the side and a shift of
``distance - reach + margin_m`` (at most ``max_shift_m``) closes the gap, the pack's shift skill runs first (a skill-only step).
Why: the plan does not know geometry; the robot learnt it only at the Pick ("beyond arm reach") and then asked the person for help.
Data ``planner.options.reach_check.shift``: ``{skill, param, margin_m, max_shift_m}`` + ``source`` or ``assumed: true`` (docs/reach_check.md).
Fail open: no declared reach, shift block, pick role, skill of the robot or point, or too far even after the shift: nothing changes.
Switch off: key ``enabled`` (legacy ``reach_check``), off by default. Event: lab flag ``reach_check`` ``{object, dist_m, reach_m, shift_m}``.
"""

from __future__ import annotations

import math
from typing import Optional

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import pack_data

FLAG = "reach_check"


def decide(dist, reach, margin, max_shift) -> Optional[float]:
    """The base shift (metres, > 0) that brings a point ``dist`` m to the side within ``reach`` with ``margin`` to spare; None when it
    is within reach already, the shift would be more than ``max_shift``, or a value is missing, negative or not a number."""
    vals = (dist, reach, margin, max_shift)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in vals):
        return None
    d, r, m, mx = (float(v) for v in vals)
    if not all(math.isfinite(v) for v in (d, r, m, mx)) or d < 0 or r <= 0 or m < 0 or mx <= 0 or d <= r:
        return None
    need = round(d - r + m, 3)
    return need if need <= mx else None


def shift_config(data) -> Optional[dict]:
    """The declared shift ``{skill, param, margin_m, max_shift_m}`` (numbers checked by ``decide``), or None: not labelled, no names."""
    if not pack_data.labelled(data):
        return None
    skill, param = data.get("skill"), data.get("param")
    if not all(isinstance(v, str) and v.strip() for v in (skill, param)):
        return None
    return {"skill": skill.strip(), "param": param.strip(), "margin_m": data.get("margin_m"), "max_shift_m": data.get("max_shift_m")}


def side_of(point) -> Optional[float]:
    """The signed side offset (base frame y, metres) of a found point ``[x, y, z]``, or None."""
    try:
        y = float(point[1])
    except (TypeError, ValueError, IndexError):
        return None
    return y if math.isfinite(y) else None


def shift_step(cfg: dict, obj: str, signed: float) -> dict:
    """The skill-only step (no plan action: no symbolic effect) that shifts the base ``signed`` metres toward the object's side."""
    return {"action": "", "object": obj, "skill": cfg["skill"], "params": {cfg["param"]: signed},
            "reason": f"{cfg['skill']}::{cfg['param']}={signed}", "reach_check": True}


SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)), Key("shift", "dict", {}, sources=("manifest",))),
    hooks=("on_step",),
    order=32,
    enabled="enabled",
    doc="A Pick of an object seen beyond the declared side reach gets the pack's base shift first (needs planner.options.reach_check.shift).",
)


class ReachCheck(Option):
    """Hook ``on_step``: a Pick whose object the last Find here saw out of reach gets the shift step inserted before it, once per sighting."""

    def _plan(self, run, st) -> Optional[tuple]:
        """``(step, dist, reach, shift)`` of the shift a Pick needs, or None (no data, no point, already used, nothing to do)."""
        pick = self.svc.role("pick")
        o = st.get("object")
        if pick is None or st.get("action") != pick or getattr(run.world, "found", None) != o:
            return None
        robot = self.svc.robot
        cfg = shift_config(pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "shift"))
        reach = (getattr(robot.manifest, "geometry", None) or {}).get("reach_m")
        point = (getattr(run, "seen_at", None) or {}).get(o)
        used = run.__dict__.setdefault("opt", {}).setdefault(FLAG, {})
        side = side_of(point)
        knows = getattr(robot, "knows_skill", None)
        if cfg is None or side is None or used.get(o) == tuple(point) or not (callable(knows) and knows(cfg["skill"])):
            return None
        used[o] = tuple(point)                      # one decision per sighting: the next Find writes a new point
        shift = decide(abs(side), reach, cfg["margin_m"], cfg["max_shift_m"])
        return None if shift is None else (shift_step(cfg, o, math.copysign(shift, side)), abs(side), float(reach), shift)

    def on_step(self, run, st):
        plan = self._plan(run, st)
        if plan is None:
            return None
        step, dist, reach, shift = plan
        run.steps.insert(run.i, step)
        self.svc.lab(FLAG, object=st["object"], dist_m=round(dist, 3), reach_m=reach, shift_m=shift)
        yield run.ev("recover", at_index=run.i, kind=FLAG, msg=f"{step['reason']} ({st['object']} {dist:.2f} m, reach {reach:.2f} m)")
        return step


register(SPEC, ReachCheck)
__all__ = ["FLAG", "SPEC", "ReachCheck", "decide", "shift_config", "side_of", "shift_step"]
