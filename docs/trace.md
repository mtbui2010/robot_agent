# Run trace (option `trace`, schema 1)

One JSONL file per run, `<dir>/<run_id>/trace.jsonl` (`dir`: key `trace.dir` / `$ROBOT_AGENT_TRACE_DIR`, default the folder of the run's
`task_runs` log). Written only with the option on (`trace=1`, `$ROBOT_AGENT_TRACE=1`, `planner.options.trace.enabled`). Built from what the
engine already makes: no extra LLM or robot call, no event added or changed. Append-only, one flush per record; a write error logs one
warning and the run goes on without its trace. Module `robot_agent/options/trace.py`; tests `tests/test_option_trace.py`.

Every record: `v` (schema), `k` (kind), `t` (seconds since the header), `ts` (epoch seconds: join with the robot, vision and model
server logs). Kinds:

| `k` | fields |
|---|---|
| `header` | `schema`, `run_id`, `epoch` (t = 0), `task`, `lang` |
| `event` | `ev` (the event name), `step` (index of the last step started), `data`: every field but `world` (kept on `done`) and `say_audio`. Yielded events, events of non-flow hooks (`svc.emit`), and the live pushes `step_log` (a skill's `log_data`; `log_image` is a base64 string, so it becomes its length) and `plan_step` (SAGE progress) when a host channel exists |
| `lab` | `flag` (the option), `step`, `data` (the lab_log fields; also past lab_log's cap of 50) |
| `llm` | `purpose`, `source` (`robot_agent` / `embodiplan`), `model`, `t` / `ts` = start, `ms`, `prompt_chars`, `reply_chars` (images left out), `error`, `step`, `phase` |
| `recovery` | after each failed step: `step`, `action`, `object`, `code` (FailureCode), `rule` (the robot policy's rule for it: `action`, `retries`, `then`, `retry_allowed`, `mark_searched`, `note`), `reason`, `chose` (`recover:<kind>`, `replan`, `ask`, `search`, `retry`, `next_step`, `stop:<status>`) and that event's `msg` |

Values: numbers, bools and strings up to 4096 characters are kept (unicode as is); a longer string is `{_len}`, bytes `{_bytes}`, an array
`{_shape, _dtype}` (its values when it has at most 16), a list over 64 items `{_list, head}`.

`purpose` is the label of the latest LLM-budget take before the call (`LLMBudget.take(what=)`: `understand`, `translate`, `plan`,
`replan`, `planner` (each SAGE call), `plan edit`, `plan refinement`, `search`, `which_one`, `request coverage`, `option`, ...), used up by
the call; a call with no take before it (a VLM verify, a skill's own call) is `other`. SAGE labels all its calls `planner`: `phase` (the
last `plan_step` phase, when the host streams them) is `expand` / `refine` before those calls and empty before decompose. A
`plan_decompose` / `plan_expand` purpose needs embodiplan's `_chat` to pass its prompt key as `what` (not done).

Not in the trace: calls that do not pass `observe.llm.call` (a bench or test client that is not an `LLMobj`: wrap it in
`observe.llm.RecordingLLM`); a `recovery_rule` override of option `hw_wait` (the base rule is shown); the robot package's own calls
(detector / vision-server requests, grasp checks): robot_agent does not make them, so they show only as far as a skill returns or logs
them (`log_data({"msg": ...})` becomes a `step_log` record).
