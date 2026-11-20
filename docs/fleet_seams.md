# Fleet seams: what a fleet manager calls on one robot

The robot side of the contract in robot_fleet `docs/SPEC.md` section 5 (the spoke). One robot per process: the process is the robot, so no
field below carries a robot id. Everything here is opt-in: a client that sends none of the new fields and opens none of the new routes gets
exactly what it got before (the default path of `/ws/agent` is pinned by `tests/test_run_manager_api.py` and
`tests/test_stop_honest_and_empty_ws.py`). Code: `robot_agent/api/robot_info.py`, `robot_agent/api/run_start.py`,
`robot_agent/api/event_stream.py`, `robot_agent/options/goals_only.py`; tests: `tests/test_fleet_seams.py`.

## `GET /robot/manifest` (read-only)

```json
{"robot": "zorbot", "version": "2.1", "plan_actions": ["MoveTo", "Pick", "Place"],
 "capabilities": {"base": true, "arms": 1}, "places": ["shelf@galley", "crate@galley"], "site": "flat_one"}
```

| field | from |
|---|---|
| `robot`, `version`, `capabilities` | the robot package's manifest (`manifest.yaml`) |
| `plan_actions` | `RobotView.plan_actions`, sorted: the abstract actions a plan may use on this robot |
| `places` | the ENV keys of the ACTIVE site (this robot's own place names; another robot's map may name them differently) |
| `site` | the active site (location) name; `""` when the configs have no sites |

503 when the robot package's manifest cannot be loaded. It starts nothing. `GET /` is unchanged.

## `WS /ws/agent`: the new fields of the first message

```json
{"prompt": "<text>", "lang": "en", "planner": "sage",
 "goals": [{"type": "on", "object": "cup", "from": "", "to": "table@living room"}],
 "options": {"accept_goals": "1"},
 "client_ref": {"plan_id": "p7", "goal_id": "g1", "attempt": 1, "hash": "<content hash>"},
 "busy": "refuse"}
```

`planner: "sage"` (or a robot started with both `ROBOT_AGENT_CLOSED_LOOP=1` and `ROBOT_AGENT_PLANNER=sage`) is needed for `goals` and
`goals_only`: only the AssistLoop takes typed goals and runs options (`UnifiedAgent.run`, `planner_names.loop_choice`). Without it the
robot runs the prompt on its default path (the open-loop direct planner; `ROBOT_AGENT_PLANNER=sage` alone does not change that), which
ignores both and EXECUTES the prompt. The SPEC's messages (section 5) do not send `planner` today: a fleet manager must add it.

### `goals` (a list of typed goals)

How goals reached the loop before: `AssistLoop.run_blocking(task, goals=...)` took them, and `_take_given_goals` used them in place of the
understand step when the run option `accept_goals` is on (`assist_loop.py`, the line `r.given_goals = ...` in `_run_blocking`). But
`UnifiedAgent.run` never passed `goals`, so no WebSocket client could send any (only in-process callers such as the bench harness).
The route now (no new parameter of `UnifiedAgent.run`, no line added to it or to `assist_loop.py`): `/ws/agent` puts the list into the
run's `options` as `options["goals"]` (`run_start.with_goals`, like `generator`), and `UnifiedAgent.run` passes it to the AssistLoop's
`run_blocking(..., goals=...)` (`unified_agent._given_goals`; the plain ClosedLoop gets no argument). `goals` is not an option key (not in
`ASSIST_OPTIONS`), so it never reaches the loop's settings. Items that are not dicts are dropped; an empty or non-list `goals` is ignored.

- With `options.accept_goals` on, the goals replace the understand step (no LLM call, no question, no read-back; grounded on this robot's
  ENV, not filtered: docs/options.md and `tests/test_accept_goals.py`). With it off they are ignored and the prompt is understood as text.
- No prompt (or a blank one): the prompt is written from the goals, `goals.goal_text` (`"on cup from shelf to table@living room"`), so
  the run has a goal text for its record and the dashboard.

### `client_ref` (idempotent keyed start)

The key is `(plan_id, goal_id)`; `attempt` (an integer, default 0) and `hash` (the caller's hash of the goal's content) are compared. The
robot looks the key up in the runs it keeps (`GET /runs`: the active run and the last 50 finished, `run_manager.MAX_FINISHED`) and decides
under one lock with the start, so two resends cannot both start (`run_start.verdict`, pure):

