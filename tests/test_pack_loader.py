"""The pack loader, the registry of named callables, the schema-1 lift and the shipped packs (robot_agent/core/pack,
robot_agent/packs; Step S02 of docs/proposals/generalize_core_design.md). A robot that declares no ``pack:`` section resolves
to the standard pack with what it declares today, and resolving changes nothing the old code computes. Made-up packs only;
nothing here pins WHAT the shipped packs hold, so a later step adds its section file without touching this file."""

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest
import yaml

from robot_agent.api.robot_info import manifest_of
from robot_agent.contract import FailureCode, RecoveryRule, load_adapter
from robot_agent.contract.manifest import Manifest
from robot_agent.contract.recovery import Recovery, RecoveryPolicy, policy_for
from robot_agent.core.pack import PackError, Registry, check, lift, loader, resolve, show
from robot_agent.core.planning.robot import RobotView

ROOT = Path(__file__).resolve().parents[1]
FAKES = ["fakebot", "fakehome", "fakeline"]


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compat = load_tool("pack_compat_check")
lint = load_tool("lint_arch")


# ── a made-up pack layer ─────────────────────────────────────────────────────────────────────────────────────────────
BASE0 = {"pack": {"schema": 2, "name": "base0", "source": "made up"},
         "phrases": {"q_confirm": {"en": "Is that right?"}, "q_stop": {"en": "Shall I stop?"}}}
HOME = {"pack": {"schema": 2, "name": "home", "source": "made up"},
        "goal_kinds": {"on": {"rules": "home.on", "needs": ["Pick", "Place"]}, "in": {"rules": "home.in"}, "go": {"rules": "home.go"}},
        "predicates": {"holding": {"eval": "home.holding", "args": ["object?"], "scopes": ["tree", "effect", "pre", "goal"]},
                       "at": {"eval": "home.at", "args": ["place"], "scopes": ["tree", "pre", "goal"]}},
        "actions": {"Pick": {"class": "grasp"}, "Place": {"class": "release"}},
        "verifiers": {"home.look": {"callable": "home.look"}},
        "recovery": {"policies": {"home.again": {"callable": "home.again"}}, "by_action": {"Pick": "home.again"}},
        "phrases": {"q_confirm": {"en": "Shall I do that?"}, "rb_on": {"en": "put the {object} on the {place}"}}}
INDEX = {"root": "base0", "packs": {"base0": {"dir": "base0"}, "home": {"dir": "home"}}, "schema_1_base": "home",
         "schema_1_lift": {"options": "planner.options", "trees": "planner.options.tree_option.trees", "recovery.codes": "adapter.recovery_overrides"},
         "schema_1_keep": ["planner.action_roles", "limits", "geometry"]}


def write_layer(folder: Path, index=INDEX, **packs) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.yaml").write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    for name, sections in ({"base0": BASE0, "home": HOME} | packs).items():
        (folder / name).mkdir(exist_ok=True)
        for key, value in sections.items():
            (folder / name / f"{key}.yaml").write_text(yaml.safe_dump({key: value}, sort_keys=False), encoding="utf-8")
    return folder


@pytest.fixture
def layer(tmp_path):
    return write_layer(tmp_path / "layer")


@pytest.fixture
def reg():
    """A registry with the callables the made-up ``home`` pack names."""
    r = Registry()
    rules = types.SimpleNamespace(reached=lambda goal, steps, world, ctx: True, holds=lambda goal, belief, ctx: True,
                                  missing=lambda goal, steps, world, ctx: None)
    for kind in ("on", "in", "go"):
        r.register("goal.rules", f"home.{kind}", rules, module="tests.home")
    for name in ("holding", "at"):
        r.register("predicate", f"home.{name}", lambda belief, args: None, module="tests.home")
    r.register("verifier.goal", "home.look", lambda goal, ctx: iter(()), module="tests.home")
    r.register("recovery", "home.again", lambda run, step, reason, rule, ctx: iter(()), module="tests.home")
    return r


def toy(**top) -> Manifest:
    skills = [{"name": n.lower(), "action": n} for n in ("MoveTo", "Find", "Pick", "Place", "Fasten")]
    return Manifest.from_dict({"robot": "toy", "skills": skills, **top})


