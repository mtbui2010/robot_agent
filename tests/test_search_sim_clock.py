"""The search memory and belief read the host's clock (AssistLoop._now), not the wall clock, so the ranking
prompt ("seen N s ago") does not depend on how fast the machine is."""
import time
import types

from robot_agent.core.planning.assist_loop import AssistLoop, Memory
from robot_agent.core.planning.search import SearchConfig


def _loop(clock):
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg = {"search": SearchConfig(rank="llm"), "live_path": None}
    lp.clock = clock
    return lp


def test_memory_stamps_with_the_host_clock(tmp_path):
    m = Memory(tmp_path / "m.json", clock=lambda: 42.0)
    m.see("book", "desk", "seen")
    assert m.data["objects"]["book"]["ts"] == 42.0
    m2 = Memory(tmp_path / "m2.json"); m2.see("cup", "desk", "seen")
    assert m2.data["objects"]["cup"]["ts"] > 1e9             # no clock: wall time


def test_remembered_age_uses_host_clock(tmp_path):
    lp = _loop(lambda: 100.0)
    m = Memory(tmp_path / "m.json", clock=lp._now)
    m.see("book", "desk", "seen")
    assert lp._remembered(m, "book") == "desk"                # age 0 on the host clock: trusted
    lp.clock = lambda: 100.0 + 10 * 6 * 3600
    assert lp._remembered(m, "book") is None                  # ten half-lives later: stale


def test_search_belief_ranking_prompt_is_clock_independent(tmp_path):
    prompts = []
    for lag in (0.0, 0.05):
        lp = _loop(lambda: 500.0)
        mem = Memory(tmp_path / f"m{lag}.json", clock=lp._now)
        mem.data["objects"]["book"] = {"place": "desk", "ts": 440.0, "how": "house memory"}
        places = types.SimpleNamespace(keys=["desk", "shelf"], env={"desk": {}, "shelf": {}}, openable=lambda k: False)
        r = types.SimpleNamespace(
            robot=types.SimpleNamespace(adapter=None, plan_actions={"Find"}), places=places, memory=mem,
            goals=[{"object": "book", "type": "go"}], pre_prefer=None, search=None, steered=[])
        list(lp._search_start(r))
        time.sleep(lag)                                       # a slow machine
        prompts.append(r.search.rank_prompt("book"))
    assert prompts[0] == prompts[1]
    assert "60 s ago" in prompts[0]
