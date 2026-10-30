"""Safe parsers for text that comes from an LLM, a plan or a user.

Used instead of Python's built-in evaluator on such text. Only literals are accepted (strings, numbers,
``True``/``False``/``None``, lists, tuples, dicts, sets) plus plain arithmetic on
numbers (``-0.5``, ``90+10``, ``0.25*2``) so the argument forms the guides use
keep working. Names, calls, attribute access and the like raise ``ValueError``.

Stdlib only; safe to import from any layer.
"""

from __future__ import annotations

import ast
import json
import operator
from typing import Any

__all__ = ["parse_literal", "parse_kwargs", "parse_number", "parse_plan_answer"]

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _literal(node: ast.AST) -> Any:
    """Value of a literal expression node; numeric arithmetic allowed."""
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _literal(node.left), _literal(node.right)
        if not all(isinstance(v, (int, float, complex)) and not isinstance(v, bool) for v in (left, right)):
            raise ValueError("arithmetic is only allowed on numbers")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
        v = _literal(node.operand)
        if not isinstance(v, (int, float, complex)):
            raise ValueError("unary +/- is only allowed on numbers")
        return _UNOPS[type(node.op)](v)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = [_literal(e) for e in node.elts]
        return {ast.List: list, ast.Tuple: tuple, ast.Set: set}[type(node)](items)
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):           # {**x}
            raise ValueError("dict unpacking is not a literal")
        return {_literal(k): _literal(v) for k, v in zip(node.keys, node.values)}
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as e:
        raise ValueError(f"not a literal: {ast.dump(node)[:80]}") from e


def parse_literal(text: str) -> Any:
    """``text`` as a Python literal (``ast``), what evaluating it gave for literals.

    Raises ``ValueError`` for anything that is not a literal.
    """
    try:
        tree = ast.parse(str(text).strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"cannot parse {str(text)[:80]!r}: {e.msg}") from e
    return _literal(tree.body)


def parse_plan_answer(raw: Any) -> Any:
    """An LLM's planning answer as data: JSON first, then a Python literal
    (``str(dict)`` uses ``'`` and ``True``/``None``). Raises ``ValueError`` when
    it is neither, so the caller can keep the raw text."""
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw)
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        pass
    return parse_literal(s)


def parse_kwargs(args: str) -> dict:
    """``"x=0.5, y=0, mode='front'"`` -> ``{'x': 0.5, 'y': 0, 'mode': 'front'}``.

    Same result as evaluating ``dict(<args>)`` (the old code) for literal values,
    including a single positional mapping and ``**{...}``. Raises ``ValueError``
    on a syntax error or a non-literal value (``loc=kitchen``).
    """
    try:
        tree = ast.parse(f"dict({args})", mode="eval")
    except SyntaxError as e:
        raise ValueError(f"cannot parse arguments {args[:80]!r}: {e.msg}") from e
    call = tree.body
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "dict"):
        raise ValueError(f"cannot parse arguments {args[:80]!r}")
    if len(call.args) > 1 or any(isinstance(a, ast.Starred) for a in call.args):
        raise ValueError(f"at most one positional mapping allowed: {args[:80]!r}")
    try:
        out = dict(*[_literal(a) for a in call.args])
    except TypeError as e:
        raise ValueError(f"positional argument is not a mapping: {args[:80]!r}") from e
    seen = set()
    for kw in call.keywords:
        if kw.arg is None:                              # **{...}
            more = _literal(kw.value)
            if not isinstance(more, dict):
                raise ValueError("** needs a dict literal")
            dup = seen & set(more)
            if dup:
                raise ValueError(f"repeated argument {sorted(dup)[0]!r}")
            seen |= set(more)
            out.update(more)
        else:
            if kw.arg in seen:
                raise ValueError(f"repeated argument {kw.arg!r}")
            seen.add(kw.arg)
            out[kw.arg] = _literal(kw.value)
    return out


def parse_number(s: str):
    """``'5'`` -> 5, ``'1.5'`` / ``'1e3'`` -> float. Raises ``ValueError``."""
    s = str(s).strip()
    try:
        return int(s)
    except ValueError:
        return float(s)