def line_pack(**sections) -> dict:
    return {"schema": 2, "name": "line", "extends": "home", "source": "made up", **sections}


def stop_of(layer, reg, **sections) -> str:
    with pytest.raises(PackError) as e:
        resolve(toy(), raw=line_pack(**sections), registry=reg, root=layer)
    return "\n".join(e.value.errors)


# ── the registry ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_name_is_registered_once_in_a_known_namespace_and_found_by_name():
    r = Registry()
    fn = lambda belief, args: True                                                      # noqa: E731
    r.register("predicate", "line.clear", fn)
    assert r.get("predicate", "line.clear") is fn and r.has("predicate", "line.clear") and r.names("predicate") == ["line.clear"]
    assert r.get("predicate", "line.other") is None and r.get("recovery", "line.clear") is None
    with pytest.raises(ValueError, match="registered twice"):
        r.register("predicate", "line.clear", fn)
    with pytest.raises(ValueError, match="unknown namespace"):
        r.register("answer", "line.read", fn)
    with pytest.raises(ValueError, match="<pack>.<name>"):
        r.register("predicate", "clear", fn)


def test_register_is_also_a_decorator_that_returns_the_function():
    r = Registry()

    @r.register("verifier.goal", "line.look")
    def look(goal, ctx):
        yield True, "seen", "camera"

    assert r.get("verifier.goal", "line.look") is look and r.names() == [("verifier.goal", "line.look")]


def test_the_number_of_parameters_is_checked_at_registration():
    r = Registry()
    with pytest.raises(ValueError, match="takes 1 to 1 parameter"):
        r.register("predicate", "line.one", lambda belief: True)
    with pytest.raises(ValueError, match="not callable"):
        r.register("predicate", "line.text", "holding")
    r.register("predicate", "line.more", lambda belief, args, strict=False: True)       # an optional third one is fine
    r.register("recovery", "line.any", lambda *a: iter(()))


def test_a_goal_rules_object_needs_reached_holds_and_missing_and_may_come_without_phrase():
    r = Registry()
    full = types.SimpleNamespace(reached=lambda g, s, w, c: True, holds=lambda g, b, c: True, missing=lambda g, s, w, c: None)
    r.register("goal.rules", "line.mount", full)
    with pytest.raises(ValueError, match="method missing is missing"):
        r.register("goal.rules", "line.half", types.SimpleNamespace(reached=full.reached, holds=full.holds))
    with pytest.raises(ValueError, match="method phrase takes"):
        r.register("goal.rules", "line.bad", types.SimpleNamespace(**vars(full), phrase=lambda goal: "x"))


def test_a_pack_registers_only_its_own_names_whichever_comes_first_the_claim_or_the_name():
    fn = lambda belief, args: True                                                      # noqa: E731
    r = Registry()
    r.claim("home", "packs.home.callables")
    r.register("predicate", "home.at", fn, module="packs.home.callables.predicates")
    with pytest.raises(ValueError, match="registered by 'packs.home.callables' only"):
        r.register("predicate", "home.holding", fn, module="line_robot.callables")      # another pack takes a standard name
    with pytest.raises(ValueError, match="registers only home"):
        r.register("predicate", "line.at", fn, module="packs.home.callables.extra")     # the home module registers a foreign name
    late = Registry()
    late.register("predicate", "home.at", fn, module="line_robot.callables")
    with pytest.raises(ValueError, match="registered by 'packs.home.callables' only"):
        late.claim("home", "packs.home.callables")
    with pytest.raises(ValueError, match="its callables module is"):
        r.claim("home", "elsewhere.callables")


# ── the shipped packs ────────────────────────────────────────────────────────────────────────────────────────────────
def test_every_shipped_pack_parses_has_its_own_name_and_keeps_its_callables_in_the_pack_layer():
    idx = loader.index()
    decls = [loader.shipped(n) for n in idx["packs"]]
    assert idx["root"] in idx["packs"] and idx["schema_1_base"] in idx["packs"]
    assert len({d.name for d in decls}) == len(decls) and all(d.schema == 2 for d in decls)
    assert all(d.callables.startswith(loader.PACKS + ".") for d in decls if d.callables)
    assert all(d.label() is not None for d in decls), "a shipped pack says where its facts come from"


