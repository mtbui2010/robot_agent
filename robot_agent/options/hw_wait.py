"""Option ``hw_wait`` (default off): the wait after a hardware fault is the pack's declared schedule and number of tries for THAT kind of failure.

The failure it closes: one fixed wait (``planner.recovery.wait``, one schedule for every failure) and the policy table's own retry counts
serve a camera that needs a second, a motor that needs a restart and a server that is down alike; a run stops at a fault another wait would
have cleared, or spends wait commands on a fault no wait clears.

Pack data ``planner.options.hw_wait.kinds`` (a failure code of the contract is the key; each entry needs at least one of its fields)::

    {"<failure code>": {"schedule_s": [seconds, ...], "tries": int >= 1, "action": "wait_health" | "health_check" | "stop"}, "source": "<label>"}
    # or "assumed": true instead of source; the two label fields are not failure kinds

* ``schedule_s``: the wait entries for that kind, in seconds, in place of the ``planner.recovery.wait`` schedule (the wait ACTION is still the
  pack's ``recovery.wait.action``). An EMPTY list sends no wait command at all: the pack says a wait does not help that kind.
* ``tries``: the failures of that kind on one step answered with the policy's action before it stops (the policy table's ``retries``).
* ``action``: what the first answers are, instead of the policy table's.

A kind with no entry, a bad entry, no ``recovery.wait`` declaration (the schedule only) or no data at all: the engine's own behaviour, byte for
byte. The schedule is read where ``wait_on_robot`` runs the wait; without that option the engine's own poll stays.
Hook ``on_start`` (no effect). Services ``wait_schedule``, ``recovery_rule``. Keys: ``enabled`` (legacy ``hw_wait``), ``kinds`` (dict, from the manifest).
Lab flag ``hw_wait``: ``{kind, schedule_s, tries, action}``.
"""

from __future__ import annotations

import dataclasses

from robot_agent.contract.recovery import Recovery
from robot_agent.contract.types import FailureCode
from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.options.config import resolve_key
from robot_agent.core.planning.pack_data import labelled

SPEC = OptionSpec(
    name="hw_wait",
    keys=(Key("enabled", "bool", False, aliases=("hw_wait",)), Key("kinds", "dict", {}, sources=("manifest",))),
    hooks=("on_start",),
    order=90,
    enabled="enabled",
    services=("wait_schedule", "recovery_rule"),
    doc="Hardware-fault wait: the wait schedule, the number of tries and the first answer per failure kind are pack data; an empty schedule sends no wait command.",
)

_LABELS = ("source", "assumed", "note")
_ACTIONS = {"wait_health": Recovery.WAIT_HEALTH, "health_check": Recovery.HEALTH_CHECK, "stop": Recovery.STOP}


def schedule_of(entry):
    """The seconds of an entry's ``schedule_s`` (a list, possibly empty), or None when the entry declares none or a bad one."""
    raw = entry.get("schedule_s") if isinstance(entry, dict) else None
    if not isinstance(raw, (list, tuple)):
        return None
    try:
        secs = [float(s) for s in raw]
    except (TypeError, ValueError):
        return None
    return secs if all(s > 0 for s in secs) else None


def tries_of(entry):
    """The declared ``tries`` (an int >= 1), else None."""
    n = entry.get("tries") if isinstance(entry, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else None


def kinds_of(declared) -> dict:
    """{failure code value: entry} of the dict-shaped entries whose key is a failure code of the contract; the label fields are dropped. An entry counts
    only with a label (``source`` or ``assumed: true``), its own or the whole block's; unlabelled data is not used."""
    if not isinstance(declared, dict):
        return {}
    out, whole = {}, labelled(declared)
    for k, v in declared.items():
        code = FailureCode.try_parse(k) if k not in _LABELS else None
        if code is not None and isinstance(v, dict) and (whole or labelled(v)):
            out[code.value] = v
    return out


class HwWait(Option):
    spec = SPEC

    def on_start(self, run) -> None:
        """No state to set up: the option answers two services; the registry asks every option to declare a hook."""

    def _kinds(self) -> dict:
        planner = getattr(getattr(self.svc.robot, "manifest", None), "planner", None)
        return kinds_of(self.cfg.get("kinds") or resolve_key(SPEC, SPEC.key("kinds"), {}, {}, planner))

    def _entry(self, code) -> dict:
        c = FailureCode.try_parse(code)
        return self._kinds().get(c.value, {}) if c else {}

    def wait_schedule(self, run, schedule):
        """The wait entries for the failure the run just noted, or None (the engine's schedule stands)."""
        failures = getattr(run, "failures", None) or []
        entry = self._entry(failures[-1].get("code")) if failures else {}
        secs = schedule_of(entry)
        if secs is None:
            return None
        self.svc.lab(SPEC.name, kind=failures[-1].get("code"), schedule_s=secs)
        return secs

    def recovery_rule(self, run, code, rule):
        """``rule`` with the declared ``tries`` and ``action`` of that failure kind, or None (the policy's rule stands)."""
        entry = self._entry(code)
        tries, act = tries_of(entry), _ACTIONS.get(entry.get("action"))
        if tries is None and act is None:
            return None
        new = dataclasses.replace(rule, retries=tries or rule.retries, action=act or rule.action)
        self.svc.lab(SPEC.name, kind=FailureCode.parse(code).value, tries=new.retries, action=new.action.value)
        return new


register(SPEC, HwWait)