| case | answer |
|---|---|
| key not seen | start as usual; the ref is kept on the run (`GET /runs`, `GET /runs/{id}`: `client_ref`) and in the run record header (`observe/record.py`, row `header` and the `snapshot`) |
| same key, `attempt` lower than one seen | `{"event": "refused", "reason": "stale", "run_id": "<run of the key>"}`, closed |
| same key, another `hash` than the newest run of the key | `{"event": "refused", "reason": "conflict", "run_id": "<that run>"}`, closed |
| same key, same `hash`, that run still active | `{"event": "duplicate", "run_id": "<run>", "state": "running"}`, then the client observes that run (the usual `run_attached` event, with no `injected`: its prompt is NOT injected again; then every event of the run) |
| same key, same `hash`, that run ended | `{"event": "duplicate", "run_id": "<run>", "state": "done"}` (or `"failed"`), closed; the outcome is in `GET /runs/{id}` |
| `client_ref` not a dict with a non-empty `plan_id` and `goal_id`, or `attempt` not an integer | `{"event": "refused", "reason": "bad_ref"}`, closed |

A same-hash resend with a higher attempt is a duplicate too (a retry of a goal that already ran needs a new `goal_id` or `plan_id`).
Limits: a key older than the last 50 finished runs is forgotten (the manager's journal and `GET /runs/{id}/record` on disk are the record
then). The run is still watched only by the socket that started it: when that socket closes, the run is cancelled (the dead-man,
`api/agent.py` `_watch_client`); a duplicate observer's close cancels nothing. So "attach" helps a resend while the first socket is still
open, not a reconnect after a lost one.

### `busy: "refuse"`

While a run is active the robot answers `{"event": "refused", "reason": "busy", "run_id": "<active run>"}` and closes: nothing is
injected and nothing starts. Without the field a second client observes the active run and its prompt goes into it as an event (the
behaviour for people, `tests/test_run_manager_api.py::test_second_client_observes_and_injects`). With no active run, `busy` changes
nothing. A ref'd duplicate of the active run is answered `duplicate` (above), not `busy`.

Order of the checks: `bad_ref`, the `client_ref` verdict, `busy`, the empty prompt (`{"event": "error", "msg": "empty prompt: no run was
started"}`, unchanged), then the start (a start that lost a race to another client: `busy` again with `busy: "refuse"`, else observe).

## Option `goals_only` (an understand-only run)

First message as SPEC section 5 (`understand`), plus `planner: "sage"`:

```json
{"prompt": "put the cup on the table", "lang": "en", "planner": "sage", "options": {"goals_only": "1"}}
```

The goals are understood and grounded as in any run (questions to the person included: `clarify` events), then the run ends with

```json
{"event": "done", "status": "understood", "goals": [{"type": "on", "object": "cup", "from": "", "to": "table@living room"}],
 "run_id": "...", "msg": "", "say": "I will put the cup on the table.", "world": {...}}
```

and nothing is planned or run (no `plan` event, no skill). The last `understand` event carries the goals too. `say` is the read-back
sentence (the robot's phrases in the run's language), not "Task failed". The run manager counts it as a good end (`GET /runs`: state
`done`, outcome `understood`; `api/agent.py` `_OK_STATUS`), and the dataset filter does not count it as a failed case. With `accept_goals` and `goals` the caller's goals are grounded and handed back (no LLM call). With
`plan_only` the plan wins (`done` `planned`). Details: docs/options.md.

Read-back: the same as any run, governed by the loop's own keys. A scripted demo with no read-back and no issue question sends
`{"goals_only": "1", "confirm_task": "0", "confirm_answers": "0", "goal_readback": "0"}` (`confirm_task` defaults to
`$ROBOT_AGENT_CONFIRM_TASK`, off; `goal_readback`, the issue check, defaults on). A request the reader finds unclear is still asked
about (`clarify`): that question cannot be switched off, only capped (`clarify_rounds`).

## `WS /ws/events` (read-only)

On connect: `{"event": "events_open", "active": "<run_id>" | null}`. Then every event published on the active run and on every run that
starts later, each with its `run_id` (the events `/ws/agent` streams plus the run manager's `run_state`, `run_refused`, ...). Events
published before the connect: `GET /runs/{id}?events=true`. It never starts, injects into or cancels a run: any message from the client
is answered `{"event": "refused", "reason": "read_only"}` and changes nothing, and closing it stops nothing (robotapp rule 6: a read
never starts a run; checked by `test_ws_events_never_starts_a_run_and_refuses_a_prompt`). It subscribes through `RunManager.on_start` /
`on_finish` and `Run.subscribe`, and removes its hooks when it closes.

## Not covered

No real robot and no real LLM ran any of this (fake agent, scripted LLM). Stop / cancel are unchanged (`POST /agent/cancel`). A
fleet-level `robot_id` is not added on purpose (one robot per process).
