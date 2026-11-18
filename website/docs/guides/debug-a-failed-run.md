---
sources: [robot_agent:robot_agent/observe/record.py, robot_agent:robot_agent/observe/replay.py, robot_agent:robot_agent/api/agent.py, robot_agent:robot_agent/api/diagnostics.py, robot_agent:robot_agent/diagnose.py, robot_agent:robot_agent/contract/failures.py, robot_agent:robot_agent/contract/recovery.py, robot_agent:robot_agent/core/planning/assist_loop.py, robot_agent:robot_agent/core/planning/verify.py, robot_agent:robot_agent/core/skill_registry.py, robot_agent:tests/fakes.py]
verified_at: {robot_agent: ad501cf15ece}
---

# Debug a failed run

A run failed and you want to know why. This guide shows how to find out which stage failed, what the failure code says, what the robot believed at that moment, and how to replay the run on a laptop. Each step has a command and the output of a real run.

!!! abstract "In this page"
    - Every run leaves a [run record](../start/glossary.md#runrecord) and a metrics line. Start with the `done` event: it carries the message, the [failure code](../start/glossary.md#failurecode), the device and the evidence.
    - The event stream tells you the **stage** that failed: `understand`, `gate`, the per-step [guard](../start/glossary.md#guard), the skill, or the [verify layers](../start/glossary.md#verify-layers).
    - The belief at the end of the run says what the robot assumed, and which values a sensor vouched for.
    - Three runs were made for this page on the offline fake robot, with a scripted LLM answer. They are fake. Nothing here comes from a real robot, and no recorded real run was opened.

!!! code "Where in the code"
    - The record and the metrics line: [`observe/record.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/observe/record.py#L254-L345) (`load_record`, `run_metrics`, `metrics_of`); replay CLI: [`observe/replay.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/observe/replay.py#L495-L522)
    - HTTP endpoints for runs: [`api/agent.py` L251-L287](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/api/agent.py#L251-L287); diagnostics: [`api/diagnostics.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/api/diagnostics.py#L26-L52), [`diagnose.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/diagnose.py)
    - The gate's end state: [`assist_loop.py` L1262-L1266](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/assist_loop.py#L1262-L1266); the per-step guard: [`_guard` L1801-L1832](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/assist_loop.py#L1801-L1833); pick recovery: [`_recover_pick` L2057-L2095](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/assist_loop.py#L2057-L2095)
    - Verify layers and their verdict: [`verify.py` L236-L247](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/verify.py#L236-L247)
    - Failure codes and the recovery table: [`contract/failures.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/contract/failures.py), [`contract/recovery.py` L66-L91](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/contract/recovery.py#L66-L91)
    - Tracebacks from a skill: [`skill_registry.py` L231-L245](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/skill_registry.py#L231-L245)
    - The fake robot with scripted faults: [`tests/fakes.py` L91](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/tests/fakes.py#L91)

## The runs used in this guide

Both ideas come from [Runs and records](../concepts/runs-and-records.md): a run has an id, and the record is a JSONL file with one row per line. To have something to debug, three runs were recorded on the fake robot `FakeRobot` through the run manager (the same set-up as the one in [Runs and records](../concepts/runs-and-records.md#one-real-record)). The task is always "Bring the apple from the desk to the dining table." The planner is the small one from [Add a planner](add-a-planner.md). What differs is the fault that was scripted:

| Run | Scripted fault | What it will show |
|---|---|---|
| A | Every `pick` of the apple returns `isdone: false`, "could not grasp the apple: gripper is empty" | A failure in the **skill** |
| B | Every `pick` of the apple reports success, but the gripper stays empty (fault kind `slip`) | A failure in **verify** |
| C | No fault. The planner returns a plan with no `Find` and no `MoveTo` | A failure in the **gate** |

The fault is set with the `faults` argument of `FakeRobot`, for run A:

```python
robot = FakeRobot(env, {"apple": "desk@study room", "mug": "counter@kitchen"},
                  faults=[{"skill": "pick", "obj": "apple", "nth": "all", "kind": "fail",
                           "msg": "could not grasp the apple: gripper is empty"}])
```

Run B uses `"kind": "slip"` instead. Run C uses `faults=[]` and a planner whose `generate_plan` returns only `Pick apple` and `Place dining table@kitchen`. The recording script is the one in [Runs and records](../concepts/runs-and-records.md#one-real-record), with the planner chosen by name through the `generator` option. The ids of the three runs were `20261118-170257-6c69d5` (A), `20261118-170415-cbfa3e` (B) and `20261118-170320-ad1364` (C). All three ended with status `failed`.

## Step 1. Find the run and its metrics line

A live robot gives you three read-only HTTP endpoints (read in `api/agent.py`; **not run**, because no server can be started here):

| Endpoint | Returns |
|---|---|
| `GET /runs/{id}` | The run: state, outcome and its last events |
| `GET /runs/{id}/record` | The record rows. `?kinds=event,skill` filters, `?limit=N` keeps the last N rows. Works for finished runs, from disk. |
| `GET /runs/{id}/metrics` | The metrics line. Marked `live: true` while the run is active. |

Without a server, read the files. The record is `<data_dir>/run_records/<host>/<run_id>/record.jsonl` and the metrics lines are in `<data_dir>/run_records/<host>/metrics.jsonl`, where `<data_dir>` is the value of `ROBOT_AGENT_DATA_DIR` ([where the files go](../concepts/runs-and-records.md#where-the-file-goes)). This is the real metrics line of run A:

```json
{"run_id": "20261118-170257-6c69d5", "task_run_id": null, "robot_id": "default", "user_id": "alice", "goal": "Bring the apple from the desk to the dining table.", "loop": "assist", "started": 1794988977.2997837, "duration_s": 0.107, "outcome": "failed", "status": "failed", "llm_calls": 1, "llm_errors": 0, "llm_latency_ms": 0.0, "planner_calls": 1, "skill_calls": 10, "replans": 0, "failure_codes": {"grasp_failed": 2}, "failure_code": "grasp_failed", "failure_device": "pick", "success": false, "verified_success": false}
```

Read it like this:

- `outcome: failed` and `success: false`: the run did not reach its goal.
- `failure_code: grasp_failed` and `failure_device: pick`: the deciding failure, and the skill that raised it.
- `failure_codes: {"grasp_failed": 2}`: two plan steps failed with this code.
- `replans: 0`, `planner_calls: 1`: the planner was asked once. The loop did not ask it to replan.

The metrics line is a summary. It cannot tell you the stage or the belief. For that, read the events.

## Step 2. Read the event stream

Every event of the run is an `event` row in the record. This short script prints one line per event and leaves out the bulky fields (the belief, the clocks). Save it outside the repos:

```python
"""Print one line per event of a run record. Usage: python3 -B trace.py <record.jsonl>"""
import json, sys
for line in open(sys.argv[1]):
    row = json.loads(line)
    if row["kind"] != "event":
        continue
    e = row["event"]
    if e["event"] in ("run_state", "status", "task_start", "reconcile"):
        continue
    keep = {k: e[k] for k in ("event", "index", "at_index", "action", "object", "kind", "status", "code", "reason", "msg", "problems", "question") if k in e}
    if e["event"] == "step_verify":
        keep["layers"] = [(v["layer"], v["ok"]) for v in e["verifies"]]
    print(json.dumps(keep))
```

Run A, a failure in the skill:

```text
{"event": "understand", "status": "clear", "reason": "", "question": null}
{"event": "gate", "problems": []}
{"event": "plan"}
{"event": "step_start", "index": 0, "action": "MoveTo", "object": "desk@study room"}
{"event": "step_verify", "index": 0, "layers": [["isdone", true], ["symbolic", true], ["localization", true]]}
{"event": "step_done", "index": 0, "status": "success"}
{"event": "step_start", "index": 1, "action": "Find", "object": "apple"}
{"event": "step_verify", "index": 1, "layers": [["isdone", true], ["symbolic", true], ["sensor", true]]}
{"event": "step_done", "index": 1, "status": "success"}
{"event": "step_start", "index": 2, "action": "Pick", "object": "apple"}
{"event": "step_verify", "index": 2, "layers": [["isdone", false]]}
{"event": "step_done", "index": 2, "status": "failed", "reason": "could not grasp the apple: gripper is empty"}
{"event": "recover", "at_index": 2, "kind": "retry", "msg": "pick apple failed; looking again"}
{"event": "step_start", "index": 2, "action": "Find", "object": "apple"}
...
{"event": "step_start", "index": 3, "action": "Pick", "object": "apple"}
{"event": "step_verify", "index": 3, "layers": [["isdone", false]]}
{"event": "step_done", "index": 3, "status": "failed", "reason": "could not grasp the apple: gripper is empty"}
{"event": "clarify", "question": "I can't pick up the apple. Could you move it closer to the edge, then say yes?"}
{"event": "done", "status": "failed", "code": "grasp_failed", "msg": "could not pick up the apple: could not grasp the apple: gripper is empty"}
```

The `...` stands for the repeated `Find` that succeeded (three lines). Read the stream from the top and ask one question at each event: did this stage pass?

1. `understand` says `clear`: the task was understood.
2. `gate` has an empty `problems` list: the plan was certified.
3. Steps 0 and 1 end with `success`, and all their verify layers pass.
4. Step 2 (`Pick`) has one verify layer, `isdone`, and it is **false**. The skill itself said it failed. That is where the run went wrong.
5. `recover` shows the loop retried (look again, grasp again). The retry failed in the same way.
6. `clarify` shows the loop asked the person for help. The fake person gave no answer, so the run ended: `done` with `status: failed`, the code and the reason.

### Which stage failed

The event that fails first tells you the stage. Run A is the `skill` row of this table; B and C are shown below.

| Stage | What you see in the events | In the end state |
|---|---|---|
| Understand | `understand` with a status other than `clear`, or a `clarify` question | `done` with status `refused` for a refusal. **Not run** here; read in `assist_loop.py`. |
| Gate | One to three `gate` events whose `problems` are not empty, then `plan` with `certified: false` | `done` with "I could not make a plan that passes my checks". No step runs. |
| Guard | A `recover` with `kind` `reconcile` or `dropped`, or a `replan` whose reason starts with `precondition:`, right before a `step_start` | See [Checks before motion](../concepts/checks-before-motion.md). **Not run** here; read in `_guard`. |
| Skill | `step_verify` where the first layer, `isdone`, is false; `step_done` with `status: failed` and a `code` in its `result` | A recovery (`recover`, `search`, `replan`, `clarify`), then `done` if recovery runs out |
| Verify | `step_verify` where `isdone` is true and a later layer (`symbolic`, `sensor`, `localization`) is false | The same recovery as for a skill |

The verify layers are the checks after a skill says it worked. See [Checks before motion](../concepts/checks-before-motion.md) and the [glossary](../start/glossary.md#verify-layers). `verdict` in `verify.py` says a step passes only when every layer passed, and its reason is the first failing detail.

### Run B: the skill said yes, a check said no

Run B had the same fault in the world but a different symptom. The part of its events that differs:

```text
{"event": "step_start", "index": 2, "action": "Pick", "object": "apple"}
{"event": "step_verify", "index": 2, "layers": [["isdone", true], ["symbolic", true], ["sensor", false]]}
{"event": "step_done", "index": 2, "status": "failed", "reason": "pick reported success but the gripper is empty"}
...
{"event": "done", "status": "failed", "code": "grasp_failed", "msg": "could not pick up the apple: pick reported success but the gripper is empty"}
```

Here `isdone` is **true**. The skill claimed success. The `sensor` layer disagreed: the gripper sensor saw an empty hand. The step failed anyway, with the first failing detail as the reason. This is why a step counts as done only after observation, not after the skill's word.

### Run C: the plan never ran

```text
{"event": "understand", "status": "clear", "reason": "", "question": null}
{"event": "gate", "problems": ["step 1 'Pick apple': needs 'Find apple' just before it, at the same place", "step 2 'Place dining table@kitchen': the robot must MoveTo dining table@kitchen first"]}
{"event": "gate", "problems": ["step 1 'Pick apple': needs 'Find apple' just before it, at the same place", "step 2 'Place dining table@kitchen': the robot must MoveTo dining table@kitchen first"]}
{"event": "gate", "problems": ["step 1 'Pick apple': needs 'Find apple' just before it, at the same place", "step 2 'Place dining table@kitchen': the robot must MoveTo dining table@kitchen first"]}
{"event": "plan"}
{"event": "done", "status": "failed", "code": "unknown", "msg": "I could not make a plan that passes my checks: step 1 'Pick apple': needs 'Find apple' just before it, at the same place; step 2 'Place dining table@kitchen': the robot must MoveTo dining table@kitchen first"}
```

There is no `step_start`: no skill was called and the fake robot's trace was empty. The `gate` events are the evidence, and each lists what is wrong with the plan. The `msg` of `done` repeats the first three. The three `gate` events are the three rounds of the gate: check, repair, one fresh plan. To fix this you change the planner, not the robot.

## Step 3. Read the failure code

The `done` event of a failed run carries `code`, `device` and `evidence`. The record has the same code in the `result` of the failed `skill` row and in the failed `step_done` event. For run A:

```text
code: grasp_failed | device: pick | evidence: {'step': 'Pick apple', 'reason': 'could not grasp the apple: gripper is empty'}
```

A [failure code](../start/glossary.md#failurecode) says **why**, in a form that is the same for every robot. The core uses the code, not the text, to choose the recovery. The default rules for the codes you meet most often (from `contract/recovery.py`; the full table of 13 codes is in [Skills and failures](../concepts/skills-and-failures.md)):

| Code | Default recovery | Meaning |
|---|---|---|
| `absent` | Search on from the belief, up to 3 times, then ask the person | Looked, the object is not there |
| `grasp_failed` | Retry once, then replan | The gripper closed on nothing, or the wrong thing |
| `unreachable` | Move closer once, then replan | Seen, but out of reach |
| `blocked` | Retry once, then ask the person | The path or the spot is occupied |
| `perception_unavailable` | Wait for the robot's health twice, then stop | Could not look; not proof the object is absent |
| `software` | Stop at once | A bug in the skill. Retrying cannot help |
| `unknown` | Observe, then replan | No usable reason in the result |

!!! note "The table is the default, the loop can do more"
    Run A got `grasp_failed`, and the table says "retry once, then replan". The AssistLoop handles a `Pick` itself first (`_recover_pick`): it looks and grasps again in place, then asks the person to move the object closer, and ends the run with "could not pick up the apple" if the person does not help. So the events you see (`recover`, `clarify`, `done`) are the AssistLoop's own rule for `Pick`, not the generic table. The same is true for `Find` and `MoveTo`. Read the events, not only the table.

The code comes from the skill's result. A skill that still returns only `{'isdone': False, 'msg': '...'}` gets its code read off the text by `classify_legacy_failure`, in one place. A message that matches nothing becomes `unknown`. If you see `unknown` and the message is clear to a person, the text did not match any rule: the fix is in the skill (return a code) or in that function, not in the loop. A message that mentions a Python error, such as `TypeError` or `has no attribute`, becomes `software`, and the run stops.

!!! warning "Two cases where the metrics line is thin"
    - **A gate failure has no useful code.** Run C ended with `failure_code: unknown` and `failure_codes: {}` (its metrics line, from the file: `"failure_codes": {}, "failure_code": "unknown", "failure_device": null`). The code is `unknown` because no skill failed. Read `done.msg` and the `gate` events.
    - **A verify failure may leave `failure_codes` empty.** Run B's skill reported success, so no step *result* carried a code. Its metrics line has `"failure_codes": {}` and `"failure_code": "grasp_failed"`. The deciding code is `failure_code`; `failure_codes` counts only codes that came from step results.

## Step 4. Read the belief at the failure

The belief is what the robot assumed. The `done` event, each `step_done` event and each `skill` row carry a copy (`world`, and `world_before` on the skill row), with a provenance for each value: `sensor` (read off the robot), `operator` (a person edited it) or `belief` (an effect of a skill, or loaded from disk). The meaning is in [Belief and reconcile](../concepts/belief-and-reconcile.md).

This script prints the belief of the last `done` event and the failure fields:

```python
"""Print the belief at the end of a run and where each value came from. Usage: python3 -B belief.py <record.jsonl>"""
import json, sys
done = [json.loads(l)["event"] for l in open(sys.argv[1]) if json.loads(l)["kind"] == "event" and json.loads(l)["event"]["event"] == "done"][-1]
w = done["world"]
for k in ("arrived", "found", "holding"):
    print(f"{k:8} = {w[k]!r:18} source: {w['provenance'][k]['source']}")
print("code:", done.get("code"), "| device:", done.get("device"), "| evidence:", done.get("evidence"))
```

Real output for run A (run B gives the same belief lines):

```text
arrived  = 'desk@study room'  source: sensor
found    = 'apple'            source: belief
holding  = None               source: belief
code: grasp_failed | device: pick | evidence: {'step': 'Pick apple', 'reason': 'could not grasp the apple: gripper is empty'}
```

What it tells you:

- The robot was at the desk, and a sensor (the pose check after `MoveTo`) vouched for that. The failure is **not** a navigation problem.
- It believed the apple was found, but only as a belief: the `Find` skill said so, and no sensor confirmed it. In run A the fake `Find` succeeded each time, so the apple was in view; the failure is in the grasp.
- It believed its hand was empty (`holding = None`), which matches the failed grasp.

Compare this with a belief that is wrong. If `holding` shows an object but the run failed with `dropped` or a "not in the gripper" reason, the belief and the robot disagreed, and the guard or reconcile should have fixed it. See [Belief and reconcile](../concepts/belief-and-reconcile.md).

## Step 5. Replay the run offline

[Replay](../start/glossary.md#replay) runs the recorded task again with no robot. It answers every LLM call and every skill call from the record. Use it to test a fix and to be sure the record is what you think it is. There are two modes (`plan` re-runs understanding, the planner and the checks; `exec --fake` re-runs the whole run against a fake robot). The exit codes are 0 for identical, 1 for differs, 2 for a mismatch or a record that cannot be replayed.

```bash
cd <repos>/robot_agent
PYTHONPATH=<repos>/robot_agent/tests:<repos>/embodiplan PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m robot_agent.observe.replay plan <data_dir>/run_records/<host>/<run_id>
PYTHONPATH=<repos>/robot_agent/tests:<repos>/embodiplan PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m robot_agent.observe.replay exec <data_dir>/run_records/<host>/<run_id> --fake
```

Real output for run C (the gate failure), both commands, exit status 0 each:

```text
replay plan 20261118-170320-ad1364 (assist): IDENTICAL
  recorded plan: pick::apple
place::dining table@kitchen
  replayed plan: pick::apple
place::dining table@kitchen
replay exec 20261118-170320-ad1364 (assist): IDENTICAL
```

The replay reproduced the bad plan and the same failure. That tells you the cause is in the plan, and that the record is faithful.

For run A, `plan` printed `IDENTICAL` (exit 0), but `exec --fake` printed this and exited with 1:

```text
replay exec 20261118-170257-6c69d5 (assist): DIFFERS
  diff: event #5 (plan vs plan):
--- recorded
+++ replayed
@@ -40,2 +40,6 @@
    "object": "desk@study room"
+  },
+  {
+   "action": "Find",
+   "object": "apple"
   },
```

!!! warning "A retried pick makes `exec --fake` report DIFFERS"
    The same `DIFFERS` came from run B, where the pick was also retried. The run was **not** different: the skill calls and the outcome are the same. The difference is one extra `Find` in the `plan` event. In the code, the `plan` event is sent with the loop's own list of steps (`steps=r.steps`), and the pick recovery then inserts a `Find` into that same list (`r.steps[r.i:r.i] = [...]`). The record writes the event when it is sent. Replay keeps the event objects and compares them after the run, so the replayed `plan` event shows the later list. (This is a reading of the code that fits the output; it was not tested by a change.) When `exec --fake` reports one `plan` difference that only adds a retry `Find`, treat the run as replayed. Use `plan` mode to compare the plan, and look at `--json` (`"identical": false`, one entry in `diffs`) to confirm that there is no other difference.

Replay needs the robot package of the run and `embodiplan` importable, as described in [Runs and records](../concepts/runs-and-records.md#replay). Without them you can get a `DIFFERS` that is only an environment problem, or an exit status of 2. Use it from a command line or a test, not inside a live agent process.

## Step 6. Check the robot and the skill

The record says what the loop saw. If the cause is in the robot, check the robot.

- **`GET /diagnostics`** is a snapshot of the environment, the skills and the devices (Python version, robot package, ROS status, selected environment variables, the state of each skill and device, the count of boot errors and the log file path). **`GET /diagnostics/boot`** lists the errors captured at start-up, with full tracebacks. Both are read in `api/diagnostics.py` and were **not run** (no server).
- **`python -m robot_agent.diagnose <robot_pkg> [<data_dir>]`** is a check to run before a robot agent starts: the Python version, ROS imports, the skills config and every skill import, and `connections.json`. It exits 0 when all checks pass and 1 otherwise. It was **not run**, because it needs ROS.
- **`ROBOT_AGENT_DEBUG_RESPONSE=1`** (also `true`, `yes`, `on`). When an internal skill raises an exception, the result is `{'isdone': False, 'msg': <the exception text>}`. With this variable set, the result also carries `skill`, `module_path`, `func_name` and the full `traceback`. Set it on the robot agent's process and run the task again. It does not change anything else. This was read in `skill_registry.py` and not run.

!!! note "Where each kind of cause usually lives"
    - **Gate or understand failed**: the planner, the task text, or the manifest's actions and places. Fix and replay with `plan` mode.
    - **Skill failed**: the skill, or the world (the object really is not there). The code and the message say which.
    - **Verify failed with the skill saying success**: the skill reports success too early, or a sensor or its limits in the manifest are wrong.
    - **`software`**: a bug in the skill code. The traceback (above) points at the line.

## A short checklist

1. Open the metrics line. Note `outcome`, `failure_code`, `failure_device`, `replans`.
2. Print the events. Find the first event that failed: `gate` problems, `step_verify` with a false layer, `recover`, `done`.
3. Name the stage with the table in Step 2.
4. Read the `done` event: `code`, `device`, `evidence`, `msg`.
5. Read the belief and its sources.
6. Replay with `plan` and `exec --fake`. Expect exit 0, except the retry case above.
7. If the cause is in the robot, run the diagnostics and turn on the debug response.

## Where to go next

- [Runs and records](../concepts/runs-and-records.md): the full list of record rows, the metrics fields and the replay options.
- [Checks before motion](../concepts/checks-before-motion.md): the gate, the guard and the skill's own check.
- [Skills, results, failure codes](../concepts/skills-and-failures.md): the 13 codes and the recovery table.
- [The belief and reconcile](../concepts/belief-and-reconcile.md): why a belief can be wrong and how it is repaired.
- [Add a planner](add-a-planner.md): fix a plan that fails the gate.
