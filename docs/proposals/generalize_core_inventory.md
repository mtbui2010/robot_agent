# Generalize the core: inventory (round A, stream INV)

Status: PROPOSAL input, read-only measurement of the code. No production code was changed.
Base: robot_agent `8002347` (branch `generalize-core`), robot_fleet `d74f326` (read only), `embodiplan` as installed in the shared venv
(read only, outside this repo). Date: 2026-11-22.

How to read the evidence: every `path:line` is relative to the root of this repo (`robot_agent/...`) unless it starts with `robot_fleet/`
(the repo `/media/keti/workdir/remote_dir/robot_fleet`). "Read" = I read the lines. "Run" = I executed a small snippet in the shared venv
and quote its output. Sizes were measured with `wc -l`, `awk` and a small `ast` script (commands below), never guessed.

Short answer (what the numbers say):

- One file, `robot_agent/core/planning/assist_loop.py` (4081 lines), is about 4.5 times the next core file (`robot_agent/core/planning/loop.py`, 912) and holds 218 functions; no function in it
  is over 80 lines. It is big because it has many small methods, not because of big methods.
- The household vocabulary is in three layers: closed TABLES (goal kinds, six conditions, a closed action set, a closed per-action recovery
  dict), closed BRANCHES (40 lines that compare a goal kind and 66 lines that name an action, in the loop alone) and closed ENGLISH TEXT
  (prompt, checker findings, questions). The yes/no words are already language-pack data; the yes/no PROCEDURE is closed.
- Two cheap cuts need no design: the module-level `Places` + `Memory` + `words` (195 lines, no dependency on the loop) and the goal-reach
  simulation (96 lines, no outward call). Together about 290 lines, more than 7 percent of the file.
- Of the seven claims G1-G7 in `pex_case/docs/framework_fit.md`: G2, G3, G4, G5, G6 are right (line numbers off by 1-6 at most); G1 is right
  only for the LLM understand path (wrong as stated for goals given by the caller); G7's evidence is wrong (`ask_model` exists) though its
  conclusion holds for a pack SKILL. Details in "Check of G1-G7".

## Sizes

Commands (run from the repo root; they are the source of every number in this section):

```
git ls-files 'robot_agent/*.py' | xargs wc -l | sort -rn | head -45     # file sizes (224 files, 44460 lines)
python3 <ast script: end_lineno - lineno + 1 per def>                    # function sizes
python3 tools/lint_arch.py | tail -1                                    # baseline of the architecture lint
```

Lint baseline (tools/arch_baseline.json via `tools/lint_arch.py`): 0 new findings, 40 import findings, 8 size offenders, wiring
`cfg_keys 62, cfg_branches 59`. Its limits are in `tools/arch_rules.json` (engine files 1200 lines, functions 80; options files 400, functions 60);
the baseline stores `assist_loop.py` as 4082 (it counts the last line without newline; `wc -l` says 4081).

### Files

| lines | file |
|---:|---|
| 4081 | robot_agent/core/planning/assist_loop.py |
| 1096 | robot_agent/connect/ros/node.py |
| 912 | robot_agent/core/planning/loop.py |
| 798 | robot_agent/utils.py |
| 740 | robot_agent/core/unified_agent.py |
| 723 | robot_agent/core/device_manager.py |
| 718 | robot_agent/core/planning/understand.py |
| 715 | robot_agent/connect/pool.py |
| 702 | robot_agent/contract/manifest.py |
| 671 | robot_agent/interact/run_manager.py |

17 files have 500 lines or more, 2 have 1000 or more (assist_loop and connect/ros/node). The 44 registered options in `robot_agent/options/`
(`REGISTRY.specs()` returns 44) total 5958 lines; the largest is `robot_agent/options/cond_goal.py` (399, the limit is 400). Other planner files that the loop leans on:
`robot_agent/core/planning/goal_fix.py` 634, `plan_repair.py` 488, `speech.py` 349, `option_services.py` 373, `goals.py` 285, `step_ladder.py` 174,
`search_hooks.py` 199, `which_one.py` 129 (the last three are already mixin classes of `AssistLoop`: `robot_agent/core/planning/assist_loop.py:362`).

### Functions

Over 100 lines in the whole package: 9, none in the planner or in `assist_loop.py`:
`robot_agent/core/device_manager.py:125` (`add_connect`, 240), `robot_agent/core/unified_agent.py:481` (`run_direct`, 178), `robot_agent/diagnose.py:47`
(`main`, 169), `robot_agent/core/unified_agent.py:339` (`run`, 138) and `:355` (`run._blocking`, 114), `robot_agent/observe/replay.py:363` (127),
`robot_agent/rename_skill.py:92` (108), `robot_agent/delete_skill.py:141` (101), `robot_agent/new_skill.py:459` (100). 18 functions are 80 lines or more,
36 are 60 lines or more (of 2791 functions).

In `assist_loop.py` (218 functions; class `AssistLoop` is lines 362-4081 = 3720 lines; 250 blank lines, 198 comment-only lines) the largest are
`AssistLoop.__init__` 80 (:369), `_understand_round` 78 (:1816), `_recover_find` 77 (:3808), `_v2_cfg_base` 77 (:465), `_ground_goals` 73 (:991),
`_read_back` 71 (:2438), `_generate_and_certify` 61 (:2682), `Places.candidates` 60 (:157). The 50 `self.options.` call sites and 95 `self.cfg` reads
(55 `.get(`, 40 `[`) in the file are the option wiring the lint counts.

### assist_loop.py phase map

