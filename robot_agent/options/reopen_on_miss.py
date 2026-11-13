"""Option ``reopen_on_miss`` (default off): a Find that sees nothing inside the container the robot has just opened is tried once more
after closing and opening that container again.

A door or drawer can report "open" and still not show what is inside (opened only part way, a camera that sees the door). The plan
Open -> Find then ends in a miss that says nothing about the object: the search marks the place searched and goes elsewhere. With the
option, when the Find that follows an Open of a container fails ABSENT, the remedy the pack declares is run on that container (once per
container) and the Find is made again, with no miss counted.

Pack data: ``planner.recovery.reopen_on_miss``, a list of the pack's actions run on the container (e.g. ``[Close, Open]``); none
declared, an action the robot lacks, or no ``find`` / ``open`` role in ``planner.action_roles`` = the option does nothing.
Generic: no object or place name; "just opened" is the last completed step being the pack's Open of the container the Find is at.
Hook ``on_step_failed``. Keys: ``enabled`` (legacy ``reopen_on_miss``). Lab flag ``reopen_on_miss``: ``{container, object}``.
"""

from __future__ import annotations

from robot_agent.contract import FailureCode
from robot_agent.core.options import NEXT, Key, Option, OptionSpec, register

DATA = "reopen_on_miss"           # the manifest's planner.recovery.reopen_on_miss: [action, ...]


def remedy(planner) -> list:
    """The actions the pack declares, or [] (a mapping ``recovery`` with a list of non-empty names; anything else = none)."""
    rec = (planner or {}).get("recovery") if isinstance(planner, dict) else None
    v = rec.get(DATA) if isinstance(rec, dict) else None
    return [str(a) for a in v if str(a).strip()] if isinstance(v, (list, tuple)) else []


SPEC = OptionSpec(
    name="reopen_on_miss",
    keys=(Key("enabled", "bool", False, aliases=("reopen_on_miss",)),),
    hooks=("on_step_failed",),
    order=90,
    enabled="enabled",
    doc="A Find that misses inside the container just opened closes and opens it again once (pack data recovery.reopen_on_miss), then looks again.",
)


class ReopenOnMiss(Option):
    def on_step_failed(self, run, st, code, reason, a, o):
        find, open_ = self.svc.role("find"), self.svc.role("open")
        if find is None or open_ is None or a != find or code is not FailureCode.ABSENT or not run.completed:
            return None
        last = run.completed[-1]
        c = last.get("object")
        if last.get("action") != open_ or c != run.world.arrived or c not in run.world.opened:
            return None                                       # the Find is not right after an Open of the container it is at
        done = run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {}).setdefault("done", set())
        fix = remedy(getattr(self.svc.robot.manifest, "planner", None))
        if c in done or not fix or not set(fix) <= set(self.svc.robot.plan_actions):
            return None
        done.add(c)
        run.steps[run.i:run.i] = [{"action": x, "object": c} for x in fix]       # the failed Find then runs again, no miss counted
        self.svc.lab(SPEC.name, container=c, object=o)
        yield run.ev("recover", at_index=run.i, kind=SPEC.name, msg=f"the {o} is not in {c}: " + ", ".join(x.lower() for x in fix) + ", looking again")
        return NEXT


register(SPEC, ReopenOnMiss)
