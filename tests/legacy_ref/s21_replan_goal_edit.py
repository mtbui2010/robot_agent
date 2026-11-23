"""Reference copy for step S21: the replan, goal-change and task-stack methods of AssistLoop EXACTLY as they were in assist_loop.py at
f984a53 (lines 803-1046, before they moved to phases/replan.py and phases/goal_edit.py; _refine_plan, 786-801, stays in the loop and is
not copied). RefReplan: _sage_replan, _replan_suffix, _no_replan, _no_replan_end, _replan_or_fail. RefGoalEdit: _apply_pending, _goal_edit, _llm_goal_change, _held_dropped,
_new_command, _llm_new_task, _suspend_for, _resume_suspended, _repair_goals.
Only the imports changed (absolute names; the module names the old methods read as globals of assist_loop.py are imported here).
Used by tests/test_replan_goal_edit_phases.py as the differential reference; never edit the bodies."""
import json
import re
import types
from typing import Optional

from robot_agent.core.planning import goal_change
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning.loop import _DONE, _NEXT, _STOP
from robot_agent.core.planning.understand import LLMBudgetExceeded


class RefReplan:
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
        from robot_agent.core.run_control import call_cancellable, is_cancel
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


class RefGoalEdit:
    def _apply_pending(self, r):
        """MODIFY / ADD / DROP / NEW said since the last step boundary, applied before the next step
        (proposal 3.3): None (nothing changed), _NEXT (goals changed, the plan repaired) or _STOP."""
        from robot_agent.core.run_control import current_run
        run = current_run()
        events = run.take_pending() if run is not None else []
        changed = False
        for e in events:
            out = yield from (self._new_command(r, run, e) if e.get("intent") == "new" else self._goal_edit(r, e))
            if out is _STOP:
                return _STOP
            changed = changed or out is _NEXT
        if not changed:
            return None
        return (yield from self._repair_goals(r, "; ".join(str(e.get("text") or "") for e in events)))

    def _goal_edit(self, r, e):
        """MODIFY (goal diff), ADD (merged into the goals), DROP (goals removed; the held object first).
        _NEXT when the goals changed; None when the change could not be applied (the person is told)."""
        intent, text = e.get("intent") or "modify", str(e.get("text") or "")
        ch = goal_change.read(text, intent, r.goals, r.places, r.world.holding,
                              reached=lambda g: self._goals_hold([g], r.world, r.placed, r.completed, r.world_at_start.arrived))
        self.options.observe("on_goal_edit", r, text)
        ch = ch if ch is not None and not ch.empty else self._llm_goal_change(r, text)
        if ch is None or ch.empty:
            turn = types.SimpleNamespace(text=text, change=ch)         # e.g. unapplied_change: ask once, read again, remember
            if (yield from self.options.decide_flow("on_goal_unapplied", r, turn)) is _STOP:
                return _STOP
            ch = turn.change
        if ch is None or ch.empty:
            yield r.ev("goal_change", applied=False, intent=intent, text=text, goals=r.goals,
                       say=self._in_lang(f"Sorry, I could not apply that: {text}. I will carry on.", r.lang))
            return None
        put_back = self._held_dropped(r, ch)
        r.goals = ch.goals + ([put_back] if put_back else [])
        say = self.summary(r.goals) if r.goals else "OK, I will not do that."
        yield r.ev("goal_change", applied=True, intent=intent, text=text, how=ch.how, goals=r.goals,
                   added=ch.added, removed=ch.removed, changed=[n for _o, n in ch.changed],
                   say=self._in_lang(("OK. " if r.goals else "") + say, r.lang))
        return _NEXT

    def _llm_goal_change(self, r, text):
        """The understand LLM reads the change against the task (the cheap reading failed): GoalChange or None."""
        from robot_agent.core.run_control import is_cancel
        dialogue = list(r.dialogue) + [("robot", self.summary(r.goals)), ("user", text)]
        try:
            u = self._understand(r.task, r.places, r.memory, r.world, dialogue, budget=r.llm_budget)
        except Exception as e:
            if is_cancel(e):
                raise
            return None
        goals, q = self._ground_goals(u["goals"], r.places, r.memory, " ".join([r.task, text]), latest=text)
        self.options.observe("on_goal_reread", r, text, u, goals, q)
        if q or u.get("status") != "clear":
            return None
        key = lambda g: (g.get("type"), g.get("object"), g.get("to"))
        old, new = {key(g) for g in r.goals}, {key(g) for g in goals}
        return goal_change.GoalChange(goals=goals, added=[g for g in goals if key(g) not in old],
                                      removed=[g for g in r.goals if key(g) not in new], how="llm")

    def _held_dropped(self, r, ch):
        """A goal of the object in the hand was dropped: the goal that puts it back where it was taken
        from (cfg drop_held 'put_back', the default) or down here ('place_here'); else None."""
        held = r.world.holding
        if not held or any(g.get("object") == held for g in ch.goals) or not any(
                g.get("object") == held for g in ch.removed):
            return None
        src = next((g.get("from") for g in ch.removed if g.get("object") == held and g.get("from")), "")
        if not src:
            moves = [st["object"] for st in r.completed if st.get("action") == "MoveTo"]
            src = moves[-1] if moves else (r.world.arrived or "")
        to = r.world.arrived if self.cfg.get("drop_held") == "place_here" else src
        return {"type": "on", "object": held, "from": "", "to": to} if to else None

    def _new_command(self, r, run, e):
        """NEW during a run: someone who outranks the run's starter goes first; else the person is asked
        "first, or after this task?" (no answer: after). First = the current task is suspended on the
        task stack (goals, achieved goals, belief) and resumed afterwards. None / _NEXT / _STOP."""
        text = str(e.get("text") or "")
        ch = goal_change.read(text, "new", [], r.places) or self._llm_new_task(r, text)
        if ch is None or not ch.goals:
            yield r.ev("goal_change", applied=False, intent="new", text=text, goals=r.goals,
                       say=self._in_lang(f"Sorry, I did not understand: {text}. I will finish this task first.", r.lang))
            return None
        new = ch.goals
        key = lambda g: (g.get("type"), g.get("object"), g.get("to"))
        if {key(g) for g in new} <= {key(g) for g in r.goals}:
            yield r.ev("goal_change", applied=False, intent="new", text=text, goals=r.goals,
                       say=self._in_lang(f"I am already doing that. {self.summary(r.goals)}", r.lang))
            return None
        first = run is not None and run.outranked_by(e.get("role"))
        if not first:
            q = (f"Someone asked me to {self.summary(new)[len('I will '):].rstrip('.')}. Should I do that first? "
                 f"If not, I will do it after the current task.")
            yield r.ev("clarify", question=q, round=0, kind="new_task")
            ans = self._ask(q, r.node, self._log_fn(r.i), r.lang)          # silence: after the current task
            stop = self._cancelled(r)
            if stop:
                yield stop; return _STOP
            first = self.yes_no(ans)[0] is True or bool(re.search(r"\b(now|first|right away)\b", ans.lower()))
        if not first:
            r.goals = r.goals + [g for g in new if g not in r.goals]
            yield r.ev("goal_change", applied=True, intent="new", text=text, how=ch.how, goals=r.goals, added=new,
                       say=self._in_lang(f"OK, after this task. {self.summary(new)}", r.lang))
            return _NEXT
        yield from self._suspend_for(r, new, text)
        return _NEXT

    def _llm_new_task(self, r, text):
        from robot_agent.core.run_control import is_cancel
        try:
            u = self._understand(text, r.places, r.memory, r.world, [], budget=r.llm_budget)
        except Exception as e:
            if is_cancel(e):
                raise
            return None
        goals, q = self._ground_goals(u["goals"], r.places, r.memory, text)
        return None if q or u.get("status") != "clear" else goal_change.GoalChange(goals=goals, added=goals, how="llm")

    def _suspend_for(self, r, new, text):
        """Suspend the current task on the task stack and make ``new`` the goals. An object in the hand
        is put down here first (the suspended task picks it up from here again)."""
        from robot_agent.core.run_control import suspend_task
        held, here = r.world.holding, r.world.arrived
        old = [dict(g) for g in r.goals]
        put_down = []
        if held and here and not r.places.openable(here):
            put_down = [{"type": "on", "object": held, "from": "", "to": here}]
            for g in old:
                if g.get("object") == held:
                    g["from"] = here
        achieved = [g for g in old if self._goals_hold([g], r.world, r.placed, r.completed, r.world_at_start.arrived)]
        frame = suspend_task(r.task, old, achieved, r.world.to_dict(), reason=f"new task first: {text}")
        r.saved_goals = getattr(r, "saved_goals", []) + [old]          # the stack is the run's; this a fallback
        r.suspended += 1
        r.goals = put_down + [dict(g) for g in new]
        yield r.ev("goal_change", applied=True, intent="new", text=text, goals=r.goals, suspended=old,
                   suspended_frame=frame.to_dict() if frame is not None else None,
                   say=self._in_lang(f"OK, first that. {self.summary(new)} Then I will go back to my task.", r.lang))

    def _resume_suspended(self, r):
        """The interrupting task is done: take the suspended one back (task stack), observe again
        (reconcile), and repair the plan from here. _NEXT, _DONE (nothing left to do) or _STOP."""
        from robot_agent.core.run_control import mark_resumed, resume_task
        frame = resume_task()
        old = frame.goals if frame is not None else r.saved_goals[-1]
        r.saved_goals = r.saved_goals[:-1]
        r.suspended -= 1
        fixed = self._reconcile(r, "resume_task")       # observe again (RunManager on_resume fired)
        mark_resumed()
        if fixed is not None:
            yield fixed
        r.goals = [dict(g) for g in old]
        yield r.ev("task_resumed", goals=r.goals, say=self._in_lang("Now back to my task. " + self.summary(r.goals), r.lang))
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            return _DONE
        return (yield from self._repair_goals(r, "resumed the task the new one interrupted"))

    def _repair_goals(self, r, said):
        """The goals changed: the planner's suffix repair from here. _NEXT or _STOP."""
        r.goal_txt = goal_kinds.goal_text(r.goals)
        r.run.obs = "goals: " + json.dumps(r.goals, ensure_ascii=False)
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            r.steps = r.steps[:r.i]
            return _NEXT
        r.replan_subgoals = self.options.decide("on_goal_replan", r)      # e.g. replan_guard: sub-goals rebuilt from the new goals
        out = yield from self._replan_or_fail(r, {"action": "Change", "object": "goals"}, f"the user changed the goals ({said})",
                                              f"could not replan for the changed goals: {r.goal_txt}")
        r.replan_subgoals = None
        return out
