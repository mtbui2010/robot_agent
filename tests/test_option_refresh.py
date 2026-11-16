"""``refresh_options``: a host that merges pack data into a built loop's manifest (a manifest overlay) resolves the option keys again.

The class it closes: the keys are resolved once, when the loop is built; data merged later is not seen, so an option that is on keeps its
default (empty) data and silently does nothing. Made-up options and values only; the loop is the real AssistLoop."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.core.options import REGISTRY, Key, Option, OptionSpec, Registry
from robot_agent.core.planning.option_services import refresh_options

LAMP = OptionSpec(name="zz_lamp", keys=(Key("enabled", "bool", False, aliases=("zz_lamp",)), Key("chant", "str", ""),
                                        Key("table", "dict", {}, sources=("manifest",)), Key("beats", "int", 3)),
                  enabled="enabled")


class Lamp(Option):
    pass


def lamp_registry():
    r = Registry(builtin=None, entry_points=None)
    r.register(LAMP, Lamp)
    return r


@pytest.fixture
def loop(make_assist, env):
    return make_assist(FakeRobot(env, {}), FakeLLM(default={}), ScriptedPlanner(steps("MoveTo dining table@kitchen")))


def merge_options(monkeypatch, loop, options: dict):
    """What a host's overlay does after the loop was built: the robot view's ``planner.options`` gets more data."""
    planner = dict(loop.robot.manifest.planner or {})
    monkeypatch.setattr(loop.robot.manifest, "planner", {**planner, "options": {**(planner.get("options") or {}), **options}})


def snapshot(lp):
    return {n: dict(c) for n, c in lp.options.configs.items()}, [o.spec.name for o in lp.options.active], dict(lp.cfg)


def test_refresh_options_a_manifest_that_did_not_change_gives_the_same_keys_and_options_empty(loop):
    before = snapshot(loop)
    refresh_options(loop, {"input_pivot": "none"})
    assert snapshot(loop) == before


def test_refresh_options_the_engine_never_refreshes_by_itself_data_merged_later_is_not_seen_off(loop, monkeypatch):
    merge_options(monkeypatch, loop, {"zz_lamp": {"enabled": True, "chant": "ooh"}})
    assert "zz_lamp" not in loop.options.configs                                 # the loop's own dispatcher, built at construction
    from robot_agent.core.planning import assist_loop
    assert "refresh_options" not in open(assist_loop.__file__, encoding="utf-8").read()     # only a host calls it: the default path is unchanged


def test_refresh_options_made_up_option_sees_its_data_merged_after_build_and_a_run_option_still_wins(loop, monkeypatch):
    merge_options(monkeypatch, loop, {"zz_lamp": {"chant": "ooh", "table": {"blue": 2, "assumed": True}, "beats": 9}})
    disp = refresh_options(loop, {"zz_lamp": "1", "zz_lamp.beats": "4"}, environ={}, registry=lamp_registry())
    lamp = disp.option("zz_lamp")
    assert lamp is not None and loop.options is disp
    assert dict(lamp.cfg) == {"enabled": True, "chant": "ooh", "table": {"blue": 2, "assumed": True}, "beats": 4}
    assert loop.cfg["zz_lamp.chant"] == "ooh" and loop.cfg["zz_lamp"] is True     # mirrored into loop.cfg by legacy name


def test_refresh_options_an_option_switched_on_only_by_the_merged_manifest_is_made(loop, monkeypatch):
    disp = refresh_options(loop, {}, environ={}, registry=lamp_registry())
    assert disp.option("zz_lamp") is None
    merge_options(monkeypatch, loop, {"zz_lamp": {"enabled": True}})
    assert refresh_options(loop, {}, environ={}, registry=lamp_registry()).option("zz_lamp") is not None


def made_up(key: Key):
    return {"bool": lambda: not key.default, "int": lambda: int(key.default or 0) + 7, "float": lambda: float(key.default or 0) + 0.5,
            "str": lambda: "zq made-up text", "dict": lambda: {"zq": {"made_up": 1}, "assumed": True}}[key.type]()   # lazy: a str default need not be a number


def test_refresh_options_every_registered_option_sees_a_manifest_key_merged_after_build_none_is_silently_inert(loop, monkeypatch):
    """The regression of the class: for EVERY registered option, a key its manifest may declare reaches its config once the host refreshes."""
    data = {s.name: {k.name: made_up(k) for k in s.keys if "manifest" in k.sources} for s in REGISTRY.specs()}
    merge_options(monkeypatch, loop, data)
    refresh_options(loop, {}, environ={})
    missed = [f"{n}.{k}" for n, keys in data.items() for k, v in keys.items() if loop.options.configs[n][k] != v]
    assert not missed and sum(map(len, data.values())) > 10, missed
