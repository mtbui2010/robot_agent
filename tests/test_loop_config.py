"""Step S24: the config resolution of AssistLoop (ASSIST_OPTIONS, the body of __init__ that fills self.cfg, _resolve_options_cfg,
_v2_cfg, _v2_cfg_base, _um_cfg, _rm_cfg, _r3_cfg) moved from assist_loop.py to the LoopConfig mixin (core/planning/loop_config.py);
__init__ stays in the loop file and calls current() and host_id() there (tests patch both names on the assist_loop module). These
tests prove nothing changed: (1) the mixin is in AssistLoop's method resolution order and serves the names, the loop file no longer
defines them, __init__ still calls current() and host_id() in the loop file, ASSIST_OPTIONS is re-exported and equals the
reference, every global name the moved bodies read is the same object as in the reference; (2) the moved bodies are the reference
bodies (the same syntax trees; the cfg block differs only in its memory_path entry and the generator / planner pair, which
__init__ sets right after it); (3) differential tests against the reference copy (tests/legacy_ref/s24_loop_config.py, the old
text at e789b86): the whole resolved config, key by key IN ORDER, the attributes __init__ sets, and the order of the calls of
current(), host_id() and data_dir(), over grids of made-up packs, run options, environment variables and manifest values, errors
included. Made-up packs only (fakebot, fakehome, fakeline)."""

import ast
import builtins
import itertools
import re
import types
from pathlib import Path

import pytest

from legacy_ref import s24_loop_config as old
from robot_agent.core.planning import assist_loop, loop_config
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.phases import PHASE_MIXINS

MOVED = ("_resolve_options_cfg", "_v2_cfg", "_v2_cfg_base", "_um_cfg", "_rm_cfg", "_r3_cfg")
STAY = ("__init__", "_manifest_planner", "_make_planner", "run_blocking", "_run_blocking", "_converge", "_lab")
LOOP_FILE = Path(assist_loop.__file__)
MIXIN_FILE = Path(loop_config.__file__)


class OldLoop(old.RefLoopConfig, AssistLoop):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert loop_config.LoopConfig in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(loop_config.LoopConfig) < mro.index(assist_loop.WhichOne)
    for n in MOVED + ("_resolve_assist_cfg",):
        assert n not in vars(AssistLoop), n
        assert getattr(AssistLoop, n) is vars(loop_config.LoopConfig)[n], n      # nothing earlier in the MRO shadows it
    assert "__init__" not in vars(loop_config.LoopConfig)


def test_the_loop_file_does_not_define_them_any_more_and_the_rest_stays():
    src = LOOP_FILE.read_text()
    for n in MOVED:
        assert f"def {n}(" not in src, n
    assert "\nASSIST_OPTIONS = " not in src
    for n in STAY:
        assert f"def {n}(" in src and n in vars(AssistLoop), n


def test_init_calls_current_and_host_id_in_the_loop_file():
    init = _methods(LOOP_FILE, "AssistLoop")["__init__"]
    called = {n.func.id for n in ast.walk(init) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"current", "host_id", "data_dir"} <= called
    for name in ("current", "host_id", "data_dir"):
        assert name not in vars(loop_config), name                  # the moved code cannot call an unpatched copy


def test_assist_options_is_re_exported_and_equals_the_reference():
    assert assist_loop.ASSIST_OPTIONS is loop_config.ASSIST_OPTIONS
    assert isinstance(loop_config.ASSIST_OPTIONS, frozenset)
    assert loop_config.ASSIST_OPTIONS == old.ASSIST_OPTIONS


def test_the_moved_module_is_about_the_designed_size():
    assert len(MIXIN_FILE.read_text().splitlines()) <= 300


def _methods(path, cls):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}


def _free_globals(fn) -> set:
    loads = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    local = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)}
    local |= {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}
    local |= {n.name for n in ast.walk(fn) if isinstance(n, ast.FunctionDef)}
    return {x for x in loads - local if x not in dir(builtins)}


def test_every_global_name_of_the_moved_bodies_is_the_same_object_as_in_the_reference():
    new = _methods(MIXIN_FILE, "LoopConfig")
    seen = set()
    for n in MOVED + ("_resolve_assist_cfg",):
        for name in _free_globals(new[n]):
            assert hasattr(loop_config, name) and getattr(loop_config, name) is getattr(old, name), (n, name)
            seen.add(name)
    assert {"os", "json", "FailureCode", "STEP_LADDER", "DEFAULT_LLM_BUDGET", "search_belief", "Dispatcher", "LoopServices",
            "role_models", "exec_integrity"} == seen


