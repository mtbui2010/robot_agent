---
sources: [robot_agent:robot_agent/skill_configs.py, robot_agent:robot_agent/new_robot.py, robot_agent:robot_agent/contract/types.py, robot_agent:robot_agent/runtime.py, robot_agent:robot_agent/core/skill_registry.py, robot_agent:robot_agent/api/skills.py, robot_agent:robot_agent/api/events.py, robot_agent:robot_agent/app_factory.py, robot_agent:robot_agent/connect/codecs.py, robot_agent:robot_agent/contract/manifest.py, robot_agent:Makefile, robot_agent:pyproject.toml, robot_agent:README.md, robot_agent:docs/contract.md, kcare_robot:kcare_robot/skills/_recognition_helpers.py, kcare_robot:kcare_robot/configs/skills_config.py, kcare_robot:kcare_robot/configs/common/skills.json, kcare_robot:kcare_robot/configs/locations/, kcare_robot:CLAUDE.md, robotapp:scripts/dump_openapi.py, robotapp:frontend/lib/generated/openapi.json, robotapp:frontend/lib/planView.ts, symbolic_robot:bench/suites/home/planners/robot_agent_adapter.py, symbolic_robot:symbolic_robot/domains/home/configs/manifest.yaml, kcare_sim:README.md]
verified_at: {robot_agent: 3269923, kcare_robot: d4f5577b342e, robotapp: f30f0b52d969, symbolic_robot: f55ace24c3b4, kcare_sim: a288298acede}
---

# Known pitfalls

!!! note "Read this first"
    This page describes the code **at the pinned commits** listed in the front matter of each page. It is current behaviour, not a promise. The code may have changed since; check the permalinks before you rely on an entry.

!!! abstract "In this page"
    - One place for the places where the code does not match what you would expect, or where two repos disagree.
    - Each entry gives the symptom, the cause with a code link, how to avoid it, and the page that explains the area.
    - A summary table at the end lists every entry.

Entries are grouped: core and contract, API and dashboard, simulators and benchmark, docs and setup. "Not run" means I read the code but did not start the system (no robot, ROS, simulator or LLM is available here).

Link base for the code links below: `https://github.com/mtbui2010/<repo>/blob/<sha>/<path>`. The shas are `robot_agent` `3269923`, `kcare_robot` `d4f5577b342e`, `robotapp` `f30f0b52d969`, `symbolic_robot` `f55ace24c3b4`, `kcare_sim` `a288298acede`.

## Core and contract

### P1. One robot's values live inside the hub

**Symptom.** The hub is meant to hold no robot-specific facts ([robot as data](../concepts/robot-as-data.md)), and `make lint-core` reports 0 hits. Yet the hub has one robot's values.

**Cause.**

