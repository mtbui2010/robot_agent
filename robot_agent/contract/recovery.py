"""Default recovery policy, keyed by :class:`FailureCode`.

The executive asks ``policy.rule_for(code)`` what to do after a failed step
instead of reading the failure text. The table below is the core default for
every robot; an adapter overrides single entries through a
``recovery_overrides`` attribute (``{code: RecoveryRule | dict}``), e.g. a
robot whose camera recovers by itself may wait longer on
PERCEPTION_UNAVAILABLE.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .types import FailureCode


class Recovery(str, Enum):
    STOP = "stop"                        # end the run, say why
    WAIT_HEALTH = "wait_health"          # wait, check adapter.health(), try again
    HEALTH_CHECK = "health_check"        # check adapter.health(), then go on
    REAPPROACH = "reapproach"            # move / turn to get closer, try again
    CONTINUE_SEARCH = "continue_search"  # look at the next place the belief suggests
    RETRY = "retry"                      # the same skill again, in place
    REFIND = "refind"                    # look for the object again (it moved / fell)
    VERIFY = "verify"                    # observe what actually happened first
    ASK_HELP = "ask_help"                # ask the person (clear the way, move it closer)
    REPLAN = "replan"                    # planner suffix repair from here


@dataclass(frozen=True)
class RecoveryRule:
    action: Recovery
    retries: int = 1                      # failures answered with ``action`` before ``then``
    then: Optional[Recovery] = None       # after the retries are used (None = STOP)
    retry_allowed: bool = True            # False: never repeat the failed skill (e-stop)
    mark_searched: bool = False           # a Find failure counts the place as searched
    report: bool = False                  # tell the user / operator the reason
    note: str = ""

    @classmethod
    def coerce(cls, v) -> "RecoveryRule":
        if isinstance(v, cls):
            return v
        if isinstance(v, (str, Recovery)):
            return cls(Recovery(v))
        if isinstance(v, Mapping):
            d = dict(v)
            d["action"] = Recovery(d["action"])
            if d.get("then") is not None:
                d["then"] = Recovery(d["then"])
            return cls(**d)
        raise TypeError(f"cannot make a RecoveryRule from {v!r}")

    def next_after(self, attempts: int) -> Recovery:
        """What to do after ``attempts`` failures of this kind on one step
        (1 = the first failure)."""
        if attempts <= max(self.retries, 1):
            return self.action
        return self.then or Recovery.STOP


R = Recovery
DEFAULT_RECOVERY: dict = {
    FailureCode.ESTOP: RecoveryRule(R.STOP, retry_allowed=False, report=True,
                                    note="emergency stop: never retried"),
    FailureCode.CANCELLED: RecoveryRule(R.STOP, retry_allowed=False, note="the user stopped it"),
    FailureCode.SOFTWARE: RecoveryRule(R.STOP, retry_allowed=False, report=True,
                                       note="a bug in the skill: retrying / replanning cannot help"),
    FailureCode.HW_FAULT: RecoveryRule(R.HEALTH_CHECK, retries=1, then=R.STOP, report=True,
                                       note="device fault: check health once, then stop and report"),
    FailureCode.PERCEPTION_UNAVAILABLE: RecoveryRule(R.WAIT_HEALTH, retries=2, then=R.STOP, mark_searched=False,
                                                     report=True,
                                                     note="could not look: not evidence of absence"),
    FailureCode.TIMEOUT: RecoveryRule(R.HEALTH_CHECK, retries=1, then=R.REPLAN, note="check health, then replan"),
    FailureCode.UNREACHABLE: RecoveryRule(R.REAPPROACH, retries=1, then=R.REPLAN,
                                          note="seen but out of reach: get closer"),
    FailureCode.BLOCKED: RecoveryRule(R.RETRY, retries=1, then=R.ASK_HELP, note="path / spot occupied"),
    # 3 = assist_loop's search_max: then the person is asked where it is
    FailureCode.ABSENT: RecoveryRule(R.CONTINUE_SEARCH, retries=3, then=R.ASK_HELP, mark_searched=True,
                                     note="not here: search on from the belief"),
    FailureCode.GRASP_FAILED: RecoveryRule(R.RETRY, retries=1, then=R.REPLAN, note="look + grasp again once"),
    FailureCode.DROPPED: RecoveryRule(R.REFIND, retries=1, then=R.REPLAN, report=True,
                                      note="the held object is gone: find it again"),
    FailureCode.BUDGET_EXCEEDED: RecoveryRule(R.STOP, retry_allowed=False, report=True,
                                              note="the run's LLM-call budget is used: stop and say so"),
    FailureCode.UNKNOWN: RecoveryRule(R.VERIFY, retries=1, then=R.REPLAN,
                                      note="no usable reason: observe, then replan"),
}
del R


class RecoveryPolicy:
    """The default table with per-robot overrides on top."""

    def __init__(self, overrides: Optional[Mapping] = None, base: Optional[Mapping] = None):
        self.table: dict = dict(base or DEFAULT_RECOVERY)
        for code, rule in (overrides or {}).items():
            self.table[FailureCode.parse(code)] = RecoveryRule.coerce(rule)

    def rule_for(self, code) -> RecoveryRule:
        c = FailureCode.try_parse(code) if code is not None else None
        return self.table.get(c or FailureCode.UNKNOWN, self.table[FailureCode.UNKNOWN])

    def decide(self, code, attempts: int = 1) -> Recovery:
        return self.rule_for(code).next_after(attempts)

    def with_overrides(self, overrides: Mapping) -> "RecoveryPolicy":
        return RecoveryPolicy(overrides, base=self.table)


def policy_for(adapter: Any = None) -> RecoveryPolicy:
    """The default policy plus the adapter's ``recovery_overrides`` (if any)."""
    ov = getattr(adapter, "recovery_overrides", None) if adapter is not None else None
    return RecoveryPolicy(ov or None)


__all__ = ["Recovery", "RecoveryRule", "RecoveryPolicy", "DEFAULT_RECOVERY", "policy_for"]
