"""v2 guide: the robot description / action list generator (core/planning/robot_prompt.py) and
the guide placeholders {ROBOT} / {ACTIONS} / {EXAMPLES} (core/guide_manager.render_guide)."""

import json
import types

from robot_agent.contract import Manifest
from robot_agent.core import guide_manager as gm
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


def test_legacy_guide_unchanged():
    g = "Command: give me X\nProgram:\nmove::shelf\n{prompt} {robot} LOCATIONS_HERE\nCommand: COMMAND_HERE."
    assert gm.render_guide(g, view=view(ARM), command="anything") is g
    assert gm.placeholders_in(g) == []


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


def test_examples_placeholder_retrieves_similar(tmp_path):
    v = view(ARM, seed=_seed(tmp_path))
    g = "{ROBOT}\n{ACTIONS}\n{EXAMPLES}\nCommand: COMMAND_HERE."
    out = gm.render_guide(g, view=v, command="Bring the cup from the kitchen table to the sofa.", k=1,
                          live_path="")
    assert "Command: Bring the cup from the kitchen table to the sofa.\nProgram:\nmove::table@kitchen" in out
    assert "- open_box::<container> (Open): open the box lid" in out and "armbot:" in out
    loo = gm.render_guide(g, view=v, command="Bring the cup from the kitchen table to the sofa.", k=1,
                          leave_one_out=True, live_path="")
    assert "Bring the cup from the kitchen table" not in loo and "Command: Open the box." in loo


def test_live_memory_only_verified(tmp_path):
    live = tmp_path / "live.jsonl"
    live.write_text("\n".join(json.dumps({"task": t, "plan_text": '{"steps": [{"action": "MoveTo", "object": "a"}]}',
                                          **({"verified": v} if v is not None else {})})
                              for t, v in (("go to a ok", True), ("go to a bad", False), ("go to a old", None))))
    tasks = {e["task"] for e in ex.read_pool(None, str(live))}
    assert tasks == {"go to a ok"}
    assert len(ex.read_pool(None, str(live), verified_only=False)) == 3


def test_resolve_guide_uses_the_command(monkeypatch, tmp_path):
    v = view(ARM, seed=_seed(tmp_path))
    monkeypatch.setattr(gm, "_resolve_guide", lambda pkg: ("{EXAMPLES}", None))
    monkeypatch.setattr(RobotView, "load", classmethod(lambda cls, pkg: v))
    monkeypatch.setattr(gm, "_live_memory_path", lambda: None)
    monkeypatch.setenv("ROBOT_AGENT_GUIDE_EXAMPLES", "1")
    with gm.guide_command("Open the box."):
        guide, fmt = gm.resolve_guide("pkg")
    assert guide.startswith("Command: Open the box.\nProgram:\nmove::box\nopen_box::box") and fmt is None


def test_preview_fills_command(monkeypatch, tmp_path):
    v = view(ARM, seed=_seed(tmp_path))
    monkeypatch.setattr(RobotView, "load", classmethod(lambda cls, pkg: v))
    monkeypatch.setattr(gm, "_live_memory_path", lambda: None)
    out = gm.preview_guide("{EXAMPLES}\nCommand: COMMAND_HERE.", "pkg", "Put the apple on the desk.", k=2)
    assert out["placeholders"] == ["{EXAMPLES}"] and not out["legacy"]
    assert out["examples"][0]["task"] == "Put the apple on the desk." and len(out["examples"]) == 2
    assert out["rendered"].endswith("Command: Put the apple on the desk..")


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
