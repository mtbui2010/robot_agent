"""Legacy failure text -> FailureCode: the ONE place the string heuristics live.

Skills that still return ``{'isdone': False, 'msg': '...'}`` carry their reason
only as English text. Until each skill returns a :class:`SkillResult` with a
code, the core reads the code off that text here, and nowhere else
(``assist_loop._SOFTWARE_ERR`` is the same pattern as :data:`SOFTWARE_ERR`
below and should import it from here once that file is migrated).

The patterns are drawn from what the planner skills (move / find / pick /
place / open_drawer / grasp_succeed / switchbot) and the core's own verify
details actually say. Order matters: the first rule that matches wins, so the
more specific / more severe causes come first (a skill bug that mentions
"not found" is still a bug).
"""

from __future__ import annotations

import re

from .types import FailureCode

# a bug in a skill, not the world: retrying / replanning cannot help
# (identical to robot_agent.core.planning.assist_loop._SOFTWARE_ERR)
SOFTWARE_ERR = re.compile(r"unexpected keyword argument|missing \d+ required|is not defined|has no attribute|"
                          r"TypeError|NameError|AttributeError|ImportError|SyntaxError")

_I = re.IGNORECASE
_RULES: list[tuple[FailureCode, re.Pattern]] = [
    (FailureCode.SOFTWARE, SOFTWARE_ERR),
    # SkillRegistry.execute: the skill name resolves to nothing -> a config bug
    (FailureCode.SOFTWARE, re.compile(r'skill "[^"]*" not registered|no skill or device agent|Traceback \(most recent', _I)),
    (FailureCode.CANCELLED, re.compile(r"\bcancel+ed\b|aborted by (the )?user|stopped by (the )?user", _I)),
    (FailureCode.ESTOP, re.compile(r"\be-?stop\b|emergency[ _-]?(stop|button)", _I)),
    # place found the hand empty / a held object vanished
    (FailureCode.DROPPED, re.compile(r"no longer in the hand|\bdropped\b|slipped (out|from)|lost the object", _I)),
    # pick closed on nothing; place could not let go ("still in the gripper")
    (FailureCode.GRASP_FAILED, re.compile(r"gripper is empty|could not grasp|failed to grasp|grasp(ing)? fail|"
                                          r"still in the gripper|nothing in the gripper|not in the gripper", _I)),
    # could not look: no frame / depth / camera / detector
    (FailureCode.PERCEPTION_UNAVAILABLE, re.compile(
        r"no fresh frame|no depth|no (head|wrist|arm)? ?(/ ?\w+ )?image|camera (agent )?'?[\w-]*'? ?not registered|"
        r"detector'? not registered|client '?(detector|vlms?)'? not registered|vision model failed|"
        r"sam returned none|perception (is )?(down|unavailable)|camera (is )?(down|unavailable|offline)", _I)),
    (FailureCode.TIMEOUT, re.compile(r"timed? ?out|time-out|\btimeout\b|no reply|did not answer in time", _I)),
    # seen, but out of reach: core verify details + pick / place reach_problem()
    (FailureCode.UNREACHABLE, re.compile(r"out of reach|beyond the arm reach|\bunreachable\b|too far for my arm|"
                                         r"more than max_shift|m deep from the robot|outside the reach|"
                                         r"stopped [\d.]+ m from|can'?t get to|could not get to|cannot reach", _I)),
    (FailureCode.BLOCKED, re.compile(r"\bblocked\b|obstacle|in the way|no free spot|collision|occupied", _I)),
    (FailureCode.HW_FAULT, re.compile(r"connection refused|not reachable|\bservo\b|\bmotor\b|hardware fault|"
                                      r"adapterbusy|device (is )?(busy|offline|fault)|no vla-serve server", _I)),
    # looked, not there
    (FailureCode.ABSENT, re.compile(r"not found|could ?n[o']t find|can'?t find|find .* failed|no detection|"
                                    r"nothing (was )?detected|not detected|not seen|not visible", _I)),
]


def classify_legacy_failure(msg) -> FailureCode:
    """FailureCode for a legacy failure reason (a skill's ``msg`` or a verify
    detail). UNKNOWN when nothing matches or there is no text."""
    s = "" if msg is None else str(msg)
    if not s.strip():
        return FailureCode.UNKNOWN
    for code, pat in _RULES:
        if pat.search(s):
            return code
    return FailureCode.UNKNOWN


def is_software_error(msg) -> bool:
    """The assist_loop test: a skill bug, stop at once (not retried)."""
    return bool(SOFTWARE_ERR.search(str(msg or "")))
