"""Declared rules of a pack's actions: preconditions, effects on the fact store, typed arguments (Step S26).

What: pure functions over the ``actions`` and ``predicates`` tables of a resolved pack. ``of_pack(pack)`` keeps what is
declared: the actions with ``rules: declared`` (their ``pre`` and ``effects``) and the actions that declare ``args``.
``violations`` lists the ``pre`` predicates that do not hold in a world (code ``pre_<predicate>``); ``binding`` and
``apply_effects`` add and remove the ``effects`` facts of a step; ``typed_params`` checks a step's ``args`` against the
declaration and gives the ones the bound skill declares. Why: a custom action had no precondition, no belief effect and no
typed argument (docs/proposals/generalize_core_design.md, "Actions" and "Predicates"; fit G2, G3).
How: the action model wrapper (core/planning/action_model.py) is the one caller of ``violations`` / ``apply_effects``, so the
plan gate and the pre-step guard get the rules with no new call site; a run makes its robot's rules ACTIVE (``use``) at its
start and puts the previous ones back at its end (core/planning/loop.py); ``_bind`` (phases/exec_helpers.py) adds the typed
arguments. Off: a pack with no ``rules: declared`` action and no ``args`` gives ``EMPTY`` and every caller is the old code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from robot_agent.contract import Unbindable
from robot_agent.contract.pack import pred_parts

DECLARED = "declared"                 # the value of an action's `rules` that this module reads (the other is the action model)
HELD = "held"                         # ?held binds the object in the hand BEFORE the step (the design's PEX example: Seat)
WILD = "*"


@dataclass(frozen=True)
class Rules:
    """What a pack declares about its actions. ``declared``: action -> entry with ``rules: declared``; ``args``: action ->
    {argument name -> its spec}; ``predicates``: the pack's predicate table; ``pack``: for the registered evaluators."""
    declared: dict = field(default_factory=dict)
    args: dict = field(default_factory=dict)
    predicates: dict = field(default_factory=dict)
    pack: Any = None


EMPTY = Rules()
_ACTIVE = [EMPTY]


def of_pack(pack) -> Rules:
    """The declared rules of a resolved pack (EMPTY when it declares none)."""
    table = pack.table("actions") if pack is not None else {}
    declared = {n: e for n, e in table.items() if isinstance(e, dict) and e.get("rules") == DECLARED}
    args = {n: dict(e["args"]) for n, e in table.items() if isinstance(e, dict) and isinstance(e.get("args"), dict) and e["args"]}
    if not declared and not args:
        return EMPTY
    return Rules(declared, args, dict(pack.table("predicates")), pack)


def of_robot(robot) -> Rules:
    """The declared rules of the robot's pack IN USE: the one resolved at construction (a manifest that declares ``pack:``)
    or set by ``use_pack``. A robot whose pack was never resolved (schema 1: it declares no rule, design R3) is not resolved
    here, so a run of today's robots does no new work. EMPTY for a stub or a pack error."""
    try:
        pack = vars(robot).get("_pack")             # RobotView.pack's cache; reading ``robot.pack`` would resolve it
        return of_pack(pack) if pack is not None else EMPTY
    except Exception:
        return EMPTY


def use(rules: Rules) -> Rules:
    """Make ``rules`` the active ones (the run's robot); returns the ones active before (to put back at the run's end)."""
    before, _ACTIVE[0] = _ACTIVE[0], rules if isinstance(rules, Rules) else EMPTY
    return before


def active() -> Rules:
    return _ACTIVE[0]


# ── binding ``?name`` to a step ─────────────────────────────────────────────────────────────────────────────────────────
def binding(rules: Rules, step: dict, world) -> dict:
    """{variable -> value} of a step of a declared action: its main argument (``arg.name``) is the step's object, a typed
    argument is its value (or its default), ``held`` is the object in the hand now. Read BEFORE the step's effects."""
    a = str(step.get("action") or "")
    out = {HELD: getattr(world, "holding", None)}
    for n, spec in (rules.args.get(a) or {}).items():
        given = step.get("args") if isinstance(step.get("args"), dict) else {}
        if n in given or isinstance(spec, dict) and "default" in spec:
            out[n] = given[n] if n in given else spec["default"]
    arg = (rules.declared.get(a) or {}).get("arg")
    if isinstance(arg, dict) and arg.get("name"):
        out[arg["name"]] = str(step.get("object") or step.get("target") or "")
    return out


def _values(args: list, bound: dict) -> Optional[tuple]:
    """The predicate's argument values (WILD kept); None when a ``?name`` is not bound (the predicate cannot be told)."""
    out = []
    for x in args:
        if x.startswith("?"):
            v = bound.get(x[1:])
            if v is None or v == "":
                return None
            out.append(str(v))
        else:
            out.append(x)
    return tuple(out)


def _fact_holds(world, name: str, vals: tuple) -> Optional[bool]:
    facts = getattr(world, "facts", None)
    if not isinstance(facts, dict):
        return None                                    # a world with no fact store (the planner's SymbolicState): cannot tell
    return any(len(t) == len(vals) and all(v == WILD or v == x for v, x in zip(vals, t)) for t in facts.get(name, ()))


