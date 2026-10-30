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
- The planning loops reach the robot only through `contract.load_adapter`
  (`core/planning/robot.py` `RobotView`); `make lint-core` is blocking.
- Post-step verify: effects + the manifest's VerifySpecs, no precondition re-check; a skill
  without a sensor gets an UNKNOWN layer (`VerifyResult.unknown`, new field).
- `step_verify` layers: `sensor` / `localization` from the manifest's sensors (was `in_hand`
  / `localization` in AssistLoop only).
- Failures: one normalisation (`run_skill`), codes from `SkillResult` /
  `classify_legacy_failure`, stop / searched decisions from the recovery policy.
- The announcer's Korean phrasebook moved to the robot (kcare_robot `configs/adapter.py`).
