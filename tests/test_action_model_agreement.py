"""One action model: every robot_agent copy of the action rules agrees with embodiplan.action_model.

The cases are the rows of docs/action_model_divergence.md. Before this change the copies (embodiplan
verifier, verify.symbolic_check, mapper.apply_effect, AssistLoop._check / _effect) disagreed on many
of them; now each one asserts that all paths give the same decision.

Run: PYTHONPATH=<robot_agent>:<embodiplan> python -m pytest -q tests/test_action_model_agreement.py
"""
import re
from pathlib import Path

import pytest

import robot_agent.core.planning.assist_loop as A
from embodiplan import action_model as am
from embodiplan import verifier as ev
from robot_agent.core.planning import action_model as ram
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.mapper import ActionMapper
from robot_agent.core.planning.verify import symbolic_check

ROOT = Path(__file__).resolve().parents[1]
T, S, D = "table@kitchen", "shelf@living room", "drawer@bedroom"
PLACES = Places({T: {"loc": {"x": 0, "y": 0}}, S: {"loc": {"x": 2, "y": 0}},
                 D: {"loc": {"x": 4, "y": 0}, "openable": True}})
# _check findings about goals, not about the action rules
GOAL_LEVEL = ("no goal needs it", "the plan does not achieve", "the goals are done", "already placed")


class NameMap:
    SKILL_MAP = {"MoveTo": "move", "Find": "find", "Pick": "pick", "Place": "place", "PutIn": "place",
                 "Open": "open_drawer", "Close": "close_drawer", "TurnOn": "turn_on", "TurnOff": "turn_off",
                 "Wash": "wash"}
    NOOP_ACTIONS = {"Sit", "LieOn", "Serve", "Wait"}
    SUPPORTED_ACTIONS = set(SKILL_MAP) | NOOP_ACTIONS
    to_loc = staticmethod(lambda s: s)
    to_obj = staticmethod(lambda s: s)


def ws(d):
    return WorldState(arrived=d.get("arrived"), found=d.get("found"), holding=d.get("holding"),
                      opened=set(d.get("opened", ())), on=set(d.get("on", ())))


def loop():
    al = object.__new__(AssistLoop)
    al._namemap = NameMap
    return al


def C(id_, world, action, obj="", expect=None, goals=()):
    return pytest.param(world, {"action": action, "object": obj}, expect, list(goals), id=id_)


# expect: the first error code, "warn:<code>" for a warning only, None for admissible
CASES = [
    C("C01-moveto-no-destination", {}, "MoveTo", "", "empty_object"),
    C("C02-moveto-already-there", {"arrived": T}, "MoveTo", T, "warn:already_there"),
    C("C03-find-no-object", {"arrived": T}, "Find", "", "empty_object"),
    C("C04-find-at-unarrived-place", {"arrived": S}, "Find", "cup", "find_wrong_place",
      goals=[{"type": "on", "object": "cup", "from": T, "to": S}]),
    C("C05-find-before-any-moveto", {}, "Find", "cup", None),
    C("C06-find-held-object", {"arrived": T, "holding": "cup"}, "Find", "cup", "find_held"),
    C("C07-pick-without-find", {"arrived": T}, "Pick", "cup", "no_find_before_pick"),
    C("C08-pick-while-holding", {"arrived": T, "found": "apple", "holding": "cup"}, "Pick", "apple",
      "already_holding"),
    C("C09-pick-other-than-found", {"arrived": T, "found": "apple"}, "Pick", "cup", "pick_not_found"),
    C("C10-pick-no-object-after-find", {"arrived": T, "found": "cup"}, "Pick", "", None),
    C("C11-pick-held-object", {"arrived": T, "holding": "cup"}, "Pick", "cup", "already_holding"),
    C("C12-place-without-holding", {"arrived": S}, "Place", S, "place_without_holding"),
    C("C13-place-elsewhere", {"arrived": T, "holding": "cup"}, "Place", S, "place_wrong_location"),
    C("C14-place-ok", {"arrived": S, "holding": "cup"}, "Place", S, None),
    C("C15-place-on-closed-drawer", {"arrived": D, "holding": "cup"}, "Place", D, None),
    C("C16-putin-closed-drawer", {"arrived": D, "holding": "cup"}, "PutIn", D, "putin_closed_container"),
    C("C17-putin-open-drawer", {"arrived": D, "holding": "cup", "opened": {D}}, "PutIn", D, None),
    C("C18-putin-non-container", {"arrived": S, "holding": "cup"}, "PutIn", S, None),
    C("C19-putin-not-there", {"arrived": T, "holding": "cup", "opened": {D}}, "PutIn", D, "place_wrong_location"),
    C("C20-open-drawer-twice", {"arrived": D, "opened": {D}}, "Open", D, "already_open"),
    C("C21-open-while-holding", {"arrived": D, "holding": "cup"}, "Open", D, "hand_full"),
    C("C22-open-not-there", {"arrived": T}, "Open", D, "interact_without_find"),
    C("C23-open-found-not-there", {"arrived": T, "found": D}, "Open", D, "interact_without_find"),
    C("C24-open-ok", {"arrived": D}, "Open", D, None),
    C("C25-close-already-closed", {"arrived": D}, "Close", D, "close_not_opened"),
    C("C26-close-while-holding", {"arrived": D, "opened": {D}, "holding": "cup"}, "Close", D, "hand_full"),
    C("C27-open-not-openable", {"arrived": S}, "Open", S, "not_openable"),
    C("C28-turnon-already-on", {"arrived": "lamp", "found": "lamp", "on": {"lamp"}}, "TurnOn", "lamp",
      "already_on"),
    C("C29-turnoff-when-off", {"arrived": "lamp", "found": "lamp"}, "TurnOff", "lamp", "turnoff_not_on"),
    C("C30-wash-unreached", {"arrived": T}, "Wash", "cup", "interact_without_find"),
    C("C31-serve-empty-hand", {}, "Serve", "cup", "warn:serve_without_holding"),
    C("C32-unknown-action", {}, "Fly", "cup", "unknown_action"),
]


