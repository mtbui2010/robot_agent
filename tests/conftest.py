"""Shared fixtures. Pure Python: no ROS, no FastAPI app, no LLM server, no network.

Fixtures
--------
env            the sim_house ENV dict (tests/data/env_sim_house.json)
places         ``assist_loop.Places`` over ``env``
agent_state    a :class:`fakes.FakeState` installed as ``current()`` (robot package ``fakebot``)
make_assist    factory: ``make_assist(robot, llm, planner, world=None, **overrides) -> AssistLoop``
               wired to a FakeRobot, a scripted LLM and a scripted planner

Test doubles (``from fakes import ...``): FakeRobot, FakeSkillRegistry, FakeLLM,
ScriptedPlanner, FakeState, steps, run_task, load_env.
"""

from __future__ import annotations

import socket
import sys
import types
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
for p in (str(ROOT), str(TESTS)):          # the worktree's robot_agent, then fakes / fakebot
    if p not in sys.path:
        sys.path.insert(0, p)

from fakes import FakeSkillRegistry, FakeState, load_env  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any TCP connection is a bug in a unit test (kcare :8001, Isaac :5577, an LLM server)."""
    def refuse(*a, **k):
        raise AssertionError(f"network access in a unit test: {a[:2]}")
    monkeypatch.setattr(socket, "create_connection", refuse)
    real_connect = socket.socket.connect

    def connect(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            refuse(addr)
        return real_connect(self, addr)
    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture
def env() -> dict:
    return load_env()


@pytest.fixture
def places(env):
    from robot_agent.core.planning.assist_loop import Places
    return Places(env)


@pytest.fixture
def agent_state(tmp_path, monkeypatch):
    import robot_agent.state as state_mod
    import robot_agent.core.planning.assist_loop as AL
    import robot_agent.core.planning.loop as LP

    st = FakeState(tmp_path)
    for m in (AL, LP, state_mod):
        monkeypatch.setattr(m, "current", lambda: st)
    for m in (AL, LP):
        monkeypatch.setattr(m, "host_id", lambda: "test")
    return st


@pytest.fixture
def make_assist(tmp_path, monkeypatch, env, agent_state):
    import robot_agent.skill_configs as sc
    import robot_agent.core.planning.assist_loop as AL
    from robot_agent.core.run_control import begin_run

    monkeypatch.setattr(sc, "ENV", env)
    monkeypatch.setattr(AL, "reconcile_world", lambda node, world, threshold=0.8: world)
    begin_run()                                   # no cancel left over from another test

    def make(robot, llm, planner, world=None, **overrides):
        if world:
            agent_state.world.update_from_dict(world)
        agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(robot),
                                      device_manager=types.SimpleNamespace(_ros_node=None),
                                      _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
        ov = dict(log_dir=str(tmp_path / "runs"), live_path=str(tmp_path / "live.jsonl"),
                  memory_path=str(tmp_path / "object_memory.json"), mute_skill_tts=False,
                  speak_backend=False, vlm_enabled=False,
                  input_pivot="none")      # the older tests exercise the loop without the language layer's IN side
        ov.update(overrides)
        loop = AL.AssistLoop(agent, emit=None, **ov)
        loop._llm_client = llm
        loop._make_planner = lambda: planner
        return loop

    return make
