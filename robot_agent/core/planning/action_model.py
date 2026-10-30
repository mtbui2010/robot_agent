"""The action rules used by the planning layer: one source, ``embodiplan.action_model``.

The plan checker (``AssistLoop._check``), the per-step guard and the post-step symbolic
verify (``verify.symbolic_check``) and the world effects (``ActionMapper.apply_effect``,
``AssistLoop._effect``) all decide through this module, so they cannot drift apart again.
It is the only planning module that imports the rules from embodiplan; the others import
from here. See ``docs/action_model_divergence.md``.
"""

from __future__ import annotations

import functools

# embodiplan is imported on first use, not at import time: importing the package loads
# all its planners (and `requests`), and `import robot_agent.core.planning` stays light.


@functools.lru_cache(maxsize=None)
def _model():
    try:
        from embodiplan import action_model as am
    except ImportError as e:  # pragma: no cover - depends on install
        raise ImportError(
            "robot_agent needs embodiplan>=0.2.1,<0.3 with embodiplan.action_model "
            "(pip install -e ../embodiplan)"
        ) from e
    return am


def check(step, world, **kw):
    """embodiplan.action_model.check."""
    return _model().check(step, world, **kw)


def apply(step, world, **kw):
    """embodiplan.action_model.apply."""
    return _model().apply(step, world, **kw)


def simulate(plan, world=None, **kw):
    """embodiplan.action_model.simulate."""
    return _model().simulate(plan, world, **kw)


def __getattr__(name):
    # ACTION_MODEL_VERSION, Capabilities, Violation, ROBOT_ACTIONS (the model's vocabulary), and
    # ROBOT_CAPABILITIES: the core's default body, one hand and Find only looks (a found place
    # still needs a MoveTo). The loops use the robot's own (``RobotView.model_capabilities``).
    if name == "ROBOT_CAPABILITIES":
        return _model().Capabilities(hands=1, find_reaches=False)
    if name in ("ACTION_MODEL_VERSION", "Capabilities", "Violation", "ROBOT_ACTIONS"):
        return getattr(_model(), name)
    raise AttributeError(name)


# Codes that need robot context the caller may not have (vocabulary, ENV, goals):
# reported only where that context is passed (the plan checker), not by symbolic_check.
CONTEXT_CODES = {"unknown_action", "not_openable", "find_wrong_place"}

__all__ = ["ACTION_MODEL_VERSION", "Capabilities", "Violation", "check", "apply", "simulate",
           "ROBOT_CAPABILITIES", "ROBOT_ACTIONS", "CONTEXT_CODES"]
