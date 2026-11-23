"""Planner names at the two levels, and the old name of the SAGE planner.

What: a request (/ws/agent ``planner``, ``ROBOT_AGENT_PLANNER``) names a loop: ``sage`` = the AssistLoop,
``sage_plain`` = the plain ClosedLoop around the ``sage`` backend, ``direct`` = the ``llm_direct`` backend, or a
registry name. :func:`loop_choice` turns it into (backend, AssistLoop?); :func:`backend_name` is the registry key.
Why: the planner GRACE was renamed SAGE; robots, dashboards, bench configs and stored runs still send ``grace``.
Two bridges keep them working, each with one DeprecationWarning (and a log line) per process: the backend
``grace`` is ``sage`` (B1), the request ``grace`` is ``sage_plain`` (B4).
Switch off: nothing to switch; a bridge goes when no caller sends the old name (drop its ``_OLD_*`` entry).
"""

from __future__ import annotations

import logging
import warnings
from typing import Optional, Tuple

log = logging.getLogger(__name__)

ASSIST = "sage"                 # the request name of the AssistLoop (SAGE + understanding / clarification / guard)
PLAIN = "sage_plain"            # the request name of the plain ClosedLoop around the SAGE backend
BACKEND = "sage"                # the registry key of the SAGE backend
_REQUEST_BACKEND = {ASSIST: BACKEND, PLAIN: BACKEND, "direct": "llm_direct"}
_OLD_BACKEND = {"grace": BACKEND}       # bridge B1 (lower case)
_OLD_REQUEST = {"grace": PLAIN}         # bridge B4 (lower case)
_warned: set = set()


def _deprecated(old: str, new: str, what: str) -> None:
    if (what, old.lower()) in _warned:
        return
    _warned.add((what, old.lower()))
    msg = f"{what} {old!r} is deprecated, use {new!r} (the planner was renamed SAGE)"
    warnings.warn(msg, DeprecationWarning, stacklevel=3)
    log.warning(msg)


def _renamed(name, table: dict, what: str):
    key = name.strip() if isinstance(name, str) else name
    new = table.get(key.lower()) if isinstance(key, str) else None
    if new is None:
        return name
    _deprecated(key, new, what)
    return new


def backend_name(name):
    """The registry key for ``name`` (``grace`` -> ``sage``, any case); any other value unchanged."""
    return _renamed(name, _OLD_BACKEND, "planner backend")


def request_name(name):
    """The request-level name for ``name`` (``grace`` -> ``sage_plain``, any case); any other value unchanged."""
    return _renamed(name, _OLD_REQUEST, "planner")


def is_assist(planner, env_planner=None) -> bool:
    """Does the request run the AssistLoop? ``planner`` ``sage``, or none and ``ROBOT_AGENT_PLANNER=sage``."""
    return planner == ASSIST or (not planner and env_planner == ASSIST)


def loop_choice(planner, env_planner=None) -> Tuple[Optional[str], bool]:
    """``(backend, assist)`` for a request: the ClosedLoop backend (None: not chosen, or not a planner name)
    and whether the AssistLoop runs it (``sage``, or no backend chosen and ``ROBOT_AGENT_PLANNER=sage``)."""
    name = request_name(planner) if planner else None
    backend = _REQUEST_BACKEND.get(name) if name else None
    if backend is None and name:
        from .registry import is_planner_name
        backend = name if is_planner_name(name) else None
    return backend, planner == ASSIST or (backend is None and env_planner == ASSIST)


def loop_backend(name):
    """The backend a ClosedLoop config names (a request name or an old name is accepted too)."""
    return backend_name({PLAIN: BACKEND}.get(name, name))


def record_name(backend):
    """The planner a plain ClosedLoop run records: ``sage_plain`` for the SAGE backend, else the backend."""
    return PLAIN if backend_name(backend) == BACKEND else backend
