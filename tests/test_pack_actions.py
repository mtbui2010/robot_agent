"""The action table as pack data (Step S10 of docs/proposals/generalize_core_design.md).

Five copies of "which actions name a place" (each a different set), the understand prompt's abilities, the planner prompt's
argument words and descriptions, the progress verbs, the non-plan steps, the releasing actions and the default run texts were
closed constants of the core. They are read from the standard pack's ``actions`` and ``constants`` files now. Proved here: the
data equals the old constants, kept verbatim in tests/legacy_ref/s10_action_tables.py (character for character, order included
where an order is read); every call site of the engine holds the same value it held; each call site keeps its own set (no set
is unified); the description of a robot is the same as the old function's over every subset of the actions; the pure readers on
made-up tables; a made-up pack's own action reaches the sets of the call sites that take a pack. Made-up robots only."""

import itertools
import types

import pytest

from legacy_ref import s10_action_tables as old
from robot_agent.contract import legacy_adapter, pack as schema
from robot_agent.core.pack import actions as A, default_pack, loader
from robot_agent.core.planning import announcer, mapper, plan_repair, reconcile, robot, robot_prompt, search


def decl(entries, name="made_up"):
    return schema.parse({"schema": 2, "name": name, "extends": "household", "source": "made up", "actions": entries}, name)


# -- the default data is the old constants -------------------------------------------------------------------------------------
def test_the_five_sets_of_the_old_call_sites_are_the_data():
    assert A.used_for("place_arg") == old.LOC_ACTIONS
    assert A.used_for("at_place") == old.AT_PLACE_ACTIONS
    assert A.used_for("to_loc") == old.MAPPER_LOCATION_ACTIONS == old.LEGACY_ADAPTER_LOCATION_ACTIONS
    assert A.used_for("act_here") == old.SEARCH_AT_PLACE and A.used_for("between") == old.SEARCH_BETWEEN


def test_the_sets_are_not_unified():
    sets = [A.used_for(u) for u in A.USES]
    assert len({s for s in sets}) == len(sets) == 5          # five uses, five different sets
    assert A.used_for("to_loc") < A.used_for("place_arg")    # the mappers' three names are not the loop's five
    assert "Find" in A.used_for("at_place") and "Find" not in A.used_for("place_arg") and "MoveTo" not in A.used_for("at_place")


def test_the_words_and_flags_are_the_old_tables_in_the_old_order():
    assert A.not_plan_steps() == old.NOT_PLAN_STEPS and A.releasing() == old.RELEASES
    assert A.abilities() == old.ABILITY and list(A.abilities()) == list(old.ABILITY)      # robot_description lists them in this order
    assert A.descriptions() == old.ACTION_TEXT
    assert {k: v for k, v in A.verbs().items()} == {k: v for k, v in old.VERBS_EN.items() if k in A.verbs()}
    assert A.core_text() == old.CORE_TEXT


def test_every_entry_is_english_only_with_a_source_and_a_known_class():
    for name, e in A.default_table().items():
        assert e.get("source"), name
        assert e.get("class") in (None, *schema.TABLES["actions"]["class"]), name
        assert (e.get("arg") or {}).get("type") in (None, *schema.TABLES["actions"]["arg"]["type"]), name
    assert set(A.default_table()) == set(old.ABILITY) | set(old.ACTION_TEXT) | old.NOT_PLAN_STEPS


# -- the engine's names are views of the data -----------------------------------------------------------------------------------
def test_the_engines_names_hold_the_old_values():
    assert robot.CORE_TEXT == old.CORE_TEXT and robot.NOT_PLAN_STEPS == old.NOT_PLAN_STEPS and robot._ABILITY == old.ABILITY
    assert list(robot._ABILITY) == list(old.ABILITY)
    assert robot_prompt.ACTION_TEXT == old.ACTION_TEXT and robot_prompt.ORDER == old.ORDER
    assert announcer.VERBS == {"en": old.VERBS_EN}
    assert reconcile.AT_PLACE_ACTIONS == old.AT_PLACE_ACTIONS and mapper._LOCATION_ACTIONS == old.MAPPER_LOCATION_ACTIONS
    assert search._BETWEEN == old.SEARCH_BETWEEN and search._AT_PLACE == old.SEARCH_AT_PLACE
    assert plan_repair.RELEASES == old.RELEASES and isinstance(plan_repair.RELEASES, tuple)


