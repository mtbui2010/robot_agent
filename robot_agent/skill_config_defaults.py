"""Neutral, robot-agnostic shapes for the skill-config groups.

These are the LAST fallback in ``ConfigManager``'s chain:

  1. ``configs/locations/<site>/skill_configs_override.json`` (per-site; what
     the dashboard's Edit-then-Save writes)
  2. ``<robot_pkg>.configs.tasks``  ← where a robot's real numbers belong
  3. this module

An override replaces a whole group outright — there is no per-key merge — so a
value here is only ever reached when neither the site nor the robot package
declares the group at all.

Deliberately empty of hardware numbers. These used to carry one specific
robot's millimetre-era values (its furniture layout, joint angles, Korean word
maps), which meant every NEW robot package silently inherited another robot's
living room as its defaults. Those numbers now live with the robot that owns
them, in ``kcare_robot/configs/tasks.py``.

Keep the keys and value SHAPES documented here — skills index into these dicts,
so the structure is the contract even when the numbers are not. Units are
metres, degrees, and seconds throughout.
"""

# Gripper opening range, metres: [closed, open].
GRIP_CONFIGS_DEFAULT: dict = {
    'range': [0.0, 0.1],
}

# Vertical lift. 'range' is [lowest, highest] in metres; 'home' is the resting
# height per robot_mode ('front' / 'left' / 'right').
LIFT_CONFIGS_DEFAULT: dict = {
    'range': [0.0, 1.0],
    'home': {},
}

# Head pan/tilt in degrees. 'ry_range' bounds the tilt; 'rz' is the pan per
# robot_mode; 'ry' names a few tilt presets.
HEAD_CONFIGS_DEFAULT: dict = {
    'ry_range': [-90, 90],
    'rz': {},
    'ry': {},
}

# Arm geometry and named joint poses.
#   range        absolute base-frame working box, metres. NOTE: 'x' is the
#                standoff distance the MOBILE BASE drives to (see
#                _approach_helpers.compute_forward_distance) — it is not an
#                arm reach limit.
#   tool_range   bounds for RELATIVE movet deltas, metres — not absolute poses.
#   home/ready/… named joint configurations, degrees, keyed by robot_mode.
ARM_CONFIGS_DEFAULT: dict = {
    'range': {'x': [0.0, 1.0], 'y': [-1.0, 1.0], 'z': [0.0, 2.0]},
    'tool_range': {'x': [-0.5, 0.5], 'y': [-0.5, 0.5], 'z': [-0.5, 0.5]},
    'home': {},
    'ready': {},
}

# Mobile base: speeds, and any per-robot offsets the approach helpers use.
MOBILE_CONFIGS_DEFAULT: dict = {}

# Detector/selector thresholds for the recognition skills.
FIND_CONFIGS_DEFAULT: dict = {}

# Hand-eye calibration parameters.
CALIB_PARAMS_DEFAULT: dict = {}

# The map pose the robot returns to when idle: {'x', 'y', 'theta'}.
HOME_LOC_DEFAULT: dict = {}

# The symbolic world: '<furniture>@<room>' -> {'loc': {'x','y','theta'},
# 'height': m, 'label': str, ...}. Populated per robot — an empty ENV simply
# means no named locations are known yet.
ENV_DEFAULT: dict = {}

# Word maps for spoken output, e.g. {'cup': '컵'}. Language-specific, so they
# belong to the robot package rather than the runtime.
KR2EN_DEFAULT: dict = {}
EN2KR_DEFAULT: dict = {}
