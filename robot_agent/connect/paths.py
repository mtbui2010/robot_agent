"""Runtime directories for the connect layer.

Absorbed from pyconnect, where every log path was hardcoded relative to the
*installed package* (``pyconnect/logs/...``). That breaks a pip-installed or
read-only deployment, so paths are resolved here instead:

  1. ``ROBOT_AGENT_LOG_DIR`` when set,
  2. the active :class:`~robot_agent.state.AgentState` log dir
     (``<common_dir>/logs``) once ``bootstrap()`` has run,
  3. ``~/.robot_agent/logs`` as a last resort.

Resolution is lazy — importing this module never touches the filesystem, and
the answer is re-resolved until an AgentState exists so a module imported
before ``bootstrap()`` still ends up logging to the right place.
"""

from __future__ import annotations

import os
from pathlib import Path

_CACHED: Path | None = None


def log_root() -> Path:
    """Base directory for all connect-layer logs."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    env = os.environ.get('ROBOT_AGENT_LOG_DIR')
    if env:
        _CACHED = Path(env)
        return _CACHED

    try:
        from robot_agent.state import current
        # Only cache once a real AgentState exists; before bootstrap we keep
        # falling back so the first caller does not pin the fallback path.
        _CACHED = Path(current().log_dir)
        return _CACHED
    except Exception:
        return Path.home() / '.robot_agent' / 'logs'


def log_dir(*parts: str) -> Path:
    """Return ``log_root()/<parts>``, creating it on first use."""
    p = log_root().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
