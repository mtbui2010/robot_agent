---
sources: [robot_agent:robot_agent/core/planning/base.py, robot_agent:robot_agent/core/planning/registry.py, robot_agent:robot_agent/core/planning/backends/, robot_agent:robot_agent/core/planning/loop.py, robot_agent:robot_agent/core/planning/assist_loop.py, robot_agent:robot_agent/core/unified_agent.py, embodiplan:embodiplan/__init__.py]
verified_at: {robot_agent: ad501cf15ece, embodiplan: a69064255c69}
---

# Planners and loops

!!! abstract "In this page"
    - A **planner** is a plug-in with two methods. It turns a task and a belief into a list of steps. It does not run anything.
    - A **loop** is the code that runs a plan: it checks it, executes each step, recovers from failures and records the run. There are two: [ClosedLoop](../start/glossary.md#closedloop) (planner key `grace`) and [AssistLoop](../start/glossary.md#assistloop) (planner key `sage`).
    - The core adds understanding, gating, recovery, recording and replay around any planner, so a new planner stays small.
    - `grace` and `llm_direct` are planner names. `sage` is **not**: it picks the AssistLoop, and `resolve_planner('sage')` raises an error.

## The idea in plain words

Think of a planner as an author and a loop as a stage manager. The author writes the script: "go to the table, find the cup, pick it up". The stage manager checks the script is playable, calls each line in turn, notices when an actor fails, asks the author for a rewrite of the rest, and keeps a record of the night.

This split is why you can swap the author without touching the stage manager. It is also why two names in the system, `grace` and `sage`, are easy to mix up: one names an author, the other names a stage manager.

## The planner: two methods

A [planner](../start/glossary.md#planner) is any object that has the two methods of the `Planner` protocol (`base.py`). Both return `(steps, metrics)`. A step is a small dict, for example `{"action": "Find", "object": "cup"}`.

| Method | Inputs | Returns |
|---|---|---|
| `generate_plan` | `task` (text), `obs` (text of what the robot sees and believes), `visible_objects` (list) | `(steps, metrics)` for the whole task |
| `replan` | `task`, `completed` (steps already done), `failed_step`, `failure_reason`, `obs`, `visible_objects` | `(steps, metrics)` for the rest of the task, spliced after `completed` |

A planner may offer more, by simply having the methods. The core checks for them and never reaches into the planner's private state.

| Optional capability | What it adds |
|---|---|
| `SubgoalRepair` (`blocks`, `join_blocks`, `repair_block`) | The gate can send a critical finding back to one sub-goal of the plan, instead of asking for a whole new plan. See [Checks before motion](checks-before-motion.md). |
| `ProgressReporting` (a `progress` attribute) | The core streams live planning progress to the dashboard. |
| `EpisodeMemory` (`record_episode`) | The planner learns from runs that ended in success. |

## Which planner keys exist

The registry (`registry.py`) builds a planner from a name. A name is one of three kinds:

| Name | Planner | Notes |
|---|---|---|
| `grace` | `GraceBackend` (`backends/pyplanner_grace.py`) | Wraps embodiplan's planner class. Offers all three optional capabilities. Needs `embodiplan` installed. |
| `llm_direct` | `LlmDirectBackend` (`backends/llm_direct.py`) | One LLM call that writes the whole plan as `skill::inputs` lines. The dashboard calls this choice `direct`. |
| `package.module:Class` or an entry-point name | Your plug-in | Any importable class that builds a `Planner`. Entry points use the group `robot_agent.planners`. |

`get_planner` returns the planner wrapped in a proxy that records every `generate_plan` and `replan` call on the current run (see [Runs and records](runs-and-records.md)).

!!! warning "`sage` is not a planner name"
    This ran offline:

    ```python
    from robot_agent.core.planning.registry import resolve_planner
    print(resolve_planner("grace"))
    print(resolve_planner("sage"))
    ```

    ```text
    (<class 'robot_agent.core.planning.backends.pyplanner_grace.GraceBackend'>, True)
    ValueError: unknown planner 'sage'; available planners: grace, llm_direct (or 'package.module:Class', or an entry point in group 'robot_agent.planners')
    ```

    `sage` is a choice of **loop**. In `unified_agent.py` the UI choice `sage` selects the AssistLoop, and `grace` and `direct` select the ClosedLoop with the planner `grace` or `llm_direct`. Any other name is tried as a planner name, so a plug-in can be selected directly. If you pass `sage` to the registry yourself, you get the error above.

## The two loops

The AssistLoop is built on the ClosedLoop (it is a subclass). Its planner is called the **generator**. The generator is the `generator` option or the environment variable `ROBOT_AGENT_SAGE_GENERATOR`, and the default is `grace`. So both loops, by default, end up calling the same planner. They differ in what they do around it.

```mermaid
flowchart TB
    UI[Planner choice on /ws/agent] -->|grace / direct / plug-in| CL[ClosedLoop]
    UI -->|sage| AL[AssistLoop]
    CL --> PL[Planner: generate_plan, replan]
    AL -->|generator, default grace| PL
    AL -. subclass of .-> CL
```

| | ClosedLoop (`grace`) | AssistLoop (`sage`) |
|---|---|---|
| Starts from | The task text, as given | The task, turned into goals by one LLM call; may refuse or ask the person to clarify |
| Plan check | Refuses only steps the robot cannot bind to a skill; one new plan if so | The full [gate](../start/glossary.md#gate): whole-plan simulation, plan tidy, repair, one fresh plan |
| Before each step | Nothing | The [guard](../start/glossary.md#guard) on the current belief |
| After each step | Layered verify, then replan on failure | Layered verify, recovery in place per action (Find, Pick, MoveTo, Place), then replan on failure |
| Replans (default `max_replans`) | 3 | 6 |
| Changes said during a run | Reports the change was not applied | Applies a change to the goals and repairs the plan |
| Done means | Every planned step ran | Every goal was also seen to hold (`goal_check`) |
| Object memory, search | No | Yes: remembers where objects were seen; does not search a place twice |

Both loops share the same event stream, the same record of the run, and the same recovery policy for failure codes. Both reconcile the belief with the robot at the start and after a pause (see [Belief and reconcile](belief-and-reconcile.md)).

## What the core adds around any planner

Whichever planner you plug in, the core provides:

1. **Understanding** (AssistLoop): the task becomes goals, or a refusal or a question.
2. **Gating**: the plan is checked before the robot acts (full gate in the AssistLoop, binding check in the ClosedLoop).
3. **Binding**: each step is mapped to a skill of the robot through the [RobotAdapter](../start/glossary.md#robotadapter).
4. **Verification**: each result is checked in layers.
5. **Recovery**: retries, waiting for the robot's health, asking the person, and replans by calling the planner's `replan`.
6. **Recording**: every event and every planner call goes into the run's record; the record can be replayed (see [Runs and records](runs-and-records.md)).

The planner stays free of all of this. It sees only the arguments in the table above.

## How `grace` and `sage` relate to embodiplan

The `grace` planner wraps a class from the separate `embodiplan` package. That package names its class `SAGEPlanner` (the code spells it "Symbolic Action-Gating and Editing"), and `GRACEPlanner` is kept as a deprecated alias of it (`embodiplan/__init__.py`). So the two codenames end in the same class. They differ in the loop around it.

For how that planner works inside (sub-goals, expansion, editing, memory), read the embodiplan site: <https://mtbui2010.github.io/embodiplan/>. For the exact interface a new planner must follow, see [Planner library contract](../contracts/planner-library.md). To write one, see [Add a planner](../guides/add-a-planner.md).

!!! code "Where in the code"
    - Planner protocol and optional capabilities: [`base.py` L246-L279](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/base.py#L246-L279) and following
    - Registry and built-in names: [`registry.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/registry.py#L29-L93)
    - Built-in planners: [`backends/`](https://github.com/mtbui2010/robot_agent/tree/ad501cf15ece/robot_agent/core/planning/backends)
    - Choosing the loop: [`unified_agent.py` L355-L382](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/unified_agent.py#L355-L382)
    - ClosedLoop: [`loop.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/loop.py#L242-L255); AssistLoop and its generator: [`assist_loop.py`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/robot_agent/core/planning/assist_loop.py#L303-L310)
    - Alias in embodiplan: [`embodiplan/__init__.py` L79-L80](https://github.com/mtbui2010/embodiplan/blob/a69064255c69/embodiplan/__init__.py#L79-L80)
    - Longer notes (dense): [`docs/planners.md`](https://github.com/mtbui2010/robot_agent/blob/ad501cf15ece/docs/planners.md)

See also: [The closed loop](closed-loop.md), [Checks before motion](checks-before-motion.md).
