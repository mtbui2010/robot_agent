# Options: the plug-in interface of the engine

An option is one switchable behaviour of the planning loop (a safety refusal, a reach check for deliveries, the handling of a
correction that changes nothing). Since round 19 an option is a **module** that declares itself to the engine; the loop
(`core/planning/assist_loop.py`) does not hold `if self.cfg.get("<option>")` branches for it. Rules: [architecture.md](architecture.md),
CLAUDE.md rules 13-15. The reference for the flat run options and manifest keys of the options that are not moved yet is [env.md](env.md).

## The parts

| part | where | role |
|---|---|---|
| `OptionSpec`, `Key`, `Option` | `core/options/spec.py` | what an option declares: name, key schema, hooks, services, order |
| `resolve`, `OptionConfig` | `core/options/config.py` | the keys of one option: run option > environment > manifest, **per key** |
| `Registry`, `register` | `core/options/registry.py` | the list of options; built-in ones from `robot_agent/options/__init__.py`, others by entry point group `robot_agent.options` |
| `Dispatcher` | `core/options/dispatch.py` | builds the enabled options of one loop, calls their hooks in `order` |
| `Services` | `core/options/services.py` | the interface an option uses to ask the loop for something (`LoopServices` in `core/planning/option_services.py` implements it) |
| the options | `robot_agent/options/<name>.py` | `hazard`, `reach_delivery`, `unapplied_change` so far |

`python -m robot_agent.core.options list` prints the registered options, their keys and hooks.
`python -m robot_agent.core.options check MANIFEST.yaml` reports `planner.options` entries that name no option or key.

## Writing an option

```python
# robot_agent/options/my_option.py
from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="my_option",                                   # planner.options.my_option, and the lab_log flag
    keys=(Key("enabled", "bool", False, aliases=("my_option",)),),   # the legacy flat name: run option, $ROBOT_AGENT_MY_OPTION, planner.my_option
    hooks=("on_final",),                                # only the hooks it implements
    order=50, enabled="enabled",
    doc="One line for the options list.")

class MyOption(Option):
    def on_final(self, run):                            # self.cfg: its resolved keys; self.svc: the engine services
        return None                                     # None = no effect; a value ends the chain (decide hooks)

register(SPEC, MyOption)
```

Then: add `my_option` to the import line of `robot_agent/options/__init__.py`, an entry below in this file, and a test that runs it through
`AssistLoop` with the option on and off (`tests/test_architecture.py` lists the file in `THROUGH_THE_LOOP`). The pack's data, if the
option needs any, is read from the manifest (`planner.<section>`); the module holds no object, place, person or skill name and no word
of a language (CLAUDE.md rule 14); a missing declaration means the option does nothing.

Rules for the module: it imports the engine (`robot_agent.core.*`, `robot_agent.contract`) and nothing else of the package; never another
option (shared code goes DOWN into the engine as a public function); never a `_private` name of another module; functions of at most 60
lines, the file at most 400. Per-run state goes in `run.opt["<name>"]` (a dict the option creates), not in new attributes of the run.

## Keys and where they come from

A `Key` has a type (`bool`, `int`, `float`, `str`, `dict`), a default, `aliases` (the legacy flat names; the first one also names the
environment variable `ROBOT_AGENT_<ALIAS>`), `sources` and optional `choices`. The canonical names are `<option>.<key>` as a run option and
`planner.options.<option>.<key>` in the manifest. Sources are tried in the order the key lists them; the first that has a value wins; the
default is `("run", "env", "manifest")`. A key may leave out a source: `hazard.rules` is `("run", "env")`, because what a pack may
switch off is not the pack's to decide. A bool is read as the loop always read its flags (`1 | true | yes | on`, any case). In the manifest
the nested form wins over the flat legacy key; an empty value counts as no value. `AssistLoop.cfg` keeps the legacy names of every
registered option's keys (`cfg["hazard_rules"]`, `cfg["reach_delivery"]`, ...), so readers of `loop.cfg` (the benchmark adapter) see the
same values.

## Hooks

Each hook has a kind. `decide`: the first option that returns something other than None ends the chain and its value is the answer.
`transform`: the options are chained, each gets the value so far and returns a new one (None = unchanged). `observe`: all run, results
dropped. A *flow* hook is a generator (`yield from`): it may yield run events and return the value. `NEXT` and `STOP` (from
`robot_agent.core.options`) are the loop's own step signals.

