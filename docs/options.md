# Options: the plug-in interface of the engine

An option is one switchable behaviour of the planning loop (a safety refusal, a reach check for deliveries, the handling of a
correction that changes nothing). Since round 19 an option is a **module** that declares itself to the engine; the loop
(`core/planning/assist_loop.py`) does not hold `if self.cfg.get("<option>")` branches for it. Rules: [architecture.md](architecture.md),
CLAUDE.md rules 13-15. The reference for the flat run options and manifest keys of the options that are not moved yet is [env.md](env.md).

## The parts

| part | where | role |
|---|---|---|
| `OptionSpec`, `Key`, `Option` | `core/options/spec.py` | what an option declares: name, key schema, hooks, services, order |
| `resolve`, `OptionConfig` | `core/options/config.py` | the keys of one option: run option > environment > manifest, **per key** |
| `Registry`, `register` | `core/options/registry.py` | the list of options; built-in ones from `robot_agent/options/__init__.py`, others by entry point group `robot_agent.options` |
| `Dispatcher` | `core/options/dispatch.py` | builds the enabled options of one loop, calls their hooks in `order` |
| `Services` | `core/options/services.py` | the interface an option uses to ask the loop for something (`LoopServices` in `core/planning/option_services.py` implements it) |
| the options | `robot_agent/options/<name>.py` | `ask_source`, `confirm_look`, `translate_retry`, `hazard`, `reach_delivery`, `unapplied_change`, `search_per_object`, `place_find`, `reopen_on_miss`, `end_state`, `object_words`, `precond_repair`, `gate_prune`, `dedupe_keep_end`, `goal_words`, `ask_focus`, `entity_cover`, `hw_wait`, `search_budget`, `lang_words`, `cond_goal`, `cond_extract`, `recipient_keep`, `plan_complete`, `ask_agree`, `replan_guard`, `name_recover`, `asr_guard` so far |

`python -m robot_agent.core.options list` prints the registered options, their keys and hooks.
`python -m robot_agent.core.options check MANIFEST.yaml` reports `planner.options` entries that name no option or key.

## Writing an option

```python
# robot_agent/options/my_option.py
from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="my_option",                                   # planner.options.my_option, and the lab_log flag
    keys=(Key("enabled", "bool", False, aliases=("my_option",)),),   # the legacy flat name: run option, $ROBOT_AGENT_MY_OPTION, planner.my_option
    hooks=("on_final",),                                # only the hooks it implements
    order=50, enabled="enabled",
    doc="One line for the options list.")

class MyOption(Option):
    def on_final(self, run):                            # self.cfg: its resolved keys; self.svc: the engine services
        return None                                     # None = no effect; a value ends the chain (decide hooks)

register(SPEC, MyOption)
```

Then: add `my_option` to the import line of `robot_agent/options/__init__.py`, an entry below in this file, and a test that runs it through
`AssistLoop` with the option on and off (`tests/test_architecture.py` lists the file in `THROUGH_THE_LOOP`). The pack's data, if the
option needs any, is read from the manifest (`planner.<section>`); the module holds no object, place, person or skill name and no word
of a language (CLAUDE.md rule 14); a missing declaration means the option does nothing.

Rules for the module: it imports the engine (`robot_agent.core.*`, `robot_agent.contract`) and nothing else of the package; never another
option (shared code goes DOWN into the engine as a public function); never a `_private` name of another module; functions of at most 60
lines, the file at most 400. Per-run state goes in `run.opt["<name>"]` (a dict the option creates), not in new attributes of the run.

## Keys and where they come from

A `Key` has a type (`bool`, `int`, `float`, `str`, `dict`), a default, `aliases` (the legacy flat names; the first one also names the
environment variable `ROBOT_AGENT_<ALIAS>`), `sources` and optional `choices`. The canonical names are `<option>.<key>` as a run option and
`planner.options.<option>.<key>` in the manifest. Sources are tried in the order the key lists them; the first that has a value wins; the
default is `("run", "env", "manifest")`. A key may leave out a source: `hazard.rules` is `("run", "env")`, because what a pack may
switch off is not the pack's to decide. A bool is read as the loop always read its flags (`1 | true | yes | on`, any case). In the manifest
the nested form wins over the flat legacy key; an empty value counts as no value. `AssistLoop.cfg` keeps the legacy names of every
registered option's keys (`cfg["hazard_rules"]`, `cfg["reach_delivery"]`, ...), so readers of `loop.cfg` (the benchmark adapter) see the
same values.

