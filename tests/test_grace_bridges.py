"""The bridges for the old name of the SAGE planner (GRACE): this file keeps the word on purpose.

B1 the registry name ``grace`` builds the ``sage`` backend (one DeprecationWarning); B2 a robot package that only
has ``configs/grace_namemap.py`` still loads; B3 an old ``grace_memory`` store still loads as ``sage_memory``;
B4 the request-level ``grace`` runs the plain loop like ``sage_plain`` while ``sage`` still runs the AssistLoop.
Made-up robots, skills and places only."""

import asyncio
import sys
import types
import warnings

import pytest

from fakes import FakeRobot, FakeSkillRegistry

from robot_agent.core.planning import planner_names as PN
from robot_agent.core.planning import direct_prompt as dp
from robot_agent.core.planning import registry
from robot_agent.observe import paths as P


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    monkeypatch.setattr(PN, "_warned", set())


# ── B1: the registry name ─────────────────────────────────────────────────────────────────────────────────────────
def test_b1_registry_alias_grace_resolves_to_the_sage_backend_and_warns_once():
    with warnings.catch_warnings(record=True) as got:
        warnings.simplefilter("always")
        old = registry.resolve_planner("Grace")
        registry.resolve_planner("grace")
    assert old == registry.resolve_planner("sage") and old[1] is True
    assert old[0].__name__ == "SageBackend" and registry.is_planner_name("GRACE")
    assert [w.category for w in got] == [DeprecationWarning]                     # once per process, any case
    with pytest.raises(ValueError, match="available planners: sage, llm_direct"):
        registry.resolve_planner("no_such_planner")                              # the old name is not advertised


def test_b1_a_closed_loop_config_naming_grace_builds_the_sage_backend(monkeypatch):
    import robot_agent.core.planning.loop as LP
    seen = []
    monkeypatch.setattr(LP, "get_planner", lambda name, **kw: seen.append((name, sorted(kw))) or object())
    lp = object.__new__(LP.ClosedLoop)
    lp._llm_cfg, lp.robot_pkg = {}, "toybot"
    lp.robot = types.SimpleNamespace(capabilities=lambda: {}, seed_memory=lambda: None)
    monkeypatch.setattr(LP.robot_prompt, "sage_prompt_kwargs", lambda robot: {})
    for name in ("grace", "sage"):
        lp.cfg = {"planner": name}
        lp._make_planner()
    assert seen[0] == seen[1] and seen[0][0] == "sage" and "safe_refine" in seen[0][1]
    assert PN.record_name("grace") == PN.record_name("sage") == "sage_plain"


# ── B2: the namemap module ────────────────────────────────────────────────────────────────────────────────────────
def test_b2_a_package_with_only_grace_namemap_still_loads(tmp_path, monkeypatch):
    from robot_agent.contract import NamemapAdapter, load_adapter
    from robot_agent.contract.legacy_adapter import namemap_module_name
    for rel, text in {"oldbot/__init__.py": "", "oldbot/configs/__init__.py": "",
                      "oldbot/configs/grace_namemap.py": 'SKILL_MAP = {"MoveTo": "roll"}\nSUPPORTED_ACTIONS = {"MoveTo"}\n',
                      "newbot/__init__.py": "", "newbot/configs/__init__.py": "",
                      "newbot/configs/grace_namemap.py": 'SKILL_MAP = {"MoveTo": "old"}\n',
                      "newbot/configs/sage_namemap.py": 'SKILL_MAP = {"MoveTo": "hop"}\n'}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        a = load_adapter("oldbot")
        assert isinstance(a, NamemapAdapter) and a.bind({"action": "MoveTo", "object": "Shed"})[0] == "roll"
        assert namemap_module_name("newbot") == "newbot.configs.sage_namemap"      # the new name wins
        assert namemap_module_name("nobot_xyz") is None
    finally:
        for k in [k for k in sys.modules if k.split(".")[0] in ("oldbot", "newbot")]:
            del sys.modules[k]


# ── B3: the stored memory ─────────────────────────────────────────────────────────────────────────────────────────
def test_b3_an_old_grace_memory_store_still_loads(tmp_path, monkeypatch):
    c = tmp_path / "common"
    c.mkdir()
    (c / "grace_memory.h1.jsonl").write_text('{"task": "water the fern"}\n')
    monkeypatch.delenv(P.ENV_VAR, raising=False)
    p = P.data_dir("sage_memory", c, "h1")                                       # no data root: in common_dir
    assert p == c / "sage_memory.h1.jsonl" and p.read_text() == '{"task": "water the fern"}\n'
    monkeypatch.setenv(P.ENV_VAR, str(tmp_path / "root"))                        # a data root: copied there too
    q = P.data_dir("sage_memory", c, "h2")
    assert q == tmp_path / "root" / "sage_memory.h2.jsonl" and not q.exists()   # nothing to copy: nothing made
    (c / "grace_memory.h2.jsonl").write_text('{"task": "feed the cat"}\n')
    assert P.data_dir("sage_memory", c, "h2").read_text() == '{"task": "feed the cat"}\n'
    p.write_text("new\n")
    assert P.data_dir("sage_memory", c, "h1").read_text() == "new\n"            # an existing new store is kept


# ── B4: the request-level name ────────────────────────────────────────────────────────────────────────────────────
def test_b4_request_grace_is_sage_plain_and_sage_stays_the_assist_loop(monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_SAGE_GENERATOR", raising=False)
    assert PN.loop_choice("grace") == PN.loop_choice("sage_plain") == ("sage", False)
    assert PN.loop_choice("sage") == ("sage", True) and PN.loop_choice("direct") == ("llm_direct", False)
    assert PN.loop_choice(None, "sage") == (None, True) and PN.loop_choice("no_such_thing") == (None, False)
    assert dp.uses_direct_prompt("grace") is dp.uses_direct_prompt("sage_plain") is False


def test_b4_through_the_agent_grace_runs_the_plain_loop_like_sage_plain(monkeypatch, env, agent_state):
    import robot_agent.core.planning.assist_loop as AL
    import robot_agent.core.planning.loop as LP
    from robot_agent.core.unified_agent import UnifiedAgent
    made = []

    def spy(kind):
        class Loop:
            def __init__(self, agent, emit=None, **kw):
                made.append((kind, kw.get("planner")))

            def run_blocking(self, task, lang, plan_only=False):
                yield {"event": "done", "success": True}
        return Loop
    monkeypatch.setattr(LP, "ClosedLoop", spy("plain"))
    monkeypatch.setattr(AL, "AssistLoop", spy("assist"))
    monkeypatch.delenv("ROBOT_AGENT_PLANNER", raising=False)
    ua = UnifiedAgent(skill_registry=FakeSkillRegistry(FakeRobot(env, {})),
                      device_manager=types.SimpleNamespace(_ros_node=None))
    for name in ("grace", "sage_plain", "sage"):
        asyncio.run(_drain(ua.run(prompt="Tidy the shed.", lang="en", planner=name)))
    assert made == [("plain", "sage"), ("plain", "sage"), ("assist", None)]


async def _drain(gen):
    return [ev async for ev in gen]
