"""Goal change and the task stack of AssistLoop, as a phase mixin (what the person says during a run, taken between two steps).

What: ``GoalEditPhase`` holds ``_apply_pending`` (MODIFY / ADD / DROP / NEW said since the last step boundary), ``_goal_edit``,
``_llm_goal_change``, ``_held_dropped``, ``_new_command``, ``_llm_new_task``, ``_suspend_for`` (the current task goes on the task
stack), ``_resume_suspended`` and ``_repair_goals`` (the planner's suffix repair for the changed goals). They were moved VERBATIM out
of ``assist_loop.py`` (Step S21 of the core generalization); the only changes are the relative imports inside five method bodies
(``run_control``: one package level deeper) and the module imports below (the old module-level names of ``assist_loop.py``). The
hooks ``on_goal_edit``, ``on_goal_reread``, ``on_goal_unapplied`` and ``on_goal_replan`` are called exactly as before.
Why: the loop keeps the common procedure; the mid-run change of goals is one phase with one interface (docs/proposals/
generalize_core_design.md, "Phase pipeline"). ``core/planning/goal_change.py`` is the pure reading of a change and keeps its name;
this module is the loop's use of it.
Stays in ``assist_loop.py``: ``summary``, ``_goals_hold``, ``_reconcile`` and the rest of the loop; the moved methods reach them
through ``self``. ``_replan_or_fail`` is in ``replan.py``.
Switch off: remove the ``GoalEditPhase`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put the methods back into
``assist_loop.py`` (after ``_replan_or_fail``, before ``_execute_phase``) and delete this module.
"""
from __future__ import annotations

import json
import re
import types

from .. import goal_change
from .. import goals as goal_kinds
from .. import loop as _loop

_DONE, _NEXT, _STOP = _loop._DONE, _loop._NEXT, _loop._STOP     # the end markers of a round: the loop's own values


class GoalEditPhase:
    # ── what the person says during the run (Run.pending, taken between two steps) ──
    def _apply_pending(self, r):
        """MODIFY / ADD / DROP / NEW said since the last step boundary, applied before the next step
        (proposal 3.3): None (nothing changed), _NEXT (goals changed, the plan repaired) or _STOP."""
        from ...run_control import current_run
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
        from ...run_control import is_cancel
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
        from ...run_control import is_cancel
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
        from ...run_control import suspend_task
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
        from ...run_control import mark_resumed, resume_task
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
