# The planner's retrieval memory

SAGE (the `grace` / `sage` planners, embodiplan) puts the most similar past plans into its
prompts. They come from two pools (embodiplan `docs/memory.md` has the file format and the
cleaning tool):

| pool | file | who fills it |
|---|---|---|
| seed | the robot's manifest `planner.seed_memory` (JSONL, shipped with the robot) | a person, verified offline |
| live | `data_dir("grace_memory")` = `<common>/grace_memory.<host>.jsonl` (option `live_path`) | the loops, after a verified run |

## Seed pool (manifest)

```yaml
planner:
  seed_memory: ../planner/seed_memory.jsonl   # relative to manifest.yaml
```

The key is optional; a robot without it starts SAGE with no seed. `Manifest.seed_memory`
resolves the path, `RobotView.seed_memory()` hands it to the planner
(`ClosedLoop._make_planner` -> `GraceBackend(seed_memory=...)` -> SAGE's `gt_path`, unless a
`gt_path` is given). embodiplan < 0.2.3 cannot read a JSONL seed: GraceBackend logs a warning
and goes on without it. `check_manifest` fails when the file is missing or a line is not an
episode (`task` + `plan_text` / `steps`). Write seed plans in the robot's own place names and
check them with the action model under the manifest's capabilities (kcare_robot:
`tests/test_seed_memory.py`).

## What the live pool learns: `core/planning/episode_memory.py`

A wrong plan in memory is copied into the next plans, so a run is recorded only when its goals
were **seen** to hold after the task (`ROBOT_AGENT_MEMORY_RECORD`, option `memory_record`):

| mode | AssistLoop (`sage`) | ClosedLoop (`grace`) |
|---|---|---|
| `observed` (default) | success, and every `on` / `in` goal's latest `goal_check` is `ok: true`; a state goal (`open` / `close`, no sensor) must not be `ok: false` | never (it does not look at its goals) |
| `belief` (the old rule) | every success | every success |
| `off` | never | never |

Not recorded in `observed` mode: a goal that could not be looked at (inside a closed drawer,
no way back to the place), `goal_check=belief`, a plan the loop compiled itself. SAGE then
applies its own gate (the plan passes the action model from its start belief) and stores the
record with `verified: true`, `init_state`, `evidence` (`goal_observation` / `belief`) and the
`run_id`.

## Cleaning the deployed pool

The live pool written before this rule holds false successes, duplicates and benchmark tasks.
Clean a copy, never the file the agent is appending to:

```bash
python -m embodiplan.memory_tools clean <common>/grace_memory.<host>.jsonl \
    --out /tmp/grace_memory.clean.jsonl --exclude-tasks <test tasks> \
    --capabilities <robot>/configs/manifest.yaml --report
```

Then stop the agent, keep the old file as a backup and move the clean file in its place.
For an evaluation, run SAGE with `leave_one_out=True` so it never retrieves the test task
itself.