# -- (2) the bodies are the reference's bodies ---------------------------------------------------------------------------------
def _dump(node):
    node = ast.parse(ast.unparse(node))
    for n in ast.walk(node):
        for f in ("lineno", "end_lineno", "col_offset", "end_col_offset"):
            if hasattr(n, f):
                setattr(n, f, 0)
    return ast.dump(node)


def test_method_bodies_are_the_reference_bodies():
    new, rf = _methods(MIXIN_FILE, "LoopConfig"), _methods(old.__file__, "RefLoopConfig")
    assert set(new) == set(MOVED) | {"_resolve_assist_cfg"} and set(rf) == set(MOVED) | {"__init__"}
    for n in MOVED:
        assert _dump(new[n]) == _dump(rf[n]), n


def test_the_cfg_block_is_the_reference_block_but_for_the_memory_path_and_the_planner_pair():
    new = _methods(MIXIN_FILE, "LoopConfig")["_resolve_assist_cfg"]
    ref = _methods(old.__file__, "RefLoopConfig")["__init__"]
    ref_body = [s for s in ref.body if not (isinstance(s, ast.Assign) and ast.unparse(s.targets[0]) in ("overrides", "self.original",
                                                                                                       "common", "self._reaches"))
                and "ClosedLoop.__init__" not in ast.unparse(s) and "_resolve_options_cfg" not in ast.unparse(s)
                and not ast.unparse(s).startswith("overrides['planner']")]
    new_body = new.body[1:]                                          # the docstring
    assert len(ref_body) == len(new_body)
    for a, b in zip(ref_body[:-1], new_body[:-1]):
        assert _dump(a) == _dump(b)
    rd, nd = ref_body[-1].value.args[0], new_body[-1].value.args[0]      # the self.cfg.update({...}) dicts
    rk, nk = [ast.literal_eval(k) for k in rd.keys], [ast.literal_eval(k) for k in nd.keys]
    assert rk == nk + ["generator", "planner"]
    for k, a, b in zip(nk, rd.values, nd.values):
        if k == "memory_path":
            assert ast.unparse(b) == "memory_path()"
            assert ast.unparse(a) == "Path(overrides.get('memory_path') or data_dir('object_memory', common, host_id()))"
        else:
            assert _dump(a) == _dump(b), k
    assert [ast.unparse(v) for v in rd.values[-2:]] == ["self.cfg['planner']", "'sage'"]
    init = ast.unparse(_methods(LOOP_FILE, "AssistLoop")["__init__"])
    assert "self.cfg.update({'generator': self.cfg['planner'], 'planner': 'sage'})" in init
    assert "Path(overrides.get('memory_path') or data_dir('object_memory', common, host_id()))" in init


def test_the_moved_text_is_the_reference_text():
    """Comments and docstrings included: the six verbatim methods are the reference's lines, in the same order."""
    def tail(path, first):
        lines = Path(path).read_text().splitlines()
        return lines[next(i for i, x in enumerate(lines) if x.startswith(first)):]
    start = "    def _resolve_options_cfg("
    assert tail(MIXIN_FILE, start) == tail(old.__file__, start)
    first = "# per-run overrides accepted from /ws/agent `options`"
    new, ref = tail(MIXIN_FILE, first), tail(old.__file__, first)
    assert new[:19] == ref[:19] and new[18].startswith("ASSIST_OPTIONS = frozenset(")


# -- (3) differential tests ---------------------------------------------------------------------------------------------------
def _norm(v, tmp):
    return re.sub(r" at 0x[0-9a-f]+", " at 0x", repr(v)).replace(str(tmp), "<tmp>")


