# Planners (plug-ins)

Code: `robot_agent/core/planning/base.py` (interface), `registry.py` (loading by name),
`subgoal_repair.py` (the gate's use of the optional repair), `backends/` (built-in planners).
Tests: `tests/test_v2_planner_plugin.py`; scenarios `scenarios/tests/test_t1_v2_planner_plugin.py` (PL01-PL04).

The planner turns a task into steps. SAGE / GRACE (embodiplan) is one planner among others: a new
method is a class in its own repo, selected by name, with no edit in robot_agent.

## The interface

A planner is any object with two methods (`base.Planner`, a structural `Protocol`; no base class needed):

```python
def generate_plan(self, task: str, obs: str, visible_objects: list[str]) -> tuple[list[dict], dict]: ...
def replan(self, task: str, completed: list[dict], failed_step: dict, failure_reason: str,
           obs: str, visible_objects: list[str]) -> tuple[list[dict], dict]: ...
```

Both return `(steps, meta)`. A step is a dict `{"action": "MoveTo", "object": "dining table", "reason"?: str}`
with an action of the robot's manifest (`MoveTo Find Pick Place PutIn Open Close Say ...`); object names
may be the words a person uses, the core grounds them to the robot's places. `meta` is any dict
(`method`, timings, ...); it is shown as `plan_meta` on the `plan` event. `replan` returns the suffix
that replaces the failed step and everything after it.

What the planner reads in `obs` (plain text):

| line | loop | meaning |
|---|---|---|
| `Places the robot can go to (use exactly these):` + `- <place>` lines | AssistLoop | the only valid places |
| `Goals: on apple from desk@study room to dining table@kitchen; ...` | AssistLoop | the understood goals (`on`, `in`, `open`, `close`) |
| `Robot state: arrived=X found=- holding=- opened=[A] on=[-]` | both | the belief (`WorldState.as_text()`) |
| `Already looked (object not there): A, B` | AssistLoop | places searched in vain |
| `A checker rejected the previous plan:` + `- <finding>` | AssistLoop | the gate's findings (refine) |
| the robot adapter's `observe()` text | ClosedLoop | whatever the robot reports |

Steps from a planner are never executed blindly; see "What the core gives every planner".

## Optional capabilities

A planner MAY offer more. The core checks each one with a plain attribute look-up (`base.subgoal_repair`,
`base.planner_capabilities`), never by type or by a planner's private names, and works without them.

| capability | members | what the core does with it | without it |
|---|---|---|---|
| `SubgoalRepair` | `blocks()`, `join_blocks(blocks)`, `repair_block(block, feedback, *, task, state=None, obs="")` | the certified gate sends every sub-goal block with a critical checker finding back with the finding as feedback, up to `edit_rounds` (`ROBOT_AGENT_EDIT_ROUNDS`, 3) rounds (`subgoal_repair.repair_plan`) | the gate asks for one fresh `generate_plan` with the findings in `obs` |
| `ProgressReporting` | `progress` attribute | set to a callable taking a dict; each call becomes a `plan_step` event | no live progress |
| `EpisodeMemory` | `record_episode(task, steps)` | called with the executed steps after a successful run | nothing is learnt |

`blocks()` returns the sub-goal blocks of the last `generate_plan`: objects with `name: str` and
`steps: list[dict]` (`base.PlanBlock`); the core replaces `block.steps` with what `repair_block` returns,
then takes `join_blocks(blocks)` as the plan. `state` is the action model's symbolic state at the block's
start. `repair_block` returns `[]` when nothing usable came back and raises on planner errors.

GraceBackend (`backends/pyplanner_grace.py`) implements all three on embodiplan's public SAGE API
(`last_blocks`, `flatten`, `edit_block` with the memory examples of `retrieve_examples`, `progress`,
`record_episode`); it is the only module that knows those names.

## Selecting a planner

| where | value |
|---|---|
| `ROBOT_AGENT_PLANNER` | the ClosedLoop planner (default `grace`); `sage` = AssistLoop |
| `ROBOT_AGENT_SAGE_GENERATOR` | the planner behind AssistLoop (default `grace`) |
| `/ws/agent` `planner` | `grace`, `direct`, `sage`, or any planner name below (ClosedLoop) |
| `/ws/agent` `generator` | with `planner: "sage"`: the planner behind AssistLoop |

A planner name is (`registry.resolve_planner`):

1. a built-in name: `grace` (embodiplan SAGE / GRACE), `llm_direct` (one LLM call, its prompt built from the robot: [direct_prompt.md](direct_prompt.md));
2. `package.module:Class`: any importable class or factory; nothing to register;
3. the name of an entry point in the group `robot_agent.planners`.

An unknown name fails with `unknown planner 'x'; available planners: grace, llm_direct, ...`.

Config: `grace` and `llm_direct` get their config as before. A plug-in's constructor gets the keys of this
set that it accepts (all of them when it takes `**kwargs`; `None` values are left out):
`host`, `model` (the LLM config), `live_path` (a per-machine data path for memory), `llm_cfg`,
`robot_pkg`, `capabilities` (the manifest capabilities, with the core's defaults), `actions` (the
robot's plan actions), `robot_description` (the robot's body in one sentence, from the manifest;
see [direct_prompt.md](direct_prompt.md)). A planner that plans with the direct planner's prompt
(`core/planning/direct_prompt.py`) says so with a class attribute `uses_direct_prompt = True`
(`GET /planner/prompt?planner=` then reports `uses_prompt: true` for it).

## Writing a planner in its own repo

```python
# my_planner/planner.py
class MyPlanner:
    def __init__(self, capabilities=None, actions=None, host=None, model=None):
        self.actions = set(actions or ())

    def generate_plan(self, task, obs, visible_objects):
        steps = [{"action": "MoveTo", "object": "kitchen"}]
        return steps, {"method": "my_planner"}

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        return [], {"method": "my_planner"}
```

Use it without installing anything: `ROBOT_AGENT_SAGE_GENERATOR=my_planner.planner:MyPlanner`
(the package must be on `PYTHONPATH`). Or give it a short name with an entry point in its
`pyproject.toml`, then `pip install -e .` and select `my_planner`:

```toml
[project.entry-points."robot_agent.planners"]
my_planner = "my_planner.planner:MyPlanner"
```

A full working example outside robot_agent, with no LLM: `scenarios/harness/plugin_planner.py`
(`GoalPlanner`: goals + robot state -> steps; it plans for KAAIR and the base robot).

Rules: return actions of the robot's manifest only (`actions`); do not import robot_agent internals
(the contract types in `robot_agent.contract` are fine); be deterministic given the same inputs when
you can (replay serves recorded planner calls, `observe.md`).

## What the core gives every planner

The same for SAGE and a plug-in; none of it is the planner's job:

- **Recording / replay**: `get_planner` wraps the planner in `observe.record.RecordingPlanner`; every
  `generate_plan` / `replan` is on the run's record.
- **Understanding** (AssistLoop): the person's words -> goals, clarification questions, the `Goals:` line.
- **Certified gate**: the plan is checked by the action model and the goal checker before any motion;
  critical findings go to `SubgoalRepair` or one refined plan; a plan that still fails never runs
  (`I could not make a plan that passes my checks: ...`). Steps the robot cannot bind are refused too.
- **Guard per step**: preconditions on the belief before each skill, the skill's own verify specs after.
- **Recovery**: failures are classified (`contract.FailureCode`) and handled by policy (retry, wait for
  health, search, ask the person, replan through `replan`, give up), within `max_replans`.
- **Goal check**: "success" only when the goals are observed to hold (`goal_check`).
- **Learning**: `record_episode` after a success, when the planner has it.
