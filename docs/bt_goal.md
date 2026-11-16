# bt_goal: a goal run by a behaviour tree given as data (prototype, default off)

**Idea.** The reader (LLM) makes the goals; a goal whose kind has a declared tree is run by that tree instead of a SAGE plan: no planner call
for it, and its failures are handled by the tree's own Fallback / Retry / Ask, not by the engine's recovery ladder.

**Runner** (`core/planning/behavior_tree.py`, pure, no clock): a node is a list `[type, *args]`. `sequence` (stops at the first child that does
not succeed), `fallback` (tries the next child after a failure), `retry n child` (up to n ticks of the child), `cond name *slots` (reads the
belief, never acts: `holding`, `hand_free`, `at`, `placed`), `act Action slot` (one robot step), `ask key refs` (the pack phrase `key`; success
when the answer holds a word of the pack lists `refs`, e.g. `ask_next.retry`, `dialogue.yes`), `confirm key` (a yes / no, the engine's own
reading), `reply key` (a pack phrase said in the person's language). Slots `{object}`, `{from}`, `{to}` come from the goal (`{from}` empty: the engine's trusted object memory, else no tree). A tick is one pass
through the tree: `success` / `failure`; `running` exists but is never returned, because the robot's skills block (one call = one result);
`stopped` ends the tick at once (the run was cancelled or ended).

**Data.** `planner.options.bt_goal.trees`: `{<goal type>: {"tree": [...], "skip_if": [goal fields], "source" | "assumed"}}`. Phrases are
language-pack data (`speech` block, labelled): the Ask fallback reuses `q_ask_next_pick` of option `ask_next`; the reply is `say_bt_goal_done`.

**When it acts (fail open).** Only when EVERY goal of the run has a labelled tree for its type, no `skip_if` field set, every slot filled, every
`act` action declared by the robot and an Ask skill for `ask` / `confirm`; else nothing happens and SAGE plans as when off (empty data = off).

**Engine touch (core).** Service `runs_goals(run)` asked once in `_plan_phase` (yes: the planner is not called, the plan is empty); hook
`on_run_goals(run)` (decide, flow) at the start of `_execute_phase`; service `try_step(run, st)` runs one step through the engine's own path
(`on_step` options, guard, bind, skill, verify, records, events) and appends it to the plan. While a tree runs, the option answers `on_step_failed`
(the failure is the tree's). After the trees the engine's plan end runs as always: the goals are looked at; a goal that does not hold goes to
the SAGE replan (the LLM takes over from the current belief). Corrections said meanwhile are read after the trees end.

**Switch off:** key `enabled` (run option `bt_goal`), default off. **Event:** lab flag `bt_goal` `{goal, status}`: "claimed" once, then per goal.
**Limits.** No search node (a Find miss fails the tree, SAGE continues), no container tree yet (`in`), goals run in the reader's order only.
