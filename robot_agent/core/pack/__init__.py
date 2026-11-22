"""The engine's pack API: a robot's vocabulary and rules as declared data plus named callables.

What: ``resolve(manifest, adapter)`` -> :class:`Pack` (the chain core -> base -> robot, merged); ``register(namespace,
"<pack>.<name>", obj)`` for a pack's callables module; ``default_pack()`` for code with no robot at hand.
Why: the engine reads goal kinds, predicates, actions, verifiers, recovery, questions and phrases from here instead of
naming one kind of robot (docs/proposals/generalize_core_design.md). The schema itself is ``robot_agent/contract/pack.py``.
Off: a manifest with no ``pack:`` section is schema 1 and gets the standard pack; nothing is resolved until it is asked for.
Print what a robot resolves to: ``python -m robot_agent.core.pack.show <package or path>``.
"""

from robot_agent.contract.pack import PackDecl, PackError

from .check import Problem
from .loader import Pack, default_pack, resolve
from .registry import NAMESPACES, REGISTRY, Registry, register

__all__ = ["Pack", "PackDecl", "PackError", "Problem", "resolve", "default_pack", "register", "REGISTRY", "Registry", "NAMESPACES"]
