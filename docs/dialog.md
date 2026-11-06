# Dialog and understanding

How the robot asks the person, reads the answers and turns a task into goals.
Code: `robot_agent/interact/dialog.py` (asking, yes / no), `robot_agent/core/planning/understand.py`
(the understand LLM call, refusals, read-back), `robot_agent/core/planning/goals.py` (goal kinds and slots).
`core/planning/assist_loop.py` only calls them.

## Asking

`dialog.ask` puts the run in the "waiting on a question" state, so an answer typed by any client
(`RunManager.inject`, `POST /runs/{id}/inject`) answers the question instead of being queued as a new
command. The robot's Ask skill (from the manifest) only says the question and listens. `ask_heard` asks again
on silence (`silence_retries`). An `Answer` records where it came from. A typed answer (`source='typed'`) cannot
be misheard. An answer heard through the robot's microphone (`source='robot'`) can.

## Understanding fixes (SAGE-v2 P0.7: FROM_ME, ASR2, ASR1, UND_T0)

### Goals the words do not ask for (`core/planning/goal_filter.py`)

The understand call runs at temperature 0, so a misreading comes back on every call and a re-sample does
not hide it. Three are corrected from the person's own words, without an LLM call, on every understanding
(`AssistLoop._ground_understood`); the `understand` event lists them as `filtered`:

