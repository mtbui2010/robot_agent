# robot_agent docs

Design and reference documentation of the core (rule 9 in [../CLAUDE.md](../CLAUDE.md)).
One topic per file, named after the module or feature it covers.

Rules and process:

- [rules.md](rules.md): the full text of every CLAUDE.md rule, with the reason for each
- [testing.md](testing.md): `make test`, fakes, golden tests, lint-core, where new tests go

Core design:

- [contract.md](contract.md): RobotAdapter, manifest, SkillSpec / SkillResult / FailureCode
- [architecture.md](architecture.md): layers (contract <- engine <- options), what `tools/lint_arch.py` enforces, size baseline and the plan to shrink it
- [options.md](options.md): the option plug-in interface (spec, keys, hooks, registry), how to write an option, the registered options
- [bt_goal.md](bt_goal.md): option `bt_goal`: goals with a declared behaviour tree run as trees, the others by SAGE (key `mixed`); data, runner, what is not covered
- [ask_next.md](ask_next.md): option `ask_next`: after a failed step, ask the person what to do next
- [heard_check.md](heard_check.md): option `heard_check`: a goal's own quote must be in what was heard
- [heat_safety.md](heat_safety.md): option `heat_safety`: nothing that can burn goes on a heat source
- [reach_check.md](reach_check.md): reachability check (TAMP-lite) between the plan and the arm
- [safety.md](safety.md): the per-robot safety profile (hazard / place / receiver classes and rules as data), the engine, the `refusals` event key
- [metal_microwave.md](metal_microwave.md): the rule 'no metal in a microwave oven' as safety data (sources, words, gaps); no code
- [run_manager.md](run_manager.md): run ids, one active run per robot, cancel / pause / inject, intent router
- [fleet_seams.md](fleet_seams.md): what a fleet manager calls on one robot: `GET /robot/manifest`, `goals` / `client_ref` / `busy` on `/ws/agent`, option `goals_only`, read-only `WS /ws/events`
- [languages.md](languages.md): language packs (data), what a pack may hold, adding a language, scripts
- [dialog.md](dialog.md): the robot's questions, yes / no answers (unclear ones asked again), typed refusals, the understand call's sampling
- [planners.md](planners.md): planner interface, optional capabilities (sub-goal repair), plug-ins by `module:Class` / entry point
- [memory.md](memory.md): the planner's retrieval memory: the manifest seed pool, which runs are recorded, cleaning
- [direct_prompt.md](direct_prompt.md): the direct planner's prompt built from the manifest, places and memory (`planner.notes`, `GET /planner/prompt`), SAGE's prompts from the same generator, evaluating a change
- [verify.md](verify.md): step verification layers, the manifest's VLM verify, sensor health, the LLM-call budget
- [guard.md](guard.md): precondition layers (gate, per-step guard, skill), reconcile with the sensors, provenance
- [search.md](search.md): search with a belief: location prior, one LLM ranking per object, searched places as a hard constraint
- [observe.md](observe.md): RunRecord, metrics line, replay CLI, `ROBOT_AGENT_DATA_DIR`, `/ws/agent` event schema
- [trace.md](trace.md): the run trace of option `trace` (one JSONL per run: events, lab entries, LLM calls, recovery decisions), schema 1
- [action_model_divergence.md](action_model_divergence.md): the one action model and where the old copies differed

- [training_images.md](training_images.md): the two docker images for training policies (openpi, LeRobot) and the `vla-train` command (init / run / resume / status / stop): build, containers, run file

Planning layer (moved here from kcare_robot/docs):

- [CLOSED_LOOP_ARCHITECTURE.md](CLOSED_LOOP_ARCHITECTURE.md): closed-loop planning (SAGE via embodiplan)
- [ACTION_MAPPER_SPEC.md](ACTION_MAPPER_SPEC.md): plan step -> skill call, world-state effects
- [TRACKING_VERIFY_VOICE.md](TRACKING_VERIFY_VOICE.md): run records, layered verification, announcements

Tests: `make test` (see [testing.md](testing.md)).
- [env.md](env.md): the ENV contract (places, people), the SAGE-v2 manifest keys and options
