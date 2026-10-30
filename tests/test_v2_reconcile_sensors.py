"""SAGE-v2 reconcile (core/planning/reconcile.py, docs/guard.md): provenance on the WorldState, the pose ->
`arrived` within arrive_tol (else None), a missing / stale pose = unknown, container states from the adapter."""

import time

from robot_agent.core.planning import reconcile as R
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.robot import RobotView

from fakes import load_env

DS, DT, DR = "desk@study room", "dining table@kitchen", "drawer@kitchen"


class Stub:
    """The fakebot adapter with a controllable pose() / container_states()."""

    def __init__(self, inner, pose=None, containers=None):
        self._inner, self._pose, self._containers, self.container_calls = inner, pose, containers, 0
        if containers is None:
            self.container_states = None

    def __getattr__(self, k):
        return getattr(self._inner, k)

    def pose(self, node=None):
        return dict(self._pose) if self._pose is not None else {}

    def container_states(self, node=None):         # replaced by None when the stub has no containers
        self.container_calls += 1
        return dict(self._containers)


def view(pose=None, containers=None, max_age=None) -> RobotView:
    rv = RobotView.load("fakebot")
    rv = RobotView(Stub(rv.adapter, pose, containers))
    if max_age is not None:
        rv.geometry.extra["pose_max_age_s"] = max_age
    else:
        rv.geometry.extra.pop("pose_max_age_s", None)
    return rv


def loc(env, k):
    return env[k]["loc"]["x"], env[k]["loc"]["y"]


# ── WorldState provenance ────────────────────────────────────────────────
def test_provenance_follows_the_value():
    w = WorldState(arrived=DS)
    assert w.provenance_of("arrived")["source"] == "belief"
    w.mark("arrived", "sensor", ts=100.0, detail="0.01 m from desk")
    assert w.provenance_of("arrived") == {"source": "sensor", "ts": 100.0, "detail": "0.01 m from desk"}
    w.arrived = DT                                    # a skill's effect, a load: a belief again
    assert w.provenance_of("arrived")["source"] == "belief"
    w.mark_edited(["arrived", "holding_since"])       # PUT /agent/world
    assert w.provenance_of("arrived")["source"] == "operator"


def test_to_dict_backwards_compatible_and_as_text_unchanged():
    w = WorldState(arrived=DS, opened={DR})
    d = w.to_dict()
    assert set(d) == {"arrived", "found", "holding", "opened", "on", "holding_since", "found_pose", "holding_pose",
                      "provenance"}
    assert d["arrived"] == DS and d["opened"] == [DR]
    assert d["provenance"]["opened"]["unconfirmed"] == [DR]
    assert w.as_text() == f"arrived={DS} found=- holding=- opened=[{DR}] on=[-]"
    c = w.copy()
    c.mark("arrived", "sensor")
    assert w.provenance_of("arrived")["source"] == "belief"     # a deep copy


def test_container_provenance():
    w = WorldState(opened={DR})
    w.mark_container(DR, True, ts=5.0)
    assert w.container_provenance(DR) == {"source": "sensor", "ts": 5.0, "open": True}
    w.opened.discard(DR)                              # closed by a skill since: belief again
    assert w.container_provenance(DR)["source"] == "belief"
    assert w.provenance_view()["opened"]["unconfirmed"] == []


# ── the pose ─────────────────────────────────────────────────────────────
def test_pose_within_tolerance_sets_arrived_sensor():
    env = load_env()
    x, y = loc(env, DS)
    w = WorldState(arrived=DT)
    rep = R.reconcile_arrived(w, view({"x": x + 0.1, "y": y, "ts": time.time()}), None, None, env, 0.3)
    assert w.arrived == DS and w.provenance_of("arrived")["source"] == "sensor" and rep["source"] == "adapter"


