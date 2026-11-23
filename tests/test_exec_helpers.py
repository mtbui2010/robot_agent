"""Step S23a: the execution helpers (_bind, _params, _holding, _base_pose, _dist_to_place, _found_point, _place_of_far_point)
moved verbatim from assist_loop.py to the ExecHelpers mixin in phases/exec_helpers.py. These tests prove nothing changed:
(1) the mixin is in AssistLoop's method resolution order, serves the seven names and ``_effect`` stays in assist_loop.py;
(2) the module's place-argument set equals the loop's; (3) a differential test against the verbatim reference copy
(tests/legacy_ref/s23a_exec_helpers.py) over made-up robots, steps, sensors, places and configs: the old and the new class
return equal results and make the same sensor calls. Made-up robots only."""

import itertools
import types

import pytest

from legacy_ref import s23a_exec_helpers as old
from robot_agent.contract import Unbindable
from robot_agent.core.planning import assist_loop, langdata, places
from robot_agent.core.planning.phases import PHASE_MIXINS, exec_helpers

NAMES = ("_bind", "_params", "_holding", "_base_pose", "_dist_to_place", "_found_point", "_place_of_far_point")


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_seven_names():
    assert exec_helpers.ExecHelpers in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(exec_helpers.ExecHelpers) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(assist_loop.AssistLoop), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(exec_helpers.ExecHelpers, n), n


def test_effect_stays_in_the_loop_file():
    assert "_effect" in vars(assist_loop.AssistLoop) and "_effect" not in vars(exec_helpers.ExecHelpers)


def test_place_argument_set_is_the_loops():
    assert exec_helpers._LOC_ACTIONS == assist_loop._LOC_ACTIONS == old._LOC_ACTIONS


# -- (3) differential test ----------------------------------------------------------------------------------------------------
class FakeRobot:
    def __init__(self, mapped, holding=None, pose=None):
        self.mapped, self._holding, self._pose = mapped, holding, pose

    def bind(self, st, world):
        if isinstance(self.mapped, Exception):
            raise self.mapped
        return self.mapped

    def holding_sensor(self):
        return self._holding

    def pose_sensor(self):
        return self._pose


class FakeSR:
    def __init__(self, results):
        self.results, self.calls = results, []

    def execute(self, skill, params, node):
        self.calls.append((skill, dict(params), node))
        r = self.results[skill]
        if isinstance(r, Exception):
            raise r
        return r


def _host(cls, robot, sr, cfg, lang, find_params):
    h = cls()
    h.robot, h.sr, h.cfg, h._run_lang, h._find_params = robot, sr, cfg, lang, find_params
    h._query_of = lambda st, o: f"query:{o}"
    h._in_lang = lambda text, lang: f"[{lang}]{text}"
    return h


class Old(old.RefExecHelpers):
    pass


class New(exec_helpers.ExecHelpers):
    pass


def _both(robot_args, sr_results, cfg, lang, find_params):
    out = []
    for cls in (Old, New):
        sr = FakeSR(dict(sr_results))
        out.append((_host(cls, FakeRobot(*robot_args), sr, dict(cfg), lang, find_params), sr))
    return out


def _outcome(fn):
    try:
        return ("ok", fn())
    except Exception as e:
        return ("raised", type(e).__name__)


STEPS = [{"action": a, "object": o} for a in ("Find", "Pick", "Place", "MoveTo", "Say", "Announce", "Wait", "Open")
         for o in ("cup", "table", "hello there")]
MAPPED = [None, ("skill_a", {"k": 1}), ("skill_b", None), ("skill_c", {"inputs": "x", "text": "ciao a tutti"}),
          ("skill_d", {"text": "hello", "inputs": "   "}), Unbindable("no way")]
CFGS = [{"find_min_conf": 0, "find_verify": False, "reach_far": 0.9}, {"find_min_conf": 0.4, "find_verify": True, "reach_far": 0.9}]
FIND_PARAMS = [set(), {"min_conf"}, {"min_conf", "verify"}]
LANGS = ["en", "ko", "", None, "de"]


@pytest.mark.parametrize("lang", LANGS)
def test_bind_and_params_equal_the_reference(lang, monkeypatch):
    for mapped, cfg, fp in itertools.product(MAPPED, CFGS, FIND_PARAMS):
        (o, _), (n, _) = _both((mapped,), {}, cfg, lang, fp)
        for st in STEPS:
            world = types.SimpleNamespace()
            assert _outcome(lambda: o._bind(dict(st), world)) == _outcome(lambda: n._bind(dict(st), world)), (st, mapped, cfg, fp)
            assert _outcome(lambda: o._params(dict(st), world)) == _outcome(lambda: n._params(dict(st), world)), (st, mapped)


