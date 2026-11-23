"""The recover phase of AssistLoop: a step that succeeded, the recovery rule's own rung, the waits on the robot, and the
pack's recovery policy of a failed step.

What: ``RecoverPhase`` is a mixin with ``_step_succeeded``, ``_reset_container``, ``_apply_policy``, ``_wait_on_robot`` and
``_wait_health``, moved VERBATIM out of ``assist_loop.py`` (Step S14 of the core generalization; only the relative import
inside ``_wait_health`` gained a dot), and ``_run_policy``: the policy the robot's pack names for a failed step (by goal kind,
by action; robot_agent/core/pack/recovery.py) runs on the engine's ladders. The standard pack names the six ladders the loop
chose from a dict of action names before (robot_agent/packs/household/recovery.yaml); any other action gets the builtin
``planner``, as before.
Why: the loop names no action for its recovery (docs/proposals/generalize_core_design.md, "Recovery", "Phase pipeline").
``_step_failed`` stays in ``assist_loop.py`` (tests/test_find_ladder.py looks for it there) and keeps the order of its
steps: the failure code, the rule, hook ``on_step_failed``, the never-retry stop, the rule's rung, the same-failure stop, then
this policy, then the planner. The hooks are called at the same points as before.
Switch off: remove the ``RecoverPhase`` line from ``PHASE_MIXINS`` in ``phases/__init__.py`` and put the methods back; a robot
pack switches a ladder off by naming another policy (``by_action: {Pick: planner}``) or a whole goal kind (``by_kind``).
"""
from __future__ import annotations

import time

from robot_agent.contract import FailureCode, Recovery, Unbindable
from .. import exec_integrity, plan_tidy
from .. import reconcile as sensed
from ..robot import run_skill
from ...options import NEXT as _NEXT, STOP as _STOP     # the same objects as loop._NEXT / loop._STOP (no private import)
from ...pack import goal_semantics, recovery as pack_recovery


