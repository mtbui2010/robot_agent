# Changelog

robot_agent follows SemVer for its contract (`robot_agent.contract`, the
manifest format, the event schema). See `docs/contract.md`.

## Unreleased

### Contract
- `VerifySpec.expect` (manifest `verify: [{kind: sensor, skill: ..., expect: false}]`):
  what the sensor's `isdone` must be; default true. Unknown before; old manifests
  load unchanged.
- `NamemapAdapter.to_loc` / `to_obj`: the robot's names, passed through from the namemap.
- Adapter extras read by the core (optional): `is_openable(key, entry)`, `quiet_skills`,
  `i18n.phrases` keys `verb.<Action>`, `robot_description`, `verify_failed.<Action>`.

### Core
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
