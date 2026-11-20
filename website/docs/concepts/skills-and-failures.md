---
sources: [robot_agent:robot_agent/contract/types.py, robot_agent:robot_agent/contract/failures.py, robot_agent:robot_agent/contract/recovery.py, robot_agent:robot_agent/core/planning/robot.py, robot_agent:robot_agent/core/planning/loop.py, robot_agent:robot_agent/core/planning/assist_loop.py, robot_agent:robot_agent/options/fetch_guard.py, robot_agent:robot_agent/core/skill_registry.py, robot_agent:tests/fakes.py, kcare_robot:kcare_robot/configs/manifest.yaml]
verified_at: {robot_agent: e65e9aa, embodiplan: 76da520, kcare_robot: d4f5577b342e}
---

# Skills, results, failure codes

!!! abstract "In this page"
    - A skill is one function that makes the robot do one thing. It returns a [SkillResult](../start/glossary.md#skillresult): success, or failure with one of **13** [FailureCode](../start/glossary.md#failurecode) values.
    - The failure code is the same for every robot. The core decides what to do next from the code, not from the text of the error.
    - A table keyed by failure code gives the default recovery. A robot can override single entries.
    - `docs/contract.md` in the hub lists 12 codes. The code has 13: `budget_exceeded` was added. This page uses the code.

## The idea in plain words

A skill is a verb the robot knows: move, find, pick, place, open a drawer. The planner never calls a motor. It writes a plan such as "Find apple, Pick apple", and the core runs the matching skill for each step.

When a step goes wrong, the core needs to know **why** in a way that does not depend on the robot. "Gripper is empty" on one robot and "grasp failed" on another both mean the same thing: the hand closed on nothing. So every failure is mapped to one code from a short fixed list. The core then looks up what to do for that code: try again, look somewhere else, ask the person, or stop.

## What a skill is

A skill is a plain function, called as `func(node=node, **params)` ([`skill_registry.py:227`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/skill_registry.py#L227-L227)). The [manifest](../start/glossary.md#manifest) describes it with a [SkillSpec](../start/glossary.md#skillspec) (see [A robot as data](robot-as-data.md)). The registry knows three kinds ([`skill_registry.py:197-203`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/skill_registry.py#L197-L203)):

| Kind | How it runs |
|---|---|
| internal | The registry imports the module and calls the function. |
| external | The registry sends an HTTP request (the `requests` package is imported only then). |
| plan | A skill made of other skill steps. |

If a skill raises an exception, the registry catches it and returns a failure whose text is the exception message. A skill that is not registered returns the failure text `Skill "<name>" not registered`.

## SkillResult

Older skills return a dict, `{'isdone': bool, 'msg': str, ...}`. New code returns a `SkillResult`. The core turns every return value into one through `SkillResult.from_legacy`, in one place: `run_skill` ([`robot.py:306`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/planning/robot.py#L318-L334)).

| Field | Type | Meaning |
|---|---|---|
| `ok` | bool | Did the skill succeed. |
| `code` | `FailureCode` or `None` | Why it failed. Always `None` when `ok` is true. When `ok` is false and no code is given, the code is read from `msg`. |
| `msg` | str | Human-readable text. |
| `device` | str or `None` | Which device failed, when the skill knows. |
| `data` | dict | Evidence: a pose, a confidence, detections. Any key of a legacy dict that is not a status key goes here. |

Source: [`types.py:62-153`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/types.py#L62-L153). Helpers: `SkillResult.success(msg, **data)`, `SkillResult.failure(code, msg, device, **data)`, `from_legacy(raw, strict)` and `to_legacy()`. A `SkillResult` is truthy when `ok` is true.

How `from_legacy` reads other return values:

| Skill returned | Result |
|---|---|
| `{'isdone': b, 'msg': s}` | `ok=b`; on failure the code is the dict's own `code` if valid, else read from `msg` |
| `{'ok': b, 'code': c}` | `ok=b`; a `code` that is not a `FailureCode` (an integer) is kept as `data['legacy_code']` |
| a `bool` | `ok` is that value; a false value has code `unknown` |
| a `str` | success, the text is `msg` (a reply) |
| `None`, or a dict with neither flag | success by default. With `strict=True` (the planner's rule) it is a failure, code `unknown`, `msg` "skill returned nothing" |

### The text heuristics live in one place

Skills that still return only English text have no code. [`classify_legacy_failure`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/failures.py#L26-L69) reads the code off the text. It is the only place in the core that does this. It tries a list of regular expressions in order, and the first match wins, so more severe causes come first. For example, a Python `TypeError` in the text is always `software`, even when the text also says "not found". When nothing matches, the code is `unknown`.

Real calls (run offline):

```text
>>> SkillResult.from_legacy({'isdone': False, 'msg': 'gripper is empty'})
SkillResult(ok=False, code=<FailureCode.GRASP_FAILED: 'grasp_failed'>, msg='gripper is empty', device=None, data={})
>>> SkillResult.from_legacy(None, strict=True)
SkillResult(ok=False, code=<FailureCode.UNKNOWN: 'unknown'>, msg='skill returned nothing', device=None, data={})
>>> classify_legacy_failure('object out of reach')
<FailureCode.UNREACHABLE: 'unreachable'>
```

## A real failing and a real succeeding result

These come from the offline fake robot in `tests/fakes.py` (`FakeRobot`). It is a symbolic stand-in, not a real robot. I put an apple on the kitchen counter and a mug on the desk, moved to the desk, and asked it to find the apple. Then I moved to the counter and asked again. Each call goes through `run_skill`, the same function the loops use. The raw dict is what the fake skill returned.

```text
find apple (at the desk)
  raw:        {'isdone': False, 'msg': 'apple not found'}
  SkillResult(ok=False, code=<FailureCode.ABSENT: 'absent'>, msg='apple not found', device=None, data={})
  to_legacy:  {'isdone': False, 'msg': 'apple not found', 'code': 'absent'}

find apple (at the counter)
  raw:        {'isdone': True, 'ins': {'apple': {'pose_3d': [0.6, 0.05, 0.8]}}}
  SkillResult(ok=True, code=None, msg='', device=None, data={'ins': {'apple': {'pose_3d': [0.6, 0.05, 0.8]}}})
  to_legacy:  {'ins': {'apple': {'pose_3d': [0.6, 0.05, 0.8]}}, 'isdone': True, 'msg': ''}
```

The failure has code `absent` ("I looked and it is not here"). The text rule matched `not found`. The success keeps the detection as evidence in `data`, and has no code.

## The failure codes

The enum is [`FailureCode`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/types.py#L15-L28). I counted its members: **13**. The value is the lowercase string; `FailureCode.parse` also accepts upper case and the enum member.

| Value | Plain meaning | First action | Retries, then |
|---|---|---|---|
| `absent` | Looked, the object is not there. | `continue_search` | 3, then `ask_help` |
| `perception_unavailable` | Could not look (camera or detector down). Not evidence that the object is absent. | `wait_health` | 2, then `stop` |
| `unreachable` | Seen, but out of reach from here. | `reapproach` | 1, then `replan` |
| `blocked` | The path or the spot is occupied, or a drawer will not move. | `retry` | 1, then `ask_help` |
| `grasp_failed` | The gripper closed (or opened) on the wrong thing. | `retry` | 1, then `replan` |
| `dropped` | The held object was lost. | `refind` | 1, then `replan` |
| `hw_fault` | A device or server fault. | `health_check` | 1, then `stop` |
| `estop` | Emergency stop pressed. | `stop`, never retried | none |
| `timeout` | No answer in time. | `health_check` | 1, then `replan` |
| `cancelled` | The user stopped it. | `stop`, never retried | none |
| `software` | A bug in the skill. Retrying cannot help. | `stop`, never retried | none |
| `unknown` | A legacy result with no usable reason. | `verify` | 1, then `replan` |
| `budget_exceeded` | The run used up its LLM-call budget. | `stop`, never retried | none |

One code can be changed by the engine after the skill returned it. With the option `fetch_guard` (on by default, part `absent_find`), the AssistLoop asks the service `failure_code` at a step failure ([`assist_loop.py` line 3676](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/planning/assist_loop.py#L3676)). A failed `Find` whose code is `unknown` (the skill reported "not done" and no text the heuristic reads) becomes `absent`: the look did not see the object, so the search runs, and then the question "I couldn't find the X. Where is it?", instead of the `unknown` rule (`verify`, then `replan`) that could return the same plan. Other actions and other codes are not changed, and a camera that cannot look keeps `perception_unavailable`. This is not measured on a benchmark; the reason for it is a real-robot run record of 2026-11-20 with 5 failed Finds and 4 identical replans.

The code in the first column is the thing a skill or an adapter must return. A manifest lists, per skill, which of these it may return (`errors:`). A value that is not one of the 13 stops the manifest from loading. The loader's error says which values are valid; for example `'oops' is not a FailureCode (valid: absent, perception_unavailable, ...)`.

## Who decides what to do

Three parties are involved:

1. **The skill or the adapter decides the code.** The skill returns it, or the text heuristic above reads it. The adapter maps device errors onto the 13 codes.
2. **The recovery table decides the default answer.** It is `DEFAULT_RECOVERY` in [`recovery.py:66-92`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/recovery.py#L66-L92), the one table every robot shares. A robot can override single entries by giving its adapter a `recovery_overrides` attribute (`{code: rule}`); `policy_for(adapter)` merges them. The `NamemapAdapter` also takes a module-level `RECOVERY_OVERRIDES`.
3. **The loop applies it.** Planner loops differ in how much of the table they use.

### The recovery table

Each entry is a `RecoveryRule`: the first action, how many failures get that action (`retries`), what comes after (`then`; none means stop), and flags (`retry_allowed`, `mark_searched`, `report`).

| Action | Meaning |
|---|---|
| `stop` | End the run and say why. |
| `wait_health` | Wait, check `adapter.health()`, try again in place. |
| `health_check` | Check `adapter.health()` once, then go on. |
| `reapproach` | Move or turn to get closer, try again. |
| `continue_search` | Look at the next place the belief suggests. |
| `retry` | The same skill again, in place. |
| `refind` | Look for the object again (it moved or fell). |
| `verify` | Observe what actually happened first. |
| `ask_help` | Ask the person (clear the way, move it closer). |
| `replan` | Ask the planner to repair the plan from this step. |

`next_after(attempts)` gives the action: the rule's first action while `attempts` is at most `retries` (at least 1), then `then`, or `stop`. For `absent`, attempts 1, 2 and 3 give `continue_search`, and attempt 4 gives `ask_help` (run offline):

```text
>>> [RecoveryPolicy().decide("absent", n).value for n in (1, 2, 3, 4)]
['continue_search', 'continue_search', 'continue_search', 'ask_help']
```

Flags that matter:

- `retry_allowed=False` for `estop`, `cancelled`, `software` and `budget_exceeded`: the failed skill is never repeated, and the run stops.
- `mark_searched=True` only for `absent`: a failed Find counts the place as searched.
- `report=True` for `perception_unavailable`, `dropped`, `hw_fault`, `estop`, `software` and `budget_exceeded`: tell the user or the operator why.

### What each loop does with it

| Loop | What it takes from the table |
|---|---|
| ClosedLoop (planner key `sage_plain`) | Only `retry_allowed`: a code that forbids a retry stops the run; every other failure leads to a replan ([`loop.py:814-836`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/planning/loop.py#L814-L836)). |
| AssistLoop (planner key `sage`) | The whole table. It applies `stop`, `wait_health` and `health_check` itself in `_apply_policy`. The other actions go to per-action recovery code (Find, Pick, MoveTo, Open, Place, PutIn), and then to a replan. Options can act first through the hook `on_step_failed`. ([`assist_loop.py:3660-3767`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/planning/assist_loop.py#L3660-L3767)). |

Pick and MoveTo also have a ladder of their own after the policy's rung. `STEP_LADDER` in `contract/recovery.py` names the code whose rule gives the ladder (Pick: `grasp_failed`, MoveTo: `blocked`), and `step_rung` picks the rung: retry, then ask the person once, then replan (Pick) or stop (MoveTo). The code that runs these rungs is in [`step_ladder.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/planning/step_ladder.py). A failed `Find` with `absent` (or with `unknown`, which `fetch_guard` turns into `absent`, see above) uses the search described in the closed-loop page; its three retries are the default of the loop's `search_max`, after which the person is asked where the object is. Before that search starts, `look_then_ask` may already have looked where the robot stands and asked once "I don't see the X here. Where is it?" (see [Dialog and intent](dialog-and-intent.md#two-questions-the-robot-adds-before-it-plans); English phrases only, other languages not verified).

I read `_step_failed` and `_apply_policy`. I did not trace each per-action recovery method, so this page does not claim that every action in the table is carried out the same way in every case. For the two loops themselves, see [Planners](planners.md) and [The closed loop](closed-loop.md).

## VerifySpec: how a step is checked

A skill that returns `ok` may still not have changed the world. The manifest lists post-checks in `verify:`. Each is a [`VerifySpec`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/types.py#L155-L186) with a `kind`:

| `kind` | What it checks | Fields |
|---|---|---|
| `symbolic` | The declared `effects` hold in the world model. Always cheap. | none |
| `sensor` | Another skill of the robot reads the outcome. | `skill`, optional `expect` |
| `vlm` | A yes/no question about a camera frame. | `prompt`, optional `expect` |

A `sensor` whose result has a `pose` checks the base against the target place, within `arrive_tol_m`. Any other sensor passes when its `isdone` equals `expect`. `expect` defaults to true ("the hand holds it"); a release step declares `expect: false`.

Real example: the kcare `pick` skill has `verify: [{kind: symbolic}, {kind: sensor, skill: grasp_succeed}]` ([`manifest.yaml:114-131`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/kcare_robot/configs/manifest.yaml#L114-L131)). The skill can report success, and the sensor skill still decides whether the hand really holds something. If a check fails, its reason text goes through the same `classify_legacy_failure`, so a failed check also ends up as a failure code ([`failure_code`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/planning/robot.py#L275-L281)). In the kcare manifest the `vlm` checks exist but are off by default (`vlm_verify: false`).

!!! warning "A skill that returns nothing"
    In the planner, a skill that returns `None` or a dict with no `isdone` or `ok` is a failure with code `unknown` (`strict=True`). In the direct path (`unified_agent`) the same return counts as success. Always return a dict with `isdone` or a `SkillResult`.

!!! warning "Text matching is a fallback"
    A skill whose message happens to contain a phrase such as `not found` is classified `absent`, even if the real cause is something else. Return a code yourself, with `SkillResult.failure(code, msg)`, so the heuristic is not needed.

!!! code "Where in the code"
    - Types: [`contract/types.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/types.py)
    - Text to code: [`contract/failures.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/failures.py)
    - Recovery table: [`contract/recovery.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/contract/recovery.py)
    - Run one skill: [`core/planning/robot.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/planning/robot.py#L318-L334)
    - Registry: [`core/skill_registry.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/core/skill_registry.py)
    - Option that changes one code: [`options/fetch_guard.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/robot_agent/options/fetch_guard.py#L80-L85) (`failure_code`)
    - Offline fake: [`tests/fakes.py`](https://github.com/mtbui2010/robot_agent/blob/e65e9aa/tests/fakes.py)

Next: [The belief](belief-and-reconcile.md). To write a skill, see [Add a skill](../guides/add-a-skill.md).
