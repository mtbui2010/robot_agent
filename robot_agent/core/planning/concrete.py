"""Plan steps that carry the robot's own skill call (the direct planner's ``skill::inputs``).

A grace / SAGE step is abstract (``{"action": "MoveTo", "object": "kitchen"}``) and is bound to a
skill by the robot's adapter. A direct-planner step (``backends/llm_direct.py``) also names the call
the robot runs::

    {"action": "MoveTo", "object": "kitchen", "skill": "move", "params": {"inputs": "kitchen"},
     "reason": "move::kitchen"}

``action`` is the manifest action of that skill (the world model, verifiers, guard and memory read
it); a skill with no plan action (a plan skill, an alias, ``lift::height=0.5``) gets ``action: ""``
(*skill-only*: no symbolic effect, no symbolic check). :func:`bind` is the one place such a step
becomes ``(skill, params)``: ``RobotView.bind`` and ``ActionMapper.to_skill`` call it first, a step
without ``skill`` binds as before. See docs/direct_prompt.md "Steps and binding".
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Union

from robot_agent.contract import Unbindable

PREFIXES = "!~%"        # run_direct's step flags: ! failure ignored, ~ forced failure, % not narrated


def is_concrete(step: Any) -> bool:
    """Does ``step`` name the robot's skill call (a ``skill`` key)?"""
    return isinstance(step, dict) and bool(step.get("skill"))


def skill_only(step: Any) -> bool:
    """A concrete step with no plan action: no symbolic effect, nothing for the action model to check."""
    return is_concrete(step) and not str(step.get("action") or "").strip()


def skill_actions(view: Any) -> dict:
    """{skill name: plan action} of the robot: its manifest skills whose action is a plan step
    (``RobotView.plan_actions``), the reverse of ``skill_map``. Sensors / Ask / Query map to none."""
    plan = set(getattr(view, "plan_actions", ()) or ())
    out: dict = {}
    for s in getattr(getattr(view, "manifest", None), "skills", ()) or ():
        if s.action in plan:
            out.setdefault(s.name, s.action)
    for action, skill in (getattr(view, "skill_map", {}) or {}).items():
        if action in plan:
            out.setdefault(skill, action)
    return out


def into_containers(steps: list, view: Any, env: Optional[dict] = None) -> list:
    """A ``Place`` into something the robot opens is a ``PutIn`` when the same skill realises both
    (``skill_map``: Place -> place, PutIn -> place): the world model and SAGE's checker read an
    ``in`` goal off PutIn. ``env``: the ENV entries for ``view.openable``. Changes ``steps`` in place."""
    sm = getattr(view, "skill_map", {}) or {}
    for st in steps:
        if (is_concrete(st) and st.get("action") == "Place" and sm.get("PutIn") == st["skill"]
                and view.openable(st.get("object") or "", (env or {}).get(st.get("object")))):
            st["action"] = "PutIn"
    return steps


def _registry_knows(registry: Any, name: str) -> Optional[bool]:
    """Does the skill registry have ``name`` (a skill, a plan skill or an alias)? None: it cannot say."""
    for attr in ("resolve", "get"):
        fn = getattr(registry, attr, None)
        if callable(fn):
            try:
                return fn(name) is not None
            except Exception:
                return None
    return None


def knows(name: str, names: Iterable[str] = (), registry: Any = None) -> bool:
    """Is ``name`` a skill of the robot: one of ``names`` (its manifest / skill map), else one the
    skill registry resolves (plan skills, aliases)?"""
    if name in set(names or ()):
        return True
    return bool(_registry_knows(registry, name)) if registry is not None else False


def bind(step: dict, known: Union[Callable[[str], bool], Iterable[str]], robot: str = "") -> Optional[tuple]:
    """``(skill, params)`` of a concrete step (None when it is not one); raises Unbindable when the
    skill is not one of the robot's (``known``: a predicate or a set of names) or its arguments
    could not be read."""
    if not is_concrete(step):
        return None
    skill = str(step["skill"]).strip()
    ok = known(skill) if callable(known) else skill in set(known)
    if not ok:
        on = f" on robot {robot!r}" if robot else ""
        raise Unbindable(f"no skill {skill!r}{on} (step {step.get('reason') or skill!r})")
    if step.get("params_error"):
        raise Unbindable(f"{skill}: {step['params_error']}")
    return skill, dict(step.get("params") or {})


def parse_item(item: str, actions: Optional[dict] = None) -> Optional[dict]:
    """One ``skill::inputs`` item of a direct program as a plan step (None: not one).

    ``params`` are read exactly as Structured mode reads them (``plan_skill.parse_inputs``);
    ``actions`` is :func:`skill_actions` of the robot (None: the old shape, ``action`` = the skill).
    Spoken names (``이동->move::식탁 앞->table_top``) keep the real side in ``skill`` / ``object``
    and the said side in ``say``; a leading ``! ~ %`` goes to ``flags``."""
    from robot_agent.core.plan_skill import parse_inputs
    from robot_agent.spoken import SpokenError, split_skill, split_value
    bits = item.split("::")
    if len(bits) != 2:
        return None
    name, inputs = bits[0].strip(), bits[1].strip()
    flags = "".join(c for c in name if c in PREFIXES) if actions is not None else ""
    skill, obj, say = name.translate({ord(c): None for c in flags}).strip(), inputs, {}
    try:
        said, skill = split_skill(skill)
        if said:
            say["action"] = said
        if "=" not in inputs and "->" in inputs:
            said, obj = split_value(inputs)
            say["object"] = said.split(">>")[0]
    except SpokenError:
        pass                                    # left as written; the skill call reports it
    try:
        params, err = parse_inputs(inputs), ""
    except ValueError as e:
        params, err = {}, str(e)
    step = _step(skill, obj, params, actions, item.strip())
    step.update({k: v for k, v in (("say", say), ("flags", flags), ("params_error", err)) if v})
    return step


def _step(skill: str, obj: str, params: dict, actions: Optional[dict], reason: str) -> dict:
    if actions is None:                          # no robot: the old shape (action = the skill name)
        return {"action": skill, "object": obj, "reason": reason}
    action = actions.get(skill, "")
    if "=" in obj:                               # k=v arguments: the object is the `inputs` value
        v = params.get("inputs")
        obj = "" if v is None else (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v))
    return {"action": action, "object": obj, "reason": reason, "skill": skill, "params": params}


__all__ = ["PREFIXES", "is_concrete", "skill_only", "skill_actions", "into_containers", "knows", "bind", "parse_item"]
