"""Declared rules of a pack's actions (opt-in, Step S26): preconditions, effects on the fact store, typed arguments.

What: pure readers of the ``actions`` and ``predicates`` tables of a resolved pack. ``of_pack(pack)`` collects, as a
:class:`Rules` value, the actions with ``rules: declared`` (their ``pre`` and their ``effects``) and the actions that declare
typed ``args``. ``violations(rules, step, world)`` lists the ``pre`` predicates of the step's action that do not hold
(code ``pre_<predicate>``); ``binding`` reads the step's variables before it runs and ``apply_effects`` adds and removes the
facts of its ``effects`` afterwards; ``typed_params`` checks a step's ``args`` against the declaration and gives the ones the
bound skill's own ``params`` schema names. ``use`` / ``active`` hold the rules of the robot whose run is going on: the action
model wrapper (``core/planning/action_model.py``, the one place rules are read) and the bind of a step ask ``active()``.
Why: a custom action had no precondition, no belief effect and no typed argument (docs/proposals/generalize_core_design.md,
"Actions" and "Predicates"); the plan gate and the pre-step guard already decide through the action model wrapper, so a
declared action gets a gate and a guard with no new call site.
How a predicate of ``pre`` is read: a ``kind: fact`` predicate holds when the fact store has a matching tuple (``*`` matches
anything); an ``eval`` predicate is its registered evaluator over the belief. ``?<name>`` is bound to the step's object
(``?object`` and the name of the action's ``arg``), to the step's typed argument of that name, or ``?held`` to what the hand
holds before the step. "Cannot tell" (an unbound variable, an evaluator that says None, a name the table lacks) is NOT a
violation; the schema-2 loader already refuses a pack that uses an undeclared predicate or one outside its scope.
Off: a pack that declares no ``rules: declared`` action and no ``args`` gives ``EMPTY`` (every shipped pack and every
schema-1 robot, see ``of_robot``): the wrapper then returns the action model's answer unchanged and the bind adds nothing.
A field ``from_option`` of an argument and a fact that outlives a run (``persistent``) are deferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from robot_agent.contract.adapter import Unbindable
from robot_agent.contract.pack import pred_parts

from .predicates import View

WILD = object()                              # the value of a ``*`` argument: matches anything


@dataclass(frozen=True)
class Rules:
    """The declared rules of one pack. ``declared``: action -> {arg, pre, add, del}; ``args``: action -> {name: spec}."""
    declared: Mapping[str, dict] = field(default_factory=dict)
    args: Mapping[str, dict] = field(default_factory=dict)
    preds: Mapping[str, dict] = field(default_factory=dict)
    pack: Any = field(default=None, compare=False, repr=False)

    @property
    def empty(self) -> bool:
        return not self.declared and not self.args


EMPTY = Rules()
_ACTIVE = [EMPTY]                            # the rules of the run going on (one slot; ``use`` replaces it)


def of_pack(pack) -> Rules:
    """The declared rules of a resolved pack (``EMPTY`` when it declares none)."""
    acts = pack.table("actions") if pack is not None else {}
    declared, args = {}, {}
    for name, e in acts.items():
        if not isinstance(e, dict):
            continue
        if e.get("rules") == "declared":
            eff = e.get("effects") if isinstance(e.get("effects"), dict) else {}
            arg = e.get("arg") if isinstance(e.get("arg"), dict) else {}
            declared[name] = {"arg": arg.get("name"), "pre": tuple(e.get("pre") or ()),
                              "add": tuple(eff.get("add") or ()), "del": tuple(eff.get("del") or ())}
        if isinstance(e.get("args"), dict) and e["args"]:
            args[name] = dict(e["args"])
    if not declared and not args:
        return EMPTY
    return Rules(declared, args, dict(pack.table("predicates")), pack)


def of_robot(robot) -> Rules:
    """The rules of a robot's pack when it is a schema-2 pack already in use (declared in the manifest, resolved when the
    robot was built, or set by ``use_pack``); ``EMPTY`` otherwise: a schema-1 robot is never resolved for this."""
    pack = (getattr(robot, "__dict__", None) or {}).get("_pack")
    return of_pack(pack) if pack is not None and getattr(pack, "schema", 1) >= 2 else EMPTY


def use(rules: Optional[Rules]) -> Rules:
    """Make ``rules`` (None: ``EMPTY``) the active rules; returns the ones they replace (to put back at the run's end)."""
    before, _ACTIVE[0] = _ACTIVE[0], rules if rules is not None else EMPTY
    return before


def active() -> Rules:
    return _ACTIVE[0]


# ── variables and predicates ────────────────────────────────────────────────────────────────────────────────────────
def binding(rules: Rules, step: dict, world) -> dict:
    """{variable: value} of ``step`` read BEFORE it runs ({} when its action is not declared)."""
    d = rules.declared.get(step.get("action"))
    if d is None:
        return {}
    obj = step.get("object") or step.get("target") or ""
    out = {k: v for k, v in (step.get("args") or {}).items()} if isinstance(step.get("args"), dict) else {}
    out.update({"object": obj, "held": getattr(world, "holding", None)})
    if d.get("arg"):
        out[d["arg"]] = obj
    return out


def _values(args: list, bound: dict) -> Optional[tuple]:
    """The predicate's arguments with the variables bound; None when one is unbound (cannot tell)."""
    out = []
    for a in args:
        if a == "*":
            out.append(WILD)
        elif a.startswith("?"):
            v = bound.get(a[1:])
            if v is None or v == "":
                return None
            out.append(v)
        else:
            out.append(a)
    return tuple(out)


def _matches(vals: tuple, fact: tuple) -> bool:
    return len(vals) == len(fact) and all(v is WILD or v == f for v, f in zip(vals, fact))


def _facts(world) -> dict:
    f = getattr(world, "facts", None)
    return f if isinstance(f, dict) else {}


def holds(rules: Rules, text: str, bound: dict, world) -> Optional[bool]:
    """One predicate (``not`` allowed) of a declared rule in ``world``: True / False, None = cannot tell."""
    neg, name, args = pred_parts(text)
    decl, vals = rules.preds.get(name), _values(args, bound)
    if not isinstance(decl, dict) or vals is None:
        return None
    if decl.get("kind") == "fact":
        r = any(_matches(vals, t) for t in _facts(world).get(name, ()))
    else:
        fn = rules.pack.named("predicate", decl.get("eval")) if rules.pack is not None else None
        if fn is None or WILD in vals and len(vals) != 1:
            return None
        r = fn(View(world, {}), () if WILD in vals else vals)
        if r is None:
            return None
    return not r if neg else bool(r)


def violations(rules: Rules, step: dict, world) -> list:
    """[(code, reason)] of the ``pre`` predicates of the step's declared action that do not hold in ``world``."""
    d = rules.declared.get(step.get("action"))
    if not d or not d["pre"]:
        return []
    bound, out = binding(rules, step, world), []
    for text in d["pre"]:
        if holds(rules, text, bound, world) is False:
            out.append((f"pre_{pred_parts(text)[1]}",
                        f"{step.get('action')} {step.get('object') or ''}: its precondition {text.strip()} does not hold"))
    return out


def apply_effects(rules: Rules, step: dict, bound: dict, world) -> None:
    """Remove the ``del`` and add the ``add`` facts of the step's declared action in ``world.facts`` (in place). Only
    ``kind: fact`` predicates are written; a fact with an unbound variable or a ``*`` is not added."""
    d = rules.declared.get(step.get("action"))
    store = getattr(world, "facts", None)
    if not d or not isinstance(store, dict):
        return
    for key, adding in (("del", False), ("add", True)):
        for text in d[key]:
            neg, name, args = pred_parts(text)
            vals = _values(args, bound)
            if (rules.preds.get(name) or {}).get("kind") != "fact" or vals is None:
                continue
            if adding != neg:                     # `add: [not x(..)]` removes, `del: [not x(..)]` adds
                if WILD not in vals:
                    store.setdefault(name, set()).add(vals)
            else:
                left = {t for t in store.get(name, ()) if not _matches(vals, t)}
                if left:
                    store[name] = left
                else:
                    store.pop(name, None)


# ── typed arguments at bind ─────────────────────────────────────────────────────────────────────────────────────────
def _type_error(spec: dict, v) -> str:
    """What is wrong with the value ``v`` under one argument's declaration ('' = nothing)."""
    t = spec.get("type")
    num = isinstance(v, (int, float)) and not isinstance(v, bool)
    ok = {"string": isinstance(v, str), "number": num, "integer": isinstance(v, int) and not isinstance(v, bool),
          "boolean": isinstance(v, bool), "enum": v in (spec.get("values") or ())}.get(t, True)
    if not ok:
        return f"must be {'one of ' + ', '.join(map(str, spec.get('values') or ())) if t == 'enum' else 'a ' + str(t)}"
    if num and t in ("number", "integer"):
        if spec.get("min") is not None and v < spec["min"] or spec.get("max") is not None and v > spec["max"]:
            return f"must be within {spec.get('min')} .. {spec.get('max')}"
    return ""


def param_names(skills, skill: str) -> set:
    """The parameter names the manifest's ``params`` schema declares for the skill named ``skill``."""
    s = next((x for x in skills or () if getattr(x, "name", None) == skill), None)
    props = ((getattr(s, "params", None) or {}).get("properties") or {}) if s is not None else {}
    return set(props) if isinstance(props, dict) else set()


def typed_params(rules: Rules, step: dict, accepted) -> dict:
    """The step's typed ``args`` (defaults filled) that the bound skill accepts. Raises ``Unbindable`` for an argument the
    action does not declare, a wrong value or a missing required one ({} when the action declares no ``args``)."""
    decl = rules.args.get(step.get("action")) or {}
    if not decl:
        return {}
    given = step.get("args") or {}
    if not isinstance(given, dict):
        raise Unbindable(f"{step.get('action')}: args must be a mapping, got {type(given).__name__}")
    unknown = sorted(set(given) - set(decl))
    if unknown:
        raise Unbindable(f"{step.get('action')}: no argument {', '.join(unknown)} (declared: {', '.join(decl)})")
    out = {}
    for name, spec in decl.items():
        spec = spec if isinstance(spec, dict) else {}
        if name not in given and "default" not in spec:
            if spec.get("required"):
                raise Unbindable(f"{step.get('action')}: the argument {name} is required")
            continue
        v = given.get(name, spec.get("default"))
        bad = _type_error(spec, v)
        if bad:
            raise Unbindable(f"{step.get('action')}: the argument {name} {bad}, got {v!r}")
        if name in accepted:
            out[name] = v
    return out


__all__ = ["Rules", "EMPTY", "WILD", "of_pack", "of_robot", "use", "active", "binding", "holds", "violations",
           "apply_effects", "param_names", "typed_params"]
