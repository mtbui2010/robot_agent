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
| `speaker` | `true` for the person who gives the task; without it, the person entry whose aliases hold a speaker word ("me", 나한테) |
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
| `planner.go_steps`, `planner.kind_anchor`, `planner.name_canon` | `0` (default) or `1` each (option > `$ROBOT_AGENT_GO_STEPS` / `_KIND_ANCHOR` / `_NAME_CANON` > manifest): grounding chain v2, three deterministic fixes of the grounded goals (no LLM call). `go_steps`: a `go` goal that is only a step (to a person, the speaker, a `say` listener, or the person-named source of a later move) is dropped while a non-`go` goal remains. `kind_anchor`: an `open` goal is dropped when the person's words carry no open verb (`goal_fix.OPEN_WORDS`, extended by the pack's i18n `open_words`) and another goal remains; a robot without Open is not touched. `name_canon`: an English object name that is not in the object memory (plural, head noun) is mapped onto a memory name (the original stays in `said`, `from` is the remembered place); several remembered places give a which-one question Run-language rule for `kind_anchor`: the open words are DATA, read for the RUN language: the language pack's `words.open_words` (`robot_agent/core/planning/lang/<code>.json`, shipped for en and ko) plus the robot pack's i18n key `open_words` for any language; with no open-word data for the language nothing is dropped (a trusted English reading that differs from the original is checked instead). The shipped Korean and English patterns overlap the idea of the simulated user's open-verb rule in homeloop_robot (`\bopen`, `열(?!쇠)`); the robot's Korean morphology is its own. An English paraphrase with no "open" word ("pop the fridge door") loses its open goal when the packs list no word for it. `planner.kind_anchor_ask` (`0` default, `1`; option > `$ROBOT_AGENT_KIND_ANCHOR_ASK` > manifest; acts only with `kind_anchor`): instead of dropping that goal it is kept and the ordinary read-back names the doubt ("I am not sure you asked me to open the ..."), so the person decides; no new question kind. |
| `planner.exec_integrity` (family), `planner.gate_strict`, `planner.endstate_check`, `planner.reset_container`, `planner.wait_on_robot` | `0` (default) or `1` each (option > `$ROBOT_AGENT_<NAME>` > manifest; the family sets the four): `gate_strict` repeats the gate's symbolic repair with a per-goal score when it left a critical finding and keeps it only if it certifies, and binds a plan object to the one goal object whose word tokens strictly contain it (`planner.label_tokens: none` = exact match only); `endstate_check` makes the return to the last `go` goal a retried, checked step; `reset_container` handles a BLOCKED PutIn into a believed-open container (put down at the pick place, Close, Open, fetch, PutIn; once per container); `wait_on_robot` runs `planner.recovery.wait: {action: <skill action>, schedule_s: [..]}` in WAIT_HEALTH (no declaration: the old poll) `reset_container` is pack data: it runs only where the manifest declares `planner.recovery.blocked_putin` (a list of actions run on the container, e.g. `[Close, Open]`, after the held object is put down; no declaration = the option does nothing), only when every spliced action is in `plan_actions`, and without the put-down when the robot has another free hand. The remedy mirrors a mock robot's partial-open state machine when declared for it: report it as an ablation (homeloop_bench docs/experiments.md). `endstate_check` re-runs the action that achieved the go goal in the completed plan (MoveTo, or the pack's Dock after a MoveTo), `planner.recovery.return_tries` times (default 3). `wait_on_robot`'s schedule is manifest-declared; no declaration = no-op. |
| `planner.inventory_ask` | `0` (default) or `1`: `name_canon` made general. `planner.canonical_lang` (`en` default, `none` skips the rule, other codes use the pack's i18n key `plural_fold`, `suffix>replacement` pairs joined by `|`) names the language of the core's object labels; the user's words narrow the remembered instances first, else one closed which-question. Instances are counted, not names: memory names of one instance (the same record `instance` / `id` / `sighting`, or names related by the pack's i18n key `object_aliases`, `{lang: ["mug\|coffee mug", ...]}`, the first phrase canonical) are one candidate, listed by the shortest name. The question declares its kind (`speech.LocalText.ask` = `{qtype: which_object, options, obj}`, read with `speech.declared_ask`) The question's declared kind is the core's own schema `{intent: choose, options, about}` (`speech.declared_ask`), not a host's qtype list; a host maps it (homeloop_bench: `INTENT_TO_QTYPE`). The options also come as a list slot `options`, said with the pack's `sep_or` phrase, so a localized `q_which_known` can use `{options}`. The result depends on how the host's memory names objects (class and colour+class names, a name shared by objects of different usual places removed): records with `instance` / `id` are supported and should be tested too. |
| `planner.reask_abstain` | `0` (default) or `1`: a reading with no goals whose own question asks where an object IS (not where to put it) is understood once more, with a note that the robot finds objects itself (its Find skill). The new reading is taken when it has goals, covers the named objects and is not `unsafe`; else today's question stays. +1 call, once per run |
| `planner.dialog_unstick` | `0` (default) or `1` (option > `$ROBOT_AGENT_DIALOG_UNSTICK` > manifest): three ways a dialogue ended or went round in circles. (1) The person said "no", then the whole request again, and it reads the same as the refused reading: that reading is read back once more (lead phrase `rb_restated` before the ordinary read-back `q_confirm`, so a pack that has `q_confirm` says the summary without a translator call) instead of asking which part is wrong and stopping at the next "no". (2) A "not sure" answer to a where-is question of the model: the task is understood once more with the robot's Find stated (+1 call, once per run). "Not sure" is data: the language pack's `answer_unsure` phrases plus the robot pack's i18n key `answer_unsure` (`{lang: [phrases]}`, substring match); a language without data falls back to "a short answer that names no place of the ENV" only when the manifest declares the numbers (below). (3) A question that opens with its own apology (`speech.APOLOGY_KEYS`) is not asked again behind the silence apology. Update: a restatement is read back once more only when it names an ENV entity the request did not (`dialog_unstick.adds_entity`); a bare restatement re-draws the same reading and is not asked again. The structural "not sure" fallback (short answer naming no place) applies only to a language WITHOUT `answer_unsure` data; English has data, so "Yes." is not a not-sure. The numbers are manifest data, `planner.unstick`: `fallback_words`, `fallback_chars` (the structural not-sure fallback), `restate_frac`, `restate_word_len`, `restate_min_words`, `restate_min_bigrams` (what counts as saying the request again). A number that is missing switches that part off. The language-layer fixes of round 3 (lead part dropped from a joined question, a read-back keeps its kind in the last resort, the pack's `non_ascii_min` for a Latin-script language) act only with `dialog_unstick` or `inventory_ask` on. |
| `limits.payload_kg`, `limits.max_object_size_m`, `limits.objects` | what the body can carry, and the robot's own object-class estimates `{name: {weight_kg, size_m}}`; a goal over a limit is refused |
| `safety.hazards` | robot-pack hazard rules `[{objects: [regex], places: [class or regex], say}]`, added to the core rules |