Phases are the four of `_run_blocking` (`robot_agent/core/planning/assist_loop.py:1690-1696`: understand, plan, execute, final check). The
other rows are the support code each phase calls. Ranges are first line of the first function to last line of the last function (measured
from the `ast` spans); the "lines" column sums to 3688, the remaining 393 lines are the file header, module constants, `Places`, `Memory` and
blank/comment gaps (rows 0a-0d).

| # | lines | range | what it is |
|---|---:|---|---|
| 0a | 120 | 1-120 | docstring, imports, `_LOC_ACTIONS` (:81), `ASSIST_OPTIONS` (:83-100), `_USER_WORDS` (:101), `words`, `_strip_article`, `_fmt_en` |
| 0b | 130 | 123-252 | class `Places` (the live ENV as a closed place vocabulary) |
| 0c | 44 | 258-301 | class `Memory` (object -> last place, answers) |
| 0d | 50 | 307-356 | `UNDERSTAND_SCHEMA` (:307-327), `UNDERSTAND_PROMPT` (:329-356) |
| 1 | 288 | 369-656 | config resolution: `__init__`, `_v2_cfg_base`, `_um_cfg`, `_rm_cfg`, `_r3_cfg`, run-level helpers (`_lab`, `_now`, `_role`, `_manifest_planner`, `_llm`) |
| 2 | 284 | 658-941 | understand call: `_understand`, routed model, prompt flags, `_cannot_line`, `_over_limits`, `_capability_check`, `_understand_schema`, `_cannot_do` |
| 3 | 194 | 944-1137 | grounding of goals on the ENV: `_ground_container`, `_ground_template`, `_ground_goals`, `_ground_speaker`, `_person_destination` |
| 4 | 126 | 1143-1268 | language / ask / speak plumbing, read-back phrase (`summary`, `_goal_phrase`) |
| 5 | 273 | 1271-1543 | plan checker (gate) and goal-reach simulation: `_ground_step`, `_check`, `_word_step`, `_simulate_goals`, `_missing`, `_reaches`, `_goals_hold` |
| 6 | 114 | 1546-1659 | execution helpers: `_bind`, `_holding`, `_effect`, `_found_point`, `_place_of_far_point` |
| 7 | 123 | 1662-1784 | run driver `_run_blocking`, `_finish`, failure bookkeeping |
| P1 | 863 | 1787-2649 | PHASE 1 understand + clarify + read-back (the biggest block: 28 dialogue functions, 523 lines, plus grounding of the understanding) |
| P2 | 291 | 2651-2941 | PHASE 2 plan: `_plan_phase`, `_generate_and_certify` (gate rounds 0, symbolic, 1, 2), `_plan_call`, sub-goals, `_gate_repair` |
| 8 | 89 | 2943-3031 | replan (suffix repair, refine, `_replan_or_fail`) |
| 9 | 170 | 3034-3203 | goal change during a run, new command, task stack (`_apply_pending`, `_new_command`, `_suspend_for`) |
| P3a | 261 | 3205-3465 | PHASE 3 execute loop, plan end, coverage gap, observation of goals (`_observe_goals`, `_observe_goal`, `_look`) |
| P3b | 340 | 3467-3806 | guard, run one step, post-check, success, failure, policy, waits (`_guard`, `_run_step`, `_post_check`, `_step_failed`, `_apply_policy`) |
| P3c | 256 | 3808-4063 | recovery of Find: `_recover_find`, relooks, second look, search legs, phantom |
| P4 | 16 | 4066-4081 | PHASE 4 `_final_check` |

Note on structure: the loop already inherits from three mixins in other files (`WhichOne`, `SearchHooks`, `StepLadder`) and reaches its options
only through `LoopServices` (`robot_agent/core/planning/option_services.py`, 373 lines, `Services` protocol in `robot_agent/core/options/services.py:12-45`).

## Closed tables

"Closed" = a literal table or enum in code that a robot pack cannot extend; adding a goal kind, a condition or an action means editing the core.
Tests: 1 = default data a pack can already override (data), 0 = code.

### Goal kinds and goal fields

| table | where | what is closed | pack can extend? |
|---|---|---|---|
| `GOAL_KINDS` | robot_agent/core/planning/goals.py:33-46 | 7 kinds: on, in, open, close, go, say, deliver; each = needs (actions) + a prompt line | no |
| `MANIPULATION`, `TEMPLATE_KINDS` | robot_agent/core/planning/goals.py:32, :47 | which kinds the LLM may always use (4) and which are compiled without an LLM (go, say, deliver) | no |
| `OPTIONAL_KINDS` | robot_agent/core/planning/goals.py:50-54 | one extra kind, `give`, mapped onto `on` by `canonical` (:93-96) | no (switch only: option `understand_give_kind`) |
| `kinds_for` | robot_agent/core/planning/goals.py:74-81 | the LLM enum = MANIPULATION + template kinds the robot has actions for + optional | no |
| `missing_actions` | robot_agent/core/planning/goals.py:57-71 | an unknown kind returns `[its type]` (:61-63); `say` with a place also needs MoveTo (:69-70) | no |
| `domain_actions` | robot_agent/core/planning/goals.py:84-90 | the vocabulary of actions a refusal may name as missing | no |
| `UNDERSTAND_SCHEMA` enum | robot_agent/core/planning/assist_loop.py:317 (overwritten at :916 by `_kinds()`); `reason_type` enum :313-314 | goal `type` enum and refusal reasons | no |
| goal slots | robot_agent/core/planning/assist_loop.py:318-324 (required: type, object, from, to); robot_agent/core/planning/goals.py:114 (`dedupe` key); robot_agent/options/bt_goal.py:31 (`SLOTS`); robot_agent/options/ask_agree.py:51-52; robot_agent/options/answer_align.py:42-43 | every goal is `{type, object, from, to}` plus ad-hoc fields (`said`, `content`, `hand`, `near_person`, `user_at`, `from_any`, `person`, `name_local`); a goal with a slot that is not a place or an object has no home | no |
| kind literals elsewhere | 40 lines that compare a goal kind in `robot_agent/core/planning/assist_loop.py`; 24 in `goal_fix.py`; 23 in `goals.py`; 17 in `plan_repair.py`; 8 in `understand.py`; 1-3 in 21 option files | measured: `grep -E '(get\("type"\)|\["type"\]|\bt\b) *(==|!=|in|not in) *[("{]'` restricted to the kind words | n/a |

