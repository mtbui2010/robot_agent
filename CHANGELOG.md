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
- The planning loops reach the robot only through `contract.load_adapter`
  (`core/planning/robot.py` `RobotView`); `make lint-core` is blocking.
- Post-step verify: effects + the manifest's VerifySpecs, no precondition re-check; a skill
  without a sensor gets an UNKNOWN layer (`VerifyResult.unknown`, new field).
- `step_verify` layers: `sensor` / `localization` from the manifest's sensors (was `in_hand`
  / `localization` in AssistLoop only).
- Failures: one normalisation (`run_skill`), codes from `SkillResult` /
  `classify_legacy_failure`, stop / searched decisions from the recovery policy.
- The announcer's Korean phrasebook moved to the robot (kcare_robot `configs/adapter.py`).
