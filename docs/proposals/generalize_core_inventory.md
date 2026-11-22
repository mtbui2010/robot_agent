# Generalize the core: inventory, level B (round A, stream INV2)

Status: PROPOSAL input. Read-only measurement of the code; no production code was changed. This file replaces the level-A inventory of
stream INV (branch `gc/inv`, commit `ab26742`): every statement of that file was re-checked, the wrong ones are corrected here, and the file is
extended to level B (the full registry: skills and actions, verifiers, recovery, questions and answers, the option map, the phase map).
Base: robot_agent `8002347` (branch `generalize-core`), robot_fleet `d74f326` (read only), `embodiplan` as installed in the shared venv (read only).
Date: 2026-11-22.

How to read the evidence. Every `path:line` is relative to the root of this repo and is written with its full path (`robot_agent/...`),
except two groups that live in other trees: `robot_fleet/<file>.py:N` is the package `robot_fleet/robot_fleet/` of the repo
`/media/keti/workdir/remote_dir/robot_fleet`, and `embodiplan` / `pex_robot` lines are written as "file line N" (outside this repo). "Read" = I read the
lines. "Run" = I executed a snippet in the shared venv (`/media/keti/workdir/data/homeloop_lora/venv-bench`) and quote its output. Sizes were measured
with `wc -l`, `awk` and `ast` (commands in "Sizes"). "Not read" is listed at the end.

## Short answer

1. The household vocabulary is in four layers, not three: closed TABLES (goal kinds, conditions, actions, recovery), closed BRANCHES (40 lines that compare a goal kind and
   67 lines that name an action inside `assist_loop.py` alone), closed ENGLISH TEXT (prompt, checker findings, 62 phrases, failure-code patterns) and closed PROCEDURES
   (yes/no parser, end-of-run look at `on`/`in` only, per-action recovery dict). The yes/no WORDS are already language-pack data; the PROCEDURE is closed.
2. There are THREE predicate vocabularies that do not know each other: the six `bt_goal` conditions (`robot_agent/options/bt_goal.py:61`), the six predicates the post-step verifier evaluates
   on a manifest `effects` list (`robot_agent/core/planning/verify.py:53-70`), and the six belief fields of the action model. A fourth thing, `pre` / `hold` / `errors` of a manifest
   skill, is validated and stored but evaluated nowhere (measured: no reader outside `robot_agent/contract/`). So a pack can DECLARE more than the core USES.
3. The contract layer is already open where the core is closed: a manifest accepts any CamelCase action name (`robot_agent/contract/manifest.py:108`, `:680`), typed
   `params` (JSON schema), `effects`, `verify`, `errors`, `interruptible`, per-pack `recovery_overrides` (`robot_agent/contract/recovery.py:133-138`), `action_roles`, 30 option hooks and
   pack data under `planner.options.<option>` with a `source` / `assumed` label (`robot_agent/core/planning/pack_data.py:6-8`). The closed part is what the LOOP does with an action it does not know.
4. A custom action (PEX `MeasureClips`, `Seat`, `Crimp`; factory and home `Look`) is run as a skill, gets NO precondition check (embodiplan: "robot-specific actions: no symbolic precondition", action_model.py line 371),
   NO belief effect, no per-action recovery ladder (`robot_agent/core/planning/assist_loop.py:3701-3703`), and its declared `effects` are checked only when they use one of the six tracked predicates
   (`robot_agent/core/planning/verify.py:69`, `:92`: an unknown predicate is `None`, never a failure).
5. `assist_loop.py` (4081 lines, 218 functions, none over 80) is big because of many small methods. Phase map in "Phase map"; the largest block is understand + clarify + read-back (763 lines, 32 methods).
6. Cheapest first cuts need no design: `Places` + `Memory` + `words` (195 lines, no use of the loop or of anything but stdlib and `langdata`) and the goal-reach simulation (96 function lines).
7. The fleet's closed table is `_NEEDS` (`robot_fleet/scheduler.py:16-23`) plus `_CONTAINER_KINDS` (`robot_fleet/graph.py:26`); voice and assignment are already open. The robot's `GET /robot/manifest`
   carries `plan_actions` but not goal kinds, their needs, phrases or goal-kind capabilities (`robot_agent/api/robot_info.py:17-21`).
8. Questions: 28 kinds of question (30 of the 62 `CORE_PHRASES` are `q_` keys); 16 carry an event `kind`, 12 carry none, one emits no event at all; the answer is read by EIGHT different routes (yes/no three-way, yes/no four-way, place grounding,
   pack word lists, instance choice, re-understanding by the model, ad-hoc regex, option-specific functions). Only en, ko and vi have full language packs; ja and zh have detection marks only.
9. Verdict on `pex_case/docs/framework_fit.md`: of G1-G13, S1-S10 and the P rows, the robot_agent lines are right (within 1 line) for G2-G6, G12, S1, S3, S9, P2-P4, P6, P8; G1 is right only for the LLM path; G7 has WRONG evidence (its
   conclusion holds for a pack skill, not for options); G11 and P7 have WRONG LINES; G8-G10, S4-S8, S10, P1, P5 cite no robot_agent line. Ten corrections to the level-A inventory are listed at the end.

## Sizes

Commands (run from the repo root; they are the source of every number):

```
git ls-files 'robot_agent/*.py' | xargs wc -l | sort -rn | head -24      # 224 files, 44460 lines
python3 <ast: end_lineno - lineno + 1 for every def/async def>             # 2813 functions (all, nested ones included)
python3 tools/lint_arch.py | tail -1                                      # architecture baseline
```

Lint baseline (`tools/arch_baseline.json`, `tools/lint_arch.py`): 0 new findings, 40 import findings, 8 size offenders (1 file: `assist_loop.py` 4082 as the lint counts it, `wc -l` says 4081; 7 functions:
`robot_agent/contract/manifest.py::_planner` 89, `robot_agent/core/device_manager.py::add_connect` 240, `robot_agent/core/plan_skill.py::run_plan_skill` 91, `UnifiedAgent.run` 138, `run._blocking` 114,
`run_direct` 178, `run_direct._blocking` 81), wiring `cfg_keys 62, cfg_branches 59`. Limits (`tools/arch_rules.json`): core, contract, interact: file 1200, function 80; options: file 400, function 60.

### Files over 500 lines (17) and the files that matter here

| lines | file |
|---:|---|
| 4081 | robot_agent/core/planning/assist_loop.py |
| 1096 | robot_agent/connect/ros/node.py |
| 912 | robot_agent/core/planning/loop.py (`ClosedLoop`, parent of `AssistLoop`) |
| 798 | robot_agent/utils.py |
| 740 | robot_agent/core/unified_agent.py |
| 723 | robot_agent/core/device_manager.py |
| 718 | robot_agent/core/planning/understand.py |
| 715 | robot_agent/connect/pool.py |
| 702 | robot_agent/contract/manifest.py |
| 671 | robot_agent/interact/run_manager.py |
| 636 | robot_agent/core/planning/language.py |
| 634 | robot_agent/core/planning/goal_fix.py |
| 539 | robot_agent/core/planning/search.py |
| 488 | robot_agent/core/planning/plan_repair.py |
| 373 | robot_agent/core/planning/option_services.py |
| 349 | robot_agent/core/planning/speech.py |
| 285 | robot_agent/core/planning/goals.py |
| 199 / 174 / 129 | search_hooks.py / step_ladder.py / which_one.py (the three mixins of `AssistLoop`, `robot_agent/core/planning/assist_loop.py:362`) |

The 45 files of `robot_agent/options/` (44 options + `__init__.py`, 19 lines) total 5958 lines; the largest is `robot_agent/options/cond_goal.py` (399, limit 400). The option engine is 5 small files
(`robot_agent/core/options/`: spec 112, dispatch 130, registry 84, config 82, services 45 lines).

### Functions

Over 100 lines in the whole package: 8 (strictly more than 100): `robot_agent/core/device_manager.py:125` (`add_connect`, 240), `robot_agent/core/unified_agent.py:481` (`run_direct`, 178),
`robot_agent/diagnose.py:47` (169), `robot_agent/core/unified_agent.py:339` (`run`, 138) and `:355` (`run._blocking`, 114), `robot_agent/observe/replay.py:363` (127), `robot_agent/rename_skill.py:92` (108),
`robot_agent/delete_skill.py:141` (101). None in the planner or in `assist_loop.py`. 18 functions have 80 lines or more, 36 have 60 or more, of 2813.

In `assist_loop.py`: 218 functions (`AssistLoop` 189 methods, `Places` 12, `Memory` 7, 3 module functions, 7 nested), the longest has 80 lines (`AssistLoop.__init__`, :369). Next: `_understand_round` 78 (:1816),
`_recover_find` 77 (:3808), `_v2_cfg_base` 77 (:465), `_ground_goals` 73 (:991), `_read_back` 71 (:2438). The file has 250 blank and 198 comment-only lines. It reads the option engine 50 times
(`self.options.`, 53 occurrences) and the config dict `self.cfg` 100 times (57 `.get(`, 43 `[`).

## Phase map of assist_loop.py

The driver is `_run_blocking` (`robot_agent/core/planning/assist_loop.py:1662-1703`): open the run, reconcile, build `Places`, `Memory`, verifier, budget; then four phases (:1690-1696) `_understand_phase`,
`_plan_phase`, `_execute_phase`, `_final_check`. The brief's six pipeline phases (understand, generate-and-certify, execute, verify, recover, answer) cut across these four; the table gives each source block with its
line range (first line of the first function to last line of the last function, from `ast`), its measured size and the brief's phase it belongs to. "span" counts every line of the range, "fn lines" only the function bodies.