Sample of the branch points outside `assist_loop.py` (all read): `robot_agent/core/planning/goal_fix.py:63,67,77,99,104,124`;
`robot_agent/core/planning/plan_repair.py:67,208-212,236,257`; `robot_agent/core/planning/understand.py:299,356,418,424,432`;
`robot_agent/core/planning/search_hooks.py:65` (the search belief only for `on` / `in`); `robot_agent/options/precond_repair.py:30-31`
(`NEEDS`, `KINDS = ("on","in","hand")`); `robot_agent/options/plan_release.py:33-34`; `robot_agent/options/plan_complete.py:28`;
`robot_agent/options/heat_safety.py:21` (`PUT_KINDS`); `robot_agent/options/fetch_guard.py:34-35` (`_FETCH`, `_LOOKS`); `robot_agent/options/hazard.py:43-44`.

### Conditions, predicates and the belief

| table | where | what is closed |
|---|---|---|
| `CONDITIONS` | robot_agent/options/bt_goal.py:61 | `holding, hand_free, at, placed, opened, closed` (6 names) |
| `holds` | robot_agent/options/bt_goal.py:50-58 | the predicate bodies; unknown name or wrong arity returns False (:55-58) |
| tree validation | robot_agent/options/bt_goal.py:86-96 | a tree with a condition not in `CONDITIONS`, an act whose action the robot lacks, or an ask without the pack phrase is unusable (returns None); the goal then goes to SAGE with no event or warning |
| tree grammar | robot_agent/core/planning/behavior_tree.py:17-18 | composites `sequence, fallback` (+ `retry`), leaves `cond, act, ask, confirm, reply` |
| tree leaf arity | robot_agent/core/planning/behavior_tree.py:31 and :86 | `valid` accepts any number of string arguments; `tick` calls `host.<leaf>(*args)`; the host's `act` (robot_agent/options/bt_goal.py:164) takes exactly `(action, obj)` (read, not run: a 3-argument act would raise TypeError at tick) |
| world / belief fields | `embodiplan` `action_model.py` lines 105-122 (class SymbolicState: arrived, found, holding, opened, on, visible); robot_agent/core/planning/action_model.py:19-28 loads it | the belief the loop reasons with is a fixed 6-field household state (one `holding`, one `arrived`) |
| action rules | `embodiplan` `action_model.py` line 266 (`check`), `base.py` line 53-57 (`ROBOT_ACTIONS`: MoveTo Find Pick Place PutIn Open Close TurnOn TurnOff Wash Sit LieOn Serve Wait) | the rules (hand_full, already_open, place_without_holding ...) and the closed action set; outside this repo |
| `CONTEXT_CODES` | robot_agent/core/planning/action_model.py:58 | three violation codes that need robot context |
| goal-kind checks of "holds" | robot_agent/core/planning/assist_loop.py:1484-1501 (`_missing`), :1504-1521 (`_reaches`), :1523-1543 (`_goals_hold`) | the per-kind test written as an if-chain: on, in, open, close, then `TEMPLATE_KINDS` via `goals.reached` (robot_agent/core/planning/goals.py:243-266) |
| end-of-run look | robot_agent/core/planning/assist_loop.py:3417-3418 | only `on` and `in` are looked at; any other kind returns "no sensor declared for it (belief)" and is never observed; run: `goals.missing_actions({"type":"install"}, ["Pick","Place"])` returned `['install']` |

### Action signatures and action names

