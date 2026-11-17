# Changelog

robot_agent follows SemVer for its contract (`robot_agent.contract`, the
manifest format, the event schema). See `docs/contract.md`.

## Unreleased

### Editable "Go to" poses (`/poses`)
- A pose is a label + a PLAN of steps written like a shortcut button's (`lift::0.5&&movej::approach_drawer`), stored in `<common>/poses.json` (shared and merged like the buttons). The
  operator adds / edits / deletes ALL of them (`GET|POST /poses`, `PUT|DELETE /poses/{id}`); the robot package only SEEDS the first list (`POSES['seed']`, kcare: the `approach_*` of
  `ARM_CONFIGS`), `POST /poses/defaults` adds the missing seeds back. ONE speed for every pose (`PUT /poses/speed`, 0.05..1; `POSES['default_speed']`): each step of a speed skill
  (`POSES['speed_skills']`: movej / movel / movet) gets `speed=<s>` unless it sets its own. `POST /poses/{id}/run` runs the plan (teleop off first, answers when done); while an episode is
  being recorded the move is sent to the recorder as a `goto` mark. Replaces `poses` in `GET /teleop` (removed). 8 tests.
- Episodes: a Go to pose move is no longer cut by the suggested trim (the model should learn the init move): `summary.init_moves` lists it (pose name from the mark) and `summary.notes` says so.

