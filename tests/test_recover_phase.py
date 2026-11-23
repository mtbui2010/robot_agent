"""Step S14 (part a): _step_succeeded, _reset_container, _apply_policy, _wait_on_robot and _wait_health moved verbatim from
assist_loop.py to the RecoverPhase mixin in phases/recover.py. These tests prove nothing changed:
(1) the mixin is in AssistLoop's method resolution order, serves the five names, no other class of the order defines them,
assist_loop.py no longer defines them and _step_failed stays there;
(2) the method bodies are the same syntax trees as the reference copy (tests/legacy_ref/s14_recover.py, copied from 3deb5c5),
the one relative import read by its module name;
(3) every global name the bodies use is the same object in the new module as in the loop's old module (same code + same
globals = same behaviour). The golden cassettes replay the runs that reach them (success, wait_health, reset_container)."""

import ast
import inspect

from legacy_ref import s14_recover as old
from robot_agent.core import run_control
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.phases import PHASE_MIXINS, recover

NAMES = ("_step_succeeded", "_reset_container", "_apply_policy", "_wait_on_robot", "_wait_health")


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_five_names():
    assert recover.RecoverPhase in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(recover.RecoverPhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(assist_loop.AssistLoop), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(recover.RecoverPhase, n), n


def test_no_other_class_of_the_order_defines_the_names():
    for n in NAMES:
        owners = [c.__name__ for c in assist_loop.AssistLoop.__mro__ if n in vars(c)]
        assert owners == ["RecoverPhase"], (n, owners)


def test_only_the_named_methods_are_in_the_mixin():
    own = {k for k, v in vars(recover.RecoverPhase).items() if callable(v)}
    assert own == set(NAMES)


def test_step_failed_stays_in_the_loop_file():
    assert "_step_failed" in vars(assist_loop.AssistLoop) and "_step_failed" not in vars(recover.RecoverPhase)


# -- (2) the bodies are the same syntax trees ---------------------------------------------------------------------------------
class _SameImport(ast.NodeTransformer):
    """``from ...run_control`` (the new module) and ``from robot_agent.core.run_control`` (the copy) name one module."""

    def visit_ImportFrom(self, node):
        node.module, node.level = node.module.split(".")[-1], 0
        return node


def _methods(cls):
    tree = _SameImport().visit(ast.parse(inspect.getsource(cls)))
    return {n.name: ast.dump(n) for n in tree.body[0].body if isinstance(n, ast.FunctionDef)}


def test_method_bodies_equal_the_reference_syntax_trees():
    new, ref = _methods(recover.RecoverPhase), _methods(old.RefRecoverPhase)
    assert set(new) == set(ref) == set(NAMES)
    for n in NAMES:
        assert new[n] == ref[n], n


def test_the_relative_import_names_the_run_control_module():
    import importlib
    mod = importlib.import_module("...run_control", recover.__package__)
    assert mod is run_control


# -- (3) the globals the bodies read are the same objects ---------------------------------------------------------------------
def _globals_used(cls):
    names = set()
    for n in ast.walk(ast.parse(inspect.getsource(cls))):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            names.add(n.id)
    return names


def test_global_names_are_the_same_objects_as_in_the_loop_module():
    used = _globals_used(recover.RecoverPhase) & set(vars(recover))
    assert used >= {"time", "FailureCode", "Recovery", "Unbindable", "exec_integrity", "plan_tidy", "sensed", "run_skill",
                    "_NEXT", "_STOP"}
    for n in used:
        assert getattr(recover, n) is getattr(old, n), n
    for n in used - {"Recovery"}:                     # the loop module no longer needs Recovery itself
        assert getattr(recover, n) is getattr(assist_loop, n), n
