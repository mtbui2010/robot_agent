"""What an option declares: a name, a key schema, the hooks it implements, a dispatch order (docs/options.md).

An option is a module in ``robot_agent/options/`` that registers an ``OptionSpec`` and a factory. The engine never
imports it: it asks the registry (``registry.py``) and calls the hooks through the dispatcher (``dispatch.py``).
This file holds only the declarations, no behaviour and no domain fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

# What a flow hook returns to end the step it was called from (the loop's own signals, same values).
NEXT, STOP = "next", "stop"

# The sources a key may be read from, in the order they are tried when a key lists them.
SOURCES = ("run", "env", "manifest")

# hook name -> (kind, flow). kind: ``decide`` the first non-None result wins and stops the chain; ``transform`` the
# options are chained, each one gets the value so far and returns a new one (None = unchanged); ``observe`` all run, the
# results are dropped. flow: the hook is a generator (``yield from``) that may yield run events.
HOOKS: Dict[str, Tuple[str, bool]] = {
    "on_start": ("observe", False),          # run set-up: the options' per-run state
    "on_ground": ("transform", False),       # the delivery mode chosen for a goal (grounding)
    "on_plan": ("transform", False),         # the grounded plan, before the provable no-ops are removed: options may drop or add steps
    "on_where_answer": ("decide", True),     # the answer to the engine's own "I couldn't find the X. Where is it?": a place to go to, or None
    "on_understand_schema": ("transform", False),  # the understand call's output schema (a dict): options add optional fields; None = unchanged
    "on_understand_prompt": ("transform", False),  # the understand prompt template (before ``.format``; escape braces): options add lines; None = unchanged
    "on_heard": ("decide", True),            # the heard request, before the reader is called: the option may ask instead (the loop's next step, via ``ask_clarify``) or None
    "on_goals": ("decide", True),            # the understood goals, before the read-back: refuse or drop some
    "on_issues": ("transform", False),       # the read-back's issues (what the words ask for that the goals do not cover): options drop or add some
    "on_goal_edit": ("observe", False),      # a goal change (correction) is about to be read
    "on_goal_reread": ("observe", False),    # the understand LLM re-read a correction: what it asked, found
    "on_goal_unapplied": ("decide", True),   # a correction could not be applied: ask, record
    "on_step": ("decide", True),             # before a step runs: the step may be replaced
    "on_step_done": ("observe", False),      # a step succeeded
    "on_step_failed": ("decide", True),      # a step failed, before the recovery policy
    "on_goal_check": ("decide", False),      # the end-of-run check of one goal: a verdict of its own
    "on_translation_lost": ("decide", False),  # the input translation lost a place name the person said: try again (text, lost, translate)
    "on_second_look": ("decide", False),     # the goal check's first look missed and the second saw it: confirm or not
    "on_final": ("decide", False),           # the end of the run, before the goals are checked
    "on_goals_met": ("decide", False),       # the goals hold: the run may end with something to say
    "on_finish": ("observe", False),         # the ``done`` event's fields, as they are about to be sent
    "on_planner_kwargs": ("transform", False),  # the constructor keys the planner is built with (a dict): options add the ones the planner backend accepts
    "on_goal_replan": ("decide", False),     # the goals changed mid-run, before the plan is repaired: sub-goals for the rest of the plan, or None
    "on_replan_findings": ("transform", False),  # a replan's suffix, after the engine's own findings (a list): options add findings (one refine, then the run ends uncertified)
    "on_listener_rule": ("decide", False),   # a delivery to a person who is also a message's listener (run, goals, words): (goals, notes) of an option's rule in place of the engine's, or None
}

_PYTYPES = {"bool": bool, "int": int, "float": float, "str": str, "dict": dict}


@dataclass(frozen=True)
class Key:
    """One setting of an option. ``aliases``: the legacy flat names, accepted as run options, as manifest ``planner.<alias>``
    and (the first one) as ``$ROBOT_AGENT_<ALIAS>``; the canonical names are ``<option>.<key>`` and ``planner.options.<option>.<key>``."""
    name: str
    type: str = "bool"                       # bool | int | float | str | dict
    default: Any = False
    aliases: Tuple[str, ...] = ()
    sources: Tuple[str, ...] = SOURCES       # precedence, kept PER KEY (the first source that has a value wins)
    choices: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.type not in _PYTYPES:
            raise ValueError(f"key {self.name}: unknown type {self.type!r}")
        bad = [s for s in self.sources if s not in SOURCES]
        if bad:
            raise ValueError(f"key {self.name}: unknown source {bad}")


@dataclass(frozen=True)
class OptionSpec:
    name: str                                # the manifest key planner.options.<name> and the lab_log flag
    keys: Tuple[Key, ...]
    hooks: Tuple[str, ...] = ()              # names from HOOKS the option implements
    order: int = 100                         # dispatch order; the numbers reproduce the order of the old call sites
    enabled: str = ""                        # the bool key that turns the option on ("" = always on once registered)
    services: Tuple[str, ...] = ()           # queries it answers for the engine (``Dispatcher.query``)
    doc: str = ""                            # one line for docs/options.md and ``python -m robot_agent.core.options``

    def __post_init__(self):
        bad = [h for h in self.hooks if h not in HOOKS]
        if bad:
            raise ValueError(f"option {self.name}: unknown hook {bad} (known: {sorted(HOOKS)})")
        names = [k.name for k in self.keys]
        if len(set(names)) != len(names):
            raise ValueError(f"option {self.name}: duplicate key names")
        if self.enabled and self.enabled not in names:
            raise ValueError(f"option {self.name}: enabled key {self.enabled!r} is not a key")

    def key(self, name: str) -> Key:
        return next(k for k in self.keys if k.name == name)

    def flat_names(self) -> Tuple[str, ...]:
        """Every run-option name this spec accepts: legacy aliases and ``<option>.<key>``."""
        return tuple(a for k in self.keys for a in (*k.aliases, f"{self.name}.{k.name}"))


class Option:
    """Base class of an option. A hook is a method named as in ``HOOKS``; an option defines only the ones its spec lists.
    ``cfg`` is its resolved ``OptionConfig``, ``svc`` the engine services it may use (``services.Services``)."""
    spec: OptionSpec

    def __init__(self, cfg, svc) -> None:
        self.cfg = cfg
        self.svc = svc
