"""The action rules used by the planning layer: one source, ``embodiplan.action_model``.

The plan checker (``AssistLoop._check``), the per-step guard and the post-step symbolic
verify (``verify.symbolic_check``) and the world effects (``ActionMapper.apply_effect``,
``AssistLoop._effect``) all decide through this module, so they cannot drift apart again.
It is the only planning module that imports the rules from embodiplan; the others import
from here. See ``docs/action_model_divergence.md``.
"""

from __future__ import annotations

try:
    from embodiplan import action_model as _am
except ImportError as e:  # pragma: no cover - depends on install
    raise ImportError(
        "robot_agent needs embodiplan>=0.2,<0.3 with embodiplan.action_model "
        "(pip install -e ../embodiplan)"
    ) from e

ACTION_MODEL_VERSION = _am.ACTION_MODEL_VERSION
Capabilities = _am.Capabilities
Violation = _am.Violation
check = _am.check
apply = _am.apply
simulate = _am.simulate

# The body this core drives today: one hand, and Find only looks (a found place still
# needs a MoveTo). Per-robot values move to the manifest with the RobotAdapter (step 4).
ROBOT_CAPABILITIES = Capabilities(hands=1, find_reaches=False)

# Codes that need robot context the caller may not have (vocabulary, ENV, goals):
# reported only where that context is passed (the plan checker), not by symbolic_check.
CONTEXT_CODES = {"unknown_action", "not_openable", "find_wrong_place"}

__all__ = ["ACTION_MODEL_VERSION", "Capabilities", "Violation", "check", "apply", "simulate",
           "ROBOT_CAPABILITIES", "CONTEXT_CODES"]