The keys are resolved ONCE, when the loop is built (`AssistLoop._resolve_options_cfg`). A host that merges pack data into a built loop's robot
view (the benchmark's manifest overlay) calls `refresh_options(loop, run_options)` (`core/planning/option_services.py`) before the run: the
keys are resolved again against the manifest as it is now, the options are made again and mirrored into `loop.cfg`; an unchanged manifest gives
the same keys. Without it an option whose data is only in the merged part is on and silently does nothing: in D3, `cond_extract` made no
extraction call in any run because its `prompt` was in the overlay only (`tests/test_option_refresh.py` checks every registered key). The engine
never calls it itself (the default path is unchanged). The late `resolve_key` reads of `precond_repair`, `hw_wait`, `search_budget`,
`lang_words`, `plan_complete` and `replan_guard` were a per-option work-around of the same thing; they stay and read the same value.

## Hooks

Each hook has a kind. `decide`: the first option that returns something other than None ends the chain and its value is the answer.
`transform`: the options are chained, each gets the value so far and returns a new one (None = unchanged). `observe`: all run, results
dropped. A *flow* hook is a generator (`yield from`): it may yield run events and return the value. `NEXT` and `STOP` (from
`robot_agent.core.options`) are the loop's own step signals.

| hook | kind | when | used by |
|---|---|---|---|
| `on_start(run)` | observe | run set-up, after the places are read | `reach_delivery` (time of the person snapshot) |
| `on_ground(mode, places, to)` | transform | the delivery mode for a goal to a person | `reach_delivery` |
| `on_plan(steps, run)` | transform | the grounded plan, before `plan_tidy`: the steps, or None (unchanged) | `place_find`, `precond_repair`, `gate_prune`, `replan_guard` (`release_dedupe`) |
| `on_where_answer(run, obj, ans)` | decide, flow | the answer to the engine's own re-asked "I couldn't find the X. Where is it?": the place to go to, or None | `ask_source` (`use_room`, `clarify_unusable`) |
| `on_understand_schema(schema)` | transform | the understand call's output schema (a fresh dict): options add optional fields; None = unchanged | `cond_goal` (`when`) |
| `on_understand_prompt(text)` | transform | the understand prompt template, before `.format` (escape braces): options add lines; None = unchanged | `cond_goal` |
| `on_heard(run)` | decide | the heard request, before the reader is called: a question to ask instead of reading (the engine asks it, sets `repeat_asked`), or None | `asr_guard` |
| `on_goals(run)` | decide, flow | understood goals, before the read-back and the grounding | `hazard` (the gate), `ask_source` (asks where an object is), `cond_extract` (writes the `when` of `cond_goal` after one extra model call), `cond_goal` (looks at the place of a `when`), `goal_words` (asks when a name is an object and a place) |
| `on_issues(issues, run, goals)` | transform | the read-back's issues (`understand.find_issues` plus the loop's extras): the list, or None (unchanged) | `ask_focus`, `entity_cover`, `ask_agree` |
| `on_planner_kwargs(kwargs)` | transform | the constructor keys of the planner (a dict) the loop builds; a key must also be in the backend's whitelist (`_GRACE_KWARGS`) | `replan_guard` (`replan_block_fix`) |
| `on_goal_replan(run)` | decide | the goals changed mid-run, before the plan is repaired: sub-goals for the rest of the plan (the planner's `replan(subgoals=)`), or None | `replan_guard` |
| `on_replan_findings(bad, run, suffix)` | transform | a replan's suffix (and its refine), after the engine's own findings: the list; a finding makes the one refine, then the uncertified end | `replan_guard` |
| `on_goal_edit(run, text)` | observe | a correction is about to be read | `unapplied_change` |
| `on_goal_reread(run, text, understood, goals, q)` | observe | the understand LLM re-read a correction | `unapplied_change` |
| `on_goal_unapplied(run, turn)` | decide, flow | the correction changed nothing (`turn.text`, `turn.change`) | `unapplied_change` |
| `on_step(run, step)` | decide, flow | before a step runs; returns the step that replaces it | `reach_delivery` |
| `on_step_done(run, step, held)` | observe | a step succeeded (`held`: the object in the hand before it) | `reach_delivery` |
| `on_step_failed(run, step, code, reason, action, obj)` | decide, flow | a step failed, before the recovery policy | `reach_delivery` |
| `on_goal_check(run, obj)` | decide | the end-of-run check of one goal: `(ok, why, skill)` | `reach_delivery` |
| `on_translation_lost(text, lost, translate)` | decide | the input translation lost a place name the person said: the English to use, or None | `translate_retry` |
| `on_second_look(run, obj, place)` | decide | the goal check's first look missed and the second saw the object: `(ok, why, skill)` or None (the second look is believed) | `confirm_look` |
| `on_final(run)` | decide | end of run, before the goals are checked; returns the `done` event | `unapplied_change` |
| `on_goals_met(run)` | decide | the goals hold; returns the `done` event | `hazard` (partial refusal sentence) |
| `on_finish(run, fields)` | observe | the fields of a `done` event, before it is built | `hazard` (`refusals`) |

`goal_words` also answers the service `goal_guard(run, goals)` (a guard with `place(name)` and `frozen(goal)`) that the engine puts on `places.guard` before the goal repairs (`goal_fix.drop_invented`, `repair_destination`, `shared_listener` read it; None = old behaviour).

Services (`OptionSpec.services`, asked with `Dispatcher.query(name, *args, default=...)`): `hazard.forbids(goal, places)`,
`hazard.refused_goals(run)`, `hazard.gate_off()`, `reach_delivery.no_refetch()`, `dedupe_keep_end.keep_after_dedupe()`. A query returns the first answer that is not None; `Dispatcher.collect(name, *args)` joins the lists of every option that serves `name` (`refused_goals`: `hazard` and `cond_goal`).

## The options

### `ask_source`
Ask where an object is, before looking for it (default off). The default SAGE behaviour is to guess, look, and ask only after misses
(`search_max`) or when nowhere is left. With the option, an `on` / `in` goal whose source the request does not name, and whose object the
memory does not hold as a trusted sighting, is asked about once, before planning: "Where is the mug?" (pack i18n key `q_where_is_obj`).
A place in the answer becomes the goal's source and a sighting the search trusts; "I don't know", silence or an answer without a place
leaves the goal as it is, so the object memory and the ranked search take over, and the question is not asked again after the first
misses. Key `enabled` (legacy `ask_source`, `$ROBOT_AGENT_ASK_SOURCE`); key `reask_on_miss` (default off, legacy `ask_source_reask`): when the person did
name a place and the first look there does not see the object, ask once more ("I couldn't find the X. Where is it?") instead of searching on alone;
after "I don't know" it does not ask again. More questions mean more chances of a wrong answer: it is
meant for a person who answers, not for the benchmark's simulated user.

Answers that name no place (each its own key, default off; they act on the answer to the question above):
* `use_room` (legacy `ask_source_room`, `$ROBOT_AGENT_ASK_SOURCE_ROOM`): the answer names a room only (`Places.candidates` gives the room marker `@room`).
  The robot says "OK, I will look in the study room." (pack i18n key `a_look_in_room`, slot `room`, English default) and the search belief looks at
  that room's places first (`SearchBelief.prefer`: before all others; inside the room the usual order, route included); the other places stay a
  fallback once the room is searched. Needs the search belief (default on). The first place is the goal's `from`.
* `confirm_near` (`ask_source_near`): the answer nearly names one place (`understand.fuzzy_place`, a cut or misspelled name): "Did you mean the X?"
  (`q_did_you_mean`); a yes makes it the source. When no place is near, the same question is asked for a ROOM (`understand.fuzzy_room`: the ENV's
  room names with and without "room", underscores read as spaces, and the language pack's room names `Places.ext.rooms`; same measure and
  threshold 0.25; two rooms equally near = no guess); a yes is used as `use_room` does ("OK, I will look in the study room."). "Story Room" is
  0.2 from "study room", "Stoney Room" is 0.27 (not near enough): key `near_threshold` (float, default 0.25, `ROBOT_AGENT_ASK_SOURCE_NEAR_THRESHOLD`)
  moves the room threshold; the place threshold stays 0.25.
  The measure (`understand.near_distance`, fixed a priori, not tuned on any data): Levenshtein with a swap of two neighbouring letters costing ONE edit
  (`toolobx` for `toolbox`), and a word that is a whole name of 4+ letters plus 1-2 letters (`workbenches`) costing one edit; divided by the longer
  length, at most `near_threshold`. Cut forms such as `laun. shelf` or `the study dsk` match through the other words of the span; a different word
  (`over there`, `garage`) stays far.
* `hedged` (`ask_source_hedged`): a hedged answer that still names a place ("I am not sure, maybe near the washr") is used, not dropped as an "I don't
  know": when the answer holds an "I don't know" phrase and names no place exactly, the nearly-named place (then room) is offered ("Did you mean the X?",
  as `confirm_near`, also when that key is off). A bare "I am not sure", or a hedge naming nothing near, stays an "I don't know". An exact place in a hedge is
  used already (the "I don't know" phrase never blocked it).
