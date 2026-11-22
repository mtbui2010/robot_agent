# Generalize the core: design, level B (round A, stream DESIGN2)

Status: PROPOSAL. No production code was written or changed. This file replaces the level-A design scope (the minimal extension points):
the owner changed the scope on 2026-11-22 to level B, the full registry.
Base: robot_agent `8002347` (branch `generalize-core`), robot_fleet `d74f326` (read only), `embodiplan` as installed in the shared venv (read only).
Inputs: the level-B inventory (`docs/proposals/generalize_core_inventory.md`, branch `gc/inv2`, commit `05f8466`; cited below as "inv"), the golden
harness (branch `gc/gold`, commit `7cb7687`: 21 cassettes), `pex_case/docs/framework_fit.md` (cited as "fit"), the manifests of `kcare_robot`,
`symbolic_robot` (home, factory) and `pex_robot`, and the code at the lines cited. Date: 2026-11-22.
Note for a later push: this proposal names the owner's robot packs and quotes parts of their manifests. robot_agent is a public repository;
the file is a local commit on `generalize-core` and should be reviewed for that before it is pushed anywhere.

How to read the evidence: `path:line` is relative to the root of this repo. "read" = I read the lines for this design. "inv" = the number or line
comes from the inventory and I did not read it again. The YAML blocks are EXAMPLES of declared data, not files that exist. What I did not read is
listed at the end.

## Short answer

1. Level B = one versioned PACK DECLARATION per robot: data (goal kinds, predicates, actions with typed arguments, verifiers, recovery policies,
   questions and answers, phrases, trees, constants, fleet needs) plus NAMED registered callables. The household behaviour leaves the core and
   becomes the STANDARD pack `household`, inside this repo (`robot_agent/packs/household/`). A robot pack extends it or replaces parts of it.
2. A manifest with no `pack:` section is schema 1. The loader gives it the standard pack unchanged. So `kcare_robot`, `symbolic_robot` (home,
   factory), `pex_robot`, `tests/fakebot` and `tests/fakehome` load and behave exactly as today, with no edit.
3. The registry is a registry of NAMES. A pack file never holds code, a dotted path to evaluate, or an expression. It names a callable that a
   module registered (`std.find_at_destination`, `pex.pipe_in_clips`).
4. Every household rule moves in two levels. First: the old code becomes a named callable with the SAME body, chosen through a table (provable:
   a golden replay and a differential test). Second, and only where it pays: the callable is replaced by declarative data. The second level is
   needed for new packs, not for the household pack, and most of it can be deferred.
5. `assist_loop.py` (4081 lines) becomes a thin phase pipeline of about 300 lines. The phases are small modules (each 300 lines or less) with one
   interface: a generator that yields events and returns `NEXT`, `STOP` or `DONE` (the signals that exist today). The 30 hooks and 25 services
   stay where they are.
6. The honest structure is not six phases in a row. It is four stages in a row (understand, generate-and-certify, execute, final verify) and
   two services with the same interface that the stages call (recover, answer).
7. 28 migration steps. Every step that touches `assist_loop.py` makes it smaller (the table in "Migration steps" gives the estimate per step).
   Step 1 is not production code: it extends the goldens, because the 21 cassettes use one made-up robot (`fakebot`) and do not cover the
   template kinds (go, say, deliver), a hand-over, a close goal, a non-English run or several ladders.
8. The fleet reads goal kinds, their needs, their footprint slots and the question kinds from `GET /robot/manifest` (new fields, additive).
   Its `_NEEDS` table becomes a compatibility data file used only for a robot that reports no goal kinds.
9. Cost, stated plainly: about 2700 new lines (schema, loader, registry, tests), about 3750 lines moved out of `assist_loop.py`, about 700 lines of
   YAML, 28 steps of about one golden replay and one 60 s test run each. The parts I would defer are listed in "Cost and where level B could be
   overengineered"; a smaller cut that still removes G1 to G6 of "fit" is 13 of the 28 steps.
10. What level B does NOT fix: the gaps of "fit" that are not vocabulary (G8 to G13, S2, S5 to S8), and the string option "0" that is read as true
    (S1), which is a bug fix with a behaviour change and needs its own decision.

## Design rules

These six rules decide every section below.

- R1 Names, not code. A pack names callables that Python modules registered. No `eval`, no `exec`, no dotted path taken from data and imported
  as code (CLAUDE.md rule 7). The one module a pack may name is its own `callables` module, loaded the way `configs/adapter.py` is loaded today
  (`robot_agent/contract/legacy_adapter.py:227-240`, read).
- R2 Two levels. A household rule first becomes a named callable with the old body. Declarative data replaces a callable only when a test shows
  the data gives the same results on the old inputs.
- R3 Schema 1 is today. For a pack with no `pack:` section the core evaluates nothing that it does not evaluate today. Example: the `pre` list
  of a manifest skill is parsed and never evaluated today (inv, "Skills and actions" B); it stays unevaluated for schema 1.
- R4 One source of action rules. The rules of the standard actions stay in `embodiplan.action_model`, reached only through
  `robot_agent/core/planning/action_model.py` (61 lines, read). Declared preconditions and effects are an EXTENSION for the actions the model
  passes without a rule ("robot-specific actions: no symbolic precondition", embodiplan action_model.py line 371, read), not a copy.
- R5 The default path is byte-identical: the same events, the same prompts (the cassettes keep a hash of every prompt), the same model calls.
  A new event, event field or question kind exists only when a pack declares it.
- R6 `assist_loop.py` never grows, at any commit. A step that needs new code puts it in a new module.

## Pack declaration

### Where a pack file lives

| pack | files | who loads it |
|---|---|---|
| a robot pack | `<pkg>/configs/manifest.yaml`, new optional top-level key `pack:`; the sections may be inline or in `<pkg>/configs/pack.yaml` (`pack: {file: pack.yaml}`); optional `<pkg>/configs/pack_callables.py` | the manifest loader, then the pack loader |
| the standard pack | `robot_agent/packs/household/pack.yaml` and `robot_agent/packs/household/callables/*.py` | by name, from the index |
| the core pack | `robot_agent/packs/core/pack.yaml` and `callables/` (what the loop needs to run at all: say, ask, wait, stop, the generic questions) | always first in the chain |
| the index | `robot_agent/packs/index.yaml`: the pack names, their files, and `schema_1_base: household` with a `source` label | the pack loader |

The file reference follows a pattern the repo already has: `safety.profile` is a path relative to the manifest, loaded and validated with
errors by field path (`robot_agent/contract/manifest.py:525-541`, `robot_agent/core/safety/profile.py:82-89`, read). The folder `robot_agent/packs/`
is the owner's decision 2 (inside this repo, no new repo). It is inside the Python package so that an install ships it; `pyproject.toml` line 88-89
lists package data by hand today (`lang/*.json`), so the YAML files must be added there (Step 2).

Layering. `robot_agent/packs` is a new layer in `tools/arch_rules.json`: it may import `contract` and the engine's public pack API; the engine never
imports it. It is loaded by name, the way the option layer is (`BUILTIN = "robot_agent.options"`, `robot_agent/core/options/registry.py:16`, read).
The name `household` appears in the index file (data), not in engine code.

### The whole schema

```yaml
pack:
  schema: 2                 # integer. No `pack:` key at all = schema 1 (see "Compatibility")
  name: pex                 # [a-z][a-z0-9_]*: the namespace of this pack's callables, question kinds and failure subcodes
  version: "1.0"            # the pack's own version (free text, as `version` of the manifest)
  extends: household        # ONE base pack by name from the index, or null (the core pack only)
  file: pack.yaml           # optional: read the sections below from this file, relative to the manifest
  callables: pex_robot.configs.pack_callables   # optional: ONE module of the robot package; importing it registers names
  source: "pex_case task card, docs/case/case_text.md"   # the default label of entries that carry none; or `assumed: true`
  remove: {goal_kinds: [in, open, close], questions: [help_pick]}   # names of the base that this robot does not have
  goal_kinds: {}            # section "Goal kinds"
  predicates: {}            # section "Predicates"
  actions: {}               # section "Actions"
  verifiers: {}             # section "Verifiers"
  recovery: {}              # section "Recovery"
  questions: {}             # section "Answers"
  phrases: {}               # section "Phrases"
  trees: {}                 # goal kind -> behaviour tree (today: planner.options.bt_goal.trees)
  constants: {}             # per-robot numbers (clarify rounds, replans, search looks ...)
  options: {}               # option -> keys (today: planner.options)
  fleet: {}                 # section "Fleet needs"
```

Eleven data sections. Each entry of a section is a mapping with a name, its fields, and a label: `source: "<where the fact comes from>"` or
`assumed: true`. An entry with neither takes the pack's default label; a pack with neither fails validation in schema 2. This is the rule that
exists today for option data (`robot_agent/core/planning/pack_data.py:6-8`, read), made total.

### Merge rule

The loader builds a chain: `core`, then the base named by `extends` (and its own base), then the robot pack. For each section, an entry of a
later pack REPLACES the entry of the same name whole. There is no deep merge inside an entry (a half-overridden entry is hard to label and
hard to test). `remove` deletes names. One base only: no multiple inheritance and no import of single entries from another pack (see the
deferred list).

### Versioning

`pack.schema` is an integer that the loader knows (`SUPPORTED = (1, 2)`). A higher number is a load error that names the highest supported
schema. A schema change that only adds optional fields does not raise the number; a change of meaning does. The standard pack has its own
`version`; the answer of `GET /robot/manifest` reports `pack: {schema, name, version, extends}` so that a client can tell what it talks to.

### How the core loads and validates it

1. `Manifest.from_dict` accepts the top-level key `pack` and keeps it as written (`_TOP_KEYS`, `robot_agent/contract/manifest.py:40`, read;
   today an unknown key is kept in `Manifest.extra`, `:245`, and only warned about).
2. `robot_agent/contract/pack.py` (new, contract layer, no engine import): `parse(raw, where) -> PackDecl`, plain dataclasses, every bad field
   reported by path in one error, the style of `ManifestError` (`robot_agent/contract/manifest.py:118-127`, read). A YAML key that is not a
   string is an error with a hint: YAML 1.1 reads a bare `on:` as `True` (fit P1), so the kind must be written `"on":`.
3. `robot_agent/core/pack/loader.py` (new): `resolve(manifest, adapter) -> Pack`. It builds the chain, lifts a schema-1 manifest (see
   "Compatibility"), merges, imports the `callables` modules (base first), then validates the RESULT.
4. Validation of the result (schema 2: an error stops the load; data lifted from schema 1: fail open as today, the entry is not used):
   every callable name is registered in the right namespace; every predicate named in a `pre`, `effects`, `holds` or tree is declared, with the
   right number of arguments, for that use; every action named in `needs`, `trees` or `recovery.by_action` is declared or is a skill action of
   the manifest; every phrase key of a question exists in the manifest's default language; every entry is labelled.
5. The result is cached with the robot view (`RobotView`, `robot_agent/core/planning/robot.py:48-58`, read) as `robot.pack`. The loop reads the
   vocabulary only from there.

`python -m robot_agent.contract.check_manifest <pkg> --strict` runs steps 1 to 4 and prints the resolved pack (which entry came from which
pack), so a pack author sees what the robot will use.

### Named registered callables

`robot_agent/core/pack/registry.py` (new), the same shape as the option registry (`robot_agent/core/options/registry.py:27-30`, read:
a duplicate name is an error):

| namespace | signature (all pure unless said) | used for |
|---|---|---|
| `predicate` | `(belief, args) -> True / False / None` | conditions of trees, `pre`, `effects`, `holds` |
| `goal.reached` | `(goal, steps, world, ctx) -> bool` | does a plan reach the goal (the gate) |
| `goal.holds` | `(goal, belief, ctx) -> bool` | does the goal hold in the belief |
| `goal.missing` | `(goal, steps, world, ctx) -> text or None` | what the gate tells the planner |
| `goal.compile` | `(goal, at, ctx) -> steps` | the fixed steps of a template kind |
| `goal.ground` | generator `(goal, slot, ctx) -> value or a question` | grounding of one slot |
| `goal.phrase` | `(goal, places, nice) -> LocalText` | the read-back phrase |
| `goal.conflicts` | `(goals, ctx) -> (goals, question or None)` | the goal validator |
| `verifier.step` | `(step, result, ctx) -> list of VerifyResult` | per-step postconditions |
| `verifier.goal` | generator `(goal, ctx) -> (True / False / None, evidence, device)` | the end-of-run check |
| `recovery` | generator `(run, step, reason, rule, ctx) -> (outcome or None, replan hint)` | a ladder; the signature of `_recover_*` today (`robot_agent/core/planning/step_ladder.py:23`, read) |
| `answer` | `(text, spec, ctx) -> AnswerResult` | reading an answer |
| `action.rules` | `check(step, belief)`, `apply(step, belief)` | a rule provider (`std.embodiplan` wraps the action model) |

