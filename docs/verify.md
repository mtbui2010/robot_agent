# Step verification, sensor health and the LLM-call budget

What the planning core checks after a skill says it succeeded, what it checks before it trusts a
look or an arrival, and how many LLM calls one run may make. Robot-agnostic: every threshold,
prompt and sensor comes from the robot's adapter / `manifest.yaml`.

Code: `core/planning/verify.py` (layers), `core/planning/verify_vlm.py` (VLM layer),
`core/planning/sensor_health.py` (health), `core/planning/budget.py` (budget, used by
`core/planning/subgoal_repair.py`). The hooks in
`assist_loop.py` / `loop.py` are a few lines each.

## Layers after a step

`StepVerifier.verify` runs, in order, and stops at the first failure:

1. `isdone`: the skill's own result.
2. `symbolic`: the step's declared `effects` hold after the action model applies them
   (preconditions are never checked again here).
3. `vlm`: the manifest's `{kind: vlm, prompt: ...}` specs of the skill (below). The legacy hook
   (`ROBOT_AGENT_VERIFY_VLM=1`, the adapter's `vlm_actions` / `vlm_hook`) only runs for a step that
   has no manifest vlm spec.
4. `sensor` / `localization`: the skill's `{kind: sensor, skill: ...}` specs (a pose sensor is
   measured against the ENV loc with `geometry.arrive_tol_m`). None declared: an UNKNOWN layer.

AssistLoop adds the stale-pose check after a MoveTo (below). The step's effects enter the belief
only when every layer passed.

## The VLM layer (`verify_vlm.VlmCheck`)

```yaml
- name: place
  action: Place
  verify: [{kind: symbolic}, {kind: vlm, prompt: "Is the {object} on the {place}?"}]
```

`{object}` is the object the Place let go of (for other actions the step's object), `{place}`
the step's place (`table@kitchen` reads "table in the kitchen"), `{action}` the action.

The core asks the adapter: `adapter.vlm_ask(prompt, step=step, node=node)`. It may return a
bool, `None`, `'yes'` / `'no'` / anything else (unsure), a dict `{answer | ok, confidence, note}`
or the legacy `(ok, confidence, note, ms)` tuple.

| answer | layer | then |
|---|---|---|
| yes | `vlm` ok | the step succeeded |
| no | `vlm` failed | not a success: the effects stay out of the belief, a release that emptied the hand leaves the object's place unknown, and SAGE replans with "the check after Place ... says it did not happen" (the per-action in-place recovery is skipped) |
| unsure, confidence below `geometry.vlm_min_conf`, an exception, no `vlm_ask` | `vlm_observe` | the observation path: for Place / PutIn the robot's Find looks for the object where it was put (seen: ok, not seen: failed like a "no"); nothing to look for (Open / Close / Pick) or Find cannot run: an UNKNOWN layer, never a crash and never a checked pass |

A skill without vlm specs never calls the VLM.

On / off, per run:

- the run option `vlm_verify` (`/ws/agent` options, loop overrides), else
- `ROBOT_AGENT_VLM_VERIFY=1|0`, else
- the manifest's `capabilities.vlm_verify` (default **on** when the manifest declares vlm specs).

KAAIR (`kcare_robot` manifest) declares vlm specs for Place / Open / Close but sets
`capabilities.vlm_verify: false`, because the VLM latency on the real robot is not measured yet:
its behaviour is unchanged until `ROBOT_AGENT_VLM_VERIFY=1`. Its `vlm_ask` sends the head-camera
frame to the VLM connection the qa skill uses (answers yes / no / unsure).

## Sensor health (`sensor_health`)

`RobotAdapter.health(node)` reports what the robot knows about its sensors:

| key | meaning |
|---|---|
| `frame_age_s` | seconds since the last camera frame |
| `fps` | camera rate |
| `depth` | a depth image is there (bool) |
| `pose_age_s` | seconds since the last base pose |
| `ok` | the adapter's summary (`None` = not known) |

Any key may be missing or `None` (not known = not stale). The limits are the robot's, in its
manifest `geometry`: `frame_max_age_s`, `min_fps`, `need_depth`, `pose_max_age_s`. A robot that
sets none is not checked (health is not even read).

- **Before a Find**: a stale camera is a failed Find with FailureCode `perception_unavailable`;
  the skill does not run. The recovery policy for that code waits for `health()` to read ok
  (`health_wait_s`), then runs the Find again, and stops after its retries; the place is not
  counted as searched and nobody is asked where the object is.
- **After a MoveTo** that passed its layers: a stale pose fails the `localization` layer
  ("no fresh pose ...: arrival not confirmed"), so the arrival is not counted; the same policy
  (wait, MoveTo again, stop) applies.

`ROBOT_AGENT_SENSOR_HEALTH=0` (or the run option `sensor_health: false`) turns both checks off.

KAAIR's `health()` reads the ROS agents: `head_rgb` (age, fps), `head_depth` (present),
`mobile_pose` (age), from the receive time `TopicAgent` keeps (`rev_time`, `rev_period`). An agent
that is not a subscription has no receive time and is reported unknown. Its manifest sets
`frame_max_age_s: 2.0` and `pose_max_age_s: 2.0`.

## LLM-call budget (`budget`)

One unit of `understand.LLMBudget` per core LLM call of a run: understand (and its repair /
re-sample calls), the planner's `generate_plan` and each `replan`, each sub-goal edit of the plan
gate, the gate's one refinement, and the search's per-object place ranking (`search_hooks`, the
same `r.llm_budget`; once the budget is used the ranking is skipped and the prior alone orders the
places, the next planner call then ends the run). The cap is `llm_budget` (run option) /
`ROBOT_AGENT_LLM_BUDGET`, default **20** (was 40); 0 = no cap.

Past the cap the run ends `failed` with code `budget_exceeded` (`FailureCode.BUDGET_EXCEEDED`,
recovery STOP, never retried), `device: llm`, `evidence: {llm_calls, limit}` and the robot's
phrase `llm_budget` (i18n; the core's English text otherwise). A phrase the robot has in the
user's language is not translated again.

The gate's sub-goal repair (`subgoal_repair.repair_plan`, the planner's optional `SubgoalRepair`;
`edit_rounds`, default 3) takes one unit per `repair_block` call and stops as soon as a round leaves as many
critical findings as the round before (`budget.EditProgress`): the run's `gate` event lists
"edits stopped: N finding(s), not fewer than the round before".
A block after the point where the earlier blocks already reach the goals is emptied by the core (no
call: the planner could only answer `{"steps": []}`), and a round starts only when its calls leave
`edit_reserve` calls (`ROBOT_AGENT_EDIT_RESERVE`, default 6, at most a third of the cap) for the rest of
the run; else "edits stopped: N sub-goal edit(s) would leave less than R of the L LLM calls left" and the
gate goes on with the plan as edited (its one fresh plan, then certify). Edits made before the budget ran
out are kept; with no call left at all the first edit raises `LLMBudgetExceeded` as before. (T2 H01: a
61-step plan of 22 one-word SAGE sub-goals spent the whole cap on edits before the robot moved.)

The scenario suite records the calls: every result row carries `llm_calls` (the test's fake-LLM
and fake-planner calls, `scenarios/harness/fakes.py` `LLM_CALLS`).
