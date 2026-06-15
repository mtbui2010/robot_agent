"""Task-tracking records + JSONL run logger.

Single source of truth for tracking (live status), verification (held results),
and logging (serialized to JSONL). Plain dataclasses, JSON-serializable.

NO pyplanner import. Timestamps are passed in by the caller — this module never
calls ``time.time()`` at import time or implicitly (libs forbid ``Date.now``-style
nondeterminism). See docs/TRACKING_VERIFY_VOICE.md §1–§2.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Optional, Union

# numpy is optional — guard the import so this module is stdlib-only at runtime.
try:  # pragma: no cover - environment dependent
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
@dataclass
class VerifyResult:
    layer: str                              # 'isdone' | 'symbolic' | 'vlm'
    ok: bool
    detail: str = ''                        # reason / message
    confidence: Optional[float] = None      # vlm only
    latency_ms: float = 0.0


@dataclass
class StepRecord:
    index: int
    action: str                             # GRACE action (MoveTo, Pick, …)
    object: str                             # CamelCase target
    skill: Optional[str]                    # mapped kcare skill (None for no-op)
    params: dict = field(default_factory=dict)
    status: str = 'pending'                 # pending|running|success|failed|skipped
    attempt: int = 1                        # increments across replans
    started_at: float = 0.0
    ended_at: Optional[float] = None
    result: dict = field(default_factory=dict)            # sanitized skill return
    verifies: list[VerifyResult] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)         # per-step log lines

    @property
    def verified(self) -> bool:
        """True iff at least one layer ran and every run layer passed."""
        return bool(self.verifies) and all(v.ok for v in self.verifies)


@dataclass
class TaskRecord:
    run_id: str                             # time-based id (passed in; no Date.now)
    task: str                               # NL instruction
    lang: str = 'en'
    obs: str = ''
    visible: list[str] = field(default_factory=list)
    planner: str = 'grace'                  # which backend produced the plan
    plan_meta: dict = field(default_factory=dict)         # PlanMetrics
    warnings: list[str] = field(default_factory=list)     # mapper.screen()
    steps: list[StepRecord] = field(default_factory=list)
    replans: list[dict] = field(default_factory=list)     # {at_index, failed, reason, suffix_len}
    status: str = 'planning'                # planning|running|success|failed|aborted
    started_at: float = 0.0
    ended_at: Optional[float] = None


# --------------------------------------------------------------------------- #
# JSON helpers — best-effort, stdlib only, numpy/NaN-safe
# --------------------------------------------------------------------------- #
def _to_jsonable(obj: Any) -> Any:
    """Recursively coerce ``obj`` into something ``json.dumps`` can handle.

    - dataclasses    -> dict
    - set / frozenset -> sorted list (deterministic output)
    - tuple          -> list
    - numpy scalars  -> python scalars (guarded import)
    - numpy arrays   -> list
    - NaN / +-inf    -> None  (json's default would emit invalid `NaN`/`Infinity`)
    Anything still unknown falls back to ``str(obj)``.
    """
    # Primitives that json already understands.
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None

    # numpy scalars / arrays (best-effort, only if numpy present).
    if _np is not None:  # pragma: no branch
        if isinstance(obj, _np.generic):
            return _to_jsonable(obj.item())
        if isinstance(obj, _np.ndarray):
            return [_to_jsonable(x) for x in obj.tolist()]

    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}

    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (set, frozenset)):
        try:
            return [_to_jsonable(x) for x in sorted(obj)]
        except TypeError:
            # unorderable members — fall back to insertion-stable str sort
            return [_to_jsonable(x) for x in sorted(obj, key=repr)]

    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]

    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode('utf-8', 'replace')
        except Exception:
            return repr(obj)

    if isinstance(obj, Path):
        return str(obj)

    # Last resort: stringify so logging never hard-fails.
    return str(obj)


def _dumps(payload: dict) -> str:
    return json.dumps(_to_jsonable(payload), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Run logger — one JSONL file per run
# --------------------------------------------------------------------------- #
class RunLogger:
    """Append-only JSONL logger for a single run.

    ``path`` is typically ``<kcare data>/task_runs/<run_id>.jsonl`` — never inside
    ``pyplanner/``. Each streamed event is one line; ``snapshot()`` writes the full
    ``TaskRecord`` as the final line so the file replays exactly what the UI saw.
    """

    def __init__(self, run: TaskRecord, path: Union[str, os.PathLike]) -> None:
        self.run = run
        self.path = Path(path)
        # Create parent dirs eagerly so the first event() never fails on a missing dir.
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, kind: str, t: Optional[float] = None, **fields: Any) -> None:
        """Append one JSON line ``{"t": t?, "kind": kind, **fields}``.

        ``t`` (a caller-supplied timestamp) is included only when provided — this
        module never calls ``time.time()`` itself.
        """
        payload: dict[str, Any] = {}
        if t is not None:
            payload['t'] = t
        payload['kind'] = kind
        payload.update(fields)
        self._append(payload)

    def snapshot(self) -> None:
        """Write the full ``TaskRecord`` (via ``dataclasses.asdict``) as a line."""
        self._append({'kind': 'snapshot', 'record': dataclasses.asdict(self.run)})

    def _append(self, payload: dict) -> None:
        line = _dumps(payload)
        with self.path.open('a', encoding='utf-8') as fh:
            fh.write(line)
            fh.write('\n')


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def new_task_record(
    run_id: str,
    task: str,
    lang: str = 'en',
    obs: str = '',
    visible: Optional[list[str]] = None,
    planner: str = 'grace',
    started_at: float = 0.0,
) -> TaskRecord:
    """Construct a fresh ``TaskRecord`` in the ``planning`` state."""
    return TaskRecord(
        run_id=run_id,
        task=task,
        lang=lang,
        obs=obs,
        visible=list(visible) if visible else [],
        planner=planner,
        started_at=started_at,
        status='planning',
    )
