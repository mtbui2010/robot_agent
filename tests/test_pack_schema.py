"""The pack schema (robot_agent/contract/pack.py, pack_files.py; Step S02 of docs/proposals/generalize_core_design.md): a
``pack:`` section parses whole, every bad field is reported by its path in one error, a manifest keeps the section as written
and a manifest without one is unchanged (schema 1). Made-up data only."""

import textwrap

import pytest
import yaml

from robot_agent.contract import check_manifest as cm
from robot_agent.contract import pack as P
from robot_agent.contract.manifest import Manifest, ManifestError
from robot_agent.contract.pack_files import read

# a made-up assembly pack that uses every section and the fields later steps read (holds, unknown_is, remove, fleet ...)
FULL = {
    "schema": 2, "name": "line", "version": "1.0", "extends": "household", "source": "made up for the tests",
    "remove": {"goal_kinds": ["in", "open"], "questions": ["help_pick"]}, "questions_default_kind": "line.question",
    "goal_kinds": {"mount": {
        "slots": {"object": {"type": "object", "required": True}, "to": {"type": "place", "required": True}},
        "needs": ["Measure", ["Seat", "Press"]], "understood": "capable", "planned_by": "tree", "prompt": '"mount" = fix a part.',
        "holds": ["seated(?object, ?to)", "hand_free()"], "observe": "line.part_seated", "unknown_is": "failure",
        "phrase": "rb_mount", "subgoal": "mount the {object} on {to}", "conflicts": "none", "limits": ["payload"]}},
    "predicates": {"seated": {"kind": "fact", "args": ["object", "place"], "scopes": ["tree", "pre", "goal"]},
                   "clear": {"eval": "line.clear", "args": ["place?"], "scopes": ["tree", "effect"], "assumed": True}},
    "actions": {"Seat": {
        "class": "release", "arg": {"name": "station", "type": "place"},
        "args": {"arm": {"type": "enum", "values": ["left", "right"], "default": "left"},
                 "dz_mm": {"type": "number", "min": -10, "max": 10, "unit": "mm", "assumed": True}},
        "plan_step": True, "rules": "declared", "pre": ["holding(*)", "not seated(?held, ?station)"],
        "effects": {"add": ["seated(?held, ?station)"], "del": ["clear(?station)"]}, "releases": True,
        "post": ["line.part_seated_step"], "recovery": "line.seat_retry", "errors": ["unknown", {"code": "unknown", "subcode": "line.off"}],
        "duration_s": {"typical": 20, "max": 60}, "verb": "verb.Seat", "ability": "ability.Seat", "describe": "describe.Seat"},
        "Measure": {"arg": {"name": "station", "type": "place"}, "releases": "from_effects"}},
    "verifiers": {"line.part_seated": {"kind": "sensor", "skill": "seat_look", "expect": True, "params": {"looks": 3}},
                  "line.part_seated_step": {"callable": "line.seated_step", "tolerance_mm": 8},
                  "end_of_run": {"return_to_last_go": True}},
    "recovery": {"codes": {"dropped": {"action": "stop", "report": True}, "blocked": "ask_help"},
                 "policies": {"line.seat_retry": {"ladder": [{"do": "retry", "times": 1, "with": {"dz_mm": 2}}, {"do": "stop"}]},
                              "line.search": {"callable": "line.search", "floor_head": "floor"}},
                 "by_action": {"Seat": "line.seat_retry"}, "by_kind": {"mount": "tree"},
                 "failure_patterns": [{"pattern": "no part", "code": "absent", "language": "en"}]},
    "questions": {"part_far": {"phrase": "q_part_far", "kind": "line.decision", "silence_retries": 1, "answer": {
        "type": "choice", "keep_text": True, "unclear": "reask", "reasks": 1, "extract": {"dz_mm": {"type": "number", "unit": "mm"}},
        "choices": {"use": {"words": {"en": ["use it", "go ahead"]}, "implies": "yes"}, "leave": {"lists": ["dialog.stop_words"], "implies": "no"}}}},
                  "help": {"phrase": None, "kind": None, "answer": {"type": "yes_no", "then": "place"}}},
    "phrases": {"q_part_far": {"en": "The part is {mm} mm off. Use it?", "slots": ["mm"]}, "prompt.understand": {"file": "prompts/understand.en.txt"}},
    "trees": {"mount": {"tree": ["sequence", ["act", "Measure", "{to}"], ["retry", 2, ["act", "Seat", "{to}", {"arm": "left"}]]],
                        "events": True, "ladder": False},
              "go": [{"tree": ["act", "MoveTo", "{to}"], "skip_if": "at"}, {"tree": ["act", "MoveTo", "{to}"]}]},
    "constants": {"llm_budget": {"value": 22}, "seat_tol_mm": {"value": 8, "assumed": True}},
    "options": {"look_then_ask": {"enabled": False}},
    "fleet": {"kinds": {"mount": {"footprint": {"objects": ["object"], "places": ["to"]}}}},
}