| table | where | what is closed |
|---|---|---|
| act signature | robot_agent/options/bt_goal.py:164-169 | one step is `{"action": action, "object": obj}`; no other argument reaches the skill from a tree |
| `_LOC_ACTIONS` | robot_agent/core/planning/assist_loop.py:81 | `MoveTo, Place, PutIn, Open, Close` take a place; the same idea again in `robot_agent/core/planning/reconcile.py:31` (`AT_PLACE_ACTIONS`), `robot_agent/core/planning/mapper.py:45-46`, `robot_agent/contract/legacy_adapter.py:30`, `robot_agent/core/planning/search.py:31-34` (`_BETWEEN`, `_AT_PLACE`, `_NOT_A_PLACE`) |
| `NOT_PLAN_STEPS` | robot_agent/core/planning/robot.py:38 | `Observe, Verify, Ask, Query` are not plan steps |
| `_ABILITY` | robot_agent/core/planning/robot.py:42-45 | what each abstract action says in the understand prompt (8 actions) |
| `ORDER`, `ACTION_TEXT`, `_CAPS` | robot_agent/core/planning/robot_prompt.py:22, :26-37, :40-42 | 10 actions and their argument words and descriptions; capability words (base, lift, head, drawer, voice, speaker, tray) |
| `ROLES` | robot_agent/core/planning/action_roles.py:24; robot_agent/contract/manifest.py:99 (`ACTION_ROLE_KEYS`) | 7 roles a pack may name an action for: put_down, move, open, close, find, pick, say |
| `RELEASES`, `OPS` | robot_agent/core/planning/plan_repair.py:52-54 | `Place, PutIn`; 13 repair operators |
| `_NOOP`, `_PICK_HELD` | robot_agent/core/planning/plan_tidy.py:27-28 | violation codes treated as no-op steps |
| per-action recovery | robot_agent/core/planning/assist_loop.py:3701-3703 | `{"Find","Pick","MoveTo","Open","Place","PutIn"}` -> a `_recover_*` method; any other action has no ladder and goes to the planner |
| `STEP_LADDER` | robot_agent/contract/recovery.py:97 | only `Pick` (GRASP_FAILED, may replan) and `MoveTo` (BLOCKED, may not) have a ladder |
| `DEFAULT_RECOVERY` | robot_agent/contract/recovery.py:66-91 | 13 failure codes with retries and next action (e.g. ABSENT 3 retries then ask); a pack may override it per manifest (data), the default is household |
| failure codes | robot_agent/contract/failures.py:26-57 (`_RULES`, 32 lines of text patterns) | text patterns that classify a skill's error message into a code |
| `VERBS`, `PHRASES` | robot_agent/core/planning/announcer.py:25-39, :42-55 | English verbs of 27 action names (15 CamelCase, 12 lower-case skill names) and the 10 progress templates; the pack's `verb.<Action>` wins (:141) |
| `PLANNER_KEYS` | robot_agent/contract/manifest.py:86-96 (`PLANNER_KEYS`, 72 names) | the closed list of keys a pack may write in `planner:`; the open door is `planner.options.<option>.<key>` (:96 comment, `robot_agent/core/planning/pack_data.py:1-60`) |

### Phrases (the core's English text)

| table | where | what |
|---|---|---|
| `CORE_PHRASES` | robot_agent/core/planning/speech.py:24-87 | 62 English phrases: questions (`q_where_put`, `q_which_drawer`, `q_where_are_you`...), read-back pieces (`rb_on`, `rb_in`, `rb_open`, `rb_go`, `rb_say*`), `say_*`; a pack may override each in its i18n, by language |
| `HELP_PICK`, `HELP_MOVE` | robot_agent/core/planning/speech.py:89-90 | the fixed English ends of the help questions of the Pick and MoveTo ladders (a bench parser anchors on them) |
| `CORE_TEXT` | robot_agent/core/planning/robot.py:21-34 | 8 English defaults (`cannot_do`, `delivery`, `robot_description`, `yes_or_no`, ...) |
| `BUDGET_TEXT` | robot_agent/core/planning/budget.py:23 | the budget-end sentence |
| `UNDERSTAND_PROMPT` | robot_agent/core/planning/assist_loop.py:329-356 | "You turn a household robot task into goals", example kinds and a drawer, "tennis ball", "red cup" |
| `_CANNOT`, `_CAN_OPEN` | robot_agent/core/planning/assist_loop.py:844-846 | what a household robot cannot do (throw, pour, cook, wash, climb, open windows or room doors, use tools, handle knives near people, leave the house) |
| goal prompt lines | robot_agent/core/planning/goals.py:38-45, :51-53 | the understand line of go / say / deliver / give |
| `_SAY` | robot_agent/core/planning/understand.py:469-471 | English reasons of the "issues" check |
| speak keys that survive the language layer | robot_agent/core/planning/speech.py:95-96, :109 | `READBACK_KEYS`, `READBACK_PLAIN`, `APOLOGY_KEYS` |

### Words, answers and how they are read

| table | where | what |
|---|---|---|
| yes / no words | robot_agent/interact/dialog.py:36-40 (compiled at import from every language pack: robot_agent/core/planning/lang/en.json:146-160) | DATA (language packs), but compiled once; a pack cannot add words per robot |
| `yes_no` | robot_agent/interact/dialog.py:119-130 | closed three-way logic: a no-word returns False; else a yes-word returns True; anything else returns False with the text as a correction. Run: "Use the measured clip positions and try once more." -> `(False, same text)`; "Yes. Use it" -> `(True, '')`; "No, leave it." -> `(False, 'leave it')`; "go ahead" -> `(True, '')` |
| `_YES`, `_NO` | robot_agent/core/planning/verify_vlm.py:29-30 | English tokens for a VLM verdict |
| bt `confirm` | robot_agent/options/bt_goal.py:175-177 | `yes_no(ans)[0] is True`: the answer to a confirm is one bit |
| bt `ask` | robot_agent/options/bt_goal.py:171-173, :136-143 | the answer is matched against word lists named by the tree (`block.key` of the language packs); this is the open route to "answer in words" |
| menu answers | robot_agent/options/bt_goal.py:119-127 (`read_menu`: stop, place, retry), robot_agent/options/ask_next.py:31-32 (`CHOICES`, `MENU` keyed where / pick / move) | closed answer kinds |
| speaker words | robot_agent/core/planning/goals.py:152 (`SPEAKER_WORDS`), robot_agent/core/planning/assist_loop.py:101 (`_USER_WORDS`, a second copy), robot_agent/core/planning/persons.py:35 (data from packs) | three places say who "me" is |
| English verb regexes | robot_agent/core/planning/understand.py:155-165, :309-315; robot_agent/core/planning/goal_change.py:28-41; robot_agent/core/planning/goal_filter.py:28 (`_MOVES`) | English move / drop / destination wording used to check or edit goals (put, bring, take, ... forget, skip ...) |

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
| `ABSENT` retries | robot_agent/contract/recovery.py:82 (comment says "assist_loop's search_max") | 3 |
| geometry defaults | robot_agent/core/planning/assist_loop.py:401-410 | taken from the manifest (`arrive_tol_m`, `reach_m`, `reach_far_m`): data, correct pattern |
| manifest `limits` | robot_agent/contract/manifest.py:104 (`LIMIT_KEYS`: `payload_kg`, `max_object_size_m`) | only these two body limits exist |

