# Core generalization, level B: result of the run (2026-11-23)

Branch `generalize-core`, worktree `/media/keti/workdir/remote_dir/ra_core_int`. Base 8002347 (round A tip 65124d0).

## Outcome in one paragraph

The run stopped in wave W0. The stop reason is the owner's stop rule: the integrated tip was not green after 3 integrator rounds. The
only red check was the script check "[roundB] no existing test, golden or cassette was modified ... since the round-A tip" (it listed
`tests/golden/harness.py`); the S01 additions were then moved into `harness_sets.py` and `harness.py` was restored (commit 0d4fec1).
The test suite itself is green. No migration step was merged: the production code of `robot_agent/` is equal to 65124d0.

## What changed

- Steps done: none (0 of 35 migration steps merged). No production behaviour changed.
- Added (tests, tools and docs only): the inventory, the design (corrected after a 29-finding red-team review), the plan
  (`generalize_core_plan.json`, 35 steps, 23 waves), the golden traces of round A (21 cassettes) and the W0 extension S01 (41 set
  cassettes, snapshots, order test, planner-prompt golden, mutation sets `tools/golden_mutations/s01_*.py`), and the ratchet
  (`ratchet.json`).
- Only existing files edited: `docs/proposals/generalize_core_design.md`, `docs/testing.md`, `tools/golden_mutation_check.py`,
  `tools/golden_record.py` (all docs or tooling of this branch, none a test or a golden).

## Sizes and tests

| item | before | after |
|---|---:|---:|
| `robot_agent/core/planning/assist_loop.py` | 4081 lines | 4081 lines (not touched) |
| tests (pytest, `-p no:warnings`) | 2916 passed, 4 skipped | 3075 passed, 1 skipped (68 s) |
| architecture lint | 0 new, 40 import findings, 8 size offenders | the same |

Module list: no new production module exists. Target list with line budgets is in the design, "Target module list and line budgets"
(assist_loop.py about 450, 12 pack modules, 4 phase-independent callable files, phases of 100-300 lines each). Not built.

## What the pack declaration now allows

Nothing yet. The declaration (schema 2, `pack:` section, goal kinds, predicates, actions with typed arguments, verifiers, recovery,
phrases, answers, constants, trees, fleet needs) is designed, not implemented. A manifest with no `pack:` section stays schema 1 and
would load as the standard pack unchanged. The example PEX pack file, as designed (`pex_robot/configs/pack.yaml`, header and the
`install` kind):

```yaml
pack:
  schema: 2
  name: pex
  version: "1.0"
  extends: household
  file: pack.yaml
  callables: pex_robot.configs.pack_callables
  source: "pex_case task card (docs/case/case_text.md) and pexcase/agent/task.py"
  remove: {goal_kinds: [in, open, close, deliver, give]}
goal_kinds:
  install:
    source: "pex_case task card: one pipe on one wall, seated in both clips and crimped"
    slots: {object: {type: object, required: true}, to: {type: place, required: true}}
    needs: [MeasureClips, Pick, Seat, Crimp, Place]
    understood: capable
    planned_by: tree
    holds: ["pipe_seated(?object, ?to)", "crimped(?object)", "hand_free()"]
    observe: pex.pipe_in_clips
    unknown_is: failure
    conflicts: none
    phrase: rb_install
```

## Deferred (not built, with the reason)

- S28 remaining literals (the 19 options with a kind word and the 2 with action names, constants as pack data, the four default-on
  household options switched on by the standard pack, phrase keys for inline English): no cassette switches most of these options on,
  and moving an option's default is the owner's decision; do it per option when a second robot needs it.
- Gate finding sentences as `finding.<code>` phrase data (half of the old Step 11): one user (the household pack), the text is planner
  input; the move of the checker itself is S11.
- Callable namespaces goal.compile, goal.ground, goal.phrase, goal.conflicts and action.rules, and the schema fields needs_when,
  canonical, understood: option:<key>: one user each (say, give); they stay code in core/planning/goals.py (review finding 10,
  over-engineered).
- The Phase protocol and the PIPELINE tuple: no pack can add a phase; _run_blocking keeps its four calls (review finding 10).
- The declarative table of the 28 household questions with a registry of answer readers, the answer type `number`, `extract`,
  `unclear: model`, and questions_default_kind: name: the bt ask leaf with pack word lists (S25a, S25) and the literal default kind
  (S31) cover what a pack needs today (review finding 9).