def test_a_robot_that_declares_nothing_gets_the_core_pack_then_the_schema_1_base():
    idx = loader.index()
    p = loader.default_pack()
    assert p.schema == 1 and p.header() == {"schema": 1, "name": "", "version": "", "extends": idx["schema_1_base"]}
    assert p.chain == (loader.shipped(idx["root"]).name, loader.shipped(idx["schema_1_base"]).name, lift.NAME)
    assert set(p.tables) == set(loader.schema.TABLES) and p.kept == {}


def test_every_callable_the_shipped_packs_name_is_registered():
    assert [x for x in loader.default_pack().problems if x.kind == "callable"] == []


def test_the_engine_reaches_the_pack_layer_by_name_only():
    found = lint.check_layers(str(ROOT), lint.load_rules())
    assert [k for k in found if "robot_agent.packs" in k or k.split(" -> ")[0].endswith(("robot_agent/packs", ".yaml"))] == []
    assert [k for k in found if k.startswith("layer: robot_agent/packs")] == []
    rules = lint.load_rules()
    assert rules["layers"]["packs"] == ["robot_agent/packs"] and rules["may_import"]["packs"] == ["contract", "engine"]
    assert "packs" not in rules["may_import"]["engine"] + rules["may_import"]["contract"]


def test_the_package_data_of_an_install_covers_every_data_file_of_the_pack_layer():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    patterns = json.loads(re.search(r'^"robot_agent\.packs" = (\[.*\])$', text, re.M).group(1))
    packs = ROOT / "robot_agent" / "packs"
    covered = {f for pat in patterns for f in packs.glob(pat)}
    data = {f for f in packs.rglob("*") if f.is_file() and f.suffix in (".yaml", ".txt")}
    assert data and data <= covered, sorted(str(f) for f in data - covered)
    assert all((d / "__init__.py").is_file() for d in packs.iterdir() if d.is_dir() and d.name != "__pycache__"), "a pack folder is a package"


def test_the_new_modules_stay_small():
    files = [ROOT / "robot_agent/contract/pack.py", ROOT / "robot_agent/contract/pack_files.py"] + \
        [ROOT / "robot_agent/core/pack" / f"{n}.py" for n in ("__init__", "loader", "registry", "check", "lift", "show")]
    sizes = lint.measure(str(ROOT), {"size": [{"path": "robot_agent", "max_function": 50, "max_file": 300}]})
    mine = {k: v for k, v in sizes.items() if any(str(f.relative_to(ROOT)) in k for f in files)}
    assert len([k for k in mine if k.startswith("file:")]) == len(files)
    assert {k: v for k, v in mine.items() if v > (300 if k.startswith("file:") else 50)} == {}


# ── the chain and the merge ──────────────────────────────────────────────────────────────────────────────────────────
def test_the_chain_is_the_root_pack_then_the_base_then_the_robot(layer, reg):
    p = resolve(toy(), raw=line_pack(), registry=reg, root=layer)
    assert p.chain == ("base0", "home", "line") and p.header() == {"schema": 2, "name": "line", "version": "1.0", "extends": "home"}
    alone = resolve(toy(), raw={"schema": 2, "name": "line", "extends": None, "assumed": True}, registry=reg, root=layer)
    assert alone.chain == ("base0", "line") and alone.table("goal_kinds") == {} and list(alone.table("phrases")) == ["q_confirm", "q_stop"]


def test_an_entry_of_a_later_pack_replaces_the_base_entry_whole_and_keeps_its_place(layer, reg):
    p = resolve(toy(), raw=line_pack(goal_kinds={"in": {"rules": "home.go"}, "mount": {"rules": "home.on"}},
                                     phrases={"q_stop": {"en": "Stop the line?"}}), registry=reg, root=layer)
    assert list(p.table("goal_kinds")) == ["on", "in", "go", "mount"], "a replaced kind keeps its place, a new one comes last"
    assert p.entry("goal_kinds", "in") == {"rules": "home.go"} and p.origin["goal_kinds"] == {"on": "home", "in": "line", "go": "home", "mount": "line"}
    assert p.entry("phrases", "q_confirm") == {"en": "Shall I do that?"} and p.origin["phrases"]["q_confirm"] == "home"
    assert p.entry("phrases", "q_stop") == {"en": "Stop the line?"} and p.origin["phrases"]["rb_on"] == "home"
    assert p.value("goal_kinds", "on", "needs") == ["Pick", "Place"] and p.value("goal_kinds", "in", "needs") is None, "no deep merge"
    assert p.value("goal_kinds", "in", "unknown_is") == "belief" and p.value("goal_kinds", "none", "unknown_is") == "belief"