Nothing in `robot_agent/core`, `robot_agent/options`, `robot_agent/contract` or `robot_agent/interact` names a robot, a site or a benchmark in code
(the lint rule `forbidden_modules` in tools/arch_rules.json holds this). The coupling is to a household DOMAIN, not to a named robot.

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

### b) Action names in the loop (66 lines name an action in quotes; by function)

`_LOC_ACTIONS` :81; `_ground_step` :1276, :1284 (`action_model.ROBOT_ACTIONS`); the checker :1317, :1354-1421 (including the finding text
"use MoveTo, Find, Pick, Place, PutIn, Open, Close" at :1355 and per-action wording for Open, Close, Find, Pick, Place, PutIn); `_simulate_goals`
:1459-1470; `_bind` :1554-1565 (Say / Announce, Find `min_conf` / `verify`); `_effect` :1607-1620; `_post_check` :3606-3627 (a release looked at twice, a
Find seen out of reach); `_precondition` :3470-3474; `_guard` :3522-3540 (Pick with a stale hold, Place with a lost object); `_step_failed` :3701-3703
(the per-action recovery dict); `_refind_after_pause` :3239-3242; `_look_at_untouched` :3340-3341; `_observe_goal` :3431-3434; `_end_action`
:3376-3386; `_generate_and_certify` :2694 and `_lab_subgoals` :2785 (`"Say" in plan_actions`).

### c) English text the loop itself speaks or asks

