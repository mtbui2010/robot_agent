# bt_goal: each goal run by a small declared system, a behaviour tree given as data (default ON; a robot with no declared trees is unchanged)

**Idea.** The reader (LLM) makes the goals; with `mixed` off (see Mixed below), when EVERY goal has a usable tree for its kind, the trees run the goals and the planner is not
called. All or nothing: one goal without a tree (a kind with none, a `skip_if` field set, a slot not filled, an action or Ask skill the robot
lacks, a phrase its language lacks) and SAGE plans the whole run, as when off.

**Mixed (key `mixed`, default ON).** All or nothing is what `mixed` off gives (run option `bt_goal.mixed=0`). With `mixed` on (the default), goals of a kind WITHOUT a tree no longer make SAGE plan the
whole run: the goals go in the reader's order. A run of neighbouring goals WITH trees is ticked as above. A run of neighbouring goals WITHOUT a
tree is planned by the SAGE planner ONCE, when the run reaches it, from the world as it is then (so it sees what the trees before it did; core
service `plan_goals(run, goals)`: the planner's steps for those goals alone, grounded and tidied), and its steps run one by one through
`try_step(..., ladder=True)` (the engine's own step path and ladder, the planner rung held back). A step with no way left hands the goals LEFT
(this part and every goal after it, trees included) to the SAGE replan once (`_to_sage`). Calls: one planner call per run of neighbouring
goals without a tree (none when every goal has a tree). No goal with a tree: SAGE plans the run, byte for byte as when off. **Not covered:**
the plan of a SAGE part skips the loop's certify gate (`_check`, the repair), only the planner's own verifier runs; a goal change said while
a part runs ends it like a tree (the engine applies the change).

**Runner** (`core/planning/behavior_tree.py`, pure, no clock): a node is `[type, *args]`: `sequence`, `fallback`, `retry n child`, the leaves
`cond name *slots` (reads the belief: `holding`, `hand_free`, `at`, `placed`, `opened`, `closed`), `act Action slot [{typed args}]` (one robot step; the optional mapping: Step S25), `ask key
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

**Tree keys (Step S25a).** Two opt-in keys sit next to `tree:` in a tree entry (the entry that is used; a kind with a list of entries has them per entry). None set: nothing below applies and the entry runs as before.
- `ladder: false`: every `act` of the tree calls `try_step(run, step, ladder=False)` ("the recovery is the caller's", `core/planning/option_services.py`): a failed step is not retried, searched or asked about by the engine; the tree's own `retry` / `fallback` decide, and `exhausted` applies when the tree has no way left. Only a literal `false` counts; the default is `true` (the ladder runs, as above). Steps run by SAGE for a kind with no tree (`mixed`) always run the ladder.
- `events: true`: every node, after it ends, is sent through the service `emit` as the public event `tree_node` `{goal, path, node, status}` (`goal`: the goal's number in the run, `path`: the node's place in its tree, "0.1.2", `node`: what it is, `status`: success / failure / stopped). It comes with the lab row `bt_goal`, which is written either way. Only a literal `true` counts; the default is `false` (no event).
- The `ask` leaf reads its choice words from the language pack (`ask_next.retry`, as above) and, for a ref that has no such list, from the robot pack's `questions` table: the ref `<question>.<choice>` names `answer.choices.<choice>` of that question, its `words` for the language of the run and the language-pack lists in its `lists`. A ref that names neither gives no word (the answer is "no"). A robot with no pack question is read as before. Code: `core/planning/tree_host.py` (pure).
Example (a made-up pack): `questions: {route: {phrase: q_ask_next_move, answer: {type: choice, choices: {shortcut: {words: {en: [shortcut, direct]}}}}}}` and the tree `[fallback, [ask, q_ask_next_move, route.shortcut], [act, MoveTo, "{to}"]]`.
**Typed arguments and pack questions (Step S25).** Both are opt-in: a tree with neither runs as before (`behavior_tree.valid` and `fill` are tested against a reference copy of the old code).
- `act` may end with ONE mapping, the typed arguments of the action: `[act, Seat, "{to}", {arm: left, dz_mm: -2}]`. Keys are strings, values strings, numbers or booleans; a string value `"{to}"` is filled from the goal like any slot (a missing one drops the tree). The tree host gives the step `args`; the pack declares the arguments of the action (`actions.<name>.args`, Step S26) and the engine checks them when it binds the step: a wrong value or an argument the action does not declare fails the step with the engine's own reason. An argument reaches the skill only when the skill's own params declare it. No mapping: the step is `{action, object}` as always. A mapping on any other leaf makes the tree invalid.
- `[ask, <question>]`: when the first argument names a question of the robot's pack (`questions.<name>` with a `phrase` and `answer.type: choice`; no shipped pack declares any), the leaf puts that question (its phrase, a `clarify` event with the question's `kind`, none when the kind is null) and reads the answer as the FIRST choice (declaration order) whose words it says: a word as the word lists read it (whole word, declared endings), a phrase of several words as that run of whole words, case and punctuation ignored; `words` of the language of the run and the language-pack `lists`. A choice read: the leaf succeeds and the fact `answered(<question>, <choice>)` is kept in the belief's fact store (`world.facts`, shown in the events); no choice read (not understood, silence): the leaf fails and an earlier `answered` of that question is removed. With `answer.keep_text: true` the raw answer is kept too, as `answer_text(<question>, <text>)` (not for silence). A tree branches on the content with the leaf `[cond, answered, <question>, <choice>]` (known to the host only: `CONDITIONS` stays the six belief names). The refs form `[ask, <phrase key>, *refs]` above is unchanged and is used whenever the first argument names no such question of the pack (a name that is both a phrase key and a pack question is the question). A robot is judged able to use the tree when it says the phrase of the question. Other answer types (`yes_no`, `place`, `number`, `extract`, `unclear: model`) are not read here: the question is then no pack question for this leaf.
Example (a made-up pack): `questions: {route: {phrase: q_ask_next_move, kind: route, answer: {type: choice, keep_text: true, choices: {shortcut: {words: {en: [shortcut, direct way]}}}}}}` and the tree `[sequence, [ask, route], [fallback, [cond, answered, route, shortcut], [act, MoveTo, <crate place>]], ...]`.
Goldens: tests/golden/scenario_sets/s25_typed_ask.py (`s25_typed_arg_reaches_skill`, `s25_typed_arg_wrong_value`, `s25_branch_on_answer_shortcut`, `s25_branch_on_answer_detour`, `s25_branch_on_answer_unclear`); unit tests: tests/test_option_bt_goal_typed.py. Code: `core/planning/behavior_tree.py` (the mapping), `core/planning/tree_host.py` (pure: reading and keeping the answer).
Goldens: tests/golden/scenario_sets/s25a_tree_keys.py (`bt_ladder_off`, `bt_ladder_on_retry`, `bt_node_events`, `bt_choice_answer`, `bt_choice_other_answer`); unit tests: tests/test_option_bt_goal_seams.py.

**Switch off:** key `enabled` (run option `bt_goal=0`), default ON (it does nothing for a robot that declares no `trees`). **Event:** lab flag `bt_goal`: `{goal, status}` ("claimed" once, per goal its
end, `exhausted` with the action taken) and `{goal, node, status}` per node (with the tree key `events`, also the event `tree_node`).
**Goal changes.** A correction said while a tree runs ends the trees before the next Act (status `changed`, service `changes_waiting`); the
engine applies it at its step boundary as for a plan (its repair, a SAGE replan for the new goals).
**Limits.** Goals run in the reader's order; the menu answer is read with the pack's words only (a person who does not understand a menu
question is a stop under A).