def test_remove_deletes_names_of_the_base_and_an_unknown_name_is_recorded_not_fatal(layer, reg):
    p = resolve(toy(), raw=line_pack(remove={"goal_kinds": ["in"], "recovery.by_action": ["Pick"]}), registry=reg, root=layer)
    assert list(p.table("goal_kinds")) == ["on", "go"] and "in" not in p.origin["goal_kinds"] and p.table("recovery.by_action") == {}
    assert p.problems == []
    typo = resolve(toy(), raw=line_pack(remove={"goal_kinds": ["inn"]}), registry=reg, root=layer)
    assert typo.problems == [check.Problem("line", "remove", "remove.goal_kinds", "'inn' is not an entry of the base")]
    assert list(typo.table("goal_kinds")) == ["on", "in", "go"]


def test_a_resolved_pack_is_a_copy_that_a_reader_cannot_change_for_the_next_one(layer, reg):
    first = resolve(toy(), raw=line_pack(), registry=reg, root=layer)
    first.entry("goal_kinds", "on")["needs"].append("Fly")
    assert resolve(toy(), raw=line_pack(), registry=reg, root=layer).entry("goal_kinds", "on")["needs"] == ["Pick", "Place"]
    assert first.named("goal.rules", "home.on") is reg.get("goal.rules", "home.on") and first.named("predicate", None) is None


def test_a_base_that_is_not_shipped_a_shipped_name_and_a_wrong_section_stop_the_load(layer, reg):
    with pytest.raises(PackError, match="no pack named 'factory' ships with the engine"):
        resolve(toy(), raw={**line_pack(), "extends": "factory"}, registry=reg, root=layer)
    with pytest.raises(PackError, match="'home' is the name of a pack that ships with the engine"):
        resolve(toy(), raw={**line_pack(), "name": "home"}, registry=reg, root=layer)
    with pytest.raises(PackError, match="pack.schema"):
        resolve(toy(), raw={"schema": 7, "name": "line"}, registry=reg, root=layer)


def test_a_pack_layer_whose_packs_extend_each_other_in_a_circle_is_an_error(tmp_path, reg):
    a = {"pack": {"schema": 2, "name": "a", "extends": "b", "assumed": True}}
    b = {"pack": {"schema": 2, "name": "b", "extends": "a", "assumed": True}}
    root = write_layer(tmp_path / "circle", {"root": "base0", "packs": {"base0": {}, "a": {}, "b": {}}, "schema_1_base": "a"}, a=a, b=b)
    with pytest.raises(PackError, match="extends itself"):
        resolve(toy(), registry=reg, root=root)


# ── the result check of a schema-2 pack ──────────────────────────────────────────────────────────────────────────────
def test_a_schema_2_pack_that_names_only_what_exists_resolves(layer, reg):
    reg.register("verifier.step", "line.seated_step", lambda step, result, ctx: [], module="tests.line")
    p = resolve(toy(), raw=line_pack(
        goal_kinds={"mount": {"holds": ["seated(?object, ?to)", "holding(*)"], "needs": ["Fasten", ["Pick", "Place"]], "observe": "home.look"}},
        predicates={"seated": {"kind": "fact", "args": ["object", "place"], "scopes": ["tree", "goal", "effect"]}},
        actions={"Fasten": {"rules": "declared", "pre": ["at(?place)"], "effects": {"add": ["seated(?object)"]},
                            "post": ["line.seated"], "recovery": "tree"}},
        verifiers={"line.seated": {"callable": "line.seated_step"}},
        recovery={"by_kind": {"mount": "home.again"}, "by_action": {"Fasten": "stop", "Find": "planner"}},
        trees={"mount": {"tree": ["sequence", ["act", "Fasten", "{to}"], ["cond", "seated", "{object}", "{to}"], ["ask", "q_stop"]]}},
        questions={"far": {"phrase": "q_stop", "answer": {"type": "yes_no"}}}, fleet={"kinds": {"mount": {"footprint": {"objects": ["object"]}}}},
    ), registry=reg, root=layer)
    assert p.problems == [] and p.origin["goal_kinds"]["mount"] == "line" and p.questions_default_kind is None


