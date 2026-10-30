# Observability: RunRecord, metrics, replay, data dir

Code: `robot_agent/observe/` (`paths.py`, `record.py`, `llm.py`, `replay.py`).
API: `robot_agent/api/agent.py` (`/runs/{id}/record`, `/runs/{id}/metrics`),
`robot_agent/api/events.py` (`/schema/events`), `robot_agent/api/models.py`.
Tests: `tests/test_observe_*.py`.

## RunRecord

Every run the RunManager starts gets one JSONL file, keyed by the manager's `run_id`:
`<data_dir('run_records')>/<run_id>/record.jsonl`. This covers the SAGE assist loop, the closed loop,
the open-loop LLM plan and `direct` command runs. `RunManager.on_start` opens it and subscribes it to
the run's events. `RunManager.on_finish` closes it. `run_control.manager()` installs both hooks.

| row kind | content |
|---|---|
| `header` | run_id, robot_id, user_id, role, goal, host, robot_pkg, schema version |
| `context` | loop kind (`assist` / `closed` / `direct` / `open`), task, lang, the loop's resolved config, the LLM config without secrets, ENV, world, object memory |
| `event` | every event published on the run (agent events and run-manager notices). Strings over 4 KB are replaced by `{_omitted, len, sha1}`. `say_audio` is dropped |
| `user` | `run_input` / `run_refused`, with `user_id` and role |
| `llm` | `source` (`robot_agent` / `embodiplan`), `request` (prompt, format, guide, or messages), `response`, `model`, `backend`, `seed`, `temperature`, `latency_ms`, `error`, `messages` |
| `planner` | `generate_plan` / `replan` at the robot_agent boundary: inputs, steps, meta |
| `skill` | skill, params, raw return, `result` (`SkillResult`: ok, code, msg), latency, depth (plan skills nest), the world before it |
| `step` | a finished step: index, status, `world_before`, `world_after` |
| `health` | `load_adapter(robot_pkg).health(node)` at start and end |
| `end`, `snapshot` | outcome. The snapshot row holds the header plus the metrics |

The capture points are the single paths all runs use. No loop code was changed to fill the record.

- `LLMobj.chat` / `chat_guide` (`connect/llm/llm_object.py`) cover every robot_agent LLM client,
  including the pool's `PoolLLM`. A `chat_guide` that calls `chat` is recorded once.
- `embodiplan.LLMBackend.chat`. embodiplan has no public way to inject a client: each planner builds
  its own `LLMBackend`. So `core/planning/backends/pyplanner_grace.py` wraps that public method once,
  at the class. Outside a recorded run or a replay, the wrapper calls the original. *Change request
  to embodiplan:* accept an `llm=` callable in `BasePlanner`, so the wrapper can go.
- `get_planner` returns the backend behind a transparent `RecordingPlanner` proxy. `_p`, `progress`
  and `record_episode` pass through.
- `SkillRegistry.execute`.
- `Run.publish`, through a subscription.
- `UnifiedAgent.run` / `run_direct` call `note_loop` / `note_direct` to write the `context` row.

A call is assigned to a run through `run_control.current_run()`: the thread bound by `begin_run()`,
else the active run. `record.bound(rec)` forces a record for the calling thread. Test doubles that
skip these paths (a fake skill registry, a scripted LLM, a scripted planner) are wrapped with
`record.instrument(loop)`.

Recording is best-effort and never raises into a run. `ROBOT_AGENT_RECORD=0` turns it off. If there is
no data dir (no `AgentState`, as in the API unit tests), nothing is recorded.

One id per run: a planning loop started under a RunManager run takes that run's `run_id` (events,
the `task_runs/<run_id>.jsonl` file and the RunRecord agree). Only a loop run outside the RunManager
(CLI, tests) stamps its own time-based id; the metrics line then gives it as `task_run_id` (an alias,
None when the ids are the same).

## Metrics

When a run ends, one line is appended to `<data_dir('metrics')>` (`run_records/<host>/metrics.jsonl`):

`run_id, task_run_id, robot_id, user_id, goal, loop, started, duration_s, outcome, status,
llm_calls, llm_errors, llm_latency_ms, planner_calls, skill_calls, replans, failure_codes
({code: n} over top-level skill calls), failure_code, failure_device (the deciding failure of a failed
run, from its `done` event), success, verified_success`

`verified_success` means the run succeeded and, for every step that was verified, the last
`step_verify` passed. An open-loop or direct run never verifies its steps, so it can have
`success: true` with `verified_success: false`.

- `GET /runs/{id}/metrics`: the stored line. While the run is active, the metrics so far, with
  `live: true`.
