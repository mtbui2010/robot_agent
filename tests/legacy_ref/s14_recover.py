"""Reference copy for step S14: the success step, the failed step, the recovery rule's rung and the waits of AssistLoop
EXACTLY as they were in assist_loop.py:3081-3255 at 6f844de (before _step_succeeded, _reset_container, _apply_policy,
_wait_on_robot and _wait_health moved to phases/recover.py and _step_failed chose its ladder through the pack). Used by
tests/test_pack_recovery.py as the reference (syntax trees and the old dict of six action names); never edit the method
bodies. The one relative import inside _wait_health is left as written (it is not run here)."""
import re  # noqa: F401
import time  # noqa: F401

from robot_agent.contract import FailureCode, Recovery, Unbindable  # noqa: F401
from robot_agent.core.planning import exec_integrity, plan_tidy  # noqa: F401
from robot_agent.core.planning import reconcile as sensed  # noqa: F401
from robot_agent.core.planning.loop import _NEXT, _STOP  # noqa: F401
from robot_agent.core.planning.robot import run_skill  # noqa: F401
from robot_agent.core.planning.verify_vlm import refuted  # noqa: F401


class RefRecover:
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

    def _step_failed(self, r, st, skill, rec, res, reason):
        """A failed step: stop (policy / same failure again), recover in place, or replan."""
        a, o = st["action"], st["object"]
        rec.status = "failed"; r.run.steps.append(rec)
        yield r.ev("step_done", index=r.i, status="failed", result=rec.result, reason=reason,
                   say=self._announce(r, "step_fail", st, a, o, reason=reason),
                   world=r.world.to_dict())
        if st.get("verify_goal"):
            r.world.found = None; r.i += 1              # not there: the goal does not hold yet (replan at the end)
            return _NEXT
        if self.cfg.get("reset_container") and a == "PutIn":
            out = yield from self._reset_container(r, st, res, reason)
            if out is not None:
                return out
        # ── stop at once: the recovery policy does not retry it (a skill bug, an e-stop), or the same
        # failure twice ──
        code = self.options.query("failure_code", r, a, base := r.robot.failure_code(res, reason), default=base)     # e.g. fetch_guard: a silent Find miss is ABSENT
        rule = r.robot.policy.rule_for(code)
        device = res.device or skill
        self._note_failure(r, st, code, device, reason)
        out = yield from self.options.decide_flow("on_step_failed", r, st, code, reason, a, o)   # e.g. reach_delivery: the person left
        if out is not None:
            return out
        if not rule.retry_allowed:
            what = "internal error in" if code is FailureCode.SOFTWARE else f"{code.value} in"
            yield self._finish(r, "failed", msg=f"{what} the {skill} skill (not retried): {reason}", code=code,
                               device=device, evidence=r.failures[-1]["evidence"]); return _STOP
        out = yield from self._apply_policy(r, st, skill, code, rule, reason, device)
        if out is not None:
            return out
        sig = (a, o, r.world.arrived, re.sub(r"[-\d.]+", "#", str(reason))[:160])
        r.seen_fail[sig] = r.seen_fail.get(sig, 0) + 1
        # Find has its own budget (search_max misses -> ask where -> stop); this stop kept it from the question (sim 2026-10-23)
        if r.seen_fail[sig] > 2 and a != "Find" and not self._search_open(r, a):
            yield self._finish(r, "failed", msg=f"{a} {o} failed again the same way: {reason}"); return _STOP
        # ── skill-level retry in place / ask the person (not planning) ──
        # only a sensor check disagrees with the skill's own success (the layers before it passed)
        r.failed_layers = {v.layer for v in rec.verifies if not v.ok}
        r.sensor_only = bool(rec.verifies) and all(v.ok for v in rec.verifies if v.layer != "sensor") \
            and any(v.layer == "sensor" and not v.ok for v in rec.verifies)
        no = refuted(rec.verifies)                     # the VLM / its Find said it did not happen: replan
        recover = None if no is not None else {"Find": self._recover_find, "Pick": self._recover_pick,
                                               "MoveTo": self._recover_move, "Open": self._recover_open, "Place": self._recover_place,
                                               "PutIn": self._recover_place}.get(a)
        out, extra = (yield from recover(r, st, reason, rule)) if recover else (None, self._refuted(r, st, no))
        if out is not None:
            return out
        # ── a new plan: the planner's suffix repair ──
        return (yield from self._replan_or_fail(r, st, f"{reason}{extra}", f"{a} {o} failed: {reason} (replan budget used)"))

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
        from ..run_control import cancel_requested
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