def test_the_contract_layers_own_copy_is_pinned_to_the_data():
    """The contract layer cannot read the pack (layer rule); its three names are tied to the data by this test instead."""
    assert legacy_adapter._LOCATION_ACTIONS == A.used_for("to_loc")


def test_the_loops_place_set_is_the_data_and_still_a_name_of_the_loop():
    """``_LOC_ACTIONS`` stays an attribute of assist_loop: the recorded mutation `place_arg_set_changed` patches it by name."""
    from robot_agent.core.planning import assist_loop
    assert assist_loop._LOC_ACTIONS == old.LOC_ACTIONS == A.used_for("place_arg")


def test_the_robot_description_is_the_old_functions_over_every_subset():
    def old_description(plan_actions):
        acts = [a for a in old.ABILITY if a in plan_actions]
        return old.CORE_TEXT["robot_description"].format(abilities=", ".join(old.ABILITY[a] for a in acts) or "nothing")

    names = sorted(set(old.ABILITY) | {"Close", "Dock", "Observe", "TurnOn"})
    for n in range(len(names) + 1):
        for subset in itertools.islice(itertools.combinations(names, n), 400):
            view = types.SimpleNamespace(plan_actions=set(subset), has_text=lambda k: False)
            assert robot.RobotView.robot_description(view) == old_description(set(subset))


def test_the_prompt_lines_are_the_old_ones():
    for a in old.ORDER:
        for style in ("action",):
            arg, text = old.ACTION_TEXT[a]
            view = types.SimpleNamespace(spec=lambda x: None)
            assert robot_prompt.action_line(view, a, style) == f"{a}{f' <{arg}>' if arg else ''}" + (f": {text}" if text else "")


# -- the pure readers, on made-up tables ------------------------------------------------------------------------------------------
def test_the_readers_on_a_made_up_table():
    t = {"Seat": {"class": "release", "arg": {"name": "wall", "type": "place"}, "releases": True, "verb": "seat",
                  "ability": "seat a pipe", "describe": "seat the pipe on the clips"},
         "Think": {"plan_step": False}, "Bad": "not a mapping"}
    assert A.used_for("to_loc", t) == {"Seat"} and A.used_for("place_arg", t) == {"Seat"} and A.used_for("between", t) == frozenset()
    assert A.not_plan_steps(t) == {"Think"} and A.releasing(t) == ("Seat",)
    assert A.abilities(t) == {"Seat": "seat a pipe"} and A.verbs(t) == {"Seat": "seat"}
    assert A.descriptions(t) == {"Seat": ("wall", "seat the pipe on the clips")}


def test_an_empty_table_gives_empty_sets_and_no_words():
    assert A.used_for("place_arg", {}) == frozenset() and A.not_plan_steps({}) == frozenset() and A.releasing({}) == ()
    assert A.abilities({}) == {} and A.descriptions({}) == {} and A.verbs({}) == {}


def test_an_unknown_use_is_an_error_not_an_empty_set():
    with pytest.raises(KeyError):
        A.used_for("no_such_use")


def test_table_of_reads_the_pack_and_falls_back_to_the_default_table():
    assert A.table_of(None) is A.default_table() and A.table_of(default_pack()) == A.default_table()
    bare = types.SimpleNamespace(table=lambda name: {})
    assert A.table_of(bare) is A.default_table()


def test_a_made_up_packs_action_reaches_the_sets_it_belongs_to():
    own = decl({"Seat": {"class": "release", "arg": {"name": "wall", "type": "place"}, "releases": True}})
    t = loader.merge(loader.chain_of(own))[0]["actions"]
    assert "Seat" in A.used_for("to_loc", t) and "Seat" in A.used_for("act_here", t) and "Seat" in A.releasing(t)
    assert "Seat" not in A.used_for("to_loc") and "Seat" not in A.releasing()          # the default table is not changed
    assert A.used_for("to_loc", t) - {"Seat"} == A.used_for("to_loc")


def test_core_text_is_cached_but_the_engines_copy_is_its_own():
    assert A.core_text() is A.core_text()
    robot.CORE_TEXT["x"] = "y"
    try:
        assert "x" not in A.core_text()
    finally:
        del robot.CORE_TEXT["x"]
