# Changelog

robot_agent follows SemVer for its contract (`robot_agent.contract`, the
manifest format, the event schema). See `docs/contract.md`.

## Unreleased

### Grounding chain v2 and abstention re-ask (options `go_steps`, `kind_anchor`, `name_canon`, `reask_abstain`; env `ROBOT_AGENT_<NAME>`, manifest `planner.<name>`; all default off)
- `go_steps`, `kind_anchor`, `name_canon`: three deterministic steps in `_ground_understood` after `shared_listener`
  (`goal_fix.go_steps`, `kind_anchor_open`, `canon_names`; `lexicon`-style `OPEN_WORDS`). `reask_abstain`: one directed
  re-understand of a goal-less "where is it" reading (`goal_fix.abstains_on_location`, `AssistLoop._reask_abstention`).
  New phrase `q_which_known`. Off: byte-identical events (tests `tests/test_sage_lab_r2_grounding.py`).

### Accept goals from the caller (option `accept_goals`, env `ROBOT_AGENT_ACCEPT_GOALS`, manifest `planner.accept_goals`; default off)
- With the option on, `AssistLoop.run_blocking(task, lang, goals=[...])` skips the understand step and starts from the
  goals it is given (`{type, object, from, to}`), grounded on the ENV and not filtered. An `understand` event with the
  reason `goals given by the caller` is emitted. No goals given, or the option off: the usual understanding, byte-identical
  events (test `tests/test_accept_goals.py`).

### Person goals (option `person_goals`, env `ROBOT_AGENT_PERSON_GOALS`, manifest `planner.person_goals`; default off)
- A mixed request (go / say goals and moves) gives the planner the template goals as fixed sub-goals with the
  loop's own steps (`goals.compile_steps`); the planner expands the moves only (embodiplan 0.2.7
  `fixed_subgoals`). New gate operators `recipient` (a hand-over for a hand goal goes to the goal's person, after
  a MoveTo) and `unopenable` (an Open / Close of what the map says cannot be opened, named by no goal, goes).
  `goals.dedupe` drops an exact repeat of a goal. A fixed block gets no LLM edit and is never emptied. Off: nothing
  changes.

### Direct planner steps run in the closed loops (docs/direct_prompt.md "Steps and binding")
- `llm_direct` steps keep the robot's call (`skill`, `params` read as Structured mode reads them, `flags`)
  and carry the skill's manifest action (`move` -> `MoveTo`; `""` for a skill with no plan action). Binding
  honours the call in one place, `core/planning/concrete.py` (`RobotView.bind`, `ActionMapper.to_skill`),
  after checking the skill exists (`RobotView.registry`: plan skills, aliases); grace / SAGE steps bind
  as before. Fixes ClosedLoop with planner `direct` (and SAGE with generator `llm_direct`) failing every
  step with "no skill for action 'move'". `ActionMapper(namemap, registry=None)`; `RobotView.knows_skill`.

### Direct planner prompt (docs/direct_prompt.md) — Planner Guide removed
- The direct planner (`llm_direct`, dashboard `direct`, and the open-loop path of `UnifiedAgent.run`) builds
  its prompt by code each time it plans (`core/planning/direct_prompt.py`): `describe_robot`, the skill
  lines, the dashboard's plan skills, the ENV places, manifest `planner.notes`, the k most similar
  examples (seed pool + verified live memory), fixed answer rules, the command. Both callers ask through
  `direct_prompt.plan_answer` (`llm.chat(prompt=...)`, recorded / replayed as `chat`).
- Manifest: optional `planner.notes` (list of non-empty strings, <= 300 characters each).
- New `GET /planner/prompt?command=&planner=&k=&leave_one_out=` -> `{prompt, examples, planner, uses_prompt}`.
- Env: `ROBOT_AGENT_DIRECT_EXAMPLES` (default 3), `ROBOT_AGENT_DIRECT_LOO`; the old names
  `ROBOT_AGENT_GUIDE_EXAMPLES` / `ROBOT_AGENT_GUIDE_LOO` are still read when the new ones are unset.
