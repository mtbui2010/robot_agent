---
sources: [robot_agent:robot_agent/core/planning/base.py, robot_agent:robot_agent/core/planning/registry.py, robot_agent:robot_agent/core/planning/backends/pyplanner_sage.py, robot_agent:robot_agent/core/planning/backends/examples.py, robot_agent:robot_agent/core/planning/action_model.py, robot_agent:robot_agent/core/planning/verify.py, robot_agent:robot_agent/core/planning/assist_loop.py, robot_agent:robot_agent/core/planning/loop.py, robot_agent:robot_agent/core/unified_agent.py, robot_agent:pyproject.toml, robot_agent:CLAUDE.md, robot_agent:tests/test_action_model_agreement.py, robot_agent:tests/test_v2_planner_plugin.py, embodiplan:embodiplan/__init__.py, embodiplan:embodiplan/action_model.py, embodiplan:embodiplan/sage.py, embodiplan:pyproject.toml, embodiplan:CLAUDE.md, kcare_robot:pyproject.toml, symbolic_robot:bench/suites/home/planners/robot_agent_adapter.py]
verified_at: {robot_agent: 5058637, embodiplan: 76da520, kcare_robot: d4f5577b342e, symbolic_robot: f55ace24c3b4}
---

# Planner library and core

This page describes the agreement between the hub (`robot_agent`) and the planner library [embodiplan](../start/glossary.md#embodiplan). You get the two methods a planner must have, the one set of action rules that the hub shares with the library, the version pin, and the few places in the hub that import the library. For how the planners themselves work, read the [embodiplan site](https://mtbui2010.github.io/embodiplan/). This page does not repeat it.

!!! abstract "In this page"
    - A planner is any object with two methods, `generate_plan` and `replan`. Both return `(steps, metrics)`.
    - The rules that say what each action needs and changes (the [action model](../start/glossary.md#action-model)) live in embodiplan only. The hub's plan gate, step guard and post-step check all call that one module.
    - The hub pins embodiplan to `>=0.2.1,<0.3`. The hub imports embodiplan in only three files.
    - Optional keywords and capabilities go only to a planner that declares them: `subgoals=` and `budget=` (embodiplan 0.2.6 and later), fixed sub-goals (0.2.7), and a model of its own for some calls (`chat_override`).
    - `sage` names two things: in the registry the SAGE planner (`resolve_planner('sage')` returns `SageBackend`), and in a request the AssistLoop around it. `sage_plain` is a request name, not a registry name.

!!! code "Where in the code"
    - Hub: [`core/planning/base.py:246-279`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/base.py#L246-L279) (the protocol), [`registry.py:74-95`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/registry.py#L74-L95) (`resolve_planner`), [`core/planning/action_model.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/action_model.py) (the only door to the rules), [`backends/pyplanner_sage.py`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py)
    - Library: [`embodiplan/__init__.py`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/__init__.py), [`embodiplan/action_model.py`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/action_model.py), [`embodiplan/sage.py`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/sage.py)
    - Pins: [`pyproject.toml`](https://github.com/mtbui2010/robot_agent/blob/3269923/pyproject.toml#L21-L25), [`tests/test_action_model_agreement.py:285-287`](https://github.com/mtbui2010/robot_agent/blob/3269923/tests/test_action_model_agreement.py#L285-L287)

## The picture

```mermaid
flowchart LR
  subgraph HUB[robot_agent]
    LOOP[ClosedLoop / AssistLoop]
    REG[registry.resolve_planner]
    GB[backends/pyplanner_sage.py<br/>SageBackend]
    AM[core/planning/action_model.py]
    GATE[plan gate]
    GUARD[step guard]
    VER[post-step check]
  end
  subgraph LIB[embodiplan]
    SAGE[SAGEPlanner<br/>get 'SAGE']
    MODEL[action_model.py<br/>check / apply / simulate]
  end
  LOOP --> REG --> GB --> SAGE
  GATE --> AM
  GUARD --> AM
  VER --> AM
  AM --> MODEL
  SAGE --> MODEL
```

The same `MODEL` serves the planner (SAGE's own gate, inside the library) and the hub (three checks). That is by design: there is one source of the rules, so the planner and the robot cannot disagree about what a step needs. The terms used here are in the glossary: [gate](../start/glossary.md#certified-gate), [guard](../start/glossary.md#guard), [ClosedLoop](../start/glossary.md#closedloop) (planner key `sage_plain`), [AssistLoop](../start/glossary.md#assistloop) (planner key `sage`).

## 1. The planner protocol: two methods

The hub does not care which class plans. It needs the shape below ([`base.py:246-279`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/base.py#L246-L279)):

```python
@runtime_checkable
class Planner(Protocol):
    """Structural interface every planner backend must satisfy.

    Both methods return ``(steps, metrics)`` where ``steps`` is a list of
    PlanStep dicts and ``metrics`` is an arbitrary dict (may be empty ``{}``).
    See ``ACTION_MAPPER_SPEC.md`` section 1 and ``pyplanner/base.py``'s
    ``BasePlanner`` for the reference signatures.
    """

    def generate_plan(
        self,
        task: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        """Produce a full plan for ``task`` given the current observation."""
        ...

    def replan(
        self,
        task: str,
        completed: list[dict],
        failed_step: dict,
        failure_reason: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        """Produce a suffix plan recovering from ``failed_step``.

        ``completed`` is the prefix already executed successfully; the returned
        steps are spliced after it by the driver.
        """
        ...
```

| Method | Inputs | Returns |
|---|---|---|
| `generate_plan` | the task text, an observation string, the list of visible object names | `(steps, metrics)` |
| `replan` | the task, the steps already done, the failed step, the failure reason, the observation, the visible objects | `(steps, metrics)`: only the rest of the plan, which the loop adds after the done steps |

A step is a dict such as `{'action': 'Pick', 'object': 'cup'}`. `metrics` may be `{}`.

Some planners offer more. The core looks for these by attribute and uses them when present ([`base.py:289-345`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/base.py#L289-L345)):

| Optional capability | Members | What the core does with it |
|---|---|---|
| `SubgoalRepair` | `blocks()`, `join_blocks()`, `repair_block()` | Sends back only the sub-goal block that the plan gate found wrong. Without it the core asks for one fresh plan. |
| `ProgressReporting` | a `progress` attribute | Streams planning progress to the dashboard. |
| `EpisodeMemory` | `record_episode(task, steps)` | Called once with the steps of every run that ended in success. |
| `chat_override` | a context manager `chat_override(fn)` | The core can answer the planner's LLM calls with a model of its own (used for the gate's repair calls, option `repair_model`). A planner without it is repaired by its own model. `base.chat_override(planner)` finds it, also behind a proxy. |

Two more things are passed only when the planner says it takes them. `base.accepts(planner, method, name)` is true only for a parameter of that exact name (a `**kwargs` planner does not count). Then `generate_plan` and `replan` get `subgoals=` (one sub-goal per goal the loop understood, so the planner does not decompose the task) and `budget=` (an object with `take(n=1, what="")`, taken before every LLM call). A planner without the `budget` parameter gets one unit taken before the call and its reported `llm_calls - 1` added after it. A planner with the attribute `fixed_subgoals = True` keeps the steps of a sub-goal given as `(text, steps)`; the loop uses this only with the option `person_goals` (default off). SAGE in embodiplan 0.2.7 does all of this; see [Planner library (embodiplan)](../repos/embodiplan.md#sage-as-the-hub-uses-it).

`SageBackend` offers these through embodiplan's SAGE: it reads `last_blocks` and `edit_block` ([`pyplanner_sage.py:296-302`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L296-L302)) and calls `flatten` from `embodiplan.sage` ([`:304-307`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L304-L307)). In embodiplan these are in [`sage.py`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/sage.py) (`generate_plan` at line 597, `replan` at 807, `edit_block` at 472, `last_blocks` at 464, `flatten` at 251).

## 2. The action model: one source of rules

The action model is three functions, from embodiplan's header ([`action_model.py:8-10`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/action_model.py#L8-L10)):

```text
#     check(step, world)     -> list[Violation]   (errors + warnings)
#     apply(step, world)     -> world             (effect of a successful step)
#     simulate(plan, world)  -> VerifierReport    (check + apply over a plan)
```

`world` is duck-typed: any object with the fields `arrived`, `found`, `holding`, `opened` and `on`. Inside embodiplan that is `SymbolicState`; in the hub it is the robot's [belief](../start/glossary.md#belief), the `WorldState` class ([`base.py:49`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/base.py#L49)). The data types that cross are `Capabilities` (how many hands, whether `Find` brings the target within reach), `Violation` (a stable `code`, a `reason`, a severity), `ROBOT_ACTIONS` (the action names) and `ACTION_MODEL_VERSION`.

The hub reaches these only through its own file `core/planning/action_model.py`, which loads embodiplan on first use and forwards the call ([`action_model.py:18-53`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/action_model.py#L18-L53)). Its header says it plainly: the gate, the guard and the effects "all decide through this module, so they cannot drift apart again" ([`action_model.py:3-7`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/action_model.py#L3-L7)).

| Hub user | What it asks the model | Where |
|---|---|---|
| Plan gate (`AssistLoop._check`) | `check` and `apply` over a copy of the belief, step by step | [`assist_loop.py:1300-1336`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/assist_loop.py#L1300-L1336) |
| Step guard (before the robot moves) | `check` through `symbolic_check`; errors other than `unknown_action` refuse the step | [`verify.py:31-44`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/verify.py#L31-L44), called at [`assist_loop.py:3474`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/assist_loop.py#L3474) |
| Post-step check (after a skill said it succeeded) | `apply` on a copy, then the manifest's `effects` are tested on it | [`verify.py:73-83`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/verify.py#L73-L83) |
| World update after a step | `apply` in place | [`assist_loop.py:1606`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/assist_loop.py#L1606), [`mapper.py:194`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/mapper.py#L194) |
| Removal of useless steps, sub-goal repair | `check`, `apply`, `simulate` | `plan_tidy.py:39, 56`, `subgoal_repair.py:92` |

The robot's body comes from the [manifest](../start/glossary.md#manifest): `RobotView.model_capabilities()` turns the manifest's `capabilities` into the model's `Capabilities` ([`robot.py:140-141`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/robot.py#L152-L153); `Capabilities.from_any` reads `hands` or `arms`, and `find_reaches`, [`embodiplan/action_model.py:56-80`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/action_model.py#L56-L84)). This is how a robot manifest, described in [Robot package and core](robot-package.md), changes what a plan may contain. For the rules per action, see the embodiplan pages [Symbolic state](https://mtbui2010.github.io/embodiplan/concepts/symbolic-state/) and [Action model reference](https://mtbui2010.github.io/embodiplan/reference/action-model/).

Real values, run offline with the hub and embodiplan on `PYTHONPATH` (no robot, no LLM):

```text
ACTION_MODEL_VERSION 1.0 embodiplan 0.2.7
ROBOT_ACTIONS 14 Close Find LieOn MoveTo Open Pick Place PutIn Serve Sit TurnOff TurnOn Wait Wash
check Pick cup on empty belief: [('no_find_before_pick', 'error')]
```

and `simulate` on two small plans, printed with `summary()`:

```text
FAIL (1 violation(s)):
  step 1: [no_find_before_pick] Pick requires a preceding Find of the target object (Pick cup)
OK (3 steps verified)
```

The first plan is the single step `Pick cup`. The second is `MoveTo kitchen`, `Find cup`, `Pick cup`.

### `ACTION_MODEL_VERSION`

It is the string `"1.0"` ([`embodiplan/action_model.py:29`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/action_model.py#L29)), re-exported from the package ([`__init__.py:55`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/__init__.py#L55)). The header of the model file says rule changes bump it and are listed in embodiplan's `CHANGELOG.md` ([`action_model.py:16`](https://github.com/mtbui2010/embodiplan/blob/76da520/embodiplan/action_model.py#L16)). The hub forwards the name unchanged ([`action_model.py:45-53`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/action_model.py#L45-L53)), so `robot_agent.core.planning.action_model.ACTION_MODEL_VERSION` and embodiplan's are the same value by construction. The hub's test asserts this equality ([`test_action_model_agreement.py:285-287`](https://github.com/mtbui2010/robot_agent/blob/3269923/tests/test_action_model_agreement.py#L285-L287)). The hub does not otherwise compare the version at run time; no code refuses to start on a different value.

## 3. The version pin

Both sides declare the same range, `>=0.2.1,<0.3`:

| Place | Line | What it says |
|---|---|---|
| Hub `pyproject.toml`, `dependencies` | [`:24`](https://github.com/mtbui2010/robot_agent/blob/3269923/pyproject.toml#L24) | `"embodiplan>=0.2.1,<0.3"` |
| Hub `pyproject.toml`, extra `sage` | [`:59`](https://github.com/mtbui2010/robot_agent/blob/5058637/pyproject.toml#L59) | the same range again |
| Hub error text when embodiplan is missing | [`action_model.py:23-26`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/action_model.py#L23-L26) | "robot_agent needs embodiplan>=0.2.1,<0.3 ..." |
| Hub rule 3 | [`CLAUDE.md`](https://github.com/mtbui2010/robot_agent/blob/3269923/CLAUDE.md) | "public API, `>=0.2.1,<0.3`" |
| kcare_robot `pyproject.toml`, extra `planner` | [`:35`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/pyproject.toml#L35) | the same range |
| embodiplan rule 1 | [`CLAUDE.md`](https://github.com/mtbui2010/embodiplan/blob/76da520/CLAUDE.md) | "Keep the public API (`__all__`) backwards compatible within 0.x; robot_agent pins `>=0.2.1,<0.3`" |

The library at the checked commit is version `0.2.7` ([`pyproject.toml`](https://github.com/mtbui2010/embodiplan/blob/76da520/pyproject.toml), `__init__.py:57`), which is inside the range. The hub's test checks that its own `pyproject.toml` still holds a pin of the form `embodiplan>=0.2[.N],<0.3` ([`test_action_model_agreement.py:285-287`](https://github.com/mtbui2010/robot_agent/blob/3269923/tests/test_action_model_agreement.py#L285-L287)). It does not check the installed version. These tests were not run here (`pytest` is not installed on the machine that wrote this page); only what they check was read.

The hub also adapts to older versions in code. For example, a JSONL seed pool is used only if embodiplan lists `jsonl` in `SEED_FORMATS` (embodiplan 0.2.3 and later), otherwise the hub logs a warning and starts without it ([`pyplanner_sage.py:150-166`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L150-L166)).

!!! warning "embodiplan is both required and optional"
    In `pyproject.toml` embodiplan is a normal dependency (line 24), and it is also an extra named `sage` (lines 56-59) whose comment says it is "kept out of the core deps so the generic runtime never hard-depends on a planner". In practice the loops need it: the first call to the action model raises `ImportError` without it ([`action_model.py:20-26`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/action_model.py#L20-L26)). The package is local, not on PyPI: install it first (`pip install -e ../embodiplan`).

## 4. Where the hub imports embodiplan

Hub rule 3: import `embodiplan` only in `core/planning/backends/` and `core/planning/action_model.py` ([`CLAUDE.md`](https://github.com/mtbui2010/robot_agent/blob/3269923/CLAUDE.md)). A search of `robot_agent/` at the checked commit finds real imports only there:

| File and line | What is imported | Why |
|---|---|---|
| [`core/planning/action_model.py:21`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/action_model.py#L21) | `embodiplan.action_model` | the rules, on first use |
| [`backends/pyplanner_sage.py:144`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L144) | `embodiplan` | `available()`: can the planner be built here |
| [`backends/pyplanner_sage.py:160`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L160) | `embodiplan.memory_retriever.SEED_FORMATS` | seed pool format check |
| [`backends/pyplanner_sage.py:195, 222-223`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L195-L223) | `embodiplan`, then `embodiplan.get("SAGE", **selected)` | builds the planner |
| [`backends/pyplanner_sage.py:306`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L306) | `embodiplan.sage.flatten` | `join_blocks` |
| [`backends/examples.py:67`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/backends/examples.py#L67) | `embodiplan.memory_retriever` | ranks example plans, returns `None` without the library |

All imports are inside functions, so `import robot_agent.core.planning` does not load embodiplan ([`core/planning/__init__.py:22-23`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/__init__.py#L22-L23)). One other file touches the library without importing it: `observe/replay.py:418` only calls `planner_available('sage')`.

What crosses at `get("SAGE")`: the hub builds a dict of candidate settings (`host`, `model`, `provider`, `api_key`, `gt_path`, `live_path`, `safe_refine`, plus the extra keys the loop passes) and keeps only those named in `_SAGE_KWARGS` ([`pyplanner_sage.py:36-60, 206-221`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L36-L60)). So `get("SAGE")` returns the SAGE planner.

The test `test_no_private_embodiplan_access` ([`test_action_model_agreement.py:276-282`](https://github.com/mtbui2010/robot_agent/blob/3269923/tests/test_action_model_agreement.py#L276-L282)) checks by text search that `assist_loop.py` does not use four of SAGE's private names, and that three planning files do not mention `embodiplan.verifier`.

## 5. `resolve_planner`

`resolve_planner(name)` returns `(factory, builtin)` and takes four kinds of name ([`registry.py:74-95`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/registry.py#L74-L95)):

| Name | Result |
|---|---|
| `sage` (any case) | the built-in `SageBackend` (a wrapper around embodiplan's SAGE planner) |
| `llm_direct` | the built-in `LlmDirectBackend`, one LLM call, no embodiplan |
| `package.module:Class` | any importable class or factory that returns a `Planner` |
| an entry-point name in the group `robot_agent.planners` | what that entry point loads |

Any other name raises `ValueError`. Real results, run offline:

```text
'sage' -> SageBackend True
'llm_direct' -> LlmDirectBackend True
'SAGE' -> SageBackend True
'sage_plain' -> ValueError unknown planner 'sage_plain'; available planners: sage, llm_direct (or 'package.module:Class', or an entry point in group 'robot_agent.planners')
'tests.fakes:ScriptedPlanner' -> ScriptedPlanner False
```

### Request names and registry names

A request (the `planner` on `/ws/agent`, or `ROBOT_AGENT_PLANNER`) is not a registry name. `loop_choice` ([`planner_names.py:62-71`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/planner_names.py#L62-L71)) turns it into a planner for the ClosedLoop and a flag for the AssistLoop; it is called at [`unified_agent.py:369-371`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/unified_agent.py#L369-L371). Run offline, `loop_choice` gave:

```text
'sage' -> ('sage', True)
'sage_plain' -> ('sage', False)
'direct' -> ('llm_direct', False)
'tests.fakes:ScriptedPlanner' -> ('tests.fakes:ScriptedPlanner', False)
```

Then the choice of loop is made ([`unified_agent.py:379-381`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/unified_agent.py#L379-L381)): the flag `True` selects `AssistLoop`, otherwise `ClosedLoop` runs the planner. `AssistLoop` sets its own planner to the `generator` option, or the environment variable `ROBOT_AGENT_SAGE_GENERATOR`, or `sage` ([`assist_loop.py:372`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/assist_loop.py#L372)).

| User choice | Loop | Planner that is built |
|---|---|---|
| `sage_plain` | ClosedLoop | `SageBackend` (embodiplan's SAGE planner) |
| `direct` | ClosedLoop | `LlmDirectBackend` |
| `sage` | AssistLoop | by default `sage`, so the same `SageBackend` |
| `package.module:Class` or an entry point | ClosedLoop | that planner |

So both `sage_plain` and `sage` end in the same embodiplan class. They differ in the loop around it: AssistLoop adds understanding of the command, clarification and a guard on each step. The page [Planners](../concepts/planners.md) explains the choice in plain words.

!!! note "The generator is a registry name"
    The `generator` option and `ROBOT_AGENT_SAGE_GENERATOR` name a planner, so a registry name or `package.module:Class` fits. The ClosedLoop reads the value through `loop_backend` ([`loop.py:314`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/loop.py#L314)), which also maps `sage_plain` to `sage`; that was read in the code, not run. To add your own planner, see [Add a planner](../guides/add-a-planner.md).

## What breaks when the contract changes

| Change | What happens | Found by |
|---|---|---|
| embodiplan changes an action rule and bumps `ACTION_MODEL_VERSION` | The hub's gate, guard and post-step check change together, because they all call the one module. The manifest's `pre` and `effects` for each skill may now disagree with it. | the robot package's tests, the hub's agreement tests |
| embodiplan changes a rule but does **not** bump the version | The same change, with no signal. The hub's version test still passes, since it compares the two names of one value. | not machine-checked |
| embodiplan removes or renames a name in `__all__` | Breaks only within a major step of the range; embodiplan's rule 1 forbids this within 0.x. The hub names `get`, `LLMBackend`, `action_model`, `memory_retriever`, `sage.flatten`. | hub tests, at install |
| embodiplan `0.3` is released | `pip` refuses it for the hub (`<0.3`). The hub must edit three places (`pyproject.toml` twice, the error text), plus the kcare pin. | install |
| A planner returns something that is not `(steps, metrics)` | The loop cannot read the plan. `SageBackend` converts embodiplan's metrics object into a dict ([`_metrics_to_dict`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L63)). | run time |
| A new key is added to `_SAGE_KWARGS` that an older embodiplan does not take | Per the code comment, versions from 0.2.4 take the `actions` and `robot_description` keys; older ones take `**kwargs` and ignore them. The key `replan_block_fix` (set by the hub's option `replan_guard`, part `block_fix`) is not defined in embodiplan 0.2.7: its constructor takes `**kwargs`, so it is ignored there ([`pyplanner_sage.py:53-55`](https://github.com/mtbui2010/robot_agent/blob/5058637/robot_agent/core/planning/backends/pyplanner_sage.py#L53-L55)). | not machine-checked |

!!! warning "The household benchmark drives the hub by replacing module globals"
    `symbolic_robot`'s in-process benchmark runs the hub's loops without a server. Its adapter replaces `state.current`, `assist_loop.current`, `loop.current`, `host_id`, `skill_configs.ENV` and `connect.llm.init_llm_client` with its own values for each episode, and restores them after ([`robot_agent_adapter.py:1-17`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/planners/robot_agent_adapter.py#L1-L17)). That is not the hub's public API. A rename of any of those names inside the hub breaks the benchmark, and no hub test knows about it. Also, whether the benchmark's home pack matches the hub revision this site describes was not re-checked for this revision (an earlier version of this page found `planner:` manifest keys that the hub did not know yet; the hub has since added many options). **To avoid it:** when you benchmark, check that the hub revision you run is the one the benchmark was written for. See [Benchmark a planner](../guides/benchmark-a-planner.md).

For the planners themselves, go to the [embodiplan site](https://mtbui2010.github.io/embodiplan/): [SAGE](https://mtbui2010.github.io/embodiplan/planners/sage/), [action model reference](https://mtbui2010.github.io/embodiplan/reference/action-model/) and [interface reference](https://mtbui2010.github.io/embodiplan/reference/interface/).
