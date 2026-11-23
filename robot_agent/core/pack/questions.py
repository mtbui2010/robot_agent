"""The default kind of a question event: a ``clarify`` that carries no ``kind`` gets the one literal a pack declares.

What: ``with_default_kind(event, default)`` is the pure rule (data in, event out); ``declared_default(robot)`` reads the
pack's ``questions_default_kind`` (None when the pack declares none); ``tag(event, robot)`` joins the two and is what
``RunContext.ev`` (core/planning/loop.py, the one place every event is built) calls. Only a ``clarify`` event is looked at;
an event that has a kind (even a kind of its own) is never changed. The kind goes on the STREAMED event only; the line written to the
run's JSONL log is built before it and stays as it was (RunLogger.event takes its own first argument ``kind``, so a ``kind=`` field
makes that write fail and the row is dropped: the default kind must not make the log lose the clarify rows it has today).
Why: twelve household questions carry no kind, so a reader of the event stream (a fleet manager telling an objection from a
read-back) cannot tell them from each other (docs/proposals/generalize_core_design.md, "Answers", Step S31). A pack that
needs the kind says so in one line and no question site changes.
How to switch off: leave ``questions_default_kind`` out of the pack (the default; no shipped pack sets it) and every event is
built exactly as before. The value ``name`` (each question its own name) is not built yet; the loader refuses it.
"""

from __future__ import annotations

from typing import Any, Optional

EVENT = "clarify"                       # the only event kind this module looks at
KEY = "kind"


def with_default_kind(event: dict, default: Optional[str]) -> dict:
    """``event`` as it is, unless it is a ``clarify`` with no kind (key absent, None or empty) and ``default`` is a
    non-empty text: then a copy of it with ``kind`` = ``default`` (the key goes last, as a kind passed by a caller would)."""
    if event.get("event") != EVENT or event.get(KEY) or not isinstance(default, str) or not default:
        return event
    out = dict(event)
    out.pop(KEY, None)
    out[KEY] = default
    return out


def declared_default(robot: Any) -> Optional[str]:
    """The ``questions_default_kind`` of the robot's pack; None when it declares none or the pack cannot be read here
    (a stub robot, a pack that fails to resolve: the event is then built as it always was)."""
    try:
        value = robot.pack.questions_default_kind
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def tag(event: dict, robot: Any) -> dict:
    """``event`` with the pack's default kind when it is a kind-less ``clarify``. Any other event never reads the pack."""
    if event.get("event") != EVENT or event.get(KEY):
        return event
    return with_default_kind(event, declared_default(robot))


__all__ = ["EVENT", "KEY", "with_default_kind", "declared_default", "tag"]
