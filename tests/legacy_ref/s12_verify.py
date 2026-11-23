"""Reference copy for Step S12: ``_observe_goal`` and ``_post_check`` as they were in
robot_agent/core/planning/assist_loop.py lines 3046-3082 and 3230-3261 at commit 9f1786d (the tip of generalize-core before
S12), copied verbatim as the methods of a class ``Old``. What differs from the file: the class is named ``Old``; the
imports are absolute. Every other name the bodies use (``self._look``, ``self._second_look_seen``, ``self._holding``,
``self._found_point``, ``self._place_of_far_point``, ``self.options``, ``self.cfg``, ``self.robot``) comes from the object
the test binds the copy to. Never edited: it is the old behaviour that tests/test_pack_verifiers.py compares the
pack-chosen verifiers with."""

from __future__ import annotations

import math

from robot_agent.contract import FailureCode
from robot_agent.core.planning.records import VerifyResult
from robot_agent.core.planning.sensor_health import arrival_check


class Old:
    def _observe_goal(self, r, g):
        """Look at one goal: (True | False | None = cannot tell, evidence text, device)."""
        if g.get("type") not in ("on", "in"):
            return None, f"{g.get('type')} {g.get('object')}: no sensor declared for it (belief)", None
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

    def _post_check(self, r, st, skill, res, params, rec) -> list:
        """After a reported success, the effects + the skill's VerifySpecs (manifest: Pick looked at again
        once the arm is folded, MoveTo measured against the ENV loc); a release looked at twice; a Find
        seen out of reach."""
        a, o = st["action"], st["object"]
        vres = r.verifier.verify(st, res, r.world, r.node, params=params)
        vres += arrival_check(r.robot, r.node, st, vres, self.cfg["sensor_health"])   # a stale pose: not arrived
        if (a in ("Place", "PutIn") and vres and not vres[0].ok
                and res.code is FailureCode.GRASP_FAILED and not self._holding(r.node, default=True)):
            # the release's own empty-gripper check fired right after letting go; looked once more
            vres = [VerifyResult("isdone", True, f"{skill}: second look, gripper empty"),
                    VerifyResult("recheck", True, f"first check: {vres[0].detail}")]
        if (a in ("Place", "PutIn") or a in self.robot.model_capabilities().release_actions) and vres \
                and vres[0].ok and any(v.layer == "sensor" and not v.ok for v in vres) \
                and all(v.ok for v in vres if v.layer != "sensor") and r.world.holding \
                and not self._holding(r.node, default=True) and not self._holding(r.node, default=True):
            # the release reported success and only the gripper check read "full"; two more readings read empty:
            # two of three, a noisy sensor and not a failed release
            vres = [v for v in vres if v.layer != "sensor"] + [
                VerifyResult("sensor", True, f"{skill}: two second readings, gripper empty", unknown=False)]
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
