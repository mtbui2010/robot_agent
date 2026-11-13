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
| the options | `robot_agent/options/<name>.py` | `ask_source`, `confirm_look`, `translate_retry`, `hazard`, `reach_delivery`, `unapplied_change`, `search_per_object`, `place_find`, `reopen_on_miss` so far |

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

## Hooks

Each hook has a kind. `decide`: the first option that returns something other than None ends the chain and its value is the answer.
`transform`: the options are chained, each gets the value so far and returns a new one (None = unchanged). `observe`: all run, results
dropped. A *flow* hook is a generator (`yield from`): it may yield run events and return the value. `NEXT` and `STOP` (from
`robot_agent.core.options`) are the loop's own step signals.

| hook | kind | when | used by |
|---|---|---|---|
| `on_start(run)` | observe | run set-up, after the places are read | `reach_delivery` (time of the person snapshot) |
| `on_ground(mode, places, to)` | transform | the delivery mode for a goal to a person | `reach_delivery` |
| `on_plan(steps, run)` | transform | the grounded plan, before `plan_tidy`: the steps, or None (unchanged) | `place_find` |
| `on_where_answer(run, obj, ans)` | decide, flow | the answer to the engine's own re-asked "I couldn't find the X. Where is it?": the place to go to, or None | `ask_source` (`use_room`, `clarify_unusable`) |
| `on_goals(run)` | decide, flow | understood goals, before the read-back and the grounding | `hazard` (the gate), `ask_source` (asks where an object is) |
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

Services (`OptionSpec.services`, asked with `Dispatcher.query(name, *args, default=...)`): `hazard.forbids(goal, places)`,
`hazard.refused_goals(run)`, `hazard.gate_off()`, `reach_delivery.no_refetch()`. A query returns the first answer that is not None.

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
searched for it (no belief = the run-wide count, as before). Key `enabled` (legacy `search_per_object`, `$ROBOT_AGENT_SEARCH_PER_OBJECT`). Hook `on_start` (lab
flag `search_per_object`); service `looks_made(run, obj)`. Module: `robot_agent/options/search_per_object.py`.

### `place_find`
A Find of a place is dropped (default off). The planner sometimes writes `MoveTo P, Find P, Open P`; a Find looks for a thing, and a look at a place key
(an ENV key) is always absent, which the run counts as a miss. With the option a Find whose object is an ENV key the robot is at (its belief) or has just
moved to (a MoveTo of the plan) is removed from the plan; a Find of a thing, or of a place the robot is not going to, is kept. Needs the pack's
`planner.action_roles` `move` and `find` (none declared = does nothing). Key `enabled` (legacy `place_find`, `$ROBOT_AGENT_PLACE_FIND`). Hook `on_plan`
(transform: the grounded plan before `plan_tidy`); lab flag `place_find`: `{place, index}`. Module: `robot_agent/options/place_find.py`.

### `reopen_on_miss`
A Find that misses inside the container just opened is made again after closing and opening it (default off). A door can report open and still not
show what is in it; the miss then says nothing about the object. With the option, when the Find right after an Open of the container the robot is at
fails `absent`, the pack's remedy runs on the container once (no miss is counted) and the Find is made again. Pack data: `planner.recovery.reopen_on_miss`
(a list of the pack's actions, e.g. `[Close, Open]`) and `planner.action_roles` `find` and `open`; any missing, or an action the robot lacks = does
nothing. Key `enabled` (legacy `reopen_on_miss`, `$ROBOT_AGENT_REOPEN_ON_MISS`). Hook `on_step_failed`; lab flag `reopen_on_miss`: `{container, object}`.
Module: `robot_agent/options/reopen_on_miss.py`.

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
profile gets no refusal. Hooks `on_goals`, `on_goals_met`, `on_finish`. Module: `robot_agent/options/hazard.py`.

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
