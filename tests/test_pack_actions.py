"""Step S10: the action table is pack data. Which actions take a place as their argument (five different sets), which are not
plan steps, which release the held object, the words of an action (understand prompt, planner prompt, progress line) and the
CORE_TEXT sentences come from the standard pack's robot_agent/packs/household/actions.yaml and constants.yaml, read through
robot_agent/core/pack/actions.py and constants.py. These tests prove nothing changed for a robot that declares no actions:
(1) the default data equals the old constants (a verbatim copy of the old code in tests/legacy_ref/s10_action_tables.py), set by
set, and each call site keeps ITS set (no set is unified); (2) the legacy adapter chooses to_loc / to_obj as the old code did,
with no argument and with the pack's set; (3) the pure readers on made-up tables (a new action joins the sets whose query it
matches; an empty table gives empty sets); (4) the files are labelled. Made-up robots and tables only."""

import types

import pytest

from legacy_ref import s10_action_tables as old
from robot_agent.contract import legacy_adapter
from robot_agent.contract.legacy_adapter import NamemapAdapter
from robot_agent.core.pack import actions as action_data
from robot_agent.core.pack import constants as constant_data
from robot_agent.core.planning import announcer, assist_loop, mapper, plan_repair, reconcile, robot, robot_prompt, search

TABLE = action_data.default_table()
MOVE_OR_RELEASE = ("move", "release")


# -- (1) the default data is the old constants --------------------------------------------------------------------------------
def test_each_call_site_still_has_its_own_set():
    assert assist_loop._LOC_ACTIONS == old.LOOP_LOC_ACTIONS
    assert reconcile.AT_PLACE_ACTIONS == old.RECONCILE_AT_PLACE and isinstance(reconcile.AT_PLACE_ACTIONS, frozenset)
    assert mapper._LOCATION_ACTIONS == old.MAPPER_LOCATION_ACTIONS
    assert legacy_adapter._LOCATION_ACTIONS == old.ADAPTER_LOCATION_ACTIONS
    assert search._BETWEEN == old.SEARCH_BETWEEN and search._AT_PLACE == old.SEARCH_AT_PLACE
    assert plan_repair.RELEASES == old.PLAN_REPAIR_RELEASES and isinstance(plan_repair.RELEASES, tuple)


def test_the_five_place_argument_sets_are_not_unified():
    sets = [assist_loop._LOC_ACTIONS, set(reconcile.AT_PLACE_ACTIONS), mapper._LOCATION_ACTIONS, search._AT_PLACE]
    assert len({frozenset(s) for s in sets}) == 4, "the four sets of the engine differ (the adapter's equals the mapper's)"
    assert set(legacy_adapter._LOCATION_ACTIONS) == mapper._LOCATION_ACTIONS


def test_the_views_have_the_old_types_and_order():
    assert type(assist_loop._LOC_ACTIONS) is set and type(mapper._LOCATION_ACTIONS) is set
    assert type(search._BETWEEN) is set and type(search._AT_PLACE) is set and type(robot.NOT_PLAN_STEPS) is set
    assert list(robot_prompt.ACTION_TEXT) == list(old.ACTION_TEXT) and robot_prompt.ACTION_TEXT == old.ACTION_TEXT
    assert list(robot.CORE_TEXT) == list(old.CORE_TEXT) and robot.CORE_TEXT == old.CORE_TEXT
    assert list(robot._ABILITY) == list(old._ABILITY) and robot._ABILITY == old._ABILITY


def test_not_plan_steps_and_the_words_are_the_old_ones():
    assert robot.NOT_PLAN_STEPS == old.NOT_PLAN_STEPS
    assert announcer.VERBS == old.VERBS and set(announcer.VERBS["en"]) == set(old.VERBS["en"])
    assert robot_prompt.ORDER == ("MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close", "Say", "WaitForUnload", "Dock")
    assert set(robot_prompt.ACTION_TEXT) == set(robot_prompt.ORDER), "every action of ORDER has its text in the table"


def test_the_robot_description_is_the_old_sentence():
    acts = [a for a in old._ABILITY]
    want = old.CORE_TEXT["robot_description"].format(abilities=", ".join(old._ABILITY[a] for a in acts))
    assert robot.CORE_TEXT["robot_description"].format(abilities=", ".join(robot._ABILITY[a] for a in acts)) == want


