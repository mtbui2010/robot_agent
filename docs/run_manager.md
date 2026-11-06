# Run manager and intent router

Code: `robot_agent/interact/run_manager.py`, `robot_agent/interact/intent_router.py`.
Shims: `robot_agent/core/run_control.py`. API: `robot_agent/api/agent.py`.
Tests: `tests/test_run_manager_*.py`.

## What changed

Before, a run had no identity. The cancel flag was process-global, `begin_run()` cleared it at every
entry (so a skill command or a second client could wipe a Stop pressed for a run still finishing), and a
second `/ws/agent` connection started a second plan in parallel on the same robot.

Now each robot has at most one active `Run`:

| field | meaning |
|---|---|
| `run_id` | `YYYYmmdd-HHMMSS-xxxxxx`; on every event the agent emits, in `GET /runs` |
| `robot_id`, `user_id`, `role` | who started it; role is `owner` > `member` > `guest` |
| `state` | `running`, `paused`, `cancelling`, `done`, `failed` |
| `goal` | the prompt that started it |
| `events` | the last 1000 events (agent events and run-manager notices) |
| `pending` | MODIFY / ADD / DROP / NEW events waiting for the executive |
| `outcome` | `success`, `failed`, `aborted`, `error` once finished |

`RunManager.start()` while a run is active does not start another. It returns the active run with
`decision='busy'`; the caller observes that run and sends its text in with `inject()`.

`RunManager.on_start` / `on_finish` (lists of callables, like `on_cancel`) run after a run is
created and after it finished; `run_control.manager()` hooks the RunRecord in there (`observe.md`).

## Cooperative points

The executive calls these between steps (not inside a skill):

- `run_control.wait_if_paused()` blocks while the run is paused. A cancel always wakes it.
- `run_control.pause_point(on_resume)` is `wait_if_paused()` plus a resume hook: after a real pause
  (not a cancel) `on_resume(seconds_paused)` runs in the executive thread. The planning loops use it to
  reconcile the belief (`RobotView.reconcile`, a `reconcile` event when it changed); AssistLoop also
  looks again before a Pick that would act on a Find made before the pause.
- `run_control.cancel_requested()` / `check_cancel()` (the second raises `RunCancelled`).

`wait_if_paused()` was added before the step-level cancel checks in `unified_agent.run` / `run_direct`,
`planning/loop.py` and `planning/assist_loop.py`. A pause therefore takes effect at the next step
boundary. A cancel stops the robot at once: `RunManager.on_cancel` is hooked by `run_control` to
cancel the ROS goals in flight and release blocked listens, the same as the Stop button.

## run_control shims (unchanged names)