def errors_of(raw) -> list:
    with pytest.raises(P.PackError) as e:
        P.parse(raw)
    return e.value.errors


def with_kind(**fields) -> dict:
    return {"schema": 2, "name": "line", "assumed": True, "goal_kinds": {"mount": fields}}


# ── the whole schema ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_no_pack_section_is_schema_1_and_declares_nothing():
    d = P.parse(None)
    assert (d.schema, d.name, d.extends, d.tables, d.remove, d.callables) == (1, "", None, {}, {}, None)
    assert d.header() == {"schema": 1, "name": "", "version": "1.0", "extends": None}


def test_a_pack_that_uses_every_section_parses_and_keeps_its_entries_as_written():
    d = P.parse(FULL)
    assert d.header() == {"schema": 2, "name": "line", "version": "1.0", "extends": "household"}
    assert set(d.tables) == set(P.TABLES), "every table of the schema is used by the example"
    assert d.tables["goal_kinds"]["mount"] is FULL["goal_kinds"]["mount"]
    assert d.tables["recovery.by_kind"] == {"mount": "tree"} and d.tables["fleet.kinds"] == FULL["fleet"]["kinds"]
    assert d.remove == {"goal_kinds": ["in", "open"], "questions": ["help_pick"]}
    assert d.questions_default_kind == "line.question" and d.failure_patterns == FULL["recovery"]["failure_patterns"]


def test_the_schema_has_the_eleven_sections_and_the_goal_kind_fields_of_the_design():
    assert P.SECTIONS == ("goal_kinds", "predicates", "actions", "verifiers", "recovery", "questions", "phrases", "trees",
                          "constants", "options", "fleet")
    assert set(P.TABLES["goal_kinds"]) == {"slots", "needs", "understood", "prompt", "planned_by", "rules", "holds", "observe",
                                           "unknown_is", "phrase", "subgoal", "conflicts", "limits"}
    assert P.SUPPORTED == (1, 2)


def test_an_absent_field_has_the_designs_default_or_none():
    assert P.default("goal_kinds", "unknown_is") == "belief" and P.default("goal_kinds", "conflicts") == "default"
    assert P.default("goal_kinds", "planned_by") == "planner" and P.default("goal_kinds", "understood") == "capable"
    assert P.default("actions", "releases") == "from_effects" and P.default("actions", "plan_step") is True
    assert P.default("goal_kinds", "observe") is None and P.default("questions", "kind") is None


def test_a_label_is_the_entrys_own_else_the_packs_default():
    d = P.parse(FULL)
    assert d.label(FULL["predicates"]["clear"]) == {"assumed": True}
    assert d.label(FULL["predicates"]["seated"]) == {"source": "made up for the tests"}
    assert P.PackDecl().label({}) is None


# ── errors, by path, all at once ─────────────────────────────────────────────────────────────────────────────────────
def test_every_bad_field_is_reported_by_its_path_in_one_error():
    errs = errors_of({"schema": 2, "name": "Line", "extends": 3, "surprise": 1, "remove": {"kinds": ["x"]},
                      "goal_kinds": {"mount": {"rules": "line.mount", "unkown_is": "belief", "planned_by": "magic"}},
                      "actions": {"Seat": {"args": {"arm": {"type": "enum"}}, "releases": "maybe", "pre": ["at("]}},
                      "recovery": {"ladders": {}}, "predicates": []})
    text = "\n".join(errs)
    for want in ("pack: unknown key 'surprise'", "pack.name: must be a lower-case name", "pack.extends: must be the name of ONE base",
                 "pack.remove: unknown key 'kinds'", "pack.goal_kinds.mount: unknown key 'unkown_is'",
                 "pack.goal_kinds.mount.planned_by: must be one of planner, template, tree", "pack.actions.Seat.args.arm: `type: enum` needs `values`",
                 "pack.actions.Seat.releases:", "pack.actions.Seat.pre[0]: must be a predicate", "pack.recovery: unknown key 'ladders'",
                 "pack.predicates: must be a mapping of names"):
        assert want in text, (want, text)