- Removed: `core/guide_manager.py` (`GuideManager`, `resolve_guide`, `render_guide`, `preview_guide`,
  `reconstruct_plan`, `GUIDE_USERS`, `planner_uses_guide`), `api/guides.py` (`GET/POST /guides`,
  `GET/PUT/DELETE /guides/{name}`, `POST /guides/{name}/activate|rename`, `POST /guides/preview`),
  `AgentState.guides`, the structured (`format`) branch of the direct planner and the
  `LOCATIONS_HERE` / `PLAN_SKILLS_HERE` / `{ROBOT}` / `{ACTIONS}` / `{EXAMPLES}` placeholders. A plug-in
  says `uses_direct_prompt = True` (was `uses_guide`). An old `common/guides.json` is never read.

### Contract
- Optional adapter members `pose(node) -> {x, y, yaw?, ts? | age_s?}` and `container_states(node) ->
  {key: bool | None}`, read by the SAGE-v2 reconcile (`core/planning/reconcile.py`, docs/guard.md).
  `RobotView.reconcile(..., places=, arrive_tol=, report=)`. `WorldState.provenance` (`mark`,
  `mark_edited`, `provenance_of`, `provenance_view`); `to_dict()` gains a `provenance` key (others
  unchanged); `PUT /agent/world` marks the edited fields `operator`. AssistLoop option `reconcile`
  (`ROBOT_AGENT_RECONCILE`, default 1).
- Manifest: optional top-level `description` (`Manifest.description`) and skill-level
  `description` (string, no check_manifest warning): the robot's / skill's text in the planner
  prompts (core/planning/robot_prompt.py, docs/direct_prompt.md). Old manifests load unchanged.

- `FailureCode.BUDGET_EXCEEDED` (`budget_exceeded`): the run used its LLM-call budget (recovery STOP,
  not retried). Appended after `unknown`.
- Manifest `verify: [{kind: vlm, prompt}]` is now run (`core/planning/verify_vlm.py`) through the
  adapter's optional `vlm_ask(prompt, step=, node=)`; `capabilities.vlm_verify: false` keeps it off
  (`ROBOT_AGENT_VLM_VERIFY` / the `vlm_verify` option win). `NamemapAdapter.vlm_ask` passes through to
  the namemap's.
- `RobotAdapter.health()` keys the core reads: `frame_age_s`, `fps`, `depth`, `pose_age_s`, `ok`;
  limits in manifest `geometry`: `frame_max_age_s`, `min_fps`, `need_depth`, `pose_max_age_s`,
  `vlm_min_conf` (`core/planning/sensor_health.py`, `ROBOT_AGENT_SENSOR_HEALTH`). docs/verify.md.
- `ROBOT_AGENT_LLM_BUDGET` default 20 (was 40); plan edits and the gate's refinement count too
  (`subgoal_repair.repair_plan(..., budget=)`); the edit rounds stop when the findings do not decrease. i18n key `llm_budget`.
- `connect.ros.node.NodeAgent.rev_time` / `rev_period`: receive time and smoothed gap of a subscription.
- Manifest: optional `planner:` section, `planner: {seed_memory: <JSONL, relative to the
  manifest>}` (`Manifest.planner`, `Manifest.seed_memory`, `Manifest.planner_path(key)`).
  Absent = no seed pool; old manifests load unchanged. `check_manifest` errors when the seed
  file is missing or a line is not an episode, and warns on an unknown `planner.*` key.
- `VerifySpec.expect` (manifest `verify: [{kind: sensor, skill: ..., expect: false}]`):
  what the sensor's `isdone` must be; default true. Unknown before; old manifests
  load unchanged.
- `NamemapAdapter.to_loc` / `to_obj`: the robot's names, passed through from the namemap.
- Adapter extras read by the core (optional): `is_openable(key, entry)`, `quiet_skills`,
  `i18n.phrases` keys `verb.<Action>`, `robot_description`, `verify_failed.<Action>`.

### Planner prompts (docs/direct_prompt.md)
- SAGE (`grace` / `sage`) gets the robot's plan actions and description from the manifest
  (embodiplan >= 0.2.4 `SAGEPlanner(actions, robot_description)`); `ROBOT_AGENT_SAGE_ROBOT_PROMPT=0`
  restores embodiplan's household prompts. Plug-in planners get `robot_description` too.
- (removed again, see above) Planner Guide placeholders `{ROBOT}`, `{ACTIONS}`, `{EXAMPLES}` (seed pool + verified live memory,
  top `ROBOT_AGENT_GUIDE_EXAMPLES` (3), leave-one-out with `ROBOT_AGENT_GUIDE_LOO=1`); a guide
  without them is sent unchanged. New seed versions (`guide_v2`) are added once, inactive, to an
  existing guides.json (`seeded` list).
