"""Replan of AssistLoop, as a phase mixin (the planner's suffix repair after a failed step, and the plan refinement of the gate).

What: ``ReplanPhase`` holds ``_sage_replan`` (the planner's suffix repair from step ``r.i``, within ``max_replans``),
``_replan_suffix``, ``_no_replan``, ``_no_replan_end`` and ``_replan_or_fail``. They were moved VERBATIM out of ``assist_loop.py``
(Step S21 of the core generalization); the only changes are the relative imports inside two method bodies (``run_control``: one
package level deeper) and the module imports below (the old module-level names of ``assist_loop.py``). ``_refine_plan`` (the
gate's refinement, not a replan) STAYS in the loop: ``tests/test_certify_phase.py::test_what_stays_is_still_in_the_loop_file``
pins it there. The service ``no_replan`` and the hook ``on_replan_findings`` are called exactly as before.
Why: the loop keeps the common procedure; replan is one phase with one interface, the seam where the replan policy becomes data
of the robot pack (docs/proposals/generalize_core_design.md, "Phase pipeline"). The task stack and goal change are in
``goal_edit.py`` (``core/planning/goal_change.py`` is the pure reading of a change and has another name).
Stays in ``assist_loop.py``: ``_refine_plan``, ``_obs``, ``_ground_all``, ``_check``, ``_llm_failed``, ``_finish`` and the rest of
the loop; the moved methods reach them through ``self``.
Switch off: remove the ``ReplanPhase`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put the methods back into
``assist_loop.py`` (after ``_refine_plan``, before ``_execute_phase``) and delete this module.
"""
from __future__ import annotations

from typing import Optional

from .. import loop as _loop
from ..understand import LLMBudgetExceeded

_NEXT, _STOP = _loop._NEXT, _loop._STOP            # the end markers of a round: the loop's own values


class ReplanPhase:
    def _sage_replan(self, r, failed, reason) -> Optional[dict]:
        """The planner's suffix repair from step r.i. Returns the event to yield, or None when out of budget or
        the new suffix fails the gate (``r.uncertified``: why)."""
        if self.options.query("no_replan", r, failed):    # e.g. bt_goal: a tree's own step; no planner call, the step stays failed
            r.i += 1; return r.ev("status", msg=f"{failed.get('action')} {failed.get('object')} failed, not replanned: {reason}")
        r.uncertified = []
        if r.replans >= self.cfg["max_replans"] or getattr(r, "compiled", False):
            # a compiled plan has no planner to repair it: the failure ends the run
            return None
        r.replans += 1
        world = r.world
        ers = f"E: {failed.get('action')} {failed.get('object')} failed. R: {reason}. S: robot at {world.arrived}, holding {world.holding or 'nothing'}; goals: {r.goal_txt}"
        r.replan_error = None
        suffix, ers, bad = self._replan_suffix(r, failed, ers)
        if bad and r.replans < self.cfg["max_replans"]:
            r.replans += 1                            # one refinement with the gate's findings
            suffix, ers, bad = self._replan_suffix(r, failed, ers + "; the new plan was rejected: " + "; ".join(bad))
        if r.replan_error is not None:
            # the planner is down: the old rest of the plan is not run either (the caller ends the run with it)
            r.run.replans.append({"at_index": r.i, "failed": failed, "reason": ers, "suffix_len": 0, "problems": []})
            r.steps = r.steps[:r.i]
            return None
        if bad:
            # an uncertified suffix never runs: the future of the plan is dropped
            r.uncertified = bad
            r.run.replans.append({"at_index": r.i, "failed": failed, "reason": ers, "suffix_len": len(suffix),
                                  "problems": bad, "certified": False})
            r.steps = r.steps[:r.i]
            return None
        probs = [p_[1] for p_ in self._check(r.completed + suffix, r.world_at_start, r.places, r.goals) if p_[2]]
        r.run.replans.append({"at_index": r.i, "failed": failed, "reason": ers, "suffix_len": len(suffix),
                              "problems": probs})
        r.steps = r.steps[:r.i] + suffix
        return r.ev("replan", at_index=r.i, reason=ers, problems=probs, search=getattr(r, "steered", None) or None,
                    plan=[f"{x['action']} {x['object']}" for x in suffix],
                    say=r.announcer.announce("replan"), world=world.to_dict())

    def _replan_suffix(self, r, failed, ers):
        """The planner's suffix for the failure `ers`, grounded, repaired: (suffix, ers + any planner error, its findings)."""
        from ...run_control import call_cancellable, is_cancel
        try:
            suffix, _m = self._plan_call(r, "replan", r.restated, r.completed, failed, ers, self._obs(r), [],
                                         what="replan", subgoals=getattr(r, "replan_subgoals", None))
            suffix = self._tidy(r, self._search_steer(r, self._ground_all(r, suffix or [])))[0]
        except Exception as e:
            if is_cancel(e):
                raise
            r.replan_error = e                       # kept: the run ends with it (_no_replan_end)
            return [], ers + f"; replan error: {e}", []
        suffix = self._repair_suffix(r, suffix)
        return suffix, ers, self.options.transform("on_replan_findings", self._hard_findings(r, suffix), r, suffix)

    @staticmethod
    def _no_replan(r, fail_msg) -> str:
        """Why there is no new plan: its suffix failed the gate, else `fail_msg`."""
        bad = getattr(r, "uncertified", None)
        return f"the new plan did not pass my checks: {'; '.join(bad[:3])}" if bad else fail_msg

    def _no_replan_end(self, r, fail_msg) -> dict:
        """The `done` of a run that needed a new plan and got none: the planner's error when it failed (it is
        down: said as such, with its FailureCode), else ``_no_replan``'s reason."""
        if getattr(r, "replan_error", None) is not None:
            return self._llm_failed(r, r.replan_error, "planner")
        return self._finish(r, "failed", msg=self._no_replan(r, fail_msg))

    def _replan_or_fail(self, r, failed, reason, fail_msg):
        """A replan, else end the run with `fail_msg`: _NEXT or _STOP."""
        e_ = self._sage_replan(r, failed, reason)
        if e_ is None:
            yield self._no_replan_end(r, fail_msg); return _STOP
        yield e_
        return _NEXT
