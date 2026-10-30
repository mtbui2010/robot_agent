# robot_agent rules (full text)

The short form of each rule is in [../CLAUDE.md](../CLAUDE.md); the numbers match.
These rules apply to every change in this repo.

## 1. No robot knowledge in the core

No skill names, object names, geometry thresholds, default language or hardware
schema in `robot_agent/`. They come from the `RobotAdapter` / `manifest.yaml`
(`contract.load_adapter`, wrapped by `core/planning/robot.py` `RobotView`):
skills via `bind()` / `skill_for(action)`, sensors via the manifest's
`VerifySpec`s, thresholds via `geometry`, phrases via `i18n`.
`make lint-core` fails on `grasp_succeed|mobile_pose|kcare|kaair|MOBILE_HEIGHT`
in `core/` / `interact/` / `contract/` (comments included).

Why: the core serves several robots (kcare_robot, base_robot, generated
packages); a robot name in the core breaks every other robot. See
[contract.md](contract.md).

## 2. One-way dependencies

`contract` <- `model` <- `plan` / `execute` <- `interact` <- `api`. No
cross-module imports of `_private` names. Never read `dm._ros_node` or other
private attributes from outside their module.

Why: a lower layer must be testable and replaceable without the layers above it.

## 3. Only the planner backends import `embodiplan`

Only `core/planning/backends/` (and `core/planning/action_model.py`) import
`embodiplan`, through its public API only, pinned `embodiplan>=0.2.1,<0.3`
(`pyproject.toml`).

Why: the planner is a plug-in; the rest of the core must not depend on one
planner library or on its private API.

## 4. One source of action rules

Preconditions / effects live in one place: the world / action model
(`core/planning/action_model.py`, see
[action_model_divergence.md](action_model_divergence.md)). Do not add another
copy in a checker, verifier or mapper.

Why: two copies drift apart and then the gate, the verifier and the planner
disagree about what a step does.

## 5. Every run has a `run_id` and a RunRecord

Open-loop runs included. Cancel and pause act on a run, not on the process.
See [run_manager.md](run_manager.md) and [observe.md](observe.md).

Why: every run must be traceable and replayable, and stopping one run must not
kill another.

## 6. Pure logic is testable without ROS or FastAPI

`make test` is pytest and runs in under 30 s, with no ROS, no LLM server and no
network (fake them, see `tests/conftest.py` and `tests/fakes.py`). Behaviour
changes update the golden tests deliberately (`UPDATE_GOLDEN=1 make test`, then
review the diff). Details: [testing.md](testing.md).

Why: the suite runs on every change and on machines without a robot.

## 7. No `eval` / `exec` on LLM output or user input

Use `robot_agent.safe_parse`. In `connections.json`, name callables
(`"codec:<name>"` via `robot_agent.connect.codecs.register_codec`, or
`"pkg.module:func"`); inline `def` / `lambda` source is deprecated
(`codecs._legacy_compile` is the only `exec`; `ROBOT_AGENT_NO_INLINE_CODE=1`
forbids it; `python -m robot_agent.connect.codecs --scan <file>` lists it).

Why: LLM output and config text are untrusted input; executing them is a code
injection.

## 8. Repo hygiene

Never commit `__pycache__` / `*.pyc` / `*.egg-info`. Commit messages say what
changed and why (no "YYYYMMDD -"). SemVer + `CHANGELOG.md` when a contract
changes.

Why: robot packages pin this repo's version and read the changelog to upgrade.

## 9. Core documentation lives in `docs/` of this repo

Not in a robot package. Index: [README.md](README.md).

Why: the core's design must be readable without a specific robot's repo.

## 10. Functions in `core/planning` are at most 80 lines

Checked by `tests/test_wiring_function_size.py`.

Why: the planning loops are shared by several work streams; small functions keep
changes reviewable and merges small.

## 11. Contract types at the boundary

Skills return `SkillResult`, failures carry a `FailureCode`, skills are described
by `SkillSpec`s in the manifest (`robot_agent.contract`, see
[contract.md](contract.md)). Do not pass ad-hoc dicts across the core / robot
boundary in new code.

Why: one typed contract is what lets a new robot plug in without core edits.

## 12. CLAUDE.md stays short

`CLAUDE.md` holds the one-line rules, the test command and links. Explanations,
file maps, examples and history go in `docs/*.md`, linked from `CLAUDE.md` and
from [README.md](README.md).

Why: CLAUDE.md is read on every session; long text there hides the rules.
