"""robot_agent.connect.codecs: named codecs instead of exec'd source in connections.json,
the legacy inline path (one exec, deprecation warning, env switch) and the scan helper."""

import json
import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from robot_agent.connect import codecs

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
DECODE_SRC = "def decode_func(msg):\n    return {'n': msg + 1}"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(codecs.NO_INLINE_ENV, raising=False)
    monkeypatch.setattr(codecs, "_WARNED", set())
    monkeypatch.setattr(codecs, "_REGISTRY", dict(codecs._REGISTRY))


# ── resolve ──
def test_registered_codec():
    @codecs.register_codec("double")
    def double(x):
        return 2 * x

    assert codecs.resolve("codec:double") is double
    assert codecs.resolve(" codec: double ") is double
    with pytest.raises(KeyError, match="unknown codec 'nope'"):
        codecs.resolve("codec:nope")
    with pytest.raises(TypeError):
        codecs.register_codec("bad")(42)


def test_import_paths_and_passthrough():
    assert codecs.resolve("json:dumps") is json.dumps
    assert codecs.resolve("os.path:join.__name__") == "join"
    assert codecs.resolve("json.dumps") is json.dumps                  # dotted, as before
    assert codecs.resolve("robot_agent/safe_parse/parse_kwargs").__name__ == "parse_kwargs"
    assert codecs.resolve(len) is len
    assert codecs.resolve(None) is None and codecs.resolve("  ") is None
    with pytest.raises(ImportError):
        codecs.resolve("no_such_pkg_xyz.func")


def test_names_are_never_executed():
    # without inline='always', a string that is not def/lambda source is only a name
    with pytest.raises(ImportError):
        codecs.resolve("__import__('os').system('true')", "encode_func")


# ── legacy inline code ──
def test_legacy_inline_compiles_and_warns_once(caplog):
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        f = codecs.resolve(DECODE_SRC, "decode_func", device="head_rgb")
        g = codecs.resolve(DECODE_SRC, "decode_func", device="head_rgb")
        codecs.resolve("lambda d: d", "encode_func", device="head_rgb")
    assert f(1) == {"n": 2} and g(2) == {"n": 3}
    dep = [str(x.message) for x in w if issubclass(x.category, DeprecationWarning)]
    assert len(dep) == 2                                     # once per (device, key)
    assert "head_rgb" in dep[0] and "decode_func" in dep[0] and "--scan" in dep[0]


def test_legacy_picks_named_function_then_any_callable():
    src = "def helper(x):\n    return x\n\ndef run_func(req):\n    return helper(req) * 3"
    assert codecs._legacy_compile(src, "run_func", "srv")(2) == 6
    assert codecs._legacy_compile("f = lambda x: x + 10", "encode_func", "d")(1) == 11
    assert codecs._legacy_compile("X = 1", "encode_func", "d") is None
    assert codecs._legacy_compile("lambda x: x", "encode_func", "d") is None   # a bare expression defines nothing (as before)


def test_inline_disabled_by_env(monkeypatch):
    monkeypatch.setenv(codecs.NO_INLINE_ENV, "1")
    with pytest.raises(codecs.InlineCodeDisabled, match=r"'lift'.*encode_func.*codec:<name>"):
        codecs.resolve("def encode_func(d, r):\n    return r", "encode_func", device="lift")
    @codecs.register_codec("ok")
    def ok(x):
        return x
    assert codecs.resolve("codec:ok") is ok                  # named codecs still work
    monkeypatch.setenv(codecs.NO_INLINE_ENV, "0")
    assert callable(codecs.resolve("f = lambda d: d", "encode_func"))


def test_only_one_exec_in_the_package():
    import robot_agent
    pkg = Path(robot_agent.__file__).parent
    hits = [f"{f.relative_to(pkg)}:{n}" for f in pkg.rglob("*.py")
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"(?<![\w.])exec\(", line)]
    assert len(hits) == 1 and hits[0].startswith("connect/codecs.py:")


# ── DeviceManager keeps today's behaviour ──
def test_device_manager_run_func_paths():
    from robot_agent.core.device_manager import DeviceManager
    legacy = DeviceManager._compile_run_func({"agent_name": "srv", "run_func": "def run_func(r):\n    return r * 2"})
    assert legacy(4) == 8
    assert DeviceManager._compile_run_func({"run_func": "json:loads"}) is json.loads
    assert DeviceManager._compile_run_func({"run_func": ""}) is None
    assert DeviceManager._compile_run_func({}) is None


# ── migration helper ──
CONNS = [
    {"type": "ros_topic", "name": "head_rgb", "config": {"agent_name": "head_rgb", "decode_func": DECODE_SRC,
                                                         "data_interface": "sensor_msgs/msg/Image"}},
    {"type": "ros_service", "name": "lift", "config": {"encode_func": "codec:lift_goal",
                                                       "decode_func": "lambda r: r.ok"}},
    {"type": "tcp", "name": "echo", "config": {"is_client": False, "run_func": "return_it = lambda r: r"}},
    {"type": "tcp", "name": "named", "config": {"is_client": False, "run_func": "my_robot.servers:echo"}},
]


def test_scan():
    assert codecs.scan(CONNS) == [("head_rgb", "decode_func", "def decode_func(msg):"),
                                  ("lift", "decode_func", "lambda r: r.ok"),
                                  ("echo", "run_func", "return_it = lambda r: r")]


def test_scan_cli(tmp_path):
    p = tmp_path / "connections.json"
    p.write_text(json.dumps(CONNS))
    before = p.read_text()
    out = subprocess.run([sys.executable, "-m", "robot_agent.connect.codecs", "--scan", str(p)], cwd=ROOT,
                         capture_output=True, text=True, check=True, env={"PYTHONPATH": str(ROOT)}).stdout
    assert "3 inline code entries" in out and "head_rgb" in out and "codec:<name>" in out
    assert p.read_text() == before                           # read only
