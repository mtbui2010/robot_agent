# The ENV contract and the SAGE-v2 options

The ENV (`robot_agent.skill_configs.ENV`) is the robot's map: one entry per place, keyed by its name
(`"counter@kitchen"`). The core reads it through `assist_loop.Places`. Every field below except `loc` is
optional; an ENV without the optional fields keeps the earlier behaviour (pinned by
`tests/test_sage_v2_step4_persons.py::test_env_without_person_fields_unchanged`).

## Place entries

| field | meaning |
|---|---|
| `loc` | `{x, y, rz}` where the robot stands for the place |
| `aliases` | other names, in any language (the robot says the first one in the user's script) |
| `openable` | the place opens (a drawer, a fridge) |
| `surface` | `true`: a support surface things can be put down on (table, shelf, counter); `false`: a seat, a bed, the floor. Without the field anywhere, every place that does not open counts |
| `category` | an optional class name the robot's map knows (read by `core/safety/place_class.py`) |

The core never reads a `tags` field.

## Person entries (SAGE-v2, new optional contract)

| field | meaning |
|---|---|
| `person` | the entry is a person (its id), not a place |
| `speaker` | `true` for the person who gives the task; without it, the person entry whose aliases hold a speaker word ("me", 나한테, tôi) |
| `loc` | where the robot LAST SAW the person (its perception; may be stale). No `loc`: the person is not tracked and the robot asks "Where are you?" |
| `seat` | where the person sits or lies (a place key), when known |
| `spot` | a reachable support surface beside the person (a place key); `null` = none (a hand-over); absent = the robot pack has no such perception (the nearest support place within `person_seat_m`, else a hand-over) |

A destination in the speaker's words ("me", "here", 나한테) grounds to the speaker's entry. For an untracked
person, the answer to "Where are you?" is mapped onto the map: the support surface nearest to the named place in
its room (within `person_surface_m`), never the seat or bed itself; with none, a hand-over. A hand-over (a manifest
action whose effects include `hand_empty()`, `RobotView.release_actions`) at a person reaches a delivery to that
person; the words asking for the hand ("hand me", 손에, 건네) make it the only way.

## Manifest keys

| key | meaning |
|---|---|
| `planner.llm_budget` | the run's cap on core LLM calls (option > `$ROBOT_AGENT_LLM_BUDGET` > manifest > 20) |
| `planner.translate_model`, `planner.translate_url` | the model (and server) that translates what the robot says; default the planner's |
| `planner.translate_budget` | the translator's own cap per run (default 10): one call per user utterance (kept while more than 2 calls are left) and what the robot says |
| `planner.input_pivot` | the language layer's IN side for a non-English user: `none` (original only), `translate` (English translation; original only as the source of `said`), `both`; default `none` (chosen by the m10 KO comparison) |
| `planner.detector_miss_rate` | the detector's false-negative rate, 0 to 0.9, default 0.1: after a miss at a place the object is still probable there (docs/search.md) |
| `planner.search_legs`, `planner.search_total` | SAGE-v2 search: the belief's next look replaces a failed Find without a replan; after "I don't know" the likely places are searched, up to `search_total` looks |
| `planner.person_goals` | `0` (default) or `1`: a request that mixes go / say goals with moves is planned with the template goals as the loop's own fixed steps (the planner expands the moves only), a hand-over goes to the person its goal names, a repeated goal is dropped, an Open of what cannot be opened is removed. Option `person_goals` > `$ROBOT_AGENT_PERSON_GOALS` > manifest. Needs a planner with `fixed_subgoals` (embodiplan >= 0.2.7) for the fixed steps; the other parts do not |
| `planner.accept_goals` | `0` (default) or `1`: the caller may hand a run its goals (`AssistLoop.run_blocking(task, lang, goals=[{type, object, from, to}, ...])`); the understand step is then skipped (no LLM call, no question, no read-back, no refusal) and the run starts from those goals, grounded on the ENV (a place name to its key, the speaker, the remembered source) and not filtered. Without goals, or with the option off (goals passed in are ignored), the usual understanding runs and nothing changes. Option `accept_goals` > `$ROBOT_AGENT_ACCEPT_GOALS` > manifest. A caller that knows the answer (a test of the later phases, a planner of a higher layer) uses it; the `understand` event then has the reason `goals given by the caller` |
| `limits.payload_kg`, `limits.max_object_size_m`, `limits.objects` | what the body can carry, and the robot's own object-class estimates `{name: {weight_kg, size_m}}`; a goal over a limit is refused |
| `safety.hazards` | robot-pack hazard rules `[{objects: [regex], places: [class or regex], say}]`, added to the core rules |

## AssistLoop options (per run)

`sage_subgoals` (goals | model), `gate_complete` (0 | 1), `hazard_rules` (core | pack | off), `person_seat_m`
(1.0, a priori), `person_surface_m` (1.2, a priori), `search_legs`, `search_total`, `detector_miss_rate`, `translate_model`,
`translate_url`, `translate_budget`, `input_pivot`, `person_goals` (0 | 1), `accept_goals` (0 | 1).

## What the robot says

Fixed questions and read-backs are `core/planning/speech.py` phrases (`CORE_PHRASES`, English); a robot pack gives
them in other languages in its i18n under the same keys. A phrase the robot has in the user's language is said
without an LLM call; free text goes to the translator, whose output is checked (`speech.guard`) and retried once;
when that fails the English text is said and counted (`say_not_in_lang`). ENV keys are said as names
(`speech.say_name`), never raw.

## Language layer (`core/planning/language.py`)

A non-English user is an I/O layer around an English core. `LanguageAdapter` (`loop.language`) does both sides.
IN: each user utterance is translated once (cached, counted in the translate budget); the original stays the task
for the coverage check, the read-back and `said`. A translation is distrusted when the translator says `UNCLEAR`
(ASR noise), is not English, or loses an ENV place or person the original names by alias: the robot then asks to
repeat, once per run. OUT: every text the user hears passes `speak()` (the run's `ev` calls it for each `say`).
The pack's phrases cost no call; an English slot is translated alone; raw ENV keys are said as names; an English
reason after a pack line is dropped. When nothing works the user hears the pack's `q_unclear_task` (question) or
`say_untranslated`, never English. English users: no call, no change.