Questions: :1079 and :1123 ("Where are you?..." / "Where is X?"), :951-952 (which drawer), :987-989 and :1048-1049 (which place / which x), :1007-1008 (where to put).
Wording of every finding the checker sends to the planner: :1354-1421. Failure messages: :3536-3537 ("I can't pick things up from the floor"), :3759 ("something
is wrong with my hardware"), :3524 and :3533 ("not in the gripper"). English fallbacks in `_goal_phrase`: :1251-1268. Prompt flags that rewrite the prompt by
`str.replace` of fixed English sentences: :807-820.

### d) English words and patterns

Filler words that Places strips before matching: :179-180 ("the", "of", "on", "in", "at", "kitchen's", "it", "is", ...); the English prepositions `" in the "`,
`" in "`, `" at "` replaced at :175; `_strip_article` :115 (the, a, an, my, your); the English regex of a person's location answer :1074; the English regex of an
"empty request" reason :891-892; the literal `"floor"` as a place name at :980.

### e) Domain concepts built into the core

- The place key `name@room` (`Places.head`, `Places.room`, :133-142; 23 lines with an `"@"`; `.replace("@", " in the ")` at :987, :1046, :1230, :1352, :1407, :3420;
  `robot_fleet/voice.py:56-61` makes the same convention again in the fleet).
- Openable containers (`Places.openable` :144-148) and drawers (:948, :1053), a hand (`g["hand"]`, :1103-1129), a person as a destination (`persons.is_person`, :1239, :1259),
  the speaker (:1010, :1065-1079), a surface beside a person (`near_person`, :1243-1247).
- A single `holding` slot in the belief and the single hand (`world.holding`, :1455, :1535-1541, :3522-3540) and the closed state fields from `embodiplan`.

## Fleet tables

Repo `/media/keti/workdir/remote_dir/robot_fleet` at `d74f326` (read only; other people's uncommitted files there were not touched). Sizes (`wc -l`): orchestrator.py 622,
scheduler.py 200, spoke.py 193, api.py 192, graph.py 104, voice.py 109, assign.py 130, worldnote.py 68.

| table | where | what is closed |
|---|---|---|
| `_NEEDS` | robot_fleet/scheduler.py:16-23 | goal kind -> alternatives of required plan actions: on {Pick,Place}; in {Pick,PutIn} or {Pick,Place}; open {Open}; close {Close}; go {MoveTo}; say {Say} |
| use of `_NEEDS` | robot_fleet/scheduler.py:40-54 | `_NEEDS.get(goal.type)` None -> "unknown kind: infeasible" (:41-43); a robot is feasible when one alternative is inside its `plan_actions` (:48-50) and every place the goal uses maps to the robot's own key (:51-52) |
| difference to the robot | robot_agent/core/planning/goals.py:33-46, :69-70 vs robot_fleet/scheduler.py:16-23 | the fleet has no `deliver` (robot: MoveTo + WaitForUnload) and no `give`; the fleet's `say` needs only Say while the robot's `say` with a `to` also needs MoveTo; the two tables are kept by hand and can drift |
| `_CONTAINER_KINDS` | robot_fleet/graph.py:26, used :39-40 | `open` / `close`: the object counts also as a place in the footprint |
| footprint | robot_fleet/graph.py:35-49 | a goal's footprint is object + `from` + `to` (+ object for containers); the conflict rule (:66-69) assumes every kind moves one object between places |
| goal model | robot_fleet/models.py:31-44 | `type: str` (open), `object`, `from_`, `to`, extra keys kept: the same four slots as robot_agent |
| what a robot reports | robot_fleet/spoke.py:56-61; robot_agent/api/robot_info.py:17-21 | the manifest answer carries `plan_actions` (sorted), `capabilities`, `places`, `site`, `version`; the fleet keeps only version, plan_actions, places, site; the robot does NOT say which goal kinds it can take or what each needs |
| voice | robot_fleet/voice.py:64-70; robot_fleet/voice_packs/en.json:41-50 | goal wording is DATA per language (`goal.on`, `goal.in_from`, ..., `goal.default` for an unknown kind): already open; `place_name` (robot_fleet/voice.py:56-61) uses the `name@room` convention |
| holding memory | robot_fleet/worldnote.py:30-32, :40-47; robot_fleet/orchestrator.py:78, :448, :581 | the fleet reads `world.holding` from run events and prefers a robot with an empty hand (robot_fleet/scheduler.py:68, :74): a single-hand manipulation idea |
| assign / history | robot_fleet/assign.py:18, :26-28; robot_fleet/history.py | keyed by the goal kind string: open (any kind works) |
| read-back rule | robot_fleet/orchestrator.py:242 | a `clarify` with kind None, "" or "confirm" after the goals is taken as the robot's read-back (the S3 bug of framework_fit; robot_agent sends kind-less questions at robot_agent/core/planning/assist_loop.py:2576) |
| understand request | robot_fleet/spoke.py:140 | `options: {"goals_only": "1", "confirm_task": "0"}` as strings; robot_agent reads them without coercion (robot_agent/core/planning/assist_loop.py:397-398: the string "0" is true) |

Reading: only `_NEEDS` and `_CONTAINER_KINDS` (plus the one-hand `holding` idea) are closed kind knowledge; voice and assignment are already open. The robot's own manifest
endpoint is the natural carrier for per-kind needs, because it already carries `plan_actions` and is read by `HttpSpoke.manifest` on every refresh.

## Check of G1-G7 (pex_case/docs/framework_fit.md)

File checked: `/media/keti/workdir/remote_dir/pex_case/docs/framework_fit.md` lines 40-46 (table "Gaps and workarounds"), against robot_agent `8002347`.

| id | claim | verdict | evidence |
|---|---|---|---|
| G1 | kinds are a closed table; `GOAL_KINDS` at goals.py line 33-47; `missing_actions` :57-65; "an unknown kind is refused before planning" | RIGHT for the table and the lines (the function is :57-71, the table :33-46). PARTLY WRONG for "refused before planning": the refusal exists only on the LLM understand path (`typed_refusal(..., self._cannot_do(r.goals))`, robot_agent/core/planning/assist_loop.py:1842-1869), and on that path an unknown kind cannot even appear because the schema enum is `kinds_for` (:799-800, :913-916). Goals given by the caller (`goals` + `accept_goals`, the fleet path) skip that step: `_take_given_goals` says "no refusal" (:1805-1814), so an unknown kind goes on to planning or to a declared tree. Run: `bt_goal.tree_for` with a tree declared for kind `install` and known leaves returned the tree; so only the sentence-to-goal reading and the fleet's `_NEEDS` (robot_fleet/scheduler.py:41-43) close the kind today. The end check also never looks at such a goal (:3417-3418) | read, plus run of `goals.missing_actions`, `bt_goal.tree_for` |
| G2 | six predicates only; `CONDITIONS` bt_goal.py line 61, `holds` :50-59 | RIGHT (`holds` is :50-58). Missing from the claim: a tree with another condition name is not an error, it is silently unusable (robot_agent/options/bt_goal.py:93 returns None) and the goal falls to SAGE with no event. Run: a tree with `["cond","seated","{object}"]` gave `tree_for -> None`; `holds("seated", ...)` gave False | read + run |
| G3 | `act(action, obj)` only: bt_goal.py line 164-168 | RIGHT (the method is :164-169; the step it builds is `{"action": action, "object": obj}` at :169). Added: the tree grammar would let a longer act through `valid` (robot_agent/core/planning/behavior_tree.py:31), it fails later at tick (:86) | read |
| G4 | Move / Pick / Place failures run the engine's own ladders past the tree; step_ladder.py line 83-103 | RIGHT (`_recover_move` is exactly :83-103). Mechanism: bt `act` calls `try_step(ladder=True)` (robot_agent/core/planning/option_services.py:74-92); the ladder ends in `_finish("failed")` (robot_agent/core/planning/step_ladder.py:101-102) which ends the run, so the tree sees STOPPED, not FAILURE; only the planner rung is held back (`no_replan`, robot_agent/core/planning/assist_loop.py:2963). A custom action has no ladder (:3701-3703), so its failure does reach the tree: this is what framework_fit says about `MeasureClips` | read |
| G5 | the question is a pack phrase with object/place slots; the answer is read with the core's yes/no; `confirm` :175-177; `pack_phrase` speech.py line 248-256; `yes_no` dialog.py line 119-130 (and :64-100) | RIGHT, all four ranges are exact (`ask` is :64-100, `yes_no` :119-130, `pack_phrase` :248-256, `confirm` :175-177; the slots are set at robot_agent/options/bt_goal.py:184-186). I re-ran four of its sentences through `yes_no`: the "Use the measured clip positions and try once more." result `(False, text)` is reproduced. One addition: the yes / no words are language-pack data (robot_agent/interact/dialog.py:36-40), so the closed part is the three-way procedure, not the words; the `ask` leaf (robot_agent/options/bt_goal.py:171-173) already reads word lists | read + run |
| G6 | the per-node trace is a lab flag, not in the event stream; `note` :158-160 | RIGHT (`note` is :158-159 and calls `svc.lab`). `lab_log` is capped at 50 entries and "never part of the event stream" (robot_agent/core/planning/assist_loop.py:604-610), so a long run loses the tail | read |
| G7 | a pack skill cannot use the engine's LLM channel or budget; "no such service in option_services.py" | CONCLUSION right, EVIDENCE WRONG. `LoopServices.ask_model` exists: "ONE extra call to the planner's model ... counted in the run's LLM budget" (robot_agent/core/planning/option_services.py:161-173; protocol line robot_agent/core/options/services.py:37), and `llm_calls` counts calls (:54-63). It is a service for OPTIONS (hooks), not for a pack SKILL; a skill is run through `sr.execute` and gets no services. So the gap is "skills have no LLM channel", not "the engine has none" | read |

Other evidence lines of the file that I checked on the way:

- S1 (robot_agent/core/planning/assist_loop.py:397-398): RIGHT, the string "0" is true; fleet sends it at robot_fleet/spoke.py:140.
- S3 (:1895-1904 `_two_places`, :2576 kind-less clarify, :2128 and :2467 `kind="confirm"`; fleet `orchestrator.py line 242`): RIGHT (`_two_places` is :1895-1905).
- S9 (robot_agent/core/planning/announcer.py:141-144): RIGHT.
- P2 (robot_agent/core/planning/search_hooks.py:38-72, :82-95; search.py line 64-79): RIGHT (:38-73, :82-96, :64-79); the search start runs before `runs_goals` (assist_loop.py line 2658 vs :2661).
- P3 (assist_loop.py line 3622-3625, :407-409): RIGHT. P4 (step_ladder.py line 147-174): RIGHT (`_refind_dropped` :147-163 and `_splice_refind` :166-174).
- P6 (goals.py line 57-65): the function is :57-71, the quoted lines cover most of it.
- P7 (goals.py line 117-139, `conflicts`): WRONG LINES. `conflicts` is robot_agent/core/planning/goals.py:122-148 (the quoted range starts inside `dedupe`, :109-119); the question text is at :137.
- P8 (options/look_then_ask.py `on_goals`: `g["from"] = here`): RIGHT, :64.
- G11 cites `robot_fleet SPEC.md:117`: WRONG LINE (that line is the heading "7. Demo"). The statement "a started goal is never reassigned" is in `robot_fleet/docs/SPEC.md:3`, `:53`, `:100`, `:120`.
- G12 (docs/fleet_seams.md client_ref limits): RIGHT, docs/fleet_seams.md:72-73.

Not checked: G8-G10, G13, P1, P5, S2, S4-S8, S10 (fleet and pack side, not robot_agent lines).

## Cut candidates

Ranked by independence (how few other methods and attributes a block touches), then by size. The "touches" figures come from an `ast` call graph over the 218 methods
(`self.<method>` calls and `self.<attr>` reads). A block is "pure" when it has 0 outward calls and 0 non-method `self` attributes.

Constraints that every cut must respect (all measured or read):

1. The module names `Places`, `Memory`, `words`, `ASSIST_OPTIONS`, `_NEXT`, `_STOP`, `_DONE` are imported from `assist_loop` by 92 test files (69 of them `Places`, `Memory` or `words`) and by robot_agent/core/planning/search_hooks.py:20 and
   robot_agent/core/unified_agent.py:387: a moved name must be re-exported (one import line each).
2. `robot_agent/observe/replay.py:401-406` patches `current` and `reconcile_world` as attributes of the `assist_loop` module: code that calls them must stay inside that module.
3. Tests call some methods through the class: `AssistLoop._ground_template`, `_goal_phrase`, `summary`, `_reaches`, `_check`, `_bind`, `_releases`, `_on_reached`, `_simulate_goals`, `_fresh_question`, `runaway`,
   `_finish`, `_effect` (tests grep `AssistLoop\.[a-z_]+`); a moved function keeps a one-line class attribute or a mixin.
4. 28 test lines construct the loop with `AssistLoop.__new__` and set attributes by hand (they never run `__init__`): config extraction must not be the only place that sets an attribute they read.
5. tests/test_option_refresh.py:50-52 asserts that the word `refresh_options` is not in the source of `assist_loop.py`.

| rank | block | lines (file ranges) | touches | callers from outside the block | what it holds | notes for the design |
|---|---|---:|---|---|---|---|
| 1 | `Places`, `Memory`, `words`, `_strip_article`, `_fmt_en` | 195 (:107-120, :123-252, :258-301) | none of `AssistLoop`; imports `langdata`, `env_names`, `speech` (lazy) | 69 test files, `search_hooks.py`, the loop | the closed place vocabulary (`name@room`, aliases, openable, fillers) and the object memory | most independent block; net effect about -190 lines after a 3-line re-export; the `@` convention and English fillers move with it and become data later |
| 2 | goal-reach simulation: `_releases`, `_simulate_goals`, `_on_reached`, `_missing`, `_reaches`, `_goals_hold` | 96 (:1442-1543) | 0 outward calls, 0 attributes (only `getattr(self, "_env_places")` and `robot.release_actions()`) | `_check`, `_gate_repair_run`, `_repair_suffix`, `_tidy`, `_final_check`, goal change, the step success path (4 entry points) | the per-kind "is this goal reached / does it hold" if-chains (on, in, open, close, template) | the first target of the "Predicates" design: one table kind -> (reached-by-steps, holds-in-belief, missing-text); needs a class alias because tests call `AssistLoop._reaches` and `__init__` wraps it (:448) |
| 3 | read-back phrase: `summary`, `_goal_phrase` | 42 (:1225-1268) | 0, 0 (static) | 9 callers incl. read-back, goal edit, `_compare_views`; tests call both | per-kind English and `rb_*` keyed phrases | pure on `(goals, places, nice)`; target of the "Phrases" design; keep the static names for the tests |
| 4 | grounding, pure part: `_ground_container`, `_ground_template`, `_ground_speaker` | 61 (:944-989, :1065-1079) | none of `self` (two static, one instance without attributes) | `_ground_goals` | per-kind grounding of go / say / deliver / open / close | `_ground_template` is tested through the class; stays callable there |
| 5 | grounding, rest: `_ground_goals`, `_person_destination`, `_say_name` | 126 (:991-1063, :1081-1137) | `cfg`, `options`, `_remembered`, `_where_asked` | `_ground_all`, `_take_given_goals`, `_ground_understood`, patch paths | on / in slot grounding, the person as destination | needs the options query `person_where` (:1098); do after 4 |
| 6 | plan checker wording: `_check`, `_check_step`, `_word_step`, `_word_pick`, `_word_place` | 117 (:1300-1422) | 2 outward (`_missing`, `_reaches`), attributes `robot`, `_env_places` | 1 entry (`_generate_and_certify`, `_gate_repair_run`, ...) via `_check` | the checker's finding text per action (Open, Close, Find, Pick, Place, PutIn, MoveTo) | do after 2; the household action list at :1355 and the per-action wording are the data to declare; `_check` assigns `self._env_places` (:1305), a side effect to keep |
| 7 | config resolution: `__init__` body, `_resolve_options_cfg`, `_v2_cfg`, `_v2_cfg_base`, `_um_cfg`, `_rm_cfg`, `_r3_cfg`, `_converge`, `_manifest_planner` | 231 (:369-610, minus helpers) | `robot`, `options`, `cfg`, `original`, `_find_params`; reads `os.environ` | `__init__` only (+ `refresh_options` of a host) | all numeric defaults and the string-to-bool coercions (the cause of S1 is here: no coercion of `confirm_*`) | a function `resolve_cfg(overrides, env, robot, ...) -> dict` is the cut; order of `cfg.update` and the 28 `__new__` tests (constraint 4) are the risk |
| 8 | understand call and capability: `_understand` .. `_cannot_do`, `UNDERSTAND_SCHEMA`, `UNDERSTAND_PROMPT` | 284 + 50 (:307-356, :658-941) | 3 outward (`_lab`, `_llm`, `language`), 9 attributes (`cfg`, `options`, `robot`, `original`, `um_last`, `_llm_cfg`, ...) | `_take_understanding`, `_understand_round`, `_complete_override`, `_verify_verdict` | the household prompt, the schema, the refusal reasons, the `_CANNOT` text | where goal kinds and the prompt become data; do after the kinds design |
| 9 | Find recovery: `_recover_find` .. `_refuted` | 243 (:3808-4063) | 8 outward, 11 attributes | `_step_failed` (`recover` dict :3701), `_look` | the search / relook / phantom ladder | big but entangled with `SearchHooks`; a verbatim move into a mixin file is the safe cut, not a data cut |
| 10 | replan: `_gate_repair` .. `_replan_or_fail` | 183 (:2808-3031 excluding sub-goal helpers) | 12 outward | 7 entries | repair rounds, suffix repair | entangled; mixin-move only |
| 11 | goal change / task stack | 162 (:3034-3203) | 10 outward (`_ask`, `_understand`, `summary`, `yes_no`, ...) | 4 entries | NEW / MODIFY / ADD / DROP handling, suspended tasks | mixin-move only |
| 12 | read-back dialogue | 523 (28 functions, within :1937-2649) | 16 outward, 12 attributes | 13 entries | confirm, correct, restate, clarify | the largest block; mixin-move only, and only after the kinds and phrases are data (many `rb_` and `q_` keys live here) |
| 13 | step run and failure: `_guard` .. `_wait_health` and helpers | 379 (:3467-3806 + `_bind`, `_effect`, `_found_point`, `_place_of_far_point`) | 18 outward, 16 attributes | 2 entries (`_execute_phase`, `try_step`) | one step's life, the policy rung | mixin-move only; contains the per-action `recover` dict |

Reading of the ranking:

- Ranks 1-4 (about 394 lines, 9.6 percent of the file) are pure or nearly pure, so each can be moved with a trace-identical delegation and a test that the moved function equals the old one on the
  same inputs. Ranks 2 and 3 are also where the closed per-kind logic is, so they double as the first data-driven steps.
- Ranks 9-13 shrink the file only by moving methods verbatim into mixin classes in new files (the existing pattern: `WhichOne`, `SearchHooks`, `StepLadder`). That is behaviour-safe (method resolution is
  unchanged) but does not generalize anything by itself; it should follow, not lead, the data work.
- The two data tables that the three robots need (`GOAL_KINDS` with an extension point, and the condition set of `bt_goal`) are small (robot_agent/core/planning/goals.py:33-54, 22 lines;
  robot_agent/options/bt_goal.py:50-61, 12 lines); the large work is the 40 kind-comparison lines and 66 action-name lines that sit around them.

## What was not read

- `robot_agent/core/planning/loop.py` (912 lines, the `ClosedLoop` parent), `robot_agent/core/planning/language.py` (636) and `robot_agent/core/planning/goal_fix.py` (634) were measured and grepped, not read line by line.
- The options other than `bt_goal` were read only at their closed tables and kind comparisons.
- `embodiplan` rules (`action_model.py`, `base.py`) were read at the cited lines only; it is a separate package.
- The robot packs (`kcare_robot`, `symbolic_robot`, `pex_robot`) were not inventoried here beyond `pex_robot/configs/manifest.yaml` lines 22-28 (`bt_goal.trees` for the kind `on`); the design stream owns the per-pack needs.
- No run of the loop was made; the only executions are the five snippets quoted above (`missing_actions`, `kinds_for`, `tree_for` twice, `holds`, `yes_no`).
