"""observe.paths (one data root, old layout by default, read fallback) and the
LLM capture points (LLMobj, embodiplan.LLMBackend) without any LLM server."""

import json
import types

import pytest

from robot_agent.observe import llm as OL
from robot_agent.observe import paths as P
from robot_agent.observe import record as R
from robot_agent.observe.replay import LLMStore


# ─────────────────────────── paths ───────────────────────────
def test_default_layout_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv(P.ENV_VAR, raising=False)
    c = tmp_path / "common"
    assert P.data_dir("task_runs", c, "h1") == c / "task_runs" / "h1"
    assert P.data_dir("grace_memory", c, "h1") == c / "grace_memory.h1.jsonl"
    assert P.data_dir("object_memory", c, "h1") == c / "object_memory.h1.json"
    assert P.data_dir("world_state", c, "h1") == c / "world_state.h1.json"
    assert P.data_dir("run_records", c, "h1") == c / "run_records" / "h1"
    assert P.data_dir("metrics", c, "h1") == c / "run_records" / "h1" / "metrics.jsonl"
    with pytest.raises(ValueError):
        P.data_dir("nope", c, "h1")


def test_root_moves_everything_and_reads_the_old_files(tmp_path, monkeypatch):
    c, root = tmp_path / "common", tmp_path / "nas"
    c.mkdir()
    (c / "object_memory.h1.json").write_text('{"objects": {"cup": "desk"}}')
    (c / "task_runs" / "h1").mkdir(parents=True)
    (c / "task_runs" / "h1" / "old.jsonl").write_text("{}\n")
    monkeypatch.setenv(P.ENV_VAR, str(root))
    assert P.data_dir("task_runs", c, "h1") == root / "task_runs" / "h1"
    # an in-place file is seeded from its old location the first time
    om = P.data_dir("object_memory", c, "h1")
    assert om == root / "object_memory.h1.json" and json.loads(om.read_text())["objects"]["cup"] == "desk"
    om.write_text('{"objects": {}}')
    assert json.loads(P.data_dir("object_memory", c, "h1").read_text()) == {"objects": {}}   # not re-seeded
    # a missing file stays missing (nothing to fall back to)
    assert not P.data_dir("world_state", c, "h1").exists()
    # dir kinds: read_path / search_dirs fall back to the old directory
    assert P.read_path("task_runs", c, "h1") == c / "task_runs" / "h1"
    assert P.search_dirs("task_runs", c, "h1") == [root / "task_runs" / "h1", c / "task_runs" / "h1"]


def test_world_state_file_follows_the_root(tmp_path, monkeypatch):
    from robot_agent.state import AgentState
    st = types.SimpleNamespace(common_dir=tmp_path)
    monkeypatch.setenv("ROBOT_AGENT_HOST", "h1")
    monkeypatch.delenv(P.ENV_VAR, raising=False)
    assert AgentState._world_file.fget(st) == tmp_path / "world_state.h1.json"
    monkeypatch.setenv(P.ENV_VAR, str(tmp_path / "data"))
    assert AgentState._world_file.fget(st) == tmp_path / "data" / "world_state.h1.json"


def test_loop_paths_come_from_data_dir(tmp_path, monkeypatch, agent_state):
    import robot_agent.core.planning.loop as LP
    monkeypatch.setenv(P.ENV_VAR, str(tmp_path / "data"))
    loop = LP.ClosedLoop.__new__(LP.ClosedLoop)
    loop._llm_cfg = {}
    cfg = loop._resolve_cfg({}, agent_state)
    assert cfg["log_dir"] == tmp_path / "data" / "task_runs" / "test"
    assert cfg["live_path"] == str(tmp_path / "data" / "grace_memory.test.jsonl")
    monkeypatch.delenv(P.ENV_VAR)
    cfg = loop._resolve_cfg({}, agent_state)
    assert cfg["log_dir"] == tmp_path / "task_runs" / "test"            # as before


