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
- Golden traces of the AssistLoop (`tests/golden/`, for refactors of the core that must not change behaviour): each
  `tests/golden/cassettes/*.cassette.json` holds a scenario (made-up flat, fakebot / fakehome, scripted model and planner,
  options, pack data such as `bt_goal` trees) and what one run of the REAL loop did: the model calls (prompt hash -> answer),
  the planner calls, every skill call with its result, the events (no clocks / run ids) and the end state (robot, belief,
  lab log, run record). `pytest tests/golden` replays them (the model answers from the cassette) and prints a per-section
  diff on any difference. Scenarios live in `tests/golden/scenarios.py`; `python tools/golden_record.py --record` writes
  only the MISSING cassettes and refuses to overwrite one (a meant change deletes the cassette in a reviewed commit).
  `python tools/golden_mutation_check.py` breaks ten behaviours one at a time by monkeypatching in child processes (gate,
  events, recovery count, yes/no reading, behaviour-tree fallback, replan budget, goals_only, plan_only, stop, menu stop
  word) and exits 0 only if every one is caught by at least one cassette (about 5 s).
- Golden extension for the core generalization (Step S01 of `docs/proposals/generalize_core_design.md`, recorded on the old
  code): scenario sets `tests/golden/scenario_sets/<step>_<topic>.py` (one file per step, loaded after `scenarios.py` in
  file-name order), mutation sets `tools/golden_mutations/<step>_<topic>.py` (loaded after the first ten; an entry may name
  snapshot tests as a third element for a table no run reads), the made-up line cart `tests/fakeline` (NOT household: Say,
  WaitForUnload, Inspect, Fasten; `fakeline.cart` has no arm). Opt-in scenario keys: `capture` (`planner_io`, `llm_kw`,
  `prompts`, `reconcile`, `trace`: extra sections of a NEW cassette, `FORMAT` stays 1), `environ` (ROBOT_AGENT_* variables;
  every ROBOT_AGENT_* variable is cleared before every run), `pack` (Step S02's `RobotView.use_pack`), `pause_after`; robot
  keys `skills` (scripted results of skills the FakeRobot lacks), `answers_in_order`, `drops`. A new cassette carries the
  `commit` it was recorded on (refused with uncommitted `robot_agent/` changes); `tests/golden/base_commits.json` lists the
  allowed ones and `tests/golden/test_golden_order.py` checks them. Non-cassette goldens (`tests/golden/snapshots.py`,
  written only by `python tools/golden_record.py --snapshots`, never overwritten): manifest, loop-config and planner-prompt
  snapshots of the three made-up packs, the core phrase table, one recorded run for `observe/replay.py`
  (`tests/test_manifest_snapshot.py`, `tests/test_loop_config_snapshot.py`, `tests/golden/test_planner_prompt_golden.py`,
  `tests/test_replay_smoke.py`); `tests/test_hooks_frozen.py` freezes the hook and option tables. `tests/legacy_ref/` holds
  reference copies of moved functions for differential tests (never imported by `robot_agent`). Determinism: the
  `VOLATILE` keys are dropped at any depth, so a capture section is never named after one (the trace rows are `trace_rows`).
- `make lint-core`: robot names in `core/` / `interact/` / `contract/` (strict;
  `LINT_STRICT=0` only reports). See [rules.md](rules.md) rule 1.
- `make lint-core` also runs `tools/lint_arch.py` (layers, size, option wiring; baseline `tools/arch_baseline.json`, shrink it with
  `python tools/lint_arch.py --update`); `tests/test_architecture.py` runs the same checks and tests the linter. See [architecture.md](architecture.md).
- `tests/test_wiring_function_size.py`: no function over 80 lines in
  `core/planning` (rule 10).
- `make test-smoke`: import smoke test of an installed package.
- New tests: `tests/test_<area>_<topic>.py`; tests for a fix stream
  `tests/test_fix_<name>_*.py`, for a v2 stream `tests/test_v2_<topic>_*.py`.
- Whole-system scenarios (several repos together) live in the `scenarios` repo,
  see [../../scenarios/CLAUDE.md](../../scenarios/CLAUDE.md).
