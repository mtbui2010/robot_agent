"""v2 direct: the robot description / action list generator (core/planning/robot_prompt.py) and
the direct planner's prompt built from it (core/planning/direct_prompt.py)."""

import json
import types

from robot_agent.contract import Manifest
from robot_agent.core.planning import direct_prompt as dp
from robot_agent.core.planning import robot_prompt as rp
from robot_agent.core.planning.backends import examples as ex
from robot_agent.core.planning.robot import RobotView

ARM = {"robot": "armbot", "capabilities": {"base": True, "arms": 1, "hands": ["right"], "drawer": True},
       "skills": [{"name": "move", "action": "MoveTo"}, {"name": "find", "action": "Find"},
                  {"name": "pick", "action": "Pick"}, {"name": "place", "action": "Place"},
                  {"name": "open_box", "action": "Open", "description": "open the box lid"},
                  {"name": "ask", "action": "Ask"}]}
BASE = {"robot": "cart", "capabilities": {"base": True, "arms": 0, "hands": [], "tray": True},
        "skills": [{"name": "move", "action": "MoveTo"}, {"name": "say", "action": "Say"},
                   {"name": "dock", "action": "Dock"}]}


def view(d, seed=None):
    m = Manifest.from_dict(d)
    return RobotView(types.SimpleNamespace(manifest=m, seed_memory=seed))


def test_description_from_capabilities_and_manifest_key():
    assert rp.describe_robot(view(BASE)) == ("cart: a robot that has no arm, so it cannot pick up, put down or "
                                             "open anything, drives on a mobile base and carries items on a tray.")
    assert "1 arm with a hand (right)" in rp.describe_robot(view(ARM))
    own = dict(ARM, description="  A tidy-up arm\n on wheels. ")
    assert rp.describe_robot(view(own)) == "A tidy-up arm on wheels."
    assert Manifest.from_dict(own).description == "A tidy-up arm\n on wheels."


def test_action_lines_from_skills():
    v = view(ARM)
    lines = rp.action_lines(v)
    assert [ln.split()[0] for ln in lines] == ["MoveTo", "Find", "Pick", "Place", "Open"]   # no Ask
    assert "Open <container>: open the box lid" in lines              # the skill's own description
    assert rp.action_lines(v, "skill")[0] == "- move::<place> (MoveTo): move to a place"
    assert [ln.split()[0] for ln in rp.action_lines(view(BASE))] == ["MoveTo", "Say", "Dock"]


def test_sage_kwargs_and_switch(monkeypatch):
    kw = rp.sage_prompt_kwargs(view(BASE))
    assert kw["actions"][0].startswith("MoveTo") and kw["robot_description"].startswith("cart:")
    monkeypatch.setenv(rp.ENV_SWITCH, "0")
    assert rp.sage_prompt_kwargs(view(BASE)) == {}


def _seed(tmp_path):
    p = tmp_path / "seed.jsonl"
    rows = [("Bring the cup from the kitchen table to the sofa.", ["MoveTo table@kitchen", "Find cup", "Pick cup",
                                                                   "MoveTo sofa", "Place sofa"]),
            ("Open the box.", ["MoveTo box", "Open box"]),
            ("Put the apple on the desk.", ["MoveTo desk", "Find apple", "Pick apple", "Place desk"])]
    with open(p, "w") as f:
        for task, st in rows:
            steps = [{"action": s.split(" ", 1)[0], "object": s.split(" ", 1)[1]} for s in st]
            f.write(json.dumps({"task": task, "plan_text": json.dumps({"steps": steps})}) + "\n")
    return p


PLACES = "- table@kitchen\n- sofa (also: couch)"


def test_prompt_holds_robot_skills_places_examples_command(tmp_path):
    v = view(ARM, seed=_seed(tmp_path))
    cmd = "Bring the cup from the kitchen table to the sofa."
    out = dp.build(v, cmd, places=PLACES, k=1, live_path="")
    p = out["prompt"]
    assert p.startswith("You plan tasks for this robot: armbot: a robot that has 1 arm")
    assert "- open_box::<container> (Open): open the box lid" in p and "ask::" not in p
    assert "Places:\n- table@kitchen\n- sofa (also: couch)" in p
    assert "Examples:\n\nCommand: Bring the cup from the kitchen table to the sofa.\nProgram:\nmove::table@kitchen" in p
    assert p.endswith(f"{dp.RULES}\n\nCommand: {cmd}\nProgram:\n")
    assert [e["task"] for e in out["examples"]] == [cmd] and "Notes:" not in p
    loo = dp.build_prompt(v, cmd, places=PLACES, k=1, leave_one_out=True, live_path="")
    assert "Command: Open the box." in loo and loo.count("Bring the cup from the kitchen table") == 1   # the command only