### Look at and clean the raw VLA episodes (`/recordings`)
- Each row has `robot_mode` (left | front | right | null: from the episode's meta, saved by the recorder) and each episode of an export manifest carries it, so the export can use a crop per mode. 1 test.
- `DELETE /recordings/{day}/{ep}` moves an episode (with its edit list) to `<data>/trash/<day>/<ep>`: nothing is deleted for good, put it back by moving the folder back. Only finished (DONE) episodes. 2 tests.
- A move made while teleop was OFF (the dashboard's Go to pose during a recording) is detected (`summary.auto_motion`: the teleop state changes + the arm moving for > 0.5 s): it is not an
  operator demonstration (3-4 rad/s, no servo command), so the suggested trim now keeps the teleop part with the most movement (ep_0001: 10.7-29.3 s, not 6.3-29.3 s) and the servo-gap warning is not
  repeated for it. `POST /recordings/auto-clean {ids?, overwrite?}` saves that suggestion as the edit of every episode that has none (an existing label / note is kept). 3 tests.
- `core/episodes.py` + `api/recordings.py`, from the `RECORDER` config's `dir` (404 without it): `GET /recordings` (every finished episode with numbers that tell a good one from a bad one:
  rates, camera gaps, idle start / end and a suggested trim, gripper changes, teleop off in the middle of the movement; totals), `GET /recordings/{day}/{ep}` (curves, camera info),
  `GET .../frame?cam&t` (the nearest JPEG), `PUT|DELETE .../edit` (trim, label, exclude, note: saved in `<data>/edits/`, NEVER into the raw folder), `POST /recordings/export`
  (writes `<data>/exports/<name>.json`, the manifest `robot_teleop/tools/export_lerobot.py` turns into a LeRobot dataset; answers with the command). Ids are checked (no path leaves
  the data folder). 11 tests; tried on the first two real episodes (it flagged ep_0001: teleop off 4.3 s in the middle of the movement, servo command stopped 3.7 s).

### Record raw VLA episodes from the dashboard (`/recorder`)
- `POST /recorder/prompt` takes `avoid` (the sentence made last time): Generate pressed again gives another sentence whenever the fields allow one. 2 tests.
- `RECORDER` config group (kcare: `configs/tasks.py`; none = nothing shown): `{dir, teleop, prompts: {fields, templates, synonyms}}`. `GET /recorder` (state of
  `robot_teleop.recorder`, the prompt fields, `404` without the group), `POST /recorder/prompt {fields}` -> a RANDOM sentence from the robot's templates that uses every
  filled field (`core/prompts.py`; a template may be limited to some actions; a value may be replaced by a synonym), `POST /recorder/start {prompt, meta?}`
  (recorder idle, teleop function running, no run in flight: starts the recording AND the teleop function), `POST /recorder/stop {label: success|failure|discard}`
  (teleop off, then save / throw away). Reading sends nothing. 9 tests.

### Teleop functions on the dashboard (`/teleop`)
- A function can offer named poses to go to (`GET /teleop` -> `functions[].poses: [{name, label, skill, params}]`): its TELEOP entry has
  `poses: {from: <config group>, skill, param, prefix?, keys?, names?}` and the list is READ from that group of the robot's own config
  (kcare: `ARM_CONFIGS`, keys `approach_*`), so a new pose is added in the config only. `keys` names the modes a pose is keyed by (tells a pose
  from another table of numbers). The dashboard runs it with `POST /skill/<skill>` (which already switches teleop off). 2 tests.
- A teleop function (the `robot_teleop` repo: arm servo driven by a SpaceMouse, or any device / function that speaks its two JSON-in-String
  topics `/teleop/<name>/state` + `/command`) is listed from the `TELEOP` config group (`configs/tasks.py` or a site override; no group = nothing
  shown). `GET /teleop` (state, never switches anything), `POST /teleop/{name}/enable {on, confirm}` (on needs `confirm: true`, a running function
  and no run in flight), `POST /teleop/{name}/speed {scale}`. A run starting and Cancel switch every function off (`run_control._teleop_off`).
  `core/teleop.py` names no device and no robot; its transport is injected, so it is tested without ROS (`tests/test_teleop.py`).
### Site languages (`PUT /robot/languages`)
- The manifest says what the robot can talk in (`supported`); a site switches some off: `PUT /robot/languages` `{languages: [...]}` (a
  non-empty subset of `supported`, else 400) saves `<configs>/languages.json`. `GET /robot/languages` now returns `supported` too;
  `languages` is what the site has on, `language` the default (the first one on when the default was switched off). Skills that follow
  the user's language (hri) take `current_languages()['languages']`. A language the manifest lacks can never be added.
  `core/languages.py`: `site_languages`, `set_site_languages`, `current_languages`.
### Detect the user's language (`lang: 'auto'`)
- `/ws/agent` with `lang: 'auto'` starts the run in the manifest's default language and switches detection on for the run
  (`core/languages.py`: `start_run_language`, `run_language_auto`; cleared when the run ends). A skill that listens then lets Whisper
  hear the phrase unpinned: `utils.speech_to_text(auto=True, candidates=[...])` takes the language Whisper reports (the stt client's
  new `last_language`) when it is one of `candidates` and the phrase has at least 6 letters, else transcribes again pinned to the
  language in use; `last_stt_language()` gives it. `listen_dashboard(auto, candidates, info_out)` and `transcribe_listen_audio`
  carry it back to the skill.
- `GET /robot/languages` also says `declared`: False when the manifest names no language (`languages` is then `[]`; the dashboard
  offers only "detect"). `Manifest.language_declared`. Tests: `tests/test_detect_language.py`, `tests/test_robot_languages.py`.

### Shortcut-button groups
- A button has an optional `group` (a group id, `''` = Other); the groups are an ordered list in `<data_dir>/button_groups.json`
  (`[{id, name}]`), shared and merged like `buttons.json`. New `GET/POST /buttons/groups`, `PUT/DELETE /buttons/groups/{id}`,
  `POST /buttons/groups/reorder`; `POST /buttons` and `PUT /buttons/{id}` take `group`. Deleting a group moves its buttons to
  Other (they are not deleted); a button of an unknown group reads as Other. `core/button_manager.py`, test `tests/test_button_groups.py`.

### Text to speech as audio (`GET /tts`)
- `GET /tts?text=…&lang=…` returns the sentence as `audio/mpeg` (gTTS, `core/tts.py`, a 64-sentence cache; `lang` defaults to
  the manifest's language; 400 empty / over 600 characters, 503 when gTTS or the internet is missing). For clients that
  record the robot's voice: the dashboard's screen recording cannot capture the browser's speech synthesis on every
  machine, so it plays this audio through its own recordable audio graph. Test: `tests/test_tts.py`.

### Languages per robot (manifest `language` / `languages`)
- `manifest.yaml` may list `languages: [ko, en]` (every language the robot talks in; must include `language`, the
  default). `Manifest.languages` / `supported_languages`; validated by field. New `GET /robot/languages` ->
  `{robot, language, languages}` (`api/robot.py`, `core/languages.py`); `/ws/agent` without `lang` now uses the
  manifest's `language` (was `en`). Test: `tests/test_robot_languages.py`. `docs/contract.md`.

### Direct planner steps run in the closed loops (docs/direct_prompt.md "Steps and binding")
- `llm_direct` steps keep the robot's call (`skill`, `params` read as Structured mode reads them, `flags`)
  and carry the skill's manifest action (`move` -> `MoveTo`; `""` for a skill with no plan action). Binding
  honours the call in one place, `core/planning/concrete.py` (`RobotView.bind`, `ActionMapper.to_skill`),
  after checking the skill exists (`RobotView.registry`: plan skills, aliases); grace / SAGE steps bind
  as before. Fixes ClosedLoop with planner `direct` (and SAGE with generator `llm_direct`) failing every
  step with "no skill for action 'move'". `ActionMapper(namemap, registry=None)`; `RobotView.knows_skill`.

### Direct planner prompt (docs/direct_prompt.md) — Planner Guide removed
- The direct planner (`llm_direct`, dashboard `direct`, and the open-loop path of `UnifiedAgent.run`) builds
  its prompt by code each time it plans (`core/planning/direct_prompt.py`): `describe_robot`, the skill
  lines, the dashboard's plan skills, the ENV places, manifest `planner.notes`, the k most similar
  examples (seed pool + verified live memory), fixed answer rules, the command. Both callers ask through
  `direct_prompt.plan_answer` (`llm.chat(prompt=...)`, recorded / replayed as `chat`).
- Manifest: optional `planner.notes` (list of non-empty strings, <= 300 characters each).
- New `GET /planner/prompt?command=&planner=&k=&leave_one_out=` -> `{prompt, examples, planner, uses_prompt}`.
- Env: `ROBOT_AGENT_DIRECT_EXAMPLES` (default 3), `ROBOT_AGENT_DIRECT_LOO`; the old names
  `ROBOT_AGENT_GUIDE_EXAMPLES` / `ROBOT_AGENT_GUIDE_LOO` are still read when the new ones are unset.
- Removed: `core/guide_manager.py` (`GuideManager`, `resolve_guide`, `render_guide`, `preview_guide`,
  `reconstruct_plan`, `GUIDE_USERS`, `planner_uses_guide`), `api/guides.py` (`GET/POST /guides`,
  `GET/PUT/DELETE /guides/{name}`, `POST /guides/{name}/activate|rename`, `POST /guides/preview`),
  `AgentState.guides`, the structured (`format`) branch of the direct planner and the
  `LOCATIONS_HERE` / `PLAN_SKILLS_HERE` / `{ROBOT}` / `{ACTIONS}` / `{EXAMPLES}` placeholders. A plug-in
  says `uses_direct_prompt = True` (was `uses_guide`). An old `common/guides.json` is never read.

### Contract
- Optional adapter members `pose(node) -> {x, y, yaw?, ts? | age_s?}` and `container_states(node) ->
  {key: bool | None}`, read by the SAGE-v2 reconcile (`core/planning/reconcile.py`, docs/guard.md).
  `RobotView.reconcile(..., places=, arrive_tol=, report=)`. `WorldState.provenance` (`mark`,
  `mark_edited`, `provenance_of`, `provenance_view`); `to_dict()` gains a `provenance` key (others
  unchanged); `PUT /agent/world` marks the edited fields `operator`. AssistLoop option `reconcile`
  (`ROBOT_AGENT_RECONCILE`, default 1).
- Manifest: optional top-level `description` (`Manifest.description`) and skill-level
  `description` (string, no check_manifest warning): the robot's / skill's text in the planner
  prompts (core/planning/robot_prompt.py, docs/direct_prompt.md). Old manifests load unchanged.

- `FailureCode.BUDGET_EXCEEDED` (`budget_exceeded`): the run used its LLM-call budget (recovery STOP,
  not retried). Appended after `unknown`.
- Manifest `verify: [{kind: vlm, prompt}]` is now run (`core/planning/verify_vlm.py`) through the
  adapter's optional `vlm_ask(prompt, step=, node=)`; `capabilities.vlm_verify: false` keeps it off
  (`ROBOT_AGENT_VLM_VERIFY` / the `vlm_verify` option win). `NamemapAdapter.vlm_ask` passes through to
  the namemap's.
- `RobotAdapter.health()` keys the core reads: `frame_age_s`, `fps`, `depth`, `pose_age_s`, `ok`;
  limits in manifest `geometry`: `frame_max_age_s`, `min_fps`, `need_depth`, `pose_max_age_s`,
  `vlm_min_conf` (`core/planning/sensor_health.py`, `ROBOT_AGENT_SENSOR_HEALTH`). docs/verify.md.
- `ROBOT_AGENT_LLM_BUDGET` default 20 (was 40); plan edits and the gate's refinement count too
  (`subgoal_repair.repair_plan(..., budget=)`); the edit rounds stop when the findings do not decrease. i18n key `llm_budget`.
- `connect.ros.node.NodeAgent.rev_time` / `rev_period`: receive time and smoothed gap of a subscription.
- Manifest: optional `planner:` section, `planner: {seed_memory: <JSONL, relative to the
  manifest>}` (`Manifest.planner`, `Manifest.seed_memory`, `Manifest.planner_path(key)`).
  Absent = no seed pool; old manifests load unchanged. `check_manifest` errors when the seed
  file is missing or a line is not an episode, and warns on an unknown `planner.*` key.
- `VerifySpec.expect` (manifest `verify: [{kind: sensor, skill: ..., expect: false}]`):
  what the sensor's `isdone` must be; default true. Unknown before; old manifests
  load unchanged.
- `NamemapAdapter.to_loc` / `to_obj`: the robot's names, passed through from the namemap.
- Adapter extras read by the core (optional): `is_openable(key, entry)`, `quiet_skills`,
  `i18n.phrases` keys `verb.<Action>`, `robot_description`, `verify_failed.<Action>`.

### Planner prompts (docs/direct_prompt.md)
- SAGE (`grace` / `sage`) gets the robot's plan actions and description from the manifest
  (embodiplan >= 0.2.4 `SAGEPlanner(actions, robot_description)`); `ROBOT_AGENT_SAGE_ROBOT_PROMPT=0`
  restores embodiplan's household prompts. Plug-in planners get `robot_description` too.
- (removed again, see above) Planner Guide placeholders `{ROBOT}`, `{ACTIONS}`, `{EXAMPLES}` (seed pool + verified live memory,
  top `ROBOT_AGENT_GUIDE_EXAMPLES` (3), leave-one-out with `ROBOT_AGENT_GUIDE_LOO=1`); a guide
  without them is sent unchanged. New seed versions (`guide_v2`) are added once, inactive, to an
  existing guides.json (`seeded` list).
- (removed again) `GET /guides?planner=` adds `used_by`, `active_planner`, `active_planner_uses_guide` and per-version
  `placeholders`; `POST /guides/preview` renders a guide for a command.

### Interaction (run manager, dialog)
- MODIFY / ADD / DROP / NEW said during a run are applied by AssistLoop at the next step boundary
  (`core/planning/goal_change.py`, `AssistLoop._apply_pending`); a change it cannot read is told to the
  person (`goal_change` event, `applied: false`). ClosedLoop says it cannot apply them.
- `TaskStack` / `TaskFrame` implemented (was a Protocol stub); `RunManager.suspend` / `resume_task`,
  `RunManager.on_resume` hooks (after a pause ends or a suspended task is taken back);
  `Run.outranked_by(role)`. New events: `goal_change`, `task_suspended`, `task_resumed`,
  `run_stop_deferred`.
- `interact/dialog.py`: the robot's questions are registered on the run (`Run.set_awaiting`), so an
  answer typed by any client is an ANSWER; `Run.poll_answer`. `run_control.ask_person`, `yes_no`.
- A stop during a manifest `interruptible: false` skill waits for it to end (`run_control.uninterruptible`,
  `run_skill(..., interruptible=)`, `RobotView.interruptible(skill)`, `Run.begin_critical` /
  `end_critical`); dashboard Stop and e-stop unchanged.
- `run_control.call_cancellable` / `is_cancel`: LLM and planner calls no longer delay a stop.

### Core
- Planner memory (docs/memory.md): runs are recorded into SAGE's live memory only when their
  goals were seen to hold (`core/planning/episode_memory.py`; `ROBOT_AGENT_MEMORY_RECORD` /
  option `memory_record` = `observed` (default) | `belief` (old rule) | `off`). The plain
  ClosedLoop no longer records by default. The record carries `evidence` and `run_id`
  (`GraceBackend.record_episode(task, steps, meta=None)` returns whether SAGE recorded it).
  `GraceBackend(seed_memory=)` / `RobotView.seed_memory()`: the manifest seed pool is SAGE's
  seed; `leave_one_out` is passed through.
- Executive (fix stream `execute`, D5 D7 D8 D12 D13 D17 D25 D26):
  - AssistLoop is done only when every on / in goal is looked at (`goal_check` event: MoveTo back
    + Find at the destination; a goal inside a closed drawer stays on the belief as `ok: null`).
    A goal seen not to hold goes to SAGE once more, else the run fails (code `dropped` / `absent`).
    `ROBOT_AGENT_GOAL_CHECK=belief` (option `goal_check`) restores the old belief-only check.
  - The recovery policy is applied by FailureCode: WAIT_HEALTH (`adapter.health()`, wait
    `health_wait_s`, the same step again in place), HEALTH_CHECK (an unhealthy robot stops),
    STOP once a rule's retries are used up (camera down 3 times, a second hardware fault).
  - `RobotView.reconcile(world, node, sense)`: `adapter.reconcile` + the declared holding sensor;
    called at run start (both loops) and after a pause (`run_control.pause_point`); a `reconcile`
    event when the belief changed. After a pause a Pick on an old Find looks again first.
  - A failed run's `done` event (both loops) carries `code`, `device`, `evidence` (AssistLoop: the
    first failure not recovered since; ClosedLoop also `msg`, `failed_step`); `TaskRecord.failure`;
    RunRecord metrics `failure_code` / `failure_device`.
  - One run id: the loops take the RunManager run's `run_id` (`task_run_id` in metrics is then
    None); `/ws/agent` finishes a run from `done.status` (failed / refused -> RunState failed).
  - Classifier: "no longer in the gripper" = dropped, "stuck" / "jammed" = blocked.
- The planning loops reach the robot only through `contract.load_adapter`
  (`core/planning/robot.py` `RobotView`); `make lint-core` is blocking.
- Post-step verify: effects + the manifest's VerifySpecs, no precondition re-check; a skill
  without a sensor gets an UNKNOWN layer (`VerifyResult.unknown`, new field).
- `step_verify` layers: `sensor` / `localization` from the manifest's sensors (was `in_hand`
  / `localization` in AssistLoop only).
- Failures: one normalisation (`run_skill`), codes from `SkillResult` /
  `classify_legacy_failure`, stop / searched decisions from the recovery policy.
- The announcer's Korean phrasebook moved to the robot (kcare_robot `configs/adapter.py`).
