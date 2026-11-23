"""Reference copy for step S12: the verify methods of AssistLoop EXACTLY as they were in assist_loop.py at 9f1786d (before
they moved to phases/verify_goals.py and the pack's verifiers). Only the imports changed (absolute names). Used by
tests/test_pack_verifiers.py as the differential reference; never edit the method bodies."""
import json
import math

from robot_agent.contract import FailureCode, Unbindable
from robot_agent.core.planning import action_model, episode_memory, exec_integrity
from robot_agent.core.planning.records import VerifyResult
from robot_agent.core.planning.robot import run_skill
from robot_agent.core.planning.sensor_health import arrival_check


class RefVerify:
    def _observe_goals(self, r):
        """Done only by observation: each on / in goal is looked at (cfg goal_check). Returns the goals seen
        NOT to hold as failures [{code, device, evidence}] (their belief is withdrawn), None when cancelled.
        A goal that cannot be looked at (inside a closed drawer, no Find skill) stays on the belief, as
        `unknown` in r.goal_checks."""
        if self.cfg["goal_check"] != "observe":
            return []
        last = r.completed[-1] if r.completed else {}
        unmet, last_placed = [], (last.get("object") if last.get("action") in ("Place", "PutIn") else None)
        yield r.ev("status", msg="Checking the goals...")
        for g in sorted(r.goals, key=lambda g: g.get("to") != r.world.arrived):   # where the robot stands first
            ok, ev_, device = self._observe_goal(r, g)
            stop = self._cancelled(r)
            if stop:
                yield stop; return None
            r.goal_checks.append({"goal": f"{g['type']} {g['object']} {g.get('to', '')}".strip(),
                                  "ok": ok, "evidence": ev_})
            yield r.ev("goal_check", goal=g, ok=ok, evidence=ev_, world=r.world.to_dict())
            if ok is False:
                r.placed.pop(g["object"], None); r.memory.see(g["object"], None, "not at its goal place")
                code = FailureCode.DROPPED if g.get("to") == last_placed else FailureCode.ABSENT
                unmet.append({"code": code.value, "device": device,
                              "evidence": {"goal": r.goal_checks[-1]["goal"], "observed": ev_}})
        last = r.goals[-1] if r.goals else {}
        if last.get("type") == "go" and last.get("to") and r.world.arrived != last["to"]:
            if self.cfg.get("endstate_check") and self._return_tries():  # round 3: a checked step, not a hope
                unmet += self._return_to_end(r, last["to"])
            else:
                self._look(r, {"action": "MoveTo", "object": last["to"]})    # back where the task ends ("go back and charge")
        return unmet

    def _end_action(self, r, place) -> str:
        """The action that achieved the go goal at `place` in the completed plan (its manifest effects name ``arrived``
        or ``docked``, e.g. the pack's Dock), else MoveTo."""
        for st in reversed(r.completed):
            if st.get("object") != place:
                continue
            spec = r.robot.spec(st.get("action"))
            fx = " ".join(str(e) for e in (getattr(spec, "effects", None) or []) if not str(e).strip().startswith("not "))
            if st.get("action") != "MoveTo" and ("docked(" in fx or "arrived(" in fx):
                return st["action"]
        return "MoveTo"

    def _return_tries(self) -> int:
        """The pack's declared ``planner.recovery.return_tries`` (a count >= 1), or 0 when it declares none (then
        ``endstate_check`` does nothing: the return stays one plain MoveTo)."""
        return exec_integrity.return_tries((self._manifest_planner("recovery", None) or {}).get("return_tries"))

    def _return_to_end(self, r, place, tries=None) -> list:
        """Option ``endstate_check``: the check tour's return to the task's last ``go`` place, retried (the pack's
        ``planner.recovery.return_tries``); the action that achieved the goal in the plan (MoveTo, or the
        pack's Dock after a MoveTo) is run again. If it still fails the goal is unmet (one failure entry, coded by
        the adapter) and the run does not say done."""
        tries = max(int(tries if tries is not None else self._return_tries()), 1)
        act = self._end_action(r, place)
        res = None
        for n in range(tries):
            res, _sk = self._look(r, {"action": "MoveTo", "object": place})
            if res is not None and res.ok and act != "MoveTo":
                res, _sk = self._look(r, {"action": act, "object": place})
            if res is not None and res.ok:
                self._lab("endstate_check", place=place, ok=True, tries=n + 1)
                return []
        if res is None:
            return []                                                   # no skill to check with
        self._lab("endstate_check", place=place, ok=False, tries=tries)
        code = r.robot.failure_code(res, res.msg)
        return [{"code": code.value, "device": res.device,
                 "evidence": {"goal": f"go {place}", "observed": f"could not get back to {place}: {res.msg}"}}]

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

    def _look(self, r, st):
        """Run a MoveTo / Find of the goal check (its effect applied on success): (SkillResult | None, skill)."""
        try:
            mapped = self._bind(dict(st), r.world)
        except Unbindable:
            mapped = None
        if not mapped:
            return None, None
        skill, params = mapped
        res = run_skill(self.sr, skill, params, r.node, log_fn=self._log_fn(r.i))
        if res.ok:
            action_model.apply(st, r.world, inplace=True)
        return res, skill

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

    # ── 4. done only when the goals hold ──────────────────────────────────
    def _final_check(self, r) -> dict:
        own = self.options.decide("on_final", r)          # e.g. unapplied_change: the goals checked are not what the person now wants
        if own is not None:
            return own
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            if not getattr(r, "compiled", False):            # the planner's memory learns its own plans only
                # ... and only when the goals were seen to hold (planning/episode_memory.py)
                episode_memory.record_if_verified(r.planner, r.restated, r.completed, r.goals,
                                                  getattr(r, "goal_checks", []),
                                                  mode=self.cfg.get("memory_record"), run_id=r.run_id)
            own = self.options.decide("on_goals_met", r)         # e.g. hazard: "I did the rest, but I won't ..."
            if own is not None:
                return own
            return self._finish(r, "success")
        return self._finish(r, "failed", msg="plan finished but the goals do not hold: "
                            + json.dumps(r.goals, ensure_ascii=False))
