"""Option ``goals_only`` (default off): an understand-only run. The goals are understood (or taken with ``accept_goals``) and grounded; nothing is planned or run.

What: service ``runs_goals`` says yes, so the planner is not called; hook ``on_run_goals`` (order 1, before ``bt_goal``) ends the run before any step
with ``done`` ``{status: understood, goals}``, said as the read-back sentence. The goals reported are those the understand step handed over: service
``rank_first`` answers no (no ranking call) and keeps the goals as they were before the plan phase's search set-up could fill a ``from`` from this
robot's own belief. The read-back to the person stays the loop's own (``confirm_task`` and the other understand keys); with ``plan_only`` the plan wins.
Why: a fleet manager asks one robot to understand a request and gets typed goals back to hand to any robot (docs/fleet_seams.md).
Switch off: off by default; on per run with ``goals_only=1`` (run option, ``$ROBOT_AGENT_GOALS_ONLY``, manifest ``planner.options.goals_only.enabled``).
Event: lab flag ``goals_only`` ``{goals: <count>}``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register

FLAG = "goals_only"

SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)),),
    hooks=("on_run_goals",),
    services=("runs_goals", "rank_first"),
    order=1,
    enabled="enabled",
    doc="An understand-only run: the goals are understood and grounded, then the run ends `done` {status: understood, goals}; nothing is planned or run.",
)


def reported(run_goals, handed) -> list:
    """The goals an understand-only run reports: the ones handed over by the understand step (``handed``, kept before the plan phase's
    search set-up), else the run's goals. Copies. Pure."""
    return [dict(g) for g in (handed if handed is not None else (run_goals or ()))]


class GoalsOnly(Option):
    spec = SPEC

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(FLAG, {})

    def rank_first(self, run, obj):
        """Service: no ranked first place for ``obj`` (no extra model call); the goals as handed over are kept at the first ask."""
        self._state(run).setdefault("handed", [dict(g) for g in getattr(run, "goals", None) or ()])
        return False

    def runs_goals(self, run):
        """Service: yes: the planner is not called, ``on_run_goals`` ends the run."""
        return True

    def on_run_goals(self, run):
        """Flow: the ``done`` of an understand-only run, before any step. STOP: the run ended."""
        goals = reported(getattr(run, "goals", None), self._state(run).get("handed"))
        self.svc.lab(FLAG, goals=len(goals))
        say = self.svc.readback(run, goals) if goals else None
        yield self.svc.finish(run, "understood", goals=goals, **({"say": say} if say else {}))
        return STOP


register(SPEC, GoalsOnly)
