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

## Cooperative points

The executive calls these between steps (not inside a skill):

- `run_control.wait_if_paused()` blocks while the run is paused. A cancel always wakes it.
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

1. STOP / PAUSE / RESUME by keyword, never through the LLM. English, Korean and Vietnamese. The keyword
   counts only in a short utterance (at most 4 words) that, after fillers such as "please" or "robot" are
   dropped, *is* the phrase, optionally followed by tail words ("now", "it", "đi", "nhé"). A negation
   ("don't", "đừng", "-지 마") blocks it. So "bus stop", "stop at the door", "stopwatch", "đi thôi" and
   "멈추지 마" are not stops. PAUSE is checked before STOP because "tạm dừng" and "일시정지" contain a
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

## Not done yet

- **Applying goal changes.** MODIFY / ADD / DROP / NEW are queued on `Run.pending` but no executive
  consumes them yet (`Run.take_pending()`). A second client's command is therefore recorded and
  reported to it as `queued`, not executed. This is deliberate: no parallel run.
- **Task stack.** The `TaskStack` protocol (`push` / `pop` / `peek`) is the interface for suspending a
  run on NEW and resuming it after `reconcile()`. `RunManager(task_stack=...)` accepts one; nothing
  implements it.
- **Dialog.** Dialog is still in kcare's `ask` skill. `Run.ask()` is the hook it will move to, so that
  an interjection during a question is routed as ANSWER.
- **Pause mid-skill.** Pause holds between steps only. A non-interruptible skill and a held object need
  the policy of proposal 3.3.
- The thread binding is best effort. If another `begin_run()` (a skill command) runs between `start()`
  and the run's own `begin_run()`, that thread is bound instead. The executive then falls back to "the
  active run", which is the same run with one robot per process.
