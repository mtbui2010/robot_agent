"""Option ``replan_guard`` (default off): a replan repairs the right block, after a goal change from the new goals, and does not run a suffix the action model rejects.

Three parts, each its own bool key (all on when the option is on; a key set to False turns that part off, so a run can be ablated):

* ``block_fix`` (hook ``on_planner_kwargs``): the planner is built with ``replan_block_fix=True`` (embodiplan SAGE): the failed block is found by
  lining the completed steps up with the plan, so the steps the engine added or dropped do not move the repair into another block.
* ``goal_rebuild`` (hook ``on_goal_replan``): after a mid-run change of the goals the rest of the plan is planned for sub-goals rebuilt from the NEW
  goals (service ``subgoals``), and the plan's task text becomes the new goals' text; before, a suffix repair of the old block ran with the old task.
* ``critical_refine`` (hook ``on_replan_findings``): a replan's suffix is simulated from the belief now with the action model; the findings whose code
  the pack declares critical join the engine's own, so the one existing refine sees them, and when they remain the run ends uncertified (the engine's
  rule for an unbindable step).
* ``guarded_run`` (with ``critical_refine``): a finding the engine's own run-time check refuses BEFORE the step moves (``verify.symbolic_check`` on the
  belief the suffix reaches there: the guard of ``_guard`` replans at that step) only steers the one refine: it is added when a refine follows this
  call, and a refined suffix (or one with no refine left) whose findings are all of that kind RUNS (lab ``part: "guarded"``). The run ends
  uncertified only on findings the run-time check cannot catch. Off: such findings end the run as before.
* ``release_dedupe`` (hook ``on_plan``): a release the action model refuses with an empty hand that repeats the plan's last release (same action, same
  place, nothing taken since) is dropped. A release is a step that emptied the hand in the replay; no action name is in the code.

Pack data ``planner.options.replan_guard.critical``: ``{"codes": [<action-model code>, ...], "source": "<label>"}`` (or ``"assumed": true``). No
codes, no label = the third part does nothing (fail open). A recipient the goal names has no action-model code: it is not covered.
The MoveTo a release lacks is not this option's: the pack declares it as ``precond_repair``'s ``near`` need (that hook runs on a replan's suffix too).
Keys: ``enabled`` (legacy ``replan_guard``), ``block_fix``, ``goal_rebuild``, ``critical_refine``, ``guarded_run``, ``release_dedupe``, ``critical`` (dict,
from the manifest). Lab flags ``replan_guard``: ``{part: "rebuild" | "critical" | "guarded" | "dedupe", ...}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.options.config import resolve_key
from robot_agent.core.planning import action_model, concrete
from robot_agent.core.planning.verify import symbolic_check


def codes_of(critical) -> tuple:
    """The declared critical codes; () when the data has no label (source or assumed) or is malformed."""
    if not isinstance(critical, dict) or not (isinstance(critical.get("source"), str) and critical["source"].strip() or critical.get("assumed") is True):
        return ()
    codes = critical.get("codes")
    return tuple(c for c in codes if isinstance(c, str) and c.strip()) if isinstance(codes, (list, tuple)) else ()


SPEC = OptionSpec(
    name="replan_guard",
    keys=(Key("enabled", "bool", False, aliases=("replan_guard",)), Key("block_fix", "bool", True), Key("goal_rebuild", "bool", True),
          Key("critical_refine", "bool", True), Key("guarded_run", "bool", True), Key("release_dedupe", "bool", True), Key("critical", "dict", {})),
    hooks=("on_planner_kwargs", "on_goal_replan", "on_replan_findings", "on_plan"),
    order=90,
    enabled="enabled",
    doc="A replan repairs the failed step's own block, is built from the new goals after a goal change, and a suffix with a declared critical finding goes through the one refine.",
)


class ReplanGuard(Option):
    def on_planner_kwargs(self, kwargs):
        """The planner's constructor keys plus ``replan_block_fix``; None when that part is off."""
        return {**kwargs, "replan_block_fix": True} if self.cfg.get("block_fix") else None

    def on_goal_replan(self, run):
        """Sub-goals from the goals now (and the plan's task text from them) for the repair after a goal change; None: the engine's own repair."""
        if not self.cfg.get("goal_rebuild") or not run.goals:
            return None
        sub = self.svc.subgoals(run)
        if not sub:
            return None
        run.restated = run.goal_txt
        self.svc.lab(SPEC.name, part="rebuild", subgoals=len(sub))
        return list(sub)

    def on_replan_findings(self, bad, run, suffix):
        """The engine's findings plus the declared critical ones of the suffix simulated from the belief now; None when there is nothing to add."""
        if not self.cfg.get("critical_refine") or not suffix:
            return None
        codes = codes_of(self.cfg.get("critical") or resolve_key(SPEC, SPEC.key("critical"), {}, {}, getattr(self.svc.robot.manifest, "planner", None)))
        if not codes:
            return None
        steer = self._steers(run) if self.cfg.get("guarded_run") else True
        new = [(f, g) for f, g in self._critical(run, suffix, set(codes)) if f not in bad]
        if not steer and any(g for _f, g in new):
            self.svc.lab(SPEC.name, part="guarded", findings=sum(1 for _f, g in new if g))
            new = [(f, g) for f, g in new if not g]
        if not new:
            return None
        self.svc.lab(SPEC.name, part="critical", findings=len(new))
        return list(bad) + [f for f, _g in new]

    def _steers(self, run) -> bool:
        """Whether a refine follows this call (then the findings the run-time check refuses itself steer it; else they are left to that check).
        The first call of a replan is its suffix; a second call with the same count of recorded replans is the refine (``replan_round``)."""
        n, left = self.svc.replan_round(run)
        refine = getattr(run, "replan_guard_round", None) == n
        run.replan_guard_round = n
        return not refine and left > 0

    def _critical(self, run, suffix, codes) -> list:
        """(finding 'step i A o: why', guarded) for every step whose rule finding is one of ``codes``, the suffix run from the belief now;
        ``guarded``: the engine's run-time check (``symbolic_check``) refuses that step in that belief before it moves."""
        robot, w = self.svc.robot, run.world.copy()
        caps = robot.model_capabilities()
        ctx = dict(capabilities=caps, actions=robot.plan_actions, openable=run.places.openable, containers=run.places.openable,
                   object_places={g["object"]: g.get("from") for g in run.goals if g.get("from")})
        out = []
        for i, st in enumerate(suffix):
            if concrete.skill_only(st):
                continue
            hits = [v for v in action_model.check(st, w, **ctx) if v.code in codes and v.is_error]
            if hits:
                guarded = not symbolic_check(st, w, capabilities=caps, openable=run.places.openable)[0]
                out.extend((f"step {i + 1} '{st.get('action')} {st.get('object') or ''}': {v.reason}", guarded) for v in hits)
            action_model.apply(st, w, inplace=True, capabilities=caps)
        return out

    def on_plan(self, steps, run):
        """``steps`` without a release the action model refuses with an empty hand that repeats the last release (same action and object, the hand
        emptied by it and not filled since); None when there is none."""
        if not self.cfg.get("release_dedupe") or not steps or getattr(run, "world", None) is None:
            return None
        caps, w = self.svc.robot.model_capabilities(), run.world.copy()
        openable = getattr(getattr(run, "places", None), "openable", None)
        out, last, dropped = [], None, []
        for i, st in enumerate(steps):
            key, held = (st.get("action"), st.get("object")), w.holding
            if held is None and key == last and not concrete.skill_only(st) and not symbolic_check(st, w, capabilities=caps, openable=openable)[0]:
                dropped.append(i)
                continue
            action_model.apply(st, w, inplace=True, capabilities=caps)
            last = key if held is not None and w.holding is None else (None if w.holding is not None else last)
            out.append(st)
        if not dropped:
            return None
        self.svc.lab(SPEC.name, part="dedupe", dropped=dropped)
        return out


register(SPEC, ReplanGuard)
