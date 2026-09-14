SHELL := /bin/bash
ROOT  := $(shell pwd)

# ── Target environment ───────────────────────────────────────────────────────
# robot_agent is a LIBRARY: it is nearly always installed into the env of a
# robot that depends on it, not into one of its own. So unlike a robot package
# this defaults to whatever python is active, and creates nothing.
#
#   make install                        -> the python on PATH right now
#   make install PYTHON=/path/bin/python-> that exact interpreter
#   make install CONDA_ENV=ragent       -> a dedicated conda env (created)
#
# `conda activate` does not work in make's non-interactive shell, so the target
# interpreter is always called by absolute path.
CONDA_ENV ?=
PYTHON    ?=
FORCE     ?= 0
CONDA_BASE := $(shell conda info --base 2>/dev/null)
CONDA_BIN  := $(shell command -v mamba 2>/dev/null || command -v conda 2>/dev/null)
# Python version for a NEWLY created env. ROS 2 ships C extensions built for one
# CPython, and robot_agent.connect imports rclpy alongside them, so match the
# distro when it is present.
ROS_DISTRO ?= humble
ROS_PY_VER := $(shell ls -d /opt/ros/$(ROS_DISTRO)/lib/python3.* 2>/dev/null | head -1 | sed 's|.*/python||')
PYTHON_VERSION ?= $(if $(ROS_PY_VER),$(ROS_PY_VER),3.10)

ifneq ($(CONDA_ENV),)
  ENV_PREFIX := $(if $(CONDA_BASE),$(CONDA_BASE)/envs/$(CONDA_ENV),)
  ENV_PY     := $(if $(ENV_PREFIX),$(ENV_PREFIX)/bin/python,)
else
  ENV_PY     := $(if $(PYTHON),$(PYTHON),$(shell command -v python3 2>/dev/null))
  ENV_PREFIX := $(shell $(ENV_PY) -c 'import sys; print(sys.prefix)' 2>/dev/null)
endif
ENV_PIP = $(ENV_PY) -m pip

# Where `make robot` looks for the cookiecutter, and where new robots land.
# abspath so the printed destination is readable rather than '<root>/../name'.
TEMPLATE_DIR ?= $(abspath $(ROOT)/../robot_template)
ROBOTS_DIR   ?= $(abspath $(ROOT)/..)

# `make robot` options, as variables rather than raw flags in ARGS.
#   RESET_SITES=1  wipe the source's device endpoints (fork targets new hardware)
#   BLANK_CONFIGS=1 empty configs/tasks.py too (no hardware numbers at all)
#   PORT=8005      default agent port for the new robot
#   FORCE=1        overwrite an existing destination
RESET_SITES   ?= 0
BLANK_CONFIGS ?= 0
PORT          ?=
_ROBOT_FLAGS = $(if $(filter 1,$(RESET_SITES)),--reset-sites,) \
               $(if $(filter 1,$(BLANK_CONFIGS)),--blank-configs,) \
               $(if $(PORT),--port $(PORT),) \
               $(if $(filter 1,$(FORCE)),--force,)

# Extras: net (transports), llm (backends), voice (TTS). `all` is everything
# except grace, whose pyplanner is a local path package rather than a PyPI one.
EXTRAS ?= all

.PHONY: install install-dev robot test clean help _require-py _require-import

help:
	@echo "robot_agent -- generic robot runtime (FastAPI + skills + connect layer)"
	@echo ""
	@echo "Setup:"
	@echo "  make install                          Editable-install into the ACTIVE python"
	@echo "  make install CONDA_ENV=<name>         ... into a conda env (created if missing)"
	@echo "  make install PYTHON=<path>            ... into that exact interpreter"
	@echo "  make install EXTRAS=net,llm           ... only these extras (default: all)"
	@echo "  make install-dev                      Also install pyplanner + cookiecutter"
	@echo ""
	@echo "Robots:"
	@echo "  make robot NAME=<pkg> FROM=<src_pkg>  Fork an existing robot package"
	@echo "  make robot NAME=<pkg>                 Generate a fresh one from robot_template"
	@echo "  make robot NAME=<pkg> FROM=<src> RESET_SITES=1 PORT=8005"
	@echo "                                        (a fork keeps the source's devices by default)"
	@echo ""
	@echo "  make test                             Import smoke check"
	@echo "  make clean                            Remove __pycache__ and *.egg-info"
	@echo ""
	@echo "Target python : $(ENV_PY)"
	@echo "Overridables  : CONDA_ENV=$(CONDA_ENV)  EXTRAS=$(EXTRAS)  ROBOTS_DIR=$(ROBOTS_DIR)"

