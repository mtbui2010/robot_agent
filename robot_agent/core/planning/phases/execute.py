"""The execute phase of AssistLoop: the step loop, the end of the plan, the guard before a step, and one step's run.

What: ``ExecutePhase`` is a mixin with ``_execute_phase``, ``_refind_after_pause``, ``_plan_ended``, ``_coverage_gap``,
``_look_at_untouched``, ``_precondition``, ``_guard_arrived``, ``_guard`` and ``_run_step``, moved VERBATIM out of
``assist_loop.py`` (Step S22 of the core generalization); the only change is the relative import inside ``_coverage_gap``
(``run_control``: one package level deeper) and the module imports below (the old module-level names of ``assist_loop.py``).
Why: the loop file keeps the common procedure only (docs/proposals/generalize_core_design.md, "Phase pipeline"). What stays
in ``assist_loop.py``: ``_reconcile``, ``_sense`` and ``_save_world`` (they call ``current()``, which tests patch on that
module), ``_post_check`` (tests/test_pack_verifiers.py pins it there) and ``_step_failed`` (tests/test_find_ladder.py reads
its source there). ``_guard`` and ``_run_step`` stay callable through the class (``AssistLoop._guard``; option bt_goal's
``try_step`` calls ``_run_step``). The hooks ``on_run_goals`` and ``on_step`` are called at the same points as before.
Switch off: remove the ``ExecutePhase`` line from ``PHASE_MIXINS`` in ``phases/__init__.py`` and put the methods back.
"""
from __future__ import annotations

import time

from robot_agent.contract import FailureCode, Unbindable
from .. import goals as goal_kinds
from .. import langdata, request_coverage, speech
from .. import loop as _loop
from .. import reconcile as sensed
from ..records import StepRecord, VerifyResult
from ..robot import run_skill
from ..sensor_health import find_precheck
from ..understand import ask_json
from ..verify import symbolic_check, verdict

_NEXT, _STOP, _DONE = _loop._NEXT, _loop._STOP, _loop._DONE      # the end markers of a step: the loop's own values
_vr_list = _loop._vr_list                                        # the step_verify event's list (the loop's own function)