- `GET /runs/{id}/record?kinds=llm,skill&limit=N`: the record rows. Finished runs are read from disk,
  so this also works after a restart. 404 when there is no record.

## Replay

```
python -m robot_agent.observe.replay plan <run_dir|record.jsonl|run_id> [--planner auto|recorded|live]
python -m robot_agent.observe.replay exec <run> --fake [--strict]
```

- `plan` re-runs understanding, the planner and the checker, as the loop with `plan_only`. It diffs
  the `understand` / `clarify` / `gate` / `plan` events against the record.
- `exec --fake` re-runs the whole executive. The fake robot answers each skill call with the recorded
  result of the same call (same skill and params). It diffs every loop event, the sequence of skill
  calls and the outcome. A `direct` run replays through `UnifiedAgent.run_direct`.
- Every LLM call is answered from the record, matched on its request. If the record has no call with
  the same request ("the prompt differs"), the replay reports a `ReplayMismatch` with a diff against
  the nearest recorded request.
- The planner is served in one of two ways. `recorded` returns the recorded `generate_plan` /
  `replan` output for the same task, completed prefix and failed step; it warns when `obs` differs.
  `live` runs the real SAGE / GRACE planner and serves its LLM calls from the record. `auto` picks
  `live` when the record contains embodiplan calls and embodiplan imports.
- Volatile fields (`t`, `run_id`, `plan_sec`, `holding_since`, `latency_ms`, `trace`, ...) are ignored.
- Exit status: 0 identical, 1 differs, 2 mismatch or cannot replay.
- The replay patches a few module globals while it runs (`state.current`, the loops' `current` /
  `reconcile_world`, `skill_configs.ENV`) and then restores them. Use it from a CLI or a test, not
  inside a live agent process.

Limits:

- The robot's own observation (`nm.observe(node)` in the closed loop) is not recorded. A ClosedLoop
  run on a real robot therefore replays with an empty `obs`: a `recorded` planner warns, a `live`
  planner mismatches.
- VLM verification is off in a replay.
- SAGE's live memory (`grace_memory`) is not copied into the record. A `live` replay uses a copy of
  the current file, so episodes added since the run change the prompts.
- Skill results are stored as JSON, so a ROS message in a result becomes a string.

Golden test: `tests/test_observe_record_replay.py` records an assist-loop run on the fake robot (a
clarifying question, a find miss and a replan) through the RunManager, then checks that `replay plan`
and `replay exec --fake` reproduce it exactly.

## Data dir

`robot_agent.observe.paths.data_dir(kind[, common_dir, host])` is the one place these paths are
resolved. `ROBOT_AGENT_DATA_DIR` moves all of them under one root, such as a NAS. When it is unset,
every path is where it was before:

| kind | default (C = common_dir, H = host) | with `ROBOT_AGENT_DATA_DIR=R` |
|---|---|---|
| `task_runs` | `C/task_runs/H/` | `R/task_runs/H/` |
| `run_records` | `C/run_records/H/` | `R/run_records/H/` |
| `metrics` | `C/run_records/H/metrics.jsonl` | `R/run_records/H/metrics.jsonl` |
| `grace_memory` | `C/grace_memory.H.jsonl` | `R/grace_memory.H.jsonl` |
| `object_memory` | `C/object_memory.H.json` | `R/object_memory.H.json` |
| `world_state` | `C/world_state.H.json` | `R/world_state.H.json` |

Reads fall back to the old location. The files that are rewritten in place (the memories and the world
belief) are copied from their old path the first time the new path is requested and does not exist
yet. `read_path` / `search_dirs` do the same lookup for directories; replay uses them to find a run by
id.

## /ws/agent event schema

`GET /schema/events` returns a JSON schema (draft 2020-12) of every event the agent socket sends. The
events are discriminated by `event`, and the schema carries `version` (`EVENT_SCHEMA_VERSION`, SemVer).
The pydantic models in `api/events.py` list the fields sent today and allow extra fields. The agent
does not validate what it sends against them. Adding an event or an optional field is a minor version
bump. A rename, a removal or a newly required field is a major bump and needs a CHANGELOG entry.

## Response models

`api/models.py` defines response models for the routes the dashboard uses: `/runs*`, `/skills*`,
`/skill-configs*` and `/agent/*`, except those that return raw device output. They are registered with
`response_model_exclude_unset=True`, and every model allows extra fields, so the JSON is unchanged. The
benefit is that OpenAPI now names the shapes (for example `RunList`, `RunInfo`, `SkillInfo`,
`WorldStateOut`). The `POST /skills` and `DELETE /skills/{name}` in `api/agent.py` were never reached,
because `skills.py`'s router is included first. They were removed.