# ── Environment ──────────────────────────────────────────────────────────────

# Only creates something when CONDA_ENV was asked for; otherwise it just checks
# that the active interpreter is usable and is not somewhere regrettable.
_require-py:
	@if [ -n "$(CONDA_ENV)" ] && [ ! -x "$(ENV_PY)" ]; then \
		if [ -z "$(CONDA_BIN)" ]; then \
			echo "[robot_agent] conda not found on PATH."; exit 1; \
		fi; \
		echo "[robot_agent] creating conda env '$(CONDA_ENV)' (python $(PYTHON_VERSION)) ..."; \
		$(CONDA_BIN) create -y -p "$(ENV_PREFIX)" --override-channels -c conda-forge python=$(PYTHON_VERSION); \
	fi
	@if [ -z "$(ENV_PY)" ] || [ ! -x "$(ENV_PY)" ]; then \
		echo "[robot_agent] no usable python found ($(if $(PYTHON),PYTHON=$(PYTHON),python3 not on PATH))"; \
		exit 1; \
	fi
	@have=$$($(ENV_PY) -c 'import sys; print("%d.%d" % sys.version_info[:2])'); \
	echo "[robot_agent] target python: $(ENV_PY) (python $$have)"; \
	risky=""; \
	if [ -n "$(CONDA_BASE)" ] && [ "$(ENV_PREFIX)" = "$(CONDA_BASE)" ]; then risky="the conda BASE env"; fi; \
	case "$(ENV_PY)" in /usr/bin/*|/bin/*) risky="the SYSTEM python";; esac; \
	if [ -n "$$risky" ] && [ "$(FORCE)" != "1" ]; then \
		echo ""; \
		echo "  ! $(ENV_PY) is $$risky. Installing there is hard to undo."; \
		echo "    Activate the env you want first, or be explicit:"; \
		echo "        make install CONDA_ENV=<name>"; \
		echo "        make install FORCE=1        # really use $$risky"; \
		exit 1; \
	fi; \
	if [ -n "$(ROS_PY_VER)" ] && [ "$$have" != "$(ROS_PY_VER)" ]; then \
		echo "  ! python $$have does not match ROS $(ROS_DISTRO) (built for $(ROS_PY_VER))."; \
		echo "    rclpy will not import here — fine for pure-HTTP use, not for a robot."; \
	fi

# Targets that only run code (rather than install into the env) need far less:
# just an interpreter that can import the package.
_require-import:
	@if [ -z "$(ENV_PY)" ] || [ ! -x "$(ENV_PY)" ]; then \
		echo "[robot_agent] no usable python found"; exit 1; \
	fi
	@if ! $(ENV_PY) -c 'import robot_agent' >/dev/null 2>&1; then \
		echo "[robot_agent] robot_agent is not importable in $(ENV_PY)"; \
		echo "               run 'make install' first, or activate the env that has it."; \
		exit 1; \
	fi

install: _require-py
	@$(ENV_PIP) install -q --upgrade pip setuptools wheel
	@echo "[robot_agent] pip install -e .[$(EXTRAS)]"
	@$(ENV_PIP) install -e "$(ROOT)[$(EXTRAS)]"
	@$(ENV_PY) -c "import robot_agent.connect, numpy; \
print('[robot_agent] imports OK (numpy %s)' % numpy.__version__)"

# The planner and the robot generator are only needed by developers of the
# stack, not by a robot that merely depends on robot_agent.
install-dev: install
	@if [ -d "$(ROOT)/../pyplanner" ]; then \
		echo "[robot_agent] pip install -e ../pyplanner (GRACE planner)"; \
		$(ENV_PIP) install -e "$(ROOT)/../pyplanner"; \
	else \
		echo "[robot_agent] ../pyplanner not found -- skipping"; \
	fi
	@echo "[robot_agent] pip install cookiecutter (for 'make robot' without FROM)"
	@$(ENV_PIP) install -q cookiecutter

# ── New robots ───────────────────────────────────────────────────────────────

# One entry point for all three situations:
#   FROM given -> fork that package (robot_agent.new_robot)
#   no FROM    -> generate a fresh skeleton from the robot_template cookiecutter
robot: _require-import
	@if [ -z "$(NAME)" ]; then \
		echo "Usage:"; \
		echo "  make robot NAME=<pkg> FROM=<src_pkg>   # fork an existing robot"; \
		echo "  make robot NAME=<pkg>                  # fresh from robot_template"; \
		echo ""; \
		echo "Options (fork only):"; \
		echo "  RESET_SITES=1     wipe the source's device endpoints (new hardware)"; \
		echo "  BLANK_CONFIGS=1   also empty configs/tasks.py (no hardware numbers)"; \
		echo "  PORT=8005         default agent port"; \
		echo "  FORCE=1           overwrite an existing destination"; \
		echo ""; \
		echo "By default a fork KEEPS the source's configs/locations/, so it runs"; \
		echo "as-is against the same hardware. Add RESET_SITES=1 otherwise."; \
		exit 2; \
	fi
	@if [ -e "$(ROBOTS_DIR)/$(NAME)" ] && [ "$(FORCE)" != "1" ]; then \
		echo "[robot_agent] $(ROBOTS_DIR)/$(NAME) already exists (FORCE=1 to overwrite)"; \
		exit 2; \
	fi
	@if [ -n "$(FROM)" ]; then \
		$(ENV_PY) -m robot_agent.new_robot $(NAME) --from $(FROM) \
			--dest "$(ROBOTS_DIR)/$(NAME)" $(_ROBOT_FLAGS) $(ARGS); \
	else \
		if [ ! -d "$(TEMPLATE_DIR)" ]; then \
			echo "[robot_agent] template not found at $(TEMPLATE_DIR)"; \
			echo "               pass TEMPLATE_DIR=<path>, or FROM=<pkg> to fork instead."; \
			exit 1; \
		fi; \
		if ! $(ENV_PY) -c 'import cookiecutter' >/dev/null 2>&1; then \
			echo "[robot_agent] cookiecutter is not installed in $(ENV_PY)"; \
			echo "               run 'make install-dev', or fork instead: make robot NAME=$(NAME) FROM=<pkg>"; \
			exit 1; \
		fi; \
		echo "[robot_agent] cookiecutter $(TEMPLATE_DIR) -> $(ROBOTS_DIR)/$(NAME)"; \
		$(ENV_PY) -m cookiecutter "$(TEMPLATE_DIR)" --no-input --overwrite-if-exists \
			--output-dir "$(ROBOTS_DIR)" \
			project_name="$(NAME)" package_name="$(NAME)" \
			$(if $(PORT),default_port="$(PORT)",) $(ARGS); \
	fi

# ── Checks ───────────────────────────────────────────────────────────────────

test: _require-import
	@$(ENV_PY) -c "\
import importlib, sys; \
mods = ['robot_agent.connect', 'robot_agent.connect.serde', 'robot_agent.connect.helpers', \
        'robot_agent.connect.parallel', 'robot_agent.skill_configs', \
        'robot_agent.new_robot', 'robot_agent.copy_skill', 'robot_agent.new_skill']; \
[importlib.import_module(m) for m in mods]; \
print('[robot_agent] %d modules import OK' % len(mods))"
	@$(ENV_PY) -c "\
from robot_agent.connect.serde import dict2str, str2dict; \
import numpy as np; \
from robot_agent.connect.serde import dict2byte, byte2dict; \
a = np.arange(6).reshape(2,3); \
assert (byte2dict(dict2byte({'x': a}))['x'] == a).all(); \
assert str2dict(dict2str({'a': 1})) == {'a': 1}; \
print('[robot_agent] serde round-trip OK')"

clean:
	find $(ROOT) -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find $(ROOT) -type d -name "*.egg-info"  -exec rm -rf {} + 2>/dev/null || true
	@echo "[robot_agent] cleaned."