@pytest.mark.parametrize("sections, want", [
    ({"goal_kinds": {"mount": {"rules": "line.mount"}}}, "pack.goal_kinds.mount.rules: 'line.mount' is not registered in the namespace goal.rules"),
    ({"predicates": {"clear": {"eval": "home.look", "scopes": ["tree"]}}}, "pack.predicates.clear.eval: 'home.look' is not registered in the namespace predicate"),
    ({"goal_kinds": {"mount": {"rules": "home.on", "observe": "line.look"}}}, "pack.goal_kinds.mount.observe: the verifier 'line.look' is not declared"),
    ({"goal_kinds": {"mount": {"rules": "home.on", "needs": [["Find", "Pick"], "Swim"]}}}, "pack.goal_kinds.mount.needs[1]: the action 'Swim' is not declared"),
    ({"goal_kinds": {"mount": {"rules": "home.on", "needs": [["Pick", "Fly"]]}}}, "pack.goal_kinds.mount.needs[0]: the action 'Fly' is not declared"),
    ({"goal_kinds": {"mount": {"holds": ["seated(?object)"]}}}, "pack.goal_kinds.mount.holds[0]: the predicate 'seated' is not declared"),
    ({"goal_kinds": {"mount": {"holds": ["at(?a, ?b)"]}}}, "the predicate 'at' takes 1 argument(s) (place), got 2"),
    ({"goal_kinds": {"mount": {"holds": ["at(*)"]}}}, "the argument 'place' of the predicate 'at' may not be the wildcard"),
    ({"actions": {"Fasten": {"rules": "declared", "effects": {"add": ["at(?place)"]}}}}, "the predicate 'at' may not be used as `effect`"),
    ({"actions": {"Fasten": {"recovery": "line.retry", "post": ["line.check"]}}}, "pack.actions.Fasten.recovery: the policy 'line.retry' is not declared"),
    ({"recovery": {"by_action": {"Swim": "stop"}}}, "pack.recovery.by_action.Swim: the action 'Swim' is not declared"),
    ({"recovery": {"by_kind": {"mount": "planner"}}}, "pack.recovery.by_kind.mount: the goal kind 'mount' is not declared"),
    ({"trees": {"go": {"tree": ["fallback", ["cond", "holding", "*"], ["act", "Swim", "{to}"]]}}}, "may not be the wildcard * here"),
    ({"trees": {"go": [{"tree": ["retry", 2, ["act", "Swim", "{to}"]]}]}}, "pack.trees.go.tree: the action 'Swim' is not declared"),
    ({"questions": {"far": {"phrase": "q_far", "answer": {"type": "yes_no"}}}}, "pack.questions.far.phrase: the phrase 'q_far' is not declared"),
    ({"questions": {"far": {"answer": {"type": "number"}}}}, "pack.questions.far.answer.type: the value 'number' is not built yet"),
    ({"questions": {"far": {"answer": {"type": "yes_no", "unclear": "model"}}}}, "pack.questions.far.answer.unclear: the value 'model' is not built yet"),
    ({"recovery": {"policies": {"line.retry": {"ladder": [{"do": "retry"}]}}}}, "pack.recovery.policies.line.retry.ladder: this field is not built yet"),
    ({"questions_default_kind": "name"}, "pack.questions_default_kind: the value `name` is not built yet"),
])
def test_a_schema_2_pack_that_names_what_nobody_declares_stops_the_load_with_the_path(layer, reg, sections, want):
    assert want in stop_of(layer, reg, **sections)


def test_a_phrase_of_the_robots_own_words_and_a_skill_action_count_as_declared(layer, reg):
    adapter = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"q_far": {"en": "Too far?"}}))
    p = resolve(toy(), adapter, raw=line_pack(questions={"far": {"phrase": "q_far"}}, recovery={"by_action": {"Fasten": "stop"}}),
                registry=reg, root=layer)
    assert p.problems == []
    assert "the phrase 'q_far' is not declared" in stop_of(layer, reg, questions={"far": {"phrase": "q_far"}})


