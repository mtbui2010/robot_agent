# bt_goal: each goal run by a small declared system, a behaviour tree given as data (default off)

**Idea.** The reader (LLM) makes the goals; when EVERY goal has a usable tree for its kind, the trees run the goals and the planner is not
called. All or nothing: one goal without a tree (a kind with none, a `skip_if` field set, a slot not filled, an action or Ask skill the robot
lacks, a phrase its language lacks) and SAGE plans the whole run, as when off.

**Mixed (key `mixed`, default off).** All or nothing is the default. With `mixed` on, goals of a kind WITHOUT a tree no longer make SAGE plan the
whole run: the goals go in the reader's order. A run of neighbouring goals WITH trees is ticked as above. A run of neighbouring goals WITHOUT a
tree is planned by the SAGE planner ONCE, when the run reaches it, from the world as it is then (so it sees what the trees before it did; core
service `plan_goals(run, goals)`: the planner's steps for those goals alone, grounded and tidied), and its steps run one by one through
`try_step(..., ladder=True)` (the engine's own step path and ladder, the planner rung held back). A step with no way left hands the goals LEFT
(this part and every goal after it, trees included) to the SAGE replan once (`_to_sage`). Calls: one planner call per run of neighbouring
goals without a tree (none when every goal has a tree). No goal with a tree: SAGE plans the run, byte for byte as when off. **Not covered:**
the plan of a SAGE part skips the loop's certify gate (`_check`, the repair), only the planner's own verifier runs; a goal change said while
a part runs ends it like a tree (the engine applies the change).

**Runner** (`core/planning/behavior_tree.py`, pure, no clock): a node is `[type, *args]`: `sequence`, `fallback`, `retry n child`, the leaves
`cond name *slots` (reads the belief: `holding`, `hand_free`, `at`, `placed`, `opened`, `closed`), `act Action slot` (one robot step), `ask key
refs`, `confirm key`, `reply key` (pack phrases). Slots `{object}`, `{from}`, `{to}` come from the goal (`{from}` empty: the engine's trusted
object memory). After each node the runner tells the host (`note`): the option logs it, so a run can be followed node by node per goal.

**Subgoal contract.** Entry: the tree's first child is the condition "already achieved?" (a goal that holds does nothing). Exit on success:
the world matches the goal and the hand is empty (the tree's last conditions). Exit on failure: a safe state through the engine's own paths
only (its ladder's stop says what is in the hand; a policy stop while holding says so with the pack phrase `say_bt_goal_stop_held`).

**Act = the engine's own step AND its own recovery.** `try_step(run, step, ladder=True)` runs the step through `_run_step`; a failure gets the
engine's ladder from the declared table (`contract.recovery`, the robot's `recovery_overrides`, option `hw_wait`): retry in place, wait
schedule, search legs, refind, the ASK_HELP rung (option `ask_next`'s menu when on); the steps the ladder splices in run before the Act
returns. A no-retry code (`retry_allowed` false: estop, cancelled, software, budget_exceeded) ends the run at once (no ask, no replan). The
ladder's own stop (asked and no help, a wait schedule used up, an unknown object in the hand) ends the run as without trees. Only the
ladder's PLANNER rung is held back: service `no_replan` (answered while a tree runs) makes the engine mark the step failed without a planner
call; the Act fails and the tree's Fallback / Retry go on.

**When the tree has no way left** (key `exhausted`, pure `exhausted_action(rule, policy, asked)` on the failed code's rule of the same table):
- `ask_or_stop` (default, policy A): a code a person can help with (its rule is not a robot-health rule: absent, unreachable, blocked,
  grasp_failed, dropped, unknown) is asked once per goal with the menu phrase of the failed action (key `menu`, e.g. ask_next's
  `q_ask_next_find` / `_pick` / `_move`); an answer with a `menu.retry` word or a place ticks the goal's tree once more (a named place is
  remembered as said, the tree is filled again from the memory; the goal's own destination named is no place of the object: not understood); else, and for a health code (hw_fault, timeout, perception_unavailable after
  the wait schedule), report and stop (`done failed` with the code; no question).
- `sage_once` (policy B): where A stops (not after a person's stop word), the REMAINING goals and the facts (code, step, reason, what was done)
  go to the SAGE planner once (`replan_or_fail`); its plan then runs on the engine's normal path; no plan from it: the run stops.

**Data.** `planner.options.bt_goal`: `trees` `{<goal type>: {"tree": [...], "skip_if": [goal fields], "source" | "assumed"}}` or a LIST of such
entries (the first one the goal fills and the robot can run: a `say` with a place, then one without; a `deliver` with the robot's `{delivery}`
phrase, then one without, as `goals.compile_steps`; an entry with `"needs_openable": [slots]` only when each slot names a place the robot
declares openable, i.e. an ENV entry with an `openable` field the robot's rule (`Places.openable`) says yes to; a surface, no field, not an
ENV key: the next entry. E.g. `Open {from}` behind `cond opened {from}` before the Find, then the tree without it); `menu` `{"phrases": {<action>: <phrase key>}, "retry": [<pack list refs>], "source" |
"assumed"}` (none: no question, A stops); `exhausted` (`ask_or_stop` | `sage_once`). Trees are pack data of a robot (the bench declares `on`,
`in`, `open`, `close`, `go`, `say` from its mock robot's skills); a `say` / `deliver` tree is a data view of the engine's template (same robot
calls when nothing fails) and has no entry condition (no belief predicate reads "said" / "handed over": the engine's end check does).

**Engine touch (core).** Service `runs_goals` (asked once in `_plan_phase`), hook `on_run_goals` (start of `_execute_phase`), service `try_step`
(key `ladder`: drain the ladder's spliced steps), `changes_waiting` and service `no_replan` (asked at the top of `_sage_replan`; nobody answers it by default).

**Switch off:** key `enabled` (run option `bt_goal`), default off. **Event:** lab flag `bt_goal`: `{goal, status}` ("claimed" once, per goal its
end, `exhausted` with the action taken) and `{goal, node, status}` per node.
**Goal changes.** A correction said while a tree runs ends the trees before the next Act (status `changed`, service `changes_waiting`); the
engine applies it at its step boundary as for a plan (its repair, a SAGE replan for the new goals).
**Limits.** Goals run in the reader's order; the menu answer is read with the pack's words only (a person who does not understand a menu
question is a stop under A).
