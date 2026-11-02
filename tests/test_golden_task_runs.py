"""Golden tests from recorded runs (tests/data/task_runs, a subset of kcare's 69
``configs/common/task_runs``). They replay what can be replayed without a robot:

* the recorded LLM answer through ``LlmDirectBackend`` (plan string + PlanSteps; the prompt is
  built from the robot now, the answers are what the old guides got),
* ``ActionMapper.screen`` against the recorded warnings,
* a symbolic replay of the recorded plan (``symbolic_check`` + ``to_skill`` +
  ``apply_effect`` from the recorded start world) against tests/data/golden/.

Regenerate the golden files after a deliberate behaviour change with
``UPDATE_GOLDEN=1 make test`` and review the diff.
"""

import json
import os
from pathlib import Path

import pytest

from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.mapper import ActionMapper, Unmappable
from robot_agent.core.planning.verify import symbolic_check

DATA = Path(__file__).resolve().parent / "data"
RUNS = sorted((DATA / "task_runs").glob("*.jsonl"))
GOLDEN = DATA / "golden"


def load(path):
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    by = {}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)
    return rows, by


def test_subset_is_small_and_varied():
    assert 5 <= len(RUNS) <= 10
    planners = {load(p)[1]["snapshot"][-1]["record"]["planner"] for p in RUNS}
    assert planners == {"grace", "llm_direct"}


@pytest.fixture
def mapper():
    from fakebot.configs import grace_namemap
    return ActionMapper(grace_namemap)


def _plans(path):
    _, by = load(path)
    return by.get("plan", [])


@pytest.mark.parametrize("path", RUNS, ids=lambda p: p.stem)
def test_screen_matches_recorded_warnings(path, mapper):
    for plan in _plans(path):
        assert mapper.screen(plan["steps"]) == plan["warnings"]


LLM_DIRECT = [p for p in RUNS if any((pl.get("plan_meta") or {}).get("raw") for pl in _plans(p))]


@pytest.mark.parametrize("path", LLM_DIRECT, ids=lambda p: p.stem)
def test_llm_direct_replays_recorded_answer(path, monkeypatch):
    """The recorded answer fed back as the LLM's text gives the recorded plan and steps.

    Covers the parse that used to be ``eval(str(plan_raw))``: a dict-shaped answer stays
    the answer's text (never evaluated); a plain plan stays as written. The prompt is the
    one the direct planner builds from the robot (core/planning/direct_prompt.py).
    """
    import robot_agent.connect.llm as llm_pkg
    from fakes import FakeLLM
    from robot_agent.core.planning.backends.llm_direct import LlmDirectBackend

    plan = next(pl for pl in _plans(path) if (pl.get("plan_meta") or {}).get("raw"))
    raw = plan["plan_meta"]["raw"]
    llm = FakeLLM(default=raw)
    monkeypatch.setattr(llm_pkg, "init_llm_client", lambda cfg=None: llm)
    backend = LlmDirectBackend(llm_cfg={}, robot_pkg="fakebot", live_path="")
    got_steps, meta = backend.generate_plan("task", "", [])
    assert meta == {"raw": raw}
    # the recorded steps (action = the skill name) in the richer shape of 2026-11-02: the abstract
    # action of the skill, the call itself (skill + params) and the recorded object / reason
    assert got_steps == [_richer(st) for st in plan["steps"]]
    assert [s["skill"] for s in got_steps] == [st["action"] for st in plan["steps"]]
    assert [(s["object"], s["reason"]) for s in got_steps] == [(st["object"], st["reason"]) for st in plan["steps"]]
    assert len(llm.calls) == 1 and llm.calls[0][0].endswith("Command: task\nProgram:\n")


# fakebot's manifest skill -> its plan action (the reverse of its skill map)
FAKEBOT_ACTIONS = {"move": "MoveTo", "find": "Find", "pick": "Pick", "place": "Place",
                   "open_drawer": "Open", "close_drawer": "Close"}


def _richer(st):
    """A recorded llm_direct step as LlmDirectBackend parses it now (docs/direct_prompt.md)."""
    action = FAKEBOT_ACTIONS.get(st["action"], "")
    if action == "Place" and "drawer" in st["object"]:     # fakebot opens drawers; its PutIn is `place` too
        action = "PutIn"
    return {"action": action, "object": st["object"], "reason": st["reason"],
            "skill": st["action"], "params": {"inputs": st["object"]}}


def _replay(path, mapper):
    rows, by = load(path)
    start = by["task_start"][0].get("world") or {}
    out = {"task": by["task_start"][0].get("task"), "plans": []}
    for plan in by.get("plan", []):
        w = WorldState()
        w.update_from_dict({k: v for k, v in start.items() if k in ("arrived", "found", "holding", "opened", "on")})
        trace = []
        for st in plan["steps"]:
            ok, why = symbolic_check(st, w)
            try:
                mapped = mapper.to_skill(st, w)
                call = None if mapped is None else f"{mapped[0]}::{mapped[1].get('inputs', '')}"
            except Unmappable as e:
                call = f"UNMAPPABLE: {e}"
            if ok:
                mapper.apply_effect(st, w)
            trace.append({"step": f"{st.get('action')} {st.get('object')}", "ok": ok, "why": why, "call": call})
        out["plans"].append({"trace": trace, "end": {k: v for k, v in w.to_dict().items()
                                                     if k in ("arrived", "found", "holding", "opened", "on")}})
    return out


@pytest.mark.parametrize("path", RUNS, ids=lambda p: p.stem)
def test_symbolic_replay_golden(path, mapper):
    got = _replay(path, mapper)
    gold = GOLDEN / f"{path.stem}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        gold.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
    elif not gold.exists():
        pytest.fail(f"missing golden file {gold.name}; run with UPDATE_GOLDEN=1")
    assert got == json.loads(gold.read_text())
