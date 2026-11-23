"""ROBOT_AGENT_ROS=off: a simulated robot package boots without ROS 2 (no rclpy import, no node); the default stays ROS on.

Each case runs in a subprocess: bootstrap() keeps process-wide state (one robot package per process)."""
import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent("""
    import os, sys, tempfile
    sys.modules['rclpy'] = None                       # rclpy "not installed": importing it raises ImportError
    from robot_agent import runtime
    try:
        st = runtime.bootstrap('tiny_robot', config_dir=tempfile.mkdtemp(), load_devices=False)
        print('BOOTED', type(st).__name__)
    except ImportError:
        print('IMPORT_ERROR')
""")


def boot(tmp_path, ros):
    pkg = tmp_path / "tiny_robot" / "configs"
    pkg.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tiny_robot" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "skills_config.py").write_text("SKILL_CONFIGS = {}\n")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{tmp_path}:{sys.path[0] or '.'}:" + ":".join(p for p in sys.path if p), "HOME": str(tmp_path)}
    if ros is not None:
        env["ROBOT_AGENT_ROS"] = ros
    r = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, env=env, timeout=120)
    return r.stdout + r.stderr


def test_ros_off_boots_a_robot_package_without_rclpy(tmp_path):
    for value in ("off", "0", "false", "no", "OFF"):
        assert "BOOTED AgentState" in boot(tmp_path, value), value


def test_default_and_on_still_require_ros(tmp_path):
    for value in (None, "on", "1"):
        out = boot(tmp_path, value)
        assert "IMPORT_ERROR" in out and "ROBOT_AGENT_ROS=off" in out, (value, out[-300:])
