"""Option ``end_state`` (default off): the state a task ends in, which the pack declares, is part of the goal.

"Go back to the dock" is a ``go`` goal the engine only holds to when it is the LAST goal; read in another position (a model may put it
before the other goals), or reached by a plain MoveTo where the pack's own action is what makes the state true (docking, not just
arriving at the dock), the robot ends elsewhere or undocked and says done.

Pack data: ``planner.end_state: {places: [ENV key or head, ...], action: <an action of the pack>}``. With the option, before the goals are
checked, when a ``go`` goal is for one of those places the robot goes there if it is not there and runs the declared action, up to
``tries`` times (``planner.recovery.return_tries``, default 1 when not declared); the run ends ``failed`` when that does not work. No data, an
action the robot lacks, or no such go goal = the option does nothing. Generic: no place or action name in the code.

Hook ``on_final``. Keys: ``enabled`` (legacy ``end_state``). Lab flag ``end_state``: ``{place, action, ok, tries}``.
"""

from __future__ import annotations

from typing import Optional

from robot_agent.core.options import Key, Option, OptionSpec, register

DATA = "end_state"


def declared(planner) -> Optional[dict]:
    """``{"places": [str], "action": str}`` from the pack's ``planner.end_state``, or None when it declares none or a bad one."""
    v = (planner or {}).get(DATA) if isinstance(planner, dict) else None
    if not isinstance(v, dict) or not isinstance(v.get("action"), str) or not v["action"].strip():
        return None
    places = v.get("places")
    if not isinstance(places, (list, tuple)) or not all(isinstance(p, str) and p.strip() for p in places) or not places:
        return None
    return {"places": [p.strip() for p in places], "action": v["action"].strip()}


def is_end_place(places: list, key: str) -> bool:
    """Is the ENV key ``key`` one of the declared places (the whole key or its head, the part before ``@``)?"""
    return bool(key) and (key in places or key.split("@")[0].strip() in places)


SPEC = OptionSpec(
    name="end_state",
    keys=(Key("enabled", "bool", False, aliases=("end_state",)),),
    hooks=("on_final",),
    order=95,
    enabled="enabled",
    doc="The state the pack says a task ends in (planner.end_state: places and the action that makes it true) is made true before done.",
)


class EndState(Option):
    def on_final(self, run):
        d = declared(getattr(self.svc.robot.manifest, "planner", None))
        goal = next((g for g in reversed(run.goals or []) if g.get("type") == "go" and is_end_place(d["places"], g.get("to") or "")), None) if d else None
        if goal is None or d["action"] not in set(self.svc.robot.plan_actions):
            return None
        place, act, move = goal["to"], d["action"], self.svc.role("move")
        if move is None and run.world.arrived != place:        # no declared move action: no way to get there (fail open)
            return None
        rec = (getattr(self.svc.robot.manifest, "planner", None) or {}).get("recovery")
        tries = rec.get("return_tries") if isinstance(rec, dict) else None
        tries = tries if isinstance(tries, int) and not isinstance(tries, bool) and tries >= 1 else 1
        res = None
        for n in range(tries):
            ok = run.world.arrived == place or getattr(self.svc.run_step(run, {"action": move, "object": place}), "ok", False)
            res = self.svc.run_step(run, {"action": act, "object": place}) if ok else None
            if res is not None and res.ok:
                self.svc.lab(SPEC.name, place=place, action=act, ok=True, tries=n + 1)
                return None
        self.svc.lab(SPEC.name, place=place, action=act, ok=False, tries=tries)
        return self.svc.finish(run, "failed", msg=f"could not end the task at {place} ({act.lower()} failed)")


register(SPEC, EndState)