def holds(rules: Rules, text: str, bound: dict, world) -> Optional[bool]:
    """One predicate (``name(args)``, ``not`` allowed) in ``world``: True / False, None when it cannot be told (not declared,
    an unbound variable, an evaluator that is not registered, cannot tell or fails on the arguments, a wildcard among other
    values for an evaluator)."""
    neg, name, args = pred_parts(text)
    decl = rules.predicates.get(name)
    vals = _values(args, bound) if isinstance(decl, dict) else None
    if vals is None:
        return None
    if decl.get("kind") == "fact":
        r = _fact_holds(world, name, vals)
    else:
        fn = rules.pack.named("predicate", decl.get("eval")) if rules.pack is not None else None
        if fn is None or WILD in vals and set(vals) != {WILD}:
            return None
        try:
            r = fn(world, () if WILD in vals else vals)    # () is the evaluators' wildcard
        except (KeyError, TypeError, ValueError):          # a wrong number of arguments for it: cannot tell
            return None
    return r if r is None or not neg else not r


# ── the gate and the guard: preconditions ───────────────────────────────────────────────────────────────────────────────
def violations(rules: Rules, step: dict, world) -> list:
    """[(code, reason)] of the ``pre`` predicates of a declared action that do NOT hold in ``world`` (cannot tell: not one)."""
    e = rules.declared.get(str(step.get("action") or ""))
    if not e:
        return []
    bound, out = binding(rules, step, world), []
    for text in e.get("pre") or ():
        if holds(rules, text, bound, world) is False:
            _neg, name, args = pred_parts(text)
            shown = ", ".join(str(bound.get(x[1:], x)) if x.startswith("?") else x for x in args)
            out.append((f"pre_{name}", f"the precondition {text.split('(')[0].strip()}({shown}) does not hold"))
    return out


# ── after a step: the effects on the fact store ─────────────────────────────────────────────────────────────────────────
def apply_effects(rules: Rules, step: dict, bound: dict, world) -> None:
    """Add, then remove, the ``effects`` facts of a declared action in ``world`` (in place). Only ``kind: fact`` predicates
    are changed; a negated entry, an unbound variable or a wildcard in ``add`` is skipped; a wildcard in ``del`` removes
    every matching fact. A world with no fact store is left as it is."""
    e = rules.declared.get(str(step.get("action") or ""))
    facts = getattr(world, "facts", None)
    eff = e.get("effects") if e else None
    if not isinstance(eff, dict) or not isinstance(facts, dict):
        return
    for key in ("add", "del"):
        for text in eff.get(key) or ():
            neg, name, args = pred_parts(text)
            vals = _values(args, bound)
            if neg or vals is None or (rules.predicates.get(name) or {}).get("kind") != "fact":
                continue
            if key == "add" and WILD not in vals:
                facts.setdefault(name, set()).add(vals)
            elif key == "del":
                facts[name] = {t for t in facts.get(name, ()) if not (len(t) == len(vals) and all(v in (WILD, x) for v, x in zip(vals, t)))}


# ── binding a step to its skill: typed arguments ────────────────────────────────────────────────────────────────────────
def _type_error(spec: dict, v) -> str:
    t, num = spec.get("type"), isinstance(v, (int, float)) and not isinstance(v, bool)
    ok = {"string": isinstance(v, str), "number": num, "integer": isinstance(v, int) and not isinstance(v, bool),
          "boolean": isinstance(v, bool), "enum": v in (spec.get("values") or ())}.get(t, True)
    if not ok:
        return f"{v!r} is not {'one of ' + str(spec.get('values')) if t == 'enum' else 'a ' + str(t)}"
    if num and t in ("number", "integer") and ("min" in spec and v < spec["min"] or "max" in spec and v > spec["max"]):
        return f"{v!r} is outside [{spec.get('min', '-inf')}, {spec.get('max', 'inf')}]"
    return ""


def typed_params(rules: Rules, step: dict, skill_params=()) -> dict:
    """The typed arguments of ``step`` that reach its skill: each declared argument given in ``step['args']`` (else its
    ``default``) whose name the skill's own ``params`` declare. Raises Unbindable for an argument the action does not
    declare, a value of the wrong type or range, a required one missing. An action that declares no ``args``: {} (as before)."""
    a = str(step.get("action") or "")
    spec = rules.args.get(a)
    if not spec:
        return {}
    given = step.get("args") if step.get("args") is not None else {}
    if not isinstance(given, dict):
        raise Unbindable(f"{a}: the step's args must be a mapping, not {given!r}")
    unknown = sorted(set(given) - set(spec))
    if unknown:
        raise Unbindable(f"{a}: no argument {', '.join(unknown)} (declared: {', '.join(spec)})")
    out = {}
    for n, s in spec.items():
        s = s if isinstance(s, dict) else {}
        if n not in given and "default" not in s:
            if s.get("required"):
                raise Unbindable(f"{a}: the argument {n} is required")
            continue
        v = given[n] if n in given else s["default"]
        bad = _type_error(s, v)
        if bad:
            raise Unbindable(f"{a}: the argument {n}: {bad}")
        if n in skill_params:
            out[n] = v
    return out


__all__ = ["DECLARED", "HELD", "WILD", "Rules", "EMPTY", "of_pack", "of_robot", "use", "active", "binding", "holds",
           "violations", "apply_effects", "typed_params"]
