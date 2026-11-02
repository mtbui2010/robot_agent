"""v2 directfix: llm_direct's steps keep the robot's call (skill + params) and carry the skill's
manifest action (core/planning/concrete.py, docs/direct_prompt.md "Steps and binding")."""

import types

import pytest

from fakes import FakeLLM

from robot_agent.contract import Manifest
from robot_agent.core.planning import concrete
from robot_agent.core.planning import direct_prompt as dp
from robot_agent.core.planning.backends.llm_direct import LlmDirectBackend
from robot_agent.core.planning.robot import RobotView

ARM = {"robot": "armbot", "capabilities": {"base": True, "arms": 1, "hands": ["right"]},
       "skills": [{"name": "move", "action": "MoveTo"}, {"name": "find", "action": "Find"},
                  {"name": "pick", "action": "Pick"}, {"name": "place", "action": "Place"},
                  {"name": "open_drawer", "action": "Open"}, {"name": "close_drawer", "action": "Close"},
                  {"name": "ask", "action": "Ask"}, {"name": "mobile_pose", "action": "Observe"}]}
DS, DR = "desk@study room", "drawer@kitchen"


def arm_view(**adapter):
    ns = types.SimpleNamespace(manifest=Manifest.from_dict(ARM), seed_memory=None, **adapter)
    return RobotView(ns)


def parse(plan, view=None):
    return LlmDirectBackend._parse_steps(plan, concrete.skill_actions(view or arm_view()))


def test_skill_actions_is_the_reverse_of_the_skill_map():
    acts = concrete.skill_actions(arm_view())
    assert acts == {"move": "MoveTo", "find": "Find", "pick": "Pick", "place": "Place",
                    "open_drawer": "Open", "close_drawer": "Close"}       # Ask / Observe are not plan steps


def test_each_step_keeps_the_call_and_carries_the_action():
    got = parse(f"move::{DS}\nfind::apple\npick::apple\nopen_drawer::{DR}\nplace::{DS}")
    assert [s["action"] for s in got] == ["MoveTo", "Find", "Pick", "Open", "Place"]
    assert [s["skill"] for s in got] == ["move", "find", "pick", "open_drawer", "place"]
    assert got[0] == {"action": "MoveTo", "object": DS, "reason": f"move::{DS}", "skill": "move",
                      "params": {"inputs": DS}}


def test_params_are_read_as_structured_mode_reads_them():
    from robot_agent.core.plan_skill import parse_inputs
    raw = "inputs='apple', type='fine_move'"
    (st,) = parse(f"pick::{raw}")
    assert st["params"] == parse_inputs(raw) == {"inputs": "apple", "type": "fine_move"}
    assert st["action"] == "Pick" and st["object"] == "apple"


def test_a_skill_with_no_plan_action_keeps_its_call():
    lift, ann, ask = parse("lift::height=0.5\nannounce::hello there\nask::where is it?")
    assert lift == {"action": "", "object": "", "reason": "lift::height=0.5", "skill": "lift",
                    "params": {"height": 0.5}}
    assert ann["action"] == "" and ann["object"] == "hello there" and ann["params"] == {"inputs": "hello there"}
    assert ask["action"] == "" and ask["skill"] == "ask"          # Ask is a manifest action but not a plan step
    assert all(concrete.skill_only(s) for s in (lift, ann, ask))


def test_prefixes_go_to_flags():
    a, b, c = parse("!find::apple\n~pick::apple\n%move::kitchen")
    assert (a["skill"], a["action"], a["flags"]) == ("find", "Find", "!")
    assert (b["skill"], b["flags"]) == ("pick", "~") and (c["skill"], c["action"], c["flags"]) == ("move", "MoveTo", "%")
    assert a["reason"] == "!find::apple"


def test_parallel_groups_and_skipped_lines():
    got = parse(f"move::{DS}\nnot a step\nfind::apple && pick::apple && bad\nplace::{DS} && find::cup")
    assert [s["skill"] for s in got] == ["move", "find", "pick", "place", "find"]
    assert [s.get("_parallel_group") for s in got] == [None, 0, 0, 1, 1]


def test_spoken_names_keep_the_real_side():
    (st,) = parse("이동->move::식탁 앞->table_top")
    assert st["skill"] == "move" and st["action"] == "MoveTo" and st["object"] == "table_top"
    assert st["say"] == {"action": "이동", "object": "식탁 앞"} and st["params"] == {"inputs": "식탁 앞->table_top"}


def test_unreadable_params_are_kept_as_an_error():
    (st,) = parse("move::loc=식탁->table@kitchen")
    assert st["params"] == {} and "quotes" in st["params_error"]
    with pytest.raises(concrete.Unbindable, match="quotes"):
        arm_view().bind(st)


def test_without_actions_the_old_shape_comes_back():
    assert LlmDirectBackend._parse_steps("move::kitchen\n!find::cup") == [
        {"action": "move", "object": "kitchen", "reason": "move::kitchen"},
        {"action": "!find", "object": "cup", "reason": "!find::cup"}]


def test_place_into_an_openable_container_is_putin():
    v = arm_view(skill_map={"MoveTo": "move", "Find": "find", "Pick": "pick", "Place": "place", "PutIn": "place",
                            "Open": "open_drawer", "Close": "close_drawer"},
                 is_openable=lambda key, entry: "drawer" in key)
    steps = concrete.into_containers(parse(f"place::{DR}\nplace::{DS}", v), v, {})
    assert [s["action"] for s in steps] == ["PutIn", "Place"]
    # a robot whose PutIn is another skill (or none): Place stays
    steps = concrete.into_containers(parse(f"place::{DR}"), arm_view(is_openable=lambda k, e: True), {})
    assert steps[0]["action"] == "Place"


@pytest.fixture
def backend(monkeypatch):
    import robot_agent.connect.llm as llm_pkg
    v = arm_view()
    monkeypatch.setattr(RobotView, "load", classmethod(lambda cls, pkg: v))
    monkeypatch.setattr(dp, "live_memory_path", lambda: None)
    llm = FakeLLM(f"move::{DS}\npick::apple", "find::apple\npick::apple")
    monkeypatch.setattr(llm_pkg, "init_llm_client", lambda cfg=None: llm)
    return LlmDirectBackend(llm_cfg={"name": "fake"}, robot_pkg="pkg"), llm


def test_generate_plan_and_replan_parse_the_same_way(backend):
    b, llm = backend
    steps, meta = b.generate_plan("Bring the apple.", "", [])
    assert [(s["action"], s["skill"]) for s in steps] == [("MoveTo", "move"), ("Pick", "pick")]
    new, meta2 = b.replan("Bring the apple.", steps[:1], steps[1], "grasp failed", "", [])
    assert [(s["action"], s["skill"], s["params"]) for s in new] == [("Find", "find", {"inputs": "apple"}),
                                                                     ("Pick", "pick", {"inputs": "apple"})]
    assert "failed at step 'pick::apple': grasp failed" in llm.calls[1][0] and meta2["raw"].startswith("find::")