* a stated location is the source, not a goal: "The spoon is in the kitchen drawer. Bring it to the sofa."
  gave `in spoon -> drawer` next to `on spoon -> sofa` and the question "Where should the spoon go in the
  end?". When the words say "<object> is in / on <place>" and the object has another destination, the goal
  to that place is dropped and the other goal takes it as `from`. The same for a goal into the place
  another goal takes the object from, when the words never name it as a destination ("take the spoon out
  of the drawer, put it on the counter, and close the drawer": M03, H09, V09); "... then put it back in
  the drawer" keeps both for the validator. A goal to where the words say the object is, that they never
  ask for, is dropped also without another destination: `[in spoon -> drawer, go sofa]` for the H02
  sentence ran to a "success" with the spoon back in the drawer; now the read-back finds the spoon in no
  goal and re-samples / asks;
* a negated object is not a goal: a move goal for an object named only inside "don't ...", "except ...",
  "not ...", "without ..." (the English task) is dropped before grounding ("but don't touch the mug" asked
  "Where should I put the mug?");
* a `go` goal to the destination of a move goal is the same trip.

Nothing is dropped when it would leave no goal. `ROBOT_AGENT_GOAL_FILTER=0` (option `goal_filter`): off. Tests: `tests/test_v2_t2fix_goal_filter.py`, scenarios
TF03 / TF04 (`scenarios/tests/test_t1_v2_t2fix.py`).

### The speaker is a destination, never a source (FROM_ME)

The understand model sometimes writes `from: "me"` for "bring me the cup". Before this fix the grounding then asked
"Where are you?" for the pick. `goals.speaker_slots` runs on every understanding. A speaker word (`me`, `user`,
`you`, `here`, ...; `goals.SPEAKER_WORDS`) in `from` of an on / in goal is moved to an empty `to`, or dropped
when `to` is set. The source is then searched for or taken from the object memory. "Where are you?" is asked
only for a `to: "me"` whose place the person's words do not name ("... to me at the sofa" names it).

### The place the person said is remembered (m10 round 5)

An untracked speaker's answer to "Where are you?" names where they are. The place is kept for the run
(`AssistLoop._person_at`, per person). A later answer to another question ("what should I do?", a restated request)
is the latest answer, but it does not clear the place: before this, the next grounding found no place in the latest
answer and asked "Where are you?" again until the clarify cap ended the run. The place is used only after the
question was asked; an unasked remark never places the person.

### Unclear answers to a yes / no question (ASR2)

`dialog.read_yes_no(text)` returns a `Reply` whose `kind` is one of:

| kind | example | what happens in the read-back ("I will ... Is that right?") |
|---|---|---|
| `yes` | "yes", "네", "có" | goals settled |
| `no` | "no", "no, the sofa" | a bare no gets an open "What should I do?"; "no, X" is a correction |
| `correction` | "the one in the restroom", "화장실에 있는 거" | understood again with the correction |
| `unclear` | "yet", "yea", "uh hmm", "now", "내", noise | **asked again once**: the robot's i18n `yes_or_no` |
| `silence` | "" | the existing "no answer" end |

An answer is `unclear` when each of its words is a filler, a 1-2 letter fragment or a near-miss (edit
distance 1) of yes / no. A word with content makes it a correction. `dialog.ask_yes_no` (through
`run_control.ask_yes_no`) asks an unclear answer again `yes_no_reasks` times (default 1). The re-ask is the
phrase `yes_or_no` of the robot's i18n in the run's language. The core's English text is "Sorry, I didn't catch
that. Please answer yes or no."; KAAIR has a Korean one. If the answer is still unclear after that, it is
handled as a bare "no": the robot asks the open "What should I do?". The question does not lose its turn and
the noise is not understood as a new command.

`AssistLoop.yes_no` / `run_control.yes_no` are unchanged (`Reply.ok` gives the same verdict). The recovery
questions ("Shall I try again?") still treat anything but a clear yes as no.

### A refusal needs a typed reason (ASR1)

`understand.typed_refusal` decides each refusal. The model's free text (`reason`) never refuses by itself.

- **capability**: a goal needs an action the manifest has no skill for (`goals.missing_actions`). The robot says
  its i18n `cannot_do`.
- **capability / safety from the model's typed status** (`infeasible` / `unsafe`): this stands on the person's own
  command, or on a typed answer. After an answer heard through the microphone, it stands only when the person's
  words name something unsafe or impossible (`understand._MARK`: knife, window, throw, fire, pour, washer ...,
  plus the Korean words). If they name nothing like that, the refusal could only come from a
  misheard answer ("the rest broom" for "the restroom"). The misheard answer is then dropped from the dialogue,
  and the question is asked again: "Sorry, I may have misheard you. <the question>". The `clarify_rounds` limit
  still applies.
- **declined**: the person said no (kept as a kind for the refusals the dialog adds later).

The `done` event of a refusal carries `refusal: capability | safety` (`declined` is not used yet).

### A deterministic understand call (UND_T0)

`understand.ask_json(..., options=)` sends sampling kwargs with every call, the repair calls included. The
understand reading uses `understand_temperature` (default 0) and `understand_seed` (default 0). The consistency
re-samples (`understand_samples`, run only when the goals do not match the person's words) use
`understand_sample_temperature` (default 0.7) with seeds `seed + 1`, `seed + 2`, ..., so they differ from each
other and are still reproducible. The clients pass `temperature` / `seed` on: Ollama (`LLamaClient`, the pool's
`PoolLLM`) in `options`, OpenAI as request fields, Gemini as `generation_config.temperature`. The run record
logs both (`observe.llm`).

| config key (`/ws/agent` options) | env | default |
|---|---|---|
| `understand_temperature` | `ROBOT_AGENT_UNDERSTAND_TEMPERATURE` | `0` (`''` = the server's default) |
| `understand_seed` | `ROBOT_AGENT_UNDERSTAND_SEED` | `0` (`''` or negative = no seed) |
| `understand_sample_temperature` | `ROBOT_AGENT_UNDERSTAND_SAMPLE_TEMPERATURE` | `0.7` |
| `yes_no_reasks` | `ROBOT_AGENT_YES_NO_REASKS` | `1` (`0` = no re-ask; an unclear answer then counts as a bare "no") |

Tests: `tests/test_v2_dialog_*.py`. Scenarios: `scenarios/tests/test_t1_v2_understand_dialog.py`
(FROM_ME, FROM_ME2, FROM_ME2ko, ASR2, ASR2noise, ASR2b, ASR2c, ASR2d, ASR1, ASR1b, ASR1c, UND_T0, UND_T0b).

A read-back correction that names a new destination ("Put the apple on the dining table", then "on the
kitchen counter") replaces the destination it read back: the goal read-back (`goal_readback`) no longer counts
the task's mention of that place (`understand.without_places`). ASR2c and GR06 run with `goal_readback` on.

### A restated request replaces a garbled first hearing in the object check (m10 round 6)

The object-coverage check of `find_issues` reads the task's own words. After a first hearing the recogniser garbled
("the chicken bring"), a clean answer that said the whole request again was understood, but the check still read the
garbled original and re-asked. `AssistLoop._object_source` now reads the latest answer that restates the request
(`_restates`: most of the original's words again) in place of the first hearing. Short answers do not replace it.

### Dialogue convergence (R4b item 2, `core/planning/dialog_converge.py`)
Failure class: clarify and read-back loops that end in "I could not confirm with you what to do". Causes found
in the failing dialogues of a 50-task slice (not task ids):

1. A correction after a "no" rewrote every goal. `pin_to` sent ALL move goals to the destination the correction
   named, and the model, asked again with the correction in the dialogue, moved the other object too. The person
   fixed one goal and broke another, again and again.
2. A reading the person had refused was read back again (the request said again gave the same goals).
3. The caps counted every read-back and every question, also those that changed the goals.
4. A question reworded only in politeness passed the word-for-word check.

What the loop does now (option `dialog_converge`, env `ROBOT_AGENT_DIALOG_CONVERGE`, default on; `0` = the earlier loop):

- **A correction edits the goal it names.** `correction_edit` reads one object (the goals' own names, one letter off
  allowed, the Korean names the loop attached by their head syllables) and one destination (an ENV place, or the
  speaker) from the correction. Only that goal is grounded again (a person's hand-over, a surface beside them) and the
  edited goals are read back next: no new understanding of the task, so one model call less. Several objects, no
  destination, two places, or a speaker the ENV cannot place: the earlier path.
- **A correction the goal already satisfies** ("the lamp on the shelf" when the lamp is going to the shelf) may be a
  misheard "no": the goals are read back once more; the second time the person is asked a choice ("what I take,
  where I put it, or who it is for"; phrase `q_which_part`).
- **A refused reading is not read back again.** The goal set is keyed (`goal_sig`); a set that was refused earlier
  gets the same choice question once, and the run ends as "not confirmed" if it comes back a third time. A bare
  "no" to a reading refused before also asks the choice instead of "what should I do".
- **The caps count what gained nothing.** Each read-back whose goals differ from every earlier one earns one more
  read-back (two at most); a question whose answer gave goals not seen before gives its round back (twice at most).
- **A near-duplicate question** (the same content words, other politeness) is reworded once, like a repeat.
- The read-back no longer says "from the you" for a source that is a person.

Not done: executing the goals the person never contradicted before the rest is confirmed. It needs the plan phase
split in two with a reversibility rule for what may move before confirmation; the failing dialogues show wrong
goals (a wrong object, place or recipient), not right goals left unconfirmed, so the gain is unmeasured.

Generality: objects are the goals' names, places are the ENV, the speaker words are `persons.SPEAKER_FORMS`.
The Korean phrase for `q_which_part` comes from the robot pack's i18n; a pack without it gets the translator.

### Two objects fit the words: which one? (R4b item 3, `core/planning/which_one.py`)

A Find that sees more than one object fitting its query may put `candidates` in its result: short descriptions of
the instances in view, the way a person tells them apart ("red mug", "blue mug"), never an internal id. A pack whose
Find has no `candidates` is not affected. After such a Find, when a Pick of the same object follows in the plan:

1. The candidates are narrowed by the words the person used for the object (`said`). One left: it is taken, no
   question.
2. Otherwise one question per object and run: "I see more than one mug here: the red mug or the blue mug. Which one
   do you mean?" (`q_which_one`; in another language the whole sentence is translated). It is asked outside the
   `clarify_rounds` count, so it is never lost to the cap. The answer is read by the words that tell the candidates
   apart; when they do not decide ("the second one", an answer in the user's language) one model call chooses
   (`where="which_one"`, under the run's LLM budget). An answer that chooses nothing leaves the robot as it was;
   the question is not asked again.
3. From then on the Find and Pick of that object get the chosen description as the skill input (the plan's object
   name stays, so goals and memory are unchanged); a Find that had seen the other instance is repeated.
