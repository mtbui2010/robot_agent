"""The safe parsers that replaced eval(), with every input format the callers see."""

import pytest

from robot_agent.safe_parse import parse_kwargs, parse_literal, parse_number, parse_plan_answer


# ── parse_kwargs: the `skill::args` argument list (plan_skill.parse_inputs, utils.refine_inputs) ──
@pytest.mark.parametrize("args, expected", [
    ("x=0.5,y=0, rz=0", {"x": 0.5, "y": 0, "rz": 0}),                       # moveb::x=0.5,y=0, rz=0
    ("dr6=90, dr5=10", {"dr6": 90, "dr5": 10}),                              # movej::dr6=90, dr5=10
    ("dr5=-20", {"dr5": -20}),                                               # negative number
    ('inputs="fold", mode="front"', {"inputs": "fold", "mode": "front"}),    # movej::inputs="fold", mode="front"
    ('inputs="blue handle", fixed_angle=0, back_to_init=True', {"inputs": "blue handle", "fixed_angle": 0,
                                                                  "back_to_init": True}),
    ("loc='식탁->table@kitchen'", {"loc": "식탁->table@kitchen"}),           # spoken->real value, quoted
    ('inputs="뭐 드실래요?", options="신라면, 짜파게티"', {"inputs": "뭐 드실래요?", "options": "신라면, 짜파게티"}),
    ("pose=[0.1, -0.2, 0.3], q=(1, 2)", {"pose": [0.1, -0.2, 0.3], "q": (1, 2)}),
    ("cfg={'a': 1, 'b': [None, False]}", {"cfg": {"a": 1, "b": [None, False]}}),
    ("x=90+10, y=0.25*2, z=-(3)", {"x": 100, "y": 0.5, "z": -3}),            # numeric arithmetic
    ("x=1e3", {"x": 1000.0}),
    ("{'a': 1}, b=2", {"a": 1, "b": 2}),                                     # dict(mapping, **kw)
    ("**{'a': 1}, b=2", {"a": 1, "b": 2}),
    ("inputs={'x': 1, 'y': 'a,b'}", {"inputs": {"x": 1, "y": "a,b"}}),       # repr() of a $param$ value
])
def test_parse_kwargs_formats(args, expected):
    assert parse_kwargs(args) == expected


@pytest.mark.parametrize("args", [
    "loc=kitchen",                         # bare name: was a NameError, never code
    "x=__import__('os').system('true')",
    "x=open('/etc/passwd')",
    "x=(lambda: 1)()",
    "x='a' * 3",                           # arithmetic is for numbers only
    "x=[1][0]",
    "x=1, x=2",                            # repeated keyword
    "a=1, **{'a': 2}",
    "x=",                                  # syntax error
    "1, 2",
    "x=1); print(1",
])
def test_parse_kwargs_rejects_code(args):
    with pytest.raises(ValueError):
        parse_kwargs(args)


# ── parse_plan_answer: an LLM planning answer (unified_agent.run, llm_direct) ──
def test_plan_answer_json_and_python_repr():
    js = '{"action_types": ["move"], "target_objects": ["cup"], "destination_locations": [null]}'
    assert parse_plan_answer(js) == {"action_types": ["move"], "target_objects": ["cup"],
                                     "destination_locations": [None]}
    rp = "{'target_objects': ['blue bin'], 'target_locations': ['None'], 'destination_locations': ['me']}"
    assert parse_plan_answer(rp)["destination_locations"] == ["me"]
    assert parse_plan_answer({"a": 1}) == {"a": 1}


@pytest.mark.parametrize("raw", [
    "move::kitchen\nfind::cup",            # a plain plan: the caller keeps the text
    "__import__('os').getcwd()",
    "",
])
def test_plan_answer_rejects_non_literals(raw):
    with pytest.raises(ValueError):
        parse_plan_answer(raw)


def test_plan_answer_with_reconstruct_plan():
    from robot_agent.core.guide_manager import reconstruct_plan
    d = parse_plan_answer("{'action_types': ['move', 'pick'], 'target_objects': ['kitchen', 'cup'], "
                          "'destination_locations': [None, 'me@table']}")
    assert reconstruct_plan(d) == "move::kitchen\npick::cup>>table"


# ── literals / numbers ──
def test_parse_literal_and_number():
    assert parse_literal(" [1, 'a', None] ") == [1, "a", None]
    assert parse_literal("-1.5") == -1.5
    with pytest.raises(ValueError):
        parse_literal("os.getcwd()")
    assert parse_number("5") == 5 and isinstance(parse_number("5"), int)
    assert parse_number(" 1.5 ") == 1.5
    assert parse_number("1e3") == 1000.0
    with pytest.raises(ValueError):
        parse_number("five")


# ── the callers ──
def test_plan_skill_parse_inputs():
    from robot_agent.core.plan_skill import parse_inputs
    assert parse_inputs("") == {}
    assert parse_inputs("None") == {}
    assert parse_inputs("kitchen") == {"inputs": "kitchen"}
    assert parse_inputs("x=0.5, y=0") == {"x": 0.5, "y": 0}
    assert parse_inputs("loc=kitchen") == {"inputs": "loc=kitchen"}      # unchanged fallback
    with pytest.raises(ValueError, match="said->real"):
        parse_inputs("loc=식탁->table@kitchen")


def test_plan_skill_substitution_keeps_types():
    from robot_agent.core.plan_skill import _substitute
    assert _substitute("inputs=$v$, mode='front'", {"v": [1, 2]}) == {"inputs": [1, 2], "mode": "front"}
    assert _substitute("inputs=$v$", {"v": "a, 'b'"}) == {"inputs": "a, 'b'"}
    assert _substitute("$v$", {"v": 0.3}) == {"inputs": 0.3}


def test_refine_inputs():
    from robot_agent.utils import refine_inputs as r      # voice extras are optional (try/except)
    assert r(3) == {"inputs": 3}
    assert r("") == {}
    assert r("0.9") == {"inputs": 0.9}
    assert r("front") == {"inputs": "front"}
    assert r("[1, 2]") == {"inputs": [1, 2]}
    assert r("x=0.5,y=0, rz=0") == {"x": 0.5, "y": 0, "rz": 0}


def test_no_eval_in_package():
    """Rule: no eval() on LLM output / input anywhere in robot_agent (ast.literal_eval is fine)."""
    import re
    from pathlib import Path
    import robot_agent
    pkg = Path(robot_agent.__file__).parent
    hits = [f"{f.relative_to(pkg)}:{n}" for f in pkg.rglob("*.py")
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"(?<![\w.])eval\(", line)]
    assert hits == []


def test_helpers_numbers():
    pytest.importorskip("numpy")
    from robot_agent.connect.helpers import findNumdersInString, str2num
    assert findNumdersInString("there are 3 cups and 12 apples, 007") == [3, 12, 7]
    assert str2num("5") == 5 and str2num("2.5") == 2.5 and str2num("abc") == "abc"