class RecoverPhase:
    def _step_succeeded(self, r, st, rec):
        a, o = st["action"], st["object"]
        rec.status = "success"; r.run.steps.append(rec)
        self._search_effect(r, st)                       # before the effect: where the robot did it
        held_ = r.world.holding
        self._effect(st, r.world, r.memory, r.placed)
        self.options.observe("on_step_done", r, st, held_)
        if a == "Find":
            yield from self._which_one(r, st, rec)      # two objects fit the words: which one?
        if a == "MoveTo":
            sensed.confirm_arrival(r.world, rec.verifies)    # measured at its target: sensor provenance
        if st.get("verify_goal"):
            r.placed[o] = st["verify_goal"]             # seen at its destination: the goal holds
        r.completed.append(st)
        yield r.ev("step_done", index=r.i, status="success", result=rec.result,
                   say=self._announce(r, "step_success", st, a, o), world=r.world.to_dict())
        r.i += 1
        for f in r.failures:
            if f["sig"] == (a, o):
                f["recovered"] = True                    # the step that failed before has now succeeded
        if (r.i < len(r.steps) and self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived)
                and not plan_tidy.closes_only(r.completed, r.steps[r.i:], r.world_at_start)):
            # the user's goals are met: end the task here rather than run what is left of the plan (the
            # goals are then looked at, _plan_ended)
            yield r.ev("status", msg=f"goals reached; {len(r.steps) - r.i} remaining plan steps not run")
            del r.steps[r.i:]
        return _NEXT

    def _reset_container(self, r, st, res, reason):
        """Option ``reset_container``: a PutIn that fails BLOCKED into a container the belief holds open, once per
        container (and only for a robot whose action model has Close and Open): put the held object down where the
        robot's own completed steps picked it, Close and Open the container, fetch it again, PutIn again. _NEXT or None."""
        c, held = st["object"], r.world.holding
        done = r.__dict__.setdefault("reset_done", set())
        code = r.robot.failure_code(res, reason)
        remedy = exec_integrity.blocked_putin_remedy((self._manifest_planner("recovery", None) or {}).get("blocked_putin"))
        try:
            free_hand = len(self.robot.manifest.hands()) > 1       # another hand opens / closes while this one holds
        except Exception:
            free_hand = False
        if (code is not FailureCode.BLOCKED or not held or c in done or c not in r.world.opened or not remedy
                or not exec_integrity.reset_actions(remedy, not free_hand) <= set(self.robot.plan_actions)):
            return None
        src = exec_integrity.pick_source(r.completed, c, r.places.openable) if not free_hand else c
        if not src:
            return None
        done.add(c)
        self._note_failure(r, st, code, res.device, reason)
        r.steps[r.i:r.i] = exec_integrity.reset_steps(c, held, src, remedy, not free_hand)
        self._lab("reset_container", container=c, held=held, source=src)
        yield r.ev("recover", at_index=r.i, kind="reset_container", msg=f"{c}: " + (f"put {held} down at {src}, " if not free_hand else "") + ", ".join(a.lower() for a in remedy) + ", again")
        return _NEXT

    def _apply_policy(self, r, st, skill, code, rule, reason, device):
        """The recovery policy's own actions (contract.recovery, by FailureCode) before the per-action
        recovery: STOP once the rule's retries are used up, WAIT_HEALTH (wait for the robot's health, then
        the same step again in place), HEALTH_CHECK (an unhealthy robot stops the run). None: go on."""
        key = (code, st.get("action"), st.get("object"))
        r.code_tries[key] = n = r.code_tries.get(key, 0) + 1
        todo = self.options.query("recovery_rule", r, code, rule, default=rule).next_after(n)   # option hw_wait: tries per kind
        ev_ = r.failures[-1]["evidence"]
        if todo is Recovery.STOP:
            if r.health is not None:
                ev_["health"] = r.health                  # what the robot said the last time it was asked
            yield self._finish(r, "failed", code=code, device=device, evidence=ev_,
                               msg=f"{st['action']} {st['object']} failed {n} times ({code.value}): {reason}")
            return _STOP
        if todo not in (Recovery.WAIT_HEALTH, Recovery.HEALTH_CHECK):
            return None
        wait_s = self.cfg["health_wait_s"] if todo is Recovery.WAIT_HEALTH else 0.0
        r.health = health = yield from self._wait_health(r, wait_s)
        ev_["health"] = health
        if health.get("ok") is False and not (todo is Recovery.WAIT_HEALTH and self.options.query("more_tries", r, code, n, default=False)):
            # (option hw_wait: no stop while declared tries are left) the person hears why in words (no failure code, no skill name: a Dock runs the move skill)
            yield self._finish(r, "failed", code=code, device=device, evidence=ev_,
                               msg=f"the robot is not healthy after {st.get('action')} {st.get('object')} failed "
                                   f"({skill}: {code.value}): {health.get('error') or health}",
                               say="I have to stop: something is wrong with my hardware.")
            return _STOP
        if todo is Recovery.HEALTH_CHECK:
            return None
        if st.get("action") == "Find":
            r.world.found = None
        yield r.ev("recover", at_index=r.i, kind="wait_health", code=code.value,
                   msg=f"{skill} could not run ({code.value}); waited for the robot, trying again")
        return _NEXT                                      # the same step again, in place (r.i unchanged)

    def _wait_on_robot(self, r):
        """Option ``wait_on_robot``: the pack's declared wait action (manifest planner.recovery.wait) runs once per
        schedule entry, health() read after each; None when the pack declares none (today's poll then)."""
        act, sched = exec_integrity.wait_schedule((self._manifest_planner("recovery", None) or {}).get("wait"))
        if not act:
            return None
        try:
            if self._bind({"action": act, "object": f"{sched[0]:g}"}, r.world) is None:
                return None
        except Unbindable:
            return None
        sched = self.options.query("wait_schedule", r, sched, default=sched)             # option hw_wait: the schedule of this failure kind
        h, waited = r.robot.health(r.node), []
        for secs in sched:
            if h.get("ok") is True:
                break
            skill, params = self._bind({"action": act, "object": f"{secs:g}"}, r.world)
            run_skill(self.sr, skill, params, r.node, log_fn=self._log_fn(r.i))
            waited.append(secs)
            h = r.robot.health(r.node)
        self._lab("wait_on_robot", action=act, schedule_s=sched, waited_s=waited, health_ok=h.get("ok"))
        return h

    def _wait_health(self, r, wait_s):
        """The adapter's health(), polled up to `wait_s` until it reads ok (a cancel ends the wait)."""
        from ...run_control import cancel_requested
        if self.cfg.get("wait_on_robot"):
            waited = self._wait_on_robot(r)
            if waited is not None:
                yield r.ev("health", health=waited)
                return waited
        t_end = time.time() + max(0.0, wait_s)
        while True:
            h = r.robot.health(r.node)
            if h.get("ok") is True or time.time() >= t_end or cancel_requested():
                yield r.ev("health", health=h)
                return h
            time.sleep(min(0.1, max(0.0, t_end - time.time())))

    def _run_policy(self, r, st, reason, rule, ladders):
        """The pack's recovery policy of the failed step ``st`` (core/pack/recovery.py), run with the engine's ``ladders``:
        (outcome or None, replan hint). ``planner`` and ``tree`` give (None, ""): the planner's repair follows, and it leaves a
        tree's own step failed (service ``no_replan``); ``stop`` ends the run."""
        pack = pack_recovery.selecting_pack(goal_semantics.pack_of(self))
        kinds = [g.get("type") for g in (getattr(r, "goals", None) or ()) if isinstance(g, dict)]
        builtin, fn, entry = pack_recovery.policy(pack, pack_recovery.policy_name(pack, st.get("action"), kinds))
        if fn is not None:
            return (yield from fn(r, st, reason, rule, pack_recovery.Ctx(self, r, entry, ladders)))
        if builtin == "stop":
            yield self._finish(r, "failed", msg=f"{st.get('action')} {st.get('object')} failed: {reason} (recovery policy: stop)")
            return _STOP, ""
        return None, ""