| hook | kind | when | used by |
|---|---|---|---|
| `on_start(run)` | observe | run set-up, after the places are read | `reach_delivery` (time of the person snapshot) |
| `on_ground(mode, places, to)` | transform | the delivery mode for a goal to a person | `reach_delivery` |
| `on_goals(run)` | decide, flow | understood goals, before the read-back and the grounding | `hazard` (the gate) |
| `on_goal_edit(run, text)` | observe | a correction is about to be read | `unapplied_change` |
| `on_goal_reread(run, text, understood, goals, q)` | observe | the understand LLM re-read a correction | `unapplied_change` |
| `on_goal_unapplied(run, turn)` | decide, flow | the correction changed nothing (`turn.text`, `turn.change`) | `unapplied_change` |
| `on_step(run, step)` | decide, flow | before a step runs; returns the step that replaces it | `reach_delivery` |
| `on_step_done(run, step, held)` | observe | a step succeeded (`held`: the object in the hand before it) | `reach_delivery` |
| `on_step_failed(run, step, code, reason, action, obj)` | decide, flow | a step failed, before the recovery policy | `reach_delivery` |
| `on_goal_check(run, obj)` | decide | the end-of-run check of one goal: `(ok, why, skill)` | `reach_delivery` |
| `on_final(run)` | decide | end of run, before the goals are checked; returns the `done` event | `unapplied_change` |
| `on_goals_met(run)` | decide | the goals hold; returns the `done` event | `hazard` (partial refusal sentence) |
| `on_finish(run, fields)` | observe | the fields of a `done` event, before it is built | `hazard` (`refusals`) |

Services (`OptionSpec.services`, asked with `Dispatcher.query(name, *args, default=...)`): `hazard.forbids(goal, places)`,
`hazard.refused_goals(run)`, `hazard.gate_off()`, `reach_delivery.no_refetch()`. A query returns the first answer that is not None.

## The options

### `hazard`
The robot's safety rules refuse a goal, or drop it and say so at the end. The rules are the robot's safety profile, not this option's
([safety.md](safety.md)). Keys: `rules` (legacy `hazard_rules`; `core` (alias `profile`) | `pack` | `off`; run option and environment only),
`receivers` (legacy `hazard_receiver`; turns on the rules with `enabled_by: hazard.receivers`). Always on once registered; a robot with no
profile gets no refusal. Hooks `on_goals`, `on_goals_met`, `on_finish`. Module: `robot_agent/options/hazard.py`.

### `reach_delivery`
A delivery to a person ends within their reach or in their hand (round 15). Key `enabled` (legacy `reach_delivery`). Needs the pack's
`planner.person_snapshot: {trust_s}` and `planner.action_roles`; without them it does nothing. Details: [env.md](env.md) (`planner.reach_delivery`).
`lab_events`: `{flag: reach_delivery, why: stale | stale_no_handover | no_declared_spot | asked_where | known_where, ...}`. Module:
`robot_agent/options/reach_delivery.py` (the logic that was `core/planning/reach_delivery.py` is in the same file).

### `unapplied_change`
A correction the robot cannot apply is not dropped silently: the understand step's own question is asked once, and if the correction still
changes nothing the run ends `failed` ("I could not apply your change") instead of `success`. Key `enabled` (legacy `unapplied_change`).
Module: `robot_agent/options/unapplied_change.py`.

## Moving an option out of the loop (the recipe)

1. List every place in `assist_loop.py` that reads its keys (`grep` the key names) and the run attributes it sets.
2. Name the hook for each place (reuse a hook of the table if the moment is the same; add one to `HOOKS` only for a new moment).
3. Write the module with `SPEC` and the class; replace the loop's code by the dispatcher call at the same place; remove the key from
   `ASSIST_OPTIONS` and `_v2_cfg_base` (the registry adds the names to `ASSIST_OPTIONS`). The legacy flat name stays in `PLANNER_KEYS`
   (`contract/manifest.py`; the contract layer cannot import the registry) and its comment moves into the option's docstring.
4. Prove it: the option's own tests, `tools/lint_arch.py --update` (the counts must go down), and the **replay pin**: the same episodes
   before and after, equal (`--llm fake` dev tasks for the variants that use the option, and the offline replay of recorded runs).
