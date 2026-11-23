"""The fact store of the belief (``WorldState.facts``, Step S26): empty by default and then invisible (to_dict has no
``facts`` key, so the ``world`` of every event of today's runs is unchanged), written only when not empty, accepted by
update_from_dict (``{"facts": {}}`` clears it), never in as_text() (the planner's view), copied with the world, and reset at the
start of a run (a fact of an earlier run never satisfies a precondition)."""

import json

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.core.pack import action_rules
from robot_agent.core.planning.base import WorldState

OLD_KEYS = ["arrived", "found", "holding", "opened", "on", "holding_since", "found_pose", "holding_pose", "provenance"]


def test_an_empty_fact_store_is_not_written_and_the_keys_are_the_old_ones():
    w = WorldState(arrived="desk", holding="cup", opened={"drawer"})
    assert list(w.to_dict()) == OLD_KEYS
    w.facts = {"inspected": set()}                      # a name with no fact left is not written either
    assert list(w.to_dict()) == OLD_KEYS


def test_facts_are_written_sorted_after_the_old_keys():
    w = WorldState(facts={"seated": {("pipe", "wall b"), ("pipe", "wall a")}, "inspected": {("bracket",)}})
    d = w.to_dict()
    assert list(d) == OLD_KEYS + ["facts"]
    assert d["facts"] == {"inspected": [["bracket"]], "seated": [["pipe", "wall a"], ["pipe", "wall b"]]}
    json.dumps(d)                                       # JSON-serialisable


def test_facts_are_not_in_the_planners_text():
    w = WorldState(arrived="desk")
    before = w.as_text()
    w.facts = {"inspected": {("bracket",)}}
    assert w.as_text() == before and "inspected" not in before


def test_update_from_dict_accepts_facts_and_an_empty_mapping_clears_them():
    w = WorldState()
    w.update_from_dict({"facts": {"inspected": [["bracket"], "bolt"], "seated": [["pipe", "wall a"]]}})
    assert w.facts == {"inspected": {("bracket",), ("bolt",)}, "seated": {("pipe", "wall a")}}
    w.update_from_dict({"holding": "cup"})              # an edit without the key leaves the facts as they are
    assert w.facts["inspected"] == {("bracket",), ("bolt",)}
    w.update_from_dict({"facts": {}})
    assert w.facts == {} and "facts" not in w.to_dict()
    w.update_from_dict({"facts": None})
    assert w.facts == {}


def test_a_written_world_reads_back_the_same_facts():
    w = WorldState(facts={"seated": {("pipe", "wall a")}})
    back = WorldState()
    back.update_from_dict(json.loads(json.dumps(w.to_dict())))
    assert back.facts == w.facts


def test_copy_has_its_own_facts():
    w = WorldState(facts={"inspected": {("bracket",)}})
    c = w.copy()
    c.facts["inspected"].add(("bolt",))
    c.facts["seated"] = {("pipe", "wall a")}
    assert w.facts == {"inspected": {("bracket",)}} and c != w
    assert WorldState().copy() == WorldState()


def test_clear_facts_empties_the_store():
    w = WorldState(facts={"inspected": {("bracket",)}})
    w.clear_facts()
    assert w.facts == {} and w == WorldState()


def test_a_run_starts_with_no_fact_and_puts_the_rules_back_at_its_end(make_assist, env, agent_state):
    """A fact left from before the run is gone from the first event on; the active rules are EMPTY again after the run."""
    dt, ds = "dining table@kitchen", "desk@study room"
    agent_state.world.facts = {"inspected": {("apple",)}}
    plan = steps(f"MoveTo {ds}", "Find apple", "Pick apple", f"MoveTo {dt}", f"Place {dt}")
    llm = FakeLLM({"status": "clear", "reason": "", "question": "", "goals": [
        {"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
    loop = make_assist(FakeRobot(env, {"apple": ds}), llm, ScriptedPlanner(plan))
    evs = list(loop.run_blocking("Bring the apple from the desk to the dining table."))
    assert evs[0]["event"] == "task_start" and "facts" not in evs[0]["world"]
    assert all("facts" not in (e.get("world") or {}) for e in evs)
    assert agent_state.world.facts == {}
    assert action_rules.active() is action_rules.EMPTY
