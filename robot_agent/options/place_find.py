"""Option ``place_find`` (default off): a Find whose object is a PLACE (an ENV key) the robot is at, or is about to move to, is dropped.

The planner writes ``MoveTo microwave@kitchen, Find microwave@kitchen, Open microwave@kitchen``. A Find looks for a thing, not for the place
the robot stands at: the look at a place key is always "absent", the run treats it as a miss in the search, and the plan never gets to its
Open. Arriving at the place (the MoveTo) is the whole of reaching it, so the Find adds nothing.

Generic: the test is "the step's object is a key of the ENV the robot has" and "the robot is there (its belief, or a MoveTo of the plan)".
A Find of a thing that is not an ENV key is never touched; a Find of a place the robot is not going to is kept (the gate and the planner
decide). Hook ``on_plan``. Keys: ``enabled`` (legacy ``place_find``). Lab flag ``place_find``: ``{place, index}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="place_find",
    keys=(Key("enabled", "bool", False, aliases=("place_find",)),),
    hooks=("on_plan",),
    order=80,
    enabled="enabled",
    doc="A Find of a place (an ENV key) the robot is at, or moves to just before, is dropped from the plan.",
)


class PlaceFind(Option):
    def on_plan(self, steps, run):
        """``steps`` without the Finds of the place the robot stands at; None when nothing is dropped."""
        find, move = self.svc.role("find"), self.svc.role("move")
        env = getattr(run.places, "env", {}) or {}
        if find is None or move is None:                       # the pack declares no such actions: nothing to do
            return None
        at, out = run.world.arrived, []
        for i, st in enumerate(steps):
            o = st.get("object")
            if st.get("action") == move:
                at = o
            elif st.get("action") == find and o in env and o == at:
                self.svc.lab("place_find", place=o, index=i)
                continue
            out.append(st)
        return out if len(out) != len(steps) else None


register(SPEC, PlaceFind)
