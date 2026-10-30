# robot_agent

The robot-agnostic core (planning, execution, interaction, API) that robot packages plug into.
Full rule text and reasons: [docs/rules.md](docs/rules.md). Docs index: [docs/README.md](docs/README.md).

## Rules

1. No robot names or robot data in the core; get them from the RobotAdapter / manifest ([contract](docs/contract.md)).
2. Keep dependencies one-way (`contract` <- `model` <- `plan` / `execute` <- `interact` <- `api`); no `_private` access across modules.
3. Import `embodiplan` only in `core/planning/backends/` and `core/planning/action_model.py`, public API, `>=0.2.1,<0.3`.
4. Keep one source of action rules (the action model); never copy them into a checker, verifier or mapper.
5. Give every run a `run_id` and a RunRecord; cancel / pause act on the run ([run_manager](docs/run_manager.md)).
6. Keep pure logic testable without ROS, FastAPI, LLM server or network; update golden tests on purpose.
7. Never `eval` / `exec` LLM output or user input; use `robot_agent.safe_parse` and named codecs.
8. Never commit `__pycache__` / `*.pyc` / `*.egg-info`; commit messages say what and why; SemVer + CHANGELOG on contract changes.
9. Put core documentation in `docs/` of this repo, not in a robot package.
10. Keep every function in `core/planning` at most 80 lines.
11. Use the contract types (`SkillSpec`, `SkillResult`, `FailureCode`) at the core / robot boundary.
12. Keep this file short: one-line rules and links; details go in `docs/*.md`.

## Test

`make test` (pytest, < 30 s, no ROS / network) and `make lint-core`. Details: [docs/testing.md](docs/testing.md).

## Docs

- [docs/rules.md](docs/rules.md): full rules with reasons
- [docs/testing.md](docs/testing.md): test commands, fakes, golden tests
- [docs/contract.md](docs/contract.md): adapter, manifest, contract types
- [docs/run_manager.md](docs/run_manager.md): runs, cancel / pause, intent router
- [docs/dialog.md](docs/dialog.md): dialog, yes/no answers, typed refusals, understand sampling
- [docs/planners.md](docs/planners.md): planner interface, plug-ins by module:Class / entry point
- [docs/memory.md](docs/memory.md): planner memory (seed pool, recording, cleaning)
- [docs/observe.md](docs/observe.md): RunRecord, metrics, replay, event schema
- [docs/action_model_divergence.md](docs/action_model_divergence.md): the one action model
- [docs/CLOSED_LOOP_ARCHITECTURE.md](docs/CLOSED_LOOP_ARCHITECTURE.md): closed-loop planning
- [docs/ACTION_MAPPER_SPEC.md](docs/ACTION_MAPPER_SPEC.md): plan step -> skill call
- [docs/TRACKING_VERIFY_VOICE.md](docs/TRACKING_VERIFY_VOICE.md): records, verification, announcements
