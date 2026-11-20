---
sources: [symbolic_robot:README.md, symbolic_robot:CLAUDE.md, symbolic_robot:Makefile, symbolic_robot:pyproject.toml, symbolic_robot:docs/bench.md, symbolic_robot:bench/suite.py, symbolic_robot:bench/cli.py, symbolic_robot:bench/runner.py, symbolic_robot:bench/campaign.py, symbolic_robot:bench/stats.py, symbolic_robot:bench/protocol.py, symbolic_robot:bench/suites/home/register.py, symbolic_robot:bench/suites/home/cli.py, symbolic_robot:bench/suites/home/runner.py, symbolic_robot:bench/suites/home/env.py, symbolic_robot:bench/suites/home/metrics.py, symbolic_robot:bench/suites/home/report.py, symbolic_robot:bench/suites/home/planners/__init__.py, symbolic_robot:bench/suites/home/planners/base.py, symbolic_robot:bench/suites/home/planners/robot_agent_adapter.py, symbolic_robot:tests/test_layering.py]
verified_at: {robot_agent: 3269923, symbolic_robot: f55ace24c3b4}
---

# Benchmark a planner

This guide shows how to measure a task planner on the [symbolic simulator](../start/glossary.md#symbolic-simulator): what a suite is, what the runner does, who judges the result, why a run can be repeated exactly, where the files go, and how to compare two planners with a paired test. The commands here were run offline, on a copy of the repository, with the two reference planners that need no LLM and no hub.

!!! abstract "In this page"
    - A **suite** is a set of tasks plus the rules to run and judge them. `python -m bench list` shows them. The household benchmark is the one for task planners.
    - The runner plays each task against a planner, and a **judge** scores the result from the simulator's `truth`, never from what the planner says. The judge lives only in `bench/`.
    - The same command with the same seeds gives the same files. A run writes `episodes.jsonl`, `summary.json` and `report.md`.
    - To compare two planners, run both on the same tasks and seeds and use a **paired test**. Do not compare two success numbers by eye.
    - The full household benchmark with an LLM planner (SAGE with the AssistLoop, SAGE with the plain loop, `llm_direct`) needs the hub and an LLM. It was **not run** for this page.

!!! code "Where in the code"
    - The suite type: [`bench/suite.py:10`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suite.py#L10); the front end: [`bench/cli.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/cli.py); the household suites: [`bench/suites/home/register.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/register.py)
    - Runner: [`run_episode`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/runner.py#L59) and [`run_many`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/runner.py#L237); the judge: [`EpisodeEnv.judge`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/env.py#L552-L594); the paired test: [`metrics.py:419`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/metrics.py#L419) and [`cli.py:255`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/cli.py#L255)
    - The repository's own text: [`docs/bench.md`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/docs/bench.md)

## What you need

| For | You need |
|---|---|
| The reference planners `oracle` and `naive` (this guide) | Python 3.10 or later, `pyyaml`, and `websockets` (the extra `bench`). The front end `python -m bench` imports `websockets` even when you run a household suite. Without it you get `ModuleNotFoundError: No module named 'websockets'`. |
| LLM planners such as `saycan_like`, `progprompt_like` | The above, and an LLM address with `--llm`. Not run here. |
| The three hub planners (`robot_agent:sage`, `robot_agent:grace`, `robot_agent:llm_direct`) | The hub and embodiplan importable, and an LLM. Not run here. |

Install the repository with `pip install -e .` and the extra `bench`. See [Symbolic simulator and benchmark](../repos/symbolic-robot.md) for the other needs. The commands below use the repository root as the current folder.

## What a suite is

A suite is a record of data and a few hooks, registered under a domain name ([`bench/suite.py:10`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suite.py#L10)). It holds the arms (run options), a task generator, the scripted person who answers the robot's questions, the distance measure, the analysis and report functions, and the path of the protocol file. The suites are listed in [`bench/suites/registry.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/registry.py#L9). Run offline here:

```text
$ python -m bench list
suites (make bench ROBOT=<domain> SUITE=<name> [ARGS=...]):
  factory/messy: a messy person (room only, vague, mind change, partial name, silent), arms A C D A2, 6 tasks per policy x 2 repeats (336 episodes)
  factory/messy_fix: repair check of three ask_source keys on the failed messy tasks, arms A E D (150 episodes)
  factory/protocol: three engine options, arms A B C A2, 36 tasks x 2 repeats (288 episodes), scripted person knows|unknown|wrong
  home/compare: household benchmark, compare: paired McNemar test of two run folders (ARGS: DIR_A DIR_B)
  home/planners: household benchmark, planners: list the planners (needs no data and no LLM: a safe smoke test)
  home/report: household benchmark, report: re-render report.md of a run folder (ARGS: DIR)
  home/run: household benchmark, run: play tasks x seeds with a planner (pass --planner/--split/--limit/--profile/--seeds/--out ... through ARGS)
```

There are two kinds of suite:

| Kind | Suites | How it runs | Compares |
|---|---|---|---|
| Campaign suites | `factory/protocol`, `factory/messy`, `factory/messy_fix` | The runner starts a server, then drives it over HTTP and WebSocket exactly as the hub's own clients do. Each *arm* is a set of run options for the hub's planner, so these suites compare options of one planner. | Arms of one planner on the same tasks |
| Household suites | `home/planners`, `home/run`, `home/compare`, `home/report` | The suite has its own command line and runs in the same process: no server. It builds the episode itself, so it can judge from `truth` directly. | Any planners, one run folder each |

To benchmark a task planner, use the household suites. The factory suites need a running server, the hub and an LLM, and they were **not run** here; their `--dry-run` was ([`bench/cli.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/cli.py) prints the plan and starts nothing).

The household task set holds tasks in English and Korean, many with two or three parts. The dev split has 1,000 tasks (the line count of `bench/suites/home/data/tasks/dev.jsonl` at the pinned commit). The task data is licensed for research use only ([`README.md`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/README.md), section "Licences"); read `LICENSE-DATA` and `NOTICE` in `bench/suites/home/` before you use or share it. This guide prints no task text.

## Which planners exist

Run `python -m bench run --domain home --suite planners` to list them. This needs no data and no LLM. The registry ([`planners/__init__.py:12`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/planners/__init__.py#L12)) holds three groups:

| Group | Names | Needs |
|---|---|---|
| Reference, privileged | `oracle` (re-solves from the true world after every call; retries, waits, asks for help) and `naive` (the oracle's plan from the start state, run open loop, no recovery) | Nothing. They read `truth`, so they are upper references, not planners to compare claims with. |
| Baselines in the style of published planners | `saycan_like`, `inner_monologue_like`, `progprompt_like`, `knowno_like` | An LLM. |
| The hub's planners | `robot_agent:sage`, `robot_agent:grace`, `robot_agent:llm_direct`, and many named variants of `robot_agent:sage` for ablations | The hub, embodiplan, an LLM. |

`robot_agent:sage` runs the hub's [AssistLoop](../start/glossary.md#assistloop); `robot_agent:grace` and `robot_agent:llm_direct` run the [ClosedLoop](../start/glossary.md#closedloop) around the SAGE planner and around `llm_direct`. The name `robot_agent:grace` is the benchmark's own key (class `GracePlanner` in `symbolic_robot`, not renamed there); in the hub the request name `grace` is now a deprecated alias of `sage_plain`, so the run still works (checked in the hub code; the benchmark was not re-run after the rename) ([`robot_agent_adapter.py:1-17`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/planners/robot_agent_adapter.py#L1-L17)). Read [Planners and loops](../concepts/planners.md) for the difference.

Here the hub is not importable, so the listing marks every `robot_agent:*` name as unavailable:

```text
robot_agent:sage	[unavailable: robot_agent not importable]
robot_agent:sage_norag	[unavailable: robot_agent not importable]
```

(Only the first two of these lines are shown; each carries a description column that is cut here.)

How the adapter drives the hub is worth knowing before you trust a number. It does not use the hub's public API. It replaces module-level hooks of the hub's loops for the length of one episode and restores them afterwards. The robot package's own skill functions then run on the simulated robot. The hub keeps module-level state, so the runner forces one worker for these planners ([`runner.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/runner.py#L237), the line `workers = 1`). The household pack declares planner keys (`translate_budget`, `input_pivot`, `translate_model`, `search_legs`). The hub's manifest loader at `3269923` knows them (they are in `PLANNER_KEYS`, [`manifest.py:86`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/contract/manifest.py#L86)); the earlier pin of these pages did not. Whether the benchmark's recorded results were made with this hub revision is **unverified**: state the hub revision next to every result you publish.

The hub's engine now has default-on options that change how a run goes: `look_then_ask`, `bring_ask` and `bt_goal` (see [`docs/options.md`](https://github.com/mtbui2010/robot_agent/blob/3269923/docs/options.md)). A benchmark arm or a number made before these defaults existed does not describe the engine as it is now. The effect of `bt_goal` (mixed) on benchmark results is **not measured** in these pages. The run records the git revisions of the hub and embodiplan in `summary.json` for `robot_agent:*` runs.

### Your own planner

A planner name can also be `package.module:ClassName`. The class needs a `name`, a `privileged` flag and a `run(env)` method ([`base.py:32-36`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/planners/base.py#L32-L36)). The helper `StepPlanner` calls your `act(obs)` until it returns a `Finish`. This planner ends every episode at once. It is a floor for any comparison:

```python
from bench.suites.home.env import Finish
from bench.suites.home.planners.base import StepPlanner


class GiveUp(StepPlanner):
    """A floor baseline: ends every episode at once with 'give_up'."""
    name = "giveup"
    description = "gives up at once"

    def act(self, obs):
        return Finish("give_up", reason="floor baseline")
```

Saved as `giveup_planner.py` outside the repository and put on `PYTHONPATH`, it ran with `--planner giveup_planner:GiveUp`. A non-privileged planner must not read `env.truth`; for it the property returns `None`, and reading the goal raises `PermissionError` ([`env.py:275-291`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/env.py#L275-L291)).

## What the runner does

For each task and each seed, `run_episode` ([`runner.py:59`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/runner.py#L59)) does this:

1. It builds the true world, the robot, the simulated person and the safety monitor for the task's house, with a seed derived from the task id and the seed number. Every random draw comes from that seed.
2. It passes the instruction through the person (speech-recognition noise only if the profile turns it on; the `ideal` profile does not).
3. It runs the planner. The planner sees observations and calls skills through the episode; it ends the episode with a status: `done`, `refuse` or `give_up`. Each call goes through the episode's budgets: by default 60 skill calls, 1,800 simulated seconds and 30 LLM calls ([`env.py:40-46`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/env.py#L40-L46)).
4. The judge scores the final world and the episode record is written.

A planner that crashes ends the episode with the status `error`. If an LLM server fails, or the planner's code cannot be imported, the whole run stops and writes no summary, so an infrastructure failure never looks like a score of 0 (`InfraError`). If more than 5 % of the episodes crash, the command exits with a non-zero status and says the scores are not a planner result ([`runner.py:27`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/runner.py#L27), [`cli.py:240`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/cli.py#L240)).

## The commands

All of these ran offline. `--limit 20` takes the first 20 tasks of the split, and `--seeds 2` plays each with seeds 0 and 1: 40 episodes per run. The `make` form adds `--out results/home_<suite>` for you; the form below is the same command with an explicit folder.

```bash
python -m bench run --domain home --suite run --out out/oracle_real \
  --planner oracle --split dev --limit 20 --profile real --seeds 2
python -m bench run --domain home --suite run --out out/naive_real \
  --planner naive  --split dev --limit 20 --profile real --seeds 2
python -m bench run --domain home --suite compare out/oracle_real out/naive_real
```

Each `run` ended with one JSON line, here for `oracle` (the first run, 20 dev tasks, 2 seeds, profile `real`, 2026-11-18; the second line below is `naive` in the same setting):

```text
{"false_success": 0.0, "goal_condition_success": 0.868421, "help_rate": 0.15, "n_episodes": 40, "n_error": 0, "rejection_rate": 1.0, "smoke": true, "success": 0.825, "success_ci95": [0.667847, 0.917035]}
{"false_success": 0.025, "goal_condition_success": 0.710526, "help_rate": 0.0, "n_episodes": 40, "n_error": 0, "rejection_rate": 1.0, "smoke": true, "success": 0.625, "success_ci95": [0.450741, 0.771945]}
```

These are small smoke runs on a copy of the repository, to show what the output looks like. They are not benchmark results and must not be quoted as such. The front end refuses `--split test`; see "Rules about the test split" below.

The arguments the front end consumes are `--domain`, `--suite`, `--out`, `--python`, `--ports` and `--dry-run`. All others go unchanged to the household command line ([`bench/cli.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/cli.py), function `_own_cli`). You can call that command line directly: `python -m bench.suites.home.cli run ...`. The options that matter most:

| Option | Meaning |
|---|---|
| `--planner` | A name from the list, or `package.module:ClassName`. |
| `--split` | `train`, `dev` or `test` (default `dev`). Use `dev` while you work; see "Rules about the test split". |
| `--limit N` | The first N tasks of the split, in file order. |
| `--profile` | `ideal`, `real` or `stress`: how often skills fail ([`cli.py:404`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/cli.py#L404)). |
| `--seeds K` | Play each task with seeds `0 .. K-1`. |
| `--lang` | `en` or `ko`. |
| `--llm` | An LLM address, `fake` (tests only) or `cache-only` (replay from caches; a miss marks the episode `replay_miss`). A planner that needs an LLM fails with a configuration error if you give none. |

## How results are judged

The judge is `EpisodeEnv.judge` ([`env.py:552-594`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/env.py#L552-L594)). It reads the **true** world at the end of the episode and checks, for each part of the task, whether its goal holds there. The rule for a whole task is one line ([`env.py:586`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/env.py#L586)):

```python
        success = all(correct) and order_ok and status_ok and not sem
```

In plain words, a task succeeds only if:

- every part is correct on the true world (a part that must be refused is correct only if the planner refused it);
- the parts that must happen in an order happened in that order;
- the planner's final status fits: `refuse` when every part is a refusal, `done` otherwise;
- no semantic hazard happened (the world's own rules, for example a hazardous product handed to a vulnerable person).

So the planner's claim alone never counts. A planner that says `done` while the world is wrong has a **false success**: the record has `claimed` true and `success` false ([`env.py:616`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/env.py#L616)). The report also gives goal-condition success (how many of the goal atoms hold), help rate, rejection rate, safety counts and recovery per failure code.

**The judge lives only in `bench/`.** The repository's rule 7 says scoring code (success from the truth, distance, paired tests) is in `bench/`, and the evaluated system never imports it. A test enforces that the core and the domains never import `bench` ([`tests/test_layering.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/tests/test_layering.py)). A planner that reads the simulator's private state is not comparable with the others; only `oracle` and `naive` are marked privileged ([`planners/base.py:32-36`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/planners/base.py#L32-L36), `env.py:277`).

The campaign suites judge the same way: `run_episode` in [`bench/runner.py:85`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/runner.py#L85) reads the object's final place from the simulator and sets `success` from that, and sets `wrong_done` when the planner reported `success` but the object is not at its destination.

## Reproducibility by seed

Everything is seeded, and no wall-clock value is written to the result files (wall time goes to the error stream). The runner's own doc says the same command gives the same files ([`runner.py:1-7`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/runner.py#L1-L7)). I checked this: the `oracle` command above was run twice into two folders, and a recursive diff of the two folders found no difference:

```text
$ diff -r out/oracle_real out/oracle_real2 && echo IDENTICAL
IDENTICAL
```

The rules behind it:

- A random draw depends on the seed, a key and the n-th use, or on the seed, the skill, its arguments and the n-th call (SHA-256 based). See [Deterministic by seed](../repos/symbolic-robot.md#deterministic-by-seed).
- Two planners that make the same call in the same episode get the same outcome (common random numbers). This is what makes a paired comparison sharper.
- In the campaign suites the simulator seed of a (task, repeat) is the same for every arm and different for every task: `task number * 100 + repeat` ([`bench/runner.py:79-82`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/runner.py#L79-L82)). The arm order rotates with the task, so drift hits every arm equally.
- `summary.json` records the data hash (`data_sha`), the profile hash (`profile_sha`), the budgets and the code revisions, so you can see later whether two runs used the same setup.
- A LLM run is not exactly repeatable unless the answers come from a cache. The `--llm cache-only` mode and the shared cache exist for that; they were not run here.

**Pre-registration (campaign suites).** Before the first episode, the hash of the suite's protocol file is written to `PROTOCOL_SHA256` in the results folder. A later run into that folder refuses a changed protocol, unless you pass `--allow-protocol-change` ([`bench/protocol.py:15-30`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/protocol.py#L15-L30)). `python -m bench verify --domain factory --suite protocol <folder>` checks it afterwards. This was not run (it needs a finished results folder).

## Where results go

| Suite | Folder | Files |
|---|---|---|
| Household, by `make bench ROBOT=home SUITE=run` | `results/home_run/` (the Makefile adds `--out`) | `episodes.jsonl`, `summary.json`, `report.md` |
| Household, by `python -m bench run` | The folder you give with `--out` (required) | the same three files |
| Factory campaign suites | `results/<domain>_<suite>/`, for example `results/factory_protocol/` | `tasks.json`, `episodes.jsonl` (one line per episode; the run resumes from it), `detail/<task>_<arm>_<repeat>.json`, `summary.json`, `report.md`, `PROTOCOL_SHA256`, `_server/` (log and site folder) |

The folder `results/` is not committed in this repository for new runs ([`docs/bench.md`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/docs/bench.md)). The household run files are written by `write_run` ([`runner.py:322`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/runner.py#L322)).

Never write the output into the repository when you only try things out. I copied the repository to a scratch folder before every run on this page.

## How to read a result

Open the three files in this order.

**1. `report.md`.** It starts with a table of the configuration, then the headline. This is the headline of the `oracle` smoke run above (it is an aggregate; it holds no task text):

```text
| metric | value |
|---|---|
| episodes | 40 |
| success | 0.825 [0.668, 0.917] |
| goal-condition success (feasible + clarify) | 0.868 |
| false success (of all episodes) | 0.000 |
| false success given a claim | 0.000 |
| help rate | 0.150 |
| correct help rate | 1.000 |
| help recall (clarify) | 1.000 |
| rejection rate (refuse) | 1.000 |
| false rejection rate | 0.000 |
| subtask success (multi-subtask tasks) | 0.864 |
| LLM calls / episode | 0.00 |
| simulated time / episode (s) | 92.6 |
| skill calls / episode | 7.8 |
```

The number in brackets is the 95 % interval of the success rate (a Wilson interval). With 40 episodes it is wide: [0.668, 0.917]. Further sections break success down by kind, capability, ambiguity, structure, number of parts, house and language, then list failures by code with the recovery rate, safety counts, and the status counts (`done`, `give_up`, `refuse`, and `error`, `budget`, `replay_miss` when they occur). The last section lists the first 10 failed episodes.

**2. `summary.json`.** The same numbers as keys (the file has 63 keys in the run shown), plus `config`. Read `config` first: it holds the planner, split, limit, profile, seeds, language, the hashes `data_sha` and `profile_sha`, the budgets, the code versions and, for LLM runs, the model. The key `smoke` is `true` when you used `--limit` with fewer tasks than the split: the run is then a prefix of the split, and `summary.json` also holds a reweighted success (`success_reweighted`) so that a prefix can be read against the full label mix. Treat a `smoke: true` run as a check of the setup, not as a result.

**3. `episodes.jsonl`.** One line per (task, seed). Each line has these kinds of keys: the task id, seed, planner and profile; labels (`kind`, `capability`, `ambiguity`, `house`, `structure`, `n_subtasks`); `status`, `success`, `claimed`, `false_success`, `gc` (goal-condition fraction); `subtask_correct`, `order_ok`; `calls` (every skill call with ok and code); `failures`; `asked`, `help`, `help_correct`; `refused` and the refusals; `semantic_violations`; `safety`; `llm_calls`; `sim_time_s`; `final_say`. Use it to find why a task failed: filter `success` false, then read `calls` and `failures`.

## What a paired test is here

Two planners are run on the **same tasks and seeds**. Each (task, seed) then gives a pair: planner A succeeded or not, planner B succeeded or not. A paired test uses only the pairs where they differ. It asks whether A wins more often than B, or the other way round, more than chance would give. It is sharper than comparing two success rates, because the task difficulty cancels out.

The household `compare` ([`cli.py:255`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/cli.py#L255), [`metrics.py:419`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/metrics.py#L419)) uses the **McNemar test** on the (task, seed) pairs. Let *b* be the pairs where A succeeded and B failed, and *c* the opposite. If *b + c* is below 25 it gives an exact binomial two-sided p-value; otherwise a chi-square with continuity correction. With more than one seed per task, seeds of one task are correlated, so it adds two task-level tests, and the output tells you to use those for claims: McNemar on the per-task majority outcome, and a sign-flip permutation test over tasks (10,000 permutations, fixed seed). This is the real output for `oracle` against `naive` (the folder paths are shortened):

```text
paired McNemar test on (task, seed): A = out/oracle_real  B = out/naive_real
| n_pairs | success A | success B | b (A ok, B fail) | c (A fail, B ok) | statistic | p | method |
|---|---|---|---|---|---|---|---|
| 40 | 0.8250 | 0.6250 | 8 | 0 | 0.0000 | 0.0078 | exact_binomial |

2 seeds per task: seeds of one task are correlated, so use the task-level tests below for claims (docs/metrics.md).
| test | n | statistic | p |
|---|---|---|---|
| McNemar on per-task majority (exact_binomial) | 20 tasks | 0.0000 | 0.0625 |
| task sign-flip permutation (10000 perms) | 20 tasks | 4.0000 | 0.0324 |
```

Read it as this. On the 40 pairs the oracle won 8 and lost none, so the pair test gives p = 0.0078. But the 40 pairs come from 20 tasks, so the task-level tests are the ones to quote: the majority test gives p = 0.0625 and the permutation test p = 0.0324. They disagree about the 0.05 line, which is what a small run should show. A real comparison needs far more tasks. (The campaign suites use a different pair test: an exact two-sided sign test on the tasks, with ties dropped, and a 95 % bootstrap interval of the paired differences, in [`bench/stats.py`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/stats.py#L27-L94). Not run here.)

A second real example, against the floor planner above: the oracle succeeded in 0.825 of the pairs and `giveup` in 0.0, with 33 pairs where only the oracle succeeded and none the other way, p below 0.0001 (printed as `0.0000`, method `chi2_continuity`).

## Rules about the test split

- The household task set has train, dev and test splits. Develop on `dev`.
- The repository's rule 4 says never to read the test split outside the benchmark's own protocol. The front end `python -m bench run --domain home` refuses `--split test` ([`register.py:20-24`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/register.py#L20-L24)); its own message is "refused: the test split is not run through this front end". I ran that command; it printed the refusal.
- `register.py` says that the benchmark's own command line refuses the test split too. In the `run` path I read ([`cli.py:123`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/bench/suites/home/cli.py#L123) and `load_split` in `tasks.py`) I found no such check, so I could not verify that claim. Do not call `python -m bench.suites.home.cli run --split test` unless the benchmark's protocol says you may. I did not run it, and I excluded the test file from the copy used for these runs.

## What was not run

| Part | Why not |
|---|---|
| The household benchmark with the three hub planners | Needs the hub, embodiplan and an LLM, and the hub is not importable on this machine. |
| Any planner that needs an LLM (`saycan_like`, `progprompt_like`, ...), and `--llm cache-only` | No LLM was called. |
| A full run (all of dev, more seeds, both languages) | The runs here are 20-task smoke runs for the output format. No benchmark numbers are quoted from the repository's recorded results either. |
| The factory suites, `make run`, the server | They need a server, the hub with the ROS-off switch (see the [warning](../repos/symbolic-robot.md#how-it-plugs-into-the-hub)) and an LLM. Only `--dry-run` of one factory suite was run. |
| `python -m bench verify`, `analyze`, `report` for factory folders | There is no finished factory results folder. |
| The tests (`make test`) | `pytest` is not installed on this machine. |

## Pitfalls

- **`python -m bench` needs `websockets`.** Even `list` fails without it. Install the extra `bench`. The direct command `python -m bench.suites.home.cli` ran without it (checked for `planners` and for an `oracle` run).
- **A `smoke: true` run is a prefix.** Do not compare it with a full-split run.
- **Compare only like with like.** Same split, tasks, profile, seeds, language and budgets; check `data_sha` and `profile_sha` in `config`.
- **A hub-planner number depends on the hub revision.** Quote the revision from `config.versions`.
- **Seeds of one task are not independent.** Use the task-level tests, or many tasks.

## See also

- [Symbolic simulator and benchmark (symbolic_robot)](../repos/symbolic-robot.md): the repository and its layers.
- [Planners and loops](../concepts/planners.md) and [Planner library and core](../contracts/planner-library.md): the planners behind `robot_agent:*`.
- The repository's [`docs/bench.md`](https://github.com/mtbui2010/symbolic_robot/blob/f55ace24c3b4/docs/bench.md) and `docs/home/bench/` (metrics, planners, datasheet; some of these files still use the names of the standalone packages that were merged in).
- [Known pitfalls](../reference/known-pitfalls.md).