- [`robot_agent/skill_configs.py:105-109`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/skill_configs.py#L105-L109) sets `MOBILE_HEIGHT = 435`, `ME = 'living room'`, `STANDING_OBJ_NAMES` and `LYING_OBJ_NAMES`. These are the same values the kcare robot declares in its [manifest](../start/glossary.md#manifest). The robot package then imports them back from the hub ([`kcare_robot/skills/_recognition_helpers.py:20`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/kcare_robot/skills/_recognition_helpers.py#L20-L23)).
- [`robot_agent/new_robot.py:203`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/new_robot.py#L203-L204) sets `--from` to `kcare_robot` by default. This is a developer command and is outside the lint scope.
- A search for the text `kcare_robot` in the hub at this commit finds 55 files (re-counted with `git grep -il` at `3269923`; it was 31 at the earlier pin). Most are docs, tests, help text and comments; the two entries above are the real coupling.

**Avoid.** A second robot must not rely on these constants. Put the values in your own manifest and `skill_configs_override.json`, and pass `--from <your_package>` to `new_robot`. Check that no skill of yours imports the object-name lists from the hub.

**Read more.** [A robot as data, "Where the rule does not hold"](../concepts/robot-as-data.md#where-the-rule-does-not-hold); [Robot package, "Current behaviour to know"](../contracts/robot-package.md#current-behaviour-to-know); [Add a robot](../guides/add-a-robot.md).

### P2. `make lint-core` does not see this coupling

**Symptom.** The lint passes while the hub still holds a robot's data (P1).

**Cause.** The lint searches only `*.py` files under `core/`, `interact/` and `contract/`, for the patterns `grasp_succeed mobile_pose kcare <maker> MOBILE_HEIGHT` (`<maker>` stands for the robot maker's product name) ([`Makefile:213-227`](https://github.com/mtbui2010/robot_agent/blob/3269923/Makefile#L213-L227)). `skill_configs.py`, `new_robot.py`, `api/`, tests and docs are outside it. I ran the same search (`make lint-core`) on those three folders at `3269923`: 0 hits for each pattern. The target now also runs `tools/lint_arch.py` first (layers, sizes, option wiring; it reported 0 new findings), which is not about robot names. The lint also looks for names, not for values, so a robot's data without the name passes.

**Avoid.** Do not read "lint passed" as "the hub has no robot facts". Review `skill_configs.py` by hand when you change it.

**Read more.** [A robot as data](../concepts/robot-as-data.md#where-the-rule-does-not-hold).

### P3. Thirteen failure codes, twelve in the contract doc

**Symptom.** `docs/contract.md` lists 12 [failure codes](../start/glossary.md#failurecode). The code has 13.

**Cause.** `FailureCode` has `BUDGET_EXCEEDED = "budget_exceeded"` ([`contract/types.py:31`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/contract/types.py#L18-L31)). The table in [`docs/contract.md:8`](https://github.com/mtbui2010/robot_agent/blob/3269923/docs/contract.md#L8) stops at `unknown`. I ran `len(FailureCode)` on the committed file: it printed `13`.

**Avoid.** Trust the code. A robot's skills do not return `budget_exceeded`; the run uses it when its LLM-call budget is spent, and it is not retried.

**Read more.** [Skills, results, failure codes](../concepts/skills-and-failures.md#the-failure-codes).

### P4. `skills.json` hides new skills until a reload

**Symptom.** You add a skill to `skills_config.py`, restart the agent, and the skill is not there.

**Cause.** At start, `_load_skills` first calls `sr.load_saved()` and returns if it found a saved `skills.json` ([`runtime.py:195-201`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/runtime.py#L195-L201)). Only when there is no such file is `skills_config.SKILL_CONFIGS` read. The reload endpoint `POST /skills/reload` does re-read `skills_config.py` ([`api/skills.py:44-70`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/api/skills.py#L44-L70)). The kcare repo tracks `configs/common/skills.json`, so its rule "register a new skill in `configs/skills_config.py`" ([`CLAUDE.md:19`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/CLAUDE.md#L19)) is not enough on its own. Reasoned from the code; not run.

**Avoid.** After you change `skills_config.py`, call `POST /skills/reload`, or update `skills.json` too.

**Read more.** [Add a skill](../guides/add-a-skill.md); [Skills, results, failure codes](../concepts/skills-and-failures.md).

### P5. Inline device code is deprecated but still used

**Symptom.** A `DeprecationWarning` appears once per device and key. The hub README still presents `exec()` of code strings as a design choice (see P12).

**Cause.** A [codec](../start/glossary.md#codec) in `connections.json` may be a source string (`"def encode_func(data, req): ..."`). It is compiled with `exec` in [`connect/codecs.py:100-118`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/connect/codecs.py#L100-L118), the only `exec` in the hub. It works by default. I ran `python -m robot_agent.connect.codecs --scan` on the five committed `connections.json` files of `kcare_robot` (one per site) at `d4f5577b342e`: they print 37, 38, 38, 41 and 42 inline entries. (The plan counted 39 in one working-tree file; the committed files give the range above.)

**Avoid.** For a new robot, write codecs as modules and register them with `register_codec`. Setting `ROBOT_AGENT_NO_INLINE_CODE=1` turns inline code off and gives a clear error instead; this breaks a robot that still has inline entries.

**Read more.** [Robot, hardware and simulators](../contracts/robot-and-simulators.md); [Robot package](../contracts/robot-package.md).

## API and dashboard

### P6. The dashboard's API snapshot has no `/schema/events`

**Symptom.** The dashboard's generated API file does not contain `GET /schema/events`, and a new hub router is not covered by its lint.

**Cause.** `robotapp`'s [`scripts/dump_openapi.py:16-17`](https://github.com/mtbui2010/robotapp/blob/f30f0b52d969/scripts/dump_openapi.py#L16-L17) keeps its own list of 16 routers. The hub mounts 17 ([`app_factory.py:73-89`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/app_factory.py#L73-L89)); the missing one is `events`. I read the committed `frontend/lib/generated/openapi.json`: 66 paths, and `/schema/events` is not among them.

**Avoid.** When you add a hub router, add its name to that list in `robotapp` as well, then regenerate the snapshot.

**Read more.** [Dashboard and core, "Three gaps, as they are today"](../contracts/dashboard-api.md#three-gaps-as-they-are-today).

### P7. Some events are missing from the event schema, and the dashboard does not read it

**Symptom.** `GET /schema/events` (version `1.0.0`) describes 27 events. The dashboard receives more.

**Cause.** `api/events.py` defines 27 models ([`api/events.py:26-44`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/api/events.py#L26-L44) for the version and the builder). The loops also send `goal_change`, `understand_check`, `understand_fallback`, `reconcile`, `task_resumed`, `search`, `health` and `goal_check`; I compared the names passed to `r.ev(...)` in `robot_agent/` at `3269923` with the 27 names and exactly these eight were not in the schema (`understand_fallback` is new since the earlier pin, which had seven). Lab flags such as `bt_goal` are not events at all (see [Debug a failed run](../guides/debug-a-failed-run.md)). The models are descriptive and the agent does not check what it sends against them. `robotapp` has no use of `/schema/events` (search at the pinned commit: no hit) but handles `goal_check` by hand ([`planView.ts:178`](https://github.com/mtbui2010/robotapp/blob/f30f0b52d969/frontend/lib/planView.ts#L178)).

**Avoid.** Do not treat the schema as the full list of events. Handle unknown event names in a client by ignoring them, or read the loops' `r.ev(...)` calls.

**Read more.** [Dashboard and core, the event stream](../contracts/dashboard-api.md#what-crosses-the-event-stream); [Runs and records](../concepts/runs-and-records.md).

## Simulators and benchmark

### P8. The "no ROS" switch exists now, but a simulated start was not run

**Symptom.** `symbolic_robot` and the symbolic mode of `kcare_sim` set `ROBOT_AGENT_ROS=off`. At the earlier pin of this site the hub at `main` did not read it and needed ROS.

**Cause.** The hub at `3269923` reads the variable: [`runtime.py:141-152`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/runtime.py#L141-L152) skips the `rclpy` import and init when it is `0`, `off`, `false` or `no`. The default is on, so a real robot behaves as before. This was read from the code; a start of a symbolic server with it was **not run**. The module `connect/ros/node.py` still imports `rclpy` at its top, and the ROS node is built only on first use.

**Avoid.** Set `ROBOT_AGENT_ROS=off` for a simulated robot, and leave it unset on a real one (a missing ROS 2 then stops start-up with a message that names the switch). Check that the hub you run has the switch.

**Read more.** [Robot, hardware and simulators, "The switch for no ROS"](../contracts/robot-and-simulators.md#the-switch-for-no-ros-robot_agent_rosoff); [Repo map](../start/repo-map.md).

### P9. The benchmark patches hub module globals, and its hub revision is not pinned here

**Symptom.** The household benchmark works against a hub revision other than the one documented here.

**Cause.**

- The adapter does not use the hub's public API. It replaces module-level names such as `current`, `host_id`, `skill_configs.ENV` and `connect.llm.init_llm_client`, then restores them after each episode ([`robot_agent_adapter.py:1-17`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/planners/robot_agent_adapter.py#L1-L17), `_patched` at `:318-333`, the pairs at `:770-776`). A rename in the hub breaks it silently.
- The home pack's manifest sets `llm_budget`, `translate_budget`, `input_pivot`, `translate_model` and `search_legs` under `planner:` ([`manifest.yaml:16-25`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/symbolic_robot/domains/home/configs/manifest.yaml#L16-L25)). At the earlier pin the hub read only `seed_memory` and `notes` and ignored these five. At `3269923` the hub's manifest loader lists them in `PLANNER_KEYS` ([`contract/manifest.py:86`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/contract/manifest.py#L86)), so `check_manifest` no longer warns about them. That they have an effect on a run, and which hub revision the benchmark's recorded results used, is **unverified**.

**Avoid.** Run the benchmark with the hub revision its docs name, not `main`. Do not read a benchmark result as a result of the code in this site.

**Read more.** [Benchmark a planner](../guides/benchmark-a-planner.md); [`symbolic_robot`](../repos/symbolic-robot.md).

### P10. A kcare skill the simulator README names is removed in the working tree

**Symptom.** `kcare_sim`'s README says the trained drawer policy is called from the kcare skill `vla_drawer_open` ([`README.md:14-15`](https://github.com/mtbui2010/kcare_sim/blob/a288298acede/README.md#L14-L15)).

**Cause.** At the pinned commit, `kcare_robot` registers it ([`skills_config.py:53`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/kcare_robot/configs/skills_config.py#L53)). The **uncommitted** working tree of `kcare_robot` on this machine removes that line and adds `'vla_stack'`. This is not in any commit, so it may or may not reach the repo. The skill is also in the tracked `skills.json` (see P4), so which one runs depends on which file loads.

**Avoid.** Check the `skills_config.py` and `skills.json` of the revision you run before you follow the README.

**Read more.** [`kcare_sim`](../repos/kcare-sim.md).

## Docs and setup

### P11. The hub README is out of date

**Symptom.** The README does not match the code tree.

**Cause.** It lists `api/connects.py`, `api/ros.py`, `api/skill_configs.py` and `api/llm_config.py` ([`README.md:29-33`](https://github.com/mtbui2010/robot_agent/blob/3269923/README.md#L29-L33)); none of the four is in the tree at the commit (I checked each with `git ls-tree`). It says "~4.2 K LOC, 26 files" ([`:18`](https://github.com/mtbui2010/robot_agent/blob/3269923/README.md#L18)); the package at `3269923` has 218 Python files and 43,816 lines (138 and 28,960 at the earlier pin). It describes `exec()` of code strings as a design choice ([`:96`](https://github.com/mtbui2010/robot_agent/blob/3269923/README.md#L96), [`:239`](https://github.com/mtbui2010/robot_agent/blob/3269923/README.md#L239)), which is now deprecated (P5).

**Avoid.** Use this site and the code. Treat the README as history.

**Read more.** [Repo map](../start/repo-map.md).

### P12. `embodiplan` is both a required dependency and an optional extra

**Symptom.** `pip install robot_agent` pulls in `embodiplan`, although the `[grace]` extra suggests it is optional.

**Cause.** [`pyproject.toml:24`](https://github.com/mtbui2010/robot_agent/blob/3269923/pyproject.toml#L24) lists `embodiplan>=0.2.1,<0.3` in the dependencies. [`:55-59`](https://github.com/mtbui2010/robot_agent/blob/3269923/pyproject.toml#L55-L59) declares it again as the extra `grace`, with a comment that it is "kept out of the core deps". The comment and the dependency list disagree. The planning loops need the package, so it is required in practice. The package is a local path package, not on PyPI (the comment in the file says `pip install -e ../embodiplan`).

**Avoid.** Install `embodiplan` first from its checkout, then the hub.

**Read more.** [Run the system](../start/run-the-system.md); [Planner library](../contracts/planner-library.md).

### P13. The default `python3` of this machine has no pyyaml

**Symptom.** Scripts that read a manifest fail with `ModuleNotFoundError: No module named 'yaml'`.

**Cause.** On the machine used to write this site, the first `python3` on `PATH` (a conda interpreter) has no `yaml`; `/usr/bin/python3` has it. I checked both with `import yaml`. This is a property of that machine, not of the repo.

**Avoid.** Run manifest scripts with an interpreter that has pyyaml. Install it in your environment before you run `check_manifest`.

**Read more.** [Run the system](../start/run-the-system.md); [A robot as data, "Check a manifest before you run anything"](../concepts/robot-as-data.md#check-a-manifest-before-you-run-anything).

## Summary

| # | Where | Symptom | Cause | Avoid |
|---|---|---|---|---|
| P1 | core | Robot values and default `--from kcare_robot` in the hub | `skill_configs.py:105-109`, `new_robot.py:203` | Own manifest; pass `--from` |
| P2 | core | Lint passes, coupling remains | `Makefile:213-227` scans 3 folders, by name | Review by hand |
| P3 | contract | 13 codes, doc lists 12 | `contract/types.py:31` | Trust the code |
| P4 | core | New skill missing at start | `runtime.py:195-201` reads `skills.json` first | `POST /skills/reload` |
| P5 | core | Inline device code, warning | `connect/codecs.py:118` | Register codec modules |
| P6 | API | No `/schema/events` in dashboard snapshot | `dump_openapi.py:16-17`: 16 of 17 routers | Edit the list |
| P7 | API | 8 events not in schema | `api/events.py`; loops' `r.ev(...)` | Ignore unknown events |
| P8 | simulators | No-ROS switch in the code, start not run | `runtime.py:141-152` | Set `ROBOT_AGENT_ROS=off` for a simulated robot |
| P9 | benchmark | Globals patched; hub revision of results unknown | `robot_agent_adapter.py`, `manifest.py:86` | Use the named revision |
| P10 | kcare | `vla_drawer_open` removed in working tree | `skills_config.py:53` (committed) | Check your revision |
| P11 | docs | README out of date | `README.md:18, 29-33, 96` | Use this site |
| P12 | setup | `embodiplan` required and optional | `pyproject.toml:24, 55-59` | Install it first |
| P13 | setup | No pyyaml in default `python3` | machine property | Use an interpreter with pyyaml |
