# Changelog

robot_agent follows SemVer for its contract (`robot_agent.contract`, the
manifest format, the event schema). See `docs/contract.md`.

## Unreleased

- 2026-11-15 New option `close_opened` (D4 layer L24, class container left open, e.g. a hand goal whose Close could not run with the hand full; default off): every container an Open step of the plan opens, that the replay on the belief (engine action model, the robot's capabilities) leaves open and that no `open` goal names, is closed again: a Close refused for `hand_full` is taken out and `MoveTo C` / `Close C` go in at the first point after the robot's last step at C where the hand is empty (after the release). The gate's `op_hoist` moved such a Close only when the release was in the plan it edits and never added a missing Close. Data: the `open` / `close` (and `move`) roles of `planner.action_roles`; none = unchanged. Hooks `on_start`, `on_plan` (order 89). Off and the default path: unchanged (golden green).
- 2026-11-15 New option `goal_ledger` (D4 layer L18, class goal_dropped and part of asr_loss; default off): `goal_fix.drop_invented` (F4.4) dropped a `go` goal whose place is not an exact mention while other goals remained, so a request ending "... then go back to the <place>" whose last word the recogniser garbled lost its go goal silently and the run ended done without it. The engine now asks the new service `goal_keep(run, goals)` at that drop site (argument `keep_go` of `drop_invented`; one call line rewritten in place in `assist_loop.py`, no option: None = dropped as before, golden green). `restore` (on with the option): a heard word that STARTS with a name of the place (head or alias), the name at least `min_share` (0.5, assumed) of the word, keeps the goal in its position; `on_issues` settles its `unsaid` issue and the heard word's own object issue, so no question and no re-sample. `named` (off): a goal with no such word is kept and the engine's read-back names it with the robot's doubt (no new question). Kept at the drop site, the goal passes the safety gate, `cond_goal` and `go_steps`, and the read-back's re-samples keep it. `retract` (off): an `on` / `in` destination named only in a mention a self-correction marker takes back (words follow the marker and name nothing of another goal) gets the engine's `unsaid` issue with its object, so the read-back asks the engine's own where-question; `retract_markers` (manifest, per language, labelled) narrows the pack's markers. No "Did you mean X?" question. Service `ledger_open(run)`: the go goals dropped with no evidence, for the end of the run (the option says nothing at the end).
- 2026-11-15 New option `repair_sync` (D4 layer L17, classes replan_ignores_gate / gate_repair_blind; default off): (1) `gate_sync`: the gate's symbolic repair (`plan_repair.repair`) built its plan from the planner's blocks while the gate scored the plan after the `on_plan` options and the tidy, so an operator never saw a step an option added (a Close with the hand full stayed before the release an option appended); the service `repair_kwargs` now gives the repair `sync`, which lines the blocks up with the scored plan (LCS on action and object; an added step joins the block before it); when the repair of the blocks leaves a critical finding, the operators run again from that plan, kept (and written into the blocks) only when it certifies the plan. (2) `finish_rest`: the suffix operator `finish` passes over a goal the suffix neither holds nor lets go of when it holds in the belief (`run.placed`, hand not holding it) instead of giving up at it. (3) `off_goal`: hook `on_replan_findings` adds the finding `release_off_goal` when a suffix's last release of an `on` / `in` goal's object is at another place (a staging put-down picked again is not one). Core: `plan_repair.repair(sync=, finish_rest=)` (defaults: unchanged; the edit loop is now the helper `_edits`), `assist_loop` passes `**query("repair_kwargs", r, default={})` to its two repair calls (two lines rewritten in place). No pack data. Off and the default path: unchanged (golden green).
- 2026-11-15 New option `effect_check` (D4 layer L16, classes drop_recovery / grasp_misread / silent_drop; default off): a declared release (the hand-over too) that FAILED while the belief holds an object reads the robot's holding sensor once; empty = the belief lets go and the contract's DROPPED refind runs (floor place of the room), holding or unreadable = the belief keeps it (the failure code is never read as an empty hand). Service `hand_reading`: the engine's `_holding` reads a declared "cannot read" code as the caller's default, not as empty. The goal check of an object the run released puts that room's floor place first in its search. Data `planner.options.effect_check.effects` (sensor skill + unknown codes, releases, floor head; labelled). New engine services `hand_holds`, `let_go`; `_holding` asks `hand_reading` (no answer = `isdone`, the default path unchanged, golden green).
- 2026-11-15 New option `plan_release` (D4 layer L15, class plan_missing_step; default off): the held object's own release (the pack's `achieves: in` skill, data `planner.options.plan_release.needs`, labelled) goes before the Close of its `in` goal place when the object's NEXT release in a one-hand replay is elsewhere and the plan does not pick it again; `plan_complete` skipped that splice whenever ANY later release existed (a put-down of another object too), so the gate kept a plan it could not repair. The release the splice empties gets the Find / Pick of the one unpicked goal object of its place, or goes when that place is served later; no Place -> put-in swap (the gate's operators do it). Hook `on_plan` at order 87 (after `precond_repair` and `plan_complete`), `on_start` for one lab event per distinct edit. Two hands or more, no data, no roles: unchanged. Off and the default path: unchanged (golden green).
- 2026-11-15 Lab flags (D4 simplification step S0, instrumentation only; behaviour and the default path unchanged, golden green): `hazard`, `dedupe_keep_end` and `lang_words` record a lab event when they act (`hazard`: `{rules, refused, goals}` when the gate refuses or drops goals; `dedupe_keep_end`: `{object, kept}` at a cut whose two ends differ, its service `keep_after_dedupe()` now returns the function the gate's repair asks there (`plan_repair._cut_end`; a plain True still works); `lang_words`: `{lang, condition, negation}`), and the `near` need of `precond_repair` and the key `more_tries` of `hw_wait` get their own flags (`precond_repair.near`, `hw_wait.more_tries`; before they were under the option's flag). The loop now clears `lab_log` BEFORE `on_start` (it cleared it after, so an event of `on_start` was lost; no option logged there before). New test file `tests/test_option_asr_guard.py`.
- 2026-11-15 Hook `on_listener_rule` wiring: the loop no longer rewrites its call of `goal_fix.shared_listener`; it puts the declared hook on `places.listener_rule` (only when an option answers it, else None), next to the `goal_guard` service on `places.guard`, and `goal_fix.shared_listener` runs it first (a None answer: its own rule). Same behaviour as the previous entry; `assist_loop.py` keeps its line count (the read-back's cap call is one line). Default path: identical.
- 2026-11-15 Options `recipient_keep` and `ask_agree` (D3 layers L10 / L14 review fix): their "I can read that two ways" question carried its own English sentence (`text=`) in the option; it is now only the phrase `q_which_reading` (already a core phrase, `speech.CORE_PHRASES`; the English is the same), so another language says it from the robot's i18n or the language pack's `speech` block without a translation call; Korean declares it (`lang/ko.json`, `assumed`). With no text in the run's language the question is not asked. Off and the default path: unchanged.
- 2026-11-15 Engine service `emit(run, event, **fields)` (D3 layer L14 review fix): an event from a hook or service that cannot yield is logged with the run's events and pushed through the host's live channel (the loop's `emit`, as `plan_step` is) when it has one. Option `ask_agree` asks its either-or from the read-back's query service `unrefused`: it now sends the question's `clarify` event through it and decides nothing when the run was stopped while asking (the answer is not kept, no reading picked); the turn was already counted in `run.asked` by the service `ask` (now tested through the loop). Off and the default path: unchanged.
- 2026-11-15 Engine service `readback_left(run)` and rule `dialog_converge.readback_left` (D3 layer L14 review fix): option `ask_agree` decided whether one more read-back was possible with its own copy of the loop's cap (`confirms < 2`), which ignored the extra read-backs `dialog_converge` grants and a `confirm_limit` (option `ask_focus.max_confirms`); the loop's read-back and the service now ask the one rule. The loop's result is identical (the old `default=9` of `confirm_limit` never bound: the cap is at most 4).
- 2026-11-15 New hook `on_listener_rule(run, goals, words)` (decide), D3 layer L10 review fix: option `recipient_keep` put its evidence rule on the run's places as an attribute (`places.listener_rule`) that `goal_fix.shared_listener` ran instead of its own, so the option registry and the hook ratchet could not see it. The loop now asks the hook and runs `goal_fix.shared_listener` only when no option answers; the attribute is gone. No option on the hook (the default path): identical.
- 2026-11-15 Option `replan_guard` (D3 layer L12 review fix, D1 layer `replan_block`): `guarded_run` dropped a guarded finding of a replan's suffix also when no replan was left in `max_replans`, so the suffix ran, the run-time check refused the step, found no replan budget and the run failed after the earlier suffix steps had moved the robot (it ended holding the object); a guarded finding is now left out only while a replan is left, else it stays and the run ends uncertified before the suffix runs. The hook also records its round before any early return, so the refine of a replan whose first call returned early is not read as a first call. Off and the default path: unchanged.
- 2026-11-15 Option `cond_extract` (D3 layer L9 review fix): the goals it drops as a statement of the condition are served through `refused_goals` / `goal_dropped` (as `cond_goal` serves its branch drops); before, they were only listed in `run.filtered`, so the read-back's re-sample (an issue on another goal) brought the condition's object back as a goal and the run failed on it. Off: unchanged.
- 2026-11-15 Option `cond_extract` (D3 layer L9 review fix): a later reading of the same run gets the first extraction plan again only when it reads exactly as the first one (`dialog_converge.goal_sig`, order kept); before, the same NUMBER of goals was enough, so a correction that changed an object or the order moved the if / else / drop numbers onto other goals. Off: unchanged.
- 2026-11-15 `name_recover` (D3 layer L11 review fix): with the default `understand_samples` 2 the read-back undid the name taken: the garbled word is in the person's words and not in the goals, so it raised an object issue, the re-samples read the garbled word again and that issue-free reading replaced the goals without passing the option (the run planned for the garbled name). New hook `on_issues` of the option: the garbled word counts as covered by the name taken, and a reading that holds the garbled word again gets the issue that the name taken is missing. The loop tests assert the final goals with 2 and 0 re-samples. Off and the default path: unchanged.
- 2026-11-15 `name_recover` (D3 layer L11 review fix): the safety gate (`hazard`, order 10) ran before the option (order 70) and saw only the garbled name, so a garbled name of a forbidden object, once recovered, was carried out. A goal object the option changes now goes through the gate again (new engine service `safety_gate`, over the new `Dispatcher.decide_flow_of(service, hook)`: the `on_goals` of the options serving `forbids`); the option runs at order 17, after `cond_extract` and before `cond_goal`, and recovers the object of a condition (`when`) by the same rules (it was looked for under its garbled name). Off and the default path: unchanged.
- 2026-11-15 `name_recover` (D3 layer L11 review fix): a near catalogue name is no longer taken silently: the catalogue is often only the object memory, so a real word one letter from a known name was renamed to it. One near name is asked ("Did you mean the X?", `q_did_you_mean`), several as before (`q_which_name`, now a core phrase); a silent take needs a catalogue the robot declares `complete` (new key, default false) and a pack word list (`asr.words`) that does not hold the word. The questions are phrases: `speech.phrase` now falls back on the language pack's `speech` block (Korean declares `q_which_name`) before the core English; no text in the language: nothing asked (fail open). A word is settled once per run. Off and the default path: unchanged (no pack declared any `speech` key before).
- 2026-11-15 `asr_guard` (D3 layer L11 review fix): the English `asr.hole` patterns no longer flag normal requests: `his` is left out of the possessives (it also ends a phrase, 'the cup that is his'), a bare `a` is no determiner evidence (a letter name, 'vitamin a', 'section a.'), and `and` / `or` followed by another possessive is a coordination ('my and your shoes'); the real holes ('the to the table', 'in my') are still asked again. Off: unchanged.
- 2026-11-15 Options `cond_extract` and `cond_goal` (D3 layer L9 review fix): their texts (`cond_extract.prompt` / `q_unclear`; `cond_goal.prompt` / `q_where` / `q_unsure` / `say_seen` / `say_not_seen`) are used only when labelled (new keys `source` / `assumed`, from the run options or the section `planner.options.<option>`; unlabelled = as with no text, like `precond_repair`, `plan_complete`, `replan_guard`), and a key the loop resolved empty is read again from the manifest at run time (new helpers `pack_data.fresh` / `labelled_keys` / `planner_of`), so a host that merges pack data after the loop is built does not get an inert option. Off: unchanged; golden green.
- 2026-11-15 `refresh_options(loop, run_options)` (`core/planning/option_services.py`), D3 layer L9 `cond_extract` fix: the option keys are resolved once, when the loop is built, so pack data a host merges into a built loop's manifest (the benchmark's overlay) was not seen: `cond_extract` was on in every D3 run with an empty `prompt` and made no extraction call (its `q_unclear`, `clauses` and `cond_goal`'s `q_where` / `q_unsure` were empty too). A host calls it after the merge; the engine never does (default path unchanged). Tests: every registered key merged after the build reaches its option, a made-up option, `cond_extract` through the loop with the prompt only in the manifest (one call; none without a condition word; asks once when unsure).
- 2026-11-15 Option `replan_guard` (D3 layer L12, D1 layer `replan_block`), two new parts, keys on with the option (off: the option and the default path unchanged): `guarded_run`: a critical finding of a replan's suffix that the engine's own run-time check (`symbolic_check`, the check of `_guard`) refuses before the step moves only steers the one refine; a refined suffix (or one with no refine left) with only such findings runs and the guard replans at that step, the uncertified end stays for findings the run-time check cannot catch (on d3dev the old end stopped 27 runs, 9 of them D2 successes, and the refine never removed the finding); `release_dedupe` (hook `on_plan`): an empty-hand release that repeats the plan's last release at the same place is dropped. New service `replan_round` (replans recorded, planner calls left).
- 2026-11-15 D3 review fixes: `cond_goal` has a third state UNKNOWN (a condition it cannot look at and the person did not settle ends the run `failed` with the question, no branch guessed) and serves `refused_goals` / `goal_dropped`; `Dispatcher.collect` (the read-back counts the goals both `hazard` and `cond_goal` dropped; a re-sample does not bring them back); `cond_extract` applies its first plan to a later reading of the same run; `hw_wait` key `more_tries` (default off); English `asr.hole` no longer treats `her` as a possessive. Default path unchanged.
- 2026-11-15 Option `cond_extract` (default off, new module `robot_agent/options/cond_extract.py`, hook `on_goals` at order 15 before `cond_goal`, new service `ask_model`: one stoppable, budgeted JSON call to the planner's model; D3 layer L9, switched on for D1 through `HOMELOOP_D1_LAYERS` with `cond_goal` and without its `prompt` key): a request that holds a condition word of its language (language pack `patterns.condition` plus declared `planner.options.cond_extract.clauses`) is split by one extra call into condition / goals-if / goals-else and the `when` field of `cond_goal` is written on the goals; no word, no prompt or an unusable answer: no change, zero or one call; unclear: asked once, never guessed. Off: the default path, unchanged.
- 2026-11-15 Option `replan_guard` (default off, new module `robot_agent/options/replan_guard.py`; new hooks `on_planner_kwargs`, `on_goal_replan`, `on_replan_findings`, service `subgoals`; backend `GraceBackend.replan(subgoals=)`, `replan_block_fix` in the backend whitelist; D3 layer L12, D1 layer `replan_block`): the planner is built with embodiplan's `replan_block_fix` (the failed block is found on the plan's own steps), a goal change mid-run replans for sub-goals rebuilt from the new goals, and a replan's suffix with a declared critical action-model finding (pack data `planner.options.replan_guard.critical`) goes through the one refine and, if it stays, the uncertified end. `_replan_suffix` now returns the suffix, the note and its findings (the repair and the unbindable-step check moved into it). Off: the default path, unchanged.
- 2026-11-15 Option `ask_agree` (default off, new module `robot_agent/options/ask_agree.py`, hooks `on_goals` and `on_issues`, service `unrefused` before `ask_focus`; D3 layer L14): after a bare no and the same request the reading is read back once more as a yes / no (`allow_repeat`); an "I don't know" leaves a reading standing only when it is clean (its leftover words are checked against both `answer_unsure` keys and the declared endings, no robot doubt, no changed value), and an either-or names both readings when one value the person gave was changed; `ask_focus` is not edited.
- 2026-11-15 Option `plan_complete` (default off, new module `robot_agent/options/plan_complete.py`, pack data `planner.options.plan_complete.needs`; D3 layer L13, switched on for D1 through `bench/suites/home/planners/d1_options.py`): a release is spliced before a Close / after an Open of the goal place of the held object, a delivery appended after the final go-home block goes before it (with the move to the person for a hand goal), a speech from across the room gets the move to its listener and the goal's message. Off: the plan as before.
- 2026-11-15 Options `name_recover` and `asr_guard` (default off, new modules `robot_agent/options/name_recover.py` and `asr_guard.py`, new hook `on_heard` called before the reader, pack data `asr` in `lang/en.json` and `lang/ko.json`; D3 layer L11, switched on for D1 through `HOMELOOP_D1_LAYERS`): a garbled object name takes the one near catalogue name (two near names are asked, none changes nothing, a short name is never matched, units are letters or syllable components per pack); an empty request or one with a hole is asked again once without calling the reader; off: default path unchanged.
- 2026-11-15 Option `recipient_keep` (default off, new module `robot_agent/options/recipient_keep.py`, `places.listener_rule` read by the engine's listener rule; D3 layer L10, switched on for D1 through `HOMELOOP_D1_LAYERS`): a delivery's recipient moves to the speaker only when the words hold a first-person recipient word of the language packs; a person named once as a message's listener no longer loses the delivery; off: the old rule, default path unchanged. Also D3: `hw_wait` key `more_tries` (bool, default off; legacy `hw_more_tries`): with it on, the declared `tries` are honoured when the robot stays unhealthy after a wait; off, `hw_wait` behaves as in D2.
- 2026-11-14 Option `cond_goal` (default off, new module `robot_agent/options/cond_goal.py`; new hooks `on_understand_schema` and `on_understand_prompt`, called from `AssistLoop._understand_schema` and the understand prompt, no option = the same bytes): the understand schema may carry an optional goal field `when` {object, at, present} (after `content`); the robot goes to `at`, looks (2 times), keeps the goals whose `present` matches, asks once when unsure, reads an unclear condition as absent. All texts are pack data (`planner.options.cond_goal.*`); no data = the option changes nothing.
- 2026-11-14 Option `dedupe_keep_end` (default off, new module `robot_agent/options/dedupe_keep_end.py`, service `keep_after_dedupe`, `plan_repair.repair(keep_end=)`; D2 round 2, switched on for D1 through `HOMELOOP_D1_LAYERS`): the gate's removal of a repeated fetch no longer cuts the steps after it (the final MoveTo, the way home) when no release follows; off: the old cut, default path unchanged.
- 2026-11-14 Option `entity_cover` (D2 round 2, D1 layer L4): a mention a self-correction takes back (the pack's marker, the last catalogue name before it), a short name inside a longer catalogue name the request says, and another name of a goal's object are no longer asked about as dropped objects (they made the run ask up to 7 times and lose to the baseline).
- 2026-11-14 Fix `plan_repair.op_dedupe` (D2 round 2): a repeated Find / Pick of an object already placed, with no release after it, was cut up to the LAST step of the plan, which removed the final MoveTo (the way home); it now ends at the repeated run's own Find / Pick. A repeat followed by a release is cut as before.
- 2026-11-14 Option `gate_prune` (default off, new module `robot_agent/options/gate_prune.py`, hook `on_plan`; D2 round 2, switched on for D1 through `HOMELOOP_D1_LAYERS`): an Open / Close of a place whose ENV entry declares `openable` false (and no goal names) is dropped before the gate; no field, no role or off: the old plan.
- 2026-11-14 Option `precond_repair` end-of-plan completion (D1 pilot P5): pack rule key `achieves` (on / in / hand); a plan that stops while holding the object of a goal gets the skill that achieves it (a hand-over after the approach, a put-down after a MoveTo), instead of the gate giving up.
- 2026-11-14 Options `goal_words`, `ask_focus`, `entity_cover`, `hw_wait`, `search_budget`, `lang_words` fixed after review (D1 round 1; new modules `core/planning/names.py`, `core/planning/pack_data.py`; service `unsure_leftover`; `lang/en.json` `grammar.joiners`): articles and plurals are read from the language pack (no pack data: nothing stripped or folded); pack data without `source` or `assumed: true` is not used by `hw_wait`, `search_budget` and `lang_words`; `ask_focus` counts an "I don't know" as no refusal only when it says nothing else; `entity_cover` reads the request's active text in object position (no name that sits in a negation, a condition or as a landmark), runs before `ask_focus` (order 75), and `goal_words` tells a place headed like the object by its room or alias.
- 2026-11-14 Option `lang_words` (default off, new module `robot_agent/options/lang_words.py`, new engine module `core/planning/clauses.py`; `understand._active`, `goal_fix.repair_destination` and `goal_filter.negated` take the run's `places.clauses`): the condition and negation words come from the language pack (`patterns.condition` / `negation` of `lang/en.json`, plus the robot pack's `planner.options.lang_words.clauses` for any language) in place of the engine's English regexes; no data or off: the old reading. Switched on for D1 (L8, `HOMELOOP_D1_LAYERS`).
- 2026-11-14 Option `search_budget` (default off, new module `robot_agent/options/search_budget.py`, services `search_limit` and `search_over` called from `AssistLoop._recover_find` and `_search_on` through `_search_total` / `_search_over`): the search budget is per object, the search stops when the remaining share of the belief is under the pack's stop probability, and the person is asked once before the run reports the places looked at and stops; pack data `planner.options.search_budget.budget`. Switched on for D1 (L7, `HOMELOOP_D1_LAYERS`); no data or off: the old behaviour.
- 2026-11-14 Option `hw_wait` (default off, new module `robot_agent/options/hw_wait.py`, services `wait_schedule` and `recovery_rule` called from `AssistLoop._wait_on_robot` and `_apply_policy`): the wait schedule, the number of tries and the first answer after a failure are pack data per failure kind (`planner.options.hw_wait.kinds`); an empty schedule sends no wait command; no data = the old 2+4+8 and the policy table. Switched on for D1 through `HOMELOOP_D1_LAYERS` (layer L6).
- 2026-11-14 Option `hazard` key `receivers` (legacy `hazard_receiver`; default off, no code change, new test file `tests/test_option_hazard_receivers.py` on a made-up pack): a product is refused to a receiver the pack's `enabled_by: hazard.receivers` rule names; switched on for D1 through `HOMELOOP_D1_LAYERS` (layer L5, the layer name `hazard`).
- 2026-11-14 Option `ask_focus` (default off, hook `on_issues`, services `confirm_limit` and `unrefused`; `understand` knows the issue kind `choice`): purposeful questions: no confirmation of a noun that is a place or a word of a goal's other fields, "which one?" when two or more objects of the world fit a description, an "I don't know" to the read-back is no refusal, read-back rounds bounded by `max_confirms`; switched on for D1 through `HOMELOOP_D1_LAYERS` (layer L3).
- 2026-11-14 Option `entity_cover` (default off, hook `on_issues`, new service `request_english`): every object noun of the request (names of the object catalogue, whole words) is a goal or a stated reason for dropping it, a dropped one is asked about once instead of a silent done; switched on for D1 through `HOMELOOP_D1_LAYERS` (layer L4).
- 2026-11-14 Option `goal_words` (default off, service `goal_guard`, hook `on_goals`; `goal_fix.drop_invented`, `repair_destination`, `shared_listener` read an optional `places.guard`): the goal filter compares whole words, trusts the object catalogue first, asks when a name is both an object and a place and keeps the receiver of a shared object; switched on for D1 through `HOMELOOP_D1_LAYERS`.
- 2026-11-14 Option `precond_repair` (default off, hook `on_plan`, new action role `pick`, pack data `planner.options.precond_repair.needs`): a plan that lacks a step its next skill needs (a held object, a free hand, being at the object) gets it before the gate; switched on for D1 through `HOMELOOP_D1_LAYERS`.

- Option `object_words` (default off): the run's placed / handed-over tables are looked up by the object's words ('phone' = the plan's 'cell phone'); without it a done plan was believed unmet and the run failed with every goal true.

- Option `end_state` (default off; pack data `planner.end_state`, new service `run_step`): a `go` goal to a place the pack names as an end state is made true (go there, run the pack's action) before done.

- Option `reopen_on_miss` (default off; pack data `planner.recovery.reopen_on_miss`): a Find that misses right after an Open is retried once after the pack's reopen remedy.

- Option `place_find` (default off, hook `on_plan`, new action role `find`): a Find of a place the robot is at or moves to is dropped from the plan.

- Option `search_per_object` (default off): the `search_total` budget is counted per object, not for the whole run (service `looks_made`).

### Round 19 (modular core): options are registered modules (MINOR: new manifest key `planner.options`, new modules; no behaviour change)
- New engine package `core/options` (spec, config, registry, dispatch, services) and option layer `robot_agent/options`: `hazard`,
  `reach_delivery` (was `core/planning/reach_delivery.py`) and `unapplied_change` left `assist_loop.py` for hooks (`on_goals`, `on_step`,
  `on_final`, ... 13 kinds, docs/options.md). Their old names keep working: run options `hazard_rules`, `hazard_receiver`, `reach_delivery`,
  `unapplied_change`, `$ROBOT_AGENT_*`, manifest `planner.<name>`, and `AssistLoop.cfg[<name>]`; the canonical names are `<option>.<key>` and
  `planner.options.<option>.<key>` (new). `ASSIST_OPTIONS` is the literal engine names plus the registry's. Third-party options: entry point group
  `robot_agent.options`.
- `AssistLoop`: `_hazard_gate`, `_verdict`, `_reach_step`, `_reach_ask_where`, `_reach_cfg`, `_refusal_record`, `_support_goals` moved to the
  options (`options.hazard.support_goals` is the function the tests import); `_forbids` asks the registry. `loop._NEXT/_STOP` are
  `core.options.NEXT/STOP`. Run state of an option is `run.opt["<name>"]`.
- Enforced structure: `tools/lint_arch.py` (import layers with relative imports resolved, size limits, option wiring ratchet; baseline
  `tools/arch_baseline.json`), `tests/test_architecture.py`, `make lint-core` runs it; CLAUDE.md rules 13-15; docs/architecture.md, docs/options.md.
- Pins: `--llm fake` 12 dev tasks EN/KO x 6 sage variants and the offline replay of the r17 fresh / fresh_asr rep1 caches (s8n, s9, EN/KO): 942
  episodes byte-identical to r19a; assist_loop.py 4342 -> 4210 lines, option keys read 70 -> 66, branches on `self.cfg` 77 -> 67.

### Round 19: what is hazardous is the robot's own safety profile, not the core's (MINOR for packs that declare one; behaviour change for a pack that relied on the implicit household rules)
- New manifest key `safety.profile` (path relative to the manifest, or an inline mapping), validated by field path in `contract/manifest.py`
  (`core/safety/profile.py`); `Manifest.safety_profile`; `check_manifest` prints its counts and `--strict` fails on `status: verify`. Schema: docs/safety.md.
- `core/safety` is the engine only: the household hazard tables are gone (`HAZARD_RULES`, `RECEIVER_RULES`, `HAZARDS()`, `CLASSES()`, the hard-coded
  class names), the pack keys `hazard_cleaning_*`, `place_food_*`, `person_vulnerable`, `refusal_safety`, `refusal_capability` left `lang/{en,ko,vi}.json`
  (the vi pack therefore has no hazard words; a Vietnamese profile is a robot pack's data). A robot without a profile gets no hazard refusal.
  API: `check_goal(goal, places, profile, pack_rules, mode, enabled)`, `find_rule` -> `Verdict(say, rule)`, `place_class` / `receiver_class` /
  `hazard_class` take the profile, `typed_refusal(..., marks=profile)`. Option keys: `hazard_receiver` turns on rules with `enabled_by: hazard.receivers`;
  `hazard_rules` core (alias profile) | pack | off unchanged.
- The `done` event gains `refusals` ([{kind, rule, why, goal}], every final status, absent when nothing was refused). Nothing the loop does or says changes.
- `AssistLoop._verdict` / `_forbids` replace the four copies of the hazard lookup.
- Pins: golden decision table of the r17 tables reproduced by the household profile (28k decisions); `--llm fake` 12 dev tasks EN/KO x 6 sage variants
  and the offline replay of r17 fresh / fresh_asr rep1 (s8n, s9, EN/KO, 798 episodes) are byte-identical to r17 with the same data in the pack.
- Packs that rely on the implicit household rules and declare no profile (manifest scan of the sibling packs: kcare_robot, base_robot; homeloop_robot gets its profile in its own repo) lose them. base_robot: it loses the implicit food-place rule (it cannot put anything into a fridge). No recorded base_robot result changes.

### Round 17: option `hazard_receiver` (default off, defaults byte-identical)
- `core/safety/receiver_class.py`, `hazards.RECEIVER_RULES`, `check_goal(..., receivers=)`: a hazardous product given to a person entry of a vulnerable class is refused (partial refusals are said at the end). Words: new pack key `patterns.person_vulnerable` in `en`, `ko`, `vi`. Tests: `tests/test_sage_lab_r17_hazard_receiver.py`.

### Round 16: language facts are pack data (defaults byte-identical for English and Korean)
- The language literals of the always-on core moved into the language packs (`lang/<code>.json`, new blocks `patterns`,
  `grammar`, `dialogue`, `guard`, keys `detect_marks`, `latin_script`, `object_aliases`): particles, into / on-top / from markers,
  the verbs "open" / "close", the self-correction markers (`understand._CORR` is now `understand.default_corr()`), what a refusal
  reason names, yes / no / fillers / control words (stop, pause, resume) / negations / fallback keywords, the script guard of a
  translation (`speech.guard`), the speaker's words, hazard and food-place words, the object particle, the Whisper silence
  phrases. A language with no pack gets none of them (fail open). `tools/lang_inventory.py` lists what is still in code.
- New manifest keys `planner.prompt_lang` (the language of the core's prompts, default English; `langdata.set_prompt_lang`) and
  `planner.restates` (`frac`, `word_len`, `min_words` of "the answer says the request again", defaults 0.6 / 3 / 4).
- Vietnamese ships as one data file (`lang/vi.json`), the third language of the round; drafted by the model, to be checked by a
  native speaker.

### Round 16: the numbers and action names of the round 12-15 options are pack data (opt-in options stay default off, defaults byte-identical)
- `planner.action_roles` ({put_down, move, open, close}, new module `action_roles.py`): `reach_delivery` and `relook` no longer name
  `Place`, `MoveTo`, `Open` or `Close`; a role not declared is no role and the part that needs it does nothing.
- `planner.agree_guard` also holds `particle_chain` and `tail_chars` (were `{1,2}` and `min(3, len)` in `dialog_ext.py`); the share of
  own-script letters is the pack's `script.min_share` only (the code default 0.7 is gone).
- `planner.coverage_limits` ({max_parts, max_steps}) replaces the constants of `request_coverage.py`.
- `contract/manifest.py` validates `agree_guard`, `person_snapshot`, `action_roles`, `coverage_limits` and `room_names` (known keys, ranges).
- docs/env.md: limitations that depend on simulator conventions (wait schedule, independent redraw of detector misses, stale person snapshot).

### Round 15: reach delivery (opt-in, default off, defaults byte-identical)
- `reach_delivery` with the pack data `planner.person_snapshot.trust_s`: a person entry of the ENV is a snapshot; a put-down for a
  person run after `trust_s` seconds becomes the robot's hand-over action, a hand-over that cannot reach the person asks where they
  are and goes there, a position without a declared surface gets a hand-over, the goal check reads the person's entry again, and the
  gate's completion no longer walks back to the source of an object the plan holds. New module `reach_delivery.py`; `AssistLoop.clock`
  (a host clock, default wall time).

### Round 14: dialogue data and agreement guard, relook (opt-in, default off, defaults byte-identical)
- `dialog_agree2` (includes `dialog_agree`): the Korean dialogue facts are language-pack data (`lang/*.json` block `dialogue_ext`:
  polite endings, self-correction markers, not-sure phrases, room names; robot pack `planner.room_names`); a guard
  (`planner.agree_guard.garble_ratio`) stops `dialog_agree` from confirming through garbled names, an object alone or words out of
  the script, and asks the person once more instead. New module `dialog_ext.py`.
- `relook`: a place the person named / memory holds / the last likely one gets one more look by another sense (close and reopen,
  the declared wait, or a plain second look) before it counts as searched.

### Lab round 8: a model per role (all opt-in, default off, defaults byte-identical)
- `repair_model` / `repair_url` / `repair_max_tokens`: another model answers the plan gate's repair prompts (sub-goal edit and the
  one refinement); a runaway falls back to the planner's model once (`planner.chat_override`, implemented by `GraceBackend`).
- `dialog_model` / `dialog_model_lang` / `dialog_url` / `dialog_max_tokens`: another model writes what the robot says in the
  user's language (per language, as data); a guard failure falls back to the translator once.
- `understand_think` / `repair_think` / `dialog_think`: Ollama `think` for the role client (`LLamaClient(think=)`, sent only when set).
- `request_coverage`: one LLM call before the run says done; a named part of the request with no goal or no executed step is
  asked about (yes = added as a goal and carried on). No word lists.

### Lab round 7: understanding recall (all opt-in, default off, defaults byte-identical)
- `reask_anchor`: the abstention re-reading is taken only when every place in it is in the person's words (ENV aliases, any language).
- `no_shared_listener`: switches the `shared_listener` rule off.
- `understand_views`: original and translator pivot read separately; agree = act, differ = one "which one?" question.
- `understand_goals_first`: schema key order, goals before the verdict.
- `understand_model` / `understand_url` / `understand_model_lang`: another model (per language, as data) reads the request; the
  runaway guard `understand_max_tokens` / `understand_max_goals` falls back to the planner (event `understand_fallback`, no
  quoting repair). Flags `understand_schema_strict`, `understand_give_kind`, `understand_verify_verdict`. All off / empty = unchanged.

### Generalisation: language and robot facts are data (lab round 6)
- Language packs `core/planning/lang/<code>.json` (`langdata.py`) hold the word lists (`lexicon` tables, `open_words`, the
  model-question patterns of `reask_abstain`), `answer_unsure`, articles, plural rules, translate names, script data. English
  and Korean ship; a language without a pack is not guessed at (the flag does nothing for it). Vietnamese is removed everywhere
  (core lists, router, announcer, TTS, tests, docs).
- Manifest numbers: `planner.unstick.*` (dialog_unstick), `planner.recovery.return_tries` (endstate_check; none declared = the
  option does nothing). `planner.kind_anchor_ask` (default 0): an unasked open goal is kept and read back as a doubt.
- Defaults unchanged: a replay of cached EN and KO episodes (flags off and on) is byte-identical to the previous head.

### Audit fixes (lab options; defaults unchanged)
- `kind_anchor` reads the open words of the run language (and the pack's `open_words` for any language); no data for a language = no drop.
- `reset_container` needs the pack's `planner.recovery.blocked_putin` (no declaration = no-op), every spliced action in `plan_actions`, no put-down with a free hand. `endstate_check` re-runs the action that achieved the go goal; `planner.recovery.return_tries`.
- `dialog_unstick`: a restatement is read back again only when it adds an ENV entity; the structural not-sure fallback is for languages without data only.
- `inventory_ask` declares `{intent, options, about}`; `options` list slot with `sep_or`. Round-3 language-layer fixes are gated on `dialog_unstick` / `inventory_ask`.

### Inventory ask counts instances (lab round 3)
- `canon_inventory` collapses the memory names of one remembered instance (same record `instance` / `id` / `sighting`, or related by the pack's i18n key `object_aliases`, `{lang: ["mug|coffee mug"]}`) into one candidate and lists one name per instance; the which-question declares its kind (`speech.LocalText.ask`, `speech.declared_ask`: `qtype: which_object`, options). Tests: `tests/test_sage_lab_r5_inventory_instances.py`.

### Language layer: a question keeps its kind (lab round 3)
- The last resort of `LanguageAdapter` for a read-back (`q_confirm`) is never the "say the task in other words" question; a lead part of a joined text that cannot be said is left out instead of becoming that question. Tests: `tests/test_sage_lab_r5_restated_readback.py`.

### Dialogue unstick (option `dialog_unstick`; env `ROBOT_AGENT_DIALOG_UNSTICK`, manifest `planner.dialog_unstick`; default off)
- A restatement that reproduces the refused reading is read back once more (a lead phrase `rb_restated` before the ordinary `q_confirm` read-back, so a pack that has `q_confirm` says the summary without a translator call) instead of ending the run; a not-sure answer to a where-is question makes the robot search (one more understand call with its Find stated; "not sure" = core English phrases + pack i18n `answer_unsure` + a structural fallback); a question with its own apology is not prefixed with the silence apology when asked again. Helpers: `core/planning/dialog_unstick.py`, `speech.calm`. Tests: `tests/test_sage_lab_r4_unstick.py`.

### Lab log (`AssistLoop.lab_log`)
- Each opt-in lab option (go_steps, kind_anchor, name_canon / inventory_ask, reask_abstain, gate_strict, endstate_check, reset_container, wait_on_robot) appends `{"flag": name, ...}` to `loop.lab_log` when it acts in a run (reset at the start of `run_blocking`). Not part of the event stream; options off never write to it. Tests: `tests/test_sage_lab_log.py`, `tests/test_sage_lab_log_ground.py`.

### Execution integrity and inventory ask (options `exec_integrity` family = `gate_strict`, `endstate_check`, `reset_container`, `wait_on_robot`; and `inventory_ask`; env `ROBOT_AGENT_<NAME>`, manifest `planner.<name>`; all default off)
- `gate_strict`: the gate's symbolic repair is repeated with a per-goal score only when it left a critical finding, and kept only if it certifies; plan objects bind to the one goal object whose word tokens strictly contain theirs (`planner.label_tokens: none` = exact match). `endstate_check`: the check tour's return to the last `go` goal is a retried, checked step. `reset_container`: a BLOCKED PutIn into a believed-open container puts the object down at its pick place, Close, Open, fetch, PutIn (once per container). `wait_on_robot`: WAIT_HEALTH runs the pack's `planner.recovery.wait: {action, schedule_s}`; no declaration = the old poll. Helpers: `core/planning/exec_integrity.py`.
- `inventory_ask`: `name_canon` made general (`goal_fix.canon_inventory`): `planner.canonical_lang` (`en` default, `none` skips, other codes use the pack's i18n `plural_fold` `suffix>replacement|...`), the user's own words narrow the remembered instances first, otherwise ONE closed which-question (also when the instances share a place).
- New manifest planner keys: `canonical_lang`, `label_tokens`, `recovery`. Off: byte-identical events (`tests/test_sage_lab_r3.py`).

### Grounding chain v2 and abstention re-ask (options `go_steps`, `kind_anchor`, `name_canon`, `reask_abstain`; env `ROBOT_AGENT_<NAME>`, manifest `planner.<name>`; all default off)
- `go_steps`, `kind_anchor`, `name_canon`: three deterministic steps in `_ground_understood` after `shared_listener`
  (`goal_fix.go_steps`, `kind_anchor_open`, `canon_names`; `lexicon`-style `OPEN_WORDS`). `reask_abstain`: one directed
  re-understand of a goal-less "where is it" reading (`goal_fix.abstains_on_location`, `AssistLoop._reask_abstention`).
  New phrase `q_which_known`. Off: byte-identical events (tests `tests/test_sage_lab_r2_grounding.py`).

### Accept goals from the caller (option `accept_goals`, env `ROBOT_AGENT_ACCEPT_GOALS`, manifest `planner.accept_goals`; default off)
- With the option on, `AssistLoop.run_blocking(task, lang, goals=[...])` skips the understand step and starts from the
  goals it is given (`{type, object, from, to}`), grounded on the ENV and not filtered. An `understand` event with the
  reason `goals given by the caller` is emitted. No goals given, or the option off: the usual understanding, byte-identical
  events (test `tests/test_accept_goals.py`).

### Person goals (option `person_goals`, env `ROBOT_AGENT_PERSON_GOALS`, manifest `planner.person_goals`; default off)
- A mixed request (go / say goals and moves) gives the planner the template goals as fixed sub-goals with the
  loop's own steps (`goals.compile_steps`); the planner expands the moves only (embodiplan 0.2.7
  `fixed_subgoals`). New gate operators `recipient` (a hand-over for a hand goal goes to the goal's person, after
  a MoveTo) and `unopenable` (an Open / Close of what the map says cannot be opened, named by no goal, goes).
  `goals.dedupe` drops an exact repeat of a goal. A fixed block gets no LLM edit and is never emptied. Off: nothing
  changes.

### Direct planner steps run in the closed loops (docs/direct_prompt.md "Steps and binding")
- `llm_direct` steps keep the robot's call (`skill`, `params` read as Structured mode reads them, `flags`)
  and carry the skill's manifest action (`move` -> `MoveTo`; `""` for a skill with no plan action). Binding
  honours the call in one place, `core/planning/concrete.py` (`RobotView.bind`, `ActionMapper.to_skill`),
  after checking the skill exists (`RobotView.registry`: plan skills, aliases); grace / SAGE steps bind
  as before. Fixes ClosedLoop with planner `direct` (and SAGE with generator `llm_direct`) failing every
  step with "no skill for action 'move'". `ActionMapper(namemap, registry=None)`; `RobotView.knows_skill`.

### Direct planner prompt (docs/direct_prompt.md) — Planner Guide removed
- The direct planner (`llm_direct`, dashboard `direct`, and the open-loop path of `UnifiedAgent.run`) builds
  its prompt by code each time it plans (`core/planning/direct_prompt.py`): `describe_robot`, the skill
  lines, the dashboard's plan skills, the ENV places, manifest `planner.notes`, the k most similar
  examples (seed pool + verified live memory), fixed answer rules, the command. Both callers ask through
  `direct_prompt.plan_answer` (`llm.chat(prompt=...)`, recorded / replayed as `chat`).
- Manifest: optional `planner.notes` (list of non-empty strings, <= 300 characters each).
- New `GET /planner/prompt?command=&planner=&k=&leave_one_out=` -> `{prompt, examples, planner, uses_prompt}`.
- Env: `ROBOT_AGENT_DIRECT_EXAMPLES` (default 3), `ROBOT_AGENT_DIRECT_LOO`; the old names
  `ROBOT_AGENT_GUIDE_EXAMPLES` / `ROBOT_AGENT_GUIDE_LOO` are still read when the new ones are unset.
- Removed: `core/guide_manager.py` (`GuideManager`, `resolve_guide`, `render_guide`, `preview_guide`,
  `reconstruct_plan`, `GUIDE_USERS`, `planner_uses_guide`), `api/guides.py` (`GET/POST /guides`,
  `GET/PUT/DELETE /guides/{name}`, `POST /guides/{name}/activate|rename`, `POST /guides/preview`),
  `AgentState.guides`, the structured (`format`) branch of the direct planner and the
  `LOCATIONS_HERE` / `PLAN_SKILLS_HERE` / `{ROBOT}` / `{ACTIONS}` / `{EXAMPLES}` placeholders. A plug-in
  says `uses_direct_prompt = True` (was `uses_guide`). An old `common/guides.json` is never read.

### Contract
- Optional adapter members `pose(node) -> {x, y, yaw?, ts? | age_s?}` and `container_states(node) ->
  {key: bool | None}`, read by the SAGE-v2 reconcile (`core/planning/reconcile.py`, docs/guard.md).
  `RobotView.reconcile(..., places=, arrive_tol=, report=)`. `WorldState.provenance` (`mark`,
  `mark_edited`, `provenance_of`, `provenance_view`); `to_dict()` gains a `provenance` key (others
  unchanged); `PUT /agent/world` marks the edited fields `operator`. AssistLoop option `reconcile`
  (`ROBOT_AGENT_RECONCILE`, default 1).
- Manifest: optional top-level `description` (`Manifest.description`) and skill-level
  `description` (string, no check_manifest warning): the robot's / skill's text in the planner
  prompts (core/planning/robot_prompt.py, docs/direct_prompt.md). Old manifests load unchanged.

- `FailureCode.BUDGET_EXCEEDED` (`budget_exceeded`): the run used its LLM-call budget (recovery STOP,
  not retried). Appended after `unknown`.
- Manifest `verify: [{kind: vlm, prompt}]` is now run (`core/planning/verify_vlm.py`) through the
  adapter's optional `vlm_ask(prompt, step=, node=)`; `capabilities.vlm_verify: false` keeps it off
  (`ROBOT_AGENT_VLM_VERIFY` / the `vlm_verify` option win). `NamemapAdapter.vlm_ask` passes through to
  the namemap's.
- `RobotAdapter.health()` keys the core reads: `frame_age_s`, `fps`, `depth`, `pose_age_s`, `ok`;
  limits in manifest `geometry`: `frame_max_age_s`, `min_fps`, `need_depth`, `pose_max_age_s`,
  `vlm_min_conf` (`core/planning/sensor_health.py`, `ROBOT_AGENT_SENSOR_HEALTH`). docs/verify.md.
- `ROBOT_AGENT_LLM_BUDGET` default 20 (was 40); plan edits and the gate's refinement count too
  (`subgoal_repair.repair_plan(..., budget=)`); the edit rounds stop when the findings do not decrease. i18n key `llm_budget`.
- `connect.ros.node.NodeAgent.rev_time` / `rev_period`: receive time and smoothed gap of a subscription.
- Manifest: optional `planner:` section, `planner: {seed_memory: <JSONL, relative to the
  manifest>}` (`Manifest.planner`, `Manifest.seed_memory`, `Manifest.planner_path(key)`).
  Absent = no seed pool; old manifests load unchanged. `check_manifest` errors when the seed
  file is missing or a line is not an episode, and warns on an unknown `planner.*` key.
- `VerifySpec.expect` (manifest `verify: [{kind: sensor, skill: ..., expect: false}]`):
  what the sensor's `isdone` must be; default true. Unknown before; old manifests
  load unchanged.
- `NamemapAdapter.to_loc` / `to_obj`: the robot's names, passed through from the namemap.
- Adapter extras read by the core (optional): `is_openable(key, entry)`, `quiet_skills`,
  `i18n.phrases` keys `verb.<Action>`, `robot_description`, `verify_failed.<Action>`.

### Planner prompts (docs/direct_prompt.md)
- SAGE (`grace` / `sage`) gets the robot's plan actions and description from the manifest
  (embodiplan >= 0.2.4 `SAGEPlanner(actions, robot_description)`); `ROBOT_AGENT_SAGE_ROBOT_PROMPT=0`
  restores embodiplan's household prompts. Plug-in planners get `robot_description` too.
- (removed again, see above) Planner Guide placeholders `{ROBOT}`, `{ACTIONS}`, `{EXAMPLES}` (seed pool + verified live memory,
  top `ROBOT_AGENT_GUIDE_EXAMPLES` (3), leave-one-out with `ROBOT_AGENT_GUIDE_LOO=1`); a guide
  without them is sent unchanged. New seed versions (`guide_v2`) are added once, inactive, to an
  existing guides.json (`seeded` list).
- (removed again) `GET /guides?planner=` adds `used_by`, `active_planner`, `active_planner_uses_guide` and per-version
  `placeholders`; `POST /guides/preview` renders a guide for a command.

### Interaction (run manager, dialog)
- MODIFY / ADD / DROP / NEW said during a run are applied by AssistLoop at the next step boundary
  (`core/planning/goal_change.py`, `AssistLoop._apply_pending`); a change it cannot read is told to the
  person (`goal_change` event, `applied: false`). ClosedLoop says it cannot apply them.
- `TaskStack` / `TaskFrame` implemented (was a Protocol stub); `RunManager.suspend` / `resume_task`,
  `RunManager.on_resume` hooks (after a pause ends or a suspended task is taken back);
  `Run.outranked_by(role)`. New events: `goal_change`, `task_suspended`, `task_resumed`,
  `run_stop_deferred`.
- `interact/dialog.py`: the robot's questions are registered on the run (`Run.set_awaiting`), so an
  answer typed by any client is an ANSWER; `Run.poll_answer`. `run_control.ask_person`, `yes_no`.
- A stop during a manifest `interruptible: false` skill waits for it to end (`run_control.uninterruptible`,
  `run_skill(..., interruptible=)`, `RobotView.interruptible(skill)`, `Run.begin_critical` /
  `end_critical`); dashboard Stop and e-stop unchanged.
- `run_control.call_cancellable` / `is_cancel`: LLM and planner calls no longer delay a stop.

### Core
- Planner memory (docs/memory.md): runs are recorded into SAGE's live memory only when their
  goals were seen to hold (`core/planning/episode_memory.py`; `ROBOT_AGENT_MEMORY_RECORD` /
  option `memory_record` = `observed` (default) | `belief` (old rule) | `off`). The plain
  ClosedLoop no longer records by default. The record carries `evidence` and `run_id`
  (`GraceBackend.record_episode(task, steps, meta=None)` returns whether SAGE recorded it).
  `GraceBackend(seed_memory=)` / `RobotView.seed_memory()`: the manifest seed pool is SAGE's
  seed; `leave_one_out` is passed through.
- Executive (fix stream `execute`, D5 D7 D8 D12 D13 D17 D25 D26):
  - AssistLoop is done only when every on / in goal is looked at (`goal_check` event: MoveTo back
    + Find at the destination; a goal inside a closed drawer stays on the belief as `ok: null`).
    A goal seen not to hold goes to SAGE once more, else the run fails (code `dropped` / `absent`).
    `ROBOT_AGENT_GOAL_CHECK=belief` (option `goal_check`) restores the old belief-only check.
  - The recovery policy is applied by FailureCode: WAIT_HEALTH (`adapter.health()`, wait
    `health_wait_s`, the same step again in place), HEALTH_CHECK (an unhealthy robot stops),
    STOP once a rule's retries are used up (camera down 3 times, a second hardware fault).
  - `RobotView.reconcile(world, node, sense)`: `adapter.reconcile` + the declared holding sensor;
    called at run start (both loops) and after a pause (`run_control.pause_point`); a `reconcile`
    event when the belief changed. After a pause a Pick on an old Find looks again first.
  - A failed run's `done` event (both loops) carries `code`, `device`, `evidence` (AssistLoop: the
    first failure not recovered since; ClosedLoop also `msg`, `failed_step`); `TaskRecord.failure`;
    RunRecord metrics `failure_code` / `failure_device`.
  - One run id: the loops take the RunManager run's `run_id` (`task_run_id` in metrics is then
    None); `/ws/agent` finishes a run from `done.status` (failed / refused -> RunState failed).
  - Classifier: "no longer in the gripper" = dropped, "stuck" / "jammed" = blocked.
- The planning loops reach the robot only through `contract.load_adapter`
  (`core/planning/robot.py` `RobotView`); `make lint-core` is blocking.
- Post-step verify: effects + the manifest's VerifySpecs, no precondition re-check; a skill
  without a sensor gets an UNKNOWN layer (`VerifyResult.unknown`, new field).
- `step_verify` layers: `sensor` / `localization` from the manifest's sensors (was `in_hand`
  / `localization` in AssistLoop only).
- Failures: one normalisation (`run_skill`), codes from `SkillResult` /
  `classify_legacy_failure`, stop / searched decisions from the recovery policy.
- The announcer's Korean phrasebook moved to the robot (kcare_robot `configs/adapter.py`).
