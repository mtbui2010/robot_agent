"""Registered codecs for connection callables (``run_func``, ``encode_func``,
``decode_func``, ``cancel_func`` in ``connections.json``).

A config value names a callable instead of carrying its source:

* ``"codec:<name>"``         -> a function registered with :func:`register_codec`
* ``"pkg.module:func"``      -> imported (``pkg.module``, attribute ``func``)
* ``"pkg.module.func"`` / ``"sensor_msgs/msg/Image"`` -> imported (dotted path)
* a callable                 -> used as is

Inline source (``"def encode_func(data, req): ..."`` / ``"f = lambda d: d"``) is the
deprecated legacy form. It still works by default, through
:func:`_legacy_compile` only (the one ``exec`` in robot_agent), with a one-time
``DeprecationWarning`` per device/key. ``ROBOT_AGENT_NO_INLINE_CODE=1`` turns it
off (a clear error instead).

Migration: ``python -m robot_agent.connect.codecs --scan connections.json``
lists the inline entries; move each into a module and register it::

    from robot_agent.connect.codecs import register_codec

    @register_codec("nav_goal")
    def encode_nav_goal(data, req): ...

then set ``"encode_func": "codec:nav_goal"`` (or ``"my_robot.codecs:encode_nav_goal"``).
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import warnings
from typing import Any, Callable, Optional

__all__ = ["register_codec", "get_codec", "resolve", "is_inline_code", "scan", "CODE_KEYS",
           "InlineCodeDisabled"]

logger = logging.getLogger(__name__)

# connection keys whose value is a callable
CODE_KEYS = ("run_func", "encode_func", "decode_func", "cancel_func")
NO_INLINE_ENV = "ROBOT_AGENT_NO_INLINE_CODE"

_REGISTRY: dict[str, Callable] = {}
_WARNED: set = set()
_IMPORT_PATH = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_][\w.]*$")


class InlineCodeDisabled(RuntimeError):
    """Inline code in a connection config while ``ROBOT_AGENT_NO_INLINE_CODE=1``."""


def register_codec(name: str):
    """Decorator: ``@register_codec("nav_goal")`` makes ``"codec:nav_goal"`` resolve to the function."""
    def deco(fn: Callable) -> Callable:
        if not callable(fn):
            raise TypeError(f"codec {name!r} is not callable")
        old = _REGISTRY.get(name)
        if old is not None and old is not fn:
            logger.warning("codec %r re-registered (%s replaces %s)", name,
                           getattr(fn, "__qualname__", fn), getattr(old, "__qualname__", old))
        _REGISTRY[name] = fn
        return fn
    return deco


def get_codec(name: str) -> Callable:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown codec {name!r} (registered: {known})") from None


def is_inline_code(value: Any) -> bool:
    """Is *value* the legacy inline-source form (a ``def`` / ``lambda`` string)?"""
    return isinstance(value, str) and bool(re.search(r"(^|\n)\s*def \w|\blambda\b", value))


def _resolve_dotted(path: str):
    """``'pkg.mod.attr'`` / ``'sensor_msgs/msg/Image'`` -> the object (longest importable module prefix)."""
    parts = path.replace("/", ".").split(".")
    for i in range(len(parts) - 1, 0, -1):
        try:
            mod = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        obj = mod
        try:
            for p in parts[i:]:
                obj = getattr(obj, p)
            return obj
        except AttributeError:
            continue
    raise ImportError(f"Cannot resolve: {path}")


def _legacy_compile(src: str, key: str, device: str = "?") -> Optional[Callable]:
    """Compile inline source from a connection config (deprecated).

    The only ``exec`` in robot_agent. Returns the function named *key* the
    source defines, else the first callable it defines, else None.
    """
    if os.environ.get(NO_INLINE_ENV, "") not in ("", "0"):
        raise InlineCodeDisabled(
            f"connection {device!r}: {key} is inline code, which {NO_INLINE_ENV}=1 forbids. "
            f"Register it (robot_agent.connect.codecs.register_codec) and set "
            f"\"{key}\": \"codec:<name>\" or \"pkg.module:func\".")
    if (device, key) not in _WARNED:
        _WARNED.add((device, key))
        msg = (f"connection {device!r}: inline code in {key} is deprecated; register it as a codec "
               f"(python -m robot_agent.connect.codecs --scan <connections.json>)")
        warnings.warn(msg, DeprecationWarning, stacklevel=3)
        logger.warning(msg)
    ns: dict = {}
    exec(src, ns)  # noqa: S102 - legacy connections.json only, see module docstring
    return ns.get(key) or next((v for v in ns.values() if callable(v) and not isinstance(v, type)), None)


def resolve(value: Any, key: str = "", device: str = "?", inline: str = "auto"):
    """The callable / object a connection config *value* names.

    ``inline``: ``"auto"`` compiles a ``def``/``lambda`` string (legacy),
    ``"always"`` compiles any other string too (``run_func`` has always been
    plain source), ``"never"`` treats every string as a name. Empty -> None.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        return value
    v = value.strip()
    if v.startswith("codec:"):
        return get_codec(v[len("codec:"):].strip())
    if _IMPORT_PATH.match(v):
        mod, _, attr = v.partition(":")
        obj = importlib.import_module(mod)
        for p in attr.split("."):
            obj = getattr(obj, p)
        return obj
    if inline == "always" or (inline == "auto" and is_inline_code(v)):
        return _legacy_compile(value, key, device)
    return _resolve_dotted(v)


# ── migration helper ──────────────────────────────────────────────────────
def _is_legacy(key: str, value: Any) -> bool:
    """Would :func:`resolve` (as DeviceManager calls it) compile *value* as source?"""
    if not (isinstance(value, str) and value.strip()):
        return False
    v = value.strip()
    if v.startswith("codec:") or _IMPORT_PATH.match(v):
        return False
    return key == "run_func" or is_inline_code(v)


def scan(data: Any) -> list[tuple[str, str, str]]:
    """``[(device, key, first line)]`` for every inline-code value in a connections.json
    payload (a list of ``{type, name, config}`` entries, or any nesting of dicts / lists)."""
    out = []

    def walk(node, device):
        if isinstance(node, list):
            for x in node:
                walk(x, device)
        elif isinstance(node, dict):
            dev = str(node.get("name") or node.get("agent_name") or node.get("conn_name") or device)
            for k, v in node.items():
                if k in CODE_KEYS and _is_legacy(k, v):
                    out.append((dev, k, v.strip().splitlines()[0][:80]))
                elif isinstance(v, (dict, list)):
                    walk(v, dev)

    walk(data, "?")
    return out


def _main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="python -m robot_agent.connect.codecs",
                                 description="List inline code in connections.json (read only).")
    ap.add_argument("--scan", metavar="CONNECTIONS_JSON", nargs="+", required=True)
    args = ap.parse_args(argv)
    total = 0
    for path in args.scan:
        with open(path, encoding="utf-8") as fh:
            hits = scan(json.load(fh))
        total += len(hits)
        print(f"{path}: {len(hits)} inline code entr{'y' if len(hits) == 1 else 'ies'}")
        for dev, key, line in hits:
            print(f"  {dev:24s} {key:12s} {line}")
    if total:
        print("Move each into a module, @register_codec('<name>') it, and set the key to 'codec:<name>'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
