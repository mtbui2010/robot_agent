# The robot contract (`robot_agent.contract`)

`robot_agent` is the robot-agnostic core. Everything it needs to know about one
robot crosses a single boundary, the contract in `robot_agent/contract/`:

| Name | What it is |
|---|---|
| `FailureCode` | Why a skill failed, shared by every robot: `absent`, `perception_unavailable`, `unreachable`, `blocked`, `grasp_failed`, `dropped`, `hw_fault`, `estop`, `timeout`, `cancelled`, `software`, `unknown` |
| `SkillResult` | What a skill returns: `ok`, `code`, `msg`, `device`, `data` (evidence) |
| `SkillSpec` / `VerifySpec` | One skill as the manifest declares it |
| `Manifest` | The robot's `configs/manifest.yaml`: skills, capabilities, geometry, language |
| `RobotAdapter` | Protocol: `manifest`, `geometry`, `i18n`, `bind(step)`, `observe()`, `reconcile(world)`, `health()` |
| `RecoveryPolicy` | What to do after a failure, keyed by `FailureCode` |

The package is plain stdlib (plus pyyaml to read the manifest). Importing it
starts nothing: no FastAPI, no ROS, no planner.

The dependency only goes one way. The robot package imports
`robot_agent.contract`. The core never imports a robot by name; it calls
`load_adapter(robot_pkg)`.

## manifest.yaml

```yaml
robot: kaair
version: "1.0"
language: ko
capabilities: {base: true, arms: 1, hands: [right], lift: true, head: true, drawer: true}
geometry: {arrive_tol_m: 0.3, reach_m: 0.78, reach_far_m: 1.0}
skills:
  - name: pick                 # the skill name in skills_config.py / skills.json
    action: Pick               # the abstract planner action: MoveTo Find Pick Place Open Close Say Ask ...
    params: {type: object, properties: {inputs: {type: string}}, required: [inputs]}
    pre: ["found(?inputs)", "hand_empty"]   # world-model predicates; ?x = param x; "not P" negates
    hold: []
    effects: ["holding(?inputs)", "not hand_empty"]
    verify: [{kind: symbolic}, {kind: sensor, skill: grasp_succeed}]   # symbolic | sensor | vlm (prompt)
    interruptible: false       # holding / pulling: finish or release safely before stopping
    errors: [grasp_failed, unreachable]     # FailureCode values this skill may return
    version: "1.0"
    alias_actions: [PutIn]     # optional: more actions this skill realises
```

`Manifest.from_yaml(path)` validates the whole file. It raises one
`ManifestError` whose `errors` list names each bad field by path, for example
`skills[2] (pick).errors[1]: 'oops' is not a FailureCode (valid: ...)`. Keys the
contract does not know are kept in `extra`, so a robot can carry its own notes.

Lookups: `skill_for("Pick")`, `skill("pick")`, `actions`, `has("base")`,
`hands`, `arms`, `geometry_obj()`.

Unset geometry falls back to the values the core used to hard-code:
`arrive_tol_m` 0.3, `reach_m` 0.78, `reach_far_m` 1.0.

## The adapter

`load_adapter(robot_pkg)` resolves the adapter in this order:

1. `<pkg>/configs/adapter.py`, if it exists. It must define `ADAPTER` (an
   instance), `make_adapter()` / `get_adapter()` (a factory) or a class
   `Adapter`. New robots made from `robot_template` get this file.
2. Otherwise `NamemapAdapter(<pkg>.configs.grace_namemap, manifest)`, a shim
   over the existing duck-typed namemap. `bind()` reproduces what
   `ActionMapper.to_skill` and `ClosedLoop._build_params` do today:
   - `SKILL_MAP` / `NOOP_ACTIONS` / `SUPPORTED_ACTIONS`
   - `to_loc` / `to_obj`
   - `build_params`
   - `Pick` takes its object from `world.found`

   When there is a manifest, its skill for an action wins over `SKILL_MAP`, and
   geometry and language come from it. `observe`, `reconcile_world`, `health`,
   `vlm_hook`, `apply_skill_effect`, `PHRASES` and `RECOVERY_OVERRIDES` are
   taken from the namemap when it defines them.

`bind()` returns `(skill, params)`, returns `None` for a no-op step, and raises
`Unbindable` when the robot cannot do the step.

## Failures and recovery

Skills that still return `{'isdone': False, 'msg': ...}` go through
`SkillResult.from_legacy`:

- If the dict has a `code` that is a `FailureCode`, that code is used.
- Otherwise `classify_legacy_failure(msg)` reads the code from the text. This
  is the only place in the codebase that holds these string heuristics.
- `from_legacy` also accepts switchbot-style `{'ok', 'code'}` results, `bool`,
  `str` and `None`.
- `strict=True` turns `None`, or a dict with neither flag, into a failure.
  That matches the planner's verdict; the direct path counts them as success.

`to_legacy()` gives back the `{'isdone', 'msg', ...}` dict the loops read.

`RecoveryPolicy().decide(code, attempts)` gives a `Recovery`
(`stop`, `wait_health`, `health_check`, `reapproach`, `continue_search`,
`retry`, `refind`, `verify`, `ask_help`, `replan`). The defaults:

| Code | First | After the retries | Notes |
|---|---|---|---|
| estop, cancelled, software | stop | stop | never retried; software is reported |
| hw_fault | health_check (1) | stop | reported |
| perception_unavailable | wait_health (2) | stop | the place is **not** marked searched |
| timeout | health_check (1) | replan | |
| unreachable | reapproach (1) | replan | |
| blocked | retry (1) | ask_help | |
| absent | continue_search (3) | ask_help | the place is marked searched |
| grasp_failed | retry (1) | replan | |
| dropped | refind (1) | replan | reported |
| unknown | verify (1) | replan | |

An adapter overrides entries with
`recovery_overrides = {code: RecoveryRule | {"action": ..., "retries": ..., "then": ...} | "action"}`.

## Checking a robot

```bash
python -m robot_agent.contract.check_manifest <pkg_dir | repo_root | package> [--strict]
```

The check imports nothing from the robot and needs no ROS. It reads the skill
registry the way `SkillRegistry` builds it: `configs/skills_config.py`
`SKILL_CONFIGS`, then `configs/common/skills.json` or `data/skills.json`. It
reads skill signatures from source.

Errors:

- the manifest does not load;
- a skill is not registered, or its module or function is missing;
- the manifest declares a param the function cannot take;
- the function requires a param the manifest does not declare;
- a sensor verify names a skill that is not registered.

Warnings:

- a key the contract does not know;
- the namemap's `SKILL_MAP` binds an action to a different skill than the
  manifest;
- a skill that cannot be inspected (external, or not a plain top-level def).

A robot's own test can call `assert_manifest_matches(pkg_dir)`.

## Status

The contract is additive: it adds code and changes no existing behaviour.
`assist_loop` / `ClosedLoop` still import `grace_namemap` directly and keep
their own `_SOFTWARE_ERR` regex. The regex is identical to
`contract.SOFTWARE_ERR`, and a test fails if the two drift apart. Moving the
loops onto `load_adapter`, `SkillResult` and `RecoveryPolicy` is the next step
(roadmap steps 4 and 5).
