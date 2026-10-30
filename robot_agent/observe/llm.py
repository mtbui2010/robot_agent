"""The one LLM call path of a run: record each call, or serve it during a replay.

:func:`call` wraps a real call. Normally it times it and writes an ``llm`` row on
the current run's record (no run, no row). While a replay store is installed
(:func:`serving`), the call never reaches a model: the store answers it from the
recorded calls and raises :class:`ReplayMismatch` when no recorded call has the
same request ("the prompt differs").

Callers: ``LLMobj.chat`` / ``chat_guide`` (``connect/llm/llm_object.py``),
``embodiplan.LLMBackend.chat`` (hooked in ``core/planning/backends/pyplanner_grace.py``)
and :class:`RecordingLLM`, which wraps a client that is neither (a test double).
A call made inside another one (``chat_guide`` -> ``chat``) is not recorded twice.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .record import _MAX_LLM_STR, current_record, shrink

_TLS = threading.local()
_STORE = None                     # the active replay store, see serving()


class ReplayMismatch(AssertionError):
    """A replayed run asked for something the record does not have."""


def request_of(method: str, kwargs: dict) -> dict:
    """The part of a client call that decides its answer, JSON-safe; the key a
    replay matches on."""
    req = {'method': method}
    for k, v in sorted(kwargs.items()):
        if k in ('rgb', 'rgb_path', 'crop_roi') and v is not None:
            v = f'<{k}>'
        req[k] = v
    return shrink(req, _MAX_LLM_STR)


def _val(v):
    try:
        return v() if callable(v) else v
    except Exception:
        return None


def call(source: str, real: Callable[[], Any], request: dict, *, model: Any = None,
         backend: Any = None, temperature: Any = None, seed: Any = None,
         messages: Any = None, as_tuple: bool = False) -> Any:
    """Run (or serve) one LLM call. ``model`` / ``backend`` / ``messages`` may
    be callables, read after the call (a client picks its model per call)."""
    if getattr(_TLS, 'depth', 0):
        return real()
    store = _STORE
    if store is not None:
        out = store.serve(source, request)
        return tuple(out) if as_tuple and isinstance(out, list) else out
    rec = current_record()
    if rec is None:
        return real()
    _TLS.depth = 1
    t0 = time.time()
    err = None
    out = None
    try:
        out = real()
        return out
    except BaseException as e:
        err = f'{type(e).__name__}: {e}'
        raise
    finally:
        _TLS.depth = 0
        try:
            rec.llm(source=source, request=request, response=out, error=err,
                    latency_ms=round((time.time() - t0) * 1000, 1), model=_val(model),
                    backend=_val(backend), temperature=_val(temperature), seed=_val(seed),
                    messages=_val(messages))
        except Exception:
            pass


def records_itself(client) -> bool:
    """True for clients whose calls already go through :func:`call` (LLMobj)."""
    try:
        from ..connect.llm.llm_object import LLMobj
        return isinstance(client, LLMobj)
    except Exception:
        return False


def client_call(client, method: str, kwargs: dict, real: Callable[[], Any], messages=None) -> Any:
    """:func:`call` for a robot_agent LLM client method (``chat`` / ``chat_guide``)."""
    return call('robot_agent', real, request_of(method, kwargs),
                model=lambda: getattr(client, 'current_model', None) or getattr(client, 'model', None),
                backend=type(client).__name__,
                temperature=kwargs.get('temperature', getattr(client, 'temperature', None)),
                seed=kwargs.get('seed', getattr(client, 'seed', None)), messages=messages)


class RecordingLLM:
    """Records ``chat`` / ``chat_guide`` of a client that does not record itself."""

    def __init__(self, inner):
        self._inner = inner

    def chat(self, **kwargs):
        return client_call(self._inner, 'chat', kwargs, lambda: self._inner.chat(**kwargs))

    def chat_guide(self, prompt, guide, reuse=False, **kwargs):
        kw = dict(kwargs, prompt=prompt, guide=guide)
        return client_call(self._inner, 'chat_guide', kw,
                           lambda: self._inner.chat_guide(prompt, guide, reuse=reuse, **kwargs))

    def __getattr__(self, name):
        return getattr(self._inner, name)


class serving:
    """``with serving(store):`` — every LLM call is answered by ``store.serve(source, request)``."""

    def __init__(self, store):
        self.store, self.prev = store, None

    def __enter__(self):
        global _STORE
        self.prev, _STORE = _STORE, self.store
        return self.store

    def __exit__(self, *exc):
        global _STORE
        _STORE = self.prev
        return False


def active_store() -> Optional[object]:
    return _STORE
