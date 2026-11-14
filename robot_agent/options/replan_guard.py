"""Option ``replan_guard`` (default off): a replan repairs the right block, after a goal change from the new goals, and does not run a suffix the action model rejects.

Three parts, each its own bool key (all on when the option is on; a key set to False turns that part off, so a run can be ablated):

* ``block_fix`` (hook ``on_planner_kwargs``): the planner is built with ``replan_block_fix=True`` (embodiplan SAGE): the failed block is found by
  lining the completed steps up with the plan, so the steps the engine added or dropped do not move the repair into another block.
* ``goal_rebuild`` (hook ``on_goal_replan``): after a mid-run change of the goals the rest of the plan is planned for sub-goals rebuilt from the NEW
  goals (service ``subgoals``), and the plan's task text becomes the new goals' text; before, a suffix repair of the old block ran with the old task.
* ``critical_refine`` (hook ``on_replan_findings``): a replan's suffix is simulated from the belief now with the action model; the findings whose code
  the pack declares critical join the engine's own, so the one existing refine sees them, and when they remain the run ends uncertified (the engine's
  rule for an unbindable step).

Pack data ``planner.options.replan_guard.critical``: ``{"codes": [<action-model code>, ...], "source": "<label>"}`` (or ``"assumed": true``). No
codes, no label = the third part does nothing (fail open). A recipient the goal names has no action-model code: it is not covered.
Keys: ``enabled`` (legacy ``replan_guard``), ``block_fix``, ``goal_rebuild``, ``critical_refine``, ``critical`` (dict, from the manifest).
Lab flags ``replan_guard``: ``{part: "rebuild" | "critical", ...}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.options.config import resolve_key
from robot_agent.core.planning import action_model, concrete


def codes_of(critical) -> tuple:
    """The declared critical codes; () when the data has no label (source or assumed) or is malformed."""
    if not isinstance(critical, dict) or not (isinstance(critical.get("source"), str) and critical["source"].strip() or critical.get("assumed") is True):
        return ()
    codes = critical.get("codes")
    return tuple(c for c in codes if isinstance(c, str) and c.strip()) if isinstance(codes, (list, tuple)) else ()


SPEC = OptionSpec(
    name="replan_guard",
    keys=(Key("enabled", "bool", False, aliases=("replan_guard",)), Key("block_fix", "bool", True), Key("goal_rebuild", "bool", True),
          Key("critical_refine", "bool", True), Key("critical", "dict", {})),
    hooks=("on_planner_kwargs", "on_goal_replan", "on_replan_findings"),
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
        new = [f for f in self._critical(run, suffix, set(codes)) if f not in bad]
        if not new:
            return None
        self.svc.lab(SPEC.name, part="critical", findings=len(new))
        return list(bad) + new

    def _critical(self, run, suffix, codes) -> list:
        """'step i A o: why' for every step whose rule finding is one of ``codes``, the suffix run from the belief now."""
        robot, w = self.svc.robot, run.world.copy()
        caps = robot.model_capabilities()
        ctx = dict(capabilities=caps, actions=robot.plan_actions, openable=run.places.openable, containers=run.places.openable,
                   object_places={g["object"]: g.get("from") for g in run.goals if g.get("from")})
        out = []
        for i, st in enumerate(suffix):
            if concrete.skill_only(st):
                continue
            for v in action_model.check(st, w, **ctx):
                if v.code in codes and v.is_error:
                    out.append(f"step {i + 1} '{st.get('action')} {st.get('object') or ''}': {v.reason}")
            action_model.apply(st, w, inplace=True, capabilities=caps)
        return out


register(SPEC, ReplanGuard)
