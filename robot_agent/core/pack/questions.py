"""The kind a question event carries when the question site gave none (opt-in: a pack's ``questions_default_kind``).

What: ``default_kind(pack)`` reads the literal the pack declares; ``with_kind(event, default)`` gives a ``clarify`` event that
carries no kind that literal; ``for_run(event, robot)`` is the one call the run context makes for every event it builds. Both
decisions are pure (data in, event out); only ``for_run`` reaches for the robot's pack, and only for a kind-less ``clarify``.
Why: about a dozen questions of the loop carry no ``kind`` today. A consumer of the event stream (the fleet manager) then cannot tell
an objection from a read-back; a pack that wants every question labelled declares one literal instead of editing every site.
How to switch off: leave ``questions_default_kind`` out of the pack (the default is null): every event is built exactly as before.
The value ``name`` (each question its own name) needs the table of the household questions and is not built (the loader reports it).
"""

from __future__ import annotations

from typing import Any, Optional

CLARIFY = "clarify"


def default_kind(pack: Any) -> Optional[str]:
    """The literal kind a pack gives a kind-less question: a non-empty text, else None (nothing declared, or not a text)."""
    v = getattr(pack, "questions_default_kind", None)
    return v if isinstance(v, str) and v.strip() and v != "name" else None


def with_kind(event: dict, default: Optional[str]) -> dict:
    """``event`` as it is, or a copy that carries ``kind = default`` when it is a ``clarify`` with no kind (a missing, null or
    empty ``kind``). A kind a question site gave is never replaced."""
    if default is None or event.get("event") != CLARIFY or event.get("kind"):
        return event
    return {**event, "kind": default}


def for_run(event: dict, robot: Any) -> dict:
    """``event`` with the robot's pack default kind applied. The pack is read only for a kind-less ``clarify``; a robot whose
    pack cannot be read, or has no such view, leaves the event as it is."""
    if event.get("event") != CLARIFY or event.get("kind"):
        return event
    try:
        return with_kind(event, default_kind(robot.pack))
    except Exception:
        return event


__all__ = ["CLARIFY", "default_kind", "with_kind", "for_run"]
