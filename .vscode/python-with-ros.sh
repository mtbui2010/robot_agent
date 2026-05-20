#!/usr/bin/env bash
# Wrapper that sources ROS2 + workspace overlay before launching Python.
# Used as `python` in .vscode/launch.json so F5 inherits the same env
# as a terminal that ran `source /opt/ros/humble/setup.bash` and the
# interface_ws overlay (rosinterfaces, kaair_msgs, ...).
set -e
source /opt/ros/humble/setup.bash
source /home/keti-kcare-1/ros_ws/interface_ws/install/setup.bash
exec /home/keti-kcare-1/venv/bin/python3 "$@"
