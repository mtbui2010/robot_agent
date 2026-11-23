"""A made-up recovery policy of the made-up line robot (Step S14 goldens and tests): ``s14_line.fasten_again``.

What: a failed step runs again in place (the same step, the plan unchanged) while the rule's counter of that step is at
most the entry's ``times`` (default 1); then the planner, as for an action with no policy. Registered from the robot's own
package (a pack's ``callables`` must be a module next to its manifest). Every fact here is made up (assumed).
"""

from __future__ import annotations

from robot_agent.core.options import NEXT
from robot_agent.core.pack import register


@register("recovery", "s14_line.fasten_again")
def fasten_again(run, step, reason, rule, ctx):
    """The same step again, ``times`` times per action and object (counted by the run's rule counter), then the planner."""
    tried = sum(n for (_code, a, o), n in run.code_tries.items() if a == step.get("action") and o == step.get("object"))
    if tried > int(ctx.entry.get("times", 1)):
        return None, ""
    yield run.ev("recover", at_index=run.i, kind="retry", msg=f"{step.get('action')} {step.get('object')} again ({reason})")
    return NEXT, ""