| block | range | span | fn lines | brief's phase | what is in it |
|---|---|---:|---:|---|---|
| header and module data | 1-120 | 120 | 10 | (data) | docstring 1-38, imports 40-79, `_LOC_ACTIONS` :81, `ASSIST_OPTIONS` :83-100, `_USER_WORDS` :101, `words` :107, `_strip_article` :114, `_fmt_en` :118 |
| `Places` | 123-252 | 130 | 113 | understand (data) | the live ENV as a closed place vocabulary (12 methods) |
| `Memory` | 258-301 | 44 | 35 | understand (data) | object -> last place, remembered answers (7 methods) |
| schema and prompt | 307-356 | 50 | 0 | understand (data) | `UNDERSTAND_SCHEMA` :307-327, `UNDERSTAND_PROMPT` :329-356 |
| config and run services | 369-656 | 288 | 272 | (setup) | `__init__`, `_v2_cfg_base`, `_um_cfg`, `_rm_cfg`, `_r3_cfg`, `_lab`, `_now`, `_role`, `_manifest_planner`, `_make_planner`, `_llm`, `_sampling` |
| understand: model call and capability | 658-941 | 284 | 257 | understand | `_understand`, routed model, prompt flags, `_cannot_line`, `_over_limits`, `_capability_check`, `_understand_schema`, `_cannot_do` |
| understand: grounding on the ENV | 944-1137 | 194 | 187 | understand | `_ground_container`, `_ground_template`, `_ground_goals`, `_ground_speaker`, `_person_destination` |
| speak and ask plumbing | 1143-1222 | 80 | 71 | answer | `language`, `_announce`, `_in_lang`, `_ask`, `_ask_io`, `_ask_heard`, `yes_no` |
| read-back phrase | 1225-1268 | 44 | 42 | answer | `summary`, `_goal_phrase` |
| checker (the gate's wording) | 1271-1438 | 168 | 159 | generate-and-certify | `_ground_step`, `_check`, `_check_step`, `_word_step`, `_word_pick`, `_word_place`, `_repair_plan`, `_repair_scope` |
| goal-reach simulation | 1442-1543 | 102 | 96 | generate-and-certify, verify | `_releases`, `_simulate_goals`, `_on_reached`, `_missing`, `_reaches`, `_goals_hold` |
| execution helpers | 1546-1659 | 114 | 106 | execute | `_bind`, `_params`, `_holding`, `_base_pose`, `_dist_to_place`, `_effect`, `_found_point`, `_place_of_far_point` |
| run driver and `done` | 1662-1784 | 123 | 115 | (driver), answer | `_run_blocking`, `_finish`, `_say_of_msg`, `_humanize`, `_llm_failed`, `_deciding_failure`, `_note_failure`, `_cancelled` |
| PHASE 1 understand, clarify, read-back | 1787-2549 | 763 | 715 | understand, answer | 32 methods: `_understand_phase` :1787, `_take_given_goals` :1805, `_understand_round` :1816, views, refusal, agree, `_goal_readback`, `_read_back` :2438, corrections |
| ask machinery | 2550-2649 | 100 | 89 | answer | `_ask_yes_no`, `_ask_clarify` :2563, `_asks_where`, `_essential`, `_fresh_question`, `_restates`, `_obs`, `_ground_all` |
| PHASE 2 generate and certify | 2651-2941 | 291 | 275 | generate-and-certify | `_plan_phase` :2651, `_generate_and_certify` :2682 (gate rounds 0, symbolic, 1, 2), `_plan_call`, `_subgoals`, `_gate_repair*`, `_repair_suffix`, `_tidy`, `_blocking`, `_hard_findings` |
| replan | 2943-3031 | 89 | 82 | recover | `_refine_plan`, `_sage_replan` :2960, `_replan_suffix`, `_no_replan_end`, `_replan_or_fail` |
| goal change, task stack | 3034-3203 | 170 | 162 | understand (mid-run), recover | `_apply_pending`, `_goal_edit`, `_new_command`, `_suspend_for`, `_resume_suspended`, `_repair_goals` |
| PHASE 3 execute: loop and plan end | 3205-3343 | 139 | 135 | execute | `_execute_phase` :3205, `_refind_after_pause`, `_plan_ended`, `_coverage_gap`, `_look_at_untouched` |
| observation of goals | 3345-3465 | 121 | 116 | verify | `_observe_goals` :3345, `_end_action`, `_return_to_end`, `_observe_goal` :3415, `_look` |
| guard and one step | 3467-3597 | 131 | 124 | execute | `_precondition`, `_reconcile`, `_guard_arrived`, `_guard` :3515, `_run_step` :3551 |
| post-check | 3599-3630 | 32 | 32 | verify | `_post_check` (effects + VerifySpecs + the release and Find rules) |
| success, failure, policy, waits | 3632-3806 | 175 | 170 | execute, recover | `_step_succeeded`, `_step_failed` :3660, `_reset_container`, `_apply_policy` :3735, `_wait_on_robot`, `_wait_health` |
| Find ladder | 3808-4063 | 256 | 243 | recover | `_recover_find` :3808, relook, second look, search legs, `_recover_phantom`, `_refuted` |
| PHASE 4 final check | 4066-4081 | 16 | 16 | verify | `_final_check` |

Sums (method bodies of `AssistLoop` only, `ast`): 3464 function lines of the 4081; `Places` (113) and `Memory` (35) function lines and the 10 lines of the three module functions are outside this sum, the rest is header, module data, blank lines and comments.
By the brief's phase (first-named phase of each row): understand 1159, answer 202, generate-and-certify 434, execute 365, verify 164, recover 325, setup and driver 387; shared between two phases:
goal-reach simulation 96 (generate-and-certify / verify), success-failure-policy-waits 170 (execute / recover), goal change and task stack 162 (understand mid-run / recover). 1159+202+434+365+164+325+387+96+170+162 = 3464.

Structure notes (all measured or read):
- `AssistLoop` already inherits three mixins in other files (`WhichOne`, `SearchHooks`, `StepLadder`: `robot_agent/core/planning/assist_loop.py:362`) and reaches the options only through `LoopServices`
  (`robot_agent/core/planning/option_services.py`, 373 lines; protocol `robot_agent/core/options/services.py:12-45`).
- The run state `r` is one mutable object that every phase writes (`r.goals`, `r.steps`, `r.world`, `r.completed`, `r.failures`, `r.dialogue`, ...): the phases share state by attribute, not by arguments
  (`robot_agent/core/planning/assist_loop.py:3207-3214` sets 20 of them at the start of the execute phase).
- The phases are generators that yield events and return `_NEXT` / `_STOP` / `_DONE` (`robot_agent/core/planning/assist_loop.py:3217-3233`, `:3551-3597`).

## Closed tables

"Closed" = a literal table, enum or procedure in code that a robot pack cannot extend; adding a goal kind, a condition, an action or a question means editing the core.

### Goal kinds and goal fields

| table | where | what is closed | pack can extend? |
|---|---|---|---|
| `GOAL_KINDS` | robot_agent/core/planning/goals.py:33-46 | 7 kinds: on, in, open, close, go, say, deliver; each = needs (actions, a tuple inside = any one) + one prompt line | no |
| `MANIPULATION`, `TEMPLATE_KINDS` | robot_agent/core/planning/goals.py:32, :47 | the 4 kinds the LLM may always use; the 3 kinds compiled without an LLM | no |
| `OPTIONAL_KINDS` | robot_agent/core/planning/goals.py:50-54 | `give`, mapped onto `on` by `canonical` (:93-96); switch: option `understand_give_kind` | switch only |
| `kinds_for` | robot_agent/core/planning/goals.py:74-81 | the LLM enum = manipulation kinds ALWAYS (even for a robot with no Open skill: it is understood, then refused) + template kinds the robot has actions for | no |
| `missing_actions` | robot_agent/core/planning/goals.py:57-71 | unknown kind -> `[its type]` (:61-63); `say` with a place also needs `MoveTo` (:69-70) | no |
| `domain_actions` | robot_agent/core/planning/goals.py:84-90 | the action vocabulary a refusal may name as missing | no |
| `compile_steps`, `reached`, `describe`, `subgoal_text`, `summary` | robot_agent/core/planning/goals.py:219-241, :243-266, :174-217 | per-kind step recipe (go = MoveTo; say = MoveTo + Say; deliver = MoveTo + Say + WaitForUnload), per-kind "reached" test, per-kind text | no |
| `UNDERSTAND_SCHEMA` | robot_agent/core/planning/assist_loop.py:307-327 (`type` enum :317, overwritten by `_kinds()` at :916; `reason_type` enum :313-314) | goal `type`, refusal reasons, required slots `type, object, from, to` (:324) | no |
| goal slots | robot_agent/core/planning/assist_loop.py:318-324; robot_agent/core/planning/goals.py:114 (`dedupe` key); robot_agent/options/bt_goal.py:31 (`SLOTS`); robot_agent/options/ask_agree.py:51; robot_agent/options/answer_align.py:42 | every goal is `{type, object, from, to}` plus ad-hoc fields (`said`, `content`, `hand`, `near_person`, `user_at`, `from_any`, `person`, `name_local`); a goal with a slot that is neither a place nor an object has no home | no |
| kind literals elsewhere | measured: 40 lines in `assist_loop.py`, 24 in `goal_fix.py`, 23 in `goals.py`, 17 in `plan_repair.py`, 8 in `understand.py`, 1-3 in 21 option files | command: `grep -E '(get\("type"\)\|\["type"\]\|\bt\b) *(==\|!=\|in\|not in) *[("{]'` restricted to the kind words | n/a |

Sample of branch points outside `assist_loop.py` (read): `robot_agent/core/planning/goal_fix.py:63`, `robot_agent/core/planning/plan_repair.py:67`, `robot_agent/core/planning/understand.py:299`, `robot_agent/core/planning/search_hooks.py:65`
(the search belief only for `on` / `in`), `robot_agent/options/precond_repair.py:30-31` (`NEEDS`, `KINDS`), `robot_agent/options/plan_release.py:33`, `robot_agent/options/plan_complete.py:28`,
`robot_agent/options/heat_safety.py:21` (`PUT_KINDS`), `robot_agent/options/fetch_guard.py:34-35` (`_FETCH`, `_LOOKS`), `robot_agent/core/planning/goal_filter.py:28` (`_MOVES`).

### Conditions, predicates and the belief (four vocabularies)

| vocabulary | where | members | used for | unknown name |
|---|---|---|---|---|
| bt_goal conditions | robot_agent/options/bt_goal.py:61 (`CONDITIONS`), bodies :50-58 (`holds`) | holding, hand_free, at, placed, opened, closed (6) | `cond` leaves of a declared tree (`robot_agent/core/planning/behavior_tree.py:18`, :84-85; `host.cond` returns a plain bool) | tree unusable: `_filled` returns None (`robot_agent/options/bt_goal.py:93`), goal falls to SAGE, no event, no warning; `holds` returns False (:55-58) |
| post-step predicates | robot_agent/core/planning/verify.py:53-70 (`_holds`) | hand_empty, arrived, found, holding, opened, on (6) | a skill's manifest `effects`, checked after success (`robot_agent/core/planning/verify.py:73-95`) | `None` = not checked, step passes (:69, :92) |
| manifest predicate syntax | robot_agent/contract/manifest.py:110 (`_PRED_RE`), :571 | any `[not\|!]name(args)` | validation of `pre`, `hold`, `effects` | none: any identifier passes validation |
| action-model belief | embodiplan action_model.py lines 104-119 (`SymbolicState`: arrived, found, holding, opened, on, visible); `robot_agent/core/planning/base.py:71-97` (`WorldState` adds `holding_since`, `found_pose`, `holding_pose`, `provenance`) | one `holding`, one `arrived`, one `found` | gate, guard, effects | a field a pack needs (clips measured, pipe seated) has no home |

Other closed predicate-like things:
- `NEEDS` of `precond_repair`: `hand_free`, `holding`, `near` (+ `achieves` in `on`/`in`/`hand`) (`robot_agent/options/precond_repair.py:30-31`; run through pack data `planner.options.precond_repair.needs`, labelled).
- `pre`, `hold`, `errors` of a manifest skill: parsed (`robot_agent/contract/manifest.py:684-692`) into `SkillSpec` (`robot_agent/contract/types.py:189-218`); measured: no reader in `robot_agent/core`, `robot_agent/options`, `robot_agent/interact`, `robot_agent/api`
  (`grep -rn '\.pre\b\|\.hold\b\|\.errors\b'` finds only the parser). They are documentation today.
- the effect text of a release or dock is searched as a string: `"docked(" in fx or "arrived(" in fx` (`robot_agent/core/planning/assist_loop.py:3384`); `hand_empty()` is matched as a string in `robot_agent/core/planning/robot.py:139`.
- per-kind "holds": if-chains in `robot_agent/core/planning/assist_loop.py:1484-1501` (`_missing`), :1504-1521 (`_reaches`), :1523-1543 (`_goals_hold`), then `goals.reached` for template kinds (`robot_agent/core/planning/goals.py:243-266`).
- end-of-run look: `robot_agent/core/planning/assist_loop.py:3417-3418`: only `on` and `in` are looked at; any other kind returns "no sensor declared for it (belief)" and is never observed
  (run: `goals.missing_actions({"type":"install"}, ["Pick","Place"])` returned `['install']`).

### Action signatures and action names

| table | where | what is closed |
|---|---|---|
| step shape | `{"action", "object"}` plus optional `grounded`, `verify_goal`, `skill`, `params`, `reason` (`robot_agent/core/planning/assist_loop.py:1271-1298`, :3341, `robot_agent/core/planning/concrete.py:1-50`) | one object per step; no other argument reaches a skill from a SAGE plan or a tree |
| `bt` `act` | robot_agent/options/bt_goal.py:164-169 | `act(action, obj)`: a tree leaf with more arguments passes `valid` (`robot_agent/core/planning/behavior_tree.py:31`) and fails at tick (:86). Run: `bt.valid(["act","Pick","cup","arm=left"])` is True; ticking it raises `TypeError: _Leaves.act() takes 3 positional arguments but 4 were given` |
| `_LOC_ACTIONS` | robot_agent/core/planning/assist_loop.py:81 | MoveTo, Place, PutIn, Open, Close take a place. The same idea again: `robot_agent/core/planning/reconcile.py:31` (`AT_PLACE_ACTIONS`), `robot_agent/core/planning/mapper.py:45`, `robot_agent/contract/legacy_adapter.py:30` (`_LOCATION_ACTIONS`, a third set: MoveTo, Place, PutIn), `robot_agent/core/planning/search.py:31` |
| `NOT_PLAN_STEPS` | robot_agent/core/planning/robot.py:38 | Observe, Verify, Ask, Query are not plan steps |
| `_ABILITY` | robot_agent/core/planning/robot.py:42-45 | 8 actions and their understand-prompt wording |
| `ORDER`, `ACTION_TEXT`, `_CAPS` | robot_agent/core/planning/robot_prompt.py:22, :26-37, :40-42 | 10 actions with argument word and description; 7 capability words |
| `ROLES` | robot_agent/core/planning/action_roles.py:24; robot_agent/contract/manifest.py:99 | 7 roles a pack names an action for: put_down, move, open, close, find, pick, say (a role not declared: the option part does nothing, never a fallback to a core name: `robot_agent/core/planning/action_roles.py:11-12`) |
| `RELEASES`, `OPS` | robot_agent/core/planning/plan_repair.py:52-54 | Place, PutIn; 13 repair operators: hoist dedupe insert room putin fetch action say unclose complete finish recipient unopenable |
| `_NOOP`, `_PICK_HELD` | robot_agent/core/planning/plan_tidy.py:27-28 | violation codes treated as no-op / duplicate-pick steps |
| `ROBOT_ACTIONS` | embodiplan base.py line 53 | 14 names: MoveTo Find Pick Place PutIn Open Close TurnOn TurnOff Wash Sit LieOn Serve Wait (+ Heat Cool Clean only with the env var `EMBODIPLAN_EXTRA_ACTIONS`, base.py lines 65-68) |
| per-action recovery | robot_agent/core/planning/assist_loop.py:3701-3703 | `{"Find","Pick","MoveTo","Open","Place","PutIn"}` -> a `_recover_*` method; any other action has no ladder and goes to the planner |
| `STEP_LADDER` | robot_agent/contract/recovery.py:97 | only `Pick` (GRASP_FAILED, may replan) and `MoveTo` (BLOCKED, may not) |
| `DEFAULT_RECOVERY` | robot_agent/contract/recovery.py:66-91 | 13 failure codes with retries and next action; a pack overrides per code (data), the default is household |
| failure patterns | robot_agent/contract/failures.py:26-57 (`_RULES`, 13 English regexes), :20 (`SOFTWARE_ERR`) | text patterns that classify a skill's message into a code; UNKNOWN when nothing matches |
| `VERBS`, `PHRASES` | robot_agent/core/planning/announcer.py:25-39, :42-55 | English verbs of 27 action names (15 CamelCase, 12 lower-case skill names) and the 10 progress templates; the pack's `verb.<Action>` wins (:141) |
| `PLANNER_KEYS` | robot_agent/contract/manifest.py:86-96 | the closed list (72 names) of keys a pack may write in `planner:`; the open door is `planner.options.<option>.<key>` (`robot_agent/core/planning/pack_data.py:1-29`) |
| manifest `limits` | robot_agent/contract/manifest.py:104 (`LIMIT_KEYS`) | only `payload_kg`, `max_object_size_m` |
| manifest `capabilities` | robot_agent/contract/manifest.py:544-557 (`_check_caps`), robot_agent/core/planning/robot.py:122-130 | base, lift, head, drawer must be bool; arms / hands counts; core default: one hand, Find only looks (`find_reaches` False) |

### Phrases (the core's English text)

| table | where | what |
|---|---|---|
| `CORE_PHRASES` | robot_agent/core/planning/speech.py:24-87 | 62 English phrases, 30 of them questions (`q_*`), the rest read-back pieces (`rb_*`), separators, `say_*`, `iss_*`; each can be overridden per language in the pack i18n; lookup order: pack i18n, language pack block `speech`, core English (robot_agent/core/planning/speech.py:235-246) |
| `HELP_PICK`, `HELP_MOVE` | robot_agent/core/planning/speech.py:89-90 | fixed English ends of the Pick / MoveTo help questions (a bench parser anchors on them) |
| `CORE_TEXT` | robot_agent/core/planning/robot.py:21-34 | 8 English defaults (`verify_failed`, `verify_unknown`, `sensor_unknown`, `robot_description`, `cannot_do`, `delivery`, `llm_budget`, `yes_or_no`) |
| `BUDGET_TEXT` | robot_agent/core/planning/budget.py:23 | the budget-end sentence |
| `UNDERSTAND_PROMPT` | robot_agent/core/planning/assist_loop.py:329-356 | "You turn a household robot task into goals", example kinds, "tennis ball", "red cup" |
| `_CANNOT`, `_CAN_OPEN` | robot_agent/core/planning/assist_loop.py:844-846 | what a household robot cannot do (throw, pour, cook, wash, climb, open windows or room doors, use tools, handle knives near people, leave the house) |
| goal prompt lines | robot_agent/core/planning/goals.py:38-45, :51-53 | the understand line of go / say / deliver / give |
| `_SAY` | robot_agent/core/planning/understand.py:469-471 | English reasons of the "issues" check |
| checker findings | robot_agent/core/planning/assist_loop.py:1354-1421 | 20 English sentence templates sent to the planner, including "use MoveTo, Find, Pick, Place, PutIn, Open, Close" (:1355) |
| failure messages | robot_agent/core/planning/assist_loop.py:3536-3537, :3759 | "I can't pick things up from the floor", "something is wrong with my hardware" |
| speak keys that survive the language layer | robot_agent/core/planning/speech.py:95-96, :109 | `READBACK_KEYS`, `READBACK_PLAIN`, `APOLOGY_KEYS` |

### Words, answers and how they are read

| table | where | what |
|---|---|---|
| yes / no words | robot_agent/interact/dialog.py:39-40 (compiled at import from every language pack: robot_agent/core/planning/lang/en.json:146-163) | DATA (language packs), compiled once; a pack cannot add words per robot |
| `yes_no` | robot_agent/interact/dialog.py:119-130 | closed three-way logic: a no-word returns False; else a yes-word returns True; anything else returns False with the text as a correction. Run: "Use the measured clip positions and try once more." -> `(False, same text)`; "Yes. Use it" -> `(True, '')`; "No, leave it." -> `(False, 'leave it')`; "go ahead" -> `(True, '')` |
| `read_yes_no` | robot_agent/interact/dialog.py:156-200 | four kinds: yes, no, correction, unclear (all tokens fillers or near-misses of yes/no), silence. Run: "Both. One pipe goes on each wall." -> `Reply(kind='correction')` |
| `_YES`, `_NO` | robot_agent/core/planning/verify_vlm.py:29-30 | English tokens for a VLM verdict |
| bt `confirm` | robot_agent/options/bt_goal.py:175-177 | `yes_no(ans)[0] is True`: the answer to a confirm is one bit |
| bt `ask` | robot_agent/options/bt_goal.py:171-173, :136-143 | the answer is matched against word lists named by the tree (`block.key` of the language packs): the open route to "answer in words" |
| menu answers | robot_agent/options/bt_goal.py:119-127 (`read_menu`: stop, place, retry), robot_agent/options/ask_next.py:31-32 (`CHOICES`, `MENU`) | closed answer kinds |
| speaker words | robot_agent/core/planning/goals.py:152 (`SPEAKER_WORDS`), robot_agent/core/planning/assist_loop.py:101 (`_USER_WORDS`, a second copy), robot_agent/core/planning/persons.py:31-35 (data from packs) | three places say who "me" is |
| English verb regexes | robot_agent/core/planning/understand.py:155-165, :309-315; robot_agent/core/planning/goal_change.py:28-41; robot_agent/core/planning/goal_filter.py:28 | English move / drop / destination wording used to check or edit goals |

### Per-robot or per-domain constants

| constant | where | value |
|---|---|---|
| `clarify_rounds` | robot_agent/core/planning/assist_loop.py:382 | 3 |
| `max_replans` | robot_agent/core/planning/assist_loop.py:386 | 6 |
| `edit_rounds`, `edit_reserve` | robot_agent/core/planning/assist_loop.py:389, :391 | 3, 6 |
| `understand_samples`, sample temperature | robot_agent/core/planning/assist_loop.py:416, :429 | 2, 0.7 |
| `person_seat_m`, `person_surface_m` | robot_agent/core/planning/assist_loop.py:482-483 (the second default again at :1124) | 1.0 m, 1.2 m ("a priori, not tuned": robot_agent/core/planning/persons.py:36) |
| `health_wait_s` | robot_agent/core/planning/assist_loop.py:436 | 0.5 s |
| `search_total` | robot_agent/core/planning/assist_loop.py:479 | 8 |
| `DEFAULT_LLM_BUDGET` | robot_agent/core/planning/budget.py:20 | 20 calls |
| `SECOND_LOOKS` | robot_agent/core/planning/assist_loop.py:3937 | 2 |
| same-failure stop | robot_agent/core/planning/assist_loop.py:3693 | `seen_fail > 2` |
| coverage / restate limits | robot_agent/core/planning/assist_loop.py:3287 (pack `planner.coverage_limits`, no cap if absent), :2630 (`restates`: 0.6 / 3 / 4, "a priori") | data with a default |
| `ABSENT` retries | robot_agent/contract/recovery.py:82 (comment says "assist_loop's search_max") | 3 |
| `lab_log` cap | robot_agent/core/planning/assist_loop.py:609 | 50 entries |
| geometry defaults | robot_agent/core/planning/assist_loop.py:401-410 | from the manifest (`arrive_tol_m`, `reach_m`, `reach_far_m`): data, the correct pattern |

Nothing in `robot_agent/core`, `robot_agent/options`, `robot_agent/contract` or `robot_agent/interact` names a robot, a site or a benchmark in code (`forbidden_modules` in `tools/arch_rules.json`). The coupling is to a
household DOMAIN, not to a named robot.

## Household vocabulary in the loop

Hard-coded words and branches inside `robot_agent/core/planning/assist_loop.py` (all read).

### a) Where a goal kind decides behaviour (the switch points of one goal's life)

| step | lines | what is branched on |
|---|---|---|
| understand schema and prompt | :307-327, :799-800, :913-916, :667 | kind enum, prompt lines for the kinds (`goal_kinds.prompt_lines`) |
| refusal by missing action | :936-941 (`_cannot_do`), used at :833, :900, :1842, :2193 | `goals.missing_actions` per goal |
| over limits | :861-882 | only `on` / `in` goals have an object to weigh (:870) |
| grounding | :995-1062 | template kinds (:998-1001), open / close (:944-952), `on` / `in` slots (:1003-1062), `in` into a non-openable (:1052-1055), person destination (:1090-1130) |
| read-back wording | :1236-1268 | `on` (to person / near person / from / plain), `in`, `go`, `say` (4 forms), template kinds, `open`, `close` |
| gate | :1308-1309 (`need_open`, `need_close`), :1484-1501, :1504-1521 | one `if t == ...` chain per use, repeated three times (`_missing`, `_reaches`, `_goals_hold`) |
| planning | :2686-2694 (compile template goals), :2773-2797 (sub-goals, say / deliver steps) | `goals.is_template`, `compile_steps` |
| execute | :3369-3373 (last `go` goal: return to its place), :3417-3418 (observe only `on` / `in`) | |
| goal change, task stack | :3103-3106, :3161 (a held object becomes an `on` goal) | |

### b) Action names in the loop (67 lines name one of the 13 core action names in quotes; by function)

Measured: `grep -cE '"(MoveTo|Find|Pick|Place|PutIn|Open|Close|Say|Announce|WaitForUnload|Handover|Dock|Check)"'` returns 67 (63 for the seven manipulation names).
By function: `_LOC_ACTIONS` :81; `_ground_step` :1276, :1284 (`action_model.ROBOT_ACTIONS`); the checker :1317, :1354-1421; `_simulate_goals` :1459-1470; `_bind` :1554-1565 (Say / Announce, Find `min_conf` / `verify`);
`_effect` :1607-1620; `_post_check` :3606-3627 (a release looked at twice, a Find seen out of reach); `_precondition` :3470-3474; `_guard` :3522-3540 (Pick with a stale hold, Place with a lost object);
`_step_failed` :3701-3703 (the per-action recovery dict); `_refind_after_pause` :3239-3242; `_look_at_untouched` :3340-3341; `_observe_goal` :3431-3434; `_end_action` :3376-3386; `_generate_and_certify` :2694 and
`_lab_subgoals` :2785 (`"Say" in plan_actions`); `_sage_replan` :2964 and :3264 (`"Check"`).

### c) English text the loop itself speaks or asks

Questions: :1079 and :1123 ("Where are you?" / "Where is X?"), :951-952 (which drawer), :987-989 and :1048-1049 (which place / which x), :1007-1008 (where to put).
Wording of every finding the checker sends to the planner: :1354-1421. Failure messages: :3536-3537, :3759, :3524 and :3533. English fallbacks in `_goal_phrase`: :1251-1268. Prompt flags that rewrite the prompt by
`str.replace` of fixed English sentences: :807-820. Replan reasons in English with the failure code in them: :2971, :3836-3880 ("Look next at", "it is out of reach from").

### d) English words and patterns

Filler words that `Places` strips before matching: :179-180 ("the", "of", "on", "in", "at", "kitchen's", "it", "is", ...); the English prepositions `" in the "`, `" in "`, `" at "` replaced at :175;
`_strip_article` :114 (the, a, an, my, your); the English regex of a person's location answer :1074; the English regex of an "empty request" reason :891-892; the literal `"floor"` as a place name at :980 and in
`robot_agent/core/planning/step_ladder.py:159`.

### e) Domain concepts built into the core

- The place key `name@room` (`Places.head`, `Places.room`, :133-142; 23 lines with a quoted `"@"`; `.replace("@", " in the ")` at :987, :1046, :1230, :1352, :1407, :3420;
  `robot_fleet/voice.py:56-61` makes the same convention again in the fleet; embodiplan's `_member` splits on `@` as well: action_model.py line 246).
- Openable containers (`Places.openable` :144-148) and drawers (:948, :1053), a hand (`g["hand"]`, :1103-1129), a person as a destination (`persons.is_person`, :1239, :1259), the speaker (:1010, :1065-1079),
  a surface beside a person (`near_person`, :1243-1247).
- A single `holding` slot in the belief and the single hand (`world.holding`, :1455, :1535-1541, :3522-3540) and the closed state fields from `embodiplan`; the hands count enters only through
  `Capabilities.hands` (action_model.py line 65; `robot_agent/core/planning/robot.py:122-130`).
- Floors as places for a dropped object: `step_ladder._refind_dropped` looks for a place whose head is `floor` in the same room (`robot_agent/core/planning/step_ladder.py:157-161`).

## Fleet tables

Repo `/media/keti/workdir/remote_dir/robot_fleet` at `d74f326`, package `robot_fleet/robot_fleet/` (read only; other people's uncommitted files there were not touched). Sizes (`wc -l`): orchestrator.py 622, scheduler.py 200, spoke.py 193,
api.py 192, narrator.py 169, history.py 151, config.py 138, assign.py 130, voice.py 109, graph.py 104, models.py 123, readback.py 63, worldnote.py 68, talk.py 40 (2908 lines in 25 files).

| table | where | what is closed |
|---|---|---|
| `_NEEDS` | robot_fleet/scheduler.py:16-23 | goal kind -> alternatives of required plan actions: on {Pick,Place}; in {Pick,PutIn} or {Pick,Place}; open {Open}; close {Close}; go {MoveTo}; say {Say} |
| use of `_NEEDS` | robot_fleet/scheduler.py:40-54 | `_NEEDS.get(goal.type)` None -> "unknown kind: infeasible" (:41-43); a robot is feasible when one alternative is inside its `plan_actions` (:48-50) and every place the goal uses maps to the robot's own key (:51-52); a kind with no entry has no feasible robot, so the manager can never assign it, whatever the robot says |
| difference to the robot | robot_agent/core/planning/goals.py:33-46, :69-70 vs robot_fleet/scheduler.py:16-23 | the fleet has no `deliver` (robot: MoveTo + WaitForUnload) and no `give`; the fleet's `say` needs only Say while the robot's `say` with a `to` also needs MoveTo; the two tables are kept by hand and can drift |
| `_CONTAINER_KINDS` | robot_fleet/graph.py:26, used :39-40 | `open` / `close`: the object counts also as a place in the footprint |
| footprint and conflicts | robot_fleet/graph.py:35-49, :60-69 | a goal's footprint is object + `from` + `to` (+ the object for containers); two goals conflict when objects or logical places overlap (:66-69): every kind is assumed to move one object between places |
| goal model | robot_fleet/models.py:31-44 | `type: str` (open), `object`, `from_`, `to`, extra keys kept: the same four slots as robot_agent |
| what a robot reports | robot_fleet/spoke.py:56-61; robot_fleet/models.py:108-116 (`RobotInfo`); robot_agent/api/robot_info.py:17-21 | the manifest answer carries `plan_actions` (sorted), `capabilities`, `places`, `site`, `version`; the fleet keeps only version, plan_actions, places, site; the robot does NOT say which goal kinds it can take, what each needs, or in what words it speaks |
| voice | robot_fleet/voice.py:64-70; robot_fleet/voice_packs/en.json:41-50; robot_fleet/voice.py:56-61 | goal wording is DATA per language (`goal.on`, `goal.in_from`, ..., `goal.default` for an unknown kind): already open; `place_name` uses the `name@room` convention |
| holding memory | robot_fleet/worldnote.py:30-32, :40-47; robot_fleet/orchestrator.py:78, :448, :581; robot_fleet/scheduler.py:68, :74 | the fleet reads `world.holding` from run events and prefers a robot with an empty hand: a single-hand manipulation idea, tied to a belief field name of robot_agent |
| assignment keys | robot_fleet/assign.py:18, :26-28; robot_fleet/scheduler.py:102-114 | keyed by the goal kind string, history and estimates per (robot, kind): open (any kind works) |
| object stickiness | robot_fleet/readback.py:29-34; robot_fleet/scheduler.py:124-127 (`_sticky_robot`) | "a goal on an object of an earlier goal stays on that robot": assumes `goal.object` names the one thing that travels; the scheduler's own use is switched by `policy.sticky_chains` (`robot_fleet/scheduler.py:126`), the read-back estimate always applies it |
| question events | robot_fleet/talk.py:19-22 (`QUESTION_EVENTS`: clarify, ask, question; or any event with `listen`) | the list of robot events that wait for a person |
| read-back rule | robot_fleet/orchestrator.py:242 | a `clarify` with kind None, "" or "confirm" after the goals is taken as the robot's read-back (the S3 bug of framework_fit; robot_agent sends kind-less questions at robot_agent/core/planning/assist_loop.py:2576) |
| understand request | robot_fleet/spoke.py:140 | `options: {"goals_only": "1", "confirm_task": "0"}` as strings; robot_agent reads them without coercion (robot_agent/core/planning/assist_loop.py:397-398: the string "0" is true) |

Reading: only `_NEEDS`, `_CONTAINER_KINDS`, the footprint rule and the one-hand `holding` idea are closed kind knowledge; voice and assignment are already open. The robot's manifest endpoint
(`robot_agent/api/robot_info.py:17-21`) is the natural carrier for per-kind needs: it is read by `HttpSpoke.manifest` (`robot_fleet/spoke.py:56-61`) on every refresh and already holds `plan_actions` and `capabilities`.
What a fleet manager needs from a robot to drop `_NEEDS`: (1) for each goal kind the robot takes, its alternatives of needed actions (today derived from `GOAL_KINDS` inside the robot, not exposed); (2) which slots of
a kind are places and which are objects (for the footprint); (3) the kinds that carry state between goals (the `holding` idea); (4) the question kinds a robot may raise (to replace the kind-less read-back guess).

## Skills and actions

### A. The actions the core names, and what it assumes about each

"Abstract action" = the name in a plan step (`{"action": "Pick", "object": "cup"}`); "skill" = the robot's own callable that the adapter binds it to (`RobotView.bind`, `robot_agent/core/planning/robot.py:99-105`;
`RobotAdapter.bind`, `robot_agent/contract/adapter.py:29-33`; `NamemapAdapter.bind`, `robot_agent/contract/legacy_adapter.py:91-118`). The action model rows are embodiplan `check` (action_model.py lines 266-372) and `apply`
(action_model.py lines 384-430). `Args` is what a plan step carries; everything else a skill needs comes from the adapter (`params = {"inputs": ...}` plus the adapter's `build_params`,
`robot_agent/contract/legacy_adapter.py:110-117`) or from the loop (`_bind`, `robot_agent/core/planning/assist_loop.py:1546-1566`).

| action | args of the step | where the core assumes it (besides the tables above) | precondition findings of the action model | effect on the belief | post-step check | own recovery |
|---|---|---|---|---|---|---|
| MoveTo | `object` = a place key | grounding to an ENV key :1276-1283; `name_for` -> `to_loc` robot.py:113-119; effect sets `arrived` | empty_object (error), already_there (warning): action_model.py lines 303-307 | `arrived = o`; `found` cleared unless it is `o`: action_model.py lines 399-404 | pose sensor within `arrive_tol_m` (`robot_agent/core/planning/verify.py:234-248`); stale pose = not arrived (`robot_agent/core/planning/sensor_health.py:103-114`); `guard_arrived` before the steps in `AT_PLACE_ACTIONS` (`robot_agent/core/planning/reconcile.py:31`, :178-197) | STEP_LADDER BLOCKED: retry, ask help (`HELP_MOVE`), stop (`robot_agent/core/planning/step_ladder.py:83-103`) |
| Find | `object`; the loop adds `min_conf`, `verify` params if the skill declares them (:1562-1565) and `inputs` = the instance the person chose (:1554-1555) | `object_places` belief for find_wrong_place (:1310-1312); `candidates` in the result -> which_one (`robot_agent/core/planning/which_one.py:71-104`); `found_pose` | empty_object, find_held, find_wrong_place (context code): action_model.py lines 309-317 | `found = o` (action_model.py line 405) | `find_precheck`: stale camera = PERCEPTION_UNAVAILABLE without running the skill (`robot_agent/core/planning/sensor_health.py:87-100`); a Find seen beyond `reach_far` is a failed "reach" layer (`robot_agent/core/planning/assist_loop.py:3619-3627`) | search ladder: mark searched, relook, second look, search legs, ask where, phantom (`robot_agent/core/planning/assist_loop.py:3808-4063`); ABSENT = 3 retries then ASK_HELP |
| Pick | `object` (empty: the found one, `robot_agent/contract/legacy_adapter.py:101-106`) | `holding_since` timestamp (:1610); stale hold fixed by a second sensor read (:3522-3526) | already_holding, no_find_before_pick, pick_not_found: action_model.py lines 319-325 | `holding = o or found`; `found = None` (action_model.py lines 407-412) | the holding sensor, `expected` True (`robot_agent/core/planning/robot.py:179-182`); two more reads decide a sensor-only failure (:3698-3699; `robot_agent/core/planning/step_ladder.py:32-35`) | STEP_LADDER GRASP_FAILED: retry (look again), ask help (`HELP_PICK`), replan; phantom for ABSENT (`robot_agent/core/planning/step_ladder.py:23-81`) |
| Place, PutIn | `object` = a place or container | `RELEASES` (`robot_agent/core/planning/plan_repair.py:52`); `_LOC_ACTIONS`; `placed[held] = where` (:1612-1621) | place_without_holding, empty_object, place_wrong_location, putin_closed_container: action_model.py lines 327-340 (PutIn only: closed container) | `holding = None` (action_model.py lines 413-414) | the holding sensor with `expect: false`; a release looked at twice (:3606-3618) | `_recover_place`: empty hand = dropped, REFIND from the floor of the room (`robot_agent/core/planning/step_ladder.py:132-175`) |
| Open, Close | `object` = a container | `_precondition` adds "robot is at the place" and "not while holding" (:3470-3473); `_observe_goal` skips a closed container (:3428) | empty_object, not_openable (needs `openable`), interact_without_find, already_open, close_not_opened, hand_full (one hand): action_model.py lines 342-360 | `opened` add / discard (action_model.py lines 415-419) | symbolic effects only unless the pack declares a sensor | `_recover_open` (`robot_agent/core/planning/search_hooks.py:143-160`): a failed Open inside a search gives that container up; else planner |
| Say, Announce | `object` = the sentence | `_bind` translates the text into the user's language (:1557-1561); `compile_steps` for `say` / `deliver` goals (`robot_agent/core/planning/goals.py:219-241`); `can_say = "Say" in plan_actions` (:2694, :2785); `quiet_skills` (`robot_agent/core/planning/robot.py:166-169`) | none (not in `ROBOT_ACTIONS`; `_ground_step` keeps its text as is, :1284-1285) | none | `isdone` only | none: goes to the planner |
| WaitForUnload | `object` = the item or "" | `goals.compile_steps` :219-241, `goals.reached` (`robot_agent/core/planning/goals.py:264-265`), `ORDER` and `ACTION_TEXT` (`robot_agent/core/planning/robot_prompt.py:22`, :35) | none | none | `isdone` only | none |
| a release action (e.g. `Handover`) | `object` = a person / place | recognised by its manifest effects containing `hand_empty()` (`robot_agent/core/planning/robot.py:132-141`); the literal name `"Handover"` is also in `robot_agent/core/planning/step_ladder.py:169` | none from the rules; `Capabilities.release_actions` is passed to `apply` (action_model.py lines 57-92, 395-397) | `holding = None` (`robot_agent/core/planning/assist_loop.py:1612-1621`, `_handed` set) | the post-check treats it like a Place (:3611-3618); `_observe_goal` trusts it (:3421-3424) | REFIND rung as for Place |
| Dock | `object` = a dock | `ORDER` :22; found by its effects text `docked(` / `arrived(` (`robot_agent/core/planning/assist_loop.py:3376-3386`) | none | none | pose if declared | via the pack's `planner.recovery.return_tries` (:3388-3413) |
| Wait (no-op) | `object` = seconds | `noop_actions` (`robot_agent/core/planning/robot.py:71-73`); the wait schedule of a hardware fault runs the pack's wait action (`robot_agent/core/planning/exec_integrity.py:63-72`, `robot_agent/core/planning/assist_loop.py:3769-3790`) | none | none | none | none |
| Ask | `required_params[0]` = the question; optional `retries`, `lang` | `_ask_io` (`robot_agent/core/planning/assist_loop.py:1188-1206`); `RobotView.ask_spec` (`robot_agent/core/planning/robot.py:193-195`); a robot with no Ask cannot be asked: every question returns "" | n/a (`NOT_PLAN_STEPS`) | n/a | n/a | n/a |
| Observe, Verify, Query | none | `adapter.observe` / `health` (`robot_agent/core/planning/robot.py:197-208`); `holding_sensor`, `pose_sensor` pick the Verify / Observe skills by the manifest's verify specs (:179-191); `vlm_ask` (:263-267) | n/a | n/a | n/a | n/a |
| Check (virtual) | `{"action": "Check", "object": "goals"}` | passed as the "failed step" to the planner when the plan ended but the goals do not hold (`robot_agent/core/planning/assist_loop.py:3264`) | n/a | n/a | n/a | replan |
| TurnOn, TurnOff, Wash, Sit, LieOn, Serve, Wait, (Heat, Cool, Clean) | `object` | only in embodiplan (`ROBOT_ACTIONS`, base.py line 53; rules action_model.py lines 342-370); no branch in the loop names them | per the model | `on` set for TurnOn / TurnOff (action_model.py lines 420-425) | none | none |
| a custom action (PEX `MeasureClips`, `Reorient`, `UseMeasuredClips`, `Seat`, `Crimp`; home and factory `Look`) | `object` only | `_ground_step` keeps the text as is for any name not in `ROBOT_ACTIONS` (:1284-1285); `RobotView.plan_actions` includes it when the manifest lists it (:84-89); the checker says "use MoveTo, Find, Pick, Place, PutIn, Open, Close" (:1355) when a plan uses an unknown one | none: "robot-specific actions: no symbolic precondition" (action_model.py line 371) | none | its declared `effects` only if they use one of six predicates (`robot_agent/core/planning/verify.py:53-70`); declared `verify` specs run as for any skill | none: its failure reaches the planner (:3701-3703) or, with `bt_goal`, the tree (`robot_agent/options/bt_goal.py:225-232`) |

The action model's belief is closed (`arrived`, `found`, `holding`, `opened`, `on`; a custom action cannot add a field), the preconditions are `if/elif` code per action name (action_model.py lines 303-370), and the
codes are strings. The robot-specific part that is already DATA: `capabilities.hands`, `find_reaches`, `release_actions` (from effects), `openable` (ENV flag or adapter rule: `robot_agent/core/planning/robot.py:155-164`),
`containers` (`DEFAULT_CONTAINERS`, action_model.py lines 35-38, replaced by `openable` in the checker: `robot_agent/core/planning/assist_loop.py:1311`).

### B. What a pack declares today (read) and what the core evaluates

| pack | robot (manifest) | languages | skills -> action (`name:action`) | `planner` keys and options |
|---|---|---|---|---|
| kcare_robot | kaair | ko, en | move:MoveTo find:Find pick:Pick place:Place open_drawer:Open close_drawer:Close grasp_succeed:Verify mobile_pose:Observe ask:Ask llm:Query | `seed_memory` only; capabilities: base, arms 1, hands [right], lift, head, drawer, voice, `find_reaches` False, `vlm_verify` False, `standing_objects`, `lying_objects` (kcare_robot/kcare_robot/configs/manifest.yaml) |
| symbolic_robot home | homeloop | en | move, find, pick, place, put_in:PutIn, open, close, handover:Handover, say:Say, announce:Announce, ask, look:Look, vlm_check:Query, wait:Wait, dock:Dock, grasp_check:Verify, pose:Observe | `seed_memory`, `llm_budget`, `translate_budget`, `input_pivot`, `translate_model`, `search_legs`, `notes` |
| symbolic_robot factory | shopfloor | en | move, find, pick, place, put_in, open, close, say, ask, look:Look, grasp_check, pose | `llm_budget`, `translate_budget`, `input_pivot`, `search_legs`, `detector_miss_rate`, `notes` |
| pex_robot | pex_robot | en | move, find_pipe:Find, acquire_pipe:Pick, reorient_pipe:Reorient, measure_clips:MeasureClips, use_measured_clips:UseMeasuredClips, seat:Seat, crimp:Crimp, release:Place, say, ask, grasp_check:Verify, pose:Observe | `llm_budget`, `translate_budget`, `input_pivot`, `notes`, `options`: `look_then_ask` off, `bt_goal` with a tree for kind `on` (pex_case/pex_robot/configs/manifest.yaml lines 22-44) |

(Read with the shared venv's yaml; symbolic_robot's manifests are under `/media/keti/workdir/remote_dir/symbolic_robot/symbolic_robot/domains/{home,factory}/configs/manifest.yaml`;
the test packs `tests/fakebot`, `tests/fakehome` copy the kcare and the home shapes.) Each pack's skills declare `params` (JSON schema), `pre`, `hold`, `effects`, `verify`, `interruptible`, `errors`, `version`
(`robot_agent/contract/types.py:189-218`); PEX declares for example `pre: ["found(?inputs)", "hand_empty()"]`, `effects: ["holding(?inputs)", "not found(?inputs)", "not hand_empty()"]` for its Pick.

What the core does with each declared field (measured by reading every reader):

| field | read by | effect |
|---|---|---|
| `name`, `action` | `skill_map`, `plan_actions`, `bind` (`robot_agent/core/planning/robot.py:64-89`) | binds the abstract action to the skill; any CamelCase action accepted (`robot_agent/contract/manifest.py:108`) |
| `params` | `required_params[0]` as the main input name (`robot_agent/core/planning/verify.py:165`, `robot_agent/core/planning/assist_loop.py:1200`), `param_names` for optional `retries` / `lang` (:1201), `Find` props `min_conf`, `verify` (:372-380), prompt text (`robot_agent/core/planning/robot_prompt.py:111`) | NOT validated against the schema at run time: a step carries one `object` and a bind result is a free dict |
| `pre`, `hold`, `errors` | `robot_agent/contract/manifest.py:684-692` only | parsed, never evaluated |
| `effects` | `effects_check` after success (`robot_agent/core/planning/verify.py:73-95`) for six predicates; `release_actions` (string match of `hand_empty()`); `_end_action` (string match of `docked(` / `arrived(`) | partial |
| `verify` | `StepVerifier` (`robot_agent/core/planning/verify.py:114-232`): kinds `symbolic`, `sensor`, `vlm` (`robot_agent/contract/types.py:155`) | used |
| `interruptible` | `RobotView.interruptible` -> `run_skill(..., interruptible=)` (`robot_agent/core/planning/assist_loop.py:3581-3582`) | used |
| `version` | none | not read |

### C. Step and skill-call signatures, and the one place a result becomes a verdict

- Step: `{"action": str, "object": str, ...}`; extra keys set by the loop: `grounded` (bool, the object is an ENV key: `robot_agent/core/planning/assist_loop.py:1283`), `verify_goal` (the place a Find looks at to prove a goal: :3341, :3643-3644, :3667),
  `hand` on goals (not steps). A direct-planner step also carries `skill` and `params` and binds through `concrete.bind` (`robot_agent/core/planning/concrete.py:82-95`, `robot_agent/core/planning/robot.py:103-105`); a step with `skill` and no
  `action` is "skill-only": no rule, no effect (`robot_agent/core/planning/assist_loop.py:1333-1334`, :2929).
- Skill call: `run_skill(sr, skill, params, node, log_fn, strict, raise_errors, interruptible)` (`robot_agent/core/planning/robot.py:318-334`): an exception is a failure whose code is read off its text; a legacy dict `{isdone, msg, code, device, ...}`
  becomes a `SkillResult(ok, code, msg, device, data)` (`robot_agent/contract/types.py:62-153`); with `strict` an answer with no verdict is a failure with code UNKNOWN (the planner's rule), without it success (the direct path).
- Result data the loop reads: `ins[obj].pose_3d` / `loc_3d` of a Find (`robot_agent/core/planning/assist_loop.py:1628-1638`), `candidates` (`robot_agent/core/planning/which_one.py:36-44`), `pose` of a sensor (`robot_agent/core/planning/verify.py:224`),
  `text` / `answer` of an Ask (:1204), `found` (`robot_agent/core/planning/which_one.py:110`). These keys are a de-facto skill result contract that is not declared in the manifest.

### D. Failure codes

13 codes (`robot_agent/contract/types.py:15-31`): absent, perception_unavailable, unreachable, blocked, grasp_failed, dropped, hw_fault, estop, timeout, cancelled, software, unknown, budget_exceeded.

| code | who produces it | default recovery rule (`robot_agent/contract/recovery.py:66-91`) |
|---|---|---|
| ESTOP, CANCELLED, SOFTWARE, BUDGET_EXCEEDED | the skill, or `classify_legacy_failure` patterns (`robot_agent/contract/failures.py:26-57`); the loop for BUDGET_EXCEEDED (`robot_agent/core/planning/budget.py:44`) | STOP, `retry_allowed=False` |
| HW_FAULT | skill / pattern | HEALTH_CHECK once, then STOP, report |
| PERCEPTION_UNAVAILABLE | skill / pattern / `find_precheck`, `arrival_check` (`robot_agent/core/planning/sensor_health.py:87-114`) | WAIT_HEALTH 2 times, then STOP |
| TIMEOUT | skill / pattern | HEALTH_CHECK once, then REPLAN |
| UNREACHABLE | skill / pattern / `localization` layer "stopped x m from" | REAPPROACH once, then REPLAN |
| BLOCKED | skill / pattern | RETRY once, then ASK_HELP |
| ABSENT | skill / pattern / the loop's goal check (:3365, :3441-3450) | CONTINUE_SEARCH 3 times (marks the place searched), then ASK_HELP |
| GRASP_FAILED | skill / pattern ("gripper is empty", "still in the gripper") | RETRY once, then REPLAN |
| DROPPED | skill / pattern / the loop (:3365, :3538) | REFIND once, then REPLAN, report |
| UNKNOWN | no usable text | VERIFY once, then REPLAN |

The patterns that turn a message into a code are 13 English regular expressions (`robot_agent/contract/failures.py:26-57`): a skill that reports in another language, or in words the patterns do not know, is UNKNOWN unless it sets `code`.
A pack may override any rule through `recovery_overrides` (`robot_agent/contract/recovery.py:114-139`, `robot_agent/contract/legacy_adapter.py:50-52`); there is no way to add a code.

## Verifiers and recovery

### A. Where the run is checked (in the order a run meets the checks)

| # | check | where | what it decides | closed part | options that hook it |
|---|---|---|---|---|---|
| 1 | refusal of the understanding | `typed_refusal` (`robot_agent/core/planning/understand.py:695-715`); `_cannot_do` (`robot_agent/core/planning/assist_loop.py:936-941`), `_over_limits` (:861-882), `_capability_check` (:884-902) | refuse before any plan: a goal kind needs an action the manifest lacks, an unsafe / infeasible verdict with a mark word, a payload / size limit | `GOAL_KINDS` needs; limits only for `on` / `in` (:870); the English "It cannot" line (:844-846) | `hazard`, `heat_safety` (`on_goals`, services `forbids`, `refused_goals`) |
| 2 | goal validator | `goals.conflicts` (`robot_agent/core/planning/goals.py:122-148`), `_two_places` (`robot_agent/core/planning/assist_loop.py:1895-1905`) | one object with two destinations is a question; an open and a close of one container keep the last | kinds on/in/open/close; no off switch | `ask_agree` / `dialog_agree` only change how the question is declared |
| 3 | read-back against the words | `_goal_readback` (:2331-2357), `understand.find_issues` (`robot_agent/core/planning/understand.py:399`), `_read_back` (:2438-2508) | unsure goals are re-sampled, asked, or read back and confirmed | English verb regexes, `Places` fillers | `on_issues`: answer_align, name_recover, goal_ledger, entity_cover, ask_agree, ask_focus, shared_instances, heard_check |
| 4 | plan gate | `_check` (:1300-1329), `_check_step` (:1331-1347), `_word_step` (:1349-1390), `_blocking` (:2902-2912), `_hard_findings` (:2922-2941); rounds in `_generate_and_certify` (:2682-2742): round 0 = check, "symbolic" = `plan_repair` operators (13), round 1 = the planner's sub-goal repair, round 2 = one fresh plan | an uncertified plan never runs (:2675-2679); findings are critical (go back to the planner) or only mentioned | per-action wording (20 templates), `_missing` per kind, the action model, `OPS` | `on_plan`: place_find, gate_prune, precond_repair, plan_complete, plan_release, replan_guard; `on_plan_findings`: fetch_guard; `on_replan_findings`: replan_guard, repair_sync; services `gate_off`, `repair_kwargs`, `no_refetch`, `keep_after_dedupe` |
| 5 | pre-step guard | `_precondition` (:3467-3475) = `symbolic_check` (`robot_agent/core/planning/verify.py:31-50`) + two loop rules (at the place; not holding for Open / Close); `_guard_arrived` (:3502-3513); stale hold and lost object (:3522-3540); then ONE planner repair per signature (:3543-3549) | do not move when the belief says the step cannot run | per-action rules of embodiplan; `Pick` / `Place` / `PutIn` literals in the guard | `on_step`: reach_delivery, reach_check |
| 6 | per-step postconditions | `StepVerifier.verify` (`robot_agent/core/planning/verify.py:144-196`), `_post_check` (:3599-3630), `verdict` (`robot_agent/core/planning/verify.py:251-262`) | layers, each `ok` / not ok / unknown: (1) `isdone` of the skill; (2) the declared `effects` that hold in the belief after the action model applied the step (six predicates); (3a) the manifest's `vlm` VerifySpecs (`robot_agent/core/planning/verify_vlm.py:68-141`); (3b) a legacy VLM hook for `vlm_actions`; (4) the manifest's `sensor` VerifySpecs: a result with `pose` is compared to the ENV loc within `arrive_tol_m` (`robot_agent/core/planning/verify.py:234-248`), any other sensor must have `isdone == expect`; none declared = an `unknown` layer that passes; plus `arrival_check` (stale pose) and the loop's rules (release looked at twice :3606-3618, a Find seen beyond reach :3619-3627). A step is ok iff every layer is ok (`verdict`) | the layer order and names; six predicates; a sensor that broke (HW_FAULT, TIMEOUT, PERCEPTION_UNAVAILABLE, SOFTWARE) counts as no reading, not as a failure (`robot_agent/core/planning/verify.py:25`, :218-221) | `on_step_done` (reach_delivery) observes |
| 7 | belief early exit | `_step_succeeded` (:3632-3658) | when the goals already hold in the belief the rest of the plan is dropped, unless it only closes (:3652-3657) | `_goals_hold` per kind | none |
| 8 | end of plan | `_plan_ended` (:3245-3274): `_goals_hold` (belief) then `_observe_goals` (:3345-3374, only when `cfg["goal_check"] == "observe"`, :3350), then `request_coverage` (:3254-3257, one extra LLM call); else a look at untouched goals (:3329-3343) or a replan with the virtual step `Check goals` (:3264) | "done" needs the belief AND, by default, a look | `_observe_goal` (:3415-3451) looks only at `on` / `in`; any other kind is `None` = unknown and stays on the belief (:3417-3418) | `on_goal_check`: reach_delivery; `on_second_look`: confirm_look |
| 9 | final | `_final_check` (:4066-4081): `on_final`, `_goals_hold`, memory record when verified, `on_goals_met`, `done success`; else `done failed` "plan finished but the goals do not hold" | the run's verdict and its `done` fields | same as 8 | `on_final`: unapplied_change, end_state; `on_goals_met`: hazard, heat_safety; `on_finish` observes |

"Done" semantics today, in one sentence: a goal holds when the plan's steps put it there in the belief (`placed[obj] == to`, container open / closed, template goals reached by steps) AND, for `on` / `in`, a Find
at the destination saw the object (True), or could not tell (None: kept as true), and a False withdraws the belief and becomes a failure with code DROPPED (if the last step placed it there) or ABSENT (:3365).
A goal kind with no observation (go, say, deliver, open, close, and any custom kind) is "done" by belief alone.

### B. Recovery: the order a failed step meets

`_step_failed` (`robot_agent/core/planning/assist_loop.py:3660-3708`):

1. the `step_done failed` event; a failed `verify_goal` Find just skips (:3667-3669).
2. option `reset_container` for a blocked PutIn (:3670-3673, :3710-3733; needs `Close`, `Open` in the plan actions: :3723).
3. the code: `RobotView.failure_code` = the skill's own code, else a classifier of the message (`robot_agent/core/planning/robot.py:275-281`, `robot_agent/contract/failures.py:26-57`); service `failure_code` may change it (fetch_guard: a silent Find miss is ABSENT, :3676).
4. the rule `policy.rule_for(code)` (`robot_agent/contract/recovery.py:66-91`, with the pack's `recovery_overrides`); the failure is noted (:3679).
5. hook `on_step_failed` (:3680): trace, bt_goal, reach_delivery, drop_search, reopen_on_miss.
6. `rule.retry_allowed` False (ESTOP, CANCELLED, SOFTWARE, BUDGET_EXCEEDED): the run ends (:3683-3686).
7. `_apply_policy` (:3735-3767): the n-th failure of (code, action, object) takes `rule.next_after(n)` (service `recovery_rule` may replace the rule: hw_wait); STOP ends the run; WAIT_HEALTH polls the adapter's `health()` for
   `health_wait_s` (0.5 s, :436) or runs the pack's declared wait schedule (`planner.recovery.wait`, :3769-3790); an unhealthy robot stops the run unless `more_tries` (:3754).
8. the same failure (action, object, place, reason with digits removed) a third time ends the run, except for Find (:3690-3694).
9. the per-action in-place ladder (:3697-3708), chosen by a dict keyed by the ACTION NAME (:3701-3703); a refuted step (VLM said it did not happen) skips it (:3700); the rung order is below.
10. else `_replan_or_fail` (:3025-3031) -> `_sage_replan` (:2960-2995): service `no_replan` (bt_goal: a tree's own step stays failed), a replan counter against `max_replans` (6, :386), no replan for a compiled plan (:2966), at most two
    planner calls per failure (one refinement with the gate's findings), the suffix goes through the gate again (:2989), an uncertified suffix is dropped (:2982-2988).

| action | ladder (in order) | constants | where |
|---|---|---|---|
| Find (code ABSENT) | look again here if it was lost here earlier -> doubt a container the belief says open -> relook by another sense (option `relook`) -> mark the place searched -> next leg of the search belief (spliced, no planner call) -> ask "where is it" once -> second look at the likeliest places -> stop when the places run out or `search_total` looks were made | ABSENT rule: 3 retries then ASK_HELP (`robot_agent/contract/recovery.py:82`); `search_max` default from that rule (`robot_agent/core/planning/assist_loop.py:384`); `search_total` 8 (:479); `SECOND_LOOKS` 2 (:3937) | `robot_agent/core/planning/assist_loop.py:3808-3884`, :3944-4030 |
| Find (UNREACHABLE) | REAPPROACH, then the planner with "out of reach from here" | rule: 1 retry then REPLAN | :3872-3873 |
| Find (PERCEPTION_UNAVAILABLE) | wait for health twice, then stop; never counts the place as searched | rule: 2 retries then STOP | :3874-3876; `robot_agent/contract/recovery.py:74-77` |
| Pick | phantom (ABSENT: look again once, then a false detection) -> sensor-only vote (two more reads) -> unknown object in the gripper = stop -> retry in place (look again) up to `pick_retries` -> ask for help once (`HELP_PICK`; "too far for my arm" past `reach_lateral`) -> replan (GRASP_FAILED `then`) -> stop | `pick_retries` = the GRASP_FAILED rule's retries (1, :387); `reach_lateral` = `reach_m` of the manifest (:408-409) | `robot_agent/core/planning/step_ladder.py:23-81` |
| MoveTo | retry once (a separate counter for an arrival the pose check refused) -> ask for help once (`HELP_MOVE`) -> stop | BLOCKED rule: 1 retry then ASK_HELP; `replan` False (`STEP_LADDER`) | `robot_agent/core/planning/step_ladder.py:83-103` |
| Place, PutIn | hand empty = dropped: REFIND (find on the room's `floor`, pick, go back, redo; no `floor` place = stop); else the planner | DROPPED rule: 1 refind then REPLAN | `robot_agent/core/planning/step_ladder.py:132-175` |
| Open | inside a search: give the container up, search on; else the planner | none | `robot_agent/core/planning/search_hooks.py:143-160` |
| any other action | straight to the planner | `max_replans` 6 | :3704-3708 |

The ladders are partly DATA already: the rung for each failure code, the retry counts and the next rung are a table (`robot_agent/contract/recovery.py:66-91`) a pack overrides per code; `STEP_LADDER` names which action uses which code's rule;
`step_rung` is a pure function (:100-111). What is closed: the code set (13), the rung vocabulary (10: `Recovery`, `robot_agent/contract/recovery.py:20-31`), the action-name dict at :3701-3703, the English help sentences, the `floor` convention,
and the choice of ladder per ACTION instead of per code or per kind of goal.

### C. Other bounded loops (cost and give-up limits)

| limit | value | where |
|---|---|---|
| LLM calls per run | 20 (pack `planner.llm_budget`; PEX 22) | `robot_agent/core/planning/budget.py:20`, `robot_agent/core/planning/assist_loop.py:419-420`; the end says `llm_budget` text (`robot_agent/core/planning/budget.py:23`, `robot_agent/core/planning/robot.py:31`) |
| translation calls per run | 10 (pack `planner.translate_budget`) | :490-491 |
| clarify rounds | 3 (+1 for the essential "where are you" question; rounds given back on progress, twice) | :382, :2563-2594, :2145-2156 |
| read-backs | 2, a third after a correction not read back yet | :2443-2452 |
| silence retries, yes/no re-asks | 1, 1 | :396, :431 |
| replans | 6 | :386 |
| goal-change rounds | `edit_rounds` 3, `edit_reserve` 6 | :389, :391 |

## Questions and answers

### A. Every question the core can ask

"Phrase" = where its words come from. A `LocalText` carries a key and slots; the robot says the pack's phrase for that key in the user's language (`robot_agent/core/planning/speech.py:235-246`: pack i18n, then the language pack's `speech` block, then
`CORE_PHRASES` for English only); a plain string (English) goes to the translator model (`robot_agent/core/planning/language.py:430`, budget `translate_budget`) or, if that fails, is replaced by `say_untranslated`.
"Kind" = the `kind` field of the `clarify` event; a question with no kind is indistinguishable from a read-back for a client that guesses (robot_fleet/orchestrator.py:242).

| # | question | phrase | emitted at | event kind | answer read by | next |
|---|---|---|---|---|---|---|
| 1 | read-back "I will ... Is that right?" | `q_confirm` with `rb_*` pieces; `rb_restated`, `rb_unsure` (`robot_agent/core/planning/speech.py:24-87`, :99-106) | `robot_agent/core/planning/assist_loop.py:2467`, :2128 | confirm | yes/no four-way (`read_yes_no`, `robot_agent/interact/dialog.py:187-200`) through `_ask_yes_no` (:2550-2561); a "no, ..." rest is a correction (`dialog_converge.correction_edit`, :2519-2525; `_patch_goals` :2527-2548) | yes: goals settled; bare no: question 3 or `q_which_part`; rest: patch or re-understand |
| 2 | "What should I do exactly?" / "I did not understand" / the model's own question | `q_what_exactly`, `q_unclear_task`; the model's `question` is free English | :1877-1880, :2623 | none | free text appended to the dialogue; the WHOLE task is understood again by the LLM (:2586) | next understand round |
| 3 | issue questions: where to put X, which X, "you mentioned the X", "what should I do", "did you mean X" | `q_where_put`, `q_which_x`, `q_mentioned`, `q_what_do`, `q_did_you_mean`, `q_repeat`; English only: "What should I {verb}?" (`robot_agent/core/planning/understand.py:622-646`) | via `_goal_readback` (:2331-2357) and `_ask_clarify` (:2563-2594, event at :2576) | none | free text, re-understood | next understand round |
| 4 | grounding questions: which drawer, what to say, where to go / take it, which place, where to put, which X, where are you, where is a person | `q_which_drawer` :951, `q_what_say` :962, `q_where_go` / `q_where_take` :970, `q_which_place` :988, `q_where_put` :1007, `q_which_x` :1048, `q_where_are_you` / `q_where_is_person` :1121-1122; English only: :1079 | `robot_agent/core/planning/assist_loop.py:944-1137` | none | free text; a person's location is read by `persons.place_in_answer` (`robot_agent/core/planning/persons.py:149`) | re-understood or grounded |
| 5 | two destinations for one object | English sentence with `_two_places_q` (`robot_agent/core/planning/goals.py:137-141`) | :1895-1905 | none | free text; with `dialog_agree` the options are declared and the answer settles the one not taken (:2587-2590) | re-understood |
| 6 | "I can read that two ways" (two views) | `q_which_reading` | :1980 (`_compare_views` :1943-1982, option `understand_views`) | none | a word that names one reading (`dialog_agree.names_option`) | goals chosen |
| 7 | "Which part is not right: what I take, where I put it, or who it is for?" | `q_which_part` | :2061, :2486, :2493 | none | free text | re-understood |
| 8 | "Could you say it again?" | `q_repeat` | :1823, :1850, :1862 (`misheard_question`, `robot_agent/core/planning/understand.py:714-718`, English) | none | free text | re-understood |
| 9 | coverage: "you also asked ... should I do it now?" | `q_coverage` | :3305-3309 | coverage | `_ask_yes_no`; yes adds a goal (`_goal_edit`) | replan |
| 10 | new command during a run: "Should I do that first?" | English sentence | :3126-3128 | new_task | `yes_no`, or the regex `\b(now\|first\|right away)\b` (:3133); silence = after | suspend or append |
| 11 | "I couldn't find the X. Where is it?" | `q_where_is` (English fallback text inline) | :3838 via `_ask_help` (`robot_agent/core/planning/step_ladder.py:115-123`, event with no kind) | none | `places.ground` of the answer; else option hook `on_where_answer`; else "did you mean" (#14); "I don't know" -> search legs | splice or stop |
| 12 | Pick help: "I can't pick up the X. Could you move it closer to the edge, then say yes?" | English + `HELP_PICK` (`robot_agent/core/planning/speech.py:89`) | `robot_agent/core/planning/step_ladder.py:62-64` | none | `yes_no` True = look again; else a place name = told where it is | retry or replan or stop |
| 13 | Move help: "I can't get to the X. Is something in the way? ..." | English + `HELP_MOVE` (:90) | `robot_agent/core/planning/step_ladder.py:95-97` | none | `yes_no` | retry or stop |
| 14 | "Did you mean the X?" | `q_did_you_mean` | :4007 (`_confirm_near`, :4000-4010); rooms: `robot_agent/core/planning/option_services.py:317-318` | did_you_mean, did_you_mean_room | `_ask_yes_no` | take the place |
| 15 | "I see more than one X here: ... Which one?" | `q_which_one` | `robot_agent/core/planning/which_one.py:90-92` | which_one | `which_one.choose` (the words that tell the candidates apart, :53-64); if none, ONE LLM call (:115-127) | `r.queries[object]` |
| 16 | declared-tree ask / confirm | the tree's pack phrase key (`robot_agent/options/bt_goal.py:184-186`) | `robot_agent/options/bt_goal.py:188-197` | bt_goal | `ask`: word lists named by the tree (`names.says` over pack blocks); `confirm`: `yes_no` one bit; menu: `read_menu` (:119-127) | tree continues |
| 17 | next-step menu after a failed Find / Pick / MoveTo | `q_ask_next_find`, `_pick`, `_move`, `_again`, `_place` (`robot_agent/core/planning/lang/en.json:344-348`) | `robot_agent/options/ask_next.py:121-161` | ask_next | the pack block `ask_next` word lists: retry, elsewhere, anywhere, stop (`robot_agent/options/ask_next.py:31`, :36-60) | retry, search, or stop |
| 18 | where is the object (before looking) | `q_where_is_obj`, `q_where_in_home`, `a_look_in_room` | `robot_agent/options/ask_source.py:55-95`, :78 | where_source, where_source_clarify | place grounding, room grounding, `is_unsure`, `confirm_near` (yes/no) | `from` slot set |
| 19 | where is the object (after looking here) | `q_where_is_here`, `q_place_unknown` | `robot_agent/options/look_then_ask.py:52-90`, :73 | kind variable | place / room grounding | `from` slot set |
| 20 | garbled name: "Did you mean A or B?" | `q_which_name`, `q_did_you_mean` | `robot_agent/options/name_recover.py:188-197` | name_recover | `chosen(hits, ans, langs)` (words) | name replaced |
| 21 | "which X do you mean: the thing or the place" | `q_which_x` | `robot_agent/options/goal_words.py:119-120` | goal_words | `place_named` | goal kept or dropped |
| 22 | delivery: where is the person | `q_where_are_you`, `q_where_is_person` | `robot_agent/options/reach_delivery.py:202-205` | reach_delivery | `persons.place_in_answer` | step replaced by a move |
| 23 | recipient conflict (the speaker or a named person) | `Q_WHICH` pack phrase | `robot_agent/options/recipient_keep.py:111` | recipient_keep | `speaker_evidence` | `to` slot |
| 24 | either-or read-back | `q_which_reading` | `robot_agent/options/ask_agree.py:260`, :246-256 | ask_agree | words that name one reading (`_picked`) | goal chosen |
| 25 | safety question about a thing | the pack's own ask phrase | `robot_agent/options/hazard.py:138` | hazard | `answer_says` (refuse / allow / unanswered) | refuse or proceed |
| 26 | unapplied correction: the understand step's own question | free text | `robot_agent/options/unapplied_change.py:42` | goal_change | `reread_change` (the LLM re-reads) | change applied or run fails |
| 27 | unclear condition (`cond_extract`, `cond_goal`) | `cond_unclear`, pack keys `q_unclear`, `q_where`, `q_unsure` | `robot_agent/options/cond_extract.py:176-183` (asks through `svc.ask`, which emits NO `clarify` event: `robot_agent/core/planning/option_services.py:36-42`) | none, no event | its own parser: the answer counts only when it grounds to a place (`robot_agent/options/cond_extract.py:182-183`) | `when` field |
| 28 | empty or holed request | `q_repeat` | `robot_agent/options/asr_guard.py:68-71` | none | free text | re-understood |

So: 28 rows. 16 of them carry an event kind of their own (confirm, coverage, new_task, did_you_mean, did_you_mean_room, which_one, bt_goal, ask_next, where_source, where_source_clarify, name_recover, goal_words, reach_delivery,
recipient_keep, ask_agree, hazard, goal_change, and the kind variable of look_then_ask); 12 carry none (rows 2-8, 11-13, 27, 28), and row 27 emits no event at all. The questions a pack can phrase itself: every key in `CORE_PHRASES`
(overridable per language), the pack phrases a declared tree names, the `ask_next` block, the pack's hazard question. The questions it cannot: the model's own question, rows 5, 10, 11, 12, 13 (English text in the code) and the grounding
fallback at :1079.

### B. How answers are read (eight routes)

| route | function | gives | used by |
|---|---|---|---|
| 1 yes/no, three-way | `yes_no` (`robot_agent/interact/dialog.py:119-130`) | `True` / `False` / `None`(silence) + the rest of the text as a correction; words from the language packs (`robot_agent/core/planning/lang/en.json:146-163`, ko, vi) | ladder help, coverage, new task, bt `confirm` |
| 2 yes/no, four-way | `read_yes_no`, `ask_yes_no` (`robot_agent/interact/dialog.py:187-217`) | yes / no / correction / unclear (re-asked `yes_no_reasks` times with the robot's `yes_or_no` phrase) / silence | read-back, did-you-mean |
| 3 place grounding | `Places.ground`, `persons.place_in_answer`, `understand.fuzzy_place`, `fuzzy_room`, option services `ground_place`, `room_places`, `confirm_near` | an ENV key (or a room's keys) | where is it, delivery, source questions |
| 4 pack word lists | `names.says`, `ask_next.says`, `bt_goal.read_menu`, `pack_words` (`robot_agent/options/bt_goal.py:136-143`) | a named choice (retry, elsewhere, stop, ...) | declared trees, ask_next |
| 5 instance choice | `which_one.choose` + one LLM call | an index | which one |
| 6 re-understanding | `_ask_clarify` appends `("user", ans)` to `r.dialogue` (:2586) and the next round gives the whole dialogue to the model | new goals; the loop never parses the answer itself | everything with no kind |
| 7 ad-hoc regex | `\b(now\|first\|right away)\b` (:3133), English `_USER_WORDS` and place regexes (:1074) | a bit | new task, speaker |
| 8 option-specific | `answer_says` (hazard), `speaker_evidence`, `chosen`, `place_named`, `_picked`, `reread_change` | a verdict | the options of rows 20-27 |

An answer is therefore one of: one bit (routes 1, 7), one of a closed list of words (4), an ENV place (3), an index (5), free text for the model (6). A free-text answer that carries content for a SKILL (millimetres, "use the measured
positions", an arm) has no route: the loop has no slot to put it into (framework_fit G5; run in "Words, answers" above).

### C. Languages

- Packs: `robot_agent/core/planning/lang/{en,ko,vi}.json` (365, 376, 308 lines: dialogue words, patterns, speech phrases, particles, script) and `ja.json`, `zh.json` (6 lines each: name, register, detection marks only). The prompt language is `en`
  unless the manifest sets `planner.prompt_lang` (`robot_agent/core/planning/langdata.py:44-64`).
- Say: a `LocalText` is rendered from the robot's own phrase for the key (pack i18n, then the language pack `speech` block, then English from `CORE_PHRASES`; `robot_agent/core/planning/speech.py:235-246`, `render` :259); anything else is English text
  translated by a model call (`robot_agent/core/planning/language.py:430`), with the place and person names kept (`glossary`, :256); a failed translation is `say_untranslated` (`robot_agent/core/planning/speech.py:85`, `CORE_PHRASES`).
- Hear: the person's text is read as given, or through English (`input_pivot`: none / en, pack `planner.input_pivot`, :494-495; `LanguageAdapter.hear`, `robot_agent/core/planning/language.py:535`); a correction is checked in both forms
  (`robot_agent/core/planning/assist_loop.py:2510-2517`); yes/no words, fillers, polite words, stop / pause / resume words are per-language pack data (`dialogue` block).
- Manifest: `language`, `languages` (`robot_agent/contract/manifest.py:133-153`); `Messages.text` falls back to English then to the key (`robot_agent/contract/types.py:253-267`). A robot whose manifest declares only `en` (PEX, home, factory) gets
  the English core phrases for the questions it does not override.

## Option map

The option engine (`robot_agent/core/options/`: `spec.py` 112 lines, `dispatch.py` 130, `registry.py` 84, `config.py` 82, `services.py` 45) is already the extension mechanism the level-B design needs: an option declares a name, typed keys
with sources (run, env, manifest), the hooks it implements, the services it answers and a dispatch order (`robot_agent/core/options/spec.py:1-8`, :22-56); the engine never imports an option. A hook is `decide` (first non-None wins),
`transform` (chained) or `observe` (all run); a "flow" hook is a generator that may yield events and return `_NEXT` / `_STOP` (`robot_agent/core/options/spec.py:19-21`). A service is a named query (`Dispatcher.query`, `collect`:
`robot_agent/core/options/dispatch.py:89-100`). Pack data reaches an option as `planner.options.<option>.<key>` and counts only with a `source` or `assumed: true` label (`robot_agent/core/planning/pack_data.py:6-8`, :19-24).

Measured: 44 options in 5939 lines (+ `__init__.py` 19); 5 are ON by default (`bt_goal`, `bring_ask`, `fetch_guard`, `look_then_ask`, and `hazard` with `rules=core`); 14 take pack-only keys; 10 read the pack's action roles
through `svc.role` (reopen_on_miss, plan_complete, end_state, gate_prune, precond_repair, cond_goal, plan_release, reach_delivery, place_find, reach_check); 2 name actions in code (`robot_agent/options/drop_search.py` MoveTo and Pick,
`robot_agent/options/fetch_guard.py:35` Find and Pick) and `bt_goal` names the virtual step `Check` (`robot_agent/options/bt_goal.py:360`); 19 mention a goal-kind word. 30 hooks and 25 services exist; every option listed implements at least one.
The loop itself reads its options 50 times (`self.options.` call sites in `robot_agent/core/planning/assist_loop.py`).

### Hooks (30): where the loop calls them

| hook | style | call site | phase | options that implement it |
|---|---|---|---|---|
| on_start | observe | robot_agent/core/planning/assist_loop.py:1670 | setup | trace, reach_delivery, lang_words, search_budget, search_per_object, bring_ask, object_words, dedupe_keep_end, plan_release, recipient_keep, hw_wait |
| on_ground | transform | robot_agent/core/planning/assist_loop.py:1101 | understand | reach_delivery |
| on_plan | transform | robot_agent/core/planning/assist_loop.py:2896 | generate-and-certify | place_find, gate_prune, precond_repair, plan_complete, plan_release, replan_guard |
| on_where_answer | decide, flow | robot_agent/core/planning/assist_loop.py:3843 | recover / answer | ask_source |
| on_understand_schema | transform | robot_agent/core/planning/assist_loop.py:934 | understand | cond_goal |
| on_understand_prompt | transform | robot_agent/core/planning/assist_loop.py:665 | understand | cond_goal |
| on_heard | decide, flow | robot_agent/core/planning/assist_loop.py:1824 | understand | answer_align, asr_guard |
| on_goals | decide, flow | robot_agent/core/planning/assist_loop.py:1870 | understand | answer_align, hazard, heat_safety, cond_extract, name_recover, cond_goal, look_then_ask, ask_source, goal_words, ask_agree, recipient_keep |
| on_issues | transform | robot_agent/core/planning/assist_loop.py:2320 | understand / answer | answer_align, name_recover, goal_ledger, entity_cover, ask_agree, ask_focus, shared_instances, heard_check |
| on_goal_edit | observe | robot_agent/core/planning/assist_loop.py:3056 | understand (mid-run) | unapplied_change |
| on_goal_reread | observe | robot_agent/core/planning/assist_loop.py:3086 | understand (mid-run) | unapplied_change |
| on_goal_unapplied | decide, flow | robot_agent/core/planning/assist_loop.py:3060 | understand (mid-run) | unapplied_change |
| on_run_goals | decide, flow | robot_agent/core/planning/assist_loop.py:3215 | execute | goals_only, bt_goal |
| on_step | decide, flow | robot_agent/core/planning/assist_loop.py:3554 | execute | reach_delivery, reach_check |
| on_step_done | observe | robot_agent/core/planning/assist_loop.py:3638 | execute | reach_delivery |
| on_step_failed | decide, flow | robot_agent/core/planning/assist_loop.py:3680 | recover | trace, bt_goal, reach_delivery, drop_search, reopen_on_miss |
| on_ask_help | decide, flow | robot_agent/core/planning/step_ladder.py:119 | recover / answer | ask_next |
| on_goal_check | decide | robot_agent/core/planning/assist_loop.py:3425 | verify | reach_delivery |
| on_translation_lost | decide | robot_agent/core/planning/language.py:588 | answer | translate_retry |
| on_second_look | decide | robot_agent/core/planning/search_hooks.py:129 | verify | confirm_look |
| on_final | decide | robot_agent/core/planning/assist_loop.py:4067 | verify | unapplied_change, end_state |
| on_goals_met | decide | robot_agent/core/planning/assist_loop.py:4076 | verify | hazard, heat_safety |
| on_finish | observe | robot_agent/core/planning/assist_loop.py:1726 | answer | hazard, heat_safety |
| on_planner_kwargs | transform | robot_agent/core/planning/loop.py:357 | setup | replan_guard |
| on_goal_replan | decide | robot_agent/core/planning/assist_loop.py:3199 | recover | replan_guard |
| on_replan_findings | transform | robot_agent/core/planning/assist_loop.py:3010 | recover | replan_guard, repair_sync |
| on_plan_findings | transform | robot_agent/core/planning/assist_loop.py:2909 | generate-and-certify | fetch_guard |
| on_listener_rule | decide | robot_agent/core/planning/assist_loop.py:2221 | understand | recipient_keep |
| on_event | observe | robot_agent/core/planning/option_services.py:48 (also assist_loop.py:784, loop.py:571) | all | trace |
| on_lab | observe | robot_agent/core/planning/assist_loop.py:607 | all | trace |

### Services (25): named queries the loop asks of its options

| service | call site | answered by | what the loop asks |
|---|---|---|---|
| `belief_set` | robot_agent/core/planning/assist_loop.py:1679 | object_words | the objects this run handed over |
| `belief_table` | robot_agent/core/planning/assist_loop.py:3207 | object_words | the `placed` table looked up by the object's words |
| `confirm_limit` | robot_agent/core/planning/assist_loop.py:2451; robot_agent/core/planning/option_services.py:154 | ask_focus | how many read-backs |
| `failure_code` | robot_agent/core/planning/assist_loop.py:3676 | fetch_guard | a code for a failed step (a silent Find miss is ABSENT) |
| `forbids` | robot_agent/core/planning/assist_loop.py:911 | hazard, heat_safety | does a safety rule forbid this goal |
| `gate_off` | robot_agent/core/planning/assist_loop.py:1996 | hazard | switch the gate's check of a refused goal off |
| `goal_dropped` | robot_agent/core/planning/assist_loop.py:2348 | cond_extract, cond_goal, hazard | was this goal dropped by a condition or a refusal |
| `goal_guard` | robot_agent/core/planning/assist_loop.py:2220 | goal_words | guard a goal in the filter |
| `goal_keep` | robot_agent/core/planning/assist_loop.py:2226 | goal_ledger | keep a `go` goal the filter would drop |
| `keep_after_dedupe` | robot_agent/core/planning/assist_loop.py:2861 | dedupe_keep_end | do not cut the steps after a repeated fetch |
| `looks_made` | robot_agent/core/planning/assist_loop.py:3854, :3916, :4016 | search_budget, search_per_object | looks made for an object |
| `more_tries` | robot_agent/core/planning/assist_loop.py:3754 | hw_wait | tries left for a hardware fault |
| `no_refetch` | robot_agent/core/planning/assist_loop.py:2861 | reach_delivery | no second fetch |
| `no_replan` | robot_agent/core/planning/assist_loop.py:2963 | bt_goal | do not call the planner for this step |
| `person_where` | robot_agent/core/planning/assist_loop.py:1098 | bring_ask | the question for a person's place |
| `rank_first` | robot_agent/core/planning/search_hooks.py:67 | fetch_guard, goals_only | rank unknown sources first |
| `recovery_rule` | robot_agent/core/planning/assist_loop.py:3741 | hw_wait | replace the recovery rule of a code |
| `refused_goals` | robot_agent/core/planning/assist_loop.py:2311 (`collect`) | cond_extract, cond_goal, hazard, heat_safety | goals refused or dropped |
| `repair_kwargs` | robot_agent/core/planning/assist_loop.py:2861, :2886 | repair_sync | extra arguments of the gate's repair |
| `runs_goals` | robot_agent/core/planning/assist_loop.py:2660 | bt_goal, goals_only | the option runs the goals itself; no plan |
| `search_limit` | robot_agent/core/planning/search_hooks.py:110 | search_budget | limit of looks |
| `search_over` | robot_agent/core/planning/search_hooks.py:114 | search_budget | is the search over |
| `shared_names` | robot_agent/core/planning/assist_loop.py:2302 | shared_instances | names two instances share |
| `unrefused` | robot_agent/core/planning/assist_loop.py:2472 | ask_agree, ask_focus | "I don't know" is no refusal |
| `wait_schedule` | robot_agent/core/planning/assist_loop.py:3780 | hw_wait | the wait schedule for a failure kind |

### The 44 options: what each touches

Columns "household things" and "candidate role" are my reading of the docstring and of the literals in the file (the first lines of each file; not every line of every option was read, see "What was not read"). "Candidate role" is a
pointer for the design, not a decision.

| option (`robot_agent/options/<name>.py`) | lines | default | order | hooks | services | pack-only keys | household things it touches (from the docstring and the literals) | candidate role at level B |
|---|---:|---|---:|---|---|---|---|---|
| trace | 206 | off | 0 | on_start, on_event, on_lab, on_step_failed | - | - | nothing | generic; stays an option |
| goals_only | 59 | off | 1 | on_run_goals | runs_goals, rank_first | - | nothing (ends with `done understood`) | generic; stays (the fleet seam) |
| bt_goal | 367 | ON | 5 | on_run_goals, on_step_failed | runs_goals, no_replan | trees, menu | 6 conditions; `act(action, obj)`; yes/no on `confirm`; goal type as the tree key; slots object/from/to; the literal step `Check goals` (:360) | becomes the generic tree executor; conditions, act arguments and answer schemas come from the pack registry |
| answer_align | 188 | off | 8 | on_heard, on_goals, on_issues | - | - | kinds on/in (`MOVE`, :42); either-or answers | kind-parametrised question policy |
| hazard | 204 | ON (rules=core) | 10 | on_goals, on_goals_met, on_finish | forbids, refused_goals, gate_off, goal_dropped | - | kinds on/in/say/open/close; household safety rules | stays; rules are pack data (`safety` profile) |
| heat_safety | 150 | off | 11 | on_goals, on_goals_met, on_finish | forbids, refused_goals | places, burnable | kinds on/in (`PUT_KINDS`, :21); heat sources, burnable things | stays; places and burnable are already pack data |
| cond_extract | 268 | off | 15 | on_goals | refused_goals, goal_dropped | clauses, heard_match | English clause words; asks without a `clarify` event | stays; clauses move to the language pack |
| name_recover | 208 | off | 17 | on_goals, on_issues | - | - | the robot's object catalogue; edit distance | generic matcher over declared names |
| cond_goal | 399 | off | 20 | on_understand_schema, on_understand_prompt, on_goals | refused_goals, goal_dropped | clauses | adds a `when` slot to the goal schema; roles move, open | the model for "a pack adds a goal slot" |
| goal_ledger | 174 | off | 22 | on_issues | goal_keep | retract_markers | kinds go/in/on/open | kind-parametrised |
| look_then_ask | 144 | ON | 29 | on_goals | - | - | kinds on/in; runs a Find at understanding; sets `from` (:64) | kind- and action-parametrised; a pack can switch it off (PEX does) |
| ask_source | 152 | off | 30 | on_goals, on_where_answer | - | - | kinds on/in; questions about where an object is | question policy over the Q&A schema |
| reach_delivery | 222 | off | 30 | on_start, on_ground, on_step, on_step_done, on_goal_check, on_step_failed | no_refetch | - | kind on to a person; roles move, put_down; `person_snapshot` | kind-parametrised; person is a pack concept |
| fetch_guard | 102 | ON | 32 | on_plan_findings | rank_first, failure_code | - | kinds on/in (`_FETCH`); the action names `Find`, `Pick`, `MoveTo` in code (`_LOOKS`, :35) | action literals become roles; stays |
| reach_check | 105 | off | 32 | on_step | - | shift | a Pick beyond side reach (role pick); `shift` data | guard hook with pack data |
| drop_search | 105 | off | 36 | on_step_failed | - | floors | the action names `MoveTo`, `Pick` in code; `floors` data | recovery policy of the standard pack |
| ask_next | 188 | off | 40 | on_ask_help | - | - | Find / Pick / MoveTo menus; word lists | recovery policy + question schema |
| asr_guard | 75 | off | 40 | on_heard | - | - | nothing | generic |
| unapplied_change | 62 | off | 40 | on_goal_edit, on_goal_reread, on_goal_unapplied, on_final | - | - | goal-change hooks; `on_final` | generic |
| confirm_look | 36 | off | 50 | on_second_look | - | - | nothing | verifier hook (second look) |
| lang_words | 53 | off | 60 | on_start | - | clauses | language clause words | language pack data |
| translate_retry | 38 | off | 60 | on_translation_lost | - | - | nothing | generic (language layer) |
| goal_words | 134 | off | 70 | on_goals | goal_guard | - | kinds on/in; place vs object names | kind-parametrised |
| search_budget | 104 | off | 70 | on_start | looks_made, search_limit, search_over | budget | per-object search budget; `budget` data | parameter of the Find ladder |
| search_per_object | 39 | off | 70 | on_start | looks_made | - | per-object search budget | parameter of the Find ladder |
| bring_ask | 67 | ON | 75 | on_start | person_where | - | kind on to the speaker | kind-parametrised question policy |
| entity_cover | 153 | off | 75 | on_issues | - | - | every named object becomes a goal | generic |
| object_words | 75 | off | 75 | on_start | belief_table, belief_set | - | belief table by object words | predicate / belief helper |
| ask_agree | 269 | off | 78 | on_goals, on_issues | unrefused | - | either-or read-back | question policy |
| ask_focus | 127 | off | 80 | on_issues | confirm_limit, unrefused | - | question budget | question policy |
| place_find | 45 | off | 80 | on_plan | - | - | roles find, move; a Find of a place | plan transform over roles |
| shared_instances | 65 | off | 81 | on_issues | shared_names | - | two remembered instances share a name | generic |
| heard_check | 79 | off | 82 | on_issues | - | match | kinds on/in/say; goal quote vs heard words | kind-parametrised |
| gate_prune | 45 | off | 84 | on_plan | - | - | roles open, close; unopenable places | plan transform over roles |
| precond_repair | 154 | off | 85 | on_plan | - | needs | kinds on/in/hand; needs hand_free / holding / near per action (pack data); roles | already pack-declared: the template for declared preconditions |
| dedupe_keep_end | 39 | off | 86 | on_start | keep_after_dedupe | - | nothing | generic |
| plan_complete | 188 | off | 86 | on_plan | - | needs | kinds in/hand; `needs` data; roles | already pack-declared |
| plan_release | 197 | off | 87 | on_start, on_plan | - | needs | kinds in/on/hand; `needs` data; roles | already pack-declared |
| recipient_keep | 124 | off | 88 | on_start, on_listener_rule, on_goals | - | - | the speaker and a named recipient | kind-parametrised question policy |
| hw_wait | 125 | off | 90 | on_start | wait_schedule, recovery_rule, more_tries | kinds | per failure-kind schedule (`kinds` data) | recovery policy parameter, pack data |
| reopen_on_miss | 60 | off | 90 | on_step_failed | - | - | roles find, open; pack data `planner.recovery.reopen_on_miss` | already pack-declared recovery |
| replan_guard | 138 | off | 90 | on_planner_kwargs, on_goal_replan, on_replan_findings, on_plan | - | - | planner kwargs, blocks, release dedupe | generic recovery |
| repair_sync | 134 | off | 91 | on_replan_findings | repair_kwargs | - | gate repair sync | generic recovery |
| end_state | 73 | off | 95 | on_final | - | - | kind go; `end_state` {places, action} | already pack-declared; end-of-run verifier |

What the option map says for the design:
- The engine already supports most of what level B asks for as EXTENSION POINTS: ordered hooks around every phase, named services, typed keys with three sources, labelled pack data, action roles. What it does not have is a place to put the
  vocabulary itself: no hook returns a goal kind, a predicate, an action's preconditions, a recovery ladder, a phrase table or an answer parser. Those are the closed tables above.
- 14 options already read pack-declared data (`precond_repair.needs`, `plan_complete.needs`, `plan_release.needs`, `hw_wait.kinds`, `search_budget.budget`, `end_state`, `reopen_on_miss`, `bt_goal.trees`, `heat_safety`, ...); 10 take action NAMES from the
  pack's roles. The pattern "data keyed by an action or a kind, with a label, fail open when absent" exists and works; it is applied option by option, not by the loop.
- Options that fix an action or a kind in code: `drop_search`, `fetch_guard` (action names); about 19 files with a kind word (`on`, `in`, `open`, `close`, `go`, `say`).
- Two options are the core of the PEX workarounds: `bt_goal` (G2, G3, G5, G6) and `look_then_ask` (P8; PEX has to switch it off).
- Default-ON options change the default path: `bt_goal` does nothing for a robot with no `trees`; `bring_ask`, `fetch_guard`, `look_then_ask`, `hazard` (rules=core) act for every robot, so they belong to the household behaviour a standard pack must keep.

## Cut candidates

Ranked by independence (how few other methods and attributes a block touches), then by size. The "touches" figures come from an `ast` call graph over the 189 `AssistLoop` methods (`self.<method>` calls and `self.<attr>` reads;
`getattr(self, "...")` strings are not counted). A block is "pure" when it has 0 outward calls and 0 non-method `self` attributes. Level-B rows (14-22) are new.

Constraints that every cut must respect (all measured or read):

1. The module names `Places`, `Memory`, `words`, `ASSIST_OPTIONS`, `_NEXT`, `_STOP`, `_DONE` are imported from `assist_loop` by 92 test files (69 of them `Places`, `Memory` or `words`), by `robot_agent/core/planning/search_hooks.py:20`
   and by `robot_agent/core/unified_agent.py:387`: a moved name must be re-exported (one import line each).
2. `robot_agent/observe/replay.py:401-406` patches `current` and `reconcile_world` as attributes of the `assist_loop` module: code that calls them must stay in that module.
3. Tests call these methods through the CLASS (measured with `grep -rhoE "AssistLoop\.[a-z_]+" tests`): `__new__` 28 times, `run_blocking` 19, `_fresh_question` 6, `summary` 5, `_bind` 3, `_reaches` 2, `_goal_phrase` 2, `_check` 2, `lab_log` 2,
   and once each `yes_no`, `_verify_with_body`, `runaway`, `_observe_goal`, `_now`, `_guard`, `_ground_template`, `_finish`. (The level-A list named `_releases`, `_on_reached`, `_simulate_goals`, `_effect`: tests do not call these through the class.)
   A moved function keeps a one-line class attribute or a mixin.
4. 28 test lines construct the loop with `AssistLoop.__new__` and set attributes by hand (they never run `__init__`): config extraction must not be the only place that sets an attribute they read.
5. `tests/test_option_refresh.py:50-52` asserts that the word `refresh_options` is not in the source of `assist_loop.py`.

| rank | block | lines | touches | callers outside the block | what it holds | notes for the design |
|---:|---|---:|---|---|---|---|
| 1 | `Places`, `Memory`, `words`, `_strip_article`, `_fmt_en` | 195 (:107-301) | stdlib and `langdata` at module level (checked with `ast`); `env_names`, `speech` imported inside methods | 69 test files, `search_hooks.py`, the loop | the closed place vocabulary (`name@room`, aliases, openable, fillers) and the object memory | most independent block; net effect about -190 lines after a 3-line re-export; the `@` convention and the English fillers move with it and become data later |
| 2 | goal-reach simulation: `_releases`, `_simulate_goals`, `_on_reached`, `_missing`, `_reaches`, `_goals_hold` | 96 (:1442-1543) | 0 outward calls, 0 attributes (only `getattr(self, "_env_places")`, `getattr(self, "_handed")` and `robot.release_actions()`) | 15 methods: `__init__` (wraps `_reaches`, :448), `_hand_action`, `_check`, `_effect`, `_gate_repair_run`, `_repair_suffix`, `_tidy`, `_blocking`, `_goal_edit`, `_suspend_for`, `_resume_suspended`, `_repair_goals`, `_plan_ended`, `_step_succeeded`, `_final_check` | the per-kind "is this goal reached / does it hold" if-chains (on, in, open, close, template) | the first target of the "kinds + predicates" design: one table kind -> (reached-by-steps, holds-in-belief, missing-text); needs a class alias because `__init__` wraps `_reaches` and tests call it through the class |
| 3 | read-back phrase: `summary`, `_goal_phrase` | 42 (:1225-1268) | 0, 0 (static) | 9 callers incl. read-back, goal edit, `_compare_views`; tests call both | per-kind English and `rb_*` keyed phrases | pure on `(goals, places, nice)`; target of the "Phrases" design; keep the static names for the tests |
| 4 | grounding, pure part: `_ground_container`, `_ground_template`, `_ground_speaker` | 61 (:944-989, :1065-1079) | none of `self` (two static, one instance without attributes) | `_ground_goals` | per-kind grounding of go / say / deliver / open / close | `_ground_template` is tested through the class; stays callable there |
| 5 | grounding, rest: `_ground_goals`, `_person_destination`, `_say_name` | 126 (:991-1063, :1081-1137) | `cfg`, `options`, `_remembered`, `_where_asked` | `_ground_all`, `_take_given_goals`, `_ground_understood`, patch paths | `on` / `in` slot grounding, the person as destination | needs the options query `person_where` (:1098); after 4 |
| 6 | plan checker: `_ground_step`, `_check`, `_check_step`, `_word_step`, `_word_pick`, `_word_place` | 145 (:1271-1422) | 4 outward (`_missing`, `_reaches`, `_lab`, `_manifest_planner`), attributes `robot`, `_env_places` | 4 methods | the checker's finding text per action (Open, Close, Find, Pick, Place, PutIn, MoveTo) | after 2; the per-action wording is the data to declare ("findings per violation code"); `_check` assigns `self._env_places` (:1305), a side effect to keep |
| 7 | config resolution: `__init__` body, `_resolve_options_cfg`, `_v2_cfg`, `_v2_cfg_base`, `_um_cfg`, `_rm_cfg`, `_r3_cfg`, `_converge`, `_manifest_planner` | 231-272 (:369-636) | `robot`, `options`, `cfg`, `original`, `_find_params`; reads `os.environ` | `__init__` (+ `refresh_options` of a host) | all numeric defaults and the string-to-bool coercions (the cause of S1: no coercion of `confirm_*`, :397-398) | a function `resolve_cfg(overrides, env, robot, ...) -> dict` is the cut; order of `cfg.update` and the 28 `__new__` tests are the risk |
| 8 | understand call and capability: `_understand` .. `_cannot_do`, `UNDERSTAND_SCHEMA`, `UNDERSTAND_PROMPT` | 257 + 50 (:307-356, :658-941) | `cfg`, `options`, `robot`, `original`, `um_last`, `_llm_cfg`, ... | `_take_understanding`, `_understand_round`, `_complete_override`, `_verify_verdict` | the household prompt, the schema, the refusal reasons, the `_CANNOT` text | where goal kinds and the prompt become data; after the kinds design |
| 9 | Find recovery: `_recover_find` .. `_refuted` | 243 (:3808-4063) | 8 outward, 11 attributes | `_step_failed` (`recover` dict :3701), `_look` | the search / relook / phantom ladder | big, entangled with `SearchHooks`; a verbatim move into a mixin file is the safe cut, not a data cut |
| 10 | replan: `_refine_plan`, `_sage_replan`, `_replan_suffix`, `_no_replan`, `_no_replan_end`, `_replan_or_fail` | 82 (:2943-3031) | 11 outward | 6 entries | suffix repair | mixin-move only |
| 11 | goal change / task stack | 162 (:3034-3203) | 10 outward (`_ask`, `_understand`, `summary`, `yes_no`, ...) | 4 entries | NEW / MODIFY / ADD / DROP, suspended tasks | mixin-move only |
| 12 | read-back dialogue | 715 (32 methods, :1787-2549) | 16 outward, 12 attributes | 13 entries | confirm, correct, restate, clarify | the largest block; mixin-move only, and only after the kinds and phrases are data (many `rb_` and `q_` keys live here) |
| 13 | step run and failure: `_guard` .. `_wait_health` and helpers | 379 (:3467-3806 + `_bind`, `_effect`, `_found_point`, `_place_of_far_point`) | 18 outward, 16 attributes | 2 entries (`_execute_phase`, `try_step`) | one step's life, the policy rung | mixin-move only; contains the per-action `recover` dict |
| 14 | question and answer plumbing: `_ask`, `_ask_io`, `_ask_heard`, `yes_no`, `_ask_yes_no`, `_ask_clarify`, `_asks_where`, `_essential`, `_fresh_question`, `_restates` | 119 (:1179-1222, :2550-2636) | 6 outward calls (`_cancelled`, `_finish`, `_humanize`, `_in_lang`, `_manifest_planner`, `_texts_of`); attributes `_answer_source`, `_log_fn`, `_where_asked`, `cfg`, `original`, `robot`, `sr` | 9 methods (`_agree_reask`, `_ask_views`, `_confirm_near`, `_coverage_gap`, `_new_command`, `_object_source`, `_read_back`, `_refused_reading`, `_understand_round`) | the one place a question is said, heard and counted; the yes/no wrapper; the "essential question" rule (a delivery's "Where are you?" may take one round past `clarify_rounds`) | the seam for the Q&A schema (rows 1-28 of "Questions and answers"); `_asks_where` and `_essential` name two phrase keys in code (:2601, :2610) |
| 15 | verification of the end: `_observe_goals`, `_end_action`, `_return_tries`, `_return_to_end`, `_observe_goal`, `_look`, `_final_check` | 132 (:3345-3465, :4066-4081) | 6 outward (`_bind`, `_cancelled`, `_finish`, `_goals_hold`, `_lab`, `_manifest_planner`); attributes `_log_fn`, `_second_look_seen`, `cfg`, `options`, `sr` | 2 entries (`_plan_ended`, `_run_blocking`) | what "done" is observed as; the per-kind look (only `on` / `in`) | the seam for the verifier registry (kind -> observation); `_observe_goal` is called through the class by a test |
| 16 | post-step check: `_post_check` | 32 (:3599-3630) | 3 outward (`_holding`, `_found_point`, `_place_of_far_point`), `cfg`, `robot` | `_run_step` only | the loop's two extra rules (a release looked at twice, a Find seen out of reach) on top of `StepVerifier` | a small, clean cut into the verifier module |
| 17 | step recovery policy: `_step_failed`, `_reset_container`, `_apply_policy`, `_wait_on_robot`, `_wait_health` | 143 (:3660-3806) | 9 outward (`_announce`, `_bind`, `_finish`, `_lab`, `_manifest_planner`, `_note_failure`, `_recover_find`, `_refuted`, `_replan_or_fail`) plus the mixin methods `_recover_pick`, `_recover_move`, `_recover_open`, `_recover_place`, `_search_open`; `cfg`, `options`, `robot`, `sr` | `_run_step` | the order of the recovery (above, "Recovery") and the per-action dict | seam for "recovery policies selectable by name per action or goal kind": the dict at :3701-3703 is the line to replace |
| 18 | run driver: `_run_blocking`, `_finish`, `_say_of_msg`, `_humanize`, `_llm_failed`, `_deciding_failure`, `_note_failure`, `_cancelled` | 115 (:1662-1784) | 8 outward (`_execute_phase`, `_final_check`, `_in_lang`, `_now`, `_plan_phase`, `_reconcile`, `_understand_phase`, `language`), 23 attributes (it creates the run state) | 28 methods call `_finish` or `_cancelled` | the phase pipeline and the `done` event | the "thin pipeline" is this block plus the four phase calls; cut last |

Data cuts (no method moves; each is a table that becomes declared data, with a test that the data equals the old constants before the constants are deleted):

| # | table | size | where | note |
|---:|---|---|---|---|
| D1 | `CORE_PHRASES` -> the `speech` block of `lang/en.json` | 62 entries | `robot_agent/core/planning/speech.py:24-87` | the lookup already prefers a pack's block (`speech.py:235-246`); only the English fallback is in code |
| D2 | failure patterns -> declared per pack / language | 13 rules | `robot_agent/contract/failures.py:26-57` | a pure function, a 74-line file |
| D3 | goal kinds, needs, step recipes, reached tests, texts | 285-line module | `robot_agent/core/planning/goals.py` | one place for rank 2 and rank 3 |
| D4 | the predicates: `CONDITIONS` + `holds` and `_holds` | 12 + 18 lines | `robot_agent/options/bt_goal.py:50-61`, `robot_agent/core/planning/verify.py:53-70` | two vocabularies of six that overlap on `holding`, `opened`; one table |
| D5 | the action words: `_ABILITY`, `ACTION_TEXT`, `VERBS` | 8 + 10 + 27 entries | `robot_agent/core/planning/robot.py:42-45`, `robot_agent/core/planning/robot_prompt.py:26-37`, `robot_agent/core/planning/announcer.py:25-39` | three tables of words for the same actions |
| D6 | "the argument is a place" per action | 5 copies | `robot_agent/core/planning/assist_loop.py:81`, `robot_agent/core/planning/reconcile.py:31`, `robot_agent/core/planning/mapper.py:45`, `robot_agent/contract/legacy_adapter.py:30`, `robot_agent/core/planning/search.py:31` | one declared flag per action |
| D7 | who "me" is | 3 copies | `robot_agent/core/planning/goals.py:152`, `robot_agent/core/planning/assist_loop.py:101`, `robot_agent/core/planning/persons.py:31-35` | the third is already pack data |
| D8 | the per-action recovery dict, `STEP_LADDER` | 6 + 2 entries | `robot_agent/core/planning/assist_loop.py:3701-3703`, `robot_agent/contract/recovery.py:97` | one table by action name; the codes are already data |

Reading of the ranking:
- Ranks 1-4 (about 394 lines, 9.6 percent of the file) are pure or nearly pure, so each can be moved with a trace-identical delegation and a test that the moved function equals the old one on the same inputs. Ranks 2 and 3 are also where the
  closed per-kind logic is, so they double as the first data-driven steps.
- Ranks 14-17 are the level-B seams (Q&A, verifiers, recovery). They are NOT independent (6, 6, 3 and 9 outward calls); each needs the run state `r` passed explicitly, which today is an attribute bag.
- Ranks 9-13 shrink the file only by moving methods verbatim into mixin classes in new files (the existing pattern: `WhichOne`, `SearchHooks`, `StepLadder`). That is behaviour-safe (method resolution is unchanged) but does not generalize
  anything by itself; it should follow, not lead, the data work.
- The data the three robots need first is small: `GOAL_KINDS` with an extension point (`robot_agent/core/planning/goals.py:33-54`, 22 lines) and the condition set of `bt_goal` (`robot_agent/options/bt_goal.py:50-61`, 12 lines); the large work is the 40
  kind-comparison lines and 67 action-name lines that sit around them.

## Check of the evidence lines of pex_case/docs/framework_fit.md (G1-G13, S1-S10, and the P rows)

File checked: `/media/keti/workdir/remote_dir/pex_case/docs/framework_fit.md` (124 lines), against robot_agent `8002347` (clean), with the fleet at `d74f326`. Verdicts: RIGHT (the line and the claim hold), RIGHT, LINES OFF (the claim holds, a quoted line range is
a few lines off), WRONG (the cited evidence does not show the claim, or the line is wrong), NO ROBOT_AGENT LINE (the row cites no line of this repo: pack, fleet or run artifacts), NOT CHECKED.

| id | claim (short) | verdict | evidence |
|---|---|---|---|
| G1 | the kinds are a closed table (`GOAL_KINDS` goals.py:33-47, `missing_actions` :57-65); "an unknown kind is refused before planning" | RIGHT for the table; PARTLY WRONG for "refused before planning" | table :33-46 (:47 is `TEMPLATE_KINDS`), function :57-71. The refusal exists only on the LLM understand path (`typed_refusal(..., self._cannot_do(r.goals))`, `robot_agent/core/planning/assist_loop.py:1842-1845`), where an unknown kind cannot appear because the schema enum is `kinds_for` (:799-800, :913-916). Goals given by the caller (`accept_goals`) skip it: `_take_given_goals` says "no refusal" (:1805-1814). Run: `bt_goal.tree_for` with a tree for kind `install` returned the tree `['sequence', ['act', 'Pick', 'pipe'], ['cond', 'hand_free']]`. The end check never looks at such a goal (:3417-3418). Only the sentence-to-goal reading and the fleet's `_NEEDS` (`robot_fleet/scheduler.py:41-43`) close the kind today |
| G2 | six predicates only (`CONDITIONS` bt_goal.py:61, `holds` :50-59) | RIGHT, LINES OFF by 1 (`holds` :50-58) | also: an unknown condition is not an error, the tree is unusable (`robot_agent/options/bt_goal.py:93`) and the goal falls to SAGE with no event. Run: a tree with `["cond","seated","{object}"]` gave `tree_for -> None` |
| G3 | an `act` carries (action, object) only (bt_goal.py:164-168) | RIGHT, LINES OFF by 1 (:164-169) | run: `bt.valid(["act","Pick","cup","arm=left"])` is True and ticking it raises `TypeError: _Leaves.act() takes 3 positional arguments but 4 were given` (grammar `robot_agent/core/planning/behavior_tree.py:31`, :86) |
| G4 | Move / Pick / Place failures run the engine's own ladders past the tree (step_ladder.py:83-103) | RIGHT | `_recover_move` is exactly :83-103; bt `act` calls `try_step(ladder=True)` (`robot_agent/core/planning/option_services.py:74-92`); the ladder ends in `_finish("failed")` (`robot_agent/core/planning/step_ladder.py:102-103`) so the tree sees STOPPED, not FAILURE; only the planner rung is held back (`no_replan`, `robot_agent/core/planning/assist_loop.py:2963`); a custom action has no ladder (:3701-3703) |
| G5 | the question is a pack phrase with object/place slots; answers read by the core's yes/no (`confirm` :175-177, `pack_phrase` speech.py:248-256, `yes_no` dialog.py:119-130, :64-100) | RIGHT, all four ranges exact | `ask` :64-100, `yes_no` :119-130, `pack_phrase` :248-256, `confirm` :175-177; slots set at `robot_agent/options/bt_goal.py:184-186`. Run: "Use the measured clip positions and try once more." -> `(False, same text)`; "Yes. Use it" -> `(True, '')`; "No, leave it." -> `(False, 'leave it')`; "go ahead" -> `(True, '')`. Addition: the yes/no words are language-pack data (`robot_agent/interact/dialog.py:39-40`); the closed part is the three-way procedure; the `ask` leaf already reads word lists (`robot_agent/options/bt_goal.py:171-173`) |
| G6 | the per-node bt_goal trace is a lab flag, not in the event stream (bt_goal.py:158-160) | RIGHT | `note` is :158-159 and calls `svc.lab`; `lab_log` is capped at 50 and "never part of the event stream" (`robot_agent/core/planning/assist_loop.py:604-610`), so a long run loses the tail |
| G7 | a pack skill cannot use the engine's LLM channel or budget; "no such service in option_services.py" | CONCLUSION right, EVIDENCE WRONG | `LoopServices.ask_model` exists: "ONE extra call to the planner's model ... counted in the run's LLM budget" (`robot_agent/core/planning/option_services.py:161-173`; protocol `robot_agent/core/options/services.py:37`); `llm_calls` counts calls (:54-63). It is a service for OPTIONS (hooks), not for a pack SKILL: a skill is run through `sr.execute` and gets no services. The gap is "skills have no LLM channel", not "the engine has none" |
| G8-G10 | fleet: one object name per goal, run-time questions, spacing | NO ROBOT_AGENT LINE (no row, no evidence in the file) | consistent with what was read: one `object` per goal (`robot_fleet/graph.py:37-38`, `robot_fleet/models.py:31-44`); run-time questions relayed only under `policy.relay_run_questions` (the file's own table, lines 107-110); spacing: not read |
| G11 | fleet never reassigns a started goal (robot_fleet SPEC.md:117) | WRONG LINE | `robot_fleet/docs/SPEC.md:117` is the demo description. The statement is at `robot_fleet/docs/SPEC.md:3`, `:53`, `:100`, `:120` |
| G12 | a robot's run is cancelled when the socket that started it closes (docs/fleet_seams.md "client_ref" limits) | RIGHT | `docs/fleet_seams.md:71-73` ("the run is still watched only by the socket that started it: when that socket closes, the run is cancelled"); code: `robot_agent/api/agent.py` `_watch_client` |
| G13 | cancel reaches no sim skill (the stub node's `cancel_all` does nothing; `pex_robot/serve.py` `StubNode`) | RIGHT for the stub; the pack-side workaround NOT checked | `pex_robot/serve.py` lines 19-31: `StubNode.cancel_all` returns `{}`; the core's stop calls the node's `cancel_all` (`robot_agent/core/run_control.py:360`). "pack skills check `run_control.cancel_requested()` before each tree" is pack code, not read |
| S1 | a string option "0" is TRUE (assist_loop.py:397-398; fleet `spoke.py:140`) | RIGHT | :397 and :398 read `overrides.get(key, default)` with no coercion; `robot_fleet/spoke.py:140` sends `{"goals_only": "1", "confirm_task": "0"}` |
| S2 | the run manager keeps a run `active` for a moment after its `done` event | RIGHT (mechanism; the row cites no line) | `robot_agent/api/agent.py:497-500` sends `done` to the client; `finish` runs later in the `finally` (:519), and until then `start` answers `busy` (`robot_agent/interact/run_manager.py:461-464`; `finish` clears `_active` at :496-497) |
| S3 | the kind-less clarify of `_two_places` (:1895-1904, :2576; `kind="confirm"` :2128, :2467) is taken by the fleet as a read-back (orchestrator.py:242) | RIGHT, LINES OFF by 1 (`_two_places` :1895-1905) | `_ask_clarify` :2563-2594 emits `clarify` with no `kind` at :2576; `confirm` at :2128 and :2467; `robot_fleet/orchestrator.py:242` takes kind None, "" or "confirm" after the goals as the read-back |
| S4 | a parked robot with no Pick/Place refuses the install request (`understand status infeasible`, `done refused`) | NO ROBOT_AGENT LINE; mechanism consistent | `goals.missing_actions` returns the missing Pick / Place (`robot_agent/core/planning/goals.py:57-71`), `typed_refusal` turns it into a capability refusal (`robot_agent/core/planning/understand.py:709-710`), `done refused` (`robot_agent/core/planning/assist_loop.py:1866`). The run artifact `runs5/fleet_case4/R3/ra/configs/common/task_runs` exists |
| S5 | two robots, one stand each: both blocked | NO ROBOT_AGENT LINE | pack and tree behaviour; the artifact named is not kept in the repo (the file says so) |
| S6 | the agent sees only the NEAREST other robot (`second_robot`) | NO ROBOT_AGENT LINE | pack side (`pexcase/world.py`, `pexcase/scene.py` hold `second_robot`); not read in detail |
| S7 | wall time: model load and first understanding take 20-45 s after a start (warm-up attempts 2-5, 22-51 s) | PARTLY CHECKED (artifact) | `runs5/fleet_case4/summary.json` `warm_up`: R1 1 attempt 27.9 s, R2 3 attempts 40.0 s, R3 4 attempts 47.4 s: the seconds fall in 22-51; "attempts 2-5" does not hold for R1 of that run (1 attempt). Other runs not opened |
| S8 | robot_agent keeps memory in its config directory (`object_memory`, `sage_memory.jsonl`, `world_state`, run records, `llm/history`) | RIGHT for the three file names; `llm/history` NOT checked; the causal claim is marked "not isolated" by the file itself | `object_memory`: `robot_agent/core/planning/assist_loop.py:437`; `sage_memory.<host>.jsonl`, `object_memory`, `world_state`: `robot_agent/observe/paths.py:43-48`; `world_state.<host>.json`: `robot_agent/state.py:157-160` |
| S9 | the progress line takes its verb from the pack's `verb.<Action>`, else the lower-cased action name (announcer.py:141-144) | RIGHT | `robot_agent/core/planning/announcer.py:141-144` |
| S10 | a recovery inside a skill emits no robot_agent event | NO ROBOT_AGENT LINE; consistent | the loop sees only the skill's result; its own events are `step_start`, `step_verify`, `step_done`, `recover` (21 `recover` emit sites, all in the loop and the ladders) |
| P2 | a goal with no `from` makes the engine ask the LLM to rank places BEFORE any tree runs (`_search_start` -> `_search_rank`; search_hooks.py:38-72, :82-95; search.py:64-79) | RIGHT | :38-73, :82-96, :64-79 (`SearchConfig.from_env`: `search_rank` default "llm", env `ROBOT_AGENT_SEARCH_RANK`); `_search_start` runs before `runs_goals` (`robot_agent/core/planning/assist_loop.py:2656` vs :2660) |
| P3 | `Find` is judged by the engine beyond `reach_far_m` (assist_loop.py:3622-3625, :407-409) | RIGHT | :3619-3627 and :407-409 |
| P4 | a failed Place with a `floor` place triggers the re-find splice (step_ladder.py:147-174) | RIGHT | `_refind_dropped` :147-163, `_splice_refind` :165-174 (the `floor` test is :159) |
| P6 | missing Pick / Place for an `on` goal (goals.py:57-65) | RIGHT, LINES OFF (:57-71) | the quoted range covers most of the function |
| P7 | two walls, one object: the goal validator asks "Where should the pex pipe go in the end"; no off switch (goals.py:117-139, `conflicts`) | WRONG LINES | `conflicts` is `robot_agent/core/planning/goals.py:122-148`; the quoted range starts inside `dedupe` (:109-119); the question text is :137; no off switch (`_two_places` :1895-1905 calls it unconditionally) |
| P8 | `look_then_ask` takes the parking as the pipe's source (`options/look_then_ask.py` `on_goals`: `g["from"] = here`) | RIGHT | `robot_agent/options/look_then_ask.py:62-64` |
| P1, P5 | YAML key `on:` read as True; LLM not ready after a start | NO ROBOT_AGENT LINE | not checked |

Summary of the verdicts: wrong evidence in G7 (evidence) and G11, P7 (lines); G1 right only for the LLM path; every robot_agent line of G2-G6, G12, S1, S3, S9, P2-P4, P6, P8 holds within 1 line; S2 holds by mechanism; G8-G10, S4-S8, S10, P1, P5 cite no robot_agent line.

## Corrections to the level-A inventory (`gc/inv`, `ab26742`)

| # | level-A statement | what the code shows |
|---:|---|---|
| 1 | "Over 100 lines in the whole package: 9" | 8 strictly over 100; `robot_agent/new_skill.py:459` has exactly 100 (ast: `end_lineno - lineno + 1`) |
| 2 | "of 2791 functions" | 2813 with `ast.walk` over every `def` and `async def`, nested included; 18 at 80 or more and 36 at 60 or more are unchanged |
| 3 | yes / no words at `robot_agent/interact/dialog.py:36-40` | :39-40 (:31-36 is `_words_rx`) |
| 4 | the ladder ends in `_finish("failed")` at `robot_agent/core/planning/step_ladder.py:101-102` | :102-103 |
| 5 | "95 `self.cfg` reads (55 `.get(`, 40 `[`)" | 100 (57 `.get(`, 43 `[`); 108 mentions of `self.cfg` |
| 6 | the goal-reach simulation has "4 entry points" | 15 methods call into it (list in "Cut candidates", rank 2); the block is still pure (0 outward calls, 0 attributes) |
| 7 | tests call `AssistLoop._releases`, `_on_reached`, `_simulate_goals`, `_effect` through the class | they do not (measured with `grep -rhoE "AssistLoop\.[a-z_]+" tests`); they call `_fresh_question`, `_observe_goal`, `_guard`, `_now`, `_verify_with_body`, `runaway`, `lab_log` which the list did not name |
| 8 | "66 lines name an action in quotes" | 67 for the 13 names `MoveTo Find Pick Place PutIn Open Close Say Announce WaitForUnload Handover Dock Check` (63 for the seven manipulation names) |
| 9 | embodiplan `SymbolicState` at lines 105-122 | class at 104, fields 104-119 |
| 10 | "the 44 registered options ... total 5958 lines" | 5958 counts the 45 files of the folder, `__init__.py` (19 lines) included; the 44 options are 5939 |

The remaining checked items of the level-A inventory held: file sizes, the phase function sizes, the closed tables and their lines (all 133 distinct references of that file were printed with their source line and read; the fleet ones were checked in the fleet repo; the ones listed above were off), the fleet tables, G1-G7, G11 and G12 verdicts, P7 and P8.

## What was not read

- `robot_agent/core/planning/loop.py` (912 lines, `ClosedLoop`, parent of `AssistLoop`), `robot_agent/core/planning/language.py` (636), `robot_agent/core/planning/goal_fix.py` (634), `robot_agent/core/planning/search.py` (539) and
  `robot_agent/core/planning/plan_repair.py` (488) were measured and grepped at the cited lines, not read line by line.
- Of the 44 options only `bt_goal`, `precond_repair`, `reopen_on_miss`, `ask_next`, `ask_source`, `look_then_ask`, `name_recover`, `goal_words`, `unapplied_change`, `reach_delivery`, `recipient_keep`, `ask_agree` and `hazard` were read at the question and hook code;
  for the others the spec (hooks, services, keys), the first docstring line and the literals were used.
- `embodiplan` (`action_model.py`, `base.py`) was read at `check`, `apply`, `simulate`, the data classes and `ROBOT_ACTIONS`; it is a separate package; the planners inside it were not read.
- The robot packs: manifests of `kcare_robot`, `symbolic_robot` (home, factory) and `pex_robot` were read (skills, planner keys, capabilities); their adapters (`configs/adapter.py`, namemaps), `seed_memory` and ENV files were not.
- robot_fleet: `scheduler.py`, `graph.py`, `models.py`, `spoke.py`, `voice.py`, `worldnote.py`, `talk.py`, `readback.py` and the cited parts of `orchestrator.py` were read; `history.py`, `narrator.py`, `config.py` only by grep. No fleet code was run.
- `docs/` of robot_agent and the pex_case runs (`runs5/`) were opened only where a row above says so.
- No run of the loop was made. The executions are the snippets quoted above: `missing_actions`, `kinds_for`, `tree_for` (three trees), `holds`, `bt.valid` and `tick` of a 3-argument act, `yes_no` and `read_yes_no` on five sentences,
  registry listings (`REGISTRY.specs()`, `HOOKS`), `len()` of the phrase and verb tables, `lint_arch.py`, and `ast` scans.
