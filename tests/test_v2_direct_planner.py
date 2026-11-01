"""v2 direct: llm_direct and the open-loop direct path plan with the prompt built from the robot
(core/planning/direct_prompt.py), the manifest's planner.notes, GET /planner/prompt, which planners
use the prompt, and no Planner Guide left."""

import asyncio
import importlib
import json
import types

import pytest

from fakes import FakeLLM, FakeRobot, FakeSkillRegistry

from robot_agent.contract import Manifest, ManifestError
from robot_agent.core.planning import direct_prompt as dp
from robot_agent.core.planning.robot import RobotView

ARM = {"robot": "armbot", "capabilities": {"base": True, "arms": 1, "hands": ["right"]},
       "planner": {"notes": ["Say where you put things."]},
       "skills": [{"name": "move", "action": "MoveTo"}, {"name": "find", "action": "Find"},
                  {"name": "pick", "action": "Pick"}, {"name": "place", "action": "Place"}]}
DS = "desk@study room"


def view(d=ARM):
    return RobotView(types.SimpleNamespace(manifest=Manifest.from_dict(d), seed_memory=None))


@pytest.fixture
def robot_view(monkeypatch):
    v = view()
    monkeypatch.setattr(RobotView, "load", classmethod(lambda cls, pkg: v))
    monkeypatch.setattr(dp, "live_memory_path", lambda: None)
    return v


def test_llm_direct_sends_the_built_prompt(monkeypatch, robot_view):
    import robot_agent.connect.llm as llm_pkg
    from robot_agent.core.planning.backends.llm_direct import LlmDirectBackend
    llm = FakeLLM(default=f"move::{DS}\nfind::apple\npick::apple && place::{DS}")
    monkeypatch.setattr(llm_pkg, "init_llm_client", lambda cfg=None: llm)
    b = LlmDirectBackend(llm_cfg={"name": "fake"}, robot_pkg="pkg")
    steps, meta = b.generate_plan("Bring the apple.", "", [])
    prompt, fmt = llm.calls[0]
    assert fmt is None and prompt == dp.build_prompt(robot_view, "Bring the apple.")
    assert "- pick::<object> (Pick)" in prompt and "Notes:\n- Say where you put things." in prompt
    assert [s["action"] for s in steps] == ["move", "find", "pick", "place"]
    assert steps[2]["_parallel_group"] == steps[3]["_parallel_group"] == 0 and meta["raw"].startswith("move::")
    assert LlmDirectBackend.uses_direct_prompt is True
    b.replan("Bring the apple.", [], {"action": "pick", "object": "apple"}, "slipped", "", [])
    assert "The previous attempt failed at step 'pick apple': slipped" in llm.calls[1][0]


def test_open_loop_direct_path_uses_the_same_prompt(monkeypatch, robot_view, env, agent_state):
    """UnifiedAgent.run without a planner: one LLM call with the built prompt, then the program
    runs step by step."""
    import robot_agent.connect.llm as llm_pkg
    from robot_agent.core.unified_agent import UnifiedAgent
    llm = FakeLLM(default=f"move::{DS}\nfind::apple\npick::apple")
    monkeypatch.setattr(llm_pkg, "init_llm_client", lambda cfg=None: llm)
    monkeypatch.delenv("ROBOT_AGENT_CLOSED_LOOP", raising=False)
    robot = FakeRobot(env, {"apple": DS})
    ua = UnifiedAgent(skill_registry=FakeSkillRegistry(robot), device_manager=types.SimpleNamespace(_ros_node=None))
    ua.configure_llm({"name": "fake"})

    async def go():
        return [ev async for ev in ua.run(prompt="Pick up the apple on the desk.")]
    evs = asyncio.run(go())
    kinds = [e["event"] for e in evs]
    assert len(llm.calls) == 1 and llm.calls[0][0] == dp.build_prompt(robot_view, "Pick up the apple on the desk.")
    assert next(e for e in evs if e["event"] == "plan_raw")["plan"] == f"move::{DS}\nfind::apple\npick::apple"
    assert kinds.count("step_done") == 3 and robot.holding == "apple"
    assert evs[-1]["event"] == "done" and evs[-1]["success"] is True


def test_open_loop_no_program(monkeypatch, robot_view, env, agent_state):
    import robot_agent.connect.llm as llm_pkg
    from robot_agent.core.unified_agent import UnifiedAgent
    monkeypatch.setattr(llm_pkg, "init_llm_client", lambda cfg=None: FakeLLM(default="I cannot."))
    monkeypatch.delenv("ROBOT_AGENT_CLOSED_LOOP", raising=False)
    ua = UnifiedAgent(skill_registry=FakeSkillRegistry(FakeRobot(env, {})),
                      device_manager=types.SimpleNamespace(_ros_node=None))

    async def go():
        return [ev async for ev in ua.run(prompt="Dance.")]
    evs = asyncio.run(go())
    assert any(e["event"] == "error" and "no valid task plan" in e["msg"] for e in evs)


@pytest.mark.parametrize("planner,uses", [("direct", True), ("llm_direct", True), ("grace", False),
                                          ("sage", False), ("no_such_mod:X", False)])
