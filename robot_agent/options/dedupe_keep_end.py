"""Option ``dedupe_keep_end`` (default off): the gate's de-duplication of a repeated fetch does not cut the steps after it.

``plan_repair.op_dedupe`` removes a Find / Pick of an object that the plan already placed at its goal. When no release follows the repeat, the
cut ran to the LAST step of the plan, which removed the way home (the final MoveTo). With the option the cut ends at the repeated run's own
Find / Pick. With a release after the repeat, nothing changes. No pack data. Service ``keep_after_dedupe()`` (asked by the gate's repair).
Key: ``enabled``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="dedupe_keep_end",
    keys=(Key("enabled", "bool", False, aliases=("dedupe_keep_end",)),),
    hooks=("on_start",),
    services=("keep_after_dedupe",),
    order=86,
    enabled="enabled",
    doc="The gate's removal of a repeated fetch ends at the repeat's own Find / Pick: the steps after it (the way home) stay.",
)


class DedupeKeepEnd(Option):
    def keep_after_dedupe(self):
        """Service: True (the repair keeps the steps after a repeated fetch)."""
        return True

    def on_start(self, run) -> None:
        """No per-run state; the hook is declared so the option is dispatched."""


register(SPEC, DedupeKeepEnd)
