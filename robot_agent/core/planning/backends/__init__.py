# robot_agent/robot_agent/core/planning/backends/__init__.py
"""Planner backend adapters.

Each backend conforms to the ``Planner`` interface declared in
``robot_agent.core.planning.base`` while wrapping a concrete planning
implementation:

    SageBackend       — embodiplan's SAGE planner (optional dependency)
    LlmDirectBackend  — the legacy direct-LLM planning flow

Importing this package never imports ``embodiplan``; the optional dependency
is loaded lazily inside ``pyplanner_sage`` only.
"""

__all__ = ["SageBackend", "LlmDirectBackend"]


def __getattr__(name: str):  # PEP 562 lazy re-export, keeps imports cheap/optional
    if name == "SageBackend":
        from .pyplanner_sage import SageBackend
        return SageBackend
    if name == "LlmDirectBackend":
        from .llm_direct import LlmDirectBackend
        return LlmDirectBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
