"""Execution integrity (solution-lab round 3, options ``gate_strict``, ``endstate_check``, ``reset_container``,
``wait_on_robot``; the family option ``exec_integrity`` sets all four; every one defaults OFF and off = the earlier
behaviour byte for byte). Pure helpers; the loop calls them. Robot- and language-agnostic: every robot fact comes
from the pack (action model, manifest ``planner.recovery.wait``), no text of the person is read.

* ``gate_strict``: the gate's symbolic repair is run as today first. Only when it leaves a critical finding it is run
  again from the same start with a score that counts the joined "does not achieve" finding once per unmet goal; that
  result is kept only if it certifies the plan, else today's result stands (so no LLM prompt changes).
  ``bind_subset``: a plan object whose word tokens are a strict subset of exactly one goal object's tokens is renamed
  to it (the earlier rule renamed one-word names only). ``planner.label_tokens: none`` turns it back to exact match
  (labels without spaces, e.g. Japanese or Chinese).
* ``endstate_check``: the check tour's return to the last ``go`` goal is a checked step (retried); if it still fails
  the goal is unmet and the run does not say done.
* ``reset_container``: a PutIn that fails BLOCKED into a container the belief holds open, once per container: put the
  held object down where it was picked, Close then Open the container, fetch the object again, PutIn again.
* ``wait_on_robot``: WAIT_HEALTH runs the pack's declared wait action (manifest ``planner.recovery.wait``:
  ``{action: Wait, schedule_s: [2, 4, 8]}``) and reads health() after each wait. No declaration: today's poll.
"""
from __future__ import annotations

FLAGS = ("gate_strict", "endstate_check", "reset_container", "wait_on_robot")
MISSING_PREFIX = "the plan does not achieve: "


def per_goal_findings(findings: list, n_missing: int) -> list:
    """`findings` with the joined 'does not achieve' finding replaced by one finding per unmet goal (>= 1)."""
    joined = [f for f in findings if str(f).startswith(MISSING_PREFIX)]
    if not joined:
        return list(findings)
    rest = [f for f in findings if f not in joined]
    return rest + [f"unmet goal {j + 1}" for j in range(max(int(n_missing), 1))]


def bind_subset(obj: str, goal_objects, tokens: str = "words") -> str:
    """The goal object `obj` stands for: itself when it is one, else the single goal object whose word tokens
    strictly contain obj's tokens, else `obj`. tokens == "none": exact match only."""
    names = [str(g) for g in goal_objects if g]
    if not obj or obj in names or tokens == "none":
        return obj
    tok = set(str(obj).split())
    fit = [n for n in names if tok and tok < set(n.split())]
    return fit[0] if len(fit) == 1 else obj


def wait_schedule(declared) -> tuple:
    """(action, [seconds]) from the manifest's ``planner.recovery.wait``, or (None, []) when not declared / bad."""
    if not isinstance(declared, dict):
        return None, []
    act = str(declared.get("action") or "")
    try:
        sched = [float(s) for s in (declared.get("schedule_s") or []) if float(s) > 0]
    except (TypeError, ValueError):
        return None, []
    return (act, sched) if act and sched else (None, [])


def reset_steps(container: str, held: str, source: str) -> list:
    """The steps spliced before a failed PutIn: put `held` down at `source`, Close and Open `container`, fetch again."""
    mv = lambda p: {"action": "MoveTo", "object": p, "grounded": True}      # noqa: E731
    return [mv(source), {"action": "Place", "object": source}, mv(container), {"action": "Close", "object": container},
            {"action": "Open", "object": container}, mv(source), {"action": "Find", "object": held},
            {"action": "Pick", "object": held}, mv(container)]


def pick_source(completed: list, container: str, openable) -> str:
    """The place the robot's own completed steps went to last that is not the container and cannot be opened."""
    return next((s.get("object") for s in reversed(completed) if s.get("action") == "MoveTo"
                 and s.get("object") != container and not openable(s.get("object"))), "")
