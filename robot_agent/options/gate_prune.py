"""Option ``gate_prune`` (default off): an Open / Close of a place the pack declares cannot be opened is dropped from the plan.

The planner writes ``Open shelf`` for a place that has no door (an open shelf, a table). The skill refuses, the run replans or gives up. The
repair operator that removes such steps (plan_repair ``unopenable``) runs only with ``person_goals``; this option does it for any robot loop.

Fail open: a place is "not openable" only when its ENV entry carries an explicit ``openable`` field that is false (or the adapter's
``is_openable`` says no for an entry that has the field). An entry without the field, a name that is not an ENV key, a step of an
unknown action, a place a goal of type open / close names: kept. The actions are the pack's roles ``open`` and ``close``; none declared =
nothing is done. Hook ``on_plan``. Keys: ``enabled``. Lab flag ``gate_prune``: ``{place, index}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="gate_prune",
    keys=(Key("enabled", "bool", False, aliases=("gate_prune",)),),
    hooks=("on_plan",),
    order=84,
    enabled="enabled",
    doc="An Open / Close of a place the ENV declares not openable (and no goal names) is dropped from the plan.",
)


class GatePrune(Option):
    def on_plan(self, steps, run):
        """``steps`` without the Open / Close of places declared not openable; None when nothing is dropped."""
        acts = {a for a in (self.svc.role("open"), self.svc.role("close")) if a}
        env = getattr(run.places, "env", {}) or {}
        if not acts or not env:
            return None
        named = {g.get("object") for g in run.goals or [] if isinstance(g, dict) and g.get("type") in ("open", "close")}
        out = []
        for i, st in enumerate(steps):
            o, e = st.get("object"), env.get(st.get("object"))
            if st.get("action") in acts and o not in named and isinstance(e, dict) and "openable" in e \
                    and not run.places.openable(o):
                self.svc.lab(SPEC.name, place=o, index=i)
                continue
            out.append(st)
        return out if len(out) != len(steps) else None


register(SPEC, GatePrune)