## AssistLoop options (per run)

`sage_subgoals` (goals | model), `gate_complete` (0 | 1), `hazard_rules` (core | pack | off), `person_seat_m`
(1.0, a priori), `person_surface_m` (1.2, a priori), `search_legs`, `search_total`, `detector_miss_rate`, `translate_model`,
`translate_url`, `translate_budget`, `input_pivot`, `person_goals` (0 | 1), `accept_goals` (0 | 1), `go_steps`, `kind_anchor`, `name_canon`, `reask_abstain`, `dialog_unstick` (0 | 1 each).

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

## Language packs and the flags' data (lab round 6)

No language, robot, benchmark or simulator fact is a constant in the code of the lab flags. What the planner knows about a
language is a pack, `robot_agent/core/planning/lang/<code>.json` (`langdata.py`): `words` (the lists of `lexicon.py` and
`open_words`, `where_now` / `where_put` / `where_obj` for the model's own English question), `answer_unsure`, `articles`,
`plural_rules`, `particles`, `name` / `register` (a translate target), `script` or `non_ascii_min` (what counts as said
in the language). English and Korean ship; `ja` and `zh` only name themselves. A robot adds words to any language with its
i18n phrase of the same key (`|`-separated). Numbers are the manifest's `planner` section: `unstick.*` (above),
`recovery.return_tries` (an integer >= 1; the tries of `endstate_check`), `recovery.wait`, `recovery.blocked_putin`.
Rule: data missing means the flag does nothing, for that language or that robot. No other language is claimed.
