# Generalize the core: design, level B (round A, stream DESIGN2; revised after review, stream FIX)

Status: PROPOSAL. No production code was written or changed.
Status of the branch (2026-11-23, final review, fix round 1): goldens extended, design corrected, NO core change. 0 of the 35 steps are
merged; `robot_agent/` is byte-identical to `65124d0` and `assist_loop.py` has 4081 lines (the baseline). Round C (the PEX robot) does
not start: its gate is "Round B complete, `assist_loop.py` smaller". Work resumes at wave W1 (S02, S03), after the owner has seen how the
S01 harness edit was undone (`tests/golden/harness.py` is round A's again; the additions are `tests/golden/harness_sets.py`). This file replaces the level-A design scope (the minimal extension points):
the owner changed the scope on 2026-11-22 to level B, the full registry.
Revision of 2026-11-22 (stream FIX): an adversarial review found 29 problems in the first version (two reviewers: design, golden
coverage; 7 of high severity). Each was checked against the code; the verdicts are in "Review of this design". The false claims are
corrected in place, the missing opt-in steps are added, the plan is file-disjoint by construction, and the machine-readable plan is
`docs/proposals/generalize_core_plan.json` (35 steps in 23 waves; one step deferred whole, plus the deferred parts listed there).
Base: robot_agent `8002347` (branch `generalize-core`, now at `65124d0`: docs and goldens only), robot_fleet master `0dc947c` (read only; it was
`d74f326` when the first version was written, the difference is a Makefile), `embodiplan` as installed in the shared venv (read only).
Inputs: the level-B inventory (`docs/proposals/generalize_core_inventory.md`, branch `gc/inv2`, commit `05f8466`; cited below as "inv"), the golden
harness (branch `gc/gold`, commit `7cb7687`: 21 cassettes), `pex_case/docs/framework_fit.md` (cited as "fit"), the manifests of `kcare_robot`,
`symbolic_robot` (home, factory) and `pex_robot`, and the code at the lines cited. Date: 2026-11-22.
Note for a later push: this proposal names the owner's robot packs and quotes parts of their manifests. robot_agent is a public repository;
the file is a local commit on `generalize-core` and should be reviewed for that before it is pushed anywhere.

How to read the evidence: `path:line` is relative to the root of this repo. "read" = I read the lines for this design. "inv" = the number or line
comes from the inventory and I did not read it again. The YAML blocks are EXAMPLES of declared data, not files that exist. What I did not read is
listed at the end. A step is named `S01` to `S32` (the numbers of the first version are kept where the step is the same; `S16a`, `S16b`,
`S23a`, `S23b`, `S25a`, `S27a`, `S27b` are splits, `S29` to `S32` are new, `S28` is deferred).

## Short answer

1. Level B = one versioned PACK DECLARATION per robot: data (goal kinds, predicates, actions with typed arguments, verifiers, recovery policies,
   questions and answers, phrases, trees, constants, fleet needs) plus NAMED registered callables. The household vocabulary and rules become
   the STANDARD pack `household`, inside this repo (`robot_agent/packs/household/`): one file per section. A robot pack extends it or replaces
   parts of it.
2. Said plainly (review finding 13): level B makes the vocabulary and the rules PLUGGABLE. It does not take all household code out of
   `core/planning`. The Find ladder, the step ladders, the guard's drop path and the wording of the plan checker move verbatim into modules of
   `core/planning` that the standard pack names. How much household vocabulary is left in the loop's modules is MEASURED by a ratchet count of
   the lint (Step S03: 66 lines with a standard action name and 59 with a goal kind in quotes today), not claimed.
3. A manifest with no `pack:` section is schema 1. The loader gives it the standard pack unchanged. So `kcare_robot`, `symbolic_robot` (home,
   factory), `pex_robot`, `homeloop_robot`, `shopfloor_robot`, `tests/fakebot` and `tests/fakehome` load and behave exactly as today, with no edit.
4. The registry is a registry of NAMES. A pack file never holds code, a dotted path to evaluate, or an expression. It names a callable that a
   module registered (`std.find_at_destination`, `pex.pipe_in_clips`).
5. Every household rule moves in two levels. First: the old code becomes a named callable with the SAME body, chosen through a table (provable:
   a golden replay and a differential test). Second, and only where a new pack needs it: declarative data (a `holds` list, declared effects).
6. An UNKNOWN goal kind is, today, vacuously "reached" and "holding" (`robot_agent/core/planning/assist_loop.py:1504-1543`, read: `_reaches` ends
   with `return bool(goals) or bool(steps)` and `_goals_hold` returns True for a kind it has no branch for). The first version said the
   opposite. Schema 1 keeps that result, byte for byte; a schema-2 pack must say how each of its kinds is done (Step S29), so a new kind is
   never "done by belief" by accident.
7. `assist_loop.py` (4081 lines) becomes a thin driver of about 370 lines (credible range 350 to 450, not the 300 of the first version: the
   docstring, the re-exports, `run_blocking`, `__init__`, a delegating `_check`, `_effect`, `_reconcile` and `_save_world` must stay in the
   file, see invariants 10, 13, 14). The phases are mixin modules of 300 lines or less. The 30 hooks and 25 services stay where they are.
8. The honest structure is four stages in a row (understand, generate-and-certify, execute, final verify) and two services that the stages
   call (recover, answer). `_run_blocking` keeps its four calls; no pipeline object is added (no pack can add a phase).
9. 35 migration steps in 23 waves. Wave W0 is the golden extension alone, on the old code. Exactly one step of a wave touches
   `assist_loop.py`, and it makes the file smaller; that is why there are as many waves as such steps (22). The other steps ride along, at most
   four in a wave, with no common file (the table of waves and the per-step file lists are generated from one data file and checked by a
   script).
10. The fleet reads goal kinds, their needs, their footprint slots and the question kinds from `GET /robot/manifest` (new fields, additive).
    Its `_NEEDS` table becomes a compatibility data file used only for a robot that reports no goal kinds.
11. Cost, stated plainly: about 2900 new lines (schema, loader, registry, tests), about 3700 lines moved out of `assist_loop.py`, about 600 lines
    of YAML, 35 steps of about one golden replay and one 60 s test run each. My estimate, not a measurement: the six-hour cap of the owner's
    decision 8 holds waves W0 to about W10 (the vocabulary and the opt-in steps a new pack needs, `assist_loop.py` at about 3050 lines); the
    verbatim moves of W11 to W22 continue only as far as the cap allows.
12. What level B does NOT fix: the gaps of "fit" that are not vocabulary (G8 to G13, S2, S5 to S8), a SKILL's own model channel (G7 stays
    open for skills), and the string option "0" that is read as true (S1), which is a bug fix with a behaviour change and needs its own
    decision.

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
| the standard pack | `robot_agent/packs/household/pack.yaml` (the header only), ONE FILE PER SECTION next to it (`goal_kinds.yaml`, `predicates.yaml`, `actions.yaml`, `verifiers.yaml`, `recovery.yaml`, `questions.yaml`, `phrases.yaml`, `constants.yaml`, `fleet.yaml`, `trees.yaml`; a missing file is an empty section) and `robot_agent/packs/household/callables/<module>.py` (every module in the folder is imported) | by name, from the index |
| the core pack | `robot_agent/packs/core/pack.yaml`, its section files and `callables/` (what the loop needs to run at all: say, ask, wait, stop, the generic questions) | always first in the chain |
| the index | `robot_agent/packs/index.yaml`: the pack names, their files, and `schema_1_base: household` with a `source` label | the pack loader |

The file reference follows a pattern the repo already has: `safety.profile` is a path relative to the manifest, loaded and validated with
errors by field path (`robot_agent/contract/manifest.py:525-541`, `robot_agent/core/safety/profile.py:82-89`, read). The folder `robot_agent/packs/`
is the owner's decision 2 (inside this repo, no new repo). It is inside the Python package so that an install ships it; `pyproject.toml` line 88-89
lists package data by hand today (`lang/*.json`), so the YAML files must be added there (Step S02).

Why one file per section: the first version kept all sections of the standard pack in one `pack.yaml`, and three steps it called
parallel wrote that file (review finding 4). With one file per section and a loader that takes the files it finds, a step adds its own
file and edits no shared one. A robot pack may do the same (`pack: {dir: pack}` reads `<pkg>/configs/pack/<section>.yaml`) or keep
everything inline.

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
  questions_default_kind: null   # a literal kind for a `clarify` event that carries none (opt-in, Step S31)
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

