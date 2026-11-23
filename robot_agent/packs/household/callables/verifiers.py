"""The verifiers of the standard (household) pack: ``std.find_at_destination`` (the end-of-run look at a goal) and the step
verifiers ``std.release_second_look`` and ``std.find_reach``.

What: ``find_at_destination`` is the body of ``AssistLoop._observe_goal`` after its kind test; ``release_second_look`` and
``find_reach`` are the three rules of ``AssistLoop._post_check`` after the core layers (a release looked at again; a Find
seen out of reach). Moved from robot_agent/core/planning/assist_loop.py:3046-3083 and :3230-3261 at 9f1786d with the
conditions, their order, the sensor reads and the texts unchanged; only ``self`` became ``ctx.loop`` and ``r`` ``ctx.run``.
Why: the loop names no goal kind and no action for its checks (docs/proposals/generalize_core_design.md, "Verifiers", Step
S12). ``verifiers.yaml`` next to ``pack.yaml`` declares the three entries, ``goal_kinds.yaml`` names
``std.find_at_destination`` as the ``observe`` verifier of the kinds on and in, and robot_agent/core/pack/verifiers.py
picks them for the loop (robot_agent/core/planning/phases/verify_goals.py).
Off: a robot pack that gives a kind another ``observe`` (or none) does not use ``find_at_destination`` for it; one that
removes a step verifier (``remove: {verifiers: [std.find_reach]}``) does not run that check.
"""

from __future__ import annotations

import math

from robot_agent.contract import FailureCode
from robot_agent.core.pack import register
from robot_agent.core.planning.records import VerifyResult

_RELEASES = ("Place", "PutIn")              # the plan's own release actions (assist_loop.py:3237, :3242 at 9f1786d)


@register("verifier.goal", "std.find_at_destination")
def find_at_destination(goal, ctx):
    """Look at one goal: go to its place and Find the object there. (True | False | None = cannot tell, evidence, device)."""
    self, r, g = ctx.loop, ctx.run, goal
    o, place = g["object"], g.get("to") or ""
    nice = place.replace("@", " in the ")
    if o in getattr(self, "_handed", set()) and r.placed.get(o) == place:
        # F6.2: handed over by a release action of the contract (a Handover) that succeeded: the person holds
        # it, so a look at their spot cannot see it there; the hand-over's own success is the evidence
        return True, f"the {o} was handed over at the {nice} (the hand-over succeeded)", None
    own = self.options.decide("on_goal_check", r, o)        # e.g. reach_delivery: the person's entry says it is out of reach now
    if own is not None:
        return own
    if r.places.openable(place) and place not in r.world.opened:
        return None, f"the {o} is inside the closed {nice}: not visible (belief)", None
    if r.world.arrived != place:
        res, _sk = self._look(r, {"action": "MoveTo", "object": place})
        if res is None or not res.ok:
            return None, f"could not go back to the {nice} to look ({res.msg if res else 'no MoveTo skill'})", _sk
    res, skill = self._look(r, {"action": "Find", "object": o})
    if res is None:
        return None, f"no Find skill to look for the {o}", None
    if res.ok:
        r.memory.see(o, place, "seen at its goal place")
        return True, f"the {o} is seen on the {nice}", skill
    code = r.robot.failure_code(res, res.msg)
    if code is FailureCode.ABSENT and r.placed.get(o) == place:
        # a step of the plan put it here and the release was verified: one miss of the camera is weak evidence
        # against that, so the check looks a second time before it says the goal does not hold
        again, skill2 = self._look(r, {"action": "Find", "object": o})
        if again is not None and again.ok and (seen := self._second_look_seen(r, o, place, nice, skill2)):
            return seen
        res, skill = (again, skill2) if again is not None else (res, skill)
        code = r.robot.failure_code(res, res.msg)
    if code is FailureCode.ABSENT:
        return False, f"the {o} is not on the {nice} (looked after the task: {res.msg or 'not found'})", skill
    return None, f"could not look for the {o} ({code.value}: {res.msg})", skill


@register("verifier.step", "std.release_second_look")
def release_second_look(step, result, ctx) -> list:
    """A release looked at again: a failed empty-gripper check right after letting go is read once more; a success whose
    only bad layer is the gripper sensor is read twice more (two of three)."""
    self, r, st, res, skill, vres = ctx.loop, ctx.run, step, result, ctx.skill, ctx.vres
    a = st["action"]
    if (a in _RELEASES and vres and not vres[0].ok
            and res.code is FailureCode.GRASP_FAILED and not self._holding(r.node, default=True)):
        # the release's own empty-gripper check fired right after letting go; looked once more
        vres = [VerifyResult("isdone", True, f"{skill}: second look, gripper empty"),
                VerifyResult("recheck", True, f"first check: {vres[0].detail}")]
    if (a in _RELEASES or a in self.robot.model_capabilities().release_actions) and vres \
            and vres[0].ok and any(v.layer == "sensor" and not v.ok for v in vres) \
            and all(v.ok for v in vres if v.layer != "sensor") and r.world.holding \
            and not self._holding(r.node, default=True) and not self._holding(r.node, default=True):
        # the release reported success and only the gripper check read "full"; two more readings read empty:
        # two of three, a noisy sensor and not a failed release
        vres = [v for v in vres if v.layer != "sensor"] + [
            VerifyResult("sensor", True, f"{skill}: two second readings, gripper empty", unknown=False)]
    return vres


@register("verifier.step", "std.find_reach")
def find_reach(step, result, ctx) -> list:
    """A Find that saw the object farther than the robot's reach (cfg ``reach_far``) fails the ``reach`` layer; the place
    near the sighting is remembered for the search."""
    self, r, st, rec, vres = ctx.loop, ctx.run, step, ctx.rec, ctx.vres
    a, o = st["action"], st["object"]
    if a == "Find" and vres and vres[0].ok:
        p3 = self._found_point(rec.result, o)
        r.seen_at[o] = p3
        if p3 and math.hypot(p3[0], p3[1]) > self.cfg["reach_far"]:
            near = self._place_of_far_point(p3, r.node, r.places, r.world)
            vres.append(VerifyResult("reach", False, f"the {o} was seen {math.hypot(p3[0], p3[1]):.1f} m away"
                                     + (f", near {near}" if near else ", not near any place") + ", out of reach from here"))
            if near:
                r.seen_far.append(near); r.memory.see(o, near, "seen from afar")
                if getattr(r, "search", None) is not None:
                    r.search.see(o, near, how="seen from afar")
    return vres