# -- (2) the legacy adapter takes the set as an argument ------------------------------------------------------------------------
def _adapter(**kw):
    ns = types.ModuleType("fakemap")
    ns.SKILL_MAP = {a: a.lower() for a in ("MoveTo", "Place", "PutIn", "Open", "Close", "Find", "Pick", "Say", "Serve")}
    ns.to_loc = lambda n: "LOC:" + n
    ns.to_obj = lambda n: "OBJ:" + n
    return NamemapAdapter(ns, **kw)


@pytest.mark.parametrize("kw", ({}, {"location_actions": None},
                                {"location_actions": action_data.names(TABLE, cls=MOVE_OR_RELEASE)}),
                         ids=("no_argument", "none", "the_pack_set"))
def test_bind_chooses_to_loc_or_to_obj_as_the_old_code_did(kw):
    a = _adapter(**kw)
    for action in ("MoveTo", "Place", "PutIn", "Open", "Close", "Find", "Pick", "Say", "Serve"):
        skill, params = a.bind({"action": action, "object": "x"})
        want = ("LOC:" if action in old.ADAPTER_LOCATION_ACTIONS else "OBJ:") + "x"
        assert (skill, params) == (action.lower(), {"inputs": want}), action


def test_another_set_changes_the_choice_and_the_default_does_not():
    a = _adapter(location_actions={"Open"})
    assert a.bind({"action": "Open", "object": "x"})[1] == {"inputs": "LOC:x"}
    assert a.bind({"action": "MoveTo", "object": "x"})[1] == {"inputs": "OBJ:x"}
    assert _adapter().location_actions == old.ADAPTER_LOCATION_ACTIONS


def test_the_view_of_a_robot_passes_the_pack_set(monkeypatch):
    seen = {}

    def fake_load(pkg, **kw):
        seen.update(kw)
        return _adapter()
    monkeypatch.setattr(robot, "load_adapter", fake_load)
    robot.RobotView.load("anything")
    assert set(seen["location_actions"]) == old.ADAPTER_LOCATION_ACTIONS


# -- (3) the pure readers on made-up tables --------------------------------------------------------------------------------------
MADE_UP = {"Slide": {"class": "move", "arg": {"name": "spot", "type": "place"}, "verb": "slide to", "ability": "slide over",
                     "describe": "slide the tray"},
           "Drop": {"class": "release", "releases": True, "arg": {"name": "bin", "type": "container"}},
           "Peek": {"class": "sense", "plan_step": False}, "Odd": "not a mapping", "Bare": {}}


def test_a_declared_action_joins_the_sets_whose_query_it_matches():
    assert action_data.names(MADE_UP, cls=MOVE_OR_RELEASE) == ("Slide", "Drop")
    assert action_data.names(MADE_UP, arg=("place", "container")) == ("Slide", "Drop")
    assert action_data.names(MADE_UP, releases=True) == ("Drop",)
    assert action_data.names(MADE_UP, releases=False) == ("Slide", "Peek", "Odd", "Bare")
    assert action_data.names(MADE_UP, plan_step=False) == ("Peek",)
    assert action_data.names(MADE_UP, plan_step=True) == ("Slide", "Drop", "Odd", "Bare")
    assert action_data.names(MADE_UP, cls=("move",), arg=("container",)) == ()
    assert action_data.names(MADE_UP) == tuple(MADE_UP)


def test_words_and_prompt_text_skip_what_is_not_declared():
    assert action_data.words(MADE_UP, "verb") == {"Slide": "slide to"}
    assert action_data.words(MADE_UP, "ability") == {"Slide": "slide over"}
    assert action_data.prompt_text(MADE_UP) == {"Slide": ("spot", "slide the tray")}


def test_an_empty_table_gives_nothing():
    assert action_data.names({}, cls=MOVE_OR_RELEASE) == () and action_data.words({}, "verb") == {}
    assert action_data.prompt_text({}) == {} and constant_data.texts({}, "core_text.") == {}


def test_constants_are_read_by_prefix():
    table = {"core_text.a": {"value": "x"}, "core_text.b": {"value": "y"}, "other.c": {"value": "z"}, "core_text.d": {}}
    assert constant_data.texts(table, "core_text.") == {"a": "x", "b": "y"}


# -- (4) the files are labelled ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("table", (TABLE, constant_data.default_table()), ids=("actions", "constants"))
def test_every_entry_carries_a_source_label(table):
    assert table and all(isinstance(e.get("source"), str) and e["source"] for e in table.values())


def test_the_default_table_is_what_the_default_pack_resolves_to():
    from robot_agent.core.pack import default_pack
    assert default_pack().table("actions") == TABLE and default_pack().table("constants") == constant_data.default_table()
    assert not default_pack().problems