def test_a_bare_yaml_on_key_is_an_error_that_says_to_quote_it():
    raw = yaml.safe_load("schema: 2\nname: line\nassumed: true\ngoal_kinds:\n  on: {rules: line.on}\n")
    errs = errors_of(raw)
    assert len(errs) == 1 and "pack.goal_kinds: key True" in errs[0] and '"on":' in errs[0]
    quoted = yaml.safe_load('schema: 2\nname: line\nassumed: true\ngoal_kinds:\n  "on": {rules: line.on}\n')
    assert list(P.parse(quoted).tables["goal_kinds"]) == ["on"]


@pytest.mark.parametrize("schema", [3, 99, "2", None, True])
def test_a_schema_this_core_does_not_load_names_the_supported_ones(schema):
    errs = errors_of({"schema": schema, "name": "line"})
    assert any("pack.schema" in e and "1 to 2" in e for e in errs), errs


def test_a_schema_2_pack_needs_a_name_and_schema_1_may_declare_no_section():
    assert any(e.startswith("pack.name") for e in errors_of({"schema": 2}))
    assert P.parse({"schema": 1}).schema == 1
    assert any("need `schema: 2`" in e for e in errors_of({"schema": 1, "assumed": True, "constants": {"n": {"value": 1}}}))


def test_an_entry_without_a_label_needs_the_packs_default_label():
    raw = {"schema": 2, "name": "line", "constants": {"a": {"value": 1}, "b": {"value": 2, "source": "counted"}}}
    assert errors_of(raw) == ['pack.constants.a: needs `source: "<where the fact comes from>"` or `assumed: true` (or give the pack one)']
    assert P.parse({**raw, "assumed": True}).tables["constants"]["a"] == {"value": 1}


@pytest.mark.parametrize("fields, want", [
    ({}, "needs `rules`"),                                                              # neither rules nor holds: never done
    ({"holds": ["seated(?object)"], "unknown_is": "failure"}, "needs an `observe` verifier"),   # could never succeed
    ({"rules": "line.mount", "unknown_is": "failure", "observe": None}, "needs an `observe` verifier"),
])
def test_a_goal_kind_that_could_never_be_done_is_an_error(fields, want):
    assert any(want in e for e in errors_of(with_kind(**fields)))


def test_a_goal_kind_with_rules_or_with_holds_is_accepted():
    assert P.parse(with_kind(rules="line.mount")).tables["goal_kinds"]["mount"] == {"rules": "line.mount"}
    assert P.parse(with_kind(holds=["seated(?object)"], observe="line.look", unknown_is="failure"))


@pytest.mark.parametrize("section, entry, want", [
    ("predicates", {}, "exactly one of `eval`"), ("predicates", {"eval": "line.p", "kind": "fact"}, "exactly one of `eval`"),
    ("actions", {"pre": ["at(?place)"]}, "need `rules: declared`"), ("actions", {"effects": {"add": ["p()"]}, "rules": "std.x"}, "need `rules: declared`"),
    ("verifiers", {"callable": "line.v", "kind": "vlm"}, "is either `callable"), ("verifiers", {"kind": "sensor"}, "is either `callable"),
    ("questions", {"answer": {"type": "choice"}}, "needs `choices`"),
])
def test_the_fields_of_one_entry_must_fit_together(section, entry, want):
    errs = errors_of({"schema": 2, "name": "line", "assumed": True, section: {"x": entry}})
    assert any(want in e for e in errs), errs


def test_a_recovery_policy_is_a_callable_or_a_ladder_not_both():
    raw = {"schema": 2, "name": "line", "assumed": True, "recovery": {"policies": {"line.p": {"callable": "line.p", "ladder": [{"do": "stop"}]}}}}
    assert any("not both" in e for e in errors_of(raw))


def test_a_predicate_use_is_split_into_negation_name_and_arguments():
    assert P.pred_parts("not at(?place, *)") == (True, "at", ["?place", "*"])
    assert P.pred_parts("hand_free") == (False, "hand_free", []) and P.pred_parts("!opened()") == (True, "opened", [])


