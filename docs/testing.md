# Testing robot_agent

- `make test`: pytest from the source tree (`tests/conftest.py` puts this checkout
  first on `sys.path`), no install needed, under 30 s. `make test ARGS="-k name"`
  passes arguments; `make test PYTHON=<python with pytest>` picks the interpreter.
- Without make (as the acceptance checker runs it):
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:../embodiplan python -m pytest -q -p no:cacheprovider -o addopts= -W ignore`
- No ROS, no LLM server, no network: use the fakes in `tests/conftest.py` and
  `tests/fakes.py` (fake robot, LLM and planner) and `tests/fakebot/`.
- Golden tests (`tests/test_golden_task_runs.py`): a behaviour change updates them
  on purpose with `UPDATE_GOLDEN=1 make test`, then review the diff.
- `make lint-core`: robot names in `core/` / `interact/` / `contract/` (strict;
  `LINT_STRICT=0` only reports). See [rules.md](rules.md) rule 1.
- `tests/test_wiring_function_size.py`: no function over 80 lines in
  `core/planning` (rule 10).
- `make test-smoke`: import smoke test of an installed package.
- New tests: `tests/test_<area>_<topic>.py`; tests for a fix stream
  `tests/test_fix_<name>_*.py`, for a v2 stream `tests/test_v2_<topic>_*.py`.
- Whole-system scenarios (several repos together) live in the `scenarios` repo,
  see [../../scenarios/CLAUDE.md](../../scenarios/CLAUDE.md).