def test_a_problem_of_a_shipped_pack_is_recorded_and_never_stops_a_schema_1_robot(tmp_path):
    root = write_layer(tmp_path / "layer")
    p = resolve(toy(), registry=Registry(), root=root)                                  # nothing of `home` is registered
    assert p.schema == 1 and {x.kind for x in p.problems} == {"callable"} and {x.pack for x in p.problems} == {"home"}
    assert check.Problem("home", "callable", "goal_kinds.on.rules", "'home.on' is not registered in the namespace goal.rules") in p.problems


# ── the callables module of a pack ───────────────────────────────────────────────────────────────────────────────────
def robot_package(tmp_path, monkeypatch, body: str) -> Manifest:
    cfg = tmp_path / "linebot_s02" / "configs"
    cfg.mkdir(parents=True)
    for d in (cfg.parent, cfg):
        (d / "__init__.py").write_text("", encoding="utf-8")
    (cfg / "pack_callables.py").write_text(body, encoding="utf-8")
    (cfg / "manifest.yaml").write_text("robot: linebot\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [m for m in sys.modules if m.startswith("linebot_s02")]:
        monkeypatch.delitem(sys.modules, name)
    return Manifest.from_yaml(cfg / "manifest.yaml")


def test_a_packs_callables_module_is_imported_and_registers_under_the_packs_name(tmp_path, monkeypatch, layer, reg):
    m = robot_package(tmp_path, monkeypatch, "from robot_agent.core.pack import Registry\nREG = None\n"
                      "def setup(reg):\n    reg.register('predicate', 'line.clear', clear)\n"
                      "def clear(belief, args):\n    return True\n")
    raw = line_pack(callables="linebot_s02.configs.pack_callables", predicates={"clear": {"eval": "line.clear", "scopes": ["tree"]}})
    assert "'line.clear' is not registered" in "\n".join(pytest.raises(PackError, resolve, m, raw=raw, registry=reg, root=layer).value.errors)
    sys.modules["linebot_s02.configs.pack_callables"].setup(reg)
    assert resolve(m, raw=raw, registry=reg, root=layer).named("predicate", "line.clear")(None, []) is True
    with pytest.raises(ValueError, match="registered by 'linebot_s02.configs.pack_callables' only"):
        reg.register("predicate", "line.other", lambda belief, args: True, module="elsewhere")


def test_a_pack_may_name_no_module_outside_the_robots_own_package(tmp_path, monkeypatch, layer, reg):
    m = robot_package(tmp_path, monkeypatch, "")
    for module in ("json", "robot_agent.core.pack.registry", "no_such_module_s02"):
        with pytest.raises(PackError, match="is not a module of the robot's own package"):
            resolve(m, raw=line_pack(callables=module), registry=reg, root=layer)
    with pytest.raises(PackError, match="is not a module of the robot's own package"):
        resolve(toy(), raw=line_pack(callables="linebot_s02.configs.pack_callables"), registry=reg, root=layer)   # no manifest file


# ── schema 1: the lift ───────────────────────────────────────────────────────────────────────────────────────────────
PLANNER = {"llm_budget": 22, "action_roles": {"move": "MoveTo"},
           "options": {"tree_option": {"trees": {"on": {"tree": ["act", "Pick", "{object}"]}, "go": [{"tree": ["act", "MoveTo", "{to}"], "assumed": True}]},
                                       "menu": {"retry": "q_retry"}, "source": "made up"},
                       "look_first": {"enabled": False}}}


def test_a_schema_1_manifest_is_lifted_as_written_nothing_added_labelled_or_guessed(layer, reg):
    m = toy(planner=PLANNER, limits={"payload_kg": 2}, geometry={"reach_m": 0.7})
    adapter = types.SimpleNamespace(recovery_overrides={FailureCode.DROPPED: RecoveryRule(Recovery.STOP, report=True), "blocked": "ask_help"})
    p = resolve(m, adapter, registry=reg, root=layer)
    assert p.schema == 1 and p.chain == ("base0", "home", "manifest") and p.extends == "home"
    assert p.table("options") == PLANNER["options"] and p.table("trees") == PLANNER["options"]["tree_option"]["trees"]
    assert p.origin["trees"] == {"on": "manifest", "go": "manifest"} and "source" not in p.entry("trees", "on"), "an unlabelled tree stays unlabelled"
    assert p.kept == {"planner.action_roles": {"move": "MoveTo"}, "limits": {"payload_kg": 2}, "geometry": {"reach_m": 0.7}}
    assert p.table("recovery.codes")["blocked"] == "ask_help" and p.table("recovery.codes")["dropped"]["action"] == "stop"
    assert RecoveryPolicy(p.table("recovery.codes")).table == policy_for(adapter).table
    assert list(p.table("goal_kinds")) == ["on", "in", "go"], "the base's tables are there, the manifest adds none"
    p.table("trees")["on"]["tree"].append("changed")
    assert PLANNER["options"]["tree_option"]["trees"]["on"]["tree"] == ["act", "Pick", "{object}"], "the lift copies"


def test_lifted_data_is_never_checked_so_a_schema_1_manifest_always_loads(layer, reg):
    odd = toy(planner={"options": {"tree_option": {"trees": {"on": {"tree": ["act", "Swim", "x"], "unknown": 1}, "fly": "not a tree"}}, "broken": 3}})
    p = resolve(odd, types.SimpleNamespace(recovery_overrides=None), registry=reg, root=layer)
    assert p.problems == [] and p.table("trees")["fly"] == "not a tree" and p.table("options")["broken"] == 3
    assert resolve(types.SimpleNamespace(), None, registry=reg, root=layer).chain == ("base0", "home", "manifest")
    assert lift.lift(None, None, None, ())[0].tables == {} and lift.dig(odd, None, "planner.options.none.deeper") is None


# ── the robot view ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_robot_view_resolves_its_pack_on_first_use_and_keeps_it():
    view = RobotView.load("fakeline")
    assert view.__dict__.get("_pack") is None, "building the view resolves nothing"
    pack = view.pack
    assert pack is view.pack and pack.schema == 1 and pack.chain[-1] == lift.NAME
    assert RobotView.__new__(RobotView).__dict__.get("_pack") is None


def test_a_manifest_that_declares_a_pack_is_resolved_when_the_view_is_built_and_a_wrong_one_stops_there():
    good = {"schema": 2, "name": "line_s02", "extends": None, "assumed": True, "questions_default_kind": "line.question"}
    view = RobotView(types.SimpleNamespace(manifest=Manifest.from_dict({"robot": "toy", "pack": good})))
    assert view.__dict__["_pack"].name == "line_s02" and view.pack.questions_default_kind == "line.question"
    bad = {"schema": 2, "name": "line_s02", "extends": "no_such_base_s02", "assumed": True}
    with pytest.raises(PackError, match="no pack named 'no_such_base_s02'"):
        RobotView(types.SimpleNamespace(manifest=Manifest.from_dict({"robot": "toy", "pack": bad})))


def test_use_pack_replaces_the_declaration_and_a_wrong_one_leaves_the_pack_as_it_was():
    view = RobotView.load("fakeline")
    raw = {"schema": 2, "name": "line_s02", "extends": None, "assumed": True, "questions_default_kind": "line.question",
           "constants": {"tries": {"value": 3}}}
    pack = view.use_pack(raw)
    assert pack is view.pack and (pack.schema, pack.name, pack.extends) == (2, "line_s02", None)
    assert pack.entry("constants", "tries") == {"value": 3} and pack.questions_default_kind == "line.question"
    with pytest.raises(PackError):
        view.use_pack({"schema": 2, "name": "line_s02", "constants": {"tries": 3}})
    assert view.pack is pack
    assert view.use_pack(None).schema == 1 and view.pack.chain[-1] == lift.NAME


@pytest.mark.parametrize("pkg", FAKES)
def test_resolving_the_pack_changes_nothing_the_robot_answers_today(pkg, env):
    adapter = load_adapter(pkg)
    view, plain = RobotView(adapter), RobotView(adapter)
    before = (manifest_of(view, env, "site"), sorted(view.plan_actions), view.capabilities(), view.robot_description(),
              view.manifest.to_dict(), dict(view.policy.table))
    first, again = view.pack, resolve(adapter.manifest, adapter)
    assert (first.header(), first.chain, first.tables, first.kept) == (again.header(), again.chain, again.tables, again.kept)
    after = (manifest_of(view, env, "site"), sorted(view.plan_actions), view.capabilities(), view.robot_description(),
             view.manifest.to_dict(), dict(view.policy.table))
    assert before == after == (manifest_of(plain, env, "site"), sorted(plain.plan_actions), plain.capabilities(),
                               plain.robot_description(), plain.manifest.to_dict(), dict(plain.policy.table))
    assert first.table("recovery.codes") == {} or RecoveryPolicy(first.table("recovery.codes")).table == view.policy.table


# ── the tools ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_show_prints_the_resolved_pack_of_a_package_and_of_a_path(capsys):
    assert show.main(["fakehome"]) == 0
    out = capsys.readouterr().out
    assert "pack: schema 1" in out and "chain: " in out and "kept from the manifest (planner.action_roles)" in out
    assert show.main([str(ROOT / "tests" / "fakeline"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pack"]["schema"] == 1 and data["chain"][-1] == lift.NAME and data["problems"] == []
    assert show.main([]) == 2 and show.main(["no_such_robot_s02"]) == 1


@pytest.mark.parametrize("pkg", FAKES)
def test_the_made_up_robots_resolve_to_the_vocabulary_of_the_old_code(pkg):
    report = compat.check(pkg)
    assert report["differences"] == [], report
    assert report["pack"]["schema"] == 1 and report["loaded"] == "adapter and manifest"
    assert compat.check(str(ROOT / "tests" / pkg))["differences"] == []


def test_the_frozen_tables_of_the_compat_tool_are_the_old_constants():
    from robot_agent.core.planning import goals, speech
    from robot_agent.options import bt_goal
    ref = compat.reference()
    kind = lambda k: {"needs": [list(n) if isinstance(n, tuple) else n for n in k.needs], "prompt": k.prompt}   # noqa: E731
    assert ref["goal_kinds"] == {n: kind(k) for n, k in goals.GOAL_KINDS.items()} and list(ref["goal_kinds"]) == list(goals.GOAL_KINDS)
    assert ref["optional_kinds"] == {n: kind(k) for n, k in goals.OPTIONAL_KINDS.items()}
    assert (ref["manipulation"], ref["template_kinds"]) == (list(goals.MANIPULATION), list(goals.TEMPLATE_KINDS))
    assert ref["conditions"] == list(bt_goal.CONDITIONS) and ref["core_phrases"] == dict(speech.CORE_PHRASES)
    assert len(ref["core_phrases"]) == 62 and ref["by_action"] == ["Find", "Pick", "MoveTo", "Open", "Place", "PutIn"]
    assert compat.main(["--record"]) == 2, "the frozen copy is recorded once and never overwritten"


def test_the_compat_tool_reports_a_vocabulary_that_differs_from_the_old_code(monkeypatch, capsys):
    from robot_agent.core.planning import goals
    monkeypatch.setattr(goals, "kinds_for", lambda actions, optional=(): ["on"])
    report = compat.check("fakeline")
    assert any(d.startswith("kinds: the live code gives ['on']") for d in report["differences"]), report
    assert compat.main(["fakeline"]) == 1 and "DIFFERENCE kinds" in capsys.readouterr().out


def test_the_compat_tool_compares_a_moved_table_with_the_frozen_one():
    ref = compat.reference()
    kinds = {k: {"rules": f"std.{k}", "needs": v["needs"]} for k, v in ref["goal_kinds"].items()}
    tables = {"goal_kinds": kinds, "predicates": {c: {"scopes": ["tree"]} for c in ref["conditions"]}}
    same = loader.Pack(chain=("core", "std", "manifest"), tables=tables, origin={t: {n: "std" for n in e} for t, e in tables.items()})
    diffs, notes = compat.moved_tables(ref, same)
    assert diffs == [] and len(notes) == 2, "two tables moved and equal, two not moved yet"
    kinds["in"] = {"rules": "std.in", "needs": ["Pick", "Place"]}
    tables["predicates"]["seen"] = {"scopes": ["tree"]}
    same.origin["predicates"]["seen"] = "std"
    diffs, _notes = compat.moved_tables(ref, same)
    assert len(diffs) == 2 and diffs[0].startswith("goal kinds (needs)") and diffs[1].startswith("tree conditions")
