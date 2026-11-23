"""A pack that declares no goal kind at all (a schema-2 pack that only adds questions, a made-up pack with `extends` absent) is a
robot with no goal kinds of its own: it reads the default table, as a robot with no pack does. A pack with kinds of its own keeps
them (tests/test_pack_goal_kinds.py)."""

from robot_agent.core.pack import goal_kinds as kind_data
from robot_agent.core.pack.loader import Pack, default_pack
from robot_agent.core.planning import goals


def test_a_pack_with_no_goal_kinds_reads_the_default_table():
    bare = Pack(schema=2, name="questions_only", tables={})
    assert kind_data.is_default(bare) and goals.table(bare) is goals.GOAL_KINDS
    assert goals.missing_actions({"type": "on"}, {"Pick", "Place"}, bare) == []


def test_the_default_pack_and_no_pack_still_read_the_default_table():
    assert kind_data.is_default(None) and kind_data.is_default(default_pack())


def test_a_pack_with_a_kind_of_its_own_is_not_the_default_table():
    own = Pack(schema=2, name="own", tables={"goal_kinds": {"install": {"needs": ["Measure"]}}})
    assert not kind_data.is_default(own) and list(goals.table(own)) == ["install"]
