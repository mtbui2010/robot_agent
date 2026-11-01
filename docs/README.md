# robot_agent docs

Design and reference documentation of the core (rule 9 in [../CLAUDE.md](../CLAUDE.md)).
One topic per file, named after the module or feature it covers.

Rules and process:

- [rules.md](rules.md): the full text of every CLAUDE.md rule, with the reason for each
- [testing.md](testing.md): `make test`, fakes, golden tests, lint-core, where new tests go

Core design:

- [contract.md](contract.md): RobotAdapter, manifest, SkillSpec / SkillResult / FailureCode
- [run_manager.md](run_manager.md): run ids, one active run per robot, cancel / pause / inject, intent router
- [dialog.md](dialog.md): the robot's questions, yes / no answers (unclear ones asked again), typed refusals, the understand call's sampling
- [planners.md](planners.md): planner interface, optional capabilities (sub-goal repair), plug-ins by `module:Class` / entry point
- [memory.md](memory.md): the planner's retrieval memory: the manifest seed pool, which runs are recorded, cleaning
- [direct_prompt.md](direct_prompt.md): the direct planner's prompt built from the manifest, places and memory (`planner.notes`, `GET /planner/prompt`), SAGE's prompts from the same generator, evaluating a change
- [verify.md](verify.md): step verification layers, the manifest's VLM verify, sensor health, the LLM-call budget
- [guard.md](guard.md): precondition layers (gate, per-step guard, skill), reconcile with the sensors, provenance
- [search.md](search.md): search with a belief: location prior, one LLM ranking per object, searched places as a hard constraint
- [observe.md](observe.md): RunRecord, metrics line, replay CLI, `ROBOT_AGENT_DATA_DIR`, `/ws/agent` event schema
- [action_model_divergence.md](action_model_divergence.md): the one action model and where the old copies differed

Planning layer (moved here from kcare_robot/docs):

- [CLOSED_LOOP_ARCHITECTURE.md](CLOSED_LOOP_ARCHITECTURE.md): closed-loop planning (GRACE / SAGE via embodiplan)
- [ACTION_MAPPER_SPEC.md](ACTION_MAPPER_SPEC.md): plan step -> skill call, world-state effects
- [TRACKING_VERIFY_VOICE.md](TRACKING_VERIFY_VOICE.md): run records, layered verification, announcements

Tests: `make test` (see [testing.md](testing.md)).
