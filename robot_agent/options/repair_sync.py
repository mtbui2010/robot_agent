"""Option ``repair_sync`` (default off): the gate's symbolic repair edits the plan the gate scores, and a replan's suffix that lets go of a goal's object elsewhere is repaired or reported.

What: (1) ``gate_sync``: the repair (``plan_repair.repair``) builds its plan from the planner's sub-goal blocks, but the gate scores the plan
after the ``on_plan`` options and the tidy (steps added, steps dropped); the service ``repair_kwargs`` gives the repair a function that lines
the blocks up with that plan (longest common subsequence on (action, object); an added step joins the block of the step before it), so an
operator sees the steps an option added (a Close with the hand full is moved after the release an option spliced in). (2) ``finish_rest``: the
suffix operator ``finish`` passes over a goal the suffix neither holds nor lets go of (one done before the replan) instead of giving up at it, so
the pending goal behind it gets its delivery back. (3) ``off_goal`` (hook ``on_replan_findings``): when that repair did not fix it, a suffix whose
LAST release of a goal's object (``on`` / ``in``) is at another place than the goal's (no Pick of it after) gets the finding ``release_off_goal``,
so the engine's one refine sees it; a staging put-down that is picked again later is not one.
Why: the class "the gate's repair is blind to the steps options added, and a replan's suffix puts the goal object down at the wrong place"
(gate findings and replan suffixes in the robot's own logs); no model call, no pack data: the release is a step that empties the hand in the
replay of the action model, two hands or more = no finding (one-hand replay).
Switch off: key ``enabled`` False (the default; legacy run option ``repair_sync``), or one part with ``gate_sync`` / ``finish_rest`` /
``off_goal`` False. Off: the service answers nothing and the repair runs as before. Lab flag ``repair_sync``: ``{part: sync | off_goal, ...}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import action_model

FINDING = "release_off_goal"

SPEC = OptionSpec(
    name="repair_sync",
    keys=(Key("enabled", "bool", False, aliases=("repair_sync",)), Key("gate_sync", "bool", True), Key("finish_rest", "bool", True),
          Key("off_goal", "bool", True)),
    hooks=("on_replan_findings",),
    services=("repair_kwargs",),
    order=91,
    enabled="enabled",
    doc="The gate's repair edits the plan the options made (blocks lined up with it); a suffix that lets go of a goal's object elsewhere is repaired or reported.",
)


def _key(st) -> tuple:
    return st.get("action"), st.get("object")


def _matches(a: list, b: list) -> dict:
    """{index in b: index in a} of a longest common subsequence of the two key lists."""
    t = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            t[i][j] = t[i + 1][j + 1] + 1 if a[i] == b[j] else max(t[i + 1][j], t[i][j + 1])
    out, i, j = {}, 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out[j], i, j = i, i + 1, j + 1
        elif t[i + 1][j] >= t[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def align(owned: list, steps: list) -> list:
    """``steps`` as an owned plan [(block, step)]: a step of the blocks keeps its block, an added step joins the block of the step before it
    (the first matched one's for leading steps); a step of the blocks that ``steps`` no longer has is gone. No steps: ``owned`` unchanged."""
    if not steps or not owned:
        return owned
    m = _matches([_key(s) for _o, s in owned], [_key(s) for s in steps])
    owners = [owned[m[j]][0] if j in m else None for j in range(len(steps))]
    first = next((o for o in owners if o is not None), owned[0][0])
    out, last = [], first
    for o, st in zip(owners, steps):
        last = last if o is None else o
        out.append((last, dict(st)))
    return out


def last_releases(world, suffix, caps) -> dict:
    """{object: (index, step)} of the step that let go of each object last in the suffix replayed from ``world`` (none when picked again after)."""
    w, out = world.copy(), {}
    for i, st in enumerate(suffix):
        held = w.holding
        action_model.apply(st, w, inplace=True, capabilities=caps)
        if held and w.holding is None:                  # the step emptied the hand: a release
            out[held] = (i, st)
        elif w.holding and w.holding != held:           # taken (again)
            out.pop(w.holding, None)
    return out


class RepairSync(Option):
    def repair_kwargs(self):
        """Service: the extra arguments of the gate's and the suffix's repair (``sync``, ``finish_rest``); None when both parts are off."""
        kw = {**({"sync": self.sync} if self.cfg.get("gate_sync") else {}), **({"finish_rest": True} if self.cfg.get("finish_rest") else {})}
        return kw or None

    def sync(self, owned, steps) -> list:
        """The repair's owned plan lined up with the plan the gate scores (lab ``part: sync`` when they differ)."""
        out = align(owned, steps)
        if [s for _o, s in out] != [s for _o, s in owned]:
            self.svc.lab(SPEC.name, part="sync", blocks=len(owned), plan=len(out))
        return out

    def on_replan_findings(self, bad, run, suffix):
        """The engine's findings plus ``release_off_goal`` for every ``on`` / ``in`` goal whose object the suffix lets go of last at another place;
        None when there is none (or with two hands)."""
        if not self.cfg.get("off_goal") or not suffix or getattr(run, "world", None) is None:
            return None
        caps = self.svc.robot.model_capabilities()
        if getattr(caps, "hands", 1) >= 2:
            return None
        rel = last_releases(run.world, suffix, caps)
        new = []
        for g in run.goals or ():
            o, to = g.get("object"), g.get("to")
            if g.get("type") in ("on", "in") and to and o in rel and rel[o][1].get("object") != to:
                i, st = rel[o]
                new.append(f"{FINDING}: step {i + 1} '{st.get('action')} {st.get('object') or ''}', goal place of {o}: {to}")
        new = [f for f in new if f not in bad]
        if not new:
            return None
        self.svc.lab(SPEC.name, part="off_goal", findings=len(new))
        return list(bad) + new


register(SPEC, RepairSync)
