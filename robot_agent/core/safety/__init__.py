"""The core's deterministic safety layer (SAGE-v2 F4.3): the engine only. What is hazardous is the robot's own safety profile
(``profile.py``; the manifest's ``safety.profile``); the core ships no domain rule (docs/safety.md)."""

from .hazards import Verdict, check_goal, find_rule, hazard_class  # noqa: F401
from .place_class import place_class  # noqa: F401
from .profile import EMPTY, Rule, SafetyProfile, load as load_profile  # noqa: F401
from .receiver_class import receiver_class  # noqa: F401


def profile_of(robot) -> SafetyProfile:
    """The safety profile of a robot (its manifest's ``safety.profile``), ``EMPTY`` when it declares none."""
    return getattr(getattr(robot, "manifest", None), "safety_profile", None) or EMPTY