def test_bind_translates_a_say_when_the_prompt_language_differs(monkeypatch):
    monkeypatch.setattr(langdata, "prompt_lang", lambda: "en")
    (o, _), (n, _) = _both((("say_skill", {"text": "ciao a tutti"}),), {}, CFGS[0], "ko", set())
    st = {"action": "Say", "object": "x"}
    got = n._bind(st, None)
    assert got == o._bind(st, None)
    assert got[1]["text"] == "[ko]ciao a tutti"


@pytest.mark.parametrize("sensor_result", [{"isdone": True}, {"isdone": False}, {}, RuntimeError("boom")])
@pytest.mark.parametrize("skill", [None, "holding"])
@pytest.mark.parametrize("default", [True, False])
def test_holding_equal_the_reference(sensor_result, skill, default):
    (o, so), (n, sn) = _both((None, skill), {"holding": sensor_result}, CFGS[0], "en", set())
    assert o._holding("node", default) == n._holding("node", default)
    assert so.calls == sn.calls


@pytest.mark.parametrize("pose", [{"pose": [1.0, 2.0, 90.0, 7.0]}, {"pose": [1, 2]}, {}, RuntimeError("boom")])
@pytest.mark.parametrize("skill", [None, "pose"])
def test_base_pose_equal_the_reference(pose, skill):
    (o, so), (n, sn) = _both((None, None, skill), {"pose": pose}, CFGS[0], "en", set())
    assert o._base_pose("node") == n._base_pose("node")
    assert so.calls == sn.calls


ENV = {"table": {"loc": {"x": 1.0, "y": 0.0}}, "door": {"loc": {"x": 3.0, "y": 0.0}, "openable": True},
       "anna": {"loc": {"x": 1.5, "y": 1.0}, "person": True}, "shelf": {"loc": {"x": 1.0, "y": 1.2}},
       "nowhere": {}, "halfway": {"loc": {"x": 1.0}}}


def _places():
    return places.Places(dict(ENV), openable=lambda k: bool(ENV.get(k, {}).get("openable")))


@pytest.mark.parametrize("pose", [{"pose": [0.0, 0.0, 0.0]}, {"pose": [1.0, 1.0, 90.0]}, {"pose": [2.5, 0.0, 180.0]}, {"pose": [1]}])
@pytest.mark.parametrize("skill", [None, "pose"])
def test_dist_and_far_point_equal_the_reference(pose, skill):
    (o, so), (n, sn) = _both((None, None, skill), {"pose": pose}, CFGS[0], "en", set())
    pl = _places()
    for key in list(ENV) + ["missing"]:
        assert _outcome(lambda: o._dist_to_place(key, "node", pl)) == _outcome(lambda: n._dist_to_place(key, "node", pl)), key
    for p3 in ([0.2, 0.1, 0.5], [1.5, 0.0, 0.3], [2.0, 0.5, 0.0], [0.9, 0.0, 0.0], [0.0, 3.0, 1.0]):
        for arrived in (None, "table", "shelf"):
            w = types.SimpleNamespace(arrived=arrived)
            assert (_outcome(lambda: o._place_of_far_point(p3, "node", pl, w))
                    == _outcome(lambda: n._place_of_far_point(p3, "node", pl, w))), (p3, arrived)
    assert so.calls == sn.calls


@pytest.mark.parametrize("result", [None, {}, {"ins": {}}, {"ins": "x"}, {"ins": {"cup": {"pose_3d": [1, 2, 3, 4]}}},
                                    {"ins": {"mug": {"loc_3d": [0.1, 0.2, 0.3]}}}, {"ins": {"cup": {"pose_3d": ["a"]}}},
                                    {"ins": {"cup": {"pose_3d": None, "loc_3d": [4, 5, 6]}}}, {"ins": {"cup": 5}}, "text"])
@pytest.mark.parametrize("obj", ["cup", "other", None])
def test_found_point_equal_the_reference(result, obj):
    assert old.RefExecHelpers._found_point(result, obj) == exec_helpers.ExecHelpers._found_point(result, obj)
    assert assist_loop.AssistLoop._found_point(result, obj) == old.RefExecHelpers._found_point(result, obj)
