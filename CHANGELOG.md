# Changelog

robot_agent follows SemVer for its contract (`robot_agent.contract`, the
manifest format, the event schema). See `docs/contract.md`.

## Unreleased

### Contract
- Manifest: optional top-level `description` (`Manifest.description`) and skill-level
  `description` (string, no check_manifest warning): the robot's / skill's text in the planner
  prompts (core/planning/robot_prompt.py, docs/planner_guide.md). Old manifests load unchanged.

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

### Planner prompts (docs/planner_guide.md)
- SAGE (`grace` / `sage`) gets the robot's plan actions and description from the manifest
  (embodiplan >= 0.2.4 `SAGEPlanner(actions, robot_description)`); `ROBOT_AGENT_SAGE_ROBOT_PROMPT=0`
  restores embodiplan's household prompts. Plug-in planners get `robot_description` too.
- Planner Guide placeholders `{ROBOT}`, `{ACTIONS}`, `{EXAMPLES}` (seed pool + verified live memory,
  top `ROBOT_AGENT_GUIDE_EXAMPLES` (3), leave-one-out with `ROBOT_AGENT_GUIDE_LOO=1`); a guide
  without them is sent unchanged. New seed versions (`guide_v2`) are added once, inactive, to an
  existing guides.json (`seeded` list).
- `GET /guides?planner=` adds `used_by`, `active_planner`, `active_planner_uses_guide` and per-version
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