A name is `<pack>.<name>`. A module may register only names of its own pack (`pex.*` from the PEX module; `std.*` only from
`robot_agent/packs/household`; `core.*` only from the core pack), checked at registration, so no pack can replace a standard callable
silently. The number of parameters is checked at registration. `ctx` is a small protocol of services (ask, run a skill, ask the model inside
the run's budget, emit an event, read a constant): the existing `Services` protocol (`robot_agent/core/options/services.py:12-45`, read) is the
model and most of it is reused.

### Example: the two pack files (headers)

Standard household pack, `robot_agent/packs/household/pack.yaml`:

```yaml
pack:
  schema: 2
  name: std
  version: "1.0"
  extends: null                       # only the core pack below it
  callables: robot_agent.packs.household.callables
  source: "robot_agent 8002347: the constants of core/planning, options/bt_goal.py and contract/recovery.py, moved as data"
  # the sections follow; each one is shown in its own section of this document
```

PEX pack, `pex_robot/configs/manifest.yaml` (the existing file, plus):

```yaml
pack:
  schema: 2
  name: pex
  version: "1.0"
  extends: household                  # MoveTo / Find / Pick / Place keep the standard rules
  file: pack.yaml
  callables: pex_robot.configs.pack_callables
  source: "pex_case task card (docs/case/case_text.md) and pexcase/agent/task.py"
  remove: {goal_kinds: [in, open, close, deliver, give]}
```

## Goal kinds

Today: seven kinds in `GOAL_KINDS` and one optional kind (`robot_agent/core/planning/goals.py:33-54`, read), with their behaviour in if-chains:
40 lines in `assist_loop.py`, 24 in `goal_fix.py`, 23 in `goals.py`, 17 in `plan_repair.py` (inv, "Closed tables").

### Schema of one kind

| field | meaning | today |
|---|---|---|
| `slots` | name -> `{type: object / place / place_or_person / container / text / person, required}`; extra ad-hoc fields stay allowed | `{type, object, from, to}` (`robot_agent/core/planning/assist_loop.py:318-324`, read) |
| `needs` | list; an item is an action or a list = any one of them | `GoalKind.needs` (`goals.py:26-30`) |
| `needs_when` | `[{slot: to, add: MoveTo}]`: a need that depends on a filled slot | `say` with a place also needs MoveTo (`goals.py:69-70`, read) |
| `understood` | `always` (in the model's enum even when the robot lacks the actions: understood, then refused), `capable` (only when the needs are met), `option:<key>` | `MANIPULATION`, `TEMPLATE_KINDS`, `OPTIONAL_KINDS` (`goals.py:32`, `:47`, `:50`) |
| `canonical` | another kind this one is mapped onto after understanding | `give` -> `on` (`goals.py:93-96`) |
| `prompt` | the kind's line in the understand prompt, as text | `GoalKind.prompt` |
| `planned_by` | `planner`, `template` (fixed steps, no model) or `tree` | `is_template` (`goals.py:105-106`) and option `bt_goal` |
| `compile` | a `goal.compile` name (template kinds) | `compile_steps` (`goals.py:219-241`, read) |
| `reached`, `holds`, `missing` | names, or for `holds` a list of predicates | the if-chains `_reaches`, `_goals_hold`, `_missing` (`assist_loop.py:1484-1543`, read) |
| `observe` | a goal verifier name, or null = "no sensor declared for it (belief)" | only `on` / `in` are looked at (`assist_loop.py:3417-3418`, read) |
| `unknown_is` | `belief` (cannot tell = keep the belief) or `failure` | belief (inv, "Done semantics") |
| `ground` | slot -> a `goal.ground` name | `_ground_goals` and friends (`assist_loop.py:944-1137`, inv) |
| `phrase` | a `goal.phrase` name, or a phrase key with the slot names | `_goal_phrase` (`assist_loop.py:1236-1268`, read in part) |
| `conflicts` | a `goal.conflicts` name or `none` | `conflicts` for on / in / open / close, no off switch (`goals.py:122-148`, read; fit P7) |
| `limits` | which manifest limits apply to the object (`payload`, `size`) | only on / in (`assist_loop.py:870`, inv) |
| `fleet` | see "Fleet needs" | the fleet's own tables |

"Done" semantics of a kind, in one sentence: the kind is done when `holds` is true in the belief AND its `observe` verifier does not say False
(`unknown_is` decides what "cannot tell" means). That is today's rule (inv, "Verifiers and recovery" A, the paragraph on "done") with the two
parts named.

The ORDER of the kinds is part of the understand prompt and of the schema enum (`kinds_for`, `goals.py:74-81`, read: the manipulation kinds,
then the template kinds the robot can do, then the optional ones). The loader keeps the file order; a robot pack's new kinds come after the
base's. The prompt hash in the cassettes guards this.

### Example: standard household pack

```yaml
goal_kinds:                                   # source: robot_agent/core/planning/goals.py:33-54
  "on":
    slots: {object: {type: object, required: true}, from: {type: place}, to: {type: place_or_person, required: true}}
    needs: [Pick, Place]
    understood: always
    planned_by: planner
    reached: std.on_reached          # the body of AssistLoop._on_reached + the `on` branch of _reaches
    holds: std.on_holds              # the `on` branch of _goals_hold (placed, not in the hand, handed over when asked)
    missing: std.on_missing
    observe: std.find_at_destination
    unknown_is: belief
    phrase: std.on_phrase
    conflicts: std.one_destination
    limits: [payload, size]
  "in":      {needs: [Pick, [PutIn, Place]], understood: always, planned_by: planner, reached: std.in_reached, holds: std.in_holds,
              missing: std.in_missing, observe: std.find_at_destination, phrase: std.in_phrase, conflicts: std.one_destination, limits: [payload, size]}
  open:      {needs: [Open], understood: always, planned_by: planner, reached: std.open_reached, holds: std.open_holds, observe: null,
              conflicts: std.last_open_close}
  close:     {needs: [Close], understood: always, planned_by: planner, reached: std.close_reached, holds: std.close_holds, observe: null,
              conflicts: std.last_open_close}
  go:        {needs: [MoveTo], understood: capable, planned_by: template, compile: std.go_steps, reached: std.template_reached,
              holds: std.go_holds, observe: null,
              prompt: '"go" = only go somewhere, nothing to move: object "", from "", to = the place.'}
  say:       {needs: [Say], needs_when: [{slot: to, add: MoveTo}], understood: capable, planned_by: template, compile: std.say_steps,
              reached: std.template_reached, observe: null, prompt: '"say" = tell someone something: ...'}       # the text of goals.py:40-42
  deliver:   {needs: [MoveTo, WaitForUnload], understood: capable, planned_by: template, compile: std.deliver_steps,
              reached: std.template_reached, observe: null, prompt: '"deliver" = take what the robot carries ...'}  # goals.py:43-45
  give:      {needs: [Pick, Place], understood: "option:understand_give_kind", canonical: "on", prompt: '"give" = hand an object ...'}   # goals.py:50-53
```

### Example: PEX pack

```yaml
goal_kinds:
  install:
    source: "pex_case task card: one pipe on one wall, seated in both clips and crimped"
    slots: {object: {type: object, required: true}, to: {type: place, required: true}}
    needs: [MeasureClips, Pick, Seat, Crimp, Place]
    understood: capable
    planned_by: tree                       # trees.install below ("Actions")
    holds: ["pipe_seated(?object, ?to)", "crimped(?object)", "hand_free()"]
    observe: pex.pipe_in_clips             # a real end-of-run check ("Verifiers")
    unknown_is: failure                    # "could not look" is not "done"
    conflicts: none                        # one pipe per wall: two install goals are not a contradiction (fit P7)
    phrase: rb_install                     # a phrase key with the slots {object} {place}
    prompt: '"install" = mount a pipe on a wall: object = the pipe, from "", to = the wall. One goal per wall.'
```

This removes fit G1 (no `install` kind), P7 (the validator with no switch) and the wrong read-back source of P8 (no `from` slot to fill).

## Predicates

Today there are three vocabularies that do not know each other (inv, "Conditions, predicates and the belief"): the six tree conditions
(`robot_agent/options/bt_goal.py:50-61`, read), the six predicates of a skill's `effects` (`robot_agent/core/planning/verify.py:53-70`, read), and
the belief fields of the action model. A pack can add none.

### Schema

One table for all uses. An entry: `{eval: <predicate name> | kind: fact, args: [...], scopes: [...]}`.

- `eval` names a registered callable `(belief, args) -> True / False / None`. None means "cannot tell".
- `kind: fact` needs no code: the predicate is true when the fact store holds it. The fact store is a new open field of the belief
  (`WorldState.facts`: name -> set of argument tuples; `robot_agent/core/planning/base.py:71-97`, read, has only fixed fields today). Facts are
  added and removed by declared action effects ("Actions"). An empty fact store is not written into `to_dict()`, so the `world` of every
  event of today's runs is unchanged.
- `args` are typed names (`object`, `place`, `container`); a trailing `?` marks an argument that may be the wildcard `*`.
- `scopes` say where the predicate may be used: `tree` (a `cond` leaf), `effect` (after a step), `pre` (before a step), `goal` (a kind's `holds`).

Each consumer keeps its own rule for "cannot tell" and for a wrong number of arguments, because they differ today and must not change:
a tree reads both as False (`bt_goal.py:55-58`); an effect check reads "cannot tell" as "not checked, the step passes" (`verify.py:69`, `:92`).
In scope `tree` every argument is required (the wildcard exists only for effects). With the scopes below, the tree accepts exactly the six
names of `CONDITIONS` and the effect check exactly its six, as today.

An unknown predicate: schema 1 keeps today's behaviour (tree: the tree is unusable and the goal goes to the planner, with no event,
`bt_goal.py:92-95`; effect: not checked). Schema 2: a load error that names the entry.

### Example: standard household pack

```yaml
predicates:                       # sources: options/bt_goal.py:50-61 (tree), core/planning/verify.py:53-70 (effect)
  holding:    {eval: std.holding,   args: [object?],        scopes: [tree, effect, pre, goal]}
  hand_free:  {eval: std.hand_free, args: [],               scopes: [tree, pre, goal]}
  hand_empty: {eval: std.hand_free, args: [],               scopes: [effect]}       # the manifest's name for the same test
  at:         {eval: std.at,        args: [place],          scopes: [tree, pre, goal]}
  arrived:    {eval: std.at,        args: [place?],         scopes: [effect]}
  placed:     {eval: std.placed,    args: [object, place],  scopes: [tree, goal]}  # put there and not in the hand
  opened:     {eval: std.opened,    args: [container?],     scopes: [tree, effect, pre, goal]}
  closed:     {eval: std.closed,    args: [container],      scopes: [tree, goal]}
  found:      {eval: std.found,     args: [object?],        scopes: [effect]}
  "on":       {eval: std.switched_on, args: [appliance?],   scopes: [effect]}
```

### Example: PEX pack

```yaml
predicates:
  clips_measured: {kind: fact, args: [place], scopes: [tree, pre, goal], source: "set by MeasureClips / UseMeasuredClips"}
  pipe_seated:    {kind: fact, args: [object, place], scopes: [tree, pre, goal], source: "set by Seat"}
  crimped:        {kind: fact, args: [object], scopes: [tree, goal], source: "set by Crimp"}
  pipe_on_clips:  {eval: pex.pipe_on_clips, args: [object, place], scopes: [tree, goal],
                   source: "pex_robot/sight.py: the observed pipe lies on the measured clips"}   # reads the observation; None with no fresh one
  stand_free:     {eval: pex.stand_free, args: [place], scopes: [tree], source: "pexcase leaf stand_free"}
```

This removes fit G2 (no `clips_measured`, `seated`): the subtrees that today hide inside each PEX skill can use these conditions in the
goal's own tree.

## Actions

Today (inv, "Skills and actions"): a step is `{action, object}`; 67 lines of `assist_loop.py` name one of 13 actions in quotes; "the argument
is a place" is written in five places (`robot_agent/core/planning/assist_loop.py:81`, read, and four more files, inv D6); a custom action gets
no precondition, no belief effect, no ladder; a tree's `act` takes `(action, obj)` only (`robot_agent/options/bt_goal.py:164-169`, read).

Two things are separate and stay separate: a SKILL (the robot's callable, in `skills:` of the manifest, unchanged) and an ACTION (the abstract
name a plan uses). This section declares actions.

### Schema of one action

| field | meaning | today |
|---|---|---|
| `class` | what the engine's own code needs to know about the action: `move`, `look`, `grasp`, `release`, `open`, `close`, `speak`, `wait`, `sense`, or none | the action names in quotes in the loop |
| `arg` | the main argument (the step's `object`): `{name, type: place / object / container / text / seconds / none}` | `_LOC_ACTIONS` and its four copies |
| `args` | further typed arguments: name -> `{type: string / number / integer / boolean / enum, values, min, max, unit, default, required}` | none (fit G3) |
| `plan_step` | false for sensors and the dialogue | `NOT_PLAN_STEPS` (`robot_agent/core/planning/robot.py:38`, read) |
| `rules` | the rule provider: `std.embodiplan` (the action model) or `declared` | embodiplan for its 14 names, nothing for the others |
| `pre` | predicates that must hold before (only with `rules: declared`) | parsed from the skill, never evaluated |
| `effects` | `{add: [...], del: [...]}` on the fact store (only with `rules: declared`) | none for a custom action |
| `releases` | `true`, `false` or `from_effects` | `Place`, `PutIn` by name; a hand-over by its effects (`robot.py:132-141`, read) |
| `post` | step verifier names run after the manifest's own `verify` specs | two rules in `_post_check` |
| `recovery` | a recovery policy name | the dict at `assist_loop.py:3701-3703` (read) |
| `errors` | failure codes it may return, with optional pack subcodes | the skill's `errors`, parsed, not read |
| `duration_s` | `{typical, max}` with a label | none |
| `verb`, `ability`, `describe` | phrase keys (progress line, understand prompt, planner prompt) | `VERBS`, `_ABILITY`, `ACTION_TEXT` (inv D5) |

`class` is NOT the option roles. `planner.action_roles` stays what it is: a role a pack did not declare is None and the option part does
nothing (`robot_agent/core/planning/action_roles.py:11-12`, read). The standard pack does not fill roles in for a robot; doing so would switch
ten options on for packs that never asked for them.

Typed arguments. A step may carry `args: {name: value}` next to `object`. They are validated against the action's `args` when a tree is
loaded, when a plan is certified and when the step is bound. They reach the skill as `params`, for the names the skill's own `params` schema
declares (the rule the loop already uses for `min_conf` and `verify` of a Find, `assist_loop.py:1562-1565`, read). A tree leaf carries them
as one mapping after the object: `[act, Seat, "{to}", {arm: left, dz_mm: 2}]`. Today that shape passes `valid` only when every argument is a
string, and a third argument fails at tick with a `TypeError` (inv, G3).

Declared rules. For an action with `rules: declared`, the wrapper `robot_agent/core/planning/action_model.py` adds to `check` the `pre`
predicates that do not hold (a violation with the code `pre_<predicate>`) and `apply` adds and removes the `effects` facts. The wrapper stays
the one place the rules are read (R4). The pre-step guard (`_precondition`, `assist_loop.py:3467-3475`, read) and the plan gate already go
through it, so a custom action gets a guard and a gate with no new call site.

What the core keeps. The core pack declares only `Say`, `Ask`, `Wait`, the sensor classes (`Observe`, `Verify`, `Query`) and the virtual step
`Check`. `MoveTo`, `Find`, `Pick`, `Place`, `PutIn`, `Open`, `Close`, `Announce`, `WaitForUnload` are declared by the standard pack.

### Example: standard household pack

```yaml
actions:                     # sources: assist_loop.py:81 (place arguments), :3701-3703 (ladders), contract/recovery.py:97 (STEP_LADDER),
                             #          core/planning/robot.py:42-45, robot_prompt.py:22-37, announcer.py:25-39 (words)
  MoveTo: {class: move,    arg: {name: place,  type: place},  rules: std.embodiplan, recovery: std.move_ladder, verb: verb.MoveTo, ability: ability.MoveTo}
  Find:   {class: look,    arg: {name: object, type: object}, rules: std.embodiplan, recovery: std.find_search, post: [std.find_reach],
           args: {min_conf: {type: number, from_option: find_min_conf}, verify: {type: boolean, from_option: find_verify}}}
  Pick:   {class: grasp,   arg: {name: object, type: object}, rules: std.embodiplan, recovery: std.pick_ladder}
  Place:  {class: release, arg: {name: place,  type: place},  rules: std.embodiplan, releases: true, recovery: std.release_refind, post: [std.release_second_look]}
  PutIn:  {class: release, arg: {name: place,  type: container}, rules: std.embodiplan, releases: true, recovery: std.release_refind, post: [std.release_second_look]}
  Open:   {class: open,    arg: {name: place,  type: container}, rules: std.embodiplan, recovery: std.open_in_search}
  Close:  {class: close,   arg: {name: place,  type: container}, rules: std.embodiplan}
  Announce:      {class: speak, arg: {name: text, type: text}}
  WaitForUnload: {arg: {name: object, type: object}}
```

A hand-over or a dock is not declared by name: it is recognised from the skill's effects, as today (`releases: from_effects` is the default
for an action the pack does not list).

### Example: PEX pack

```yaml
actions:            # the numbers are placeholders for Round C to replace from pexcase/agent/task.py; each is marked
  MeasureClips:
    arg: {name: wall, type: place}
    args: {looks: {type: integer, min: 1, max: 5, default: 3, assumed: true}}
    rules: declared
    effects: {add: ["clips_measured(?wall)"]}
    errors: [unknown, {code: unknown, subcode: pex.clip_off_plan}]     # the mm are in the result's data
    recovery: tree                         # the goal's tree decides (it asks the person)
    verb: verb.MeasureClips
  UseMeasuredClips: {arg: {name: wall, type: place}, rules: declared, effects: {add: ["clips_measured(?wall)"]}, recovery: tree}
  Reorient: {arg: {name: object, type: object}, rules: declared, pre: ["holding(?object)"], recovery: tree}
  Seat:
    arg: {name: wall, type: place}
    args:
      arm:   {type: enum, values: [left, right], default: left}
      pose:  {type: enum, values: [planned, measured], default: planned}
      dz_mm: {type: number, min: -10, max: 10, default: 0, unit: mm, assumed: true}
    rules: declared
    pre: ["holding(*)", "at(?wall)", "clips_measured(?wall)"]
    effects: {add: ["pipe_seated(?held, ?wall)"]}
    post: [pex.pipe_on_clips_step]
    recovery: pex.seat_retry
    duration_s: {typical: 20, assumed: true}
  Crimp: {arg: {name: object, type: object}, args: {arm: {type: enum, values: [left, right], default: right}}, rules: declared,
          pre: ["pipe_seated(?object, *)"], effects: {add: ["crimped(?object)"]}, recovery: tree}
trees:
  install:
    source: "pexcase/agent vocab.json template_order"
    events: true                       # every node is a public `tree_node` event (fit G6)
    tree:
      - fallback
      - [cond, pipe_on_clips, "{object}", "{to}"]
      - - sequence
        - - fallback
          - [act, MeasureClips, "{to}"]
          - [sequence, [ask, pex_clip_far], [cond, answered, pex_clip_far, use_measured], [act, UseMeasuredClips, "{to}"]]
        - [act, MoveTo, rack]
        - [act, Find, "{object}"]
        - [act, Pick, "{object}"]
        - [act, Reorient, "{object}"]
        - [act, MoveTo, "{to}"]
        - [retry, 2, [act, Seat, "{to}", {arm: left}]]
        - [act, Crimp, "{object}", {arm: right}]
        - [act, Place, "{to}"]
        - [cond, pipe_on_clips, "{object}", "{to}"]
        - [cond, hand_free]
```

The tree grammar is today's (`robot_agent/core/planning/behavior_tree.py`, 90 lines, read) with three additions: an optional mapping as the last
argument of `act`; `ask` and `confirm` may name a question of the pack ("Answers"); the per-node trace becomes a public event when the tree
says `events: true` (today it is a lab flag capped at 50 entries, `bt_goal.py:158-159`, read). `planner.options.bt_goal.trees` keeps working.
This removes fit G3 and G6, and makes S10 smaller: a recovery written in the goal's tree is visible, one written inside a skill is still not.

## Verifiers

Today (inv, "Verifiers and recovery" A): the per-step check is `StepVerifier` over the manifest's `verify` specs plus two rules of the loop
(`_post_check`, `robot_agent/core/planning/assist_loop.py:3599-3630`, read: a release looked at twice, a Find seen out of reach); the end-of-run
check looks only at `on` / `in` goals with a Find at the destination (`_observe_goal`, `:3415-3451`, read). Any other kind is "done" by belief.

### Schema

Two kinds of verifier, both by name:

- A STEP verifier is named in an action's `post`. It runs after the layers that exist today (isdone, the declared effects, the VLM specs,
  the sensor specs). `StepVerifier` itself is not changed: it is already driven by manifest data and names no action.
- A GOAL verifier is named in a kind's `observe`. It returns `(True / False / None, evidence text, device)`, the triple `_observe_goal`
  returns today. The run's `goal_check` event and the failure code logic around it (`_observe_goals`, `:3345-3374`, read) stay in the core.

A verifier entry is either a registered callable (`callable: <name>` and free parameters) or a declared check with no code, in the form the
manifest already has for a skill (`VerifySpec`, `robot_agent/contract/types.py:155-187`, read): `{kind: sensor, skill: <a sensor skill>, expect:
true}` or `{kind: vlm, prompt: ...}`. So a pack can give a kind a real end check with one sensor skill and no Python.

### Example: standard household pack

```yaml
verifiers:                                    # source: assist_loop.py:3415-3451, :3599-3630, :3369-3373
  std.find_at_destination: {callable: std.find_at_destination, second_look: true}   # go there, Find; a miss after a verified release looks once more
  std.release_second_look: {callable: std.release_second_look}                      # two more gripper readings before a release counts as failed
  std.find_reach:          {callable: std.find_reach, limit: geometry.reach_far_m}  # a Find seen farther than this fails the `reach` layer
  end_of_run: {return_to_last_go: true}       # "go back and charge": the robot ends where the last `go` goal says
```

### Example: PEX pack

```yaml
verifiers:
  pex.pipe_in_clips:                           # the goal verifier of `install`
    kind: sensor
    skill: clip_look                           # a sensor skill of the pack (action Verify): isdone = the pipe lies on both measured clips
    expect: true
    params: {looks: 3, tol_mm: 8}
    assumed: true                              # 8 mm is the pack's own assumed tolerance (fit, "Honest limits": ON_WALL_TOL_M)
  pex.pipe_on_clips_step: {kind: sensor, skill: clip_look, expect: true, params: {looks: 1, tol_mm: 8}, assumed: true}
```

With `goal_kinds.install.observe: pex.pipe_in_clips` and `unknown_is: failure`, the robot's "done" rests on a declared check of the pipe in
the clips, not on a Find that sees a pipe within range (fit P8 b, and the last bullet of "Honest limits of the pack").

## Recovery

Today (inv, "Verifiers and recovery" B): a failure code maps to a rule in a table a pack may override per code
(`robot_agent/contract/recovery.py:66-91`, read); `STEP_LADDER` names two actions with a ladder (`:97`); a dict keyed by six action names picks a
`_recover_*` method (`robot_agent/core/planning/assist_loop.py:3701-3703`, read); any other action goes straight to the planner; the help
questions are English text in the code (`robot_agent/core/planning/step_ladder.py:63`, `:96`, read).

### Schema

```yaml
recovery:
  codes: {}        # failure code -> rule; an overlay on the contract's table (the same fields as RecoveryRule)
  policies: {}     # name -> a policy: a registered callable, a declared ladder, or a builtin
  by_action: {}    # action -> policy name
  by_kind: {}      # goal kind -> policy name: it wins over by_action while a goal of that kind runs
  failure_patterns: []   # text pattern -> code, per language (today 13 English regexes, contract/failures.py:26-57)
```

- Selection order for a failed step: the step's own `recovery` (a tree leaf may set it), then `by_kind[goal kind]`, then `by_action[action]`
  (the action's `recovery` field is the same table), then the builtin `planner`. The steps before the ladder do not change: the failure
  code, the rule of the code, hook `on_step_failed`, the never-retry stop, the policy rung, the same-failure stop (`assist_loop.py:3660-3708`).
- Builtin policies: `planner` (today's default: replan or fail), `stop`, `tree` (the step stays failed and the goal's tree decides; without a
  tree it is `planner`). `tree` is what the service `no_replan` does for the planner rung today, extended to the whole ladder (fit G4).
- A declared ladder is a list of rungs from the vocabulary that exists (`Recovery`, `recovery.py:20-31`, read): `{do: retry | reapproach |
  refind | verify | wait_health | ask | replan | stop, times, question, with}`. `question` names a question of the pack; `with` sets typed
  arguments of the step for the retry. One generic executor (`core.ladder`) runs it; `step_rung` (`recovery.py:100-111`, read) is its core.
- The code table is NOT moved out of the contract. It is already data that a pack overrides (`recovery_overrides`), it is the documented
  "core default for every robot", and the contract layer may import nothing. `recovery.codes` is one more overlay on it.
- Failure codes stay the 13 of `FailureCode`. A pack adds a SUBCODE (a namespaced string on the result, for example `pex.clip_off_plan`); a
  rule may be keyed `unknown/pex.clip_off_plan`. The enum is not opened (see the deferred list).

### Example: standard household pack

```yaml
recovery:                                   # source: assist_loop.py:3701-3703, contract/recovery.py:97, step_ladder.py, search_hooks.py:143-160
  policies:
    std.find_search:    {callable: std.find_search}                       # relook, mark searched, search legs, ask where, second looks
    std.pick_ladder:    {callable: std.pick_ladder, ladder_code: grasp_failed, may_replan: true}
    std.move_ladder:    {callable: std.move_ladder, ladder_code: blocked,      may_replan: false}
    std.release_refind: {callable: std.release_refind, floor_head: floor}  # the dropped object is looked for on the room's `floor` place
    std.open_in_search: {callable: std.open_in_search}
  by_action: {Find: std.find_search, Pick: std.pick_ladder, MoveTo: std.move_ladder, Open: std.open_in_search,
              Place: std.release_refind, PutIn: std.release_refind}
  by_kind: {}
  codes: {}                                 # nothing: the contract's table is the default
```

### Example: PEX pack

```yaml
recovery:
  by_kind: {install: tree}                  # every failed step of an install goal goes back to the goal's tree: no engine ladder,
                                            # no "could you clear the way?", no walk to a floor place (fit G4, P4)
  by_action: {Seat: pex.seat_retry}
  policies:
    pex.seat_retry:
      source: "pexcase subtree seat: retry with a z offset, then the person"
      ladder:
        - {do: retry, times: 1, with: {dz_mm: 2}}
        - {do: retry, times: 1, with: {dz_mm: -2}}
        - {do: ask, question: pex_clip_far}
        - {do: stop}
  codes:
    dropped: {action: stop, report: true}   # the pack has no floor place on purpose
```

## Phrases

Today (inv, "Phrases"): 62 English phrases in `CORE_PHRASES` (`robot_agent/core/planning/speech.py:24-87`, read in part), 8 in `CORE_TEXT`
(`robot_agent/core/planning/robot.py:21-34`, read), the understand prompt (`robot_agent/core/planning/assist_loop.py:329-356`, read), 20 checker
sentences (`:1354-1421`, inv), verbs and action words in three tables, and English sentences inside the code. The lookup order of a phrase
is: the robot's own i18n, the language pack's `speech` block, the core English (`speech.py:235-246`, read).

### Schema

`phrases: {<key>: {<language>: <text with {slots}>, slots: [names], source | assumed}}`. A key is free text with a prefix by use:
`q_` a question, `rb_` a read-back piece, `say_` a statement, `verb.<Action>`, `ability.<Action>`, `describe.<Action>`, `finding.<code>` (what the
gate tells the planner), `prompt.<name>` (a model prompt; a long text may be `{file: prompts/understand.en.txt}`).

Lookup order after the change: the robot's own i18n (the adapter, unchanged), the language pack's `speech` block (unchanged), then the pack
chain (robot pack, standard pack, core pack) in the place of the `CORE_PHRASES` constant. The order of the first two does not change, so a
language pack still wins over the moved English text, as today. The pack chain is used for a language only when it has that language, so a
Korean run still goes to the translator for a phrase with no Korean text, as today.

The 62 phrases are split by use, not rewritten: the generic ones (`q_confirm`, `q_repeat`, `say_stopped`, ...) go to the core pack, the
household ones (`rb_on`, `q_which_drawer`, `q_where_put`, ...) to the standard pack. The proof is one test: the union of the two files equals
`CORE_PHRASES`, and no key is in both. The name `speech.CORE_PHRASES` stays as a loaded view (the loop's `_fmt_en` reads it,
`assist_loop.py:118-120`, read).

Not converted in the move steps: an English sentence written inline in the code (the Pick and MoveTo help questions, the failure messages).
Giving such a sentence a phrase key changes how a NON-English run renders it (a `LocalText` instead of a plain string for the translator).
That is a later, separate step with a non-English golden.

### Example: standard household pack

```yaml
phrases:                          # source: core/planning/speech.py:24-87 (the household keys), announcer.py:25-39, robot.py:42-45
  rb_on:       {en: "put the {object} on the {place}", slots: [object, place]}
  rb_on_from:  {en: "put the {object} from the {source} on the {place}", slots: [object, source, place]}
  rb_in:       {en: "put the {object} in the {place}", slots: [object, place]}
  rb_open:     {en: "open the {place}", slots: [place]}
  q_where_is:  {en: "I couldn't find the {object}. Where is it?", slots: [object]}
  q_which_one: {en: "I see more than one {object} here: {text}. Which one do you mean?", slots: [object, text]}
  ability.Pick: {en: "pick it up"}
  prompt.understand: {file: prompts/understand.en.txt}       # the text of UNDERSTAND_PROMPT, byte for byte
```

and in the core pack: `q_confirm: {en: "{summary} Is that right?", slots: [summary]}`, `rb_will: {en: "I will {what}."}`.

### Example: PEX pack

```yaml
phrases:                          # source: pex_robot/configs/adapter.py PHRASES (today a Python dict)
  rb_install:           {en: "install the {object} on the {place}", slots: [object, place]}
  q_pex_use_measured:   {en: "A clip is {mm} mm from its planned place, more than I can seat the pipe on without help. Shall I use the measured clip positions, measure again, or leave it?", slots: [mm]}
  verb.MeasureClips:    {en: "measure the clips on"}
  verb.Seat:            {en: "press the pipe into the clips on"}
  prompt.understand:    {file: prompts/understand.en.txt}    # "You turn a pipe installation task into goals ..." in place of the household text
```

A pack with `extends: household` and its own `prompt.understand` no longer needs to bend its robot description around the household prompt
(fit, "Honest limits": 4 of 4 with the short phrase, 0 of 3 with the first).

## Answers

Today (inv, "Questions and answers"): 28 kinds of question; 16 carry an event `kind`, 12 carry none, one emits no event; the answer is read by
eight routes. A free-text answer that carries content for a skill has no route (fit G5): `yes_no` reads "Use the measured clip positions and
try once more." as a refusal (`robot_agent/interact/dialog.py:119-130`, read).

### Schema of one question

```yaml
questions:
  <name>:
    phrase: <phrase key>            # with its slots
    kind: <event kind> | null       # the `kind` of the `clarify` event; null = no kind (as 12 questions today)
    answer:
      type: yes_no | yes_no_correction | place | choice | instance | free_text | number
      choices: {<choice>: {words: {<language>: [...]} | lists: [<block.key of a language pack>], implies: yes | no}}
      extract: {<slot>: {type: number, unit: mm}}     # optional typed values taken from the answer text
      keep_text: true               # the raw answer is kept with the result (for a skill, a prompt, the record)
      unclear: reask | model | fail # what follows an answer no reader understands; `reask` uses `reasks` and the pack's `yes_or_no` phrase
      reasks: 1
    silence_retries: 1
```

The answer types are the routes that exist, given names (inv, "How answers are read"): `yes_no` = route 1 (`dialog.yes_no`),
`yes_no_correction` = route 2 (`read_yes_no`), `place` = route 3, `choice` = route 4 (word lists; the bt `ask` leaf already does this,
`bt_goal.py:171-173`, read), `instance` = route 5, `free_text` = route 6 (the text goes back to the understand step). `number` is new. Each is a
registered `answer` callable; a pack may register its own. Every reader returns one shape: `AnswerResult(kind, value, text, slots)`.

`unclear: model` is ONE call of the planner's model inside the run's budget, through the service that exists for options
(`ask_model`, `robot_agent/core/options/services.py:37`, read): it maps a free sentence onto one of the declared choices. A recovery policy and
an answer reader get the same service through `ctx`. That gives a pack an LLM channel that is counted and recorded by the engine, for the
decisions a pack makes at the engine's seams (fit G7). A skill still has none; that is not changed.

In a tree, `[ask, <question>]` succeeds when an answer was read. The result is kept as the fact `answered(<question>, <choice>)` and under
`answer.<question>.<slot>` for the `{...}` templates of later leaves, so a tree branches on the CONTENT of an answer with the `cond` leaf it
already has, and passes a number to a skill as a typed argument. No new node type is needed.

Event kinds. The standard pack declares the 12 kind-less questions with `kind: null`, so today's events are unchanged. A schema-2 pack may
set `questions_default_kind: name` to give every question its name as its kind. That is what a fleet manager needs to tell an objection from
a read-back (fit S3) and it is opt-in because it changes the event stream.

### Example: standard household pack (6 of the 28; the full list is the 28 rows of the inventory)

```yaml
questions:                              # source: inv "Questions and answers" A, rows 1, 9, 11, 12, 13, 15
  confirm_task: {phrase: q_confirm,   kind: confirm,   answer: {type: yes_no_correction, unclear: reask, reasks: 1}}
  coverage:     {phrase: q_coverage,  kind: coverage,  answer: {type: yes_no}}
  where_is:     {phrase: q_where_is,  kind: null,      answer: {type: place, keep_text: true}}
  help_pick:    {phrase: null,        kind: null,      answer: {type: yes_no, then: place}}     # the text is still built in step_ladder.py:63
  help_move:    {phrase: null,        kind: null,      answer: {type: yes_no}}                  # step_ladder.py:96
  which_one:    {phrase: q_which_one, kind: which_one, answer: {type: instance, unclear: model}}
```

### Example: PEX pack

```yaml
questions:
  pex_clip_far:
    source: "pex_case case text: a person decides when a clip is far off its plan"
    phrase: q_pex_use_measured
    kind: pex.clip_decision
    answer:
      type: choice
      choices:
        use_measured: {words: {en: ["use the measured", "measured positions", "go ahead"]}, implies: yes}
        remeasure:    {words: {en: ["measure again", "re-measure"]}}
        leave:        {words: {en: ["leave it", "stop"]}, implies: no}
      extract: {dz_mm: {type: number, unit: mm}}
      keep_text: true
      unclear: model
```

The answer "Use the measured clip positions and try once more." is then the choice `use_measured`, not a refusal.

## Constants, options and trees

Three small sections with no schema of their own beyond "name -> value with a label".

- `constants`: the per-robot numbers that are literals in the loop today (inv, "Per-robot or per-domain constants"). An entry is
  `{value, source | assumed}`. Precedence does not change: a run option, then the environment, then the robot's manifest or pack, then the
  standard pack (the sources of an option key today: `robot_agent/core/options/spec.py:17`, read).
- `options`: option -> keys, the content of `planner.options` today, which keeps working.
- `trees`: goal kind -> tree, the content of `planner.options.bt_goal.trees` today, which keeps working (the PEX example is in "Actions").

### Example: standard household pack

```yaml
constants:        # the code's defaults at 8002347 (lines from the inventory, not read again). Whether a value was tuned is not recorded,
                  # so each carries the code location as its source; `assumed` only where the code itself says "a priori".
  clarify_rounds:     {value: 3,   source: "core/planning/assist_loop.py:382"}
  max_replans:        {value: 6,   source: "core/planning/assist_loop.py:386"}
  edit_rounds:        {value: 3,   source: "core/planning/assist_loop.py:389"}
  edit_reserve:       {value: 6,   source: "core/planning/assist_loop.py:391"}
  understand_samples: {value: 2,   source: "core/planning/assist_loop.py:416"}
  health_wait_s:      {value: 0.5, source: "core/planning/assist_loop.py:436"}
  search_total:       {value: 8,   source: "core/planning/assist_loop.py:479"}
  person_seat_m:      {value: 1.0, assumed: true}      # "a priori, not tuned" (core/planning/persons.py:36)
  person_surface_m:   {value: 1.2, assumed: true}
  second_looks:       {value: 2,   source: "core/planning/assist_loop.py:3937"}
  same_failure_stop:  {value: 2,   source: "core/planning/assist_loop.py:3693"}
  llm_budget:         {value: 20,  source: "core/planning/budget.py:20"}
options:          # the household options that are ON for every robot today (target of Step 28; until then the option's own default)
  bt_goal:       {enabled: true}
  bring_ask:     {enabled: true}
  fetch_guard:   {enabled: true}
  look_then_ask: {enabled: true}
  hazard:        {rules: core}
trees: {}         # the standard pack declares no tree: a household goal is planned by the planner, as today
```

### Example: PEX pack

```yaml
constants:        # source: pex_robot/configs/manifest.yaml `planner` (today's values, moved)
  llm_budget:       {value: 22}
  translate_budget: {value: 8}
options:
  look_then_ask: {enabled: false}        # the pipe's place is declared; today's manifest already says this
  bt_goal:       {exhausted: ask_or_stop}
# trees: the `install` tree of section "Actions"
```

## Phase pipeline

### What the loop is today

`_run_blocking` (`robot_agent/core/planning/assist_loop.py:1662-1703`, read) opens the run and calls four phase generators in a row:
`_understand_phase`, `_plan_phase`, `_execute_phase`, `_final_check`. Each yields events and returns "the run ended" or not. The file has 218
functions, none over 80 lines (inv). `AssistLoop` already takes three mixins from other files (`:362`, read).

### The one phase interface

```python
Signal = "next" | "stop" | "done"            # the loop's own values today (_NEXT, _STOP, _DONE)

class Phase(Protocol):                       # the four stages and the two services all have this shape
    def __call__(self, r: RunContext, *args) -> Generator[dict, None, Signal]: ...

PIPELINE = ("understand", "certify", "execute", "verify")     # the stages, by the name of their entry generator on the loop
# services with the same shape, called from inside the stages:  recover(r, step, result, reason)   ask(r, question)
```

A stage is a generator: it yields the run's events and returns a signal. `_run_blocking` becomes a loop over `PIPELINE` that stops at the
first `stop`. Today the first three stage methods return a bool ("the run ended") and `_final_check` returns the `done` event itself
(`:1690-1696`, read); the pipeline entry of each stage is a thin wrapper that turns that into the signal, so the methods the tests call keep
their return values. The brief names six phases; four of them run in a row, and two (recover, answer) are called from inside the others
(recover from execute, answer from all). The design says so instead of drawing six boxes in a line.

How a phase module is built: as a MIXIN class of `AssistLoop` in its own file, with its entry generator and its private methods, the pattern
of `WhichOne`, `SearchHooks` and `StepLadder`. This is chosen for the proof, not for beauty: a method moved verbatim into a mixin keeps its
name, its `self`, its call sites and the method resolution, so the tests that call methods through the class (inv, "Cut candidates",
constraint 3) and the 28 places where a test builds the loop with `AssistLoop.__new__` (constraint 4) keep working, and a golden replay proves the
move. The run state stays one object `r`. A design where every phase is an object with explicit inputs would need all 189 methods rewritten;
it cannot be proved in small steps and is on the deferred list.

### Target module list and line budgets

All in `robot_agent/core/planning/` (limit of the lint for this layer: file 1200, function 80; the budgets below are the target, far under it).

| module | budget (lines) | what it holds (source block of the inventory's phase map) |
|---|---:|---|
| `assist_loop.py` | 350 | docstring, imports, the class shell, `PIPELINE`, `_run_blocking`, `_effect`, `_reconcile`, `_save_world` (they call `current` and `reconcile_world`, which `robot_agent/observe/replay.py:396-405` patches on THIS module), the re-exports the 92 test files import |
| `places.py` | 210 | `Places`, `Memory`, `words` (:107-301) |
| `loop_config.py` | 300 | `ASSIST_OPTIONS`, the config resolution of `__init__`, the role-model configs (:369-656) |
| `phases/understand_call.py` | 300 | the model call, capability check, schema building (:658-941); prompt and schema text are pack data |
| `phases/ground.py` | 200 | grounding on the ENV, per slot type (:944-1137) |
| `phases/understand.py` | 280 | the understand rounds, views, refusal (first third of :1787-2549) |
| `phases/agree.py` | 280 | agreement, read-back (second third) |
| `phases/correct.py` | 260 | corrections and patches (last third) |
| `phases/answer.py` | 250 | say and ask plumbing, the ask machinery, the read-back phrase dispatch (:1143-1268, :2550-2649) |
| `phases/gate.py` | 180 | the plan checker (:1271-1438) |
| `phases/certify.py` | 300 | generate and certify, the gate rounds (:2651-2941) |
| `phases/replan.py` | 100 | replan (:2943-3031) |
| `phases/goal_change.py` | 180 | goal change and the task stack (:3034-3203) |
| `phases/execute.py` | 290 | the execute loop, plan end, guard, one step (:3205-3343, :3467-3597) |
| `phases/exec_helpers.py` | 120 | bind, params, sensors (:1546-1659 without `_effect`) |
| `phases/verify.py` | 190 | post-check, goal observation, final check (:3345-3465, :3599-3630, :4066-4081) |
| `phases/recover.py` | 190 | success, failure, policy rung, waits, the policy selection (:3632-3806) |
| `find_ladder.py` | 270 | the Find ladder, verbatim (:3808-4063); named `std.find_search` by the standard pack |
| `phases/finish.py` | 130 | `_finish` and the failure notes (:1705-1784) |
| `robot_agent/core/pack/*.py` | 5 files, 150 to 300 each | `loader`, `registry`, `goal_semantics` (the dispatch by kind), `action_rules` (declared pre / effects), `answers` (the reader dispatch) |
| `robot_agent/contract/pack.py` | 300 | the schema dataclasses and their validation |
| `robot_agent/packs/household/callables/*.py` | 6 files, 300 or less each (the lint limit of this new layer: file 400, function 60) | `goals`, `predicates`, `verifiers`, `recover`, `answers`, `phrases` |

Function budget: 50 lines for new code; a function moved verbatim keeps its size (the longest in the file is 80, inv).

### What each existing option becomes

All 44 options stay registered options. The engine's 30 hooks and 25 services keep their names, their kinds and their call order
(`robot_agent/core/options/spec.py:22-53`, read; the hook call sites are listed in inv, "Hooks"). No hook is added. What changes is where an
option's household words come from.

| fate | options |
|---|---|
| unchanged, generic | `trace`, `goals_only`, `asr_guard`, `unapplied_change`, `translate_retry`, `dedupe_keep_end`, `entity_cover`, `shared_instances`, `replan_guard`, `repair_sync`, `name_recover`, `confirm_look`, `object_words` |
| becomes the generic tree executor (conditions from the predicate table, typed `act` arguments, questions from the pack, public node events) | `bt_goal` |
| stays an option; its goal-kind literals come from the pack (a kind's slots and class instead of `("on", "in")` in code) | `answer_align`, `goal_ledger`, `goal_words`, `heard_check`, `bring_ask`, `recipient_keep`, `look_then_ask`, `ask_source`, `reach_delivery`, `cond_goal`, `cond_extract`, `hazard`, `heat_safety` |
| stays an option; its action literals come from the action classes | `fetch_guard`, `drop_search` |
| stays an option; already reads pack data or roles (no change beyond accepting the data from `pack.options`) | `precond_repair`, `plan_complete`, `plan_release`, `place_find`, `gate_prune`, `reach_check`, `end_state`, `reopen_on_miss`, `lang_words` |
| stays an option AND is offered as a named recovery policy or a parameter of one | `ask_next` (the menu after a failed step), `hw_wait` (the wait schedule), `search_budget`, `search_per_object` (limits of `std.find_search`) |
| stays an option; reads the question schema | `ask_agree`, `ask_focus` |

Five options are ON by default (`bt_goal`, `bring_ask`, `fetch_guard`, `look_then_ask`, `hazard` with `rules=core`; inv, "Option map"). Four
of them act for every robot, so they are household behaviour. The target is that the standard pack switches them on (`options:` section) and
the option's own default is off; a pack that does not extend the household pack then starts without them. This is the last step and can be
deferred: PEX already switches `look_then_ask` off in its manifest.

### Hooks kept

All 30. A test freezes the table (`HOOKS` names, kinds, flow flags) and one golden with option `trace` freezes the order in which a run
reaches them (Step 1).

## Standard pack

The standard pack is today's household behaviour, taken out of the core and given names. Nothing in it is new behaviour.

What it contains: the eight goal kinds, the ten predicate names, nine actions, three verifiers and the end-of-run rule, five recovery
policies and the action table, the 28 questions, the household phrases and the understand prompt, the constants, and the option defaults.
Its callables are the bodies of today's functions.

How it reproduces today's behaviour exactly, and how that is tested. Four layers of proof, all required for every step:

1. EQUALITY of data. Before a constant is deleted, a test loads the standard pack and compares it with the constant:
   `needs` of every kind with `GOAL_KINDS`, the tree-scope predicate names with `CONDITIONS`, the union of the core and household phrases
   with `CORE_PHRASES`, the `by_action` table with the six names of the loop's dict, `STEP_LADDER`, the place-argument actions with
   `_LOC_ACTIONS`, the prompt file with `UNDERSTAND_PROMPT` (byte for byte). The test is committed in the step BEFORE the constant goes.
2. DIFFERENTIAL tests of moved functions. A copy of the old function is kept in the test tree (`tests/legacy_ref/`, never imported by the
   package) and compared with the new dispatch over a grid of inputs: every kind, with and without each slot, over a set of worlds and step
   lists (`_reaches`, `_goals_hold`, `_missing`, `_goal_phrase`, `holds`, `_holds`, `missing_actions`, `kinds_for`, `compile_steps`).
3. GOLDEN replay. All cassettes replay with no difference: the model calls (prompt hashes), the planner calls, the robot calls, the events,
   the final state. The mutation check gets at least one mutation per moved table. The older goldens (`tests/golden/default_path.json`,
   `tests/golden/safety_household_r17.json`) are never regenerated.
4. RESOLVED-PACK comparison. For a pack given by its path, the vocabulary the OLD code computes (`kinds_for`, `plan_actions`, the needs,
   the recovery table, the usable trees, the phrase of every key in every declared language; computed with the reference copies of proof
   layer 2) is compared with what the loader resolves. In the test suite it runs on the made-up packs (`tests/fakebot`, `tests/fakehome`,
   `tests/fakeline`). For the four real packs (kcare_robot, symbolic_robot home and factory, pex_robot) it runs as a tool,
   `tools/pack_compat_check.py <package path>`, at the integration stage of every step. The real manifests are NOT copied into this
   repository: it is public, and the tests may not depend on another repository.

The standard pack is also the test of the schema: if a household rule cannot be written in the schema without a callable, the schema is
not made bigger for it; the callable stays (R2).

## Fleet needs

Today (inv, "Fleet tables"): `_NEEDS` (`robot_fleet/scheduler.py:16-23`, read) repeats the robot's needs by hand and already differs from them
(no `deliver`, no `give`; `say` needs only Say); `_CONTAINER_KINDS` (`robot_fleet/graph.py:26`, read) says whose object is also a place; a kind
with no entry can never be assigned (`scheduler.py:41-43`). The robot reports `plan_actions`, `capabilities`, `places`, `site`
(`robot_agent/api/robot_info.py:17-21`, read).

### What the robot reports (additive fields of `GET /robot/manifest`)

```json
{"robot": "pex_robot", "version": "1.0", "plan_actions": ["Crimp", "Find", "..."], "capabilities": {}, "places": ["rack", "wall_north"], "site": "north",
 "pack": {"schema": 2, "name": "pex", "version": "1.0", "extends": "household"},
 "goal_kinds": {
   "install": {"feasible": true, "needs": [["MeasureClips", "Pick", "Seat", "Crimp", "Place"]], "needs_when": [],
               "slots": {"object": "object", "to": "place"},
               "footprint": {"objects": ["object"], "places": ["to"]}, "carries": "holding",
               "phrase": {"en": "install the {object} on the {place}"}}},
 "question_kinds": ["confirm", "pex.clip_decision"]}
```

- `goal_kinds` lists every kind of the robot's pack. `feasible` is the robot's own answer (`missing_actions` is empty for its actions), so the
  manager does not repeat the rule. `needs` is a list of alternatives, the shape of `_NEEDS`, for a manager that wants to explain a refusal.
- `footprint` says which slots are objects and which are places (the household `open` / `close` list the `object` slot under `places`).
- `carries` names the belief field that ties two goals to one robot (the single hand, today read as `world.holding`).
- `question_kinds` are the kinds the robot's questions carry, so a manager can tell a read-back (`confirm`) from any other question without
  the guess at `robot_fleet/orchestrator.py:242` (inv).
- `phrase` per kind lets the manager's voice word a kind it has no text for. This one is on the deferred list: the fleet's voice packs are
  already open data with a `goal.default`.

The pack's `fleet:` section holds only what cannot be derived: `fleet: {kinds: {install: {footprint: {...}, carries: holding}}}`. `needs` and
`slots` come from "Goal kinds".

Example of the standard pack: `fleet: {kinds: {open: {footprint: {objects: [object], places: [object]}}, close: {footprint: {objects: [object],
places: [object]}}}, carries: holding}`; every other household kind takes the default footprint (objects = the object slot, places = `from`
and `to`), which is the rule of `graph.py:35-49`.

### What the manager does (robot_fleet, its own worktree, its own tests)

`RobotInfo` gains `goal_kinds` (default empty) and `question_kinds`. `feasible_robots`: a robot that reports `goal_kinds` is feasible for a goal
when the kind is there with `feasible: true` and every place of the footprint maps to the robot's own key; a robot that reports none uses
the table, which moves from code to `robot_fleet/compat/needs_v1.json` with a label ("robot_agent before the pack schema"). `footprint` reads
the reported slots, with today's rule as the default. Result: no goal-kind table in the manager's code, and a kind the manager never heard
of (`install`) can be assigned.

One difference is intended and must be said in the fleet's changelog: for a robot that reports goal kinds, `deliver` and `give` become
assignable and a `say` goal with a place needs MoveTo, because the robot's rule replaces the manager's copy.

## Compatibility

| case | what happens |
|---|---|
| a manifest with no `pack:` (all of today's packs) | schema 1. The loader takes the base named by `schema_1_base` in the index (`household`) and LIFTS what the pack declares today into the same structure: `planner.options.<option>` -> `options`, `planner.options.bt_goal.trees` and `menu` -> `trees`, `planner.action_roles` -> roles, the adapter's `recovery_overrides` -> `recovery.codes`, the adapter's i18n phrases stay first in the phrase lookup, `planner.recovery.*`, `planner.end_state`, `limits`, `geometry`. Nothing is written to disk. Lifted data keeps today's rule: unlabelled = not used, an unknown name = the feature does nothing |
| kcare_robot | `planner: {seed_memory}` only: the standard pack as it is; `is_openable`, the Korean phrases and verbs come from its adapter as today |
| symbolic_robot home / factory | the standard pack; `Handover` and `Dock` are recognised from their effects as today; `Look` (a custom action) and `Wait` get no ladder, as today |
| pex_robot (today's manifest) | the standard pack; its `on` tree under `planner.options.bt_goal` and `look_then_ask.enabled: false` are lifted; its five custom actions behave as today. Round C then gives it a `pack:` section |
| a schema-2 pack on an OLD core | `pack` is an unknown top-level key: kept in `Manifest.extra`, a warning of `check_manifest`. The robot runs as a household robot; its new kinds do not exist. The manifest answer has no `pack` field, so a client can see it |
| a pack with `schema` higher than the core supports | a load error that names the supported schemas |
| `GET /robot/manifest` | new fields only; the response model is open (`robot_agent/api/models.py:58-65`, read); the dashboard and today's fleet ignore them |
| Python names other code imports | kept as views of the standard pack: `goals.GOAL_KINDS`, `MANIPULATION`, `TEMPLATE_KINDS`, `OPTIONAL_KINDS`, `bt_goal.CONDITIONS`, `speech.CORE_PHRASES`, `assist_loop.Places`, `Memory`, `words`, `ASSIST_OPTIONS`, `_NEXT`, `_STOP`, `_DONE`. Functions that took no pack (`missing_actions(goal, actions)`) gain an optional last argument and use the schema-1 base when it is absent |

Deprecation path (owner's decision 4: no end-of-support date). Schema 1 stays supported. `check_manifest` prints one information line for a
schema-1 pack ("schema 1: the standard pack `household` is used"), never a warning. A tool `python -m robot_agent.core.pack.show <pkg>` prints
the `pack:` section that is equivalent to a schema-1 manifest; it writes nothing. The legacy spellings (`planner.options.bt_goal.trees`,
`planner.action_roles`, `recovery_overrides`) are tested for as long as schema 1 is.

## Invariants

What must not change at any step. Each is checked by a test or by the step's report.

1. The cassettes of `tests/golden/cassettes/` replay with no difference; a cassette is only added, by the explicit recorder, on a commit
   BEFORE the step it protects. `tests/golden/default_path.json` and `tests/golden/safety_household_r17.json` are never regenerated. No test is
   edited to pass.
2. `wc -l robot_agent/core/planning/assist_loop.py` never rises from one commit to the next. Every step lowers the size entry of the file in
   `tools/arch_baseline.json` to the new value (a ratchet). `UnifiedAgent.run`, `create_app` and `agent_ws` keep their size baselines.
3. The full suite stays at 2916 passed or more; `tools/lint_arch.py` shows 0 new findings.
4. The default path emits no new event, event field or question kind, makes no new model call and sends byte-identical prompts.
5. No `eval` / `exec`; a pack names registered callables only; a pack registers only in its own namespace.
6. The engine never imports `robot_agent.packs`; the engine and the options name no robot, no site, no benchmark.
7. One source of action rules: only `core/planning/action_model.py` reads rules, the standard ones from embodiplan.
8. A pack that declares nothing new gets nothing new evaluated (R3): no `pre` of a schema-1 skill is checked, no option role is filled in,
   no question gets a kind.
9. The 30 hooks and 25 services keep name, kind and call order.
10. The names that tests and other modules import from `assist_loop` stay importable; methods called through the class stay class
    attributes; the word `refresh_options` does not appear in `assist_loop.py` (`tests/test_option_refresh.py:50-52`, inv); code that calls
    `current` or `reconcile_world` stays in the module that `replay.py` patches.
11. The order of the goal kinds in the understand prompt and schema does not change for a schema-1 pack.
12. The real robot's checkout and every running service are never touched; nothing is pushed; results stay on `generalize-core`.

## Migration steps

Streams (owner's decision 8: at most four code streams in a wave). LOOP owns `assist_loop.py`, `core/planning/phases/`, `places.py`,
`loop_config.py`, `find_ladder.py`: every step that touches `assist_loop.py` is in this stream and these steps are SEQUENTIAL among
themselves. PACK owns `contract/pack.py`, `core/pack/`, `robot_agent/packs/`. OPT owns `robot_agent/options/` and `core/planning/verify.py`.
GOLD owns `tests/golden/` and `tools/golden_*`. FLEET owns `api/robot_info.py`, `api/models.py` and the robot_fleet worktree. A step of one
stream may run in parallel with steps of the other streams when its "needs" are merged.

Every step ends with the same report (the skill's equivalence report): the golden replay (no difference), the mutation check, the full
suite, the lint, `wc -l` of `assist_loop.py` before and after, what moved where. Any difference in a replay stops the run (owner's decision 8).

Sizes are estimates from the inventory's phase map (spans; about 10 percent uncertain). The rule is the sign, not the number.

| step | stream | `assist_loop.py` after (lines, estimate) |
|---:|---|---:|
| start | | 4081 |
| 1, 2 | GOLD, PACK | 4081 |
| 3 | LOOP | 3890 |
| 5 | LOOP | 3796 |
| 7 | LOOP | 3790 |
| 8 | LOOP | 3750 |
| 9 | LOOP | 3562 |
| 10 | LOOP | 3560 |
| 11 | LOOP | 3396 |
| 12 | LOOP | 3231 |
| 13 | LOOP | 2977 |
| 14 | LOOP | 2807 |
| 15 | LOOP | 2631 |
| 16 | LOOP | 2301 |
| 17, 18, 19 | LOOP | 2041, 1781, 1541 |
| 20 | LOOP | 1254 |
| 21 | LOOP | 999 |
| 22 | LOOP | 733 |
| 23 | LOOP | 558 |
| 24 | LOOP | about 300 |
| 4, 6, 25, 26, 27, 28 | PACK, OPT, FLEET | unchanged |

### Step 1: extend the goldens (no production code)

- Files: `tests/golden/scenarios.py` (new entries only), `tests/golden/cassettes/*.cassette.json` (new files only), `tools/golden_mutation_check.py`
  (new mutations), a made-up test pack `tests/fakeline/` (a robot that is NOT a household robot: Say, WaitForUnload and two custom actions).
- Golden tags that cover it: the step IS the coverage. The 21 cassettes use `fakebot` in three made-up flats and cover: success, failure,
  question, stop, goals_only, bt_goal, replan, plan_only, gate, refused, default_options.
- To ADD (recorded on the code as it is now, before any other step): `template` (go, say and deliver goals), `close_goal`, `handover`
  (`fakehome`, a release action), `drop_refind` (a dropped object with a `floor` place), `move_help` (the MoveTo help question outside a tree),
  `search_legs` (a Find miss, the search, the "where is it" question, a second look), `goal_absent` (the end check does not see the object),
  `unclear_answer` (a yes / no answer asked again; silence), `two_destinations` (the validator's question), `goal_change` (a correction during a
  planner run), `non_english` (one Korean run: phrases, the translator calls), `hazard_default` (the default-on safety refusal), `hooks_order`
  (option `trace`: the order of the hooks), `custom_action` (`fakeline`: a custom action fails and goes to the planner; a tree with an unknown
  condition falls to the planner), `given_goals_unknown_kind`, `readback_kinds` (a read-back of an `in`, an `open` and a `say` goal). One new
  mutation for each table that later moves: a need of a kind, a tree condition, an effect predicate, a ladder choice, a phrase, the
  place-argument set, the end check, the prompt text.
- Effect on `assist_loop.py`: 0 lines.
- Default data: none.
- Risk: low for the product, real for the schedule: a scenario may show that the fakes cannot play a path (then the path is listed as not
  covered and its step waits). A cassette recorded after a production step would characterise the new code, not the old: the order matters.
- Parallel: GOLD stream; in parallel with Steps 2 and 3. Every later step that names an added tag needs this step merged.

### Step 2: the pack schema, the loader, the registry, the empty packs

- Files: `robot_agent/contract/pack.py` (new), `robot_agent/core/pack/{__init__,loader,registry}.py` (new), `robot_agent/packs/index.yaml`,
  `robot_agent/packs/core/pack.yaml`, `robot_agent/packs/household/pack.yaml` (headers, empty sections), `robot_agent/contract/manifest.py` (`pack` in
  `_TOP_KEYS`, a `pack` field), `tools/arch_rules.json` (the `packs` layer), `pyproject.toml` (package data), `tests/test_pack_schema.py`,
  `tests/test_pack_loader.py`, `tools/pack_compat_check.py` (new: proof layer 4 for a pack given by its path).
- Golden tags: all 21 (nothing reads the pack yet: the replay must be identical, and it shows the loader has no side effect at import).
- To ADD: nothing to the cassettes; the resolved-pack comparison (proof layer 4) as a test for the made-up packs.
- Effect on `assist_loop.py`: 0 lines.
- Default data: the schema-1 lift of the two made-up packs (in the suite) and of the four real packs (by the tool) equals what the old code
  computes.
- Risk: medium. `manifest.py` is 702 lines and gets a field; a manifest with a `pack` key was "unknown key, warning" and is now parsed. The
  layer rule must not add a lint finding, and `tests/test_architecture.py` (not read) may list the layers.
- Parallel: PACK stream; in parallel with Steps 1 and 3. Every data step needs it.

### Step 3: `Places`, `Memory`, `words` move to `places.py`

- Files: `robot_agent/core/planning/places.py` (new), `assist_loop.py` (the block :107-301 becomes one import line that re-exports the names).
- Golden tags: all (every run builds `Places`); `question` and `goals_only` exercise the grounding of names.
- To ADD: nothing.
- Effect on `assist_loop.py`: about -191 lines (4081 to about 3890).
- Default data: none (a verbatim move; the English filler words and the `name@room` rule move with it and become data in Step 28).
- Risk: low. An import cycle (`speech`, `env_names` are imported inside methods today and stay so); 69 test files import these names from
  `assist_loop` and `search_hooks.py:20` does too: the re-export line is the guard.
- Parallel: LOOP stream, first; in parallel with Steps 1 and 2.

### Step 4: the phrase tables become pack data

- Files: `robot_agent/packs/core/pack.yaml`, `robot_agent/packs/household/pack.yaml` (the `phrases` sections), `robot_agent/core/planning/speech.py`
  (`CORE_PHRASES` becomes a view; `phrase()` reads the chain last), `robot_agent/core/planning/robot.py` (`CORE_TEXT`),
  `robot_agent/core/planning/budget.py` (`BUDGET_TEXT`), `tests/test_pack_phrases.py`.
- Golden tags: `question` (5 cassettes), `success` (11: every `say`), `failure`, `refused`, `stop`; added: `non_english`, `readback_kinds`.
- To ADD: the equality test (union of the two files = `CORE_PHRASES`, no key twice; `CORE_TEXT`; `HELP_PICK`, `HELP_MOVE`).
- Effect on `assist_loop.py`: 0 lines.
- Default data: the 62 + 8 + 3 English texts, character for character.
- Risk: medium. YAML quoting (braces, quotes, a trailing space) changes a character; the equality test catches it. The lookup ORDER must put
  the pack chain after the language pack's `speech` block.
- Parallel: PACK stream; in parallel with Steps 5 and 6. Needs Steps 1, 2.

### Step 5: the goal-reach rules are chosen by kind

- Files: `robot_agent/core/pack/goal_semantics.py` (new: the dispatch), `robot_agent/packs/household/callables/goals.py` (new: `std.on_reached`,
  `std.in_holds`, ... with the bodies of the if-chains), the `goal_kinds` section (only `reached`, `holds`, `missing`), `assist_loop.py`
  (`_releases`, `_simulate_goals`, `_on_reached`, `_missing`, `_reaches`, `_goals_hold` become class attributes that delegate),
  `tests/legacy_ref/goal_reach.py`, `tests/test_goal_semantics_diff.py`.
- Golden tags: `success`, `replan`, `gate`, `bt_goal`, `failure`, `plan_only`; added: `template`, `close_goal`, `handover`.
- To ADD: the differential test over the grid (8 kinds x slots x worlds x step lists); a mutation "an `on` goal counts as reached without a
  Place".
- Effect on `assist_loop.py`: about -94 lines (to about 3796).
- Default data: one entry per kind naming the three callables; an unknown kind keeps today's result (not reached, no text).
- Risk: medium. 15 methods call into this block (inv); `__init__` wraps `_reaches` (`:448`) and tests call `_reaches` through the class;
  `_check` sets `self._env_places` as a side effect that `_on_reached` reads.
- Parallel: LOOP stream, after Step 3; in parallel with Steps 4 and 6. Needs Steps 1, 2.

### Step 6: one predicate table for trees and effects

- Files: `robot_agent/packs/household/callables/predicates.py` (new), the `predicates` section, `robot_agent/options/bt_goal.py` (`holds` and
  `CONDITIONS` delegate), `robot_agent/core/planning/verify.py` (`_holds` looks the name up), `tests/test_pack_predicates.py`.
- Golden tags: `bt_goal` (6 cassettes: every `cond` leaf), `success` and `replan` (the effect check after every step); added: `custom_action`.
- To ADD: equality (tree scope = `CONDITIONS`; effect scope = the six names of `_holds`); a differential test of both evaluators over worlds
  and argument lists, wrong arity and the wildcard included; mutations "placed ignores the hand" and "an unknown effect predicate fails".
- Effect on `assist_loop.py`: 0 lines.
- Default data: the ten entries of "Predicates".
- Risk: medium. The two old evaluators differ on a missing argument and on "cannot tell"; the consumers keep their own rule, and the
  differential test is the proof. `bt_goal.py` is 367 lines with a limit of 400.
- Parallel: OPT stream; in parallel with Steps 4 and 5. Needs Steps 1, 2.

### Step 7: the goal-kind table is pack data

- Files: the `goal_kinds` section (needs, `needs_when`, `understood`, `canonical`, `prompt`, `compile`, `conflicts`),
  `robot_agent/core/planning/goals.py` (the tables become views; `missing_actions`, `kinds_for`, `prompt_lines`, `canonical`, `is_template`,
  `compile_steps`, `reached`, `conflicts` read the kind), `robot_agent/packs/household/callables/goals.py`, `assist_loop.py` (the enum literal of
  `UNDERSTAND_SCHEMA` and `_cannot_do` read the pack), `tests/test_pack_goal_kinds.py`.
- Golden tags: every cassette with an understand call (the prompt hash holds the kind lines and their order), `goals_only`, `refused`,
  `plan_only`; added: `template`, `given_goals_unknown_kind`, `two_destinations`.
- To ADD: equality with `GOAL_KINDS`, `OPTIONAL_KINDS`, `MANIPULATION`, `TEMPLATE_KINDS`; differential tests of `missing_actions`, `kinds_for`,
  `compile_steps`, `reached`, `conflicts`; a mutation "the `in` kind needs no Pick".
- Effect on `assist_loop.py`: about -6 lines (to about 3790).
- Default data: the eight entries of "Goal kinds".
- Risk: high for the prompt (the order and the text of the kind lines are in every understand prompt) and for the 23 kind comparisons in
  `goals.py`. `goal_fix.py` (24 comparisons) and `plan_repair.py` (17) are NOT touched in this step: they keep the literals until Step 28.
- Parallel: PACK and LOOP together (one LOOP commit after Step 5). Needs Steps 1, 2, 5.

### Step 8: the read-back phrase is chosen by kind

- Files: `robot_agent/packs/household/callables/phrases.py` (new: `std.on_phrase`, ...), `assist_loop.py` (`summary`, `_goal_phrase` delegate; the
  static names stay), `tests/legacy_ref/goal_phrase.py`, `tests/test_goal_phrase_diff.py`.
- Golden tags: `question` (the read-back cassettes); added: `readback_kinds`, `handover`, `non_english`.
- To ADD: the differential test (kind x person or place destination x `hand`, `near_person`, `from`, `said`).
- Effect on `assist_loop.py`: about -40 lines (to about 3750).
- Default data: `phrase` of each kind.
- Risk: low to medium. Nine callers (inv); the phrase is a `LocalText` with a key, slots and an English text: all three must be equal.
- Parallel: LOOP stream, after Step 7. Needs Step 4.

### Step 9: grounding moves to `phases/ground.py`

- Files: `robot_agent/core/planning/phases/__init__.py`, `phases/ground.py` (new mixin: `_ground_container`, `_ground_template`, `_ground_goals`,
  `_ground_speaker`, `_person_destination`, `_say_name`), `assist_loop.py`.
- Golden tags: `question` (`which_table_question`), `goals_only` (given goals are grounded), `success`; added: `template`, `handover`.
- To ADD: nothing new beyond Step 1.
- Effect on `assist_loop.py`: about -188 lines (to about 3562).
- Default data: none in this step (a verbatim move). The per-kind branches of grounding become `ground:` entries later, only if a pack
  needs a new slot type (deferred).
- Risk: medium. `_ground_template` is called through the class by a test; the hook `on_ground` and the service `person_where` are called
  here and must stay at the same point.
- Parallel: LOOP stream, sequential.

### Step 10: the action table (class, argument kind, words)

- Files: the `actions` section, `robot_agent/core/pack/actions.py` (new: `is_place_arg`, `class_of`), `assist_loop.py` (`_LOC_ACTIONS` reads it),
  `robot_agent/core/planning/reconcile.py`, `mapper.py`, `search.py`, `robot_agent/contract/legacy_adapter.py` (their copies), `robot.py`
  (`_ABILITY`, `NOT_PLAN_STEPS`), `robot_prompt.py` (`ORDER`, `ACTION_TEXT`), `announcer.py` (`VERBS`), `plan_repair.py` (`RELEASES`).
- Golden tags: all (the progress line of every step uses a verb; the understand and planner prompts use the action words).
- To ADD: equality tests for each of the eight tables. NOTE: the five "place argument" sets are not equal today (the legacy adapter's has
  three names, inv D6). Each call site keeps ITS set: the pack declares the union and each site names its subset by class. No set is unified
  in this step.
- Effect on `assist_loop.py`: about -2 lines (to about 3560).
- Default data: the nine entries of "Actions".
- Risk: medium. Many small files; `legacy_adapter.py` is contract layer and must not import the engine (it gets the set as an argument).
- Parallel: PACK stream for the data and the non-loop files; one LOOP commit. Needs Step 2.

### Step 11: the plan checker moves to `phases/gate.py`; its sentences get keys

- Files: `phases/gate.py` (new mixin: `_ground_step`, `_check`, `_check_step`, `_word_step`, `_word_pick`, `_word_place`, `_repair_plan`,
  `_repair_scope`), the `finding.<code>` phrases, `assist_loop.py`.
- Golden tags: `gate` (`gate_symbolic_repair`), `replan`, `success` (round 0 of the gate runs on every plan).
- To ADD: a differential test of the 20 sentences (violation code x action x object) against the old strings; one cassette `gate_findings`
  where the planner sees three different findings.
- Effect on `assist_loop.py`: about -164 lines (to about 3396).
- Default data: the 20 English sentences; they go to the planner, so they are part of prompt hashes.
- Risk: medium. `_check` sets `self._env_places` (kept); tests call `_check` through the class; a mutation exists already
  (`_gate_sees_nothing`).
- Parallel: LOOP stream, sequential. Needs Steps 4, 5.

### Step 12: verifiers: the post-check and the end-of-run observation

- Files: `phases/verify.py` (new mixin: `_post_check`, `_observe_goals`, `_end_action`, `_return_tries`, `_return_to_end`, `_observe_goal`,
  `_look`, `_final_check`), `robot_agent/packs/household/callables/verifiers.py` (new), the `verifiers` section and `observe` of the kinds,
  `assist_loop.py`.
- Golden tags: `success` (the `goal_check` events), `failure` (`dropped_on_the_way`), `bt_goal`; added: `goal_absent`, `handover`,
  `template`, `close_goal`.
- To ADD: a mutation "the end check trusts the belief"; a unit test that a kind with `observe: null` gives the sentence of today
  ("no sensor declared for it (belief)").
- Effect on `assist_loop.py`: about -165 lines (to about 3231).
- Default data: `observe: std.find_at_destination` for `on` and `in`, null for the others; the two step verifiers on `Find`, `Place`, `PutIn`
  and the release actions.
- Risk: high. This is where "done" is decided. `_observe_goal` is called through the class by a test; hooks `on_goal_check`,
  `on_second_look`, `on_final`, `on_goals_met` must stay at the same points.
- Parallel: LOOP stream, sequential. Needs Steps 1, 2, 5.

### Step 13: the Find ladder moves verbatim to `find_ladder.py`

- Files: `robot_agent/core/planning/find_ladder.py` (new mixin: `_recover_find` to `_refuted`), `assist_loop.py`.
- Golden tags: `replan` (`find_miss_replan`), `bt_goal` (`bt_goal_absent_sage_replan`, `bt_goal_stop_word`); added: `search_legs`.
- To ADD: nothing beyond Step 1 (if `search_legs` could not be recorded, this step waits).
- Effect on `assist_loop.py`: about -254 lines (to about 2977).
- Default data: none (a verbatim move).
- Risk: medium. Eight outward calls and eleven attributes (inv); the constant `SECOND_LOOKS` moves with it.
- Parallel: LOOP stream, sequential.

### Step 14: recovery policies are selected by name

- Files: `phases/recover.py` (new mixin: `_step_succeeded`, `_step_failed`, `_reset_container`, `_apply_policy`, `_wait_on_robot`,
  `_wait_health`), `robot_agent/core/pack/recovery.py` (new: the selection order), `robot_agent/packs/household/callables/recover.py` (new: five
  names bound to the five methods), the `recovery` section, `robot_agent/core/planning/step_ladder.py` (`STEP_LADDER` read from the pack),
  `assist_loop.py`.
- Golden tags: `replan` (`pick_fails_twice_replan`), `failure` (`skill_bug_failure`, `bt_goal_hw_fault`), `bt_goal` (`bt_goal_blocked_move`,
  `bt_goal_grasp_help`), `question`; added: `move_help`, `drop_refind`, `custom_action`.
- To ADD: equality (the `by_action` table = the six names of the dict; `STEP_LADDER`); a mutation "a failed Pick goes straight to the
  planner".
- Effect on `assist_loop.py`: about -170 lines (to about 2807).
- Default data: the `recovery` section of "Recovery"; `by_kind` empty; an action not listed -> `planner`, as today.
- Risk: high. The order of the ten steps of `_step_failed` must not change; a mutation exists for the retry count (`_one_retry_fewer`).
- Parallel: LOOP stream, sequential. Needs Steps 2, 13.

### Step 15: questions and answers

- Files: `phases/answer.py` (new mixin: `language`, `_announce`, `_in_lang`, `_ask`, `_ask_io`, `_ask_heard`, `yes_no`, `_ask_yes_no`,
  `_ask_clarify`, `_asks_where`, `_essential`, `_fresh_question`, `_restates`, `_obs`, `_ground_all`), `robot_agent/core/pack/answers.py` (new:
  the reader table), `robot_agent/packs/core/callables/answers.py` (new: the readers that exist, by name), the `questions` section,
  `assist_loop.py`.
- Golden tags: `question` (5), `stop` (`bt_goal_stop_word`); added: `unclear_answer`, `two_destinations`, `non_english`.
- To ADD: a test that every question of the standard pack with `kind: null` emits a `clarify` with no kind, and the others their kind
  (the 28 rows); the existing mutation `_yes_means_no` covers the reader.
- Effect on `assist_loop.py`: about -176 lines (to about 2631).
- Default data: the 28 question entries; the readers are today's functions.
- Risk: medium. `_fresh_question` and `yes_no` are called through the class by tests; `_asks_where` and `_essential` name two phrase keys.
  In this step no question gets a new kind and no inline English sentence gets a key.
- Parallel: LOOP stream, sequential. Needs Step 4.

### Step 16: the understand call; the prompt and the schema become pack data

- Files: `phases/understand_call.py` (new mixin: `_understand` to `_cannot_do`), `robot_agent/packs/household/prompts/understand.en.txt`, the
  `prompt.understand` phrase, the schema's goal part built from the kinds' slots, `_CANNOT` / `_CAN_OPEN` as phrases, `assist_loop.py`.
- Golden tags: every cassette with an understand call (20 of the 21, counted from the cassettes: the prompt hash); `refused`; `goals_only`.
- To ADD: equality of the prompt file with `UNDERSTAND_PROMPT` and of the built schema with `UNDERSTAND_SCHEMA` (as JSON); a mutation "one
  word of the prompt changed" (must be caught by a prompt hash).
- Effect on `assist_loop.py`: about -330 lines (to about 2301).
- Default data: the prompt text and the schema, byte for byte. The `str.replace` prompt flags (`:807-820`, inv) keep working on the same text.
- Risk: high. One changed character changes every understand prompt; the hashes catch it. Hooks `on_understand_prompt`,
  `on_understand_schema` stay at the same point.
- Parallel: LOOP stream, sequential. Needs Steps 4, 7.

### Step 17: the understand rounds move to `phases/understand.py`

- Files: `phases/understand.py` (new mixin: `_understand_phase`, `_take_given_goals`, `_understand_round`, the views and refusal methods; about
  the first third of :1787-2549), `assist_loop.py`.
- Golden tags: all cassettes with an understand call; `refused`; `goals_only`; `question`.
- To ADD: nothing beyond Step 1 (`given_goals_unknown_kind`, `two_destinations`).
- Effect on `assist_loop.py`: about -260 lines (to about 2041).
- Default data: none (a verbatim move).
- Risk: medium. The largest block of the file (32 methods, 16 outward calls); it is cut in three by method groups, not by line count.
- Parallel: LOOP stream, sequential.

### Step 18: agreement and read-back move to `phases/agree.py`

- Files: `phases/agree.py` (new mixin: the agree methods, `_goal_readback`, `_read_back`), `assist_loop.py`.
- Golden tags: `question` (`readback_no_correction`, `which_table_question`); added: `readback_kinds`, `non_english`.
- To ADD: nothing beyond Step 1.
- Effect on `assist_loop.py`: about -260 lines (to about 1781).
- Default data: none (a verbatim move).
- Risk: medium. Hook `on_issues` and the services `confirm_limit`, `unrefused` are called here.
- Parallel: LOOP stream, sequential.

### Step 19: corrections move to `phases/correct.py`

- Files: `phases/correct.py` (new mixin: the correction and patch methods, the last third of :1787-2549), `assist_loop.py`.
- Golden tags: `question` (`readback_no_correction`); added: `goal_change`, `two_destinations`.
- To ADD: one cassette `readback_correction` (a "no, the other table" answer that patches the goals) if Step 1 did not record it.
- Effect on `assist_loop.py`: about -240 lines (to about 1541).
- Default data: none (a verbatim move).
- Risk: medium. The correction path is thin in the goldens today (one "no" answer); the added cassette is required before this step.
- Parallel: LOOP stream, sequential.

### Step 20: generate-and-certify moves to `phases/certify.py`

- Files: `phases/certify.py` (new mixin: `_plan_phase`, `_generate_and_certify`, `_plan_call`, `_subgoals`, the gate repairs, `_tidy`,
  `_blocking`, `_hard_findings`), `assist_loop.py`.
- Golden tags: `success`, `gate`, `replan`, `plan_only`, `bt_goal` (the service `runs_goals` is asked here).
- To ADD: `gate_findings` (Step 11) covers gate rounds 1 and 2; a mutation "an uncertified plan runs".
- Effect on `assist_loop.py`: about -287 lines (to about 1254).
- Default data: none (a verbatim move).
- Risk: medium. Hooks `on_plan`, `on_plan_findings`; the planner calls are in the cassettes (`planner_calls`).
- Parallel: LOOP stream, sequential.

### Step 21: replan, goal change and the task stack move out

- Files: `phases/replan.py`, `phases/goal_change.py` (new mixins), `assist_loop.py`.
- Golden tags: `replan` (4 cassettes), `bt_goal` (`bt_goal_goal_change`); added: `goal_change`.
- To ADD: a cassette `new_task_first` (a new command during a run, answered "first") for the task stack, which no cassette reaches today.
- Effect on `assist_loop.py`: about -255 lines (to about 999).
- Default data: none (a verbatim move).
- Risk: medium. The task stack is not covered today; without the added cassette this step does not start.
- Parallel: LOOP stream, sequential.

### Step 22: the execute stage moves to `phases/execute.py`

- Files: `phases/execute.py` (new mixin: `_execute_phase`, `_refind_after_pause`, `_plan_ended`, `_coverage_gap`, `_look_at_untouched`,
  `_precondition`, `_guard_arrived`, `_guard`, `_run_step`), `assist_loop.py` (`_reconcile` and `_save_world` stay).
- Golden tags: all cassettes that move the robot (16 of the 21, counted from the cassettes), `stop` (`cancel_during_move`), `bt_goal` (the service `try_step` calls
  `_run_step`).
- To ADD: a cassette `pause_resume` (the refind after a pause) and `coverage` (option `request_coverage`) if the fakes can play them; else
  they are listed as not covered in the step's report.
- Effect on `assist_loop.py`: about -266 lines (to about 733).
- Default data: none (a verbatim move).
- Risk: high. One step's life; `_guard` is called through the class by a test; `LoopServices.try_step` calls `_run_step` on the loop.
- Parallel: LOOP stream, sequential.

### Step 23: execution helpers and the finish block move out

- Files: `phases/exec_helpers.py` (`_bind`, `_params`, `_holding`, `_base_pose`, `_dist_to_place`, `_found_point`, `_place_of_far_point`),
  `phases/finish.py` (`_finish`, `_say_of_msg`, `_humanize`, `_llm_failed`, `_deciding_failure`, `_note_failure`, `_cancelled`), `assist_loop.py`
  (`_effect` stays: it calls `current()`).
- Golden tags: all (every run ends in `_finish`; every step binds).
- To ADD: nothing.
- Effect on `assist_loop.py`: about -175 lines (to about 558).
- Default data: none (a verbatim move).
- Risk: medium. 28 methods call `_finish` or `_cancelled`; `_bind` and `_finish` are called through the class by tests.
- Parallel: LOOP stream, sequential.

### Step 24: the config moves to `loop_config.py`; the loop becomes the pipeline

- Files: `robot_agent/core/planning/loop_config.py` (new: `ASSIST_OPTIONS`, a function that resolves the config, the role-model configs),
  `assist_loop.py` (`__init__` delegates; `_run_blocking` iterates `PIPELINE`), `tests/test_pipeline.py` (the stage order and the signals).
- Golden tags: all; `default_options` (`fetch_engine_defaults`: the engine's own defaults).
- To ADD: a test that the resolved config of the fakes equals a snapshot taken before the step (every key).
- Effect on `assist_loop.py`: about -258 lines (to about 300).
- Default data: none new. The numeric defaults stay as they are; they become labelled constants in Step 28.
- Risk: high. The order of the `cfg.update` calls, the environment variables, and the 28 places where a test builds the loop without running `__init__`. The string option
  that is read as true (fit S1) is NOT fixed here: it is a behaviour change.
- Parallel: LOOP stream, last of the LOOP steps.

### Step 25: `bt_goal` becomes the generic tree executor

- Files: `robot_agent/options/bt_goal.py`, `robot_agent/core/planning/behavior_tree.py` (the optional mapping of `act`), `docs/bt_goal.md`,
  `tests/test_option_bt_goal.py` (new tests only), the `trees` section.
- Golden tags: `bt_goal` (6 cassettes: today's trees must run the same).
- To ADD: `fakeline` cassettes: a tree with a typed `act` argument, a tree that branches on a `choice` answer, a tree with `events: true`
  (the `tree_node` events), a tree whose failed step uses the policy `tree`.
- Effect on `assist_loop.py`: 0 lines.
- Default data: no pack declares typed arguments, question names or `events`, so today's trees run as today; `planner.options.bt_goal.trees`
  is read as before.
- Risk: medium. `bt_goal.py` has 33 lines left under its limit; the executor part may need its own module in the options layer.
- Parallel: OPT stream; in parallel with any LOOP step. Needs Steps 6, 14, 15.

### Step 26: declared preconditions and effects for pack actions; typed arguments at bind

- Files: `robot_agent/core/pack/action_rules.py` (new), `robot_agent/core/planning/action_model.py` (the wrapper adds declared findings and
  effects), `robot_agent/core/planning/base.py` (`WorldState.facts`, written only when not empty), `phases/exec_helpers.py` (typed `args` into
  `params`), `tests/test_pack_action_rules.py`.
- Golden tags: all (no schema-1 pack declares rules: every replay must be identical, and every event's `world` must have no new key).
- To ADD: `fakeline` cassettes: a custom action whose precondition fails at the gate and at the guard; an effect that makes a goal hold.
- Effect on `assist_loop.py`: 0 lines.
- Default data: `rules: std.embodiplan` for the standard actions; nothing declared = nothing evaluated.
- Risk: high. The belief is persisted and shown in every event; a non-empty default would change every cassette. The wrapper is the single
  source of action rules (R4) and is used by the gate, the guard and the verifier.
- Parallel: PACK stream; needs Steps 6, 10, 23.

### Step 27: the fleet reads needs from the manifest

- Files (robot_agent): `robot_agent/api/robot_info.py`, `robot_agent/api/models.py`, `docs/fleet_seams.md`, `tests/test_robot_info.py`.
  Files (robot_fleet, its own worktree; other people's uncommitted files are not touched): `robot_fleet/models.py`, `spoke.py`, `scheduler.py`,
  `graph.py`, `compat/needs_v1.json`, its tests.
- Golden tags: none of the loop's cassettes reach the endpoint. Coverage is the endpoint's own test and the fleet's suite (269 tests on
  branch `pex-r5`, fit).
- To ADD: a test of the manifest answer for the made-up packs (the old fields unchanged, the new ones as resolved), the same check for
  the real packs by `tools/pack_compat_check.py`; in the
  fleet: equality of `needs_v1.json` with `_NEEDS`; a test that a robot reporting `install` gets an `install` goal.
- Effect on `assist_loop.py`: 0 lines.
- Default data: a robot that reports no `goal_kinds` is scheduled from `needs_v1.json`, exactly as today.
- Risk: medium. For a robot that reports goal kinds, `deliver` / `give` become assignable and `say` with a place needs MoveTo: intended,
  written in the fleet's changelog. Merge into the fleet's local master only when its tests are green (owner's decision 6).
- Parallel: FLEET stream; in parallel with any LOOP step. Needs Step 7.

### Step 28: the remaining literals: options, constants, default-on options, inline English

- Files: `robot_agent/options/*.py` (the 19 files with a kind word and the 2 with action names, one commit per option),
  `robot_agent/core/planning/goal_fix.py`, `plan_repair.py`, `understand.py`, `goal_filter.py` (their kind comparisons), the `constants` and
  `options` sections, `loop_config.py`, `places.py` (the filler words), `step_ladder.py` (phrase keys for the help questions).
- Golden tags: per option, the cassettes that switch it on. Today only `bt_goal`, `goals_only` and, in `default_options`, the default-on
  options are in a cassette.
- To ADD: one cassette per option changed (the option on, its path taken). Options with no cassette are not changed. For the inline English
  sentences: `non_english` must cover the question before its text gets a key.
- Effect on `assist_loop.py`: 0 or negative.
- Default data: the constants with `assumed: true` where the code says "a priori" (inv, "Per-robot constants"); the standard pack switches
  on the four default-on household options and their own default becomes off.
- Risk: medium, spread over many files; low per commit. This step is the most deferrable (see below).
- Parallel: OPT stream (one option per commit, files disjoint), up to four in a wave. Needs Steps 7, 10, 24.

## What this closes in framework_fit.md

| id | closed by | status after level B |
|---|---|---|
| G1 goal kind `install` | Goal kinds, Fleet needs | closed |
| G2 six predicates | Predicates (facts and registered evaluators) | closed |
| G3 `act(action, object)` only | Actions (typed `args`), Step 25 | closed |
| G4 the engine's ladders run past the tree | Recovery (`by_kind`, policy `tree`) | closed |
| G5 yes / no answers only | Answers (`choice`, `extract`, `keep_text`) | closed |
| G6 tree trace not in the event stream | trees `events: true` | closed, opt-in |
| G7 no LLM channel for the pack | `ctx.ask_model` for answer readers and recovery policies | closed for decisions at the engine's seams; a SKILL still has none |
| P1 YAML `on:` | the loader rejects a non-string key with a hint | closed as an error message |
| P3 Find judged beyond `reach_far_m` | the step verifier `std.find_reach` is declared per action; a pack may drop it | closed |
| P4 the floor re-find | `recovery.by_kind` / `codes.dropped` | closed |
| P7 the two-destinations validator | `conflicts: none` per kind | closed |
| P8 `look_then_ask` takes the parking as the source; the end check is a Find | a kind with no `from` slot; `observe` | closed |
| S3 kind-less question taken as a read-back | question kinds (opt-in) and `question_kinds` in the manifest | closed with a schema-2 pack and the fleet's `strict_readback` |
| S9 "I will measureclips" | `verb` of an action | closed (the pack already declares the verbs) |
| S10 recovery inside a skill is invisible | the subtrees can be written in the goal's tree | smaller, not closed |
| S1 the string "0" is true | not in level B | open: a bug fix with a behaviour change |
| G8 to G13, S2, S4 to S8, P2, P5, P6 | not vocabulary (fleet behaviour, process state, model warm-up, the simulator) | open |

## Cost and where level B could be overengineered

Cost, as honest estimates (no code was written, so these are sizes of the plan, not measurements):

- New code, about 2700 lines: `contract/pack.py` about 300, `core/pack/` about 900, tests about 1500. Moved code: about 3750 lines out of `assist_loop.py`
  into 19 modules, and about 900 lines of bodies into the standard pack's callables. Data: about 700 lines of YAML.
- 28 steps. Each needs a golden replay, the mutation check, the full suite (about 60 s) and a report. Steps 12, 14, 16, 22, 24 and 26 are
  high risk. My estimate, not a measurement: the six-hour cap of the owner's decision 8 does not hold all 28 steps; it holds roughly Steps 1 to 12.
- The goldens are the real limit. They use one made-up household robot. Step 1 has 16 kinds of scenario to add, and some may not be playable
  by the fakes. A step whose path is not in a cassette does not start.
- A pack author now has a larger surface to learn: eleven sections. The standard pack is the worked example; `check_manifest` prints the
  resolved pack.

Parts I would DEFER if the owner asked for a smaller cut, most deferrable first:

1. Step 28 (option literals, default-on options into the standard pack, constants as data, phrase keys for inline English). It changes many
   files for robots that are all household robots. Do it per option, only when a second robot needs it.
2. `duration_s` and `errors` of an action. Nobody reads them today (the fleet estimates from history; the skill's `errors` are parsed and
   unread). Declare them when a reader exists.
3. Failure subcodes. A pack can put the detail in the result's data and a tree can test a fact; the subcode is convenience.
4. Declarative `holds` lists and `ground` entries for the HOUSEHOLD kinds. The named callables are the proof-friendly form and are enough.
5. The declared ladder executor with `with:` argument overrides. The policies `tree`, `planner`, `stop` and the five standard callables cover
   three robots; a pack can register its own callable.
6. `extract` (numbers with units) and `unclear: model` in answers. `choice` with word lists and `keep_text` remove G5 without them.
7. Moving the Find ladder and the step ladders INTO the standard pack behind a context protocol. In this design they stay modules of
   `core/planning` that the standard pack names. That is honest about where the code is, and saves a protocol of about 20 methods.
8. Phases as objects with explicit inputs in place of mixins. Not provable in small steps.
9. `phrase` per kind in the manifest answer (the fleet's voice is already open data).
10. More than one base pack, importing single entries from another pack, discovering packs through entry points.
11. The split of the 62 phrases between the core pack and the standard pack: one file would do until a pack exists that does not extend
    `household`.

A smaller cut that still removes G1 to G6 (13 steps): Steps 1, 2, 3, 5, 6, 7, 10, 12, 14 (the selection table only; the Find ladder stays
where it is), 15 (only the reader table and the `choice` type), 25, 26, 27.
It leaves `assist_loop.py` at about 3000 lines: the vocabulary is open, the loop is not yet a pipeline. Steps 8, 9, 11, 13, 16 to 24 are the
"thin loop" half of level B and are worth their cost only as a whole.

Where I am least sure:

- The predicate scopes. They exist only so that the two old evaluators stay exactly as they are. If the owner accepts that a tree may use
  the four effect names too (a tree that uses one today is unusable), the scopes can go.
- The fact store. It is the smallest way to give a pack a belief field, but it is a second belief next to the action model's. A pack that
  needs a fact the standard actions change (the pipe leaves the clips when it is picked) must declare that effect itself.
- `class` of an action versus the option roles: two near-equal ideas. They are kept apart because filling in roles would change behaviour.
- Whether the mixin form meets "small modules with one interface". It meets the size and the interface at the stage level; inside a stage
  the methods still share `self`.

## What was not read

- `assist_loop.py`: I read the header and module data (:76-121), the schema and prompt (:300-368), the goal-reach block (:1438-1545),
  `_goal_phrase` in part, `_bind` and `_effect` (:1546-1625), the driver and `_finish` (:1660-1735), the execute loop and plan end
  (:3205-3275), the goal observation (:3345-3452), the guard (:3467-3550), one step, the post-check, success and failure (:3551-3712) and the
  final check. I did NOT read the understand and read-back methods (:658-1437 except the lines above, :1787-2649), generate-and-certify
  (:2651-2941), replan and goal change (:2943-3203), the policy rung and waits (:3713-3806) or the Find ladder (:3808-4063). For those the
  design rests on the inventory's phase map, call counts and line ranges.
- `loop.py` (`ClosedLoop`, only its method list), `language.py`, `goal_fix.py`, `plan_repair.py`, `search.py`, `search_hooks.py`,
  `understand.py`, `which_one.py`, `announcer.py`, `robot_prompt.py`, `reconcile.py`, `mapper.py`, `failures.py` beyond the cited lines.
- Options: only `bt_goal.py` was read in full. The other 43 are placed in the table from the inventory's option map.
- The option engine: `spec.py` and `services.py` in full, the first 30 lines of `registry.py`; not `dispatch.py`, `config.py`.
- Contract: `recovery.py`, `pack_data.py`, `action_roles.py` in full; `manifest.py` lines 1-135 and 520-702 and the `Manifest` class head;
  `types.py` lines 150-270; `adapter.py` lines 1-60; `legacy_adapter.py` only `load_adapter`.
- `embodiplan`: `Capabilities`, `SymbolicState`, the head of `check`, the end of `check`, `apply`. Not the rules of each action, not the planners.
- The packs: `pex_robot/configs/manifest.yaml` in full and the first 150 lines of its `adapter.py`; NOT `skills.py`, `sight.py`, `session.py`,
  `port.py`, `tasks.py`, and nothing of `pexcase/`. The PEX examples (argument names, numbers, the tree) are illustrations marked `assumed`,
  to be replaced in Round C from the pack's own sources. kcare_robot: the first 60 lines of its manifest and the outline of its adapter.
  symbolic_robot: the `planner` sections, the `Handover`, `Look`, `Wait`, `Dock` skills of the home manifest, the outlines of both adapters, the
  factory safety profile's head.
- robot_fleet: `scheduler.py` lines 1-60, `graph.py` lines 20-70, `spoke.py` lines 45-70, `models.py` lines 100-123. Not `orchestrator.py`,
  `assign.py`, `voice.py`; the fleet facts beyond those lines come from the inventory.
- The goldens: `scenarios.py` in full, the first 130 lines of `harness.py`, the first 60 of `golden_mutation_check.py`, and the tags of the 21
  cassettes and their counts of model and robot calls (two scripts). I did not read a cassette's events and did not run the replay or the mutation check.
- No code was run except those two scripts. No line count in this document was measured by me: the sizes are the inventory's, and the
  per-step sizes are estimates from its phase map.