def test_pose_between_places_is_none_not_the_belief():
    env = load_env()
    x, y = loc(env, DS)
    w = WorldState(arrived=DS)
    R.reconcile_arrived(w, view({"x": x + 0.8, "y": y}), None, None, env, 0.3)
    p = w.provenance_of("arrived")
    assert w.arrived is None and p["source"] == "sensor" and "> 0.30 m" in p["detail"]


def test_missing_pose_is_unknown():
    env = load_env()
    w = WorldState(arrived=DS)

    def sense(skill):
        raise RuntimeError("localization lost")
    rep = R.reconcile_arrived(w, view(None), None, sense, env, 0.3)
    assert w.arrived is None and w.provenance_of("arrived")["source"] == "unknown"
    assert "localization lost" in rep["why"]


def test_stale_pose_is_unknown():
    env = load_env()
    x, y = loc(env, DS)
    w = WorldState(arrived=DS)
    R.reconcile_arrived(w, view({"x": x, "y": y, "ts": time.time() - 30}, max_age=2.0), None, None, env, 0.3)
    assert w.arrived is None and "no fresh pose" in w.provenance_of("arrived")["why"]
    # no limit in the manifest: an old timestamp is not judged
    R.reconcile_arrived(w, view({"x": x, "y": y, "ts": time.time() - 30}), None, None, env, 0.3)
    assert w.arrived == DS


def test_pose_from_the_sensor_skill_when_the_adapter_has_none():
    env = load_env()
    x, y = loc(env, DT)
    from robot_agent.contract import SkillResult
    rv = view(None)
    seen = []

    def sense(skill):
        seen.append(skill)
        return SkillResult(True, data={"pose": [x, y, 0.0]})
    w = WorldState()
    rep = R.reconcile_arrived(w, rv, None, sense, env, 0.3)
    assert w.arrived == DT and seen == [rv.pose_sensor()] and rep["source"] == rv.pose_sensor()


def test_nearest_place():
    env = load_env()
    x, y = loc(env, DT)
    at, d, near = R.nearest_place(x + 0.2, y, env, 0.3)
    assert at == DT and near == DT and abs(d - 0.2) < 1e-6
    assert R.nearest_place(x + 0.5, y, env, 0.3)[0] is None


# ── containers ───────────────────────────────────────────────────────────
def test_containers_read_replace_the_belief_unread_stay_unconfirmed():
    env = load_env()
    w = WorldState(opened={DR, "chest@bedroom"})
    rv = view({"x": 0, "y": 0}, containers={DR: False, "washer@laundry room": None})
    rep = R.reconcile_sensors(w, rv, None, None, env, 0.3)
    assert w.opened == {"chest@bedroom"} and rep["containers"] == {DR: False}
    assert rep["unconfirmed"] == ["chest@bedroom"]
    assert w.provenance_view()["opened"]["containers"][DR] == {"source": "sensor", "ts": w.provenance["opened"]["containers"][DR]["ts"], "open": False}


def test_no_container_states_no_call():
    env = load_env()
    rv = view({"x": 0, "y": 0})
    w = WorldState(opened={DR})
    assert R.reconcile_containers(w, rv, None) == {} and w.opened == {DR}


# ── RobotView.reconcile ──────────────────────────────────────────────────
def test_robot_view_reconcile_with_places_reports_and_changes():
    env = load_env()
    x, y = loc(env, DT)
    rv = view({"x": x, "y": y}, containers={DR: True})
    w = WorldState(arrived=DS)
    rep = {}
    changed = rv.reconcile(w, None, places=env, report=rep)
    assert changed["arrived"] == [DS, DT] and changed["opened"] == [[], [DR]]
    assert rep["arrived"]["pose"] == [round(x, 3), round(y, 3)]


def test_robot_view_reconcile_without_places_is_as_before():
    rv = view({"x": 0, "y": 0}, containers={DR: True})
    w = WorldState(arrived=DS)
    rv.reconcile(w, None)
    assert w.arrived == DS and w.opened == set() and rv.adapter.container_calls == 0
