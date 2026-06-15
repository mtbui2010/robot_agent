# robot_agent/robot_agent/core/planning/registry.py
"""Planner registry -- construct a :class:`~.base.Planner` backend by name.

Backends are imported lazily inside :func:`get_planner` so that importing the
planning package never pulls in ``pyplanner`` (or any other heavy backend
dependency) unless a planner is actually requested. This keeps the executor
side import-clean per the SHARED CONTRACT.

Known backends:

* ``"grace"``      -> ``.backends.pyplanner_grace.GraceBackend``
                      (wraps pyplanner's GRACE planner).
* ``"llm_direct"`` -> ``.backends.llm_direct.LlmDirectBackend``
                      (single-shot LLM planner, no pyplanner).
"""

from __future__ import annotations

from .base import Planner

# Names advertised to users on an unknown-backend error. Kept in one place so
# the available list stays in sync with the dispatch below.
_AVAILABLE = ("grace", "llm_direct")


def get_planner(name: str, **cfg) -> Planner:
    """Return a planner backend instance for ``name``.

    Parameters
    ----------
    name:
        Backend identifier (case-insensitive): ``"grace"`` or ``"llm_direct"``.
    **cfg:
        Backend-specific keyword config (host, model, provider, ...), forwarded
        verbatim to the backend constructor.

    Raises
    ------
    ValueError
        If ``name`` is not a known backend; the message lists the available
        backends.
    """
    key = (name or "").strip().lower()

    if key == "grace":
        # Lazy import: only here do we touch pyplanner-backed code.
        from .backends.pyplanner_grace import GraceBackend

        return GraceBackend(**cfg)

    if key == "llm_direct":
        from .backends.llm_direct import LlmDirectBackend

        return LlmDirectBackend(**cfg)

    available = ", ".join(_AVAILABLE)
    raise ValueError(
        f"unknown planner {name!r}; available planners: {available}"
    )
