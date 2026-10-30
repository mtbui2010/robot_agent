# robot_agent/robot_agent/core/planning/registry.py
"""Planner registry -- construct a :class:`~.base.Planner` backend by name.

Backends are imported lazily inside :func:`get_planner` so that importing the
planning package never pulls in ``embodiplan`` (or any other heavy backend
dependency) unless a planner is actually requested. This keeps the executor
side import-clean per the SHARED CONTRACT.

A planner name is one of (``docs/planners.md``):

* a built-in name:
  ``"grace"``      -> ``.backends.pyplanner_grace.GraceBackend``
                      (wraps embodiplan's GRACE / SAGE planner);
  ``"llm_direct"`` -> ``.backends.llm_direct.LlmDirectBackend``
                      (single-shot LLM planner, no embodiplan);
* ``"package.module:Class"`` -- any importable class (or factory) that returns
  a :class:`~.base.Planner`; nothing has to be registered;
* the name of a Python entry point in the group ``robot_agent.planners``
  (declared by a planner package in its own ``pyproject.toml``).
"""

from __future__ import annotations

import importlib
import inspect

from .base import Planner

# Built-in names -> "module:attr" (module relative to this package).
_BUILTIN = {
    "grace": ".backends.pyplanner_grace:GraceBackend",
    "llm_direct": ".backends.llm_direct:LlmDirectBackend",
}
# Names advertised to users on an unknown-backend error.
_AVAILABLE = tuple(_BUILTIN)
ENTRY_POINT_GROUP = "robot_agent.planners"


def _observed(planner):
    """The planner behind a transparent proxy that records its generate_plan /
    replan calls on the current run (robot_agent.observe.record)."""
    from ...observe.record import recording_planner
    return recording_planner(planner)


def _entry_points() -> dict:
    """{name (lower case): EntryPoint} of the ``robot_agent.planners`` group."""
    try:
        from importlib.metadata import entry_points
        return {ep.name.lower(): ep for ep in entry_points(group=ENTRY_POINT_GROUP)}
    except Exception:
        return {}


def _load_ref(ref: str, package=None):
    """``"module:attr"`` -> the attribute (a ValueError that names ``ref`` when it cannot)."""
    mod_name, _, attr = ref.partition(":")
    if not mod_name or not attr:
        raise ValueError(f"planner {ref!r}: expected 'package.module:Class'")
    try:
        mod = importlib.import_module(mod_name, package)
    except ImportError as e:
        raise ValueError(f"planner {ref!r}: cannot import {mod_name!r} ({e})") from e
    obj = mod
    for part in attr.split("."):
        if not hasattr(obj, part):
            raise ValueError(f"planner {ref!r}: {mod_name!r} has no {attr!r}")
        obj = getattr(obj, part)
    return obj


def resolve_planner(name: str):
    """``(factory, builtin)``: the planner class (or factory) behind ``name``.

    Raises ValueError for an unknown name (the message lists the choices)."""
    key = (name or "").strip()
    low = key.lower()
    if low in _BUILTIN:
        return _load_ref(_BUILTIN[low], __package__), True
    if ":" in key:
        return _load_ref(key), False
    eps = _entry_points()
    ep = eps.get(low)
    if ep is not None:
        try:
            return ep.load(), False
        except Exception as e:
            raise ValueError(f"planner {name!r}: entry point {ep.value!r} failed to load ({e})") from e
    available = ", ".join(_AVAILABLE + tuple(sorted(eps)))
    raise ValueError(
        f"unknown planner {name!r}; available planners: {available} "
        f"(or 'package.module:Class', or an entry point in group {ENTRY_POINT_GROUP!r})"
    )


def is_planner_name(name) -> bool:
    """True when ``name`` names a planner the registry can try to load (built-in,
    ``module:Class`` or an installed entry point); nothing is imported."""
    if not isinstance(name, str) or not name.strip():
        return False
    key = name.strip()
    return key.lower() in _BUILTIN or ":" in key or key.lower() in _entry_points()


def accepted_kwargs(factory, cfg: dict) -> dict:
    """The part of ``cfg`` that ``factory`` accepts (all of it when it takes
    ``**kwargs``); None values are dropped, so a planner's own defaults apply."""
    cfg = {k: v for k, v in cfg.items() if v is not None}
    try:
        params = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        return cfg
    if any(p.kind is p.VAR_KEYWORD for p in params):
        return cfg
    names = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    return {k: v for k, v in cfg.items() if k in names}


def planner_available(name: str) -> bool:
    """Can ``name`` be built here (its module and dependencies import)? A
    built-in backend module may define ``available()`` (``grace``: is
    embodiplan installed)."""
    try:
        factory, builtin = resolve_planner(name)
    except Exception:
        return False
    check = getattr(inspect.getmodule(factory), "available", None) if builtin else None
    try:
        return bool(check()) if callable(check) else True
    except Exception:
        return False


def get_planner(name: str, **cfg) -> Planner:
    """Return a planner backend instance for ``name``.

    Parameters
    ----------
    name:
        A built-in name (case-insensitive: ``"grace"``, ``"llm_direct"``),
        ``"package.module:Class"``, or a ``robot_agent.planners`` entry point.
    **cfg:
        Backend config (host, model, live_path, capabilities, ...). A built-in
        backend gets it verbatim; a plug-in gets the keys its constructor
        accepts (all of them when it takes ``**kwargs``).

    Raises
    ------
    ValueError
        If ``name`` is not a known backend; the message lists the available
        backends.
    TypeError
        If a plug-in does not build a :class:`~.base.Planner`.
    """
    factory, builtin = resolve_planner(name)
    planner = factory(**cfg) if builtin else factory(**accepted_kwargs(factory, cfg))
    if not builtin and not isinstance(planner, Planner):
        raise TypeError(f"planner {name!r} built a {type(planner).__name__}, which has no "
                        "generate_plan / replan (see docs/planners.md)")
    return _observed(planner)
