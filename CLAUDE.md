# robot_agent rules

robot_agent is the robot-agnostic core (planning, execution, interaction, API).
These rules apply to every change. Design docs live in `docs/` (see `docs/README.md`).

1. **No robot knowledge in the core.** No skill names, object names, geometry
   thresholds, default language or hardware schema in `robot_agent/`. They come
   from the `RobotAdapter` / `manifest.yaml`. `make lint-core` counts the
   offenders (`grasp_succeed|mobile_pose|kcare|kaair|MOBILE_HEIGHT`); it is
   report-only now and becomes blocking when the adapter lands.
2. **One-way dependencies:** `contract` <- `model` <- `plan` / `execute` <-
   `interact` <- `api`. No cross-module imports of `_private` names. Never read
   `dm._ros_node` or other private attributes from outside their module.
3. **Only `plan/backends/*` imports `embodiplan`**, through its public API only,
   pinned `embodiplan>=0.2,<0.3`.
4. **One source of action rules** (preconditions / effects): the world / action
   model. Do not add another copy in a checker, verifier or mapper.
5. **Every run has a `run_id` and a RunRecord**, open-loop included. Cancel and
   pause act on a run, not on the process.
6. **Pure logic is testable without ROS or FastAPI.** `make test` is pytest and
   runs in under 30 s, with no ROS, no LLM server and no network (fake them, see
   `tests/conftest.py` and `tests/fakes.py`). Behaviour changes update the golden
   tests deliberately (`UPDATE_GOLDEN=1 make test`, then review the diff).
7. **No `eval` / `exec` on LLM output or user input.** Use
   `robot_agent.safe_parse`. For `connections.json`, use registered codecs, not
   `exec` of strings.
8. **Repo hygiene:** never commit `__pycache__` / `*.pyc` / `*.egg-info`. Commit
   messages say what changed and why (no "YYYYMMDD -"). SemVer + CHANGELOG when a
   contract changes.
9. **Core documentation lives in `docs/`** of this repo, not in a robot package.