# ─────────────────────────── LLM capture ───────────────────────────
def _rec(tmp_path):
    return R.RunRecord(R.RunHeader(run_id="r1"), tmp_path / "r1" / "record.jsonl")


def test_llmobj_calls_are_recorded_once(tmp_path, monkeypatch):
    from robot_agent.connect.llm import llm_object as LO
    monkeypatch.setattr(LO, "history_dir", lambda: str(tmp_path / "hist"))
    monkeypatch.setattr(LO, "cache_dir", lambda: str(tmp_path / "cache"))
    (tmp_path / "hist").mkdir()
    (tmp_path / "cache").mkdir()

    class Echo(LO.LLMobj):
        def init(self, **kw):
            self.temperature = 0.1

        def make_msg(self, prompt="", **kw):
            return {"role": "user", "content": prompt}

        def send_msgs(self, msgs, **kw):
            self.current_model = "echo-1"
            return msgs[-1]["content"].upper()

    cli = Echo()
    rec = _rec(tmp_path)
    with R.bound(rec):
        assert cli.chat(prompt="hello", format={"type": "object"}) == "HELLO"
        assert cli.chat_guide("bring cup", "Plan: COMMAND_HERE") == "PLAN: BRING CUP"
    assert cli.chat(prompt="not recorded") == "NOT RECORDED"         # no run, no row
    rows = [r for r in R.read_rows(rec.path) if r["kind"] == "llm"]
    assert len(rows) == 2                                            # chat_guide -> chat counted once
    a, b = rows
    assert a["request"] == {"method": "chat", "prompt": "hello", "format": {"type": "object"}}
    assert a["response"] == "HELLO" and a["model"] == "echo-1" and a["backend"] == "Echo"
    assert a["temperature"] == 0.1 and a["messages"] == [{"role": "user", "content": "hello"}]
    assert b["request"]["method"] == "chat_guide" and b["request"]["guide"] == "Plan: COMMAND_HERE"


def test_embodiplan_backend_is_recorded_and_served(tmp_path):
    embodiplan = pytest.importorskip("embodiplan")
    from robot_agent.core.planning.backends.pyplanner_grace import _observe_llm
    _observe_llm(embodiplan)
    _observe_llm(embodiplan)                                         # idempotent
    assert getattr(embodiplan.LLMBackend.chat, "_robot_agent_observed", False)

    class Offline(embodiplan.LLMBackend):
        def _chat_ollama(self, messages, temperature):
            return ("PLAN " + messages[-1]["content"], 11, 3)

    be = Offline(provider="ollama", model="qwen-test", host="localhost:1", temperature=0.2, seed=7)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "tidy"}]
    rec = _rec(tmp_path)
    with R.bound(rec):
        assert be.chat(msgs) == ("PLAN tidy", 11, 3)
    rows = [r for r in R.read_rows(rec.path) if r["kind"] == "llm"]
    assert len(rows) == 1 and rows[0]["source"] == "embodiplan"
    assert rows[0]["model"] == "qwen-test" and rows[0]["backend"] == "ollama"
    assert rows[0]["seed"] == 7 and rows[0]["temperature"] == 0.2
    assert rows[0]["request"]["messages"] == msgs

    # replay: the same messages are answered from the record, others are a mismatch
    store = LLMStore(R.read_rows(rec.path))
    with OL.serving(store):
        assert be.chat(msgs) == ("PLAN tidy", 11, 3)
        with pytest.raises(OL.ReplayMismatch, match="the prompt differs"):
            be.chat([{"role": "user", "content": "something else"}])


def test_shrink_keeps_prompts_and_stubs_blobs():
    s = R.shrink({"say_audio": "x" * 10, "img": "b" * 5000, "ok": "fine"})
    assert "say_audio" not in s and s["ok"] == "fine" and s["img"]["_omitted"] == "str"
    long_prompt = "p" * 20000
    assert R.shrink({"prompt": long_prompt}, R._MAX_LLM_STR)["prompt"] == long_prompt
