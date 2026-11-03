"""The core's deterministic safety layer (SAGE-v2 F4.3): place classes and household-hazard rules."""

from .hazards import HAZARD_RULES, check_goal, hazard_class  # noqa: F401
from .place_class import place_class  # noqa: F401
