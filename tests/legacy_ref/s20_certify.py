"""Reference copy for step S20: the generate-and-certify methods of AssistLoop EXACTLY as they were in assist_loop.py at 73b1a42
(lines 786-1076, before they moved to phases/certify.py): _plan_phase, _generate_and_certify, _plan_call, _subgoals, _lab_subgoals,
_has_blocks, _gate_repair, _restore_repair, _gate_repair_run, _repair_suffix, _tidy, _blocking, _untouched and _hard_findings.
Only the imports changed (absolute names; the module names the old methods read as globals of assist_loop.py are imported here).
Used by tests/test_certify_phase.py as the differential reference; never edit the bodies."""
import time
from typing import Optional

from robot_agent.contract import Unbindable
from robot_agent.core.planning import action_model, concrete, exec_integrity, persons, plan_tidy
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning.understand import LLMBudgetExceeded
from robot_agent.core.pack import actions as action_data, goal_semantics

_LOC_ACTIONS = set(action_data.names(action_data.default_table(), arg=("place", "container")))


class RefCertify:
    def _plan_phase(self, r):
        """The certified plan in r.steps. True when the run ended (plan only / no plan)."""
        yield r.ev("status", msg="Generating task plan...")
        r.restated = r.task + ("" if not r.dialogue else " (" + "; ".join(t for w_, t in r.dialogue if w_ == "user") + ")")
        r.searched, r.seen_far, r.uncertified = [], [], []
        yield from self._search_start(r)                 # may give a goal its source (search.py)
        r.goal_txt = goal_kinds.goal_text(r.goals)
        r.planner = self._make_planner()
        self._hook_progress(r.planner)
        if not r.plan_only and self.options.query("runs_goals", r):    # e.g. bt_goal: its declared trees run the goals, no planner call
            r.steps, r.certified, r.gate_findings, r.plan_error, r.run.plan_meta = [], True, [], None, {"method": "option"}; return False
        yield from self._generate_and_certify(r)
        skill_map = r.robot.skill_map
        plan_text = "\n".join(f"{x.get('skill') or skill_map.get(x['action'], x['action'])}::{x['object']}" for x in r.steps)
        yield r.ev("plan", steps=r.steps, plan_meta=r.run.plan_meta, warnings=list(r.gate_findings),
                   plan_text=plan_text, certified=r.certified, world=r.world.to_dict())
        if isinstance(r.plan_error, LLMBudgetExceeded) or (r.plan_error is not None and not r.steps):
            # the planner is down / the LLM-call budget is used: say so, not "no plan"
            yield self._llm_failed(r, r.plan_error, "planner"); return True
        if r.plan_only:
            yield self._finish(r, "planned", say=r.announcer.announce("plan_ready")); return True
        if not r.steps and not (r.goals and len(self._untouched(r.goals, [])) == len(r.goals)
                                and not (r.run.plan_meta or {}).get("error")):
            yield self._finish(r, "failed", msg="the planner produced no plan for the goals"); return True
        if not r.certified:
            # an uncertified plan never runs (the gate's findings are the reason)
            yield self._finish(r, "failed", msg="I could not make a plan that passes my checks: "
                                                + "; ".join(r.gate_findings[:3]))
            return True
        return False

    def _generate_and_certify(self, r):
        """The planner's plan, checked as written; critical findings go back to its sub-goal repair (if any)."""
        t_plan, r.plan_error = time.time(), None
        from robot_agent.core.run_control import call_cancellable, is_cancel
        r.compiled = goal_kinds.is_template(r.goals, goal_semantics.pack_of(self))
        if self.cfg.get("person_goals"):
            r.goals = goal_kinds.dedupe(r.goals)
        try:
            if r.compiled:
                # go / say / deliver goals: a fixed sequence of the robot's own actions, no LLM
                raw, meta = goal_kinds.compile_steps(
                    r.goals, r.world.arrived, r.robot.text("delivery", r.lang),
                    can_say="Say" in r.robot.plan_actions), {"method": "compiled"}
            else:
                raw, meta = self._plan_call(r, "generate_plan", r.restated, self._obs(r), [],
                                            what="plan", subgoals=self._subgoals(r))
        except Exception as e:
            if is_cancel(e):
                raise
            raw, meta, r.plan_error = [], {"error": str(e)}, e
        r.steps, tidied = self._tidy(r, self._ground_all(r, raw))     # provable no-op steps out (plan_tidy.py)
        probs = self._check(r.steps, r.world, r.places, r.goals)
        yield r.ev("gate", round=0, problems=[p_[1] for p_ in probs], plan=[f"{x['action']} {x['object']}" for x in r.steps],
                   **({"tidied": tidied} if tidied else {}))
        edits = []
        if self._blocking(r, r.steps) and not r.compiled and r.plan_error is None:
            edits += self._gate_repair(r)                         # symbolic repair first (plan_repair.py)
            probs = self._check(r.steps, r.world, r.places, r.goals)
            yield r.ev("gate", round="symbolic", edits=list(edits), problems=[p_[1] for p_ in probs],
                       plan=[f"{x['action']} {x['object']}" for x in r.steps])
        if any(p_[2] for p_ in probs) and not r.compiled:
            # the planner's sub-goal-local repair with the checker's findings (SubgoalRepair, if it has it)
            try:
                edited, llm_edits = self._repair_plan(r.planner, r.restated, self._obs(r), lambda s: self._ground_all(r, s),
                                                      r.world, r.places, r.goals, r.goal_txt, budget=r.llm_budget)
            except LLMBudgetExceeded as e:
                edited, llm_edits, r.plan_error = None, [], e
            edits += llm_edits
            # block edits that were LLM calls (an emptied block is the core's)
            r.block_edits = sum(1 for n in llm_edits if str(n).startswith("sub-goal ") and "emptied" not in str(n))
            if edited is not None:
                r.steps, more = self._tidy(r, edited)
                edits += more
                if self._blocking(r, r.steps):
                    edits += self._gate_repair(r)                 # the LLM's edits, repaired again
            probs = self._check(r.steps, r.world, r.places, r.goals)
            yield r.ev("gate", round=1, edits=edits, problems=[p_[1] for p_ in probs],
                       plan=[f"{x['action']} {x['object']}" for x in r.steps])
        blocking = self._blocking(r, r.steps)
        if blocking and r.steps and not r.compiled and r.plan_error is None and not getattr(r, "block_edits", 0):
            # a planner whose sub-goal blocks were edited already (and repaired symbolically) gets no fresh plan
            # still critical after the planner's repair (or it has none): one fresh plan with the findings,
            # certified again
            r.steps = self._tidy(r, self._refine_plan(r, blocking))[0]
            blocking = self._blocking(r, r.steps)
            yield r.ev("gate", round=2, problems=blocking, plan=[f"{x['action']} {x['object']}" for x in r.steps])
        r.certified, r.gate_findings = not blocking, blocking
        r.run.plan_meta = dict(meta or {}, plan_sec=round(time.time() - t_plan, 2), llm_plan=self._ground_all(r, raw),
                               edits=edits, certified=r.certified, gate_findings=blocking,
                               repaired_by_op=dict(getattr(r, "repaired_by_op", {})),
                               completed_by_gate=getattr(r, "repaired_by_op", {}).get("completed_by_gate", 0))

    def _plan_call(self, r, name, *args, what="plan", subgoals=None):
        """``r.planner.<name>(*args)`` under the run's LLM-call budget, counting the planner's REAL calls: a
        planner with a budget hook (``budget=``, SAGE >= 0.2.6) takes a unit before each of its calls; any other
        takes one unit before and its reported ``llm_calls`` - 1 after. ``subgoals`` go to a planner that takes
        them (F1.2)."""
        from robot_agent.core.run_control import call_cancellable
        from robot_agent.core.planning.base import accepts
        kw = {}
        if subgoals and accepts(r.planner, name, "subgoals"):
            kw["subgoals"] = list(subgoals)
        hooked = accepts(r.planner, name, "budget")
        if hooked:
            kw["budget"] = r.llm_budget
        else:
            r.llm_budget.take(what=what)
        raw, meta = call_cancellable(getattr(r.planner, name), *args, **kw)
        if not hooked:
            extra = int((meta or {}).get("llm_calls") or 1) - 1 if isinstance(meta, dict) else 0
            if extra > 0:
                r.llm_budget.used += extra
        return raw, meta

    def _subgoals(self, r) -> Optional[list]:
        """One sub-goal phrase per understood goal, in goal order (option ``sage_subgoals=goals``), else None
        (the planner decomposes the task itself)."""
        if self.cfg.get("sage_subgoals", "goals") != "goals" or not r.goals:
            return None
        if self.cfg.get("person_goals") and getattr(r.planner, "fixed_subgoals", False):
            return self._lab_subgoals(r)
        return goal_kinds.subgoals(r.goals, r.places, goal_semantics.pack_of(self))

    def _lab_subgoals(self, r) -> list:
        """``person_goals``: in a request that mixes template goals (go / say / deliver) with moves, a template goal
        is a sub-goal with its own steps (``goals.compile_steps``: MoveTo, Say with the exact sentence), so the
        planner expands the moves only and never paraphrases a sentence or invents a go. The speaker as the place
        to say it at is the speaker's ENV entry; a place the ENV does not know is not driven to. A request of
        template goals alone is compiled whole already, one of moves alone is unchanged."""
        kinds = {g.get("type") in goal_kinds.TEMPLATE_KINDS for g in r.goals}
        text = goal_kinds.subgoals(r.goals, r.places, goal_semantics.pack_of(self))
        if kinds != {True, False}:
            return text
        out, can_say = [], "Say" in r.robot.plan_actions
        for g, t in zip(r.goals, text):
            if g.get("type") not in goal_kinds.TEMPLATE_KINDS:
                out.append(t)
                continue
            h = dict(g)
            if goal_kinds.is_speaker(h.get("to")):
                h["to"] = persons.speaker_key(r.places) or ""
            elif h.get("to") and h["to"] not in r.places.env:
                h["to"] = ""
            steps = goal_kinds.compile_steps([h], None, r.robot.text("delivery", r.lang), can_say=can_say)
            out.append((t, steps) if steps else t)
        return out

    @staticmethod
    def _has_blocks(r) -> bool:
        from robot_agent.core.planning.base import subgoal_repair
        rep = subgoal_repair(r.planner)
        try:
            return bool(rep is not None and rep.blocks())
        except Exception:
            return False

    def _gate_repair(self, r) -> list:
        """The gate's symbolic repair of r.steps (plan_repair.py). Option ``gate_strict`` (round 3): when today's
        repair leaves a critical finding, repeat it from the same start with a per-goal score and keep that result
        only if it certifies the plan; otherwise today's result stands."""
        if not self.cfg.get("gate_strict"):
            return self._gate_repair_run(r)
        from robot_agent.core.planning.base import subgoal_repair
        rep = subgoal_repair(r.planner)
        try:
            blocks = rep.blocks() if rep is not None else None
        except Exception:
            blocks = None
        snap = lambda: (list(r.steps), [list(b.steps) for b in (blocks or [])], dict(getattr(r, "repaired_by_op", {})))  # noqa: E731
        pre = snap()
        notes = self._gate_repair_run(r)
        if not self._blocking(r, r.steps):
            return notes
        first = snap()
        self._restore_repair(r, blocks, pre)
        notes2 = self._gate_repair_run(r, per_goal=True)
        if not self._blocking(r, r.steps):
            self._lab("gate_strict", part="per_goal_repair", kept=True)
            return notes2 + ["per-goal repair certified the plan"]
        self._restore_repair(r, blocks, first)
        self._lab("gate_strict", part="per_goal_repair", kept=False)
        return notes

    @staticmethod
    def _restore_repair(r, blocks, sn) -> None:
        r.steps = list(sn[0])
        for b, st in zip(blocks or [], sn[1]):
            b.steps = list(st)
        r.repaired_by_op = dict(sn[2])

    def _gate_repair_run(self, r, per_goal: bool = False) -> list:
        """The gate's symbolic repair of r.steps (plan_repair.py): operators proved by the checker, on the
        planner's sub-goal blocks when it has them. Returns the edit notes; counts go to r.repaired_by_op."""
        from robot_agent.core.planning import plan_repair
        from robot_agent.core.planning.base import subgoal_repair
        rep = subgoal_repair(r.planner)
        try:
            blocks = rep.blocks() if rep is not None else None
        except Exception:
            blocks = None
        caps = self.robot.model_capabilities()
        ground = lambda s: self._ground_all(r, s)                                        # noqa: E731
        score = lambda s: len(self._blocking(r, self._tidy(r, ground(s))[0], per_goal))  # noqa: E731
        steps, notes, counts = plan_repair.repair(
            r.steps, score, r.world, r.places, r.goals, caps=caps, actions=self.robot.plan_actions,
            release=sorted(caps.release_actions), hand_goal=lambda g: bool(g.get("hand")),
            blocks=blocks or None, join=rep.join_blocks if blocks else None, ground=ground,
            complete=str(self.cfg.get("gate_complete", "0")) in ("1", "true", "True"),
            person_goals=bool(self.cfg.get("person_goals")), reached=self._reaches,
            no_refetch=bool(self.options.query("no_refetch")), keep_end=self.options.query("keep_after_dedupe") or False, **self.options.query("repair_kwargs", r, default={}))
        acc = r.__dict__.setdefault("repaired_by_op", {})
        for k, v in counts.items():
            acc[k] = acc.get(k, 0) + v
        if notes:
            new = ground(rep.join_blocks(blocks)) if blocks else steps
            r.steps = self._tidy(r, new)[0]
        return notes

    def _repair_suffix(self, r, suffix) -> list:
        """F1.3c: the gate's symbolic repair (plan_repair.py) of a replan's suffix too, from the belief now: a
        suffix that opens a container with the hand full ran as the planner wrote it (the gate only repaired the
        first plan). ``finish``: a suffix that dropped a goal still to do gets that goal's delivery back. An edit is
        kept only when the suffix's critical findings drop; no LLM call."""
        from robot_agent.core.planning import plan_repair
        if not suffix:
            return suffix
        ground = lambda s: self._ground_all(r, s)                                        # noqa: E731
        score = lambda s: len(self._blocking(r, self._tidy(r, ground(s))[0]))           # noqa: E731
        if score(suffix) == 0:
            return suffix
        caps = self.robot.model_capabilities()
        steps, notes, counts = plan_repair.repair(
            suffix, score, r.world, r.places, r.goals, caps=caps, actions=self.robot.plan_actions,
            release=sorted(caps.release_actions), hand_goal=lambda g: bool(g.get("hand")),
            only=("hoist", "dedupe", "action", "unclose", "finish"), reached=self._reaches, **self.options.query("repair_kwargs", r, default={}))
        if not notes:
            return suffix
        acc = r.__dict__.setdefault("repaired_by_op", {})
        for k, v in counts.items():
            acc[k] = acc.get(k, 0) + v
        return self._tidy(r, ground(steps))[0]

    def _tidy(self, r, steps) -> tuple:
        """plan_tidy.tidy of `steps` on the belief now: (steps, notes); `plan_tidy` off: unchanged."""
        steps = self.options.transform("on_plan", steps, r)       # e.g. place_find: a Find of the place the robot is at is dropped
        if not self.cfg["plan_tidy"] or not steps: return steps, []
        ctx = dict(capabilities=self.robot.model_capabilities(), actions=self.robot.plan_actions,
                   openable=r.places.openable, containers=r.places.openable)
        return plan_tidy.tidy(steps, r.world, r.goals, self._reaches, ctx)

    def _blocking(self, r, steps, per_goal: bool = False) -> list:
        """The findings that keep a plan from running (the certified gate): the checker's critical
        ones on the plan as written, and steps the robot cannot bind. ``per_goal`` (round 3, the repair's score
        only): the joined 'does not achieve' finding counts once per unmet goal."""
        unt = self._untouched(r.goals, steps)
        goals = [g for g in r.goals if g not in unt] if unt and len(unt) < len(r.goals) else r.goals
        probs = [p_[1] for p_ in self._check(steps, r.world, r.places, goals) if p_[2]] if steps else []
        out = list(dict.fromkeys(probs + self.options.transform("on_plan_findings", self._hard_findings(r, steps), r, steps)))
        if per_goal and any(f.startswith(exec_integrity.MISSING_PREFIX) for f in out):
            out = exec_integrity.per_goal_findings(out, len(self._missing(steps, r.world, goals)))
        return out

    @staticmethod
    def _untouched(goals, steps) -> list:
        """'on' goals the plan never moves whose object's place is unknown: they may hold already
        (the planner left them out); the run looks at the destination at the end instead (D1)."""
        picked = {st.get("object") for st in steps if st.get("action") == "Pick"}
        return [g for g in goals if g.get("type") == "on" and g.get("to") and not g.get("from")
                and not g.get("from_any") and g.get("object") not in picked]

    def _hard_findings(self, r, steps, start: int = 0) -> list:
        """Steps (from index `start`) no belief can make runnable: an action the robot does not
        have, a place not in the ENV, a step its adapter cannot bind (simulated as written)."""
        out, w = [], r.world.copy()
        for i, st in enumerate(steps):
            a, o = st.get("action", ""), st.get("object") or ""
            if i >= start:
                if a not in r.robot.plan_actions and not concrete.skill_only(st):
                    out.append(f"step {i + 1} '{a} {o}': the robot has no action {a}")
                elif a in _LOC_ACTIONS and (not st.get("grounded", True) or o.startswith("@")):
                    out.append(f"step {i + 1} '{a} {o}': {o.lstrip('@')} is not a place the robot can go to")
                else:
                    try:
                        self.robot.bind(dict(st), w)
                    except Unbindable as e:
                        out.append(f"step {i + 1} '{a} {o}': {e}")
                    except Exception:
                        pass                          # an adapter error is the step's own, at run time
            action_model.apply(st, w, inplace=True)
        return out
