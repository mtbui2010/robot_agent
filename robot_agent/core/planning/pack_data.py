"""Facts a pack declares carry their origin: a ``source`` label (a string) or ``assumed: true``. Data with neither is not used (fail open)."""

from __future__ import annotations


def labelled(data) -> bool:
    """Does the declared ``data`` (a dict) carry a ``source`` string or ``assumed: true``?"""
    return isinstance(data, dict) and (isinstance(data.get("source"), str) and bool(data["source"].strip()) or data.get("assumed") is True)