class ExecutePhase:
    # ── 3. execute: guard each step; new plans only from the planner ──────
    def _execute_phase(self, r):
        """Run the plan step by step. True when the run ended."""
        r.placed = self.options.query("belief_table", default={})      # option object_words: looked up by the object's words
        r.pick_tries, r.move_tries, r.helped, r.seen_fail, r.seen_at, r.find_fails = {}, {}, set(getattr(r, "pre_helped", ())), {}, {}, dict(getattr(r, "pre_fails", {}))   # pre_helped / pre_fails: set before the plan (option ask_source)
        r.queries = self._run_queries = {}            # object -> the description the person chose (which_one)
        r.refinds, r.found_at, r.relooked = {}, {}, set()
        r.completed, r.replans, r.i, r.replan_error = [], 0, 0, None
        r.code_tries, r.doubted, r.failures, r.goal_checks, r.health = {}, set(getattr(r, "pre_doubted", ())), [], [], None   # pre_doubted: opened before the plan (service opened_now)
        r.world_at_start = r.world.copy()
        r.suspended = 0                      # tasks this run put on the task stack (a NEW one went first)
        if (yield from self.options.decide_flow("on_run_goals", r)) is _STOP:     # e.g. bt_goal: the goals its trees run, before the plan's end
            return True
        while True:
            # a paused run holds here, between two steps; after a resume (RunManager on_resume) the belief
            # is reconciled and a Pick on a Find made before the pause looks again first
            if (yield from self._pause_point(r)):
                yield from self._refind_after_pause(r)
            stop = self._cancelled(r)
            if stop:
                yield stop; return True
            out = yield from self._apply_pending(r)          # what the person said meanwhile
            if out is None:
                out = (yield from self._plan_ended(r)) if r.i >= len(r.steps) else (yield from self._run_step(r, r.steps[r.i]))
            if out is _DONE and r.suspended:
                out = yield from self._resume_suspended(r)  # back to the task the NEW one interrupted
            if out is _STOP:
                return True
            if out is _DONE:
                return False

    def _refind_after_pause(self, r):
        """After a pause the found object may have moved: forget where it was seen and look again before
        the Pick that would act on it."""
        st = r.steps[r.i] if r.i < len(r.steps) else {}
        if r.world.found and st.get("action") == "Pick":
            o = st.get("object") or r.world.found
            r.world.found = None; r.world.found_pose = None
            r.steps[r.i:r.i] = [{"action": "Find", "object": o}]
            yield r.ev("recover", at_index=r.i, kind="refind", msg=f"resumed after a pause: looking for the {o} again")

    def _plan_ended(self, r):
        """The plan ran out: _DONE when the goals hold (in the belief, then looked at), else look at goals
        the plan left out (they may hold already), else a replan (_NEXT) or _STOP."""
        unmet = []
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            unmet = yield from self._observe_goals(r)
            if unmet is None:
                return _STOP                                     # cancelled while looking
            if not unmet:
                if self.cfg.get("request_coverage"):
                    out = yield from self._coverage_gap(r)
                    if out is not None:
                        return out
                return _DONE
        elif self._look_at_untouched(r):
            yield r.ev("status", msg="checking the goals the plan did not touch")
            return _NEXT
        why = ("; ".join(u["evidence"]["observed"] for u in unmet) if unmet else
               "; ".join(self._missing(r.completed, r.world_at_start, r.goals)))
        e_ = self._sage_replan(r, {"action": "Check", "object": "goals"},
                               "the plan ended but the goals are not reached: " + why)
        if e_ is None or r.i >= len(r.steps):
            if getattr(r, "replan_error", None) is not None:    # the planner died: say that, not "goals do not hold"
                yield self._no_replan_end(r, ""); return _STOP
            fail = dict(unmet[0]) if unmet else {}
            msg = (f"the goals do not hold: {why}" if unmet else
                   self._no_replan(r, "plan finished but the goals do not hold: " + r.goal_txt))
            yield self._finish(r, "failed", msg=msg, say=(why if unmet else None), **fail); return _STOP
        yield e_
        return _NEXT

    def _coverage_gap(self, r):
        """Option ``request_coverage`` (once per run, request_coverage.py): before the run claims it is done, one LLM call
        names the parts of the request with no goal or no executed step. None = claim as before (nothing found, an error,
        a declined part); else the next outcome of the execute loop (_NEXT: the part was added as a goal, or _STOP)."""
        from ...run_control import call_cancellable, is_cancel
        if getattr(r, "coverage_checked", False):
            return None
        r.coverage_checked = True
        said = [t for w_, t in r.dialogue if w_ == "user"]
        request = " ".join([r.task, self.original or ""] + said)
        steps = [f"{s_.get('action')} {s_.get('object')}" for s_ in r.completed]
        lim = request_coverage.limits(getattr(self.robot.manifest, "planner", None))     # pack data; a cap not declared is no cap
        try:
            d = ask_json(self._llm(), request_coverage.prompt(" ".join([self.original or r.task] + said),
                                                              goal_kinds.goal_text(r.goals), steps, lim.get("max_steps")),
                         request_coverage.SCHEMA, budget=r.llm_budget, retries=0, where="request coverage",
                         call=call_cancellable, passthrough=is_cancel, options=self._sampling(0))
        except Exception as e:                          # noqa: BLE001 - fail open: the run says done as before
            if is_cancel(e):
                raise
            return None
        covered = request_coverage.covered_names(r.goals, r.completed, r.places)
        missing = request_coverage.parts(d, request, covered, lim.get("max_parts"))
        self._lab("request_coverage", parts=missing, dropped=len(request_coverage.parts(d, request, (), lim.get("max_parts"))) - len(missing))
        if not missing:
            return None
        part = missing[0]                               # the first part that survived the filter
        # the robot's own fixed phrase with the part in the person's own words (no English sentence with a
        # foreign-script part reaches the translator); a pack without the phrase in the person's language: no question
        if not langdata.is_prompt_lang(r.lang or "en") and speech.phrase(r.robot, "q_coverage", r.lang) is None:
            self._lab("request_coverage_no_phrase", lang=r.lang)
            return None
        q = speech.LocalText("q_coverage", {"what": part})
        yield r.ev("clarify", question=q, round=r.confirms, kind="coverage")
        ans, ok_, _rest = self._ask_yes_no(r, q)
        stop = self._cancelled(r)
        if stop:
            yield stop
            return _STOP
        if ok_ is None:
            # no answer or not understood: the goals were observed to hold, so the run claims done as before
            self._lab("request_coverage_unanswered", part=part)
            return None
        if ok_ is not True:
            return None                                 # the person declined: nothing is claimed beyond that
        r.dialogue += [("robot", q), ("user", ans)]
        if (yield from self._goal_edit(r, {"intent": "add", "text": part})) is not _NEXT:
            # the part could not become a goal: the goals held before the question, so the run claims done as before
            # (never a give-up made by this option on an episode whose goals hold)
            self._lab("request_coverage_not_added", part=part)
            return None
        return (yield from self._repair_goals(r, part))

    def _look_at_untouched(self, r) -> bool:
        """Append a MoveTo + Find at the destination of every unmet goal the run never moved (once each);
        a Find that sees the object there makes the goal hold by observation. True when steps were added."""
        looked = r.__dict__.setdefault("looked_goals", set())
        at, add = r.world.arrived, []
        for g in self._untouched(r.goals, r.completed):
            o, to = g["object"], g["to"]
            if (o, to) in looked or r.placed.get(o) == to or r.world.holding == o or to not in r.places.env:
                continue
            looked.add((o, to))
            if at != to:
                add.append({"action": "MoveTo", "object": to, "grounded": True}); at = to
            add.append({"action": "Find", "object": o, "verify_goal": to})
        r.steps[r.i:r.i] = add
        return bool(add)

    def _precondition(self, r, st) -> str:
        """Why the step cannot run in the belief ('' when it can)."""
        a, o, world = st["action"], st["object"], r.world
        if a in ("Place", "PutIn", "Open", "Close") and o in r.places.env and world.arrived != o:
            return f"the robot is at {world.arrived}, not at {o}"
        if a in ("Open", "Close") and world.holding:
            return f"the robot cannot {a.lower()} {o} while holding the {world.holding}"
        ok_pre, why_ = symbolic_check(st, world, openable=r.places.openable, capabilities=r.robot.model_capabilities())
        return "" if ok_pre else why_

    def _guard_arrived(self, r, st):
        """A non-sensor `arrived` (operator / belief) that a fresh pose contradicts is stale: fixed, the
        ``recover kind=reconcile`` event (None when nothing changed)."""
        if not self.cfg["reconcile"]:
            return None
        fix = sensed.guard_arrived(r.world, r.robot, r.node, self._sense(r), r.places.env, self.cfg["arrive_tol"], st)
        if fix is None:
            return None
        self._save_world()
        return r.ev("recover", at_index=r.i, kind="reconcile", was=fix["old"], now=fix["new"],
                    msg=f"the robot is at {fix['new'] or 'no known place'}, not at {fix['old']} "
                        f"({fix['source']} belief; pose {fix['detail']})")

    def _guard(self, r, st):
        """Preconditions before the robot moves (violations go to the planner): None (go on), _NEXT, _STOP."""
        a, o, world = st["action"], st["object"], r.world
        fixed = self._guard_arrived(r, st)
        if fixed is not None:
            yield fixed
        why = self._precondition(r, st)
        if why and a == "Pick" and world.holding and not self._holding(r.node, default=True):
            # the belief holds something, the hand is empty (a stale belief): fix it and look again
            yield r.ev("recover", at_index=r.i, kind="reconcile", msg=f"the {world.holding} is not in the gripper")
            world.holding = world.holding_since = None
            why = self._precondition(r, st)
        if not why and a in ("Place", "PutIn") and world.holding:
            # still in the hand? (dropped on the way: `place` would otherwise go and pick something up)
            if not self._holding(r.node, default=True):
                lost = world.holding
                r.memory.see(lost, None, "dropped")
                world.holding = world.holding_since = None
                yield r.ev("recover", at_index=r.i, kind="dropped", msg=f"{lost} is no longer in the gripper")
                if self._refind_dropped(r, st, r.robot.policy.rule_for(FailureCode.DROPPED)):
                    return _NEXT                        # the contract's DROPPED rule: find it again, pick it up
                yield self._finish(r, "failed", msg=f"I dropped the {lost} on the way to {o.replace('@', ' in the ')}. "
                                                    f"Please pick it up; I can't pick things up from the floor.",
                                   code=FailureCode.DROPPED, device=self.robot.holding_sensor(),
                                   evidence={"step": f"{a} {o}", "observed": f"{lost} is no longer in the gripper"})
                return _STOP
        if not why:
            return None
        sig = ("pre", a, o, why)
        r.seen_fail[sig] = r.seen_fail.get(sig, 0) + 1
        e_ = self._sage_replan(r, st, f"precondition: {why}") if r.seen_fail[sig] <= 1 else None
        if e_ is None:
            yield self._no_replan_end(r, f"{a} {o}: {why}"); return _STOP
        yield e_
        return _NEXT

    def _run_step(self, r, st):
        """Guard, bind, run and check one step: _NEXT, _DONE (goals reached) or _STOP."""
        a, o, started = st["action"], st["object"], time.time()
        new = yield from self.options.decide_flow("on_step", r, st)      # e.g. reach_delivery: a stale put-down becomes a hand-over
        if new is not None:
            st = new
            a, o = st["action"], st["object"]
        out = yield from self._guard(r, st)
        if out is not None:
            return out
        try:
            mapped = self._bind(st, r.world)
        except Unbindable as e:
            return (yield from self._replan_or_fail(r, st, f"no skill for this step: {e}", f"{a} {o}: {e}"))
        if mapped is None:                                   # no-op action
            r.run.steps.append(StepRecord(index=r.i, action=a, object=o, skill=None, status="success",
                                          started_at=started, ended_at=time.time(),
                                          verifies=[VerifyResult("isdone", True, "no-op")]))
            r.completed.append(st); r.i += 1
            yield r.ev("step_done", index=r.i - 1, status="success", skill=None, world=r.world.to_dict())
            return _NEXT
        skill, params = mapped
        rec = StepRecord(index=r.i, action=a, object=o, skill=skill, params=params,
                         status="running", started_at=started)
        yield r.ev("step_start", index=r.i, action=a, object=o, skill=skill,
                   say=self._announce(r, "step_start", st, a, o))
        # manifest interruptible: false (a grasp, a drawer pull): a stop meanwhile waits for it to end
        # a stale camera before a Find: perception_unavailable without running it (sensor_health)
        res = find_precheck(r.robot, r.node, st, self.cfg["sensor_health"])
        if res is None:
            res = run_skill(self.sr, skill, params, r.node, log_fn=self._log_fn(r.i),
                            interruptible=r.robot.interruptible(skill))
        rec.result = res.to_legacy()
        stop = self._cancelled(r)
        if stop:
            rec.status = "aborted"; r.run.steps.append(rec)
            if res.ok:
                self._effect(st, r.world, r.memory, r.placed)   # it finished: the belief keeps what it did
            yield stop; return _STOP
        vres = self._post_check(r, st, skill, res, params, rec)
        rec.verifies = vres
        ok, reason = verdict(vres)
        rec.ended_at = time.time()
        yield r.ev("step_verify", index=r.i, verifies=_vr_list(vres))
        if ok:
            return (yield from self._step_succeeded(r, st, rec))
        return (yield from self._step_failed(r, st, skill, rec, res, reason))
