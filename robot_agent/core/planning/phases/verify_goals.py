"""The verify phase of AssistLoop: the check after each step, the look at the goals after the task, the final verdict.

What: ``VerifyGoals`` is a mixin with ``_post_check``, ``_observe_goals``, ``_observe_goal``, ``_end_action``,
``_return_tries``, ``_return_to_end``, ``_look`` and ``_final_check``, moved out of ``assist_loop.py`` (step S12 of the core
generalization); all but ``_observe_goal`` and ``_post_check`` VERBATIM. Those two now ask the robot's pack by name
(``core/pack/verifiers.py``): a goal kind's ``observe`` verifier looks at a goal (a kind with none stays on the belief,
"no sensor declared for it"), and the pack's step checks run after the core layers (the skill's VerifySpecs, a stale pose).
The standard pack names the old rules (``packs/household/verifiers.yaml``, ``goal_kinds.yaml``: on / in are looked at).
Why: the loop decided itself which goals are looked at and which steps get a second look; a robot pack needs its own end
check (docs/proposals/generalize_core_design.md, "Verifiers"). ``VerifyCtx`` is what a verifier may use: the run and the
loop's services, read at the time of the call (a test that patches ``AssistLoop._look`` still reaches every verifier).
Hooks ``on_goal_check``, ``on_second_look``, ``on_final`` and ``on_goals_met`` are called at the same points as before.
Switch off: remove the ``VerifyGoals`` line from ``PHASE_MIXINS`` in ``phases/__init__.py`` and put the methods back.
"""
from __future__ import annotations

import json

from robot_agent.contract import FailureCode, Unbindable

from .. import action_model, episode_memory, exec_integrity
from ..robot import run_skill
from ..sensor_health import arrival_check
from ...pack import goal_semantics, verifiers as pack_verifiers


class VerifyCtx:
    """What a pack verifier may use: ``run``, its own ``entry``; for a step check also ``skill``, ``rec`` (the step's record)
    and ``vres`` (the verify results so far). The methods call the loop's own entry points."""
    __slots__ = ("_loop", "run", "entry", "skill", "rec", "vres")

    def __init__(self, loop, run, entry=None, skill=None, rec=None, vres=None):
        self._loop, self.run, self.entry = loop, run, entry if isinstance(entry, dict) else {}
        self.skill, self.rec, self.vres = skill, rec, vres

    def look(self, step):
        """Run a MoveTo / Find of the goal check: (SkillResult | None, skill)."""
        return self._loop._look(self.run, step)

    def decide(self, hook, *args):
        """The first option verdict of ``hook`` for this run (None: no option decides)."""
        return self._loop.options.decide(hook, self.run, *args)

    def second_look_seen(self, o, place, nice, skill):
        return self._loop._second_look_seen(self.run, o, place, nice, skill)

    @property
    def handed(self) -> set:
        """The objects this run handed over (release actions)."""
        return getattr(self._loop, "_handed", set())

    def hand_full(self) -> bool:
        """The hand sensor's reading (True when the robot has none or it cannot be read)."""
        return self._loop._holding(self.run.node, default=True)

    def release_actions(self):
        return self._loop.robot.model_capabilities().release_actions

    def found_point(self, obj):
        """Where the step's Find saw ``obj`` (base frame) or None."""
        return self._loop._found_point(self.rec.result, obj)

    def place_of_far_point(self, p3):
        run = self.run
        return self._loop._place_of_far_point(p3, run.node, run.places, run.world)

    @property
    def reach_far(self) -> float:
        return self._loop.cfg["reach_far"]


def _checking_pack(loop):
    """The pack whose verifiers check this loop's runs (``core/pack/verifiers.checking_pack``). A function, not a method:
    tests call ``AssistLoop._observe_goal`` with a bare object as ``self``."""
    return pack_verifiers.checking_pack(goal_semantics.pack_of(loop))


class VerifyGoals:
    def _post_check(self, r, st, skill, res, params, rec) -> list:
        """After a reported success, the effects + the skill's VerifySpecs (manifest: Pick looked at again
        once the arm is folded, MoveTo measured against the ENV loc), then the pack's step checks by name
        (standard pack: a release looked at twice; a Find seen out of reach)."""
        a, _o = st["action"], st["object"]
        vres = r.verifier.verify(st, res, r.world, r.node, params=params)
        vres += arrival_check(r.robot, r.node, st, vres, self.cfg["sensor_health"])   # a stale pose: not arrived
        for fn, entry in pack_verifiers.step_verifiers(_checking_pack(self), a):
            vres = fn(st, res, VerifyCtx(self, r, entry, skill, rec, vres))
        return vres

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
        """Look at one goal: (True | False | None = cannot tell, evidence text, device), by the goal kind's ``observe``
        verifier in the robot's pack; a kind with none stays on the belief."""
        fn, entry = pack_verifiers.goal_verifier(_checking_pack(self), g.get("type"))
        if fn is None:
            return None, f"{g.get('type')} {g.get('object')}: no sensor declared for it (belief)", None
        return fn(g, VerifyCtx(self, r, entry))

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
