"""NamemapAdapter over a namemap module (a fake one, and KAAIR's when on disk); load_adapter."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from robot_agent.contract import (Geometry, Manifest, NamemapAdapter, Observation, RobotAdapter, Unbindable,
                                  load_adapter, policy_for, Recovery)

FIXTURE = Path(__file__).parent / "fixtures" / "contract" / "manifest_kaair.yaml"
KCARE_NAMEMAPS = [
    Path(__file__).resolve().parents[2] / "kcare_robot" / "kcare_robot" / "configs" / "grace_namemap.py",
    Path("/home/trung/trung_workdir/kcare_robot/kcare_robot/configs/grace_namemap.py"),
]


class World:
    def __init__(self, found=None):
        self.found, self.arrived, self.holding, self.opened = found, None, None, set()


def fake_namemap(**extra):
    m = types.ModuleType("toybot.configs.grace_namemap")
    m.SKILL_MAP = {"MoveTo": "go", "Find": "look", "Pick": "grab", "Place": "drop", "PutIn": "drop"}
    m.NOOP_ACTIONS = {"Wait"}
    m.SUPPORTED_ACTIONS = set(m.SKILL_MAP) | m.NOOP_ACTIONS
    m.VLM_ACTIONS = {"Pick"}
    m.to_loc = lambda n: "loc:" + n.lower()
    m.to_obj = lambda n: "obj:" + n.lower()
    for k, v in extra.items():
        setattr(m, k, v)
    return m


def test_without_manifest_one_is_synthesised():
    a = NamemapAdapter(fake_namemap())
    assert isinstance(a, RobotAdapter)
    assert a.manifest.robot == "toybot"
    assert a.manifest.skill_names == ["go", "look", "grab", "drop"]          # PutIn shares drop
    assert (a.geometry.arrive_tol_m, a.geometry.reach_m) == (0.3, 0.78)
    assert a.i18n.language == "en"
    assert a.supported_actions == {"MoveTo", "Find", "Pick", "Place", "PutIn", "Wait"}
    assert a.vlm_actions == {"Pick"}


def test_bind_like_action_mapper():
    a = NamemapAdapter(fake_namemap())
    assert a.bind({"action": "MoveTo", "object": "Kitchen"}) == ("go", {"inputs": "loc:kitchen"})
    assert a.bind({"action": "Find", "object": "Cup"}) == ("look", {"inputs": "obj:cup"})
    assert a.bind({"action": "PutIn", "object": "Box"}) == ("drop", {"inputs": "loc:box"})
    assert a.bind({"action": "Pick", "object": ""}, World(found="Mug")) == ("grab", {"inputs": "obj:mug"})
    assert a.bind({"action": "Wait", "object": ""}) is None
    for step, world in [({"action": "Wash", "object": "Cup"}, None), ({"action": "", "object": "x"}, None),
                        ({"action": "Pick", "object": ""}, World()), ({"action": "Find", "object": ""}, None)]:
        with pytest.raises(Unbindable):
            a.bind(step, world)


def test_bind_uses_build_params_like_closed_loop():
    def build_params(action, obj, world):
        if action == "Pick":
            return {"inputs": "obj:" + ((world and world.found) or obj).lower(), "type": "fine_move"}
        if action == "Explode":
            raise RuntimeError("ignored, like ClosedLoop._build_params")
        return {}
    a = NamemapAdapter(fake_namemap(build_params=build_params))
    assert a.bind({"action": "Pick", "object": "Cup"}, World(found="Mug")) == (
        "grab", {"inputs": "obj:mug", "type": "fine_move"})
    assert a.bind({"action": "Find", "object": "Cup"}) == ("look", {"inputs": "obj:cup"})


def test_manifest_skill_names_and_geometry_win():
    m = Manifest.from_dict({"robot": "toy", "language": "vi",
                            "geometry": {"arrive_tol_m": 0.5, "reach_m": 0.6},
                            "skills": [{"name": "nav", "action": "MoveTo"}]})
    a = NamemapAdapter(fake_namemap(), m)
    assert a.bind({"action": "MoveTo", "object": "Kitchen"})[0] == "nav"
    assert a.bind({"action": "Find", "object": "Cup"})[0] == "look"         # not in the manifest: namemap's
    assert a.geometry == Geometry(arrive_tol_m=0.5, reach_m=0.6)
    assert a.i18n.language == "vi"


def test_hooks_and_fallbacks():
    calls = []
    nm = fake_namemap(
        observe=lambda node: ("I see a cup", ["cup"]),
        reconcile_world=lambda node, world: calls.append(("reconcile", node)),
        health=lambda node: {"ok": True, "fps": 15},
        vlm_hook=lambda step, node: (False, 0.2, "no", 0.1),
        apply_skill_effect=lambda world, skill, params, result, node: calls.append(("effect", skill)),
        RECOVERY_OVERRIDES={"blocked": "ask_help"},
        PHRASES={"hi": {"en": "hi"}})
    a = NamemapAdapter(nm)
    o = a.observe()
    assert isinstance(o, Observation) and o.text == "I see a cup" and o.visible == ["cup"]
    w = World()
    assert a.reconcile(w, node="N") is w and calls[-1] == ("reconcile", "N")
    assert a.health() == {"ok": True, "fps": 15}
    assert a.vlm_hook({"action": "Pick"}) == (False, 0.2, "no", 0.1)
    a.apply_skill_effect(w, "grab")
    assert calls[-1] == ("effect", "grab")
    assert policy_for(a).decide("blocked") is Recovery.ASK_HELP
    assert a.i18n.text("hi") == "hi"

    bare = NamemapAdapter(fake_namemap(observe=lambda node: 1 / 0, health=lambda node: 1 / 0))
    assert bare.observe().text == ""                                          # never raises
    assert bare.health()["ok"] is False
    assert NamemapAdapter(fake_namemap()).health()["ok"] is None
    assert NamemapAdapter(fake_namemap()).vlm_hook({})[0] is True


def _write_pkg(root: Path, name: str, files: dict) -> None:
    for rel, text in files.items():
        p = root / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


@pytest.fixture
def on_path(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for k in [k for k in sys.modules if k.split(".")[0] in ("shimbot", "ownbot", "bothbot")]:
        del sys.modules[k]


NAMEMAP_SRC = 'SKILL_MAP = {"MoveTo": "move"}\nNOOP_ACTIONS = set()\nSUPPORTED_ACTIONS = {"MoveTo"}\n'


def test_load_adapter_shim_with_manifest(on_path):
    _write_pkg(on_path, "shimbot", {"__init__.py": "", "configs/__init__.py": "",
                                    "configs/grace_namemap.py": NAMEMAP_SRC,
                                    "configs/manifest.yaml": FIXTURE.read_text()})
    a = load_adapter("shimbot")
    assert isinstance(a, NamemapAdapter) and a.manifest.robot == "kaair" and a.i18n.language == "ko"
    assert a.bind({"action": "MoveTo", "object": "Home"}) == ("move", {"inputs": "Home"})   # no to_loc: as is


def test_load_adapter_prefers_configs_adapter(on_path):
    _write_pkg(on_path, "ownbot", {
        "__init__.py": "", "configs/__init__.py": "", "configs/grace_namemap.py": NAMEMAP_SRC,
        "configs/adapter.py": "class Adapter:\n    manifest = geometry = i18n = None\n    tag = 'own'\n"})
    assert load_adapter("ownbot").tag == "own"
    _write_pkg(on_path, "bothbot", {"__init__.py": "", "configs/__init__.py": "",
                                    "configs/adapter.py": "X = 1\n"})
    with pytest.raises(ImportError, match="ADAPTER"):
        load_adapter("bothbot")


def test_load_adapter_nothing(on_path):
    _write_pkg(on_path, "shimbot", {"__init__.py": "", "configs/__init__.py": ""})
    with pytest.raises(ImportError, match="grace_namemap"):
        load_adapter("shimbot")


def _load_file(path: Path):
    spec = importlib.util.spec_from_file_location("_kcare_grace_namemap_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kaair_namemap_binds_as_today():
    path = next((p for p in KCARE_NAMEMAPS if p.is_file()), None)
    if path is None:
        pytest.skip("kcare_robot not on disk")
    nm = _load_file(path)
    a = NamemapAdapter(nm, Manifest.from_yaml(FIXTURE))
    world = World(found="Cup")
    for step in [{"action": "MoveTo", "object": "LivingRoom"}, {"action": "Find", "object": "WaterBottle"},
                 {"action": "Pick", "object": ""}, {"action": "Place", "object": "SofaTable"},
                 {"action": "PutIn", "object": "Drawer"}, {"action": "Open", "object": "Drawer"}]:
        # what ActionMapper.to_skill + ClosedLoop._build_params produce today
        act, obj = step["action"], step["object"] or world.found
        skill = nm.SKILL_MAP[act]
        base = {"inputs": nm.to_loc(obj) if act in ("MoveTo", "Place", "PutIn") else nm.to_obj(obj)}
        base.update(nm.build_params(act, obj, world) or {})
        assert a.bind(step, world) == (skill, base), step
    assert a.bind({"action": "Wait", "object": ""}) is None
    with pytest.raises(Unbindable):
        a.bind({"action": "TurnOn", "object": "Light"})
    assert a.vlm_actions == nm.VLM_ACTIONS
