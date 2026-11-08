"""Option ``hazard``: the robot's own safety rules refuse a goal, or drop it and say so at the end (SAGE-v2 F4.3, docs/safety.md).

The rules are the robot's SAFETY PROFILE (manifest ``safety.profile``; ``core/safety`` is the engine that reads it). This module
is the option around that engine: the gate after the understand step, the question "does the safety layer forbid this goal"
that other steps ask, the partial-refusal sentence at the end and the ``refusals`` of the ``done`` event.

Keys: ``rules`` (legacy ``hazard_rules``; ``core`` | ``pack`` | ``off``, default ``core``; run option and environment only, never
the manifest) and ``receivers`` (legacy ``hazard_receiver``; turns on the profile rules with ``enabled_by: hazard.receivers``).
Hooks: ``on_goals`` (the gate), ``on_goals_met`` (the partial-refusal sentence), ``on_finish`` (the ``refusals`` field).
Services: ``forbids`` (the sentence that forbids a goal), ``refused_goals``, ``gate_off``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import speech
from robot_agent.core.safety import find_rule, profile_of

SPEC = OptionSpec(
    name="hazard",
    keys=(Key("rules", "str", "core", aliases=("hazard_rules",), sources=("run", "env"), choices=("core", "profile", "pack", "off")),
          Key("receivers", "bool", False, aliases=("hazard_receiver",))),
    hooks=("on_goals", "on_goals_met", "on_finish"),
    services=("forbids", "refused_goals", "gate_off"),
    order=10,
    doc="Safety rules of the robot's profile refuse a goal (or drop it and say so); product x place and product x receiver rules.",
)


def support_goals(goals, refused) -> list:
    """The open / close goals that only served the refused goals (the container a refused put goes into or
    comes from) and that no kept goal uses: refused with them. Else the person would hear "I will open the
    fridge" for a request that was a put, say no, and never reach a refusal."""
    kept = [g for g in goals if all(g is not b for b in refused)]
    uses = lambda k: any(h.get("type") in ("on", "in") and k in (h.get("to"), h.get("from")) for h in kept)   # noqa: E731
    return [g for g in kept if g.get("type") in ("open", "close") and not uses(g.get("object"))
            and any(g.get("object") in (b.get("to"), b.get("from")) for b in refused)]


def refusal_record(goal, verdict) -> dict:
    """One entry of the ``done`` event's ``refusals`` (docs/safety.md): the rule, the reason and the goal refused."""
    return {"kind": "safety", "rule": verdict.rule, "why": verdict.say,
            "goal": {k: str(goal.get(k) or "") for k in ("type", "object", "said", "from", "to", "near_person")}}


class Hazard(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault("hazard", {})

    # ── the one place that reads the options ────────────────────────────────
    def verdict(self, goal, places):
        """The safety rule (profile + the manifest's legacy list) that forbids ``goal``, as a ``Verdict``; None."""
        manifest = getattr(self.svc.robot, "manifest", None)
        pack = (getattr(manifest, "safety", None) or {}).get("hazards") or []
        return find_rule(goal, places, profile_of(self.svc.robot), pack, str(self.cfg["rules"]),
                         {"hazard.receivers"} if self.cfg["receivers"] else ())

    # ── services ────────────────────────────────────────────────────────────
    def forbids(self, goal, places):
        v = self.verdict(goal, places)
        return v.say if v else None

    def refused_goals(self, run):
        """The goals dropped for safety (already counted as covered by the read-back's issue check)."""
        return [g for g, _w in self._state(run).get("parts", [])]

    def gate_off(self):
        return True if str(self.cfg["rules"]) == "off" else None

    # ── hooks ───────────────────────────────────────────────────────────────
    def on_goals(self, run):
        """Goals the safety rules forbid: all of them -> a typed safety refusal (STOP); some -> those are dropped and said at the
        end; None."""
        found = [(g, self.verdict(g, run.places)) for g in run.goals]
        bad = [(g, v.say) for g, v in found if v]
        if not bad:
            return None
        st = self._state(run)
        st["refusals"] = list(st.get("refusals", []))
        st["refusals"] += [x for x in (refusal_record(g, v) for g, v in found if v) if x not in st["refusals"]]
        yield run.ev("understand_check", issues=[f"unsafe: {goal_kinds.describe(g)} ({w})" for g, w in bad], hazard=True)
        support = support_goals(run.goals, [g for g, _w in bad])
        if len(bad) + len(support) == len(run.goals):
            g, why = bad[0]
            yield self.svc.finish(run, "refused", refusal="safety", msg=f"unsafe: {goal_kinds.describe(g)}: {why}",
                                  say=f"I won't do that: {why}.")
            return STOP
        st["parts"] = list(st.get("parts", [])) + bad
        run.goals = [g for g in run.goals if all(g is not b for b, _w in bad) and all(g is not x for x in support)]
        return None

    def on_goals_met(self, run):
        """The goals hold but a part was refused: finish with the sentence "I did the rest, but I won't ...: why." """
        parts = self._state(run).get("parts", [])
        if not parts:
            return None
        g, why = parts[0]
        phrase = self.svc.goal_phrase(g, run.places)
        return self.svc.finish(run, "success", say=self.svc.in_lang(
            self.svc.humanize(f"I did the rest, but I won't {str(phrase)}: {why}."), run.lang),
            localized=True, refused_parts=[goal_kinds.describe(x) for x, _w in parts])

    def on_finish(self, run, fields) -> None:
        """The safety refusals on every final status (docs/safety.md), unless the caller set the field."""
        refusals = self._state(run).get("refusals")
        if refusals and "refusals" not in fields:
            fields["refusals"] = list(refusals)


register(SPEC, Hazard)
