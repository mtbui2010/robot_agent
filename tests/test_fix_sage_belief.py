"""D28 (R2): SAGE repairs from the belief robot_agent puts in the planner obs.

AssistLoop / ClosedLoop append ``"Robot state: " + WorldState.as_text()`` to the obs;
embodiplan >= 0.2.2 reads that line back (``belief_from_obs``) as the state the plan
gate and the suffix repair start from. This pins the format between the two repos.
"""
import pytest

from robot_agent.core.planning.base import WorldState

sage = pytest.importorskip("embodiplan.sage")


def test_worldstate_text_is_read_back_by_sage():
    w = WorldState(arrived="drawer@kitchen", found="spoon", holding=None,
                   opened={"drawer@kitchen", "fridge@kitchen"}, on={"lamp@living room"})
    obs = "Places the robot can go to:\n- desk@study room\nGoals: on spoon to dining table\nRobot state: " + w.as_text()
    b = sage.belief_from_obs(obs)
    assert b is not None
    assert (b.arrived, b.found, b.holding) == ("drawer@kitchen", "spoon", None)
    assert b.opened == {"drawer@kitchen", "fridge@kitchen"} and b.on == {"lamp@living room"}


def test_empty_worldstate_reads_as_empty_belief():
    b = sage.belief_from_obs("Robot state: " + WorldState().as_text())
    assert (b.arrived, b.found, b.holding, b.opened, b.on) == (None, None, None, set(), set())