def robot_ctx(goals):
    return dict(capabilities=ram.ROBOT_CAPABILITIES, openable=PLACES.openable, containers=PLACES.openable,
                object_places={g["object"]: g["from"] for g in goals if g.get("from")},
                actions=set(NameMap.SKILL_MAP) | NameMap.NOOP_ACTIONS)


def decision(violations):
    errs = [v.code for v in violations if v.is_error]
    if errs:
        return errs[0]
    warns = [v.code for v in violations if not v.is_error]
    return f"warn:{warns[0]}" if warns else None


@pytest.mark.parametrize("world,step,expect,goals", CASES)
def test_model_decision(world, step, expect, goals):
    assert decision(am.check(step, ws(world), **robot_ctx(goals))) == expect


@pytest.mark.parametrize("world,step,expect,goals", CASES)
def test_plan_checker_agrees(world, step, expect, goals):
    st = dict(step, grounded=True)
    probs = [p for p in loop()._check([st], ws(world), PLACES, goals)
             if p[0] == 0 and not any(g in p[1] for g in GOAL_LEVEL)]
    critical = [p for p in probs if p[2]]
    if expect is None:
        assert probs == []
    elif expect.startswith("warn:"):
        assert critical == [] and probs
    else:
        assert critical, f"_check missed {expect}"


@pytest.mark.parametrize("world,step,expect,goals", CASES)
def test_symbolic_check_agrees(world, step, expect, goals):
    ok, why = symbolic_check(dict(step), ws(world))
    if expect is None or expect.startswith("warn:") or expect in ram.CONTEXT_CODES:
        # context-only rules need the vocabulary / ENV / goals, which the post-step verify does not have
        assert ok, why
    else:
        assert not ok and why
    if expect == "not_openable":                     # the step guard passes the ENV
        assert symbolic_check(dict(step), ws(world), openable=PLACES.openable)[0] is False


@pytest.mark.parametrize("world,step,expect,goals", CASES)
def test_embodiplan_facade_agrees(world, step, expect, goals):
    ok, code, _ = ev.verify_step(dict(step), ws(world), capabilities=ram.ROBOT_CAPABILITIES)
    if expect is None or expect.startswith("warn:") or expect in ("find_wrong_place", "not_openable"):
        assert ok
    else:
        assert code == expect


def test_check_never_mutates_world():
    w = ws({"arrived": D, "holding": "cup", "opened": {D}})
    before = w.to_dict()
    loop()._check([{"action": "Close", "object": D}, {"action": "PutIn", "object": D}], w, PLACES, [])
    symbolic_check({"action": "Close", "object": D}, w)
    assert w.to_dict() == before


# ── effects ──────────────────────────────────────────────────────────────────
EFFECTS = [
    ({"arrived": T, "found": "cup"}, {"action": "MoveTo", "object": S}),
    ({"arrived": T, "found": D}, {"action": "MoveTo", "object": D}),
    ({"arrived": T, "found": "cup"}, {"action": "Pick", "object": ""}),
    ({"arrived": T, "found": "apple"}, {"action": "Pick", "object": "cup"}),
    ({"arrived": S, "holding": "cup"}, {"action": "Place", "object": S}),
    ({"arrived": D, "holding": "cup", "opened": {D}}, {"action": "PutIn", "object": D}),
    ({"arrived": D}, {"action": "Open", "object": D}),
    ({"arrived": D, "opened": {D}}, {"action": "Close", "object": D}),
    ({"found": "lamp"}, {"action": "TurnOn", "object": "lamp"}),
]
SYMBOLIC = ("arrived", "found", "holding", "opened", "on")


def sym(w):
    return {k: getattr(w, k) for k in SYMBOLIC}


class _Memory:
    def __init__(self):
        self.seen = []

    def see(self, *a):
        self.seen.append(a)