# ── the files of a pack ──────────────────────────────────────────────────────────────────────────────────────────────
def write(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(textwrap.dedent(text), encoding="utf-8")


def test_sections_are_read_from_one_file_and_from_one_file_per_section(tmp_path):
    write(tmp_path, "pack.yaml", """
        pack: {schema: 2, name: line, assumed: true}
        constants: {tries: {value: 3}}
    """)
    write(tmp_path / "sections", "phrases.yaml", 'phrases: {q_x: {en: "Which one?"}}\n')
    write(tmp_path / "sections", "recovery.yaml", "recovery: {by_kind: {mount: tree}}\n")
    d = read({"file": "pack.yaml", "dir": "sections", "extends": "household"}, tmp_path)
    assert (d.schema, d.name, d.extends) == (2, "line", "household")
    assert d.tables == {"constants": {"tries": {"value": 3}}, "phrases": {"q_x": {"en": "Which one?"}}, "recovery.by_kind": {"mount": "tree"}}


def test_a_missing_section_file_is_an_empty_section(tmp_path):
    write(tmp_path, "pack.yaml", "pack: {schema: 2, name: line, source: made up}\n")
    assert read({"file": "pack.yaml", "dir": "."}, tmp_path).tables == {}


def test_file_errors_name_the_file_and_a_key_given_twice_is_an_error(tmp_path):
    write(tmp_path, "pack.yaml", "pack: {schema: 2, name: other}\nconstants: {tries: {value: 3, assumed: true}}\n")
    write(tmp_path, "phrases.yaml", "words: {}\n")
    with pytest.raises(P.PackError) as e:
        read({"schema": 2, "name": "line", "file": "pack.yaml", "dir": "."}, tmp_path)
    text = "\n".join(e.value.errors)
    assert "pack.name: given twice (also in pack.yaml)" in text and "phrases.yaml: holds the one top-level key `phrases`" in text
    with pytest.raises(P.PackError, match="cannot be read"):
        read({"schema": 2, "name": "line", "file": "nothing.yaml"}, tmp_path)
    with pytest.raises(P.PackError, match="need a manifest that was read from a file"):
        read({"schema": 2, "name": "line", "file": "pack.yaml"}, None)


# ── the manifest ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_manifest_without_a_pack_section_is_unchanged():
    m = Manifest.from_dict({"robot": "toy", "connections": {"ros": "x"}})
    assert m.pack == {} and m.extra == {"connections": {"ros": "x"}} and "pack" not in m.to_dict()


def test_a_manifest_keeps_the_pack_section_as_written_and_not_as_an_unknown_key():
    raw = {"schema": 2, "name": "line", "extends": "household", "assumed": True, "constants": {"tries": {"value": 3}}}
    m = Manifest.from_dict({"robot": "toy", "pack": raw})
    assert m.pack == raw and m.extra == {} and m.to_dict()["pack"] == raw
    assert Manifest.from_dict(m.to_dict()).pack == raw


def test_a_bad_pack_section_fails_the_manifest_with_its_field_paths():
    with pytest.raises(ManifestError) as e:
        Manifest.from_dict({"robot": "toy", "version": [], "pack": {"schema": 2, "name": "line", "goal_kinds": {"mount": {"needs": "Seat"}}}})
    text = "\n".join(e.value.errors)
    assert "version:" in text and "pack.goal_kinds.mount.needs: must be a list" in text and "pack.goal_kinds.mount: needs `rules`" in text


def test_a_manifest_file_reads_the_pack_file_next_to_it_and_check_manifest_reports_its_errors(tmp_path):
    cfg = tmp_path / "toy" / "configs"
    write(cfg, "manifest.yaml", "robot: toy\npack: {schema: 2, name: line, source: made up, file: pack.yaml}\n")
    write(cfg, "pack.yaml", "constants: {tries: {value: 3}}\n")
    assert Manifest.from_yaml(cfg / "manifest.yaml").pack == {"schema": 2, "name": "line", "source": "made up", "file": "pack.yaml"}
    assert cm.check(tmp_path / "toy").errors == []
    write(cfg, "pack.yaml", "constants: {tries: 3}\n")
    assert any("pack.constants.tries: must be a mapping" in x for x in cm.check(tmp_path / "toy").errors)


@pytest.mark.parametrize("pkg", ["fakebot", "fakehome", "fakeline"])
def test_the_made_up_robots_declare_no_pack_section(pkg):
    from robot_agent.contract import load_adapter
    assert load_adapter(pkg).manifest.pack == {}