- The declared ladder executor (rungs with `with:` argument overrides): a tree with typed arguments (S25) and the tree key
  `ladder: false` (S25a) cover it (review finding 9).
- A skill's own LLM channel (fit G7): not in level B; G7 stays open for skills.
- Failure subcodes, `duration_s` and `errors` of an action, `failure_patterns` per language: nobody reads them today.
- Fact predicates that persist across runs (`persistent: true`): S26 resets facts at run start; decide when a pack needs it.
- The fleet field `carries` and the per-kind `phrase` in the manifest answer: one possible value / the fleet's voice is open data.
- The schema's goal part built from the kinds' slots (UNDERSTAND_SCHEMA as data): one user; the prompt text is S16b.
- Phases as objects with explicit inputs in place of mixins: not provable in small steps.
- fit S1 (the string option "0" read as true): a bug fix with a behaviour change; blocked by an owner decision.
- More than one base pack, importing single entries from another pack, pack discovery through entry points.

Failed or stopped steps: none. Stop reason: wave W0: the integrated tip is not green after 3 integrator rounds.

## Not done or not verified

- Real robot: nothing was run on it (and its checkout was not touched).
- Other seeds: the cassettes are one recorded seed each; the model is stubbed, so no real-model run was made.
- The four real packs' manifests (kcare_robot, symbolic_robot home/factory, pex_robot) are covered by manifest snapshot tests
  (`tests/test_manifest_snapshot.py`), not by running them through a loader, because the loader does not exist yet.
- The mutation check proves the goldens bite for the mutations listed in `tools/golden_mutations/`, not for every possible drift.
- robot_fleet: no change made, none merged (its manifest-based needs are step S32 and later).

## How to switch each part off

Nothing behavioural was added, so nothing needs switching off. The added files are tests and docs; reverting the branch to 65124d0
for `robot_agent/` is a no-op. Every future step is designed opt-in (default data = old constants).

## Round C: the PEX robot on this framework (exact steps)

Round C needs the migration steps first. Today the framework is unchanged, so no workaround of `pex_case/docs/framework_fit.md` can be
deleted yet. Steps, in order:

1. Run waves W0 to about W10 of `generalize_core_plan.json` (first fix the W0 gate: the harness check should compare against the new
   tip, not the round-A tip; that is a script decision of the owner).
2. Write `pex_robot/configs/pack.yaml` (example above) with the other sections of the design's PEX examples.
3. Delete one workaround at a time, each with a test, in this order:
   - G1 BW(goal-kind-on) and P7 (two walls, one object): needs S07 and S29 (kind `install`, `conflicts: none`).
   - G2 BW(bt-subtree-in-skill): needs S06 and S25 (predicates `clips_measured`, `pipe_seated` usable in trees).
   - G3 coarse acts: needs S10 (actions with typed arguments arm, pose, dz).
   - G4 recovery inside MoveTo: needs S14 (recovery policy `tree`, no engine ladder).
   - G5 fixed yes/no phrase `q_pex_use_measured`: needs S25 and S27a/S32 (free-text answers).
   - G6 own `bt_trace.jsonl`: needs S25a (public `tree_node` events).
   - P8 (Find within 4 m): needs S12 (a verifier `pex.pipe_in_clips`).
   - S3 (manager objection dropped): needs the fleet step on manifest question kinds (S32).
4. Point `[fwsvc]` and `[fwruns]` of the pex_case check at `/media/keti/workdir/remote_dir/ra_core_int` and robot_fleet local master
   (owner decision 6), keeping the behaviour checks.
5. Re-run `single_slip`, `single_ask`, `fleet_two_walls`, `fleet_case4` with `pexcase.stack run`; rebuild the four videos; update
   `framework_fit.md` (before/after table), `writeup.md` (at most 950 words) and README.

Cannot be deleted by level B: G7 (the pack's own model call), G11 (fleet never reassigns a started goal), G12 (run cancelled when
its socket closes), G13 (cancel reaches no sim skill), S1 (string "0" option), S2, S5 to S8 (not vocabulary).

## Plain summary

The run stopped in the first wave (W0): no restructuring step was merged, so the production code of robot_agent is unchanged and assist_loop.py is still 4081 lines. The recorded golden checks and the design are done, the suite is green and the lint has no new finding. Nothing needs switching off because no behaviour was added. Round C (the PEX robot) cannot start before waves W0 to W10 have run, and the "no existing test modified" gate must be fixed first.