`begin_run`, `request_cancel`, `cancel_requested` and the dialog functions keep their signatures. They
act on the current run: the run the thread was bound to by `begin_run()` (the first `begin_run` after
`start()` binds the run's executive thread), else the robot's active run. With no run (a skill started
from the CLI) the old process-global flag is used, as before.

- `begin_run()` no longer clears anything while the active run is being cancelled.
- `request_cancel()` (dashboard Stop, `/agent/cancel`, a client going away) cancels the active run
  without a role check, then stops the robot as before.
- New: `wait_if_paused()`, `check_cancel()`, `current_run()`, `manager()`.

## Intent router

`IntentRouter(llm=None).classify(text, awaiting=None, goal='') -> IntentDecision(intent, text, source,
confidence, reason)`. The order is:

1. STOP / PAUSE / RESUME by keyword, never through the LLM. English and Korean. The keyword
   counts only in a short utterance (at most 4 words) that, after fillers such as "please" or "robot" are
   dropped, *is* the phrase, optionally followed by tail words ("now", "it"). A negation
   ("don't", "-지 마") blocks it. So "bus stop", "stop at the door", "stopwatch" and
   "멈추지 마" are not stops. PAUSE is checked before STOP because "일시정지" contains a
   stop word.
2. ANSWER when the run is waiting on a question (`Run.ask()` / `Run.set_awaiting()`).
3. MODIFY / ADD / DROP / NEW / STATUS from the injected `llm(text, {'goal': ...})` callable. It may
   return a string, a dict `{'intent', 'confidence', 'reason'}` or an `IntentDecision`. If the LLM is
   missing, raises, returns something unknown, or returns a control intent, a deterministic keyword
   fallback decides instead.

The router only decides. `RunManager.inject(run_id, text, user_id, role)` routes the decision:

| intent | action |
|---|---|
| STOP | `cancel` (robot stopped now) |
| PAUSE / RESUME | `pause` / `resume` (idempotent) |
| ANSWER | delivered to the waiting `Run.ask()`; if no question is pending any more, it is queued as NEW |
| STATUS | returns `run.to_dict()`; never interrupts, open to every role |
| MODIFY / ADD / DROP / NEW | appended to `Run.pending` (`action='queued'`) |

## Roles

A user whose role is lower than the role of the run's starter cannot cancel, pause, resume, answer for
or change that run. They get `action='refused'` with a reason. A `run_refused` event is published on the
run, so the owner's dashboard sees the attempt. A request without a role counts as `owner`, which keeps
the old behaviour for clients that do not send one. The dashboard Stop button (`/agent/cancel`) is not
role-checked.

## API

- Every event on `/ws/agent` now has `run_id`. The first message may carry `user_id` and `role`.
  Nothing was renamed.
- During the run a client may send `{"text": "..."}`, which is injected. The reply is
  `{"event": "run_injected", "action", "intent", ...}`. `{"cancel": true}` and disconnecting still
  cancel, as before.
- A second client while a run is active gets `{"event": "run_attached", "observer": true, "run_id",
  "run", "injected"}`. Its prompt is injected, it then receives that run's events, and it is closed when
  the run ends. When it disconnects, the run is not cancelled.
- Run-manager notices: `run_state` (state changes), `run_input` (someone said something),
  `run_refused`.
- `GET /runs` returns `{"active", "runs"}`. `GET /runs/{id}?events=true`. `POST /runs/{id}/cancel`,
  `POST /runs/{id}/pause`, `POST /runs/{id}/resume` and `POST /runs/{id}/inject` take the body
  `{"user_id", "role"[, "text"]}`. They return 403 when the role is refused, 404 for an unknown run and
  409 for a run that has ended.

## Goal changes during a run (MODIFY / ADD / DROP / NEW)

`AssistLoop` takes `Run.take_pending()` at every step boundary (after `wait_if_paused()` and the cancel
check, before the next step or the end-of-plan check), so a change said during a skill takes effect when
that skill ends. Code: `AssistLoop._apply_pending` and `core/planning/goal_change.py`.

1. Each utterance is read by `goal_change.read(text, intent, goals, places, holding)`: plain English
   edits ("put it on the counter instead", "also bring the mug to the dining table", "forget the
   apple", "don't bring it, put the apple back on the desk"). `it` is the current goal (the object in the
   hand, else the first goal not reached). Places are grounded on the ENV; a phrase that does not name
   exactly one place, or a clause it cannot read, gives None.
2. None -> the understand LLM reads the change against the task (the dialogue plus the read-back of the
   current goals). Still nothing -> a `goal_change` event with `applied: false` and a sentence for the
   person ("Sorry, I could not apply that ..."): a change is never dropped silently.
3. Applied -> a `goal_change` event (`added`, `removed`, `changed`, `goals`, `say` = the new read-back) and
   a SAGE suffix repair from the current step (failed step `{"action": "Change"}`). Goals that already
   hold end the run there.

| intent | effect |
|---|---|
| MODIFY | goal diff (a destination changed, a goal swapped by the LLM), then local repair |
| ADD | merged into the goal set (after the current goals) |
| DROP | goals removed; the object in the hand first: it is put back where it was taken (`drop_held: put_back`, default) or down here (`place_here`) |
| NEW | someone who outranks the run's starter goes first; else the robot asks "Should I do that first? If not, I will do it after the current task." (no answer: after). After = appended to the goals. First = the current task is suspended on the `TaskStack` (goals, achieved goals, belief); an object in the hand is put down here first; when the new goals hold, the task is taken back (`resume_task`), the world is reconciled and the plan repaired. Goals already achieved stay achieved. A NEW that repeats a current goal is answered "I am already doing that". |

`ClosedLoop` (plans from the task text, has no goals) takes the pending events too and says each one was
not applied ("I will finish it first; please tell me again afterwards").

`TaskStack` (`interact/run_manager.py`): `push(robot_id, TaskFrame)`, `pop(robot_id, run_id=None)`,
`peek`, `frames`, `drop_run`. In memory; a finished run's frames are dropped. `RunManager.suspend(run,
frame)` / `resume_task(run)` publish `task_suspended` / `task_resumed`; the shims are
`run_control.suspend_task(...)` / `resume_task()`.

`RunManager.on_resume` (list of callables, like `on_cancel`): called with the run when it carries on
after a hold, i.e. a pause ended (`resume` of a paused run) or a suspended task was taken back. It is
the place for a reconcile (observe again before acting).

## Dialog: the robot's questions are on the run

`interact/dialog.py` owns the question state; the robot's Ask skill (manifest `Ask`) only does the I/O
(say, listen, show on the screen). `dialog.ask(run, question, io)` sets `Run.set_awaiting(question)`,
runs `io` in a worker and returns the first of: the robot's answer (`source='robot'`), an ANSWER injected
into the run by any client (`source='typed'`; the dashboard listen is ended so the skill returns), or a
stop (empty, at once). `ask_heard` asks again on silence; `yes_no` reads a confirmation. AssistLoop asks
through `run_control.ask_person(question, io, silence_retries)`. A read-back ("I will ... Is that
right?") follows only answers heard through the robot's I/O: a typed answer cannot have been misheard.

## Stop latency

- **Non-interruptible skills.** A skill whose manifest says `interruptible: false` (KAAIR: pick, place,
  open_drawer, close_drawer) runs inside `run_control.uninterruptible(skill)` (`run_skill(...,
  interruptible=False)`). A stop of the run that arrives meanwhile cancels the run at once
  (`cancel_requested()` is true, nothing new starts), but the robot's goals are cancelled only when the
  skill ends, at most `ROBOT_AGENT_UNINTERRUPTIBLE_MAX_S` (default 30 s) later. A `run_stop_deferred`
  event says so. The dashboard Stop button (`request_cancel`) and the hardware e-stop stay immediate.
- **LLM / planner calls.** `run_control.call_cancellable(fn, ...)` runs the understand call, SAGE's
  `generate_plan` / `edit_block` / `replan` in a worker and raises `RunCancelled` within 10 ms of a stop;
  AssistLoop ends the run `aborted`. The abandoned call finishes in the background and is ignored.

Measured on the scenario suite (T1, fake robot, 10 runs each, median / max):

| interrupt | robot stopped | run ended `aborted` |
|---|---|---|
| "stop" while driving (interruptible) | 0.7 / 1.1 ms | 2.4 / 3.9 ms |
| "stop" 50 ms into a 250 ms Pick (non-interruptible) | when the Pick ends: 200 / 202 ms | 201 / 206 ms |
| "stop" during a 1.2 s understand LLM call | (no motion) | 9.5 / 10 ms (was 1202 ms) |
| "stop" while the robot asks | (no motion) | 3.8 / 5.4 ms |

## Not done yet

- **Persistent task stack.** Suspended tasks live in memory, as long as their run.
- **Dialog in the robot.** kcare's `ask` skill still matches options and re-asks inside itself when it is
  given `options` / `retries`; AssistLoop passes `retries: 0` and no options, so the core decides.
- **Safe release.** A non-interruptible skill is waited for; there is no manifest "release" skill to call
  instead yet.
- **Pause mid-skill.** Pause holds between steps only.
- The thread binding is best effort. If another `begin_run()` (a skill command) runs between `start()`
  and the run's own `begin_run()`, that thread is bound instead. The executive then falls back to "the
  active run", which is the same run with one robot per process.
