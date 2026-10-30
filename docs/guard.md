# Preconditions, verification, reconcile and provenance (SAGE-v2 AssistLoop)

How the AssistLoop (planner `sage`) makes sure a step only runs when the world allows it, and that
the belief it plans on matches the robot. Robot-agnostic: the sensors, places and tolerances come
from the RobotAdapter and `manifest.yaml`. The plain `ClosedLoop` (planner `grace`) is unchanged.

Code: `core/planning/reconcile.py` (pose / containers -> belief, the `arrived` guard),
`core/planning/base.py` (`WorldState.provenance`), hooks in `assist_loop.py` (`_reconcile`,
`_guard_arrived`, `_guard`, `_step_succeeded`, `_obs`) and `robot.py` (`RobotView.reconcile`).

## Three precondition layers

1. **Gate** (before the plan runs): the checker simulates the whole plan on the belief with the
   one action model and reports rule violations (Pick without Find, Place at the wrong place, an Open
   with a full hand, goals not reached ...). Critical findings go back to the planner's repair; a plan
   still critical never runs. See `assist_loop._check` and [planners.md](planners.md).
   Before it, `core/planning/plan_tidy.py` removes the steps the action model proves do nothing (Find /
   Pick of what is in the hand, MoveTo where the robot is, Open of an open / Close of a closed container,
   a `MoveTo <room>` waypoint right before a MoveTo, every step after the goals are reached), on the
   first plan, each edit, the refinement and every replan suffix; the `gate` event lists them as
   `tidied`. `ROBOT_AGENT_PLAN_TIDY=0` (option `plan_tidy`): off. (T2: qwen3 / qwen2.5 plans with
   "Find plate, Pick plate" twice or the task repeated failed the gate after the planner did not delete
   them.)
2. **Per-step guard** (`AssistLoop._guard`, right before the robot moves): the step's preconditions on
   the belief *as it is now* (things changed since the plan). Before that, a non-sensor `arrived`
   (operator / belief) is checked against a fresh pose for a step that acts at a place (Find, Pick,
   Place, PutIn, Open, Close): when the pose puts the robot elsewhere the belief is fixed and the
   event `recover kind=reconcile` (`was`, `now`, `msg`) is sent. A believed-held object the holding
   sensor does not see is dropped the same way. A violation that remains goes to the planner's
   replan (`precondition: ...`).
3. **Skill** (on the robot): the skill checks its own preconditions and fails with a FailureCode
   (`unreachable`, `grasp_failed`, ...) handled by the recovery policy.

## After a reported success

Never the preconditions again. The effects enter the belief only when the verification layers pass
(`isdone`, `symbolic`, `vlm`, `sensor` / `localization`, the stale-pose check): see
[verify.md](verify.md). A MoveTo whose `localization` layer measured the base at its target marks
`arrived` as sensor-confirmed (no second pose read before the next step). The run is done only when
its goals are seen to hold (`goal_check`).

## Reconcile (run start, resume after a pause, a suspended task taken back)

`RobotView.reconcile(world, node, sense, places=ENV, arrive_tol=...)`:

1. the adapter's `reconcile(world, node)` (as before: e.g. its gripper check);
2. the core's holding sensor check (`sense(holding_sensor)`);
3. the sensors (`reconcile.reconcile_sensors`):
   - **arrived** from the base pose: the adapter's optional `pose(node)` -> `{x, y, yaw?, ts? | age_s?}`,
     else (or for the x / y it lacks) the manifest's pose sensor (the skill that verifies MoveTo). The
     nearest ENV place within `arrive_tol_m` (AssistLoop `arrive_tol`) is `arrived`; farther than that
     from every place, `arrived = None` (sensor: between places). A missing pose, or one older than
     `geometry.pose_max_age_s` (`sensor_health.pose_problem`; the reading's `ts` / `age_s`, else the
     adapter's `health()['pose_age_s']`), makes `arrived` **unknown** (None, provenance `unknown`): a
     believed place is never kept and never invented. The planner prompt then gets
     `Robot position: unknown (no fresh base pose): go to a place before acting there`.
   - **opened** from the adapter's optional `container_states(node)` -> `{key: True | False | None}`:
     a read state replaces the belief; containers it cannot read stay as belief and are listed as
     `unconfirmed`.

The `reconcile` event (only when the belief changed) carries `changed`, `sensors` (what was read)
and `provenance`.

## Provenance

`WorldState.provenance` records where each field's value came from; `to_dict()['provenance']` shows it
(the other keys and `as_text()`, which planners read, are unchanged):

```json
{"arrived": {"source": "sensor", "ts": 1793364683.1, "detail": "0.02 m from desk@study room"},
 "holding": {"source": "belief", "ts": null},
 "opened": {"source": "belief", "ts": 1793364683.1, "containers": {"drawer@kitchen": {"source": "belief", "ts": null, "open": true}},
            "unconfirmed": ["drawer@kitchen"]},
 "found": {"source": "belief", "ts": null}, "on": {"source": "belief", "ts": null}}
```

- `sensor`: read off the robot at `ts`; `operator`: set from the dashboard (`PUT /agent/world`, a belief
  the robot re-checks); `belief`: anything else (a skill's effect, loaded from disk); `unknown`: the
  sensor could not tell (`why`).
- An entry counts only while the field still has the value it was recorded for, so a skill's effect
  or any other change turns it back into `belief` without bookkeeping at every assignment.

## Robots

- kcare_robot (`KaairAdapter.pose`): x / y from the `mobile_pose` agent's last message, `ts` its
  receive time (`rev_time`), so `pose_max_age_s: 2.0` applies. No `container_states()`: KAAIR has no
  drawer state sensor (open / close skills only report their own pull / push), so `opened` stays a
  belief, shown unconfirmed.
- base_robot (`Adapter.pose`): the backend pose with the read time; no containers.

## Options

| option (`/ws/agent` options) | env | default |
|---|---|---|
| `reconcile` | `ROBOT_AGENT_RECONCILE` | `1` (`0`: the adapter's reconcile only, as before; no pose guard) |
| `arrive_tol` | `ROBOT_AGENT_ARRIVE_TOL` | manifest `geometry.arrive_tol_m` |

Tests: `tests/test_v2_reconcile_*.py`, `tests/test_v2_guard_*.py`. Scenarios:
`scenarios/tests/test_t1_v2_guard.py` (GR01-GR06).