def test_notes_from_the_manifest(tmp_path):
    d = dict(ARM, planner={"notes": ["  Put a held object down\n before you open anything. ", "Say what you do."]})
    p = dp.build_prompt(view(d), "Open the box.", places="", k=0)
    assert "Notes:\n- Put a held object down before you open anything.\n- Say what you do." in p
    assert "Places:" not in p and "Examples:" not in p


def test_env_knobs_and_old_names(tmp_path, monkeypatch):
    v = view(ARM, seed=_seed(tmp_path))
    cmd = "Put the apple on the desk."
    assert len(dp.build(v, cmd, places="", live_path="")["examples"]) == 3
    monkeypatch.setenv("ROBOT_AGENT_GUIDE_EXAMPLES", "1")             # the old name still works
    assert [e["task"] for e in dp.build(v, cmd, places="", live_path="")["examples"]] == [cmd]
    monkeypatch.setenv("ROBOT_AGENT_DIRECT_EXAMPLES", "2")            # the new one wins
    monkeypatch.setenv("ROBOT_AGENT_GUIDE_LOO", "1")
    got = [e["task"] for e in dp.build(v, cmd, places="", live_path="")["examples"]]
    assert len(got) == 2 and cmd not in got
    monkeypatch.setenv("ROBOT_AGENT_DIRECT_LOO", "0")
    assert cmd in [e["task"] for e in dp.build(v, cmd, places="", live_path="")["examples"]]
    monkeypatch.setenv("ROBOT_AGENT_DIRECT_EXAMPLES", "x")
    assert dp.example_count() == dp.DEFAULT_K


def test_places_from_the_live_env(monkeypatch):
    import robot_agent.skill_configs as sc
    monkeypatch.setattr(sc, "ENV", {"desk@study": {"aliases": ["table"]}, "home": {}}, raising=False)
    p = dp.build_prompt(view(BASE), "Go home.", k=0)
    assert "Places:\n- desk@study (also: table)\n- home" in p


def test_template_names_no_robot():
    import inspect
    src = inspect.getsource(dp)
    assert not any(w in src.lower() for w in ("kcare", "kaair", "grasp_succeed", "mobile_pose"))


def test_live_memory_only_verified(tmp_path):
    live = tmp_path / "live.jsonl"
    live.write_text("\n".join(json.dumps({"task": t, "plan_text": '{"steps": [{"action": "MoveTo", "object": "a"}]}',
                                          **({"verified": v} if v is not None else {})})
                              for t, v in (("go to a ok", True), ("go to a bad", False), ("go to a old", None))))
    tasks = {e["task"] for e in ex.read_pool(None, str(live))}
    assert tasks == {"go to a ok"}
    assert len(ex.read_pool(None, str(live), verified_only=False)) == 3


def test_grace_planner_gets_the_manifest_actions(monkeypatch):
    """ClosedLoop._make_planner('grace') -> GraceBackend -> SAGEPlanner(actions, robot_description)."""
    import pytest
    pytest.importorskip("embodiplan")
    from robot_agent.core.planning.loop import ClosedLoop
    lp = object.__new__(ClosedLoop)
    lp.cfg = {"planner": "grace", "host": "http://127.0.0.1:1", "model": "m", "live_path": ""}
    lp._llm_cfg, lp.robot_pkg = {}, "pkg"
    lp.robot = view(BASE)
    sage = lp._make_planner()._p
    expand = sage.system_prompts["expand"]
    assert "  Say <text>: say a sentence to the person" in expand and "Pick" not in expand
    assert "cart: a robot that has no arm" in sage.system_prompts["decompose"]
    monkeypatch.setenv(rp.ENV_SWITCH, "0")
    assert "Pick" in lp._make_planner()._p.system_prompts["expand"]       # switched off: as before
