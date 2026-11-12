"""Option ``confirm_look`` (default off): a sighting that follows a miss is checked by one more look before the goal counts as met.

The end-of-run check looks for the object at its destination. When the first look misses, it looks again (a camera can miss), and
believes the second look. A camera can also see what is not there. A miss followed by a sighting is two looks that disagree: with the
option, a third look decides, and the sighting counts only when it is seen again (two of three). The cost is one more look, only when
the first two disagree.

Generic: no object, place or word of a language; it only calls the same Find the goal check uses. Hook: ``on_second_look``.
Keys: ``enabled`` (legacy ``confirm_look``). Lab flag ``confirm_look``: ``{object, place, seen_again}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="confirm_look",
    keys=(Key("enabled", "bool", False, aliases=("confirm_look",)),),
    hooks=("on_second_look",),
    order=50,
    enabled="enabled",
    doc="A sighting after a missed look at the goal place needs a third look (two of three) before the goal counts as met.",
)


class ConfirmLook(Option):
    def on_second_look(self, run, obj, place):
        """The first look missed, the second saw ``obj`` at ``place``: look a third time. (ok, evidence, skill)."""
        seen, skill = self.svc.look_for(run, obj)
        self.svc.lab("confirm_look", object=obj, place=place, seen_again=bool(seen))
        if seen:
            return True, f"the {obj} is seen at {place} (seen again on a third look)", skill
        return False, f"the {obj} was seen once in three looks at {place}: not confirmed", skill


register(SPEC, ConfirmLook)
