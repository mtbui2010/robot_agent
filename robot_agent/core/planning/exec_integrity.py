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
  held object down where it was picked (only when no other hand is free), run the pack's declared remedy on the
  container (manifest ``planner.recovery.blocked_putin``, e.g. ``[Close, Open]``), fetch the object again, PutIn
  again. No declaration, or an action the robot does not have: nothing is done (the earlier recovery stands).
* ``wait_on_robot``: WAIT_HEALTH runs the pack's declared wait action (manifest ``planner.recovery.wait``:
  ``{action: Wait, schedule_s: [2, 4, 8]}``) and reads health() after each wait. No declaration: today's poll.
"""
from __future__ import annotations

FLAGS = ("gate_strict", "endstate_check", "reset_container", "wait_on_robot")
MISSING_PREFIX = "the plan does not achieve: "
DEFAULT_RETURN_TRIES = 3         # endstate_check: tries to re-achieve a go goal (manifest ``planner.recovery.return_tries``)


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


def blocked_putin_remedy(declared) -> list:
    """The actions the pack declares to run on a container after a BLOCKED PutIn (``planner.recovery.blocked_putin``,
    a list of action names), or [] when it declares none (then ``reset_container`` does nothing)."""
    if not isinstance(declared, (list, tuple)):
        return []
    return [str(a) for a in declared if str(a).strip()]


def reset_steps(container: str, held: str, source: str, remedy=("Close", "Open"), put_down: bool = True) -> list:
    """The steps spliced before a failed PutIn: put `held` down at `source` (when `put_down`), run the `remedy`
    actions on `container`, fetch again (only after a put-down)."""
    mv = lambda p: {"action": "MoveTo", "object": p, "grounded": True}      # noqa: E731
    fix = [{"action": a, "object": container} for a in remedy]
    if not put_down:
        return [mv(container)] + fix
    return [mv(source), {"action": "Place", "object": source}, mv(container)] + fix + [
        mv(source), {"action": "Find", "object": held}, {"action": "Pick", "object": held}, mv(container)]


def reset_actions(remedy, put_down: bool = True) -> set:
    """Every action `reset_steps` uses (the loop needs the robot to have all of them)."""
    base = {"MoveTo"} | set(remedy)
    return base | ({"Place", "Find", "Pick"} if put_down else set())


def pick_source(completed: list, container: str, openable) -> str:
    """The place the robot's own completed steps went to last that is not the container and cannot be opened."""
    return next((s.get("object") for s in reversed(completed) if s.get("action") == "MoveTo"
                 and s.get("object") != container and not openable(s.get("object"))), "")
