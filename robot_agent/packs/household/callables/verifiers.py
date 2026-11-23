"""The verifiers of the standard (household) pack: the end-of-run look at an on / in goal, and two checks after a step.

What: ``std.find_at_destination`` (namespace ``verifier.goal``): go to the goal's place and Find the object there; a miss
after a verified release looks once more. It is the body of ``AssistLoop._observe_goal`` after its kind test
(robot_agent/core/planning/assist_loop.py:3050-3082 at 9f1786d), unchanged. ``std.release_second_look`` and
``std.find_reach`` (namespace ``verifier.step``): the release rules and the Find rule of ``AssistLoop._post_check``
(assist_loop.py:3237-3260 at 9f1786d), unchanged: a release whose own empty-gripper check fired right after letting go,
or whose gripper check alone read "full", is looked at again; a Find seen farther than the loop's ``reach_far`` fails the
``reach`` layer and records where the object was seen.
Why: the loop names no goal kind and decides no check of its own (docs/proposals/generalize_core_design.md, "Verifiers",
Step S12). ``verifiers.yaml`` declares these names; ``goal_kinds.yaml`` names ``std.find_at_destination`` as the
``observe`` of on / in; ``robot_agent/core/pack/verifiers.py`` picks them for the loop.
How: everything of the loop goes through ``ctx`` (``robot_agent/core/pack/verifiers.Ctx``): ``ctx.run`` (the run's
state), ``ctx.look`` / ``ctx.decide`` / ``ctx.second_look_seen`` / ``ctx.holding`` / ``ctx.setting`` and the step's
``ctx.vres``. The calls are made in the old order and number (a test sensor that answers a sequence sees the same reads).
Off: a robot pack sets ``observe: null`` on a kind, or ``remove: {verifiers: [std.find_reach]}``; that check is not made.
"""

from __future__ import annotations

import math

from robot_agent.contract import FailureCode
from robot_agent.core.pack import register
from robot_agent.core.planning.records import VerifyResult

_RELEASES = ("Place", "PutIn")        # the household release actions (actions.yaml: class release)


def find_at_destination(goal, ctx):
    """Look at one goal: (True | False | None = cannot tell, evidence text, device)."""
    r, g = ctx.run, goal
    o, place = g["object"], g.get("to") or ""
    nice = place.replace("@", " in the ")
    if o in ctx.handed() and r.placed.get(o) == place:
        # F6.2: handed over by a release action of the contract (a Handover) that succeeded: the person holds
        # it, so a look at their spot cannot see it there; the hand-over's own success is the evidence
        return True, f"the {o} was handed over at the {nice} (the hand-over succeeded)", None
    own = ctx.decide("on_goal_check", o)        # e.g. reach_delivery: the person's entry says it is out of reach now
    if own is not None:
        return own
    if r.places.openable(place) and place not in r.world.opened:
        return None, f"the {o} is inside the closed {nice}: not visible (belief)", None
    if r.world.arrived != place:
        res, _sk = ctx.look({"action": "MoveTo", "object": place})
        if res is None or not res.ok:
            return None, f"could not go back to the {nice} to look ({res.msg if res else 'no MoveTo skill'})", _sk
    res, skill = ctx.look({"action": "Find", "object": o})
    if res is None:
        return None, f"no Find skill to look for the {o}", None
    if res.ok:
        r.memory.see(o, place, "seen at its goal place")
        return True, f"the {o} is seen on the {nice}", skill
    code = r.robot.failure_code(res, res.msg)
    if code is FailureCode.ABSENT and r.placed.get(o) == place:
        # a step of the plan put it here and the release was verified: one miss of the camera is weak evidence
        # against that, so the check looks a second time before it says the goal does not hold
        again, skill2 = ctx.look({"action": "Find", "object": o})
        if again is not None and again.ok and (seen := ctx.second_look_seen(o, place, nice, skill2)):
            return seen
        res, skill = (again, skill2) if again is not None else (res, skill)
        code = r.robot.failure_code(res, res.msg)
    if code is FailureCode.ABSENT:
        return False, f"the {o} is not on the {nice} (looked after the task: {res.msg or 'not found'})", skill
    return None, f"could not look for the {o} ({code.value}: {res.msg})", skill


def release_second_look(step, result, ctx) -> list:
    """A release looked at twice: (1) its own empty-gripper check fired right after letting go, one more reading reads
    empty; (2) it reported success and only the gripper check read "full", two more readings read empty."""
    a, skill, vres, r, res = step["action"], ctx.skill, ctx.vres, ctx.run, result
    if (a in _RELEASES and vres and not vres[0].ok
            and res.code is FailureCode.GRASP_FAILED and not ctx.holding(default=True)):
        # the release's own empty-gripper check fired right after letting go; looked once more
        vres = [VerifyResult("isdone", True, f"{skill}: second look, gripper empty"),
                VerifyResult("recheck", True, f"first check: {vres[0].detail}")]
    if (a in _RELEASES or a in ctx.release_actions()) and vres \
            and vres[0].ok and any(v.layer == "sensor" and not v.ok for v in vres) \
            and all(v.ok for v in vres if v.layer != "sensor") and r.world.holding \
            and not ctx.holding(default=True) and not ctx.holding(default=True):
        # the release reported success and only the gripper check read "full"; two more readings read empty:
        # two of three, a noisy sensor and not a failed release
        vres = [v for v in vres if v.layer != "sensor"] + [
            VerifyResult("sensor", True, f"{skill}: two second readings, gripper empty", unknown=False)]
    return vres


def find_reach(step, result, ctx) -> list:
    """A Find seen farther than the loop's ``reach_far`` setting fails the ``reach`` layer; where it was seen is kept."""
    a, o, vres, r = step["action"], step["object"], ctx.vres, ctx.run
    if a == "Find" and vres and vres[0].ok:
        p3 = ctx.found_point(o)
        r.seen_at[o] = p3
        if p3 and math.hypot(p3[0], p3[1]) > ctx.setting("reach_far"):
            near = ctx.place_of_far_point(p3)
            vres.append(VerifyResult("reach", False, f"the {o} was seen {math.hypot(p3[0], p3[1]):.1f} m away"
                                     + (f", near {near}" if near else ", not near any place") + ", out of reach from here"))
            if near:
                r.seen_far.append(near); r.memory.see(o, near, "seen from afar")
                if getattr(r, "search", None) is not None:
                    r.search.see(o, near, how="seen from afar")
    return vres


register("verifier.goal", "std.find_at_destination", find_at_destination)
register("verifier.step", "std.release_second_look", release_second_look)
register("verifier.step", "std.find_reach", find_reach)

__all__ = ["find_at_destination", "release_second_look", "find_reach"]