def test_which_planners_use_the_prompt(planner, uses, monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_SAGE_GENERATOR", raising=False)
    assert dp.uses_direct_prompt(planner) is uses


def test_sage_with_llm_direct_generator_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ROBOT_AGENT_SAGE_GENERATOR", "llm_direct")
    assert dp.uses_direct_prompt("sage")
    monkeypatch.delenv("ROBOT_AGENT_CLOSED_LOOP", raising=False)
    assert dp.default_planner() == "direct" and dp.uses_direct_prompt(None)
    monkeypatch.setenv("ROBOT_AGENT_CLOSED_LOOP", "1")
    monkeypatch.setenv("ROBOT_AGENT_PLANNER", "sage")
    monkeypatch.delenv("ROBOT_AGENT_SAGE_GENERATOR")
    assert dp.default_planner() == "sage" and not dp.uses_direct_prompt(None)
    (tmp_path / "dplug.py").write_text("class P:\n    uses_direct_prompt = True\n"
                                       "    def generate_plan(self, *a): return [], {}\n"
                                       "    def replan(self, *a): return [], {}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    assert dp.uses_direct_prompt("dplug:P")


def test_prompt_endpoint(monkeypatch, robot_view):
    pytest.importorskip("fastapi")
    from robot_agent.api import planner as api
    monkeypatch.setattr(api, "current", lambda: types.SimpleNamespace(robot_pkg="pkg"))
    monkeypatch.delenv("ROBOT_AGENT_SAGE_GENERATOR", raising=False)
    out = api.planner_prompt(command="Bring the apple.", planner="sage", k=None, leave_one_out=None)
    assert set(out) == {"prompt", "examples", "planner", "uses_prompt"}
    assert out["prompt"] == dp.build_prompt(robot_view, "Bring the apple.")
    assert out["planner"] == "sage" and out["uses_prompt"] is False and out["examples"] == []
    assert api.planner_prompt(command="x", planner="direct", k=0, leave_one_out=None)["uses_prompt"] is True


def test_prompt_endpoint_in_the_app():
    pytest.importorskip("fastapi")
    from robot_agent.api.planner import router
    paths = {r.path for r in router.routes}
    assert paths == {"/planner/prompt"}
    import robot_agent.app_factory as af
    src = open(af.__file__).read()
    assert "api.planner import router" in src and "guides" not in src


@pytest.mark.parametrize("notes,err", [("one string", "must be a list"), ([""], "non-empty"),
                                       (["x" * 301], "longer than 300"), ([3], "non-empty")])
def test_manifest_notes_validated(notes, err):
    with pytest.raises(ManifestError, match=err):
        Manifest.from_dict(dict(ARM, planner={"notes": notes}))


def test_manifest_notes_accepted_strict(tmp_path):
    from robot_agent.contract.check_manifest import PLANNER_KEYS
    assert "notes" in PLANNER_KEYS
    m = Manifest.from_dict(ARM)
    assert m.planner["notes"] == ["Say where you put things."] and dp.robot_notes(view()) == m.planner["notes"]


def test_no_planner_guide_left():
    for mod in ("robot_agent.core.guide_manager", "robot_agent.api.guides"):
        with pytest.raises(ImportError):
            importlib.import_module(mod)
    import robot_agent.safe_parse as sp
    assert not hasattr(sp, "parse_plan_answer")


@pytest.fixture
def data_dir(tmp_path, monkeypatch, agent_state):
    from robot_agent.interact.run_manager import reset_manager
    root = tmp_path / "data"
    monkeypatch.setenv("ROBOT_AGENT_DATA_DIR", str(root))
    monkeypatch.setenv("ROBOT_AGENT_HOST", "test")
    reset_manager()
    yield root
    reset_manager()


def test_record_and_replay_of_the_open_loop_path(monkeypatch, robot_view, env, data_dir):
    """The direct prompt's LLM call is recorded as `chat` and a replay store serves it back."""
    import robot_agent.connect.llm as llm_pkg
    from robot_agent.core import run_control
    from robot_agent.core.unified_agent import UnifiedAgent, _serialize_result
    from robot_agent.observe import record as R
    from robot_agent.observe import replay as RP
    from robot_agent.observe.llm import RecordingLLM
    llm = FakeLLM(default=f"move::{DS}\nfind::apple")
    monkeypatch.setattr(llm_pkg, "init_llm_client", lambda cfg=None: RecordingLLM(llm))
    monkeypatch.delenv("ROBOT_AGENT_CLOSED_LOOP", raising=False)
    robot = FakeRobot(env, {"apple": DS})
    ua = UnifiedAgent(skill_registry=R.RecordingSkills(FakeSkillRegistry(robot)),
                      device_manager=types.SimpleNamespace(_ros_node=None))
    mgr = run_control.manager()
    run = mgr.start(goal="Find the apple.", user_id="alice").run

    async def go():
        async for ev in ua.run(prompt="Find the apple."):
            run.publish(_serialize_result(ev))
    asyncio.run(go())
    mgr.finish(run.run_id, ok=True, outcome="success")
    rec = RP.load(run.run_id, data_dir=data_dir)
    calls = [r for r in rec.rows if r.get("kind") == "llm"]
    assert len(calls) == 1 and calls[0]["request"]["method"] == "chat"
    assert calls[0]["request"]["prompt"] == dp.build_prompt(robot_view, "Find the apple.")
    # the recorded answer serves the same prompt again (what a replay of an llm_direct plan does)
    store = RP.LLMStore(rec.rows)
    assert dp.plan_answer(RP.ReplayLLM(store), robot_view, "Find the apple.") == f"move::{DS}\nfind::apple"
    with pytest.raises(Exception, match="prompt differs"):
        dp.plan_answer(RP.ReplayLLM(RP.LLMStore(rec.rows)), robot_view, "Find the pear.")
