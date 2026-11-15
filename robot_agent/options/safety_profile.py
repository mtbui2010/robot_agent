"""Option ``safety_profile``: hazard rules declared as DATA of the robot in the option's own section, checked by the one safety gate.

What: the profile ``planner.options.safety_profile.profile`` (the schema of docs/safety.md: classes, rules, cited ``sources``; a place rule
may carry ``within_m``, so it also forbids a place that lies nearer than that to a place of its classes on the robot's map) answers the
service ``safety_rule(goal, places)``. Option ``hazard`` asks that service after its own rules, so the refusal, the partial refusal and the
``refusals`` record stay hazard's: no second gate. Hook ``on_start`` reads the profile again for each run (lab flag: the profile in force).
Why (D4 layer L21, classes hazard_unrefused / hazard_missed): a request to put a thing that can burn on or next to a heat source was done,
because no rule of the robot related the two, and such a rule needs a distance on the map, cited from a safety document.
How to switch it off: ``safety_profile.enabled`` false (the default; run option ``safety_profile=0``). No profile, an empty one, or one
with errors (logged once in the lab flag) forbids nothing (fail open); ``hazard.rules=off`` turns these rules off with every other one.
"""

from __future__ import annotations

import json

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import pack_data
from robot_agent.core.safety import EMPTY, find_rule, load_profile

SPEC = OptionSpec(
    name="safety_profile",
    keys=(Key("enabled", "bool", False, aliases=("safety_profile",)),
          Key("profile", "dict", {}, sources=("manifest",))),
    hooks=("on_start",),
    services=("safety_rule",),
    order=11,
    enabled="enabled",
    doc="Hazard rules declared as the robot's data in planner.options.safety_profile.profile (cited sources, a map distance within_m); "
        "the hazard gate asks them through the service safety_rule.",
)


class SafetyProfile(Option):
    _seen = ("", EMPTY)

    def profile(self):
        """The declared profile, read again from the manifest at each use (data that arrives after the loop was built counts); EMPTY
        when none or when it has errors."""
        raw = pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "profile") or {}
        sig = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
        if sig != self._seen[0]:
            prof, errs = load_profile(raw, where="planner.options.safety_profile.profile") if raw else (EMPTY, [])
            if errs:
                self.svc.lab(SPEC.name, part="errors", errors=errs[:3])
            self._seen = (sig, prof)
        return self._seen[1]

    def on_start(self, run) -> None:
        """The profile in force for this run, read from the manifest now (lab flag ``{part: loaded, profile, rules, sha256}``)."""
        prof = self.profile()
        if prof.rules:
            self.svc.lab(SPEC.name, part="loaded", profile=prof.name, rules=len(prof.rules), sha256=prof.sha256[:12])

    def safety_rule(self, goal, places):
        """The ``Verdict`` of the first declared rule that forbids ``goal`` (a lab event names it), None."""
        prof = self.profile()
        v = find_rule(goal, places, prof) if prof.rules else None
        if v:
            self.svc.lab(SPEC.name, part="rule", rule=v.rule, to=str(goal.get("to") or ""))
        return v


register(SPEC, SafetyProfile)