- (removed again) `GET /guides?planner=` adds `used_by`, `active_planner`, `active_planner_uses_guide` and per-version
  `placeholders`; `POST /guides/preview` renders a guide for a command.

### Interaction (run manager, dialog)
- MODIFY / ADD / DROP / NEW said during a run are applied by AssistLoop at the next step boundary
  (`core/planning/goal_change.py`, `AssistLoop._apply_pending`); a change it cannot read is told to the
  person (`goal_change` event, `applied: false`). ClosedLoop says it cannot apply them.
- `TaskStack` / `TaskFrame` implemented (was a Protocol stub); `RunManager.suspend` / `resume_task`,
  `RunManager.on_resume` hooks (after a pause ends or a suspended task is taken back);
  `Run.outranked_by(role)`. New events: `goal_change`, `task_suspended`, `task_resumed`,
  `run_stop_deferred`.
- `interact/dialog.py`: the robot's questions are registered on the run (`Run.set_awaiting`), so an
  answer typed by any client is an ANSWER; `Run.poll_answer`. `run_control.ask_person`, `yes_no`.
- A stop during a manifest `interruptible: false` skill waits for it to end (`run_control.uninterruptible`,
  `run_skill(..., interruptible=)`, `RobotView.interruptible(skill)`, `Run.begin_critical` /
  `end_critical`); dashboard Stop and e-stop unchanged.
- `run_control.call_cancellable` / `is_cancel`: LLM and planner calls no longer delay a stop.

### Core
- Planner memory (docs/memory.md): runs are recorded into SAGE's live memory only when their
  goals were seen to hold (`core/planning/episode_memory.py`; `ROBOT_AGENT_MEMORY_RECORD` /
  option `memory_record` = `observed` (default) | `belief` (old rule) | `off`). The plain
  ClosedLoop no longer records by default. The record carries `evidence` and `run_id`
  (`GraceBackend.record_episode(task, steps, meta=None)` returns whether SAGE recorded it).
  `GraceBackend(seed_memory=)` / `RobotView.seed_memory()`: the manifest seed pool is SAGE's
  seed; `leave_one_out` is passed through.
- Executive (fix stream `execute`, D5 D7 D8 D12 D13 D17 D25 D26):
  - AssistLoop is done only when every on / in goal is looked at (`goal_check` event: MoveTo back
    + Find at the destination; a goal inside a closed drawer stays on the belief as `ok: null`).
    A goal seen not to hold goes to SAGE once more, else the run fails (code `dropped` / `absent`).
    `ROBOT_AGENT_GOAL_CHECK=belief` (option `goal_check`) restores the old belief-only check.
  - The recovery policy is applied by FailureCode: WAIT_HEALTH (`adapter.health()`, wait
    `health_wait_s`, the same step again in place), HEALTH_CHECK (an unhealthy robot stops),
    STOP once a rule's retries are used up (camera down 3 times, a second hardware fault).
  - `RobotView.reconcile(world, node, sense)`: `adapter.reconcile` + the declared holding sensor;
    called at run start (both loops) and after a pause (`run_control.pause_point`); a `reconcile`
    event when the belief changed. After a pause a Pick on an old Find looks again first.
  - A failed run's `done` event (both loops) carries `code`, `device`, `evidence` (AssistLoop: the
    first failure not recovered since; ClosedLoop also `msg`, `failed_step`); `TaskRecord.failure`;
    RunRecord metrics `failure_code` / `failure_device`.
  - One run id: the loops take the RunManager run's `run_id` (`task_run_id` in metrics is then
    None); `/ws/agent` finishes a run from `done.status` (failed / refused -> RunState failed).
  - Classifier: "no longer in the gripper" = dropped, "stuck" / "jammed" = blocked.
- The planning loops reach the robot only through `contract.load_adapter`
  (`core/planning/robot.py` `RobotView`); `make lint-core` is blocking.
- Post-step verify: effects + the manifest's VerifySpecs, no precondition re-check; a skill
  without a sensor gets an UNKNOWN layer (`VerifyResult.unknown`, new field).
- `step_verify` layers: `sensor` / `localization` from the manifest's sensors (was `in_hand`
  / `localization` in AssistLoop only).
- Failures: one normalisation (`run_skill`), codes from `SkillResult` /
  `classify_legacy_failure`, stop / searched decisions from the recovery policy.
- The announcer's Korean phrasebook moved to the robot (kcare_robot `configs/adapter.py`).