* `clarify_unusable` (`ask_source_clarify`): the answer names nothing usable and is not an "I don't know" (`dialog_unstick.unsure_answer`: the
  `answer_unsure` phrases of the language pack and of the robot pack's i18n; a language without any never triggers the question). One question,
  "Which room, or which piece of furniture, is it in?" (`q_where_in_home`); its answer goes through the same steps; still nothing usable = "I don't know".
  A hedge that contains an unsure phrase ("I am not sure, maybe near the racks") counts as "I don't know".

### `confirm_look`
A sighting that follows a miss is checked once more (default off). The end-of-run check looks for the object at its destination; when the first
look misses it looks again and believes the second. A camera can also see what is not there (a false sighting), so with the option a third look
decides: the goal counts as met only when the object is seen again (two looks of three). The cost is one more look, only when the first two looks
disagree. Key `enabled` (legacy `confirm_look`, `$ROBOT_AGENT_CONFIRM_LOOK`). Hook `on_second_look`; lab flag `confirm_look`:
`{object, place, seen_again}`. Module: `robot_agent/options/confirm_look.py`.

### `search_per_object`
Each object has its own search budget (default off). `search_total` counts the places missed in the whole run, so the first object's misses are
charged to the second one, which then gives up with looks left. With the option the count for an object is the places the search belief has marked
searched for it (no belief = the run-wide count, as before). Key `enabled` (legacy `search_per_object`, `$ROBOT_AGENT_SEARCH_PER_OBJECT`). Hook `on_start` (a no-op marker: an option declares a hook); service `looks_made(run, obj)`. Module: `robot_agent/options/search_per_object.py`.

### `place_find`
A Find of a place is dropped (default off). The planner sometimes writes `MoveTo P, Find P, Open P`; a Find looks for a thing, and a look at a place key
(an ENV key) is always absent, which the run counts as a miss. With the option a Find whose object is an ENV key the robot is at (its belief) or has just
moved to (a MoveTo of the plan) is removed from the plan; a Find of a thing, or of a place the robot is not going to, is kept. Needs the pack's
`planner.action_roles` `move` and `find` (none declared = does nothing). Key `enabled` (legacy `place_find`, `$ROBOT_AGENT_PLACE_FIND`). Hook `on_plan`
(transform: the grounded plan before `plan_tidy`); lab flag `place_find`: `{place, index}`. Module: `robot_agent/options/place_find.py`.

### `dedupe_keep_end`
The gate's removal of a repeated fetch keeps the steps after it (default off). `plan_repair.op_dedupe` removes a Find / Pick of an object the plan already placed at its goal; with no
release after the repeat the cut ran to the LAST step of the plan and removed the way home (the final MoveTo). With the option the cut ends at the repeat's own Find / Pick; a repeat followed
by a release is cut as before. No pack data. Key `enabled`. Hook `on_start` (declared only); service `keep_after_dedupe()` (read by the gate's repair as `keep_end`). Module: `robot_agent/options/dedupe_keep_end.py`.

### `recipient_keep`
A delivery's recipient moves to the speaker only on evidence (default off; D3 layer L10). The engine rule `goal_fix.shared_listener` moved the recipient of a delivery to the speaker whenever
that person is also a message's listener and the words name them once ("bring him the cup, and let him know": the pronoun is no mention). With the option the move needs a recipient word of the
language packs (`lexicon.RECIPIENT` and the robot's `recipient_words` phrase) that is itself a first-person word (`lexicon.FIRST_PERSON`: "me", "get me", a first-person pronoun with its case
particle); a delivery verb or "here" alone says nothing about who gets the object. Without evidence the reader's recipient stays. With evidence and the person named twice the two readings disagree:
one question in `on_goals` (the existing `q_which_reading` phrase; the answer decides by the same evidence) when the run has no answer yet and `ask_when_unsure` is on. No pack data of its own; no
first-person words in the packs = the recipient always stays. Keys `enabled` (legacy `recipient_keep`), `ask_when_unsure` (true). Hooks `on_start` (sets `places.listener_rule`, which
`goal_fix.shared_listener` runs in place of its own rule), `on_goals` (the question). Lab flag `recipient_keep`: `{rule, person}`. Module: `robot_agent/options/recipient_keep.py`.

### `replan_guard`
A replan repairs the failed step's own block, is built from the new goals after a goal change, and does not run a suffix the action model rejects (default off; D3 layer L12; D1 layer `replan_block`). Three parts, each a bool key
that is on when the option is on (set one to false to ablate): (1) `block_fix`: the planner is built with the embodiplan SAGE key `replan_block_fix` (hook `on_planner_kwargs`; the backend whitelist carries it): the failed block is found
by lining the completed steps up with the plan's own steps, so the steps the engine added (a search, a look) or dropped do not move the repair into the next or the last block; a failed step that is no plan step gets the global replan.
(2) `goal_rebuild`: after a goal change (`_repair_goals`) hook `on_goal_replan` gives the planner sub-goals rebuilt from the new goals (service `subgoals`, `replan(subgoals=)`; the backend forwards them) and sets the plan's task text
to the new goals' text; before, the old block was repaired with the old task. (3) `critical_refine`: hook `on_replan_findings` simulates the replan's suffix from the belief now with the action model and adds the findings whose code the
pack declares, so the one existing refine is told and, if they remain, the run ends uncertified. Pack data `planner.options.replan_guard.critical`: `{"codes": [<action-model code>], "source": "<label>"}` (or `"assumed": true`);
no codes or no label = part 3 does nothing. A recipient the goal names has no action-model code and is not covered. (4) `guarded_run` (with part 3): a finding that the engine's own run-time check refuses before the step
moves (`verify.symbolic_check` on the belief the suffix reaches there, the check `_guard` / `_precondition` makes; it replans at that step) only steers the one refine: it is added when a refine follows the call (service
`replan_round`: the replans recorded and the planner calls left), and a refined suffix whose findings are all of that kind runs (lab `part: "guarded"`). The uncertified end stays for findings the run-time check cannot catch
(e.g. `find_wrong_place`, which needs the goals). Off: those findings end the run as before. (5) `release_dedupe` (hook `on_plan`, every plan): a release that the action model refuses with an empty hand and that repeats the
plan's last release (same action and object; a release = a step that emptied the hand in the replay, no name in the code) is dropped. The MoveTo a release lacks is `precond_repair`'s declared `near` need (its `on_plan`
runs on a replan's suffix too), not this option's. Keys `enabled` (legacy `replan_guard`), `block_fix`, `goal_rebuild`, `critical_refine`, `guarded_run`, `release_dedupe`, `critical` (dict).
Lab flag `replan_guard`: `{part, subgoals | findings | dropped}`. Module: `robot_agent/options/replan_guard.py`.

### `name_recover`
An object name the speech recogniser garbled is matched to the robot's object catalogue (default off; D3 layer L11). After the goals are read (`on_goals`), a goal object (not a `say` goal, not a place or person of the ENV)
that is not in the catalogue (object memory and `limits.objects`) and is not, as whole words, part of a catalogue name or holding one, is compared with the catalogue names by `understand.near_distance`
(a swap of neighbours is one edit, a name plus 1-2 letters is one) in the pack's unit: characters, or the letters inside a syllable when the language pack declares `asr.distance_unit: "jamo"`. ONE name within
`max_edits` (2) and `max_ratio` (0.34 of the longer name) is asked ("Did you mean the X?", core phrase `q_did_you_mean`; a yes or the name said takes it, anything else keeps the goal as read): the catalogue is often only
what the robot has seen, so a real word near a known name may be something it has not seen yet. It is taken without a question only when the robot declares its catalogue `complete` AND the language packs declare their
words (`asr.words`) and no word of the name is one. Two or more are asked ("Did you mean A or B?", phrase `q_which_name`), an answer that names exactly one decides, any other answer keeps the goal as read; none: nothing
changes, nothing is asked. A word is settled once per run (a later reading takes the same answer without a second question). The questions are phrases: the robot's i18n, else the language pack's `speech` block
(`speech.phrase` reads it; Korean declares `q_which_name`), else the core English; no text in the run's language or `ask` off: nothing asked, nothing changed (fail open). A name shorter than `min_len` (4) units is never
matched. Not recoverable: a word that sounds alike but is spelled in another script or language, and a garble of more edits than the limit; those stay as read. Keys `enabled` (legacy `name_recover`), `max_edits`,
`max_ratio`, `min_len`, `ask` (true), `complete` (false), all assumed. Pack data: `asr.distance_unit`, `asr.words`, `speech` (assumed, labels in the pack).
Lab flag `name_recover`: `{said, took, asked | near}`. Module: `robot_agent/options/name_recover.py`.

### `asr_guard`
A request the recogniser lost is asked again, once, without calling the reader (default off; D3 layer L11, switched on with `name_recover`). New hook `on_heard` runs at the start of an understand round: an EMPTY request
(the task and the person's answers hold no word) or one with a HOLE (the language pack's `asr.hole` regexes, per language: an article or possessive directly followed by a function word or by the end; English leaves out the words that also end a phrase, `her`, `his` and a bare `a` (a letter name), and `and` / `or` followed by another possessive) gives the repeat phrase
`q_repeat`; once per run (`repeat_asked`). No `asr.hole` data = only the empty check. Keys `enabled` (legacy `asr_guard`), `empty` (true), `hole` (true). Lab flag `asr_guard`: `{what}`. Module: `robot_agent/options/asr_guard.py`.

### `plan_complete`
A plan that stops short of the step its goal needs is completed from the pack's declared rules (default off; D3 layer L13). Three classes: (1) the plan picks an object and
then closes (opens) the place the goal puts it in with no release between: the skill declared `achieves: in` is spliced in before the Close (after the Open), after a MoveTo the place
when the robot is elsewhere; a release later in the plan means nothing is missing. (2) Steps another repair appended after the final go-home block (the steps `planner.end_state` names) go in front
of it, so the home goal still holds; a hand-over whose rule declares `near` gets a MoveTo the goal's person first. (3) A speech step whose rule declares `near` gets a MoveTo the say goal's
listener, and a speech step that names the listener instead of the message is rebuilt with the goal's message (one say goal, or the goal whose listener it names; two goals and no listener = nothing).
Pack data `planner.options.plan_complete.needs`: `{"rules": {<Action>: {"near": bool, "achieves": "in" | "hand"}}, "source": "<label>"}` (or `"assumed": true`). Roles read: `move`, `open`,
`close`, `say`, `pick`, `put_down`; one not declared skips that repair. No rules, no label, no goal of the kind = the plan is unchanged. Keys `enabled` (legacy `plan_complete`), `needs`
(dict, manifest only). Hook `on_plan` (order 86, after `precond_repair`). Lab flag `plan_complete`: `{action, need, index}`. Module: `robot_agent/options/plan_complete.py`.

### `cond_goal`
A request with a condition ("if the spoon is on the table, put it away; if not, leave it") is decided by the robot looking, not by the reader guessing (default off).
The understand schema gets an OPTIONAL object `when` `{object, at, present}` on a goal, written after `content` (key order: the grammar of the Ollama format follows `properties`,
required keys first; `when` is last, so a goal with no condition is written exactly as before). Meaning: do this goal only if `object` is (`present` true) or is not (false) at `at`.
Before the read-back the robot goes to `at` (role `move`), looks up to `looks` times (default 2, stops at the first sighting), keeps the goals whose `present` equals what it saw, drops the
others (they are listed in `run.filtered`) and removes `when` from every goal. Unsure = the two looks differ (a miss, then a sighting), or the object memory has the object at `at` and no
look saw it, or the robot cannot go and look: it asks once (`ask_when_unsure`, default true); a yes or no decides. Any other answer, no answer, no question text, no `move` role and no answer, a place that cannot be
grounded even after the one `q_where`: UNKNOWN, a third state: no branch is kept or dropped and the run ends `failed` saying the question (D3: before, this read as absent, a guess). Services `refused_goals(run)` (the goals it dropped:
the read-back counts them as covered) and `goal_dropped(run, goal)` (a re-sampled reading does not bring them back). If no goal is left the run ends `success` with nothing done. An incomplete `when` (a missing key, a non-boolean) is no condition.
Pack data (manifest `planner.options.cond_goal.<key>`, or a run option `cond_goal.<key>`; no language in the code): `prompt` (lines added to the understand prompt before `Task:`; none = the option changes
neither schema nor prompt), `q_where` (phrase key `cond_where`, slot `{object}`), `q_unsure` (`cond_unsure`, slots `{object}`, `{place}`), `say_seen` (`cond_seen`), `say_not_seen`
(`cond_not_seen`); an empty text = not asked / not said; the other languages come from the pack's i18n under the phrase key. Use it with a reader trained on `when` (an older reader may ignore it or
use it wrongly: measure it on requests with no condition before turning it on). Keys `enabled` (legacy `cond_goal`), `looks`, `ask_when_unsure`, `prompt`, `q_where`, `q_unsure`, `say_seen`,
`say_not_seen`. Hooks `on_understand_schema`, `on_understand_prompt`, `on_goals`; lab flag `cond_goal`: `{object, at, place, seen, holds}`. Module: `robot_agent/options/cond_goal.py`.

### `cond_extract`
A request with a condition word is split by ONE extra model call and handed to `cond_goal` (default off; D3 layer L9). The reader stays unchanged (it has no `when` field; an untrained reader turns "if X is at P, do A,
otherwise B" into plain goals). When the request (as said, and its English reading) holds a condition word of its language, the pack's prompt is sent once to the planner's model (service `ask_model`: no repair call, stoppable, in the
run's LLM budget) with the request and the reader's numbered goals; the fixed JSON answer (`conditional`, `object`, `at`, `present`, `if_goals`, `else_goals`, `drop`, `unsure`) becomes the `when` field of `cond_goal` on the goals of each branch
(the else branch gets the opposite `present`); goals in `drop` only state the condition and are removed (listed in `run.filtered`); a goal in no list is unconditional. It runs at order 15, before `cond_goal` (20): the `when` field is the hand-over, no option imports another.
No condition word, no prompt, no goals, a goal with a `when` already, or a second pass of the same run: no extra call (a later reading of the same run with the same number of goals gets the first plan applied again). Non-JSON, `conditional` false, bad or clashing indexes, no object / `present` / branch goal: goals unchanged.
Real but unclear (`unsure`, or the place is missing or not one the robot knows): the person is asked once (`q_unclear`, phrase key `cond_unclear`, slot `{object}`; no text or `ask_when_unsure` false = not asked); a place the robot knows in the answer
completes the condition, any other answer leaves the reader's goals. Pack data (manifest `planner.options.cond_extract.<key>`): `prompt` (slots `{request}`, `{goals}`), `clauses` (`{lang: {"condition": regex | [regex], "source" | "assumed": true}}`, added to the
language packs' `patterns.condition`; an entry with neither label is not used), `q_unclear`. Empty pack = the option does nothing. Use `cond_goal` WITHOUT its `prompt` key with it (and its `q_where`, `q_unsure`). Keys `enabled` (legacy `cond_extract`),
`ask_when_unsure`, `prompt`, `clauses`, `q_unclear`. Hook `on_goals`; lab flag `cond_extract`: `{state, object, at, if, else, drop}`. Module: `robot_agent/options/cond_extract.py`.
Its pack data is read through its keys: a host that merges it into a loop already built calls `refresh_options` ("Keys and where they come from"), else the option makes no call.

### `gate_prune`
An Open / Close of a place the ENV declares not openable is dropped (default off). The planner opens a shelf or a table; the skill refuses and the run replans or gives up.
Fail open: only an ENV entry with an explicit `openable` field that is false counts (no field, a name that is not a key, a place an `open` / `close` goal names: kept). Needs the
pack's `planner.action_roles` `open` and `close` (none declared = does nothing). Key `enabled`. Hook `on_plan`; lab flag `gate_prune`: `{place, index}`. Module: `robot_agent/options/gate_prune.py`.

### `precond_repair`
A plan that lacks a step its next skill needs gets that step (default off). The planner writes a hand-over with no Pick before it, a Pick with a full
hand; the gate rejects the plan and the run gives up. The pack declares what its skills need, and the option replays the plan on the belief now and
splices the missing step before the gate. Pack data `planner.options.precond_repair.needs`:
`{"rules": {<Action>: {"holding": bool, "hand_free": bool, "near": bool}}, "source": "<label>"}` (or `"assumed": true` for `source`).
`holding`: the Find / Pick of the one `on` / `in` goal object no step picks (none or several = nothing). `hand_free`: the held object is put down at
the surface an `on` goal names for it (no goal = nothing). `near`: a MoveTo the step's object (an ENV key). Roles read (`planner.action_roles`):
`move`, `find`, `pick` (new role), `put_down`; one not declared skips that repair. No rules, no label, an action without rules, or a plan that already
meets the rules = unchanged. Key `enabled` (legacy `precond_repair`), `needs` (dict, manifest only). Hook `on_plan`; lab flag `precond_repair`:
`{action, need, index}`. Module: `robot_agent/options/precond_repair.py`. End of plan: a rule key `achieves` (`"on"` | `"in"` | `"hand"`) says the skill
ends a goal of that kind; when the replay ends holding the object of such a goal that no step released, the skill is appended (`on` / `in` after a MoveTo
the goal's ENV place, `hand` for a goal with `hand`; no rule = nothing; need `achieves` in the lab flag). `gate_complete` was tried with it: no gain (docs/home/bench/d2_layers.md of symbolic_robot).

### `goal_words`
The goal filter compares whole words (default off). `goal_fix.drop_invented` dropped a move goal whose object it grounded to an ENV place, and the grounding
matches substrings and aliases: "lamp" was found inside "lampshelf", and an object that is also an alias of a place was dropped as an appliance. With the
option a name is a place only when it equals the head or an alias of exactly one ENV key (articles and case ignored); a name the robot knows as an object
(its object memory, the manifest `limits.objects` names) is an object and the goal is kept; when that name is also a place, the person is asked once which
one they mean (`on_goals`; an answer that names the place and not the object drops the goal, any other keeps it); an object named by two or more move
goals keeps the receiver of each (`repair_destination` and `shared_listener` skip it). No pack data: no catalogue = whole words alone. Key `enabled`
(legacy `goal_words`). Service `goal_guard(run, goals)`, hook `on_goals`; lab flag `goal_words`: `{rule, name, place}`. Module: `robot_agent/options/goal_words.py`.

### `ask_focus`
Purposeful questions (default off). Four rules on the read-back. (1) No redundant confirmation: an `object` issue (a noun the words name that no goal lists)
is dropped when the noun is, as whole words, the head or an alias of an ENV place, or a word of another field of a goal (a tool, a surface the plan reaches
by other words). (2) Ambiguity from the world: a move goal whose object fits two or more objects of the world (the robot's object memory and the manifest
`limits.objects`; all the description's words, whole, inside the name; none named exactly like it) adds a `choice` issue, and the read-back asks "Which X
do you mean: A or B?" (`understand.issue_question`), whatever the reader's label said. (3) An "I don't know" is no refusal: to "Is that right?" it leaves
the reading standing instead of starting the correction path; the phrases are the language pack's and the robot pack's `answer_unsure` (service `is_unsure`,
`dialog_unstick.unsure_answer`), no data = nothing is claimed. (4) A limit on the read-back rounds: key `max_confirms` (default 3; the engine's own limit is two,
and one more after a correction, so the default only caps the `dialog_converge` extras; 1 = one read-back only). No pack data. Key `enabled` (legacy
`ask_focus`), `max_confirms`. Hook `on_issues`, services `confirm_limit(run)`, `unrefused(run, ans)`; lab flag `ask_focus`: `{rule, ...}`.
"I don't know" is no refusal only when the answer says nothing else (service `unsure_leftover(run, text)`: the answer without its "not sure" phrases; a no, a correction or a reason is a refusal). Articles from the language pack.
Module: `robot_agent/options/ask_focus.py`.

### `ask_agree`
Read-back dialogue (default off, D3 layer L14). Closes the ask loops and the answers accepted although they contradict the person. (1) A bare no, then the same request:
the refused reading comes back from the person's restatement; instead of "which part is wrong?" and a stop, hook `on_goals` sets the run's `allow_repeat` once, so the reading
is read back ONE more time as a yes / no question (only a yes acts; a second no or silence stops as before). Only after a refusal that was a bare no (a "no, the X" is no
case), while the engine's own two read-backs are not used up (key `restate_confirm`, default true). (2) Service `unrefused` (order 78, before `ask_focus`): an "I don't know"
leaves the reading standing only when it is clean: the answer says nothing but "not sure" (service `unsure_leftover`; each leftover word is a filler, a first-person word, a
whole word of an unsure phrase of the pack, from `answer_unsure` or `dialogue_ext.answer_unsure`, or such a word with one declared ending, `dialogue_ext.polite_endings` or
`patterns.answer_tail`; a no-word or any other word is a refusal), the read-back did not carry the robot's own doubt (`run.unsure`), and no answer changed the reading since its
first read-back. (3) Either-or (key `either_or`, default true): when the reading changed exactly one value (object, `to` or `from` of one goal) and the person had said the earlier
value, the "I don't know" asks once, "I can read that two ways: A, or B", as a declared choice; the answer that names one reading decides (the current one stands; the earlier one
is put back in `run.patched` with `allow_repeat`, read back again, and the place given up is no "you mentioned X" question: hook `on_issues`); a plain yes or no names neither,
nothing is picked and the answer is a refusal. Pack data: the language pack's own (`dialogue.fillers` / `no` / `polite`, `words.first_person`, the unsure phrases, the endings);
none = nothing is claimed. Keys `enabled` (legacy `ask_agree`), `restate_confirm`, `either_or`. Hooks `on_goals`, `on_issues`, service `unrefused(run, ans)`; lab flag `ask_agree`:
`{rule}`. It does not edit `ask_focus`: with `ask_focus` on and this off, the D2 behaviour is unchanged. Module: `robot_agent/options/ask_agree.py`.

### `entity_cover`
Entity coverage (default off). Closes the class "the run says done after dropping one clause of the request": every object the request names must
end up as a goal or as a stated reason for dropping it, and a dropped one is asked about. The names are the object catalogue (the robot's object
memory and the manifest `limits.objects`), whole words (the last may be a plural), read in the prompt language: the task itself, or, for another
language, the language layer's trusted reading (service `request_english(run)`; no trusted reading = nothing is added). A catalogue name that the
request says, that no goal's object holds (whole words, either way round; a say goal's message counts) and that is not a place the robot knows adds
an `object` issue, so the read-back asks about it once (the engine's `issue_asked`: no repeat). When `run.filtered` holds a note that names the object
(the reason a goal for it was dropped) the issue carries it as `reason`; key `ask_reasoned` (default on) decides whether such a drop is asked about too.
A noun the catalogue does not know is ignored. Three readings that are not a dropped object (D2 round 2): a mention the person took back (the last catalogue name within 40 characters before a self-correction marker of the language pack: "the blue mug, wait, no, the red one"); a short name only inside a longer catalogue name that the request says ("mug" in "red mug"); another name of a goal's object (a shared word, the request never says the goal's own name, no third catalogue entry has the word). No pack data. Keys `enabled` (legacy `entity_cover`), `ask_reasoned`. Hook `on_issues`, service
`request_english`; lab flag `entity_cover`: `{what, reason}`. Limit: a drop the person meant (a negation) is asked about too while `ask_reasoned` is on.
Names follow the language pack (`articles`, `plural_rules`, `grammar.joiners`; `core/planning/names.py`): none declared = nothing stripped or folded. A name counts only in the active
text (no condition, negation or correction) in object position and not behind a boundary word (a landmark or tool). Order 75: runs before `ask_focus` (80).
Module: `robot_agent/options/entity_cover.py`.

### `hw_wait`

Hardware-fault wait. One fixed schedule (`planner.recovery.wait`, for example 2+4+8 s) and the policy table's retry counts serve every kind of
failure; a run stops at a fault another wait would have cleared, or sends wait commands for a fault no wait clears. With the option on, the
pack declares, per failure code of the contract, the schedule, the number of tries and the first answer:

```yaml
planner:
  options:
    hw_wait:
      kinds:
        perception_unavailable: {schedule_s: [1, 3], tries: 4, action: wait_health}   # tries >= 1; action wait_health | health_check | stop
        hw_fault: {schedule_s: []}                                                     # an empty list: no wait command for this kind
        assumed: true                                                                  # or source: "<label>"
```

`schedule_s` replaces the `recovery.wait` schedule for that kind (the wait action is still the pack's `recovery.wait.action`; read where
`wait_on_robot` runs the wait). An empty list sends no wait command: the pack says waiting does not help. `tries` and `action` replace the
policy table's `retries` and first answer for that kind. A kind with no entry, a bad entry or no data: the engine's behaviour, unchanged.
Hook `on_start` (no effect), services `wait_schedule(run, schedule)`, `recovery_rule(run, code, rule)`, `more_tries(run, code, n)` (D3: only with the key `more_tries` on: an unhealthy robot after a wait does not stop the run while the declared `tries` are left; off, the option behaves as in D2). Keys `enabled` (legacy `hw_wait`), `kinds` (dict, manifest only), `more_tries` (bool, default off, legacy `hw_more_tries`). Lab flag `hw_wait`:
`{kind, schedule_s, tries, action}`. Limit: the schedule needs `wait_on_robot` and a declared `recovery.wait`; without them the poll stays.
Data with neither `source` nor `assumed: true` (on the block or on a kind) is not used. Module: `robot_agent/options/hw_wait.py`.

### `search_budget`

Search budget per object (default off). One budget for the whole run charges the places a first object used up to the next one; a search goes on over
places nothing points to; a robot gives up without telling the person, or asks again after "I don't know". With the option on and pack data declared
(`planner.options.search_budget.budget`):

```yaml
planner:
  options:
    search_budget:
      budget: {looks_per_object: 8, stop_probability: 0.1, ask: once, assumed: true}     # or source: "<label>"; every field optional
```

`looks_per_object` (an int >= 1) replaces `search_total` and counts the places searched FOR THAT OBJECT. `stop_probability` (0 < p < 1): the search is
over when the share of the belief (sightings, habits, ranking, floor) on the places not yet looked at is under it. When the search is over the engine's
where-question is asked once per object (`ask: never` skips it); the answer is looked at, and a further miss, or "I don't know", ends the run naming
the places looked at. An "I don't know" given earlier does not repeat the question and does not end the search while looks are left. No data, no search
belief or off: the engine's behaviour, unchanged. Needs search legs (a legs-less search keeps `search_max`). Engine hooks added: `_search_total`
and `_search_over` (in `search_hooks.py`) ask services `search_limit(run, obj)` and `search_over(run, obj, ask)`; the option also answers
`looks_made(run, obj)`. Hook `on_start` (no effect). Keys `enabled` (legacy `search_budget`), `budget` (dict, manifest only). Lab flag `search_budget`:
`{object, looks, remaining, why}` once per object. A budget with neither `source` nor `assumed: true` is not used. Module: `robot_agent/options/search_budget.py`.

### `lang_words`
The words that open a condition ("if the drawer is open", "unless ...") or a negation ("do not touch the mug", "except ...") were English regexes in
the engine (`understand._COND`, `_NEG`), so a reading in another language took no clause out of the request (default off). With the option, `on_start`
gives the run's `Places` the compiled `patterns.condition` and `patterns.negation` of the run's language pack and of the prompt language's
(`core/planning/clauses.py`: `build(lang)`, `of(places)`); `understand._active` (the object coverage, the destination repair) and
`goal_filter.negated` read them in place of the engine's. The recipient words (`words.recipient`) and the correction markers
(`dialogue_ext.self_correction`) were pack data already; no clause word is left in code for a run that has the data. Data: the
language packs (`lang/<code>.json`, `patterns`: `condition`, `negation`, each a regex or a list; the shipped English pack holds the former engine regexes, same
reading) and the robot pack (`planner.options.lang_words.clauses`, `{lang: {condition, negation, source | assumed}}`, added to the pack's words of that
language; a language with no pack file is declared there, as D1 does for Korean, assumed). A language with neither key: the engine's
own reading stands (fail open); a pack with one kind takes nothing out for the other. Hook `on_start` (sets `run.places.clauses`). Keys `enabled`
(legacy `lang_words`), `clauses` (dict, manifest only). No lab flag (`lab_log` is cleared after `on_start`). Module: `robot_agent/options/lang_words.py`.

### `reopen_on_miss`
A Find that misses inside the container just opened is made again after closing and opening it (default off). A door can report open and still not
show what is in it; the miss then says nothing about the object. With the option, when the Find right after an Open of the container the robot is at
fails `absent`, the pack's remedy runs on the container once (no miss is counted) and the Find is made again. Pack data: `planner.recovery.reopen_on_miss`
(a list of the pack's actions, e.g. `[Close, Open]`) and `planner.action_roles` `find` and `open`; any missing, or an action the robot lacks = does
nothing. Key `enabled` (legacy `reopen_on_miss`, `$ROBOT_AGENT_REOPEN_ON_MISS`). Hook `on_step_failed`; lab flag `reopen_on_miss`: `{container, object}`.
Module: `robot_agent/options/reopen_on_miss.py`.

### `end_state`
The state a task ends in is part of the goal (default off). The engine holds a `go` goal ("go back to the dock") to where the plan ENDS only when it is the
last goal, and a plain MoveTo can stand in for the pack's own action (docking, not only arriving). With the option and the pack's data
`planner.end_state: {places: [ENV key or head, ...], action: <an action of the pack>}`, when a `go` goal is for one of those places, the robot goes
there (if it is not) and runs the action before the goals are checked, up to `planner.recovery.return_tries` times (default 1); when that fails the run
ends `failed`. No data, an action the robot lacks, or no `move` role (robot not there) = does nothing. Key `enabled` (legacy `end_state`,
`$ROBOT_AGENT_END_STATE`). Hook `on_final`; lab flag `end_state`: `{place, action, ok, tries}`. Module: `robot_agent/options/end_state.py`.

### `object_words`
The goal checks find an object the plan names in other words (default off). The run's belief keeps what the plan did under the executor's name
(`Pick cell phone`), the goal keeps the reader's (`phone`); the exact-name lookup found nothing, the goal was believed unmet after a plan that did
everything, and the run replanned, fetched the object again and ended failed. With the option the two belief tables (placed, handed over) look an
object up by its words: the single entry whose tokens contain or are contained in the asked name; several fits or none stay unmatched. Key `enabled`
(legacy `object_words`, `$ROBOT_AGENT_OBJECT_WORDS`). Hook `on_start` (a no-op marker); services `belief_table`, `belief_set`. Module:
`robot_agent/options/object_words.py`.

### `translate_retry`
A translation that lost a place name is made again (default off). The input translation of a non-English utterance is checked against the ENV names the
person said; when the English lacks one (a Korean word for a place came out as another thing) the sentence is untrusted and the robot asks the person to
say it again. With the option the sentence is first translated once more with each lost name already replaced by its English name from the ENV; the retry
counts only if it keeps every name, else nothing changes. One more model call, only when a name was lost. Key `enabled` (legacy `translate_retry`,
`$ROBOT_AGENT_TRANSLATE_RETRY`). Hook `on_translation_lost`; lab flag `translate_retry`: `{lost, ok}`. Module: `robot_agent/options/translate_retry.py`.

### `hazard`
The robot's safety rules refuse a goal, or drop it and say so at the end. The rules are the robot's safety profile, not this option's
([safety.md](safety.md)). Keys: `rules` (legacy `hazard_rules`; `core` (alias `profile`) | `pack` | `off`; run option and environment only),
`receivers` (legacy `hazard_receiver`; turns on the rules with `enabled_by: hazard.receivers`). Always on once registered; a robot with no
profile gets no refusal. With `receivers` on, the pack's profile rules that name `enabled_by: hazard.receivers` refuse a product x receiver pair (tests: `tests/test_option_hazard_receivers.py`; D1 turns it on, layer L5). Hooks `on_goals`, `on_goals_met`, `on_finish`. Module: `robot_agent/options/hazard.py`.

### `reach_delivery`
A delivery to a person ends within their reach or in their hand (round 15). Key `enabled` (legacy `reach_delivery`). Needs the pack's
`planner.person_snapshot: {trust_s}` and `planner.action_roles`; without them it does nothing. Details: [env.md](env.md) (`planner.reach_delivery`).
`lab_events`: `{flag: reach_delivery, why: stale | stale_no_handover | no_declared_spot | asked_where | known_where, ...}`. Module:
`robot_agent/options/reach_delivery.py` (the logic that was `core/planning/reach_delivery.py` is in the same file).

### `unapplied_change`
A correction the robot cannot apply is not dropped silently: the understand step's own question is asked once, and if the correction still
changes nothing the run ends `failed` ("I could not apply your change") instead of `success`. Key `enabled` (legacy `unapplied_change`).
Module: `robot_agent/options/unapplied_change.py`.

## Not a loop option: the speech recogniser's vocabulary hint (`ROBOT_AGENT_STT_HINT`, default off)
The transcription call happens outside the planning loop (`utils.speech_to_text`, used by the dashboard listen `POST /agent/listen/<id>/audio` and by
the robot's microphone), so it is a switch of that call, not a hook. With `ROBOT_AGENT_STT_HINT=1` the ENV's room and place names (heads and
aliases in the script of the run language, `speech.script_ok`; no person or speaker entries; deduplicated; ENV order, rooms first) are added to the
`prompt` field (Whisper's initial prompt) after the caller's own hint, cut at a name to `ROBOT_AGENT_STT_HINT_CHARS` characters (default 400).
Module `core/planning/stt_hint.py` (pure functions; no names in code). An ENV without names, or a language with none, sends nothing. A server that
answers 4xx to a call with a `prompt` gets the call again without it (`connect/stt`, one warning per process). Speaches at `127.0.0.1:8000` accepts
the field (smoke test, 2026-11-12). Whether it improves recognition is not measured (no speech sample / TTS on the machine).

## Moving an option out of the loop (the recipe)

1. List every place in `assist_loop.py` that reads its keys (`grep` the key names) and the run attributes it sets.
2. Name the hook for each place (reuse a hook of the table if the moment is the same; add one to `HOOKS` only for a new moment).
3. Write the module with `SPEC` and the class; replace the loop's code by the dispatcher call at the same place; remove the key from
   `ASSIST_OPTIONS` and `_v2_cfg_base` (the registry adds the names to `ASSIST_OPTIONS`). The legacy flat name stays in `PLANNER_KEYS`
   (`contract/manifest.py`; the contract layer cannot import the registry) and its comment moves into the option's docstring.
4. Prove it: the option's own tests, `tools/lint_arch.py --update` (the counts must go down), and the **replay pin**: the same episodes
   before and after, equal (`--llm fake` dev tasks for the variants that use the option, and the offline replay of recorded runs).