def _build(cls, pkg, options, environ, manifest, tmp):
    """(result, trace): the loop's config and attributes, or the error; the calls of current / host_id / data_dir in order."""
    import robot_agent.core.planning.loop as LP
    import robot_agent.skill_configs as sc
    import robot_agent.state as state_mod
    from fakes import FakeRobot, FakeSkillRegistry, FakeState, load_env
    trace = []
    st = FakeState(tmp)
    st.robot_pkg = pkg
    with pytest.MonkeyPatch.context() as mp:
        import os
        for k in [k for k in os.environ if k.startswith("ROBOT_AGENT_")]:
            mp.delenv(k)
        for k, v in environ.items():
            mp.setenv(k, v.replace("<tmp>", str(tmp)))
        for m, tag in ((assist_loop, "AL"), (old, "REF"), (LP, "LP"), (state_mod, "ST")):
            mp.setattr(m, "current", lambda tag=tag: trace.append(("current", tag)) or st)
        for m, tag in ((assist_loop, "AL"), (old, "REF"), (LP, "LP")):
            mp.setattr(m, "host_id", lambda tag=tag: trace.append(("host_id", tag)) or "test")
        for m, tag in ((assist_loop, "AL"), (old, "REF")):
            real = m.data_dir
            mp.setattr(m, "data_dir", lambda kind, c=None, h=None, real=real, tag=tag:
                       trace.append(("data_dir", tag, kind, _norm(c, tmp), h)) or real(kind, c, h))
        mp.setattr(sc, "ENV", load_env())
        mp.setattr(assist_loop, "reconcile_world", lambda node, world, threshold=0.8: world)
        if manifest is not None:
            def mp_planner(self, key, default=None):
                v = manifest.get(key)
                return default if v is None or v == "" else v
            mp.setattr(AssistLoop, "_manifest_planner", mp_planner)
        robot = FakeRobot(load_env(), {"cup": "dining table"})
        agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(robot), device_manager=types.SimpleNamespace(_ros_node=None),
                                      _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
        ov = dict(mute_skill_tts=False, speak_backend=False, vlm_enabled=False, log_dir=str(tmp / "runs"),
                  live_path=str(tmp / "live.jsonl"), memory_path=str(tmp / "object_memory.json"))
        ov.update(options)
        try:
            loop = cls(agent, emit=None, **ov)
        except Exception as e:                                       # noqa: BLE001 - the error is part of the result
            return ("raised", type(e).__name__, str(e)), _untag(trace)
        res = {"cfg": [(k, _norm(v, tmp)) for k, v in loop.cfg.items()], "attrs": sorted(vars(loop)),
               "find_params": sorted(loop._find_params), "original": loop.original, "options": type(loop.options).__name__,
               "mirror": [(k, _norm(v, tmp)) for k, v in loop.options.mirror().items()],
               "reaches": loop._reaches([], WorldState(), [])}
        return res, _untag(trace)


def _untag(trace):
    """The old class calls the reference module's names and the new one the loop file's: both are 'loop' (ClosedLoop's: 'LP')."""
    return [(t[0], "loop" if t[1] in ("AL", "REF") else t[1]) + tuple(t[2:]) for t in trace]


def both(tmp_path, pkg="fakebot", options=(), environ=(), manifest=None):
    out = []
    for i, cls in enumerate((OldLoop, AssistLoop)):
        tmp = tmp_path / f"t{i}_{len(list(tmp_path.iterdir()))}"
        tmp.mkdir()
        res, trace = _build(cls, pkg, dict(options), dict(environ), manifest, tmp)
        out.append((res, trace))
    return out


OPTIONS = [
    {},
    {"memory_path": ""},
    {"memory_path": None, "original": "bring the cup"},
    {"clarify_rounds": "5", "search_max": 2, "pick_retries": "4", "arrive_tol": "0.3", "find_min_conf": 0.4, "llm_budget": 7,
     "goal_check": "belief", "plan_tidy": "0", "goal_filter": False, "reconcile": "false", "max_replans": "2", "edit_rounds": 1},
    {"generator": "sage", "planner": "direct", "confirm_task": True, "confirm_answers": False, "find_verify": True, "ask_help": False},
    {"dialog_agree2": "1", "exec_integrity": "on", "inventory_ask": "yes", "gate_strict": "0"},
    {"understand_model_lang": '{"KO": " m1 ", " ": "x", "es": ""}', "understand_max_tokens": "0", "understand_max_goals": "abc",
     "understand_model": " um ", "prompt_lang": "KOR", "understand_schema_strict": "TRUE"},
    {"understand_model_lang": "not json", "understand_url": "", "repair_model": "rm", "request_coverage": "yes", "dialog_think": "1"},
    {"search_legs": "TRUE", "search_total": "3", "person_goals": "on", "accept_goals": 1, "translate_url": "http://x",
     "translate_model": None, "translate_budget": "4", "input_pivot": "TRANSLATE", "go_steps": "1", "relook": "on"},
    {"search": "0", "search_rank": "prior", "search_half_life_s": "60", "detector_miss_rate": "0.2", "dialog_converge": "0"},
    {"bt_goal": "0", "look_then_ask": "0", "fetch_guard": "1", "bring_ask": "0"},
    {"clarify_rounds": "x"},
    {"health_wait_s": "bad", "memory_path": ""},
    {"search_total": "bad", "memory_path": ""},
    {"understand_max_tokens": "12", "kind_anchor_ask": "1", "dialog_unstick": "1", "object_check": "0", "gate_complete": "1"},
]
ENVIRON = [
    {},
    {"ROBOT_AGENT_CLARIFY_ROUNDS": "9", "ROBOT_AGENT_SAGE_GENERATOR": "sage", "ROBOT_AGENT_GOAL_CHECK": "belief",
     "ROBOT_AGENT_UNDERSTAND_MODEL": "envm", "ROBOT_AGENT_EXEC_INTEGRITY": "1", "ROBOT_AGENT_RECONCILE": "0",
     "ROBOT_AGENT_LLM_BUDGET": "11", "ROBOT_AGENT_SEARCH_LEGS": "yes", "ROBOT_AGENT_PROMPT_LANG": "es"},
    {"ROBOT_AGENT_DATA_DIR": "<tmp>/data", "ROBOT_AGENT_ARRIVE_TOL": "0.7", "ROBOT_AGENT_CONFIRM_TASK": "1",
     "ROBOT_AGENT_UNDERSTAND_MODEL_LANG": '{"ko": "envko"}', "ROBOT_AGENT_REPAIR_MODEL": "envrm"},
    {"ROBOT_AGENT_MAX_REPLANS": "nan"},
]
MANIFESTS = [None, {"llm_budget": 33, "search_legs": "1", "translate_model": "tm", "detector_miss_rate": 0.3, "understand_model": "mm",
                    "dialog_unstick": True, "search_total": 2, "input_pivot": "both", "prompt_lang": "ko", "accept_goals": "",
                    "understand_model_lang": {"ko": "mko"}, "repair_model": "mrm", "exec_integrity": "1"}]


