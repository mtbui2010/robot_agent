"""Observability: one RunRecord per run, its metrics line, replay, the data dir.

* :mod:`.paths`   — ``data_dir(kind)``: where run logs, memories and the world
  belief live (``ROBOT_AGENT_DATA_DIR``, default unchanged).
* :mod:`.record`  — :class:`RunRecord` (JSONL, keyed by the RunManager's
  ``run_id``) and the hooks that fill it; ``metrics_of`` / ``metrics.jsonl``.
* :mod:`.llm`     — the single LLM call path (record, or serve during replay).
* :mod:`.replay`  — ``python -m robot_agent.observe.replay plan|exec <run>``.

Docs: ``docs/observe.md``. Submodules are imported lazily, so importing this
package pulls in nothing heavy.
"""

from __future__ import annotations

import importlib

__all__ = ['paths', 'record', 'llm', 'replay', 'data_dir', 'RunRecord', 'metrics_of', 'instrument']

_ATTRS = {
    'data_dir': ('paths', 'data_dir'),
    'RunRecord': ('record', 'RunRecord'),
    'metrics_of': ('record', 'metrics_of'),
    'instrument': ('record', 'instrument'),
}


def __getattr__(name):
    if name in ('paths', 'record', 'llm', 'replay'):
        return importlib.import_module(f'{__name__}.{name}')
    if name in _ATTRS:
        mod, attr = _ATTRS[name]
        return getattr(importlib.import_module(f'{__name__}.{mod}'), attr)
    raise AttributeError(name)
