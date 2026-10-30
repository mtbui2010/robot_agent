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

### The speaker is a destination, never a source (FROM_ME)

The understand model sometimes writes `from: "me"` for "bring me the cup". Before this fix the grounding then asked
"Where are you?" for the pick. `goals.speaker_slots` runs on every understanding. A speaker word (`me`, `user`,
`you`, `here`, ...; `goals.SPEAKER_WORDS`) in `from` of an on / in goal is moved to an empty `to`, or dropped
when `to` is set. The source is then searched for or taken from the object memory. "Where are you?" is asked
only for a `to: "me"` whose place the person's words do not name ("... to me at the sofa" names it).

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
  plus the Korean and Vietnamese words). If they name nothing like that, the refusal could only come from a
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

Known gap: when a read-back correction replaces a place the task named ("Put the apple on the dining table"
then "on the kitchen counter"), the goal read-back (`goal_readback`) still finds the dining table in the task
and asks about it. ASR2c runs with `goal_readback` off for that reason.
