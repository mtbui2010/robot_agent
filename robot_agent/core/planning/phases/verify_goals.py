"""The verify phase of AssistLoop: the goals looked at after the task, the return to the task's end place, the step verifiers
and the final verdict.

What: ``VerifyGoals`` is a mixin with ``_observe_goals``, ``_end_action``, ``_return_tries``, ``_return_to_end``, ``_look``
and ``_final_check``, moved VERBATIM out of ``assist_loop.py`` (step S12 of the core generalization), plus the two seams
that now ask the robot's pack: ``_observe_goal`` (the goal kind's ``observe`` verifier looks at one goal; a kind with none
stays on the belief) and ``_step_checks`` (the pack's step verifiers after the core layers of ``_post_check``). Both read
the pack through robot_agent/core/pack/verifiers.py; the standard pack names ``std.find_at_destination`` for on / in and
``std.release_second_look``, ``std.find_reach`` for every step (robot_agent/packs/household/verifiers.yaml).
Why: the loop decided itself which goals are looked at and which steps get a second look
(docs/proposals/generalize_core_design.md, "Verifiers"); the hooks ``on_goal_check``, ``on_second_look``, ``on_final`` and
``on_goals_met`` are called at the same points as before, and ``_observe_goal`` stays a method of the class (tests and the
golden mutations patch it there). ``_post_check`` stays in ``assist_loop.py``: its core layers read the option key
``sensor_health``, which ``_run_step`` reads there too.
Switch off: remove the ``VerifyGoals`` line from ``PHASE_MIXINS`` in ``phases/__init__.py`` and put the methods back.
"""
from __future__ import annotations

import json

from robot_agent.contract import FailureCode, Unbindable
from .. import action_model, episode_memory, exec_integrity
from ..robot import run_skill
from ...pack import goal_semantics, verifiers as pack_verifiers


class VerifyGoals:
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
        """Look at one goal: (True | False | None = cannot tell, evidence text, device). The goal kind's ``observe``
        verifier in the robot's pack looks (core/pack/verifiers.py); a kind with none stays on the belief."""
        pack = pack_verifiers.checking_pack(goal_semantics.pack_of(self))
        fn, entry = pack_verifiers.goal_verifier(pack, g.get("type"))
        if fn is None:
            return None, f"{g.get('type')} {g.get('object')}: no sensor declared for it (belief)", None
        return fn(g, pack_verifiers.Ctx(self, r, entry))

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

    def _step_checks(self, r, st, skill, res, rec, vres) -> list:
        """The step verifiers of the robot's pack for this step's action (core/pack/verifiers.py), in order, each given the
        verify results so far: the step's final list of verify results."""
        pack = pack_verifiers.checking_pack(goal_semantics.pack_of(self))
        for fn, entry in pack_verifiers.step_verifiers(pack, st["action"]):
            vres = fn(st, res, pack_verifiers.Ctx(self, r, entry, skill=skill, rec=rec, vres=vres))
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