`python -m robot_agent.contract.check_manifest <pkg> --strict` runs steps 1 and 2 (the section's schema). It cannot run the loader:
`check_manifest.py` is contract layer and imports nothing above it (read: its imports are `.legacy_adapter` and `.manifest`). Steps 3 and 4
and the resolved pack (which entry came from which pack) are printed by an engine tool, `python -m robot_agent.core.pack.show <pkg>`,
so a pack author sees what the robot will use. The first version gave all four steps to `check_manifest`.

### Named registered callables

`robot_agent/core/pack/registry.py` (new), the same shape as the option registry (`robot_agent/core/options/registry.py:27-30`, read:
a duplicate name is an error):

| namespace | signature (all pure unless said) | used for | step |
|---|---|---|---|
| `predicate` | `(belief, args) -> True / False / None` | conditions of trees, `pre`, `effects`, `holds` | S06 |
| `goal.rules` | an object with `reached(goal, steps, world, ctx) -> bool`, `holds(goal, belief, ctx) -> bool`, `missing(goal, steps, world, ctx) -> text or None`, `phrase(goal, places, nice) -> LocalText` | everything the loop asks about ONE kind | S05, S08 |
| `verifier.step` | `(step, result, ctx) -> list of VerifyResult` | per-step postconditions | S12 |
| `verifier.goal` | generator `(goal, ctx) -> (True / False / None, evidence, device)` | the end-of-run check | S12 |
| `recovery` | generator `(run, step, reason, rule, ctx) -> (outcome or None, replan hint)` | a ladder; the signature of `_recover_*` today (`robot_agent/core/planning/step_ladder.py:23`, read) | S14 |

Five namespaces. The first version had thirteen; nine of them had only the household pack as a user (review finding 10). Three are
merged into the one object `goal.rules`, six are deferred as callables of their own:
`goal.compile`, `goal.ground`, `goal.phrase` and `goal.conflicts` as separate callables (the first two stay code in `goals.py` and the
loop, the phrase is a method of `goal.rules`, the conflict rule is the flag `conflicts: default / none`), `goal.reached`, `goal.holds`
and `goal.missing` as three names per kind (now one object), `answer` (the readers stay functions of `interact/dialog.py`; a pack question
is read by word lists, S25a) and `action.rules` (there are exactly two providers: the action model, and `declared`, S26).

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

| field | meaning | today | built in round B |
|---|---|---|---|
| `slots` | name -> `{type: object / place / place_or_person / container / text / person, required}`; extra ad-hoc fields stay allowed | `{type, object, from, to}` (`robot_agent/core/planning/assist_loop.py:318-324`, read) | parsed (S02), reported to the fleet (S27a); the understand schema built from it is deferred |
| `needs` | list; an item is an action or a list = any one of them | `GoalKind.needs` (`goals.py:26-30`) | S07 |
| `understood` | `always` (in the model's enum even when the robot lacks the actions: understood, then refused) or `capable` (only when the needs are met) | `MANIPULATION`, `TEMPLATE_KINDS` (`goals.py:32`, `:47`) | S07 |
| `prompt` | the kind's line in the understand prompt, as text | `GoalKind.prompt` | S07 |
| `planned_by` | `planner`, `template` (fixed steps, no model) or `tree` | `is_template` (`goals.py:105-106`) and option `bt_goal` | S07 (the household values), S29 (`tree` for a new kind) |
| `rules` | ONE registered name per kind (namespace `goal.rules`): an object with `reached(goal, steps, world, ctx)`, `holds(goal, belief, ctx)`, `missing(goal, steps, world, ctx)` and `phrase(goal, places, nice)` | the if-chains `_reaches`, `_goals_hold`, `_missing` (`assist_loop.py:1484-1543`, read) and `_goal_phrase` (`:1236-1268`, read) | S05 (the first three), S08 (`phrase`) |
| `holds` | for a kind WITHOUT `rules`: a list of predicates | none | S29 |
| `observe` | a goal verifier name, or null = "no sensor declared for it (belief)" | only `on` / `in` are looked at (`assist_loop.py:3417-3418`, read) | S12 |
| `unknown_is` | `belief` (cannot tell = keep the belief) or `failure` | belief (inv, "Done semantics") | S29 |
| `phrase` | for a kind WITHOUT `rules`: a phrase key with the slot names | an unknown kind reads "<kind> the <object>" (`assist_loop.py:1268`, read) | S29 |
| `subgoal` | the kind's sub-goal phrase for a planner that takes `subgoals` (option `sage_subgoals=goals`, the default), a template with the slot names; household default = today's text | `goals.subgoal_text` (`goals.py:174-190`, read 2026-11-23): `put the {object}{src} on {to}` / `hand the {object}{src} to {to}`, `put the {object}{src} into {to}`, `open {object}`, `close {object}`, `go to {to}`, `say "{object}" at {to}`, `deliver the {object} to {to}`; any other kind: `describe()` | S02 (parsed), S07 (household text; pinned by tests/test_subgoal_phrases.py and the set cassettes `subgoals_*`, invariant 4) |
| `conflicts` | `default` or `none` | the validator for on / in / open / close, no off switch (`goals.py:122-148`, read; fit P7) | S07 |
| `limits` | which manifest limits apply to the object (`payload`, `size`) | only on / in (`assist_loop.py:870`, inv) | deferred with S28 |

Left as code in `goals.py`, because each has one user (review finding 10): the extra need of a `say` goal with a place (`goals.py:69-70`,
read), the mapping `give` -> `on` and the optional kind `give` behind its option (`goals.py:50-53`, `:93-96`, read), `compile_steps` of the
template kinds (`:219-241`, read), the conflict validator's body, the grounding per slot (`assist_loop.py:944-1137`, inv). The first version
gave each a schema field or a callable namespace; they come back when a second user exists.

### What "done" means, per case

- A kind with `rules` (every household kind): the kind is done when `rules.holds` is true in the belief AND its `observe` verifier does not
  say False. That is today's rule (inv, "Verifiers and recovery" A) with the two parts named.
- An unknown kind in a schema-1 pack (a goal handed in by a caller, as fit G1's workaround could have done): today's result, kept exactly.
  `_reaches` returns True as soon as there is a goal or a step, `_goals_hold` returns True, `_missing` adds no line, the end check says
  "no sensor declared for it (belief)" (`assist_loop.py:1484-1543`, `:3417-3418`, read). So the run ends `success` by belief. A dispatch built to
  the first version's sentence ("not reached, no text") would have changed that; the cassette `given_goals_unknown_kind` (S01), a row of the
  differential grid (S05) and the mutation `unknown_kind_not_reached` now guard it. I ran that mutation on the 21 cassettes of today: none
  catches it, so the guard has to be added before Step S05.
- A kind of a schema-2 pack without `rules` (Step S29):
  - `holds`: every predicate of the list is True in the belief. "Cannot tell" (None) is NOT holding.
  - reached by a plan: the list is evaluated after the steps' rules and declared effects are applied to a copy of the belief (the action
    model's `apply`, extended in S26). Here "cannot tell" is "not refuted": a predicate that reads an observation cannot be decided from a plan.
  - missing: one line per predicate that is not reached, for the planner.
  - `unknown_is: failure`: when the kind's verifier cannot tell, the goal is unmet, with the failure code `perception_unavailable`
    ("could not look", `robot_agent/contract/types.py:20`, read). With `belief` the goal stays on the belief, as today.
  - A kind with neither `rules` nor `holds` is a load error; so is `observe: null` with `unknown_is: failure` (it could never succeed).
  - `planned_by: tree`: the goal is run by its tree (`trees:`); it never goes to the planner. A tree with no way left asks or stops.

The ORDER of the kinds is part of the understand prompt and of the schema enum (`kinds_for`, `goals.py:74-81`, read: the manipulation kinds,
then the template kinds the robot can do, then the optional ones). The loader keeps the file order; a robot pack's new kinds come after the
base's. The prompt hash in the cassettes guards this for `on`, `in`, `open`, `close`, `go` only: `kinds_for` gives the made-up robots of today
no `say` and no `deliver`, so those two prompt lines are in no golden prompt (review, golden finding 1; I ran the mutation "the deliver line
changed": not caught). The made-up pack `fakeline` of S01 has Say and WaitForUnload for that reason.

### Example: standard household pack (`robot_agent/packs/household/goal_kinds.yaml`)

```yaml
goal_kinds:                                   # source: robot_agent/core/planning/goals.py:33-54
  "on":
    slots: {object: {type: object, required: true}, from: {type: place}, to: {type: place_or_person, required: true}}
    needs: [Pick, Place]
    understood: always
    planned_by: planner
    rules: std.on                    # reached / holds / missing: the `on` branches of the three if-chains and _on_reached; phrase: S08
    observe: std.find_at_destination
    unknown_is: belief
    conflicts: default
  "in":      {needs: [Pick, [PutIn, Place]], understood: always, planned_by: planner, rules: std.in, observe: std.find_at_destination}
  open:      {needs: [Open], understood: always, planned_by: planner, rules: std.open, observe: null}
  close:     {needs: [Close], understood: always, planned_by: planner, rules: std.close, observe: null}
  go:        {needs: [MoveTo], understood: capable, planned_by: template, rules: std.go, observe: null,
              prompt: '"go" = only go somewhere, nothing to move: object "", from "", to = the place.'}
  say:       {needs: [Say], understood: capable, planned_by: template, rules: std.say, observe: null,
              prompt: '"say" = tell someone something: ...'}                          # the text of goals.py:40-42
  deliver:   {needs: [MoveTo, WaitForUnload], understood: capable, planned_by: template, rules: std.deliver, observe: null,
              prompt: '"deliver" = take what the robot carries ...'}                  # goals.py:43-45
  # `give` stays the optional kind of goals.py:50-53 (one user), not an entry here
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

This removes fit G1 (no `install` kind) once Steps S07 and S29 are in, P7 (the validator with no switch, S07) and the wrong read-back
source of P8 (no `from` slot to fill).

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
  event of today's runs is unchanged. Life cycle (review finding 11; Step S26): facts are RESET at the start of a run, so a fact of a
  warm-up or of an earlier run cannot satisfy a precondition (the stale-belief failure of fit S8); `update_from_dict` accepts `facts`,
  so `PUT /agent/world {"facts": {}}` clears them (today it ignores a key it does not know, `base.py:194-215`, read); facts are not in
  `as_text()` (the planner's view). A fact that must outlive a run (`persistent: true`) is deferred.
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

The tree grammar is today's (`robot_agent/core/planning/behavior_tree.py`, 90 lines, read). Two of the additions use seams that EXIST and
come first, as tree keys next to `tree:` (Step S25a; review finding 9): `ladder: false` makes every `act` call
`try_step(run, step, ladder=False)` ("the recovery is the caller's", `robot_agent/core/planning/option_services.py:74-79`, read; today
`bt_goal.py:169` passes `ladder=True` always), and `events: true` sends each node through the service `emit` as a public `tree_node`
event (`robot_agent/core/options/services.py:19`, read; today the trace is a lab flag, `bt_goal.py:158-159`, read). The `ask` leaf already
chooses a branch by word lists (`bt_goal.py:171-173`, read); the gap is only that `pack_words` reads the language pack and not the
robot pack, and S25a closes it. Step S25 then adds what has no seam yet: an optional mapping as the last argument of `act`, and `ask`
naming a question of the pack with the answer kept as a fact ("Answers"). `planner.options.bt_goal.trees` keeps working.
This removes fit G3, G4 and G6, and makes S10 smaller: a recovery written in the goal's tree is visible, one written inside a skill is still not.

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
  The same effect exists already for the steps a tree runs: the tree key `ladder: false` ("Actions", Step S25a). `by_kind: tree` is the
  declared form for a whole goal kind and comes with Step S14.
- DEFERRED (review finding 9): a declared ladder, a list of rungs with the names of the `Recovery` enum (`recovery.py:20-31`, read: `stop`,
  `wait_health`, `health_check`, `reapproach`, `continue_search`, `retry`, `refind`, `verify`, `ask_help`, `replan`; the first version wrote
  `ask` and left two out): `{do: <rung>, times, question, with}`. `question` names a question of the pack; `with` sets typed
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

The `ladder:` form of `pex.seat_retry` above is the deferred declared ladder. Until it exists the same recovery is written in the goal's
tree: two `act` leaves with different typed arguments under a `fallback`, then the `ask` leaf (Steps S25a, S25).

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
function of `interact/dialog.py` or of the loop today. Giving each a registered name, and one result shape `AnswerResult(kind, value, text,
slots)`, is the deferred part of this section (see "What round B builds" below).

`unclear: model` is ONE call of the planner's model inside the run's budget, through the service that exists for options
(`ask_model`, `robot_agent/core/options/services.py:37`, read): it maps a free sentence onto one of the declared choices. A recovery policy and
an answer reader get the same service through `ctx`. This does NOT close fit G7: the workaround of G7 is a model call inside a SKILL, and a
skill gets no channel here. G7 stays open for skills. (`unclear: model` itself is deferred, see below.)

In a tree, `[ask, <question>]` succeeds when an answer was read. The result is kept as the fact `answered(<question>, <choice>)` and under
`answer.<question>.<slot>` for the `{...}` templates of later leaves, so a tree branches on the CONTENT of an answer with the `cond` leaf it
already has, and passes a number to a skill as a typed argument. No new node type is needed.

Event kinds. Today 12 questions carry no `kind`; that stays so for every pack that does not ask for more. A schema-2 pack may set
`questions_default_kind: <a literal>`: a `clarify` event that would carry no kind then carries that one. It is applied at the ONE place
every event is built (`RunContext.ev`, `robot_agent/core/planning/loop.py:234-243`, read), so no question site changes (Step S31). That is
what a fleet manager needs to tell an objection from a read-back (fit S3: `robot_fleet/orchestrator.py:242` takes a clarify with no kind
for the read-back, read), and it is opt-in because it changes the event stream. The value `name` (every question its own name) needs
the table of the 28 questions and is deferred with it.

What round B builds of this section, and what it defers (review finding 9). Built: a pack's own questions for the `ask` leaf of a tree
(`phrase`, `kind`, `answer.type: choice` with `choices.<name>.words` or `lists`, `keep_text`), read with the word-list reader that exists
(S25a), the answer kept as the fact `answered(<question>, <choice>)` (S25), and `questions_default_kind` (S31). Deferred: the 28
household questions as a data table with a registry of answer readers, the answer type `number`, `extract`, `unclear: model`. The
schema (S02) accepts those fields; nothing reads them yet, and a schema-2 pack that uses a deferred field gets a load error that says so.

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

Three small sections with no schema of their own beyond "name -> value with a label". Round B reads `trees` from the pack (S25a) and
nothing of `constants` and `options` of the STANDARD pack: moving the constants and the option defaults is the deferred Step S28.

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
options:          # the household options that are ON for every robot today (target of the deferred Step S28; until then the option's own default)
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

A stage is a generator: it yields the run's events and returns a signal (`_NEXT`, `_STOP`, `_DONE`, the loop's own values today). Today the
first three stage methods return a bool ("the run ended") and `_final_check` returns the `done` event itself (`:1690-1696`, read). That stays:
`_run_blocking` keeps its four calls in a row. The first version wrapped them in a `Phase` protocol and a `PIPELINE` tuple; no pack can add a
phase, so that wrapper had no user and is dropped (review finding 10). The brief names six phases; four of them run in a row, and two
(recover, answer) are called from inside the others (recover from execute, answer from all). The design says so instead of drawing six
boxes in a line.

How a phase module is built: as a MIXIN class of `AssistLoop` in its own file, with its entry generator and its private methods, the pattern
of `WhichOne`, `SearchHooks` and `StepLadder`. This is chosen for the proof, not for beauty: a method moved verbatim into a mixin keeps its
name, its `self`, its call sites and the method resolution, so the tests that call methods through the class (inv, "Cut candidates",
constraint 3) and the 28 places where a test builds the loop with `AssistLoop.__new__` (constraint 4) keep working, and a golden replay proves the
move. The run state stays one object `r`. A design where every phase is an object with explicit inputs would need all 189 methods rewritten;
it cannot be proved in small steps and is on the deferred list.

The mixins are listed in ONE tuple, `PHASE_MIXINS` in `robot_agent/core/planning/phases/__init__.py`, one entry per line, and the class line
is `class AssistLoop(*PHASE_MIXINS, WhichOne, SearchHooks, StepLadder, ClosedLoop)` (Step S03 writes it with an empty tuple). A step adds
one line to the tuple instead of editing the class line (review finding 14). The honest property of the move steps: they are revertible as
a SUFFIX, in reverse order; a middle step reverts cleanly only when no later step needs it (the `needs` of the plan).

What must stay in `assist_loop.py`, with the reason (each is an invariant below):

| stays | why |
|---|---|
| `run_blocking` (:779-784, 6 lines) and a `_check` of 40 lines or fewer | `tests/test_wiring_function_size.py:28-31` looks both up as `assist_loop.py:<name>` in a table built from the FILE name; the glob is `core/planning/*.py`, not the subfolders (read) |
| `__init__`, as a shell that calls `current()` (:375) and `host_id()` (:437) itself | both names are patched on the `assist_loop` MODULE by `tests/conftest.py:66-69`, `tests/golden/harness.py:185-188` and `robot_agent/observe/replay.py:396-405` (read); code moved to another module would call the unpatched name, silently |
| `_effect`, `_reconcile`, `_sense`, `_save_world` | they call `current()` (:1623, :3498, read); `tests/test_action_model_agreement.py:190` patches it and calls `_effect` |
| the names `current`, `host_id`, `reconcile_world` as module attributes | `monkeypatch.setattr` of a missing attribute raises |
| the re-exports `Places`, `Memory`, `words`, `ASSIST_OPTIONS`, `UNDERSTAND_PROMPT`, `UNDERSTAND_SCHEMA`, `_NEXT`, `_STOP`, `_DONE` | 98 files import from `assist_loop` (counted with `ast`: `Places` 76, `ASSIST_OPTIONS` 48, `AssistLoop` 38, `Memory` 6, the others 1 or 2 each) |
| the text of `UNDERSTAND_PROMPT`, until it is a DATA file (S16b) | it holds a non-Latin example; `tests/test_lang_inventory.py:80-86` allows that in `assist_loop.py`, `cli.py` and `plan_skill.py` only (read), so it can never move into another `.py` file |

A moved method stays a real method of a mixin, never a one-line stub, when a test reads its SOURCE:
`tests/test_sage_v2_r3_m10_round1b.py:73` reads `inspect.getsource(AssistLoop._finish)` for two phrase keys (read).

### Target module list and line budgets

All in `robot_agent/core/planning/` (limit of the lint for this layer: file 1200, function 80; the budgets below are the target, far under it).
Three names of the first version are changed because `core/planning` already has modules called `understand.py`, `verify.py` and
`goal_change.py` (listed with `ls`): the phase modules are `understand_rounds.py`, `verify_goals.py` and `goal_edit.py`.

| module | budget (lines) | step | what it holds (source block of the inventory's phase map) |
|---|---:|---|---|
| `assist_loop.py` | 450 | all | docstring, imports, the re-exports, the class shell, `__init__` (shell), `run_blocking`, `_run_blocking`, `_check` (delegating), `_effect`, `_reconcile`, `_sense`, `_save_world`, `UNDERSTAND_SCHEMA` |
| `places.py` | 210 | S03 | `Places`, `Memory`, `words` (:107-301) |
| `phases/__init__.py` | 40 | S03 | `PHASE_MIXINS` |
| `loop_config.py` | 300 | S24 | `ASSIST_OPTIONS`, the config resolution of `__init__`, the role-model configs (:369-656 without the two patched calls) |
| `phases/understand_call.py` | 300 | S16a | the model call, capability check, schema building (:658-941 without `run_blocking`) |
| `phases/ground.py` | 200 | S09 | grounding on the ENV, per slot type (:944-1137) |
| `phases/understand_rounds.py` | 280 | S17 | the understand rounds, views, refusal (first third of :1787-2549) |
| `phases/agree.py` | 280 | S18 | agreement, read-back (second third) |
| `phases/correct.py` | 260 | S19 | corrections and patches (last third) |
| `phases/answer.py` | 250 | S15 | say and ask plumbing, the ask machinery (:1143-1222, :2550-2649) |
| `phases/gate.py` | 180 | S11 | the plan checker (:1271-1438); `_check` in the loop calls it |
| `phases/certify.py` | 300 | S20 | generate and certify, the gate rounds (:2651-2941) |
| `phases/replan.py` | 100 | S21 | replan (:2943-3031) |
| `phases/goal_edit.py` | 180 | S21 | goal change and the task stack (:3034-3203) |
| `phases/execute.py` | 290 | S22 | the execute loop, plan end, guard, one step (:3205-3343, :3467-3597 without `_reconcile`, `_sense`, `_save_world`) |
| `phases/exec_helpers.py` | 120 | S23a | bind, params, sensors (:1546-1659 without `_effect`) |
| `phases/verify_goals.py` | 190 | S12 | post-check, goal observation, final check (:3345-3465, :3599-3630, :4066-4081) |
| `phases/recover.py` | 190 | S14 | success, failure, policy rung, waits, the policy selection (:3632-3806) |
| `find_ladder.py` | 270 | S13 | the Find ladder, verbatim (:3808-4063); named `std.find_search` by the standard pack |
| `phases/finish.py` | 130 | S23b | `_finish` and the failure notes (:1705-1784) |
| `tree_host.py` | 150 | S25a, S25 | pure helpers of the tree executor that do not fit under the 400-line limit of `options/bt_goal.py` (367 today) |
| `robot_agent/core/pack/*.py` | 12 files, 60 to 300 each | S02 to S32 | `loader`, `registry`, `show` (S02), `phrases` (S04), `goal_semantics` (S05), `predicates` (S06), `actions` (S10), `verifiers` (S12), `recovery` (S14), `prompts` (S16b), `action_rules` (S26), `report`, `fleet`, `questions` (S27a, S32, S31) |
| `robot_agent/contract/pack.py` | 300 | S02 | the schema dataclasses and their validation (the whole schema at once) |
| `robot_agent/packs/household/callables/*.py` | 4 files, 300 or less each (the lint limit of this new layer: file 400, function 60) | S05 to S14 | `goals`, `predicates`, `verifiers`, `recover` |

Function budget: 50 lines for new code; a function moved verbatim keeps its size (the longest in the file is 80, inv).
Size after all steps, summed from the per-step estimates: about 370 lines. The estimates are spans of the inventory's phase map (about 10
percent uncertain) and the "stays" table above adds up to about 270 lines of code before blank lines and comments, so the credible range is
350 to 450. The first version's "about 300" did not count `run_blocking`, the `_check` stub, the `__init__` shell or the schema.

### What each existing option becomes

All 44 options stay registered options. The engine's 30 hooks and 25 services keep their names, their kinds and their call order
(`robot_agent/core/options/spec.py:22-53`, read; the hook call sites are listed in inv, "Hooks"). No hook is added. What changes is where an
option's household words come from. Only the first two rows are built in round B; the rows that say "its ... literals come from the pack"
are the deferred Step S28, and until then those options keep their literals.

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
reaches them (Step S01: `hooks_order` and the frozen hook table).

## Standard pack

The standard pack is today's household behaviour, taken out of the core and given names. Nothing in it is new behaviour.

What it contains after round B: the seven goal kinds (`give` stays the optional kind in code), the ten predicate names, nine actions,
three verifiers and the end-of-run rule, five recovery policies and the action table, the household phrases and the understand prompt,
each section in its own file. Not in round B: the 28 questions as data, the constants and the option defaults (deferred).
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
   `tests/fakeline`). For the six real packs (kcare_robot, symbolic_robot home and factory, pex_robot, homeloop_robot, shopfloor_robot; the
last two were missing in the first version, review finding 16) it runs as a tool,
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
               "footprint": {"objects": ["object"], "places": ["to"]}}},
 "question_kinds": ["confirm", "pex.clip_decision"]}
```

- `goal_kinds` lists every kind of the robot's pack. `feasible` is the robot's own answer (`missing_actions` is empty for its actions), so the
  manager does not repeat the rule. `needs` is a list of alternatives, the shape of `_NEEDS`, for a manager that wants to explain a refusal.
- `footprint` says which slots are objects and which are places (the household `open` / `close` list the `object` slot under `places`).
- `question_kinds` are the kinds the robot's questions carry, so a manager can tell a read-back (`confirm`) from any other question without
  the guess at `robot_fleet/orchestrator.py:242` (inv).
- Dropped from the first version (review finding 10): `carries` (it had one possible value, `holding`) and `phrase` per kind (the fleet's
  voice packs are already open data with a `goal.default`).

The pack's `fleet:` section holds only what cannot be derived: `fleet: {kinds: {install: {footprint: {...}}}}`. `needs` and
`slots` come from "Goal kinds". The section is read and reported by Step S32; before it a kind reports the default footprint.

Example of the standard pack: `fleet: {kinds: {open: {footprint: {objects: [object], places: [object]}}, close: {footprint: {objects: [object],
places: [object]}}}}`; every other household kind takes the default footprint (objects = the object slot, places = `from`
and `to`), which is the rule of `graph.py:35-49`.

### What the manager does (robot_fleet, its own worktree, its own tests)

`RobotInfo` gains `goal_kinds` (default empty) and `question_kinds`. `feasible_robots`: a robot that reports `goal_kinds` is feasible for a goal
when the kind is there with `feasible: true` and every place of the footprint maps to the robot's own key; a robot that reports none uses
the table, which moves from code to `robot_fleet/compat/needs_v1.json` with a label ("robot_agent before the pack schema"). `footprint` reads
the reported slots, with today's rule as the default. Whose report decides (review finding 12): the manager computes a goal's footprint
BEFORE it chooses a robot (`robot_fleet/graph.py:35-49` and `:64`, `scheduler.py:45`, read), so the footprint of a kind is FLEET-WIDE.
When every robot that reports the kind reports the same footprint, that one is used; two robots that report one kind with different
footprints are an error at registration, named in the manager's log and in `/fleet/robots`; a kind no robot reports keeps the rule of
`graph.py`. A test registers two robots that declare `install` differently (Step S27b). Result: no goal-kind table in the manager's code, and a kind the manager never heard
of (`install`) can be assigned.

One difference is intended and must be said in the fleet's changelog: for a robot that reports goal kinds, `deliver` and `give` become
assignable and a `say` goal with a place needs MoveTo, because the robot's rule replaces the manager's copy.

## Compatibility

| case | what happens |
|---|---|
| a manifest with no `pack:` (all of today's packs) | schema 1. The loader takes the base named by `schema_1_base` in the index (`household`) and LIFTS what the pack declares today into the same structure: `planner.options.<option>` -> `options`, `planner.options.bt_goal.trees` and `menu` -> `trees`, `planner.action_roles` -> roles, the adapter's `recovery_overrides` -> `recovery.codes`, the adapter's i18n phrases stay first in the phrase lookup, `planner.recovery.*`, `planner.end_state`, `limits`, `geometry`. Nothing is written to disk. Lifted data keeps today's rule: unlabelled = not used, an unknown name = the feature does nothing |
| kcare_robot | `planner: {seed_memory}` only: the standard pack as it is; `is_openable`, the Korean phrases and verbs come from its adapter as today |
| symbolic_robot home / factory | the standard pack. Home: `Handover` and `Dock` are recognised from their effects as today; `Look` (a custom action) and `Wait` get no ladder, as today. Factory has no `Handover`, `Dock` or `Wait` (its actions, listed from the manifest: Ask, Close, Find, Look, MoveTo, Observe, Open, Pick, Place, PutIn, Say, Verify); the first version said it had |
| homeloop_robot, shopfloor_robot | the standard pack (both have a manifest and configs that import robot_agent; not read beyond that) |
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
   That includes the PLANNER's prompt (`robot_prompt.sage_prompt_kwargs`, the direct planner's prompt), which the first 21 cassettes do
   not hold: the `prompts` section of Step S01 does.
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
13. Tests that name functions by FILE stay untouched: `run_blocking` and `_check` are defined in `assist_loop.py`, each 40 lines or fewer
    (`tests/test_wiring_function_size.py:28-31`); `_check` is a method that calls the moved body.
14. The names patched on the `assist_loop` module stay module attributes, and every call of them stays in that module: `current`,
    `host_id`, `reconcile_world`, `AssistLoop` (`tests/conftest.py:66-80`, `tests/golden/harness.py:185-190`,
    `robot_agent/observe/replay.py:396-405`, `tests/test_action_model_agreement.py:190`).
15. A method whose SOURCE a test reads moves as a real mixin method, never as a stub (`AssistLoop._finish`,
    `tests/test_sage_v2_r3_m10_round1b.py:73`).
16. A non-Latin literal exists only in `cli.py`, `core/plan_skill.py` and `assist_loop.py`, or in a data file; the language-literal rows of
    the package stay at 70 or fewer (`tests/test_lang_inventory.py:80-86`). No new file holds text of the one language pack that
    `tests/test_sage_lab_r6_langdata.py:50-61` guards.
17. `speech.HELP_PICK` and `speech.HELP_MOVE` stay attributes of `speech`, and their text is not in `step_ladder.py`
    (`tests/test_step_ladder.py:96-106`).
18. The guards follow the code. From Step S03 the lint's loop-file SET is `assist_loop.py`, `phases/*.py`, `places.py`, `loop_config.py`,
    `find_ladder.py`, `core/pack/*.py`; the wiring ratchet (62 keys, 59 branches) is the sum over the set and falls only when a read is
    really removed; the option-key scan, the embodiplan-internals scan and the language-literal scan read the set; the new layers
    `core/pack` and `packs` hold no language literal; the vocabulary ratchet (lines with a standard action name or a goal kind in quotes)
    only falls. No existing test is edited for this: the tool's file list changes, its function signatures do not.
19. The goldens: `FORMAT` stays 1 and every cassette carries it (`tests/golden/test_golden_cassettes.py:24`); the first two entries of
    `MUTATIONS` keep their place (`:57`); a new recorded section exists only in a cassette whose scenario asks for it (`capture`); an
    EQUIVALENCE cassette is recorded in wave W0 on the old code and carries that commit; a FEATURE cassette is recorded by the opt-in step
    that adds the behaviour and is reviewed as its specification. Neither kind is ever recorded again.
20. In one wave no two steps touch the same file, and exactly one step touches `assist_loop.py` and `tools/arch_baseline.json`.
    `CHANGELOG.md`, the docs indexes and `.dev-principles*.json` are written by the wave's integrator from the steps' reports.

## Review of this design (2026-11-22) and what changed

Two adversarial reviewers read the first version: one for the design (17 findings), one for the golden coverage (12). I checked each
against the code at `65124d0`. "ran" = I replayed the 21 cassettes under that mutation in a child process (the tree was not edited).
28 findings stand; one is accepted in substance and rejected in the form of its fix (G6); two are accepted in part (D9, D10).

Design review:

| # | sev. | finding (short) | verdict and evidence | fixed in |
|---|---|---|---|---|
| D1 | high | Steps 11 and 16 move `_check` and `run_blocking`, which a test looks up by file name | TRUE. `tests/test_wiring_function_size.py:16-20` keys functions by `src.name` over `core/planning/*.py`; `:28` asks for `assist_loop.py:run_blocking` and `assist_loop.py:_check`; `run_blocking` is at `assist_loop.py:779-784`, inside the range the old Step 16 moved | both stay in the file (S11, S16a); invariant 13 |
| D2 | high | the default of an unknown kind is stated the wrong way round | TRUE. `assist_loop.py:1521` `return bool(goals) or bool(steps)`; `_goals_hold` (:1523-1543) returns True without a branch. ran `unknown_kind_not_reached`: no cassette catches it | "Goal kinds", Short answer 6, S01, S05 |
| D3 | high | no step builds the schema-2 semantics a new pack needs | TRUE (the old step list has no step for `holds` lists, `unknown_is`, `planned_by: tree` of a new kind, `remove`, `questions_default_kind`, the `fleet:` section) | S29, S30, S31, S32; the closure table |
| D4 | high | the "parallel" steps edit the same files | TRUE (old Steps 4, 5 and 6 all wrote `packs/household/pack.yaml`; old Steps 26 and 28 wrote files of the LOOP stream) | one file per section; the plan is checked by a script ("Migration steps") |
| D5 | med. | the 13-step smaller cut leaves out steps its own steps need | TRUE (old Step 14 needs 13, old Step 26 needs 23) | the smaller cut is re-derived from `needs` ("Cost") |
| D6 | med. | the planner prompt has no golden coverage | TRUE. `tests/golden/harness.py:206` replaces `_make_planner`; `RecordingPlanner` (:106-118) keeps the task or the failed step only. ran `sage_prompt_changed` (`robot_prompt.ORDER` reversed, `ACTION_TEXT` replaced): not caught | S01 (`capture: prompts`), S10 |
| D7 | med. | `__init__` calls `current()` in the range Step 24 moves | TRUE, and wider: `host_id()` (:437) is patched on the module too (`tests/conftest.py:68-69`, `tests/golden/harness.py:187-188`) | S24 keeps both calls in `__init__`; invariant 14; the replay smoke test of S01 |
| D8 | med. | the lint guards read `assist_loop.py` only | TRUE. `tools/lint_arch.py:35` `LOOP`, `:196-211`. Also: `main` and `tests/test_architecture.py:19-22` fail on a STALE entry, so without a set every move step would lower the wiring ratchet only because code left the file | S03; invariant 18 |
| D9 | med. | existing seams already close most of G4 to G6; G7 is not closed | TRUE for the seams: `try_step(run, st, ladder=False)` (`core/planning/option_services.py:74-79`), `emit` (`core/options/services.py:19`), the `ask` leaf with word lists (`options/bt_goal.py:171-173`), `ladder=True` hard-coded at `:169`. Accepted: Step S25a comes first, G7 is "open for skills", the declared ladder and the answer registry are deferred. Not accepted: dropping selection of recovery by name; the level-B brief asks for it (S14) | S25a; "Recovery", "Answers"; the deferred list |
| D10 | med. | abstractions with one user | Accepted: 13 callable namespaces become 5; `needs_when`, `canonical`, `understood: option:<key>`, `carries`, the `Phase` protocol and `PIPELINE` are dropped or deferred. Not accepted: "household kind rules as plain core functions"; the owner's level-B scope puts them in the standard pack | "Named registered callables", "Goal kinds", "Phase pipeline" |
| D11 | med. | the fact store has no life cycle | TRUE. `core/planning/base.py:178-215`: `update_from_dict` touches only the keys it knows | "Predicates"; S26 |
| D12 | med. | whose report decides a kind's footprint in the fleet | TRUE. `robot_fleet/graph.py:35-49` and `:64` compute the footprint per goal before a robot is chosen | "Fleet needs"; S27b |
| D13 | med. | "the household behaviour leaves the core" is not what the plan does | TRUE (`assist_loop.py:3693` `a != "Find"`, `:3701-3703`, `:3417` stay in `core/planning` after every step) | Short answer 2; the vocabulary ratchet of S03 |
| D14 | low | every move step edits the one class line | TRUE (`assist_loop.py:362`) | `PHASE_MIXINS` (S03); "revertible as a suffix" |
| D15 | low | four small citation errors | TRUE: the symbolic factory manifest has no Handover, Dock or Wait (listed from the file: Ask, Close, Find, Look, MoveTo, Observe, Open, Pick, Place, PutIn, Say, Verify); the rung is `ask_help`, and `continue_search`, `health_check` exist (`contract/recovery.py:20-31`); the fleet's master has 257 `def test_` and is at `0dc947c` | "Compatibility", "Recovery", S27b, the header |
| D16 | low | coverage gaps: no environment handling, no `on` effect, two more packs | TRUE (`harness.py` has no `os.environ` handling; `homeloop_robot` and `shopfloor_robot` have manifests) | S01; the compat tool of S02 runs on all six real packs |
| D17 | low | verdict: change before round B | accepted; this revision | all of the above |

Golden review:

| # | sev. | finding (short) | verdict and evidence | fixed in |
|---|---|---|---|---|
| G1 | high | every cassette has only `on` / `in` goals; six mutations replay clean | TRUE (counted from the cassettes: 23 `on`, 1 `in`). ran `template_always_reached`, `in_needs_no_pick`, `deliver_prompt_changed`: not caught; the control `go_prompt_changed` is caught | S01 (template_go, template_say_deliver on `fakeline`, close_goal, open_goal, readback_kinds) |
| G2 | high | the silence and correction branches of `yes_no` are never reached; the reader moves before its cassette | TRUE for the coverage (the scripted answers of all scenarios are `yes`, `no`, `Stop.`, one place name and one silence). ran `silence_is_yes`: not caught | S01 (unclear_answer, readback_correction, menu_retry_place), before S15 |
| G3 | high | all 21 cassettes run in English | TRUE (counted). ran `say_not_translated` (`_in_lang` = identity): not caught | S01 (non_english with scripted translator answers), before S04 |
| G4 | med. | the MoveTo help question is in no golden and in no test | TRUE for the goldens. ran `help_move_changed`: not caught; `help_pick_changed`: caught by 2 | S01 (move_help) |
| G5 | med. | only three tree conditions and five effect predicates are ever evaluated | accepted. ran `bt_at_inverted` (`at`, `opened`, `closed` inverted): not caught | S01 (bt_go_tree, bt_drawer_conditions, effect_untracked_predicate, effect_on_predicate), before S06 |
| G6 | med. | planner calls do not record the observation or the visible list; fix: format 2 | the gap is TRUE (`harness.py:109-118`). The fix as written is REJECTED: `tests/golden/test_golden_cassettes.py:24` asserts `cas["format"] == harness.FORMAT` for every cassette, so a second format number breaks a test that may not be edited. Same result without it: a scenario key `capture` adds sections to NEW cassettes; FORMAT stays 1 | S01 (`capture: planner_io`, planner_visible) |
| G7 | med. | every golden stubs the run-start reconcile | TRUE (`harness.py:190`) | S01 (`capture: reconcile`, real_reconcile), before S10 |
| G8 | med. | Step 1 may be recorded after Steps 2 and 3 | TRUE (the old "Parallel" lines) | W0 is S01 alone; the cassette carries its commit and a test checks it |
| G9 | low | the model stub drops the sampling options and the role | TRUE (`harness.py:93-103`) | S01 (`capture: llm_kw`, llm_roles), before S16a |
| G10 | low | no golden reaches `GET /robot/manifest` | TRUE | S01 (full snapshots of `manifest_of`), before S02 and S27a |
| G11 | low | the "one mutation per moved table" is not there yet | TRUE (10 mutations in `tools/golden_mutation_check.py:94-106`) | S01 (the list in its section) |
| G12 | low | determinism: no problem; `VOLATILE` keys are dropped at any depth | nothing to fix; the rule is written into `docs/testing.md` | S01 |

Found by this check, not in the review (each is now an invariant):

- `tests/test_sage_v2_r3_m10_round1b.py:73` reads the SOURCE of `AssistLoop._finish`: it must move as a real method (invariant 15).
- `tests/test_lang_inventory.py:80-86` allows a non-Latin literal in three files only: the understand prompt cannot move into a phase
  module (invariant 16). The same test caps the language-literal rows of the package at 70 (61 today, measured).
- `tests/test_step_ladder.py:96-106` wants `speech.HELP_PICK` and `speech.HELP_MOVE` to exist and the sentences not to be in
  `step_ladder.py` (invariant 17).
- `tests/test_sage_lab_r6_langdata.py:50-61` scans `robot_agent`, `tests` and `docs` (`.py`, `.json`, `.md`, `.yaml`) for text of one
  language pack that only its data file may hold: new files of every step fall under it.
- `robot_agent/contract/check_manifest.py` is contract layer and may not import the engine: it can validate the `pack:` section, not
  resolve the pack. The resolved pack is printed by an engine tool (S02).
- `CHANGELOG.md`, the docs indexes and `.dev-principles*.json` would be edited by every step: they are written by the integrator of a wave.

## Migration steps

The plan as data is `docs/proposals/generalize_core_plan.json` (waves, steps, files, needs, model, size effect). The wave table, the
"parallel with" of every step and the table of shared files below are GENERATED from that data, and a script checks it: at most four
steps in a wave; no file in two steps of one wave; exactly one step of a wave touches `assist_loop.py` and `tools/arch_baseline.json`;
every `needs` is in an earlier wave; no step has a positive size effect; a step touches `assist_loop.py` exactly when it makes it
smaller. The check passed for the plan below (35 steps, 23 waves).

### How the plan is file-disjoint

The first version named five streams and called steps of different streams parallel, while three of them wrote the same
`pack.yaml` (review finding 4). The rule is now about files, not streams:

- The standard pack is one file per section: `robot_agent/packs/household/pack.yaml` holds the header only, and the loader reads
  `<section>.yaml` next to it (`goal_kinds`, `predicates`, `actions`, `verifiers`, `recovery`, `questions`, `phrases`, `constants`, `fleet`,
  `trees`); a missing file is an empty section. `callables/__init__.py` imports every sibling module it finds. So a step ADDS its section
  file and its callables module and edits no shared file. Step S02 writes the header, the loader and the two `__init__.py` once.
- The schema (`robot_agent/contract/pack.py`) is written whole in S02, also the fields that later opt-in steps start to READ. No later
  step edits it.
- Every step that adds scenarios, cassettes or mutations writes its own files: `tests/golden/scenario_sets/<step>_*.py`,
  `tests/golden/set_cassettes/<step>_*.cassette.json`, `tests/golden/commits/<step>.json`, `tools/golden_mutations/<step>_*.py` (S01 makes
  the record tool and the mutation tool load these folders; the entries that exist keep their order). The commits file names the commits
  the step's cassettes were recorded on and their `kind`: `equivalence` (the old `robot_agent/` tree, checked against its `old_code`) or
  `feature` (recorded after a production commit of the step: no tree check). `tests/golden/harness.py` and the 21 round-A cassettes in
  `tests/golden/cassettes` are never edited; a set cassette replays through `tests/golden/harness_sets.py`.
- A schema-2 test pack is NOT a shared folder: a scenario carries its own `pack:` section (the scenario key `pack`), so one feature's
  cassette cannot change when another feature is added.
- A file that several steps must touch is touched in different waves, in the order of the table below.
- `assist_loop.py` is touched by one step per wave. That makes the moves sequential: 22 of the 23 waves have such a step.

Streams are only a label of the kind of work: GOLD (goldens), PACK (`contract/pack.py`, `core/pack/`, `packs/`), LOOP (a step that
touches `assist_loop.py`), OPT (`options/`), FLEET (the manifest answer and the robot_fleet repository).

Models (owner's decision 9; the skill's table): `sonnet` for a verbatim move or a data move with an equality test; `opus` for a step of
high risk or one that later steps rely on blindly (S01, S03, S05, S07, S12, S14, S16b, S22, S24, S26, S29); `fable` for S02 only: the
pack schema is the one decision of round B that is hard to undo, because every later step and every robot pack reads it.

Every step ends with the same report (the skill's equivalence report): the golden replay (no difference), the mutation check, the full
suite, the lint, `wc -l` of `assist_loop.py` before and after, the wiring and vocabulary counts before and after, what moved where. Any
difference in a replay stops the run (owner's decision 8). A step that names a cassette of S01 does not start when that cassette could
not be recorded.

Sizes are estimates from the inventory's phase map (spans; about 10 percent uncertain). The rule is the sign, not the number.

### Waves

| wave | steps (stream, model) | `assist_loop.py` step | lines after (estimate) |
|---|---|---|---:|
| W0 | S01 (GOLD, opus) | none | 4081 |
| W1 | S02 (PACK, fable); S03 (LOOP, opus) | S03 (-191) | 3890 |
| W2 | S04 (PACK, sonnet); S05 (LOOP, opus); S06 (OPT, sonnet) | S05 (-94) | 3796 |
| W3 | S07 (LOOP, opus); S25a (OPT, sonnet); S31 (PACK, sonnet) | S07 (-6) | 3790 |
| W4 | S10 (LOOP, sonnet); S30 (PACK, sonnet); S27a (FLEET, sonnet) | S10 (-2) | 3788 |
| W5 | S23a (LOOP, sonnet); S32 (FLEET, sonnet) | S23a (-90) | 3698 |
| W6 | S12 (LOOP, opus); S26 (PACK, opus); S27b (FLEET, sonnet) | S12 (-165) | 3533 |
| W7 | S08 (LOOP, sonnet); S25 (OPT, sonnet) | S08 (-40) | 3493 |
| W8 | S13 (LOOP, sonnet); S29 (PACK, opus) | S13 (-254) | 3239 |
| W9 | S14 (LOOP, opus) | S14 (-170) | 3069 |
| W10 | S16b (LOOP, opus) | S16b (-26) | 3043 |
| W11 | S09 (LOOP, sonnet) | S09 (-188) | 2855 |
| W12 | S11 (LOOP, sonnet) | S11 (-160) | 2695 |
| W13 | S15 (LOOP, sonnet) | S15 (-176) | 2519 |
| W14 | S16a (LOOP, sonnet) | S16a (-270) | 2249 |
| W15 | S17 (LOOP, sonnet) | S17 (-260) | 1989 |
| W16 | S18 (LOOP, sonnet) | S18 (-260) | 1729 |
| W17 | S19 (LOOP, sonnet) | S19 (-240) | 1489 |
| W18 | S20 (LOOP, sonnet) | S20 (-287) | 1202 |
| W19 | S21 (LOOP, sonnet) | S21 (-255) | 947 |
| W20 | S22 (LOOP, opus) | S22 (-266) | 681 |
| W21 | S23b (LOOP, sonnet) | S23b (-85) | 596 |
| W22 | S24 (LOOP, opus) | S24 (-230) | 366 |

Files that more than one step touches (never in the same wave; `assist_loop.py`, `tools/arch_baseline.json` and `phases/__init__.py` are
touched by every LOOP step and are left out of the table):

| file | steps that touch it, in wave order |
|---|---|
| `docs/bt_goal.md` | S25a (W3), S25 (W7) |
| `robot_agent/core/pack/goal_semantics.py` | S05 (W2), S08 (W7), S29 (W8) |
| `robot_agent/core/pack/loader.py` | S02 (W1), S30 (W4) |
| `robot_agent/core/pack/report.py` | S27a (W4), S32 (W5) |
| `robot_agent/core/pack/verifiers.py` | S12 (W6), S29 (W8) |
| `robot_agent/core/planning/loop.py` | S31 (W3), S26 (W6) |
| `robot_agent/core/planning/phases/exec_helpers.py` | S23a (W5), S26 (W6) |
| `robot_agent/core/planning/phases/verify_goals.py` | S12 (W6), S29 (W8) |
| `robot_agent/core/planning/robot.py` | S02 (W1), S10 (W4) |
| `robot_agent/core/planning/tree_host.py` | S25a (W3), S25 (W7) |
| `robot_agent/options/bt_goal.py` | S06 (W2), S25a (W3), S25 (W7) |
| `robot_agent/packs/household/callables/goals.py` | S05 (W2), S08 (W7) |
| `robot_agent/packs/household/goal_kinds.yaml` | S05 (W2), S07 (W3), S12 (W6) |
| `robot_agent/packs/household/phrases.yaml` | S04 (W2), S16b (W10) |

### What Step S01 must record (wave W0, on the old code)

The 21 cassettes of today use one made-up robot (`fakebot`), English, `on` and `in` goals. S01 extends the harness by a module that wraps
it (`tests/golden/harness_sets.py`; `harness.py` itself is never edited) and records, on `generalize-core` at `65124d0` before any production commit:

| needed by | new cassettes (equivalence, old code) | what they reach |
|---|---|---|
| S05, S07, S08, S12 | `template_go`, `close_goal`, `open_goal`, `readback_kinds` (a read-back of an `in`, an `open` and a `go` goal), `template_say_deliver` (`fakeline`), `handover` (`fakehome`), `given_goals_unknown_kind` | `goals.reached`, the template and open / close branches, the `say` and `deliver` prompt lines, today's vacuous result of an unknown kind |
| S04, S08, S15, S18 | `non_english` (Korean, with scripted answers for the translator prompts), `two_destinations` | `_in_lang`, the language pack's phrases, the validator's kind-less question |
| S15, S19 | `unclear_answer` (silence, then a near-miss word), `readback_correction` ("no, the other table"), `menu_retry_place` (the tree menu answered with a retry word and with a place) | the silence and correction branches of `yes_no`, `read_menu` |
| S06 | `bt_go_tree`, `bt_drawer_conditions` (`at`, `opened`, `closed`), `effect_untracked_predicate` (`fakeline`: an effect the belief does not track), `effect_on_predicate` | the tree conditions and effect predicates no cassette evaluates today |
| S13, S14 | `move_help` (the MoveTo ask-help rung outside a tree), `drop_refind` (a `floor` place), `search_legs`, `custom_action` (`fakeline`: a custom action fails and goes to the planner; a tree with an unknown condition falls to the planner) | the ladders that are reached only inside a tree today |
| S12 | `goal_absent` | the end check that does not see the object |
| S10, S11, S20, S21 | `gate_findings` (three findings in the reason text of a planner call), `planner_visible` (`capture: planner_io`), `real_reconcile` (`capture: reconcile`: the run-start reconcile with the pack's name map), `goal_change`, `new_task_first` | the planner's inputs, the reconcile path, the task stack |
| S16a, S22, S24 | `llm_roles` (`capture: llm_kw`), `pause_resume`, `coverage`, `env_override` (one `ROBOT_AGENT_*` variable), `hooks_order` (option `trace`), `hazard_default` | the sampling options and role clients, the refind after a pause, the environment precedence, the order of the hooks |

New recorded data, opt-in per scenario (`capture`), so that the old cassettes replay as they are and `FORMAT` stays 1:

- `prompts`: the hash and head of `robot_prompt.sage_prompt_kwargs(robot)`, of the direct planner's prompt (`direct_prompt.build_prompt`) and
  of `_planner_cfg()`. At least one cassette each for `fakebot`, `fakehome` and `fakeline` has it. This is the planner-prompt hash section:
  today the harness replaces the planner, so `ORDER` and `ACTION_TEXT` are in no golden.
- `planner_io`: the hash of the observation and the visible list in every planner call. `llm_kw`: the hash of the call's options and the
  role of the client. `reconcile`: the run-start reconcile is not stubbed.
- The set harness clears every `ROBOT_AGENT_*` variable before a run of a set and applies the scenario's `environ`; a set cassette
  carries the commit it was recorded on, `tests/golden/commits/<step>.json` lists the commits of each step with their kind, and a new
  test compares them (the `robot_agent/` tree of an `equivalence` commit equals its `old_code`).

Goldens that are not cassettes, also recorded on the old code:

- a full snapshot of `manifest_of` for `fakebot`, `fakehome`, `fakeline` (every key and value), so "additive only" can be proven (S02, S27a);
- the resolved config of the loop for the three made-up packs (every key), for S24;
- a replay smoke test: `robot_agent/observe/replay.py` on one recorded assist run of a made-up pack, for S24 and invariant 14;
- a test that freezes the hook table (names, kinds, flow flags), with `hooks_order`.

Mutations added (kill criteria: S01 is done only when each is CAUGHT; the 18 that the golden reviewer found uncaught, and those of this
check): `template_always_reached`, `non_manip_goal_seen`, `in_needs_no_pick`, `deliver_prompt_changed`, `readback_in_phrase`, `rb_go_phrase`,
`silence_is_yes`, `correction_rest_lost`, `menu_retry_unheard`, `say_not_translated`, `korean_phrases_lost`, `help_move_changed`,
`bt_opened_inverted`, `bt_at_inverted`, `bt_placed_ignores_hand`, `effect_unknown_pred_fails`, `planner_obs_empty`,
`unknown_kind_not_reached`, `on_goal_without_place`, `sage_prompt_changed`, `place_arg_set_changed`, `ladder_choice` (a failed Pick goes
straight to the planner), `end_check_trusts_belief`. `manifest_drops_capabilities` is caught by the snapshot test, not by a cassette.

Also in S01: the made-up pack `tests/fakeline/` (NOT a household robot: Say, WaitForUnload, two custom actions, one effect predicate the
belief does not track; schema 1, because the old code must load it), and the empty package `tests/legacy_ref/` for the reference copies
of moved functions (never imported by `robot_agent`).

### The steps, by wave

### Wave W0: S01

#### Step S01: Extend the goldens on the old code (harness capture sections, made-up pack fakeline, cassettes, mutations, snapshots)

- Stream and model: GOLD, opus. Needs: nothing. Parallel with (same wave, no common file): none.
- Files: `tests/golden/harness_sets.py` (`tests/golden/harness.py` is NOT edited), `tests/golden/scenario_sets/__init__.py`, `tests/golden/scenario_sets/s01_*.py`, `tests/golden/set_cassettes/*.cassette.json`, `tests/golden/test_golden_set_cassettes.py`, `tests/golden/commits/s01.json`, `tests/golden/snapshots.py`, `tests/golden/test_golden_order.py`, `tests/golden/test_planner_prompt_golden.py`, `tests/golden/prompt_snapshots/s01_*.json`, `tests/golden/manifest_snapshots/s01_*.json`, `tests/test_manifest_snapshot.py`, `tests/golden/config_snapshots/s01_*.json`, `tests/test_loop_config_snapshot.py`, `tests/golden/replay_runs/s01_*`, `tests/test_replay_smoke.py`, `tests/test_hooks_frozen.py`, `tests/fakeline/**`, `tests/legacy_ref/__init__.py`, `tests/legacy_ref/README.md`, `tools/golden_mutation_check.py`, `tools/golden_mutations/s01_*.py`, `tools/golden_record.py`, `docs/testing.md`.
- Golden coverage: the step IS the coverage.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: low for the product, real for the schedule.
- Notes: Wave 0, alone. Cassettes: NEW files only, the 21 that exist are never touched. Every equivalence cassette is recorded on generalize-core at 65124d0 (no production commit merged) and carries that commit; tests/golden/commits/s01.json lists it (kind equivalence) and a new test checks it. harness.py and the 21 round-A cassettes stay byte-identical to 65124d0 (fix round 1, 2026-11-23: the first S01 merge had added code to harness.py, which broke the rule that no existing test is modified); the additions live in tests/golden/harness_sets.py, which wraps harness.py, and the set cassettes live in tests/golden/set_cassettes, replayed by tests/golden/test_golden_set_cassettes.py (FORMAT stays 1: tests/golden/test_golden_cassettes.py:24 compares every round-A cassette with harness.FORMAT): a scenario key `capture` (planner_io, llm_kw, prompts, reconcile, trace), a key `environ`, a key `pack` (calls robot.use_pack, which Step S02 adds), ROBOT_AGENT_* cleared before every run of a set, scenario sets and mutation sets loaded from one file per step. Order inside the step: harness, fakeline, the cassettes in the order of the waves that need them, the mutations. Done only when the 21 old cassettes replay identically, every new mutation is CAUGHT and the full suite is green. A path the fakes cannot play is listed in the report and the step that needs it is not started.

### Wave W1: S02, S03

#### Step S02: The pack schema (whole), the loader with the schema-1 lift, the registry, the empty core and household packs

- Stream and model: PACK, fable. Needs: S01. Parallel with (same wave, no common file): S03.
- Files: `robot_agent/contract/pack.py`, `robot_agent/contract/manifest.py`, `robot_agent/core/pack/__init__.py`, `robot_agent/core/pack/loader.py`, `robot_agent/core/pack/registry.py`, `robot_agent/core/pack/show.py`, `robot_agent/packs/__init__.py`, `robot_agent/packs/index.yaml`, `robot_agent/packs/core/pack.yaml`, `robot_agent/packs/core/callables/__init__.py`, `robot_agent/packs/household/pack.yaml`, `robot_agent/packs/household/callables/__init__.py`, `robot_agent/core/planning/robot.py`, `tools/arch_rules.json`, `pyproject.toml`, `tools/pack_compat_check.py`, `tests/test_pack_schema.py`, `tests/test_pack_loader.py`.
- Golden coverage: all cassettes (nothing reads the pack yet: identical replay shows the loader has no side effect); manifest snapshots of S01.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: medium; the one hard-to-undo decision (the schema is a contract).
- Notes: contract/pack.py parses and validates the WHOLE schema now (holds lists, unknown_is, remove, questions_default_kind, fleet), so no later step edits it. The loader reads one file per section (`<section>.yaml` next to pack.yaml, a missing file = an empty section) and callables/__init__.py imports every sibling module it finds: a later step adds ITS file and edits no shared one. pyproject package data covers packs/**/*.yaml and packs/**/prompts/*.txt. RobotView gains `pack` and `use_pack(raw)`. Nothing pack-derived may be cached in AssistLoop.__init__. check_manifest (contract layer) validates the section's schema only; the resolved pack is printed by `python -m robot_agent.core.pack.show`. The goal-kind schema also has the field `subgoal` (the kind's sub-goal phrase for a planner that takes `subgoals`; review of S01, 2026-11-23); S07 fills the household text.

#### Step S03: Places, Memory, words move to places.py; the phases package; the lint guards follow the code

- Stream and model: LOOP, opus. Needs: S01. Parallel with (same wave, no common file): S02.
- Files: `robot_agent/core/planning/places.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/lint_arch.py`, `tools/arch_baseline.json`, `tests/test_lint_loop_guards.py`, `tests/legacy_ref/places.py`, `tests/test_places_diff.py`.
- Golden coverage: NOT the cassettes alone (review of S01, 2026-11-23: eight source mutations of words / Places / Memory, assist_loop.py:107-291, passed every cassette and snapshot; three passed the whole suite). Proof: tests/test_places_memory_pins.py (mutations article_my_your_kept, one_word_fallback_lost, answers_window_nine) and, in S03's first commit, tests/test_places_diff.py: the moved words / Places / Memory against the reference copy tests/legacy_ref/places.py over a table of names and hints (a one-word match, a name with "my", nine remembered answers). The cassettes (every run builds Places; tags question, goals_only) are a smoke check only.
- Effect on `assist_loop.py`: -191 lines (to about 3890). Risk: low for the move; the guards are what every later step relies on.
- Notes: Two commits. (1) Verbatim move; assist_loop.py re-exports Places, Memory, words; phases/__init__.py holds PHASE_MIXINS (an empty tuple, one entry per line later) and the class line becomes AssistLoop(*PHASE_MIXINS, WhichOne, ...). (2) tools/lint_arch.py: LOOP becomes the set {assist_loop.py, phases/*.py, places.py, loop_config.py, find_ladder.py, core/pack/*.py} (files that exist), the wiring ratchet counts the SUM over the set (62 keys, 59 branches today), the registered-option-key scan, a new embodiplan-internals scan and a new language-literal scan read the same set (new layers core/pack and packs: 0 rows), and a new vocabulary ratchet counts the quoted standard action names and goal kinds in the set (66 and 59 lines today). Function signatures of lint.wiring / check_wiring / run stay; no existing test is edited; the new checks get their own test file.

### Wave W2: S04, S05, S06

#### Step S04: The phrase tables become pack data (core and household phrase files; CORE_PHRASES is a view)

- Stream and model: PACK, sonnet. Needs: S02. Parallel with (same wave, no common file): S05, S06.
- Files: `robot_agent/core/planning/speech.py`, `robot_agent/core/planning/budget.py`, `robot_agent/packs/core/phrases.yaml`, `robot_agent/packs/household/phrases.yaml`, `robot_agent/core/pack/phrases.py`, `tests/test_pack_phrases.py`.
- Golden coverage: question, success, failure, refused, stop; S01: non_english, readback_kinds, move_help; mutations korean_phrases_lost, help_move_changed.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: medium (YAML quoting; the lookup order).
- Notes: 62 + 3 texts, character for character; the equality test (union of the two files = CORE_PHRASES, no key twice) is committed before the constant goes. speech.HELP_PICK and speech.HELP_MOVE stay attributes of speech (tests/test_step_ladder.py:96-106). The pack chain is read AFTER the language pack's speech block. CORE_TEXT of robot.py is NOT moved in this step (robot.py belongs to S02 and S10).

#### Step S05: The goal-reach rules are chosen by kind (one rules object per household kind)

- Stream and model: LOOP, opus. Needs: S02, S03. Parallel with (same wave, no common file): S04, S06.
- Files: `robot_agent/core/planning/assist_loop.py`, `robot_agent/core/pack/goal_semantics.py`, `robot_agent/packs/household/callables/goals.py`, `robot_agent/packs/household/goal_kinds.yaml`, `tests/legacy_ref/goal_reach.py`, `tests/test_goal_semantics_diff.py`, `tools/arch_baseline.json`.
- Golden coverage: success, replan, gate, bt_goal, failure, plan_only; S01: template_go, template_say_deliver, close_goal, open_goal, handover, given_goals_unknown_kind; mutations template_always_reached, unknown_kind_not_reached, on_goal_without_place.
- Effect on `assist_loop.py`: -94 lines (to about 3796). Risk: medium (15 callers; the unknown-kind default).
- Notes: An UNKNOWN kind keeps today's result: vacuously reached and holding, no missing text (assist_loop.py:1504-1543); the differential grid has that row. _reaches, _goals_hold, _missing, _on_reached, _simulate_goals, _releases stay class attributes that delegate (__init__ wraps _reaches; tests call it through the class).

#### Step S06: One predicate table for tree conditions and effect checks

- Stream and model: OPT, sonnet. Needs: S02. Parallel with (same wave, no common file): S04, S05.
- Files: `robot_agent/packs/household/predicates.yaml`, `robot_agent/packs/household/callables/predicates.py`, `robot_agent/core/pack/predicates.py`, `robot_agent/options/bt_goal.py`, `robot_agent/core/planning/verify.py`, `tests/legacy_ref/predicates.py`, `tests/test_pack_predicates.py`, `tests/golden/scenario_sets/s06_*.py`, `tests/golden/set_cassettes/s06_*.cassette.json`, `tests/golden/commits/s06.json`.
- Golden coverage: bt_goal (6), success, replan; S01: bt_go_tree, bt_drawer_conditions, effect_untracked_predicate, effect_on_predicate, custom_action; mutations bt_opened_inverted, bt_at_inverted, bt_placed_ignores_hand, effect_unknown_pred_fails; review of S01 (2026-11-23): tests/test_tree_condition_pins.py, mutations hand_free_always_true, holding_any_object (only these pins catch them today: no tree run evaluates hand_free with a full hand or holding o while another object is held). S06's own scenario set adds those two tree runs, with these two mutations as kill criteria.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: medium (the two old evaluators differ on purpose).
- Notes: Each consumer keeps its own rule for a wrong arity and for 'cannot tell'. bt_goal.py is 367 lines of 400: it must not grow past its limit (helpers go to core/pack/predicates.py).

### Wave W3: S07, S25a, S31

#### Step S07: The goal-kind table is pack data (needs, prompt line, understood, planned_by, conflicts flag)

- Stream and model: LOOP, opus. Needs: S05. Parallel with (same wave, no common file): S25a, S31.
- Files: `robot_agent/packs/household/goal_kinds.yaml`, `robot_agent/core/planning/goals.py`, `robot_agent/core/planning/assist_loop.py`, `tests/legacy_ref/goal_tables.py`, `tests/test_pack_goal_kinds.py`, `tools/arch_baseline.json`.
- Golden coverage: every cassette with an understand call (prompt hash), goals_only, refused, plan_only; S01: template_go, template_say_deliver (the say / deliver lines appear only for fakeline), given_goals_unknown_kind, two_destinations; mutations in_needs_no_pick, deliver_prompt_changed; S01b: subgoals_in_on_go, subgoals_close_replan (the sub-goal list a planner that takes `subgoals` gets), tests/test_subgoal_phrases.py; mutation subgoal_into_in.
- Effect on `assist_loop.py`: -6 lines (to about 3790). Risk: high for the prompt (kind lines and their order are in every understand prompt).
- Notes: needs_when (say + MoveTo), canonical (give -> on), the optional kind `give`, compile_steps and the conflict validator STAY code in goals.py (one user each); the only new switch is `conflicts: none` per kind. goal_fix.py and plan_repair.py are not touched. The per-kind sub-goal phrase (`goals.subgoal_text`, goals.py:174-190: on, in, open / close, go, say, deliver; describe() for any other kind) becomes the kind's `subgoal` field, the household text character for character; an equality test over tests/test_subgoal_phrases.py's rows is committed before the if-chain goes.

#### Step S25a: bt_goal uses the seams that exist: tree keys `ladder: false` and `events: true`, word lists from the pack

- Stream and model: OPT, sonnet. Needs: S02, S06. Parallel with (same wave, no common file): S07, S31.
- Files: `robot_agent/options/bt_goal.py`, `robot_agent/core/planning/tree_host.py`, `docs/bt_goal.md`, `tests/test_option_bt_goal_seams.py`, `tests/golden/scenario_sets/s25a_*.py`, `tests/golden/set_cassettes/s25a_*.cassette.json`, `tests/golden/commits/s25a.json`, `tools/golden_mutations/s25a_*.py`.
- Golden coverage: bt_goal (6 cassettes: today's trees run the same); S01: menu_retry_place.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: low (opt-in keys of a tree entry).
- Notes: `ladder: false` calls try_step(ladder=False) (option_services.py:74-79); `events: true` sends each node through svc.emit as a `tree_node` event (services.py:19); pack_words also reads the choice words of a pack question. Feature cassettes (fakeline with a scenario `pack`): a tree whose failed step does not run the engine's ladder, a tree with node events, a tree that branches on a choice answer. A tree with none of the keys runs as today.

#### Step S31: Opt-in: questions_default_kind gives a kind-less clarify event the declared kind

- Stream and model: PACK, sonnet. Needs: S02. Parallel with (same wave, no common file): S07, S25a.
- Files: `robot_agent/core/planning/loop.py`, `robot_agent/core/pack/questions.py`, `tests/test_pack_question_kinds.py`, `tests/golden/scenario_sets/s31_*.py`, `tests/golden/set_cassettes/s31_*.cassette.json`, `tests/golden/commits/s31.json`, `tools/golden_mutations/s31_*.py`.
- Golden coverage: all (no schema-1 pack sets the key: every replay identical); S01: two_destinations, search_legs (kind-less questions).
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: low (one place: RunContext.ev).
- Notes: Applied where every event is built (loop.py RunContext.ev): a `clarify` with no `kind` gets the pack's literal value. The value `name` (each question its own name) needs the 28-row question table and is deferred. Feature cassette: fakeline with the key set, the two-destinations question carries the kind.

### Wave W4: S10, S30, S27a

#### Step S10: The action table (class, argument kind, words): the copies read the pack

- Stream and model: LOOP, sonnet. Needs: S02, S04, S07. Parallel with (same wave, no common file): S30, S27a.
- Files: `robot_agent/packs/household/actions.yaml`, `robot_agent/core/pack/actions.py`, `robot_agent/core/planning/assist_loop.py`, `robot_agent/core/planning/reconcile.py`, `robot_agent/core/planning/mapper.py`, `robot_agent/core/planning/search.py`, `robot_agent/contract/legacy_adapter.py`, `robot_agent/core/planning/robot.py`, `robot_agent/core/planning/robot_prompt.py`, `robot_agent/core/planning/announcer.py`, `robot_agent/core/planning/plan_repair.py`, `tests/test_pack_actions.py`, `tools/arch_baseline.json`.
- Golden coverage: all; S01: the `prompts` capture (hash of sage_prompt_kwargs and of the direct planner's prompt), real_reconcile; mutations sage_prompt_changed, place_arg_set_changed. The planner prompt is NOT in the 21 old cassettes: the harness replaces the planner.
- Effect on `assist_loop.py`: -2 lines (to about 3788). Risk: medium (many small files; five place-argument sets that differ).
- Notes: Each call site keeps ITS set (the legacy adapter's has three names); no set is unified. legacy_adapter.py is contract layer: it gets the set as an argument. CORE_TEXT of robot.py moves here with _ABILITY and NOT_PLAN_STEPS.

#### Step S30: Opt-in: the merge semantics of `extends` and `remove` take effect in a run

- Stream and model: PACK, sonnet. Needs: S07. Parallel with (same wave, no common file): S10, S27a.
- Files: `robot_agent/core/pack/loader.py`, `tests/test_pack_merge.py`, `tests/golden/scenario_sets/s30_*.py`, `tests/golden/set_cassettes/s30_*.cassette.json`, `tests/golden/commits/s30.json`, `tools/golden_mutations/s30_*.py`.
- Golden coverage: all (schema 1 has no chain of its own); manifest snapshots of S01.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: low.
- Notes: An entry of a later pack replaces the base entry whole; `remove` deletes names; a removed kind leaves the understand prompt and the schema enum. Feature cassette: fakeline with `remove: {goal_kinds: [open, close]}` and one replaced kind: the prompt hash differs from the base cassette exactly by those lines (the cassette keeps the prompt head).

#### Step S27a: GET /robot/manifest reports the pack, the goal kinds with their needs, and the question kinds

- Stream and model: FLEET, sonnet. Needs: S07, S31. Parallel with (same wave, no common file): S10, S30.
- Files: `robot_agent/api/robot_info.py`, `robot_agent/api/models.py`, `robot_agent/core/pack/report.py`, `docs/fleet_seams.md`, `tests/test_robot_info_pack.py`, `tests/golden/manifest_snapshots/s27a_*.json`.
- Golden coverage: no loop cassette reaches the endpoint; S01: the manifest snapshots of fakebot, fakehome, fakeline: tests/test_manifest_snapshot.py compares the recorded top-level keys only (old keys and values unchanged, an added key passes); S27a pins its added keys in its own snapshot set.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: low (additive fields).
- Notes: New keys only: pack, goal_kinds {feasible, needs, slots}, question_kinds. The old snapshot keys and values must be equal. The added keys are pinned by `python tools/golden_record.py --snapshots --prefix s27a` (tests/golden/snapshots.py writes `<kind>_snapshots/s27a_<pack>.json`, never overwriting) and a new test tests/test_robot_info_pack.py compares them; tests/test_manifest_snapshot.py is not edited.

### Wave W5: S23a, S32

#### Step S23a: Execution helpers move to phases/exec_helpers.py

- Stream and model: LOOP, sonnet. Needs: S03. Parallel with (same wave, no common file): S32.
- Files: `robot_agent/core/planning/phases/exec_helpers.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: all cassettes that move the robot (every step binds).
- Effect on `assist_loop.py`: -90 lines (to about 3698). Risk: low to medium (_bind is called through the class).
- Notes: Verbatim mixin move of _bind, _params, _holding, _base_pose, _dist_to_place, _found_point, _place_of_far_point. _effect STAYS in assist_loop.py (it calls current(), which tests and replay.py patch on that module).

#### Step S32: Opt-in: the pack `fleet:` section (footprint per goal kind) is resolved and reported

- Stream and model: FLEET, sonnet. Needs: S27a. Parallel with (same wave, no common file): S23a.
- Files: `robot_agent/core/pack/fleet.py`, `robot_agent/core/pack/report.py`, `robot_agent/packs/household/fleet.yaml`, `tests/test_pack_fleet.py`, `tests/golden/manifest_snapshots/s32_*.json`.
- Golden coverage: manifest snapshots (S01, S27a): a pack with no fleet section reports the default footprint (objects = object, places = from, to).
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: low.
- Notes: The golden of this step is an endpoint snapshot (fakeline with a scenario pack that declares a footprint), because no loop run reaches the endpoint. `carries` is dropped (one possible value).

### Wave W6: S12, S26, S27b

#### Step S12: Verifiers: the post-check and the end-of-run observation move out and are chosen by name

- Stream and model: LOOP, opus. Needs: S05, S07, S23a. Parallel with (same wave, no common file): S26, S27b.
- Files: `robot_agent/core/planning/phases/verify_goals.py`, `robot_agent/core/pack/verifiers.py`, `robot_agent/packs/household/callables/verifiers.py`, `robot_agent/packs/household/verifiers.yaml`, `robot_agent/packs/household/goal_kinds.yaml`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tests/test_pack_verifiers.py`, `tools/arch_baseline.json`.
- Golden coverage: success (goal_check events), failure (dropped_on_the_way), bt_goal; S01: goal_absent, handover, template_go, close_goal; mutations non_manip_goal_seen, end_check_trusts_belief.
- Effect on `assist_loop.py`: -165 lines (to about 3533). Risk: high (this is where 'done' is decided).
- Notes: observe: std.find_at_destination for on / in, null for the others (today's sentence 'no sensor declared for it (belief)'). Hooks on_goal_check, on_second_look, on_final, on_goals_met stay at the same points; _observe_goal stays callable through the class.

#### Step S26: Opt-in: declared preconditions and effects of pack actions, the fact store, typed arguments at bind

- Stream and model: PACK, opus. Needs: S06, S10, S23a, S31. Parallel with (same wave, no common file): S12, S27b.
- Files: `robot_agent/core/pack/action_rules.py`, `robot_agent/core/planning/action_model.py`, `robot_agent/core/planning/base.py`, `robot_agent/core/planning/phases/exec_helpers.py`, `robot_agent/core/planning/loop.py`, `tests/test_pack_action_rules.py`, `tests/test_world_facts.py`, `tests/golden/scenario_sets/s26_*.py`, `tests/golden/set_cassettes/s26_*.cassette.json`, `tests/golden/commits/s26.json`, `tools/golden_mutations/s26_*.py`.
- Golden coverage: all (no schema-1 pack declares rules: identical replay, and no event's `world` has a new key).
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: high (the belief is in every event).
- Notes: Facts: reset at run start (a `persistent` flag is deferred), written into to_dict() only when not empty, accepted by update_from_dict (PUT /agent/world {facts: {}} clears them), not in as_text(). Feature cassettes (fakeline): a custom action whose precondition fails at the gate and at the guard; an effect that makes a fact true; typed args reach the skill.

#### Step S27b: robot_fleet reads goal kinds, needs, footprint and question kinds from the manifest

- Stream and model: FLEET, sonnet. Needs: S32. Parallel with (same wave, no common file): S12, S26.
- Files: `robot_fleet:robot_fleet/models.py`, `robot_fleet:robot_fleet/spoke.py`, `robot_fleet:robot_fleet/scheduler.py`, `robot_fleet:robot_fleet/graph.py`, `robot_fleet:robot_fleet/compat/needs_v1.json`, `robot_fleet:tests/test_manifest_needs.py`, `robot_fleet:CHANGELOG.md`.
- Golden coverage: the fleet's own suite on its master (257 test functions at 0dc947c); new: needs_v1.json equals _NEEDS; a robot that reports `install` gets an `install` goal; two robots that declare one kind with different footprints.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: medium (another repository; other people's uncommitted files there).
- Notes: Own worktree of robot_fleet from master 0dc947c; demos/* and tracked .pyc files of other people are never touched. The footprint of a kind is fleet-wide: equal reports are used, a disagreement between two robots is an error at registration, a kind nobody reports keeps graph.py's rule. Merge into the fleet's LOCAL master only when its tests are green; no push.

### Wave W7: S08, S25

#### Step S08: The read-back phrase is chosen by kind

- Stream and model: LOOP, sonnet. Needs: S04, S05. Parallel with (same wave, no common file): S25.
- Files: `robot_agent/packs/household/callables/goals.py`, `robot_agent/core/pack/goal_semantics.py`, `robot_agent/core/planning/assist_loop.py`, `tests/legacy_ref/goal_phrase.py`, `tests/test_goal_phrase_diff.py`, `tools/arch_baseline.json`.
- Golden coverage: question; S01: readback_kinds, handover, non_english, template_go; mutations readback_in_phrase, rb_go_phrase.
- Effect on `assist_loop.py`: -40 lines (to about 3493). Risk: low to medium (LocalText: key, slots and text must all be equal).
- Notes: summary and _goal_phrase stay static names on the class that delegate. An unknown kind keeps today's plain text '<kind> the <object>'.

#### Step S25: Opt-in: typed `act` arguments in a tree, `[ask, <pack question>]`, the answer as a fact

- Stream and model: OPT, sonnet. Needs: S25a, S26. Parallel with (same wave, no common file): S08.
- Files: `robot_agent/options/bt_goal.py`, `robot_agent/core/planning/behavior_tree.py`, `robot_agent/core/planning/tree_host.py`, `docs/bt_goal.md`, `tests/test_option_bt_goal_typed.py`, `tests/golden/scenario_sets/s25_*.py`, `tests/golden/set_cassettes/s25_*.cassette.json`, `tests/golden/commits/s25.json`, `tools/golden_mutations/s25_*.py`.
- Golden coverage: bt_goal (6) and the S25a cassettes: unchanged.
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: medium (bt_goal.py is near its size limit).
- Notes: An optional mapping as the last argument of `act`; the answer of a pack question is kept as answered(<question>, <choice>) and its raw text with keep_text. Feature cassettes (fakeline): a typed argument reaches the skill; a tree branches on the content of an answer.

### Wave W8: S13, S29

#### Step S13: The Find ladder moves verbatim to find_ladder.py

- Stream and model: LOOP, sonnet. Needs: S03. Parallel with (same wave, no common file): S29.
- Files: `robot_agent/core/planning/find_ladder.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: replan (find_miss_replan), bt_goal (absent_sage_replan, stop_word); S01: search_legs.
- Effect on `assist_loop.py`: -254 lines (to about 3239). Risk: medium (8 outward calls, 11 attributes).
- Notes: Verbatim mixin move (_recover_find to _refuted), SECOND_LOOKS with it. Waits if search_legs could not be recorded.

#### Step S29: Opt-in: a declared goal kind without a rules callable: holds list, reached by simulation, unknown_is, phrase key, planned_by tree

- Stream and model: PACK, opus. Needs: S04, S05, S06, S07, S08, S12, S25a, S26. Parallel with (same wave, no common file): S13.
- Files: `robot_agent/core/pack/goal_semantics.py`, `robot_agent/core/pack/verifiers.py`, `robot_agent/core/planning/phases/verify_goals.py`, `tests/test_pack_declared_kinds.py`, `tests/golden/scenario_sets/s29_*.py`, `tests/golden/set_cassettes/s29_*.cassette.json`, `tests/golden/commits/s29.json`, `tools/golden_mutations/s29_*.py`.
- Golden coverage: all (schema 1: an unknown kind stays vacuously done, cassette given_goals_unknown_kind of S01).
- Effect on `assist_loop.py`: 0 lines (the file is not touched). Risk: high (it defines 'done' for a kind the core never saw).
- Notes: For a schema-2 kind: holds = every predicate of the list is True in the belief (cannot tell = not holding); reached = the list evaluated after the steps' rules and declared effects are applied (cannot tell = not refuted); missing = one line per predicate not reached; unknown_is: failure turns a verifier's 'cannot tell' into an unmet goal with the code perception_unavailable; a kind with neither rules nor holds, or with observe null and unknown_is failure, is a load error; a planned_by tree kind never goes to the planner (its tree exhausted = ask or stop). Feature cassettes (fakeline): done by holds and verifier; verifier cannot tell -> failed; the read-back from a phrase key.

### Wave W9: S14

#### Step S14: Recovery policies are selected by name (by action, by goal kind)

- Stream and model: LOOP, opus. Needs: S13, S23a. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/recover.py`, `robot_agent/core/pack/recovery.py`, `robot_agent/packs/household/callables/recover.py`, `robot_agent/packs/household/recovery.yaml`, `robot_agent/core/planning/step_ladder.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tests/test_pack_recovery.py`, `tools/arch_baseline.json`, `tests/golden/scenario_sets/s14_*.py`, `tests/golden/set_cassettes/s14_*.cassette.json`, `tests/golden/commits/s14.json`, `tools/golden_mutations/s14_*.py`.
- Golden coverage: replan (pick_fails_twice_replan), failure (skill_bug_failure, bt_goal_hw_fault), bt_goal (blocked_move, grasp_help), question; S01: move_help, drop_refind, custom_action; mutations ladder_choice, help_move_changed.
- Effect on `assist_loop.py`: -170 lines (to about 3069). Risk: high (the order of the ten steps of _step_failed).
- Notes: by_action equals the six names of the dict at assist_loop.py:3701-3703 and STEP_LADDER (equality test first); an action not listed goes to the planner; by_kind is empty. Builtins planner, stop, tree. The declared ladder executor with `with:` overrides is deferred. Feature cassette (fakeline): a custom action with a registered policy.

### Wave W10: S16b

#### Step S16b: The understand prompt text becomes a pack file (UNDERSTAND_PROMPT is a view)

- Stream and model: LOOP, opus. Needs: S04, S07. Parallel with (same wave, no common file): none.
- Files: `robot_agent/packs/household/prompts/understand.en.txt`, `robot_agent/packs/household/phrases.yaml`, `robot_agent/core/pack/prompts.py`, `robot_agent/core/planning/assist_loop.py`, `tests/test_pack_prompt.py`, `tools/arch_baseline.json`.
- Golden coverage: every cassette with an understand call (20 of 21; the prompt hash); mutation understand_prompt_one_word (exists).
- Effect on `assist_loop.py`: -26 lines (to about 3043). Risk: high (one changed character changes every understand prompt).
- Notes: The text has a non-Latin example: it may live in assist_loop.py or in a data file, never in another .py file (tests/test_lang_inventory.py:80-86). Byte equality test first. UNDERSTAND_SCHEMA stays where it is (its goal part built from the kinds is deferred).

### Wave W11: S09

#### Step S09: Grounding moves to phases/ground.py

- Stream and model: LOOP, sonnet. Needs: S03. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/ground.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: question (which_table_question), goals_only, success; S01: template_go, template_say_deliver, handover.
- Effect on `assist_loop.py`: -188 lines (to about 2855). Risk: medium.
- Notes: Verbatim mixin move; hook on_ground and service person_where stay at the same point; _ground_template stays callable through the class.

### Wave W12: S11

#### Step S11: The plan checker moves to phases/gate.py; a delegating _check stays in assist_loop.py

- Stream and model: LOOP, sonnet. Needs: S05. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/gate.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: gate (gate_symbolic_repair), replan, success; S01: gate_findings (the reason text of planner_calls); mutation gate_sees_nothing (exists; it patches AssistLoop._check).
- Effect on `assist_loop.py`: -160 lines (to about 2695). Risk: medium.
- Notes: tests/test_wiring_function_size.py:28 looks up 'assist_loop.py:_check' by file name: _check stays in assist_loop.py as a 2-line method that calls the moved body (40 lines or fewer). The 20 finding sentences stay code (their move to phrase keys is deferred).

### Wave W13: S15

#### Step S15: The say / ask plumbing moves to phases/answer.py

- Stream and model: LOOP, sonnet. Needs: S03. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/answer.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: question (5), stop (bt_goal_stop_word); S01: unclear_answer, readback_correction, two_destinations, non_english; mutations silence_is_yes, correction_rest_lost, say_not_translated.
- Effect on `assist_loop.py`: -176 lines (to about 2519). Risk: medium.
- Notes: Verbatim mixin move; no question gets a kind and no inline sentence gets a key. _fresh_question and yes_no stay callable through the class.

### Wave W14: S16a

#### Step S16a: The understand model call and the capability check move to phases/understand_call.py

- Stream and model: LOOP, sonnet. Needs: S16b. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/understand_call.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: every cassette with an understand call; refused; goals_only; S01: llm_roles (capture llm_kw).
- Effect on `assist_loop.py`: -270 lines (to about 2249). Risk: medium to high.
- Notes: run_blocking is INSIDE the moved line range (assist_loop.py:779-784) and STAYS in assist_loop.py (tests/test_wiring_function_size.py:28). Hooks on_understand_prompt and on_understand_schema stay at the same point.

### Wave W15: S17

#### Step S17: The understand rounds move to phases/understand_rounds.py

- Stream and model: LOOP, sonnet. Needs: S03. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/understand_rounds.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: all cassettes with an understand call; refused; goals_only; question; S01: given_goals_unknown_kind, two_destinations.
- Effect on `assist_loop.py`: -260 lines (to about 1989). Risk: medium.
- Notes: Verbatim mixin move (first third of :1787-2549, cut by method groups).

### Wave W16: S18

#### Step S18: Agreement and read-back move to phases/agree.py

- Stream and model: LOOP, sonnet. Needs: S17. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/agree.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: question (readback_no_correction, which_table_question); S01: readback_kinds, non_english.
- Effect on `assist_loop.py`: -260 lines (to about 1729). Risk: medium.
- Notes: Verbatim mixin move; hook on_issues and services confirm_limit, unrefused stay.

### Wave W17: S19

#### Step S19: Corrections move to phases/correct.py

- Stream and model: LOOP, sonnet. Needs: S18. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/correct.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: question; S01: readback_correction (required), goal_change, two_destinations.
- Effect on `assist_loop.py`: -240 lines (to about 1489). Risk: medium.
- Notes: Verbatim mixin move.

### Wave W18: S20

#### Step S20: Generate-and-certify moves to phases/certify.py

- Stream and model: LOOP, sonnet. Needs: S11. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/certify.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: success, gate, replan, plan_only, bt_goal; S01: gate_findings, planner_visible (capture planner_io); mutation planner_obs_empty.
- Effect on `assist_loop.py`: -287 lines (to about 1202). Risk: medium.
- Notes: Verbatim mixin move; hooks on_plan, on_plan_findings stay.

### Wave W19: S21

#### Step S21: Replan, goal change and the task stack move to phases/replan.py and phases/goal_edit.py

- Stream and model: LOOP, sonnet. Needs: S20. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/replan.py`, `robot_agent/core/planning/phases/goal_edit.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: replan (4), bt_goal (goal_change); S01: goal_change, new_task_first (required), planner_visible.
- Effect on `assist_loop.py`: -255 lines (to about 947). Risk: medium (the task stack is in one cassette only).
- Notes: Verbatim mixin move. core/planning/goal_change.py exists already: the new module has another name.

### Wave W20: S22

#### Step S22: The execute stage moves to phases/execute.py

- Stream and model: LOOP, opus. Needs: S23a, S14. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/execute.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: all cassettes that move the robot (16 of 21), stop (cancel_during_move), bt_goal (try_step calls _run_step); S01: pause_resume, coverage (if the fakes play them).
- Effect on `assist_loop.py`: -266 lines (to about 681). Risk: high (one step's life).
- Notes: _reconcile, _sense and _save_world STAY in assist_loop.py (they call current()). _guard stays callable through the class.

### Wave W21: S23b

#### Step S23b: The finish block moves to phases/finish.py

- Stream and model: LOOP, sonnet. Needs: S22. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/phases/finish.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: all (every run ends in _finish); mutation stop_not_seen (exists).
- Effect on `assist_loop.py`: -85 lines (to about 596). Risk: medium (28 methods call _finish or _cancelled).
- Notes: _finish moves VERBATIM as a mixin method, never as a delegating stub: tests/test_sage_v2_r3_m10_round1b.py:73 reads its source for two phrase keys.

### Wave W22: S24

#### Step S24: The config resolution moves to loop_config.py; assist_loop.py is the thin driver

- Stream and model: LOOP, opus. Needs: S23b. Parallel with (same wave, no common file): none.
- Files: `robot_agent/core/planning/loop_config.py`, `robot_agent/core/planning/phases/__init__.py`, `robot_agent/core/planning/assist_loop.py`, `tools/arch_baseline.json`.
- Golden coverage: all; default_options (fetch_engine_defaults); S01: env_override, the resolved-config snapshots, the replay smoke test.
- Effect on `assist_loop.py`: -230 lines (to about 366). Risk: high (the order of cfg.update, the environment, 28 __new__ tests).
- Notes: __init__ stays in assist_loop.py and computes current() (line 375) and host_id() (line 437) THERE, then passes them on: both names are patched on the assist_loop module (tests/conftest.py:66-69, tests/golden/harness.py:185-188, observe/replay.py:396-405). _run_blocking keeps its four calls (no PIPELINE object). The moved code keeps `self.cfg` so the wiring counts do not fall by a change of spelling. The string option '0' (fit S1) is not fixed here.

## What this closes in framework_fit.md

"Closed" is said of a row only when a named step builds what closes it. The first version marked G1, P8 and S3 closed although no
step built the schema-2 semantics they rest on (review finding 3). Without Steps S29, S31, S32 and S27b those three are only PARTLY
closed: the `install` kind would be understood and assigned, and then end "done by belief" through the vacuous result of an unknown
kind.

| id | closed by | status after the steps named |
|---|---|---|
| G1 goal kind `install` | S07 (the kind table is pack data), S29 (how a declared kind is done), S27a + S27b (the fleet can assign it) | partly closed after S07 (understood, not refused); closed after S29 and S27b |
| G2 six predicates | S06 (one table, registered evaluators), S26 (facts set by declared effects) | closed after S26 |
| G3 `act(action, object)` only | S26 (typed arguments at bind), S25 (a mapping as the last argument of `act`) | closed after S25 |
| G4 the engine's ladders run past the tree | S25a (tree key `ladder: false`, the seam `try_step(ladder=False)` that exists); S14 adds the choice by action and by kind | closed after S25a |
| G5 yes / no answers only | S25a (the `ask` leaf reads the choice words of a pack question), S25 (the answer as a fact, the raw text kept) | closed for a choice among declared answers; typed values taken from free text (`extract`) are deferred |
| G6 tree trace not in the event stream | S25a (tree key `events: true`, the service `emit` that exists) | closed, opt-in |
| G7 no LLM channel for the pack | nothing in level B for a SKILL; `ask_model` exists for options | OPEN for skills (the first version said "closed for decisions at the engine's seams"; fit's workaround is a model call inside a skill) |
| P1 YAML `on:` | S02 (the loader rejects a non-string key with a hint) | closed as an error message |
| P3 Find judged beyond `reach_far_m` | S12 (the step verifier `std.find_reach` is declared per action; a pack may drop it) | closed after S12 |
| P4 the floor re-find | `recovery_overrides` (exists), S14 (`by_action`, `by_kind`) | closed after S14; with S25a alone the tree's steps skip the ladder |
| P7 the two-destinations validator | S07 (`conflicts: none` per kind) | closed after S07 |
| P8 `look_then_ask` takes the parking as the source; the end check is a Find | a kind with no `from` slot (S07); `observe` by name (S12); `unknown_is: failure` and the `holds` list (S29) | partly closed after S12 (a declared verifier runs); closed after S29 ("could not look" is not "done") |
| S3 kind-less question taken as a read-back | S31 (`questions_default_kind`), S27a (`question_kinds` in the manifest), S27b (the manager reads it; its `strict_readback`) | partly closed after S31 (the event has a kind); closed after S27b |
| S9 "I will measureclips" | S10 (`verb` of an action) | closed (the pack already declares the verbs) |
| S10 recovery inside a skill is invisible | S25a, S25 (the subtrees can be written in the goal's tree, with node events) | smaller, not closed |
| S1 the string "0" is true | not in level B | open: a bug fix with a behaviour change; the owner decides |
| G8 to G13, S2, S4 to S8, P2, P5, P6 | not vocabulary (fleet behaviour, process state, model warm-up, the simulator) | open |

## Cost and where level B could be overengineered

Cost, as honest estimates (no code was written, so these are sizes of the plan, not measurements):

- New code, about 2900 lines: `contract/pack.py` about 300, `core/pack/` about 1100, tests about 1500. Moved code: about 3700 lines out of
  `assist_loop.py` into 19 modules, and about 500 lines of bodies into the standard pack's callables. Data: about 600 lines of YAML.
- 35 steps in 23 waves. Each needs a golden replay, the mutation check, the full suite (about 60 s) and a report. Steps S05, S07, S12, S14,
  S16b, S22, S24, S26 and S29 are high risk. My estimate, not a measurement: the six-hour cap of the owner's decision 8 does not hold all
  23 waves; it holds W0 to about W10.
- The goldens are the real limit. S01 has about 30 scenarios to add, and some may not be playable by the fakes (`pause_resume`,
  `coverage`, `new_task_first` are the doubtful ones). A step whose path is not in a cassette does not start.
- A pack author has a larger surface to learn: eleven sections. The standard pack is the worked example;
  `python -m robot_agent.core.pack.show <pkg>` prints the resolved pack.

Deferred (the owner's decision 1: what the red team flagged as over-engineered is deferred; the list is also in the plan file):

1. Step S28, whole: the option literals (19 option files with a kind word, 2 with action names), the constants as pack data, the four
   default-on household options switched on by the standard pack, phrase keys for inline English. No cassette switches most of these
   options on, and changing an option's default is the owner's decision.
2. The gate's 20 finding sentences as phrase data (half of the old Step 11). One user; the text is planner input.
3. The callable namespaces `goal.compile`, `goal.ground`, `goal.phrase`, `goal.conflicts`, `action.rules`, and the fields `needs_when`,
   `canonical`, `understood: option:<key>`. One user each (review finding 10).
4. The `Phase` protocol and `PIPELINE` (review finding 10).
5. The table of the 28 household questions as data with a registry of answer readers; the answer type `number`; `extract`;
   `unclear: model`; `questions_default_kind: name` (review finding 9).
6. The declared ladder executor with `with:` overrides (review finding 9).
7. Failure subcodes, `duration_s` and `errors` of an action, `failure_patterns` per language.
8. Facts that persist across runs.
9. The fleet field `carries`; `phrase` per kind in the manifest answer.
10. The goal part of the understand schema built from the kinds' slots.
11. Phases as objects with explicit inputs in place of mixins.
12. More than one base pack, importing single entries, discovering packs through entry points.

The smaller cut, re-derived from the `needs` of the plan (review finding 5). The targets are the steps a new pack needs: S29 (a declared
kind is done by its own rule), S25 (typed tree), S27b (the fleet), S30, S31, S32. Their closure under `needs` is 20 steps:
S01, S02, S03, S04, S05, S06, S07, S08, S10, S12, S23a, S25a, S25, S26, S27a, S27b, S29, S30, S31, S32. These are the waves W0 to W8
without S13. Size of `assist_loop.py` after them, from the per-step estimates: 4081 - 191 - 94 - 6 - 2 - 90 - 165 - 40 = about 3490 lines
(the first version said "about 3000" for a list that was not closed under its own needs). With S13 (the cheapest large move, in W8)
about 3240; with S14 (recovery by name) and S16b (the understand prompt as a pack file), which is the end of wave W10, about 3050. The
vocabulary is then open and a new kind has its own "done"; the loop is not yet thin. Waves W11 to W22 are the "thin loop" half: twelve
verbatim moves that are worth their cost only as a whole, and end at about 370 lines (350 to 450).

Where I am least sure:

- The predicate scopes. They exist only so that the two old evaluators stay exactly as they are. If the owner accepts that a tree may use
  the four effect names too (a tree that uses one today is unusable), the scopes can go.
- The fact store. It is the smallest way to give a pack a belief field, but it is a second belief next to the action model's. A pack that
  needs a fact the standard actions change (the pipe leaves the clips when it is picked) must declare that effect itself.
- "Reached by simulation" for a declared kind (S29): a predicate that reads an observation cannot be decided from a plan, so the gate lets
  it pass and only the end check decides. That is honest, and weaker than the household rules.
- `class` of an action versus the option roles: two near-equal ideas. They are kept apart because filling in roles would change behaviour.
- Whether the mixin form meets "small modules with one interface". It meets the size and the interface at the stage level; inside a stage
  the methods still share `self`.
- The per-step sizes of the understand block (S17, S18, S19): the inventory gives one range of 763 lines for 32 methods, and the three-way
  cut is by method groups nobody has listed yet.

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

## What the revision of 2026-11-22 read and ran (stream FIX)

- Read for the check of the 29 findings: `tests/test_wiring_function_size.py`, `tests/golden/harness.py` and
  `tests/golden/test_golden_cassettes.py` in full, `tools/golden_mutation_check.py` and `tools/lint_arch.py` in full, `tests/test_architecture.py`
  lines 1-80 and 149-171, `tests/conftest.py` lines 60-82, `robot_agent/core/planning/assist_loop.py` lines 362-380, 770-790, 1225-1268,
  1296-1304, 1438-1545, 1686-1703, 1805-1816, 3345-3376, 3412-3420, 3476-3500, 3688-3706, 4066-4081, `goals.py` lines 20-150,
  `options/bt_goal.py` lines 44-62, 86-96 and 130-367, `core/options/services.py` lines 12-45, `core/planning/option_services.py` lines
  66-84, `loop.py` lines 234-243, 350-380 and 505-520, `base.py` lines 176-216, `contract/recovery.py` lines 18-32 and 94-112,
  `observe/replay.py` lines 385-410, the fleet's `scheduler.py` lines 12-50, `graph.py` lines 22-68 and `orchestrator.py` lines 232-250.
- Ran: the replay of the 21 cassettes without a change (clean) and under eleven monkeypatched mutations in child processes
  (`template_always_reached`, `in_needs_no_pick`, `deliver_prompt_changed`, `go_prompt_changed`, `say_not_translated`, `silence_is_yes`,
  `help_move_changed`, `help_pick_changed`, an inversion of the tree conditions `at` / `opened` / `closed`, `unknown_kind_not_reached`,
  `sage_prompt_changed`); the results are in "Review of this design". Counted with scripts: the goal kinds, languages, packs and tags of
  the cassettes, the names imported from `assist_loop` (with `ast`), the language-literal rows (`tools/lang_inventory.py`: 61, six of them in
  `assist_loop.py`), the lines of `assist_loop.py` with a standard action name (66) or a goal kind (59) in quotes.
- Ran on this revision (a change of two files under `docs/`): the full suite, 2942 passed and 4 skipped (the baseline of 2916 plus the 26
  golden tests), and `tools/lint_arch.py`, 0 new findings.
- NOT done: the nine mutations of the
  golden reviewer that I did not re-run (`non_manip_goal_seen`, `readback_in_phrase`, `rb_go_phrase`, `correction_rest_lost`,
  `menu_retry_unheard`, `korean_phrases_lost`, `effect_unknown_pred_fails`, `planner_obs_empty`, `bt_placed_ignores_hand`) are taken from
  the review; `homeloop_robot` and `shopfloor_robot` were only listed, not read; no step of the plan was tried, so every size, every
  "the fakes can play it" and the six-hour estimate are untested. The semantics of Step S29 (reached by simulation) are a design decision
  of this revision with no prototype.
