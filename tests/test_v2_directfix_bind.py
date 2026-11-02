"""v2 directfix: a step that names its skill call binds to that call in one place
(core/planning/concrete.py via RobotView.bind / ActionMapper.to_skill); grace steps bind as before."""

import types

import pytest

from robot_agent.contract import Manifest, Unbindable
from robot_agent.core.planning import action_model, concrete
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.mapper import ActionMapper, Unmappable
from robot_agent.core.planning.robot import RobotView
from robot_agent.core.planning.verify import symbolic_check

ARM = {"robot": "armbot", "skills": [{"name": "move", "action": "MoveTo"}, {"name": "pick", "action": "Pick"}]}
MOVE = {"action": "MoveTo", "object": "kitchen", "skill": "move", "params": {"inputs": "kitchen", "speed": 1},
        "reason": "move::inputs='kitchen', speed=1"}


class Adapter:
    """An adapter whose bind is recorded (and gives its own params)."""

    def __init__(self):
        self.manifest = Manifest.from_dict(ARM)
        self.bound = []

    def bind(self, step, world=None):
        self.bound.append(dict(step))
        return "move", {"inputs": "KITCHEN", "from_adapter": True}


class Registry:
    def __init__(self, *names):
        self.names = set(names)

    def resolve(self, name):
        return name if name in self.names else None


def test_a_concrete_call_wins_over_the_adapter():
    ad = Adapter()
    v = RobotView(ad)
    assert v.bind(dict(MOVE), WorldState()) == ("move", {"inputs": "kitchen", "speed": 1})
    assert ad.bound == []                                          # the abstract binding is not asked
    assert v.bind({"action": "MoveTo", "object": "kitchen"}) == ("move", {"inputs": "KITCHEN", "from_adapter": True})
    assert ad.bound == [{"action": "MoveTo", "object": "kitchen"}]   # a grace step: exactly as before


def test_the_params_are_a_copy():
    st = dict(MOVE, params={"inputs": "kitchen"})
    _skill, p = RobotView(Adapter()).bind(st)
    p["inputs"] = "x"
    assert st["params"] == {"inputs": "kitchen"}


def test_an_unknown_skill_is_unbindable_with_a_clear_message():
    v = RobotView(Adapter())
    with pytest.raises(Unbindable, match="no skill 'fly' on robot 'armbot'"):
        v.bind({"action": "", "object": "x", "skill": "fly", "params": {}, "reason": "fly::x"})


def test_plan_skills_and_aliases_come_from_the_registry():
    v = RobotView(Adapter())
    tidy = {"action": "", "object": "", "skill": "tidy_up", "params": {}, "reason": "tidy_up::"}
    with pytest.raises(Unbindable):
        v.bind(tidy)                                               # no registry: only the manifest's skills
    v.registry = Registry("tidy_up")
    assert v.bind(tidy) == ("tidy_up", {})
    v.registry = Registry()
    with pytest.raises(Unbindable):
        v.bind(tidy)


def test_action_mapper_over_a_robot_view():
    v = RobotView(Adapter())
    m = ActionMapper(v)
    assert m.to_skill(dict(MOVE), WorldState()) == ("move", {"inputs": "kitchen", "speed": 1})
    with pytest.raises(Unmappable, match="no skill 'fly'"):
        m.to_skill({"action": "", "object": "", "skill": "fly", "params": {}}, WorldState())
    assert m.to_skill({"action": "MoveTo", "object": "kitchen"}, WorldState())[1]["from_adapter"] is True
    with pytest.raises(Unmappable, match="step has no action"):
        m.to_skill({"action": "", "object": "x"}, WorldState())


def test_action_mapper_over_a_namemap_module():
    from fakebot.configs import grace_namemap
    m = ActionMapper(grace_namemap)
    assert m.to_skill({"action": "Open", "object": "drawer", "skill": "open_drawer", "params": {"inputs": "drawer"}},
                      WorldState()) == ("open_drawer", {"inputs": "drawer"})
    with pytest.raises(Unmappable, match="no skill 'announce'"):
        m.to_skill({"action": "", "object": "hi", "skill": "announce", "params": {"inputs": "hi"}}, WorldState())
    m2 = ActionMapper(grace_namemap, registry=Registry("announce"))
    assert m2.to_skill({"action": "", "skill": "announce", "params": {"inputs": "hi"}}, WorldState()) == \
        ("announce", {"inputs": "hi"})
    # a grace step through the namemap: unchanged
    assert m.to_skill({"action": "MoveTo", "object": "kitchen"}, WorldState()) == ("move", {"inputs": "kitchen"})


def test_screen_passes_skill_only_steps_and_still_warns_on_grace_ones():
    from fakebot.configs import grace_namemap
    m = ActionMapper(grace_namemap)
    st = {"action": "", "object": "", "skill": "lift", "params": {"height": 0.5}}
    assert m.screen([st, {"action": "MoveTo", "object": "kitchen"}]) == []
    assert len(m.screen([{"action": "Wash", "object": "cup"}])) == 1


def test_a_skill_only_step_has_no_symbolic_effect_or_finding():
    w = WorldState()
    w.update_from_dict({"arrived": "kitchen", "holding": "cup"})
    st = {"action": "", "object": "", "skill": "lift", "params": {"height": 0.5}}
    before = w.to_dict()
    action_model.apply(st, w, inplace=True)
    assert w.to_dict() == before and symbolic_check(st, w) == (True, "")
    assert concrete.skill_only(st) and not concrete.skill_only(MOVE) and not concrete.is_concrete({"action": "MoveTo"})


def test_knows():
    assert concrete.knows("move", {"move"}) and not concrete.knows("fly", {"move"})
    assert concrete.knows("go", (), Registry("go")) and not concrete.knows("go", (), types.SimpleNamespace())
