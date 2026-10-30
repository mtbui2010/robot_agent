# robot_agent docs

Design and reference documentation of the core (rule 9 in [../CLAUDE.md](../CLAUDE.md)).
One topic per file, named after the module or feature it covers.

Rules and process:

- [rules.md](rules.md): the full text of every CLAUDE.md rule, with the reason for each
- [testing.md](testing.md): `make test`, fakes, golden tests, lint-core, where new tests go

Core design:

- [contract.md](contract.md): RobotAdapter, manifest, SkillSpec / SkillResult / FailureCode
- [run_manager.md](run_manager.md): run ids, one active run per robot, cancel / pause / inject, intent router
- [observe.md](observe.md): RunRecord, metrics line, replay CLI, `ROBOT_AGENT_DATA_DIR`, `/ws/agent` event schema
- [action_model_divergence.md](action_model_divergence.md): the one action model and where the old copies differed

Planning layer (moved here from kcare_robot/docs):

- [CLOSED_LOOP_ARCHITECTURE.md](CLOSED_LOOP_ARCHITECTURE.md): closed-loop planning (GRACE / SAGE via embodiplan)
- [ACTION_MAPPER_SPEC.md](ACTION_MAPPER_SPEC.md): plan step -> skill call, world-state effects
- [TRACKING_VERIFY_VOICE.md](TRACKING_VERIFY_VOICE.md): run records, layered verification, announcements

Tests: `make test` (see [testing.md](testing.md)).
