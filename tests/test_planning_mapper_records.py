"""core/planning/mapper.py (ActionMapper) and records.py (RunLogger, JSON coercion)."""

import json
import math

import pytest

from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.mapper import ActionMapper, Unmappable
from robot_agent.core.planning.records import (RunLogger, StepRecord, VerifyResult, _to_jsonable,
                                               new_task_record)


@pytest.fixture
def mapper():
    from fakebot.configs import grace_namemap
    return ActionMapper(grace_namemap)


# ── ActionMapper ──
@pytest.mark.parametrize("step, world, expected", [
    ({"action": "MoveTo", "object": "LivingRoom"}, {}, ("move", {"inputs": "living room"})),
    ({"action": "MoveTo", "object": "BedRoomCorner"}, {}, ("move", {"inputs": "bed_room_corner"})),
    ({"action": "Find", "object": "CoffeeMachine"}, {}, ("find", {"inputs": "coffee machine"})),
    ({"action": "Pick", "object": ""}, {"found": "Cup"}, ("pick", {"inputs": "cup"})),
    ({"action": "Place", "object": "DiningTable"}, {}, ("place", {"inputs": "dining table"})),
    ({"action": "PutIn", "object": "Drawer"}, {}, ("place", {"inputs": "drawer"})),
    ({"action": "Open", "object": "Drawer"}, {}, ("open_drawer", {"inputs": "drawer"})),
    ({"action": "Close", "object": "drawer@kitchen"}, {}, ("close_drawer", {"inputs": "drawer@kitchen"})),
])
def test_to_skill(mapper, step, world, expected):
    w = WorldState()
    w.update_from_dict(world)
    assert mapper.to_skill(step, w) == expected


def test_to_skill_noop_and_unmappable(mapper):
    w = WorldState()
    assert mapper.to_skill({"action": "Wait", "object": ""}, w) is None
    for bad in ({"action": ""}, {"action": "TurnOn", "object": "Light"}, {"action": "move", "object": "x"},
                {"action": "Pick", "object": ""}, {"action": "Find", "object": ""}):
        with pytest.raises(Unmappable):
            mapper.to_skill(bad, w)


def test_apply_effect_sequence(mapper):
    w = WorldState()
    seq = [("MoveTo", "Kitchen"), ("Open", "Drawer"), ("Find", "Apple"), ("Pick", "Apple"),
           ("MoveTo", "Table"), ("Place", "Table"), ("Close", "Drawer"), ("TurnOn", "Light"), ("TurnOff", "Light")]
    states = []
    for a, o in seq:
        mapper.apply_effect({"action": a, "object": o}, w)
        states.append((w.arrived, w.found, w.holding, sorted(w.opened), sorted(w.on)))
    assert states == [
        ("Kitchen", None, None, [], []),
        ("Kitchen", None, None, ["Drawer"], []),
        ("Kitchen", "Apple", None, ["Drawer"], []),
        ("Kitchen", None, "Apple", ["Drawer"], []),
        ("Table", None, "Apple", ["Drawer"], []),
        ("Table", None, None, ["Drawer"], []),
        ("Table", None, None, [], []),
        ("Table", None, None, [], ["Light"]),
        ("Table", None, None, [], []),
    ]


def test_screen(mapper):
    steps = [{"action": "MoveTo", "object": "Kitchen"}, {"action": "Wait"}, {"action": "move", "object": "desk"},
             {"action": "TurnOn", "object": "Light"}]
    assert mapper.screen(steps) == [
        "step 3: unsupported action 'move' on object 'desk' -- this robot cannot execute it",
        "step 4: unsupported action 'TurnOn' on object 'Light' -- this robot cannot execute it",
    ]


# ── WorldState round trip ──
def test_world_state_roundtrip_and_text():
    w = WorldState(arrived="kitchen", holding="cup", opened={"b", "a"})
    w2 = WorldState()
    w2.update_from_dict(w.to_dict())
    assert w2.to_dict() == w.to_dict()
    assert w.as_text() == "arrived=kitchen found=- holding=cup opened=[a, b] on=[-]"
    c = w.copy()
    c.opened.add("z")
    assert "z" not in w.opened
    assert w.found_pose_is_stale((0, 0))
    w.found_pose = {"robot_pose": [1.0, 1.0, 0]}
    assert not w.found_pose_is_stale((1.1, 1.0)) and w.found_pose_is_stale((2.0, 1.0))


# ── records ──
def test_to_jsonable():
    np = pytest.importorskip("numpy")
    obj = {"s": {3, 1, 2}, "t": (1, 2), "nan": float("nan"), "inf": math.inf, "np": np.float32(0.5),
           "arr": np.arange(3), "b": b"\xff", "vr": VerifyResult("isdone", True, "ok"), 5: None}
    out = _to_jsonable(obj)
    assert out == {"s": [1, 2, 3], "t": [1, 2], "nan": None, "inf": None, "np": 0.5, "arr": [0, 1, 2],
                   "b": "�", "vr": {"layer": "isdone", "ok": True, "detail": "ok", "confidence": None,
                                         "latency_ms": 0.0}, "5": None}
    json.dumps(out)


def test_run_logger_writes_events_and_snapshot(tmp_path):
    run = new_task_record("r1", "bring the cup", planner="sage+assist", started_at=10.0)
    path = tmp_path / "deep" / "dir" / "r1.jsonl"
    log = RunLogger(run, path)
    log.event("task_start", t=10.0, task=run.task, world={"opened": {"drawer"}})
    log.event("status", msg="no time given")
    run.steps.append(StepRecord(index=0, action="Find", object="cup", skill="find", status="success",
                                verifies=[VerifyResult("isdone", True, "")]))
    run.status = "success"
    log.snapshot()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0] == {"t": 10.0, "kind": "task_start", "task": "bring the cup", "world": {"opened": ["drawer"]}}
    assert rows[1] == {"kind": "status", "msg": "no time given"}       # no implicit timestamp
    snap = rows[2]["record"]
    assert rows[2]["kind"] == "snapshot" and snap["status"] == "success" and snap["planner"] == "sage+assist"
    assert snap["steps"][0]["verifies"][0]["layer"] == "isdone"
    assert run.steps[0].verified


def test_step_record_verified():
    assert not StepRecord(0, "Find", "cup", "find").verified
    assert not StepRecord(0, "Find", "cup", "find", verifies=[VerifyResult("isdone", True),
                                                            VerifyResult("vlm", False)]).verified