@pytest.mark.parametrize("world,step", EFFECTS)
def test_effects_agree(world, step, monkeypatch):
    model = am.apply(step, ws(world))
    m = ws(world)
    ActionMapper(NameMap).apply_effect(dict(step), m)
    monkeypatch.setattr(A, "current", lambda: (_ for _ in ()).throw(RuntimeError("no runtime")))
    e, placed = ws(world), {}
    loop()._effect(dict(step), e, _Memory(), placed)
    assert sym(m) == sym(model) == sym(e)
    if step["action"] in ("Place", "PutIn"):
        assert placed == {"cup": step["object"]} and e.holding_since is None
    if step["action"] == "Pick":
        assert e.holding_since is not None


# ── whole plans through the checker ──────────────────────────────────────────
def P(*pairs):
    return [{"action": a, "object": o, "grounded": True} for a, o in pairs]


GOALS_IN = [{"type": "in", "object": "cup", "from": T, "to": D}]


def test_good_plan_has_no_critical_finding():
    plan = P(("MoveTo", D), ("Open", D), ("MoveTo", T), ("Find", "cup"), ("Pick", "cup"),
             ("MoveTo", D), ("PutIn", D))
    assert [p for p in loop()._check(plan, WorldState(), PLACES, GOALS_IN) if p[2]] == []


def test_open_twice_and_open_with_full_hand_are_critical():
    plan = P(("MoveTo", D), ("Open", D), ("Open", D), ("MoveTo", T), ("Find", "cup"), ("Pick", "cup"),
             ("MoveTo", D), ("PutIn", D))
    crit = [(i, t) for i, t, c in loop()._check(plan, WorldState(), PLACES, GOALS_IN) if c]
    assert any(i == 2 and "already open" in t for i, t in crit)
    plan = P(("MoveTo", T), ("Find", "cup"), ("Pick", "cup"), ("MoveTo", D), ("Open", D), ("PutIn", D))
    crit = [(i, t) for i, t, c in loop()._check(plan, WorldState(), PLACES, GOALS_IN) if c]
    assert any(i == 4 and "while holding the cup" in t for i, t in crit)


def test_checker_and_model_simulation_agree_on_a_plan():
    plan = P(("MoveTo", T), ("Pick", "cup"), ("MoveTo", D), ("Close", D), ("PutIn", D))
    rep = am.simulate(plan, WorldState(), stop_on_error=False, apply_on_error=True,
                      **robot_ctx(GOALS_IN))
    model_bad = {v.index for v in rep.violations}
    checker_bad = {i for i, t, c in loop()._check(plan, WorldState(), PLACES, GOALS_IN)
                   if c and i < len(plan) and not any(g in t for g in GOAL_LEVEL)}
    assert model_bad == checker_bad == {1, 3, 4}


# ── SAGE editing through embodiplan's public API only ────────────────────────
class _Block:
    def __init__(self, name, steps):
        self.name, self.steps = name, steps


class PublicOnlySage:
    """Exposes only the public 0.2 editing surface; any private access would fail."""

    def __init__(self, blocks, fixed):
        self.last_blocks, self.fixed, self.calls = blocks, fixed, []

    def retrieve_examples(self, task):
        return [], ""

    def edit_block(self, block, feedback, *, task, state=None, obs="", visible_objects=None, examples_block=None):
        self.calls.append((block.name, feedback, state))
        return self.fixed


def test_sage_edit_uses_public_api():
    al = loop()
    al.cfg = {"edit_rounds": 1}
    blocks = [_Block("put the cup in the drawer",
                     [{"action": "MoveTo", "object": T}, {"action": "Find", "object": "cup"},
                      {"action": "Pick", "object": "cup"}, {"action": "MoveTo", "object": D},
                      {"action": "Open", "object": D}, {"action": "PutIn", "object": D}])]
    fixed = [{"action": "MoveTo", "object": D}, {"action": "Open", "object": D},
             {"action": "MoveTo", "object": T}, {"action": "Find", "object": "cup"},
             {"action": "Pick", "object": "cup"}, {"action": "MoveTo", "object": D},
             {"action": "PutIn", "object": D}]
    sage = PublicOnlySage(blocks, fixed)
    ground = lambda seq: [dict(s, grounded=True) for s in seq]
    plan, notes = al._sage_edit(sage, "put the cup in the drawer", "", ground, WorldState(), PLACES,
                                GOALS_IN, "cup in drawer")
    assert [s["action"] for s in plan] == [s["action"] for s in fixed]
    assert sage.calls and "while holding the cup" in sage.calls[0][1]
    assert notes


def test_no_private_embodiplan_access():
    src = (ROOT / "robot_agent/core/planning/assist_loop.py").read_text()
    assert not re.search(r"\b(_refine|_last_blocks|_retrieve_block|_flatten)\b", src), \
        "assist_loop reaches into embodiplan internals"
    for mod in ("verify.py", "mapper.py", "assist_loop.py"):
        text = (ROOT / "robot_agent/core/planning" / mod).read_text()
        assert "embodiplan.verifier" not in text


def test_embodiplan_pin():
    assert '"embodiplan>=0.2,<0.3"' in (ROOT / "pyproject.toml").read_text()
    assert ram.ACTION_MODEL_VERSION == am.ACTION_MODEL_VERSION