def _same(pair, what):
    (a, ta), (b, tb) = pair
    assert a == b, what
    assert [t for t in ta] == [t for t in tb], what
    return a, tb


@pytest.mark.parametrize("pkg", ("fakebot", "fakehome", "fakeline"))
def test_the_resolved_config_equals_the_reference_over_the_run_options(tmp_path, pkg):
    kinds = set()
    for opts in OPTIONS:
        res, trace = _same(both(tmp_path, pkg, opts), (pkg, opts))
        kinds.add(res[0] if isinstance(res, tuple) else "built")
        assert trace[0] == ("current", "LP")                         # ClosedLoop's, then the loop's own
        if isinstance(res, dict):
            assert ("current", "loop") in trace
            assert (("host_id", "loop") in trace) == (not opts.get("memory_path", "set"))   # only without a memory path, as before
    assert kinds == {"built", "raised"}


def test_the_resolved_config_equals_the_reference_over_environment_and_manifest(tmp_path):
    for opts, env, man in itertools.product((OPTIONS[0], OPTIONS[1], OPTIONS[3], OPTIONS[6], OPTIONS[8], OPTIONS[13]), ENVIRON, MANIFESTS):
        _same(both(tmp_path, "fakehome", opts, env, man), (opts, env, man))


def test_the_order_of_the_keys_is_the_reference_order(tmp_path):
    (a, _), (b, _) = both(tmp_path)
    keys = [k for k, _v in b["cfg"]]
    assert keys == [k for k, _v in a["cfg"]]
    assert keys[0] == "planner" and keys.index("generator") == keys.index("reconcile") + 1
    assert dict(b["cfg"])["planner"] == "'sage'" and dict(b["cfg"])["generator"] == "'sage'"


def test_the_moved_methods_equal_the_reference_on_a_bare_loop(monkeypatch):
    """The 28 older tests build the loop with __new__ and call the methods directly: the same answers."""
    for man, env in itertools.product(MANIFESTS, ENVIRON[:3]):
        for k in [k for k in __import__("os").environ if k.startswith("ROBOT_AGENT_")]:
            monkeypatch.delenv(k)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        for opts in OPTIONS:
            got = []
            for cls in (OldLoop, AssistLoop):
                lp = cls.__new__(cls)
                lp.robot = types.SimpleNamespace(manifest=types.SimpleNamespace(planner=dict(man or {})))
                row = []
                for n in ("_v2_cfg", "_um_cfg", "_rm_cfg", "_r3_cfg"):
                    try:
                        row.append((n, sorted((k, repr(v)) for k, v in getattr(lp, n)(dict(opts)).items())))
                    except Exception as e:                       # noqa: BLE001 - the error is part of the result
                        row.append((n, "raised", type(e).__name__, str(e)))
                got.append(row)
            assert got[0] == got[1], (man, env, opts)
