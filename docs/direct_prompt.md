# The direct planner's prompt, and the robot's prompts from the manifest

The *direct* planner turns a command into a `skill::input` program, one step per line, with one LLM
call. It runs as:

| where | planner |
|---|---|
| dashboard / `/ws/agent` `planner: "direct"` | ClosedLoop around the `llm_direct` backend (`core/planning/backends/llm_direct.py`) |
| a request without a planner (and `ROBOT_AGENT_CLOSED_LOOP` unset) | the open-loop path of `UnifiedAgent.run` (`core/unified_agent.py`): the program runs step by step |
| `sage` with `ROBOT_AGENT_SAGE_GENERATOR=llm_direct` / option `generator` | AssistLoop around `llm_direct` |

Its prompt is **built by code each time it plans**: `core/planning/direct_prompt.py`. Both callers
use `direct_prompt.plan_answer(llm, view, command)`, which sends `build_prompt(...)` with
`llm.chat(prompt=...)` (recorded and replayed by `robot_agent.observe` like every LLM call). Nothing
is stored: there is no guide file, no guide version, no editor. (Until 2026-11-02 the prompt was a
hand-written, versioned *Planner Guide* in `common/guides.json`; see "Removed" below.)

Not to be confused with the dashboard's **Structured** mode (`direct: true` on `/ws/agent`,
`UnifiedAgent.run_direct`): it runs a written `skill::args` text as is, with no LLM and no prompt.

## What the prompt holds

In this order (`direct_prompt.build(view, command, places=None, k=, leave_one_out=, pool=, live_path=)`
returns `{prompt, examples}`):

1. **The robot**: `robot_prompt.describe_robot(view)`, the manifest's top-level `description:` when
   present, else a sentence made from `robot` and `capabilities` (`kaair: a robot that has 1 arm with a
   hand (right), so it holds one object at a time, drives on a mobile base, ...`; a base without an
   arm: "has no arm, so it cannot pick up, put down or open anything").
2. **Its skills**: `robot_prompt.action_lines(view, style="skill")`, one
   `- move::<place> (MoveTo): move to a place` line per plan action (`RobotView.plan_actions`: the
   skills minus Observe / Verify / Ask / Query, plus the robot's no-ops), with the skill's own
   `description:` when it has one; then the plan skills defined on the dashboard, when there are any.
3. **Places**: the live ENV, one line per place with its aliases (`env_names.describe_env`).
4. **Notes**: the manifest's `planner.notes`, a list of short strings (rules the robot needs that the
   skills and places do not say). Optional.
5. **Examples**: the `k` plans most similar to the command, as `Command:` / `Program:` blocks in the
   robot's skill names, from the robot's seed pool (manifest `planner.seed_memory`) plus the
   **verified** episodes (`verified: true`) of the planner's live memory, ranked by embodiplan's
   `MemoryRetriever` (the ranking SAGE uses; `core/planning/backends/examples.py`).
6. Fixed answer rules (the program only, one `skill::input` step per line) and the command.

The template names no robot; every robot word comes from its manifest, ENV and memory.

| env var | default | |
|---|---|---|
| `ROBOT_AGENT_DIRECT_EXAMPLES` (old name `ROBOT_AGENT_GUIDE_EXAMPLES`) | 3 | examples per prompt |
| `ROBOT_AGENT_DIRECT_LOO=1` (old name `ROBOT_AGENT_GUIDE_LOO`) | off | evaluation: leave-one-out, never the command's own task or a near-duplicate |

To change what the direct planner sees, change the robot: `planner.notes`, skill `description:`s, the
ENV aliases, or the seed pool (`kcare_robot/planner/seed_memory.jsonl`, checked by its tests).

## Preview

`GET /planner/prompt?command=...[&planner=...]` (read-only, `api/planner.py`) returns
`{prompt, examples, planner, uses_prompt}`: the prompt the direct planner would send for that
command (with the live memory's examples), the examples' `task` / `score` / `source`, the planner
asked about (default: the one a request without a planner runs) and whether it uses this prompt
(`direct` / `llm_direct`: yes; `grace`: no; `sage`: only with generator `llm_direct`; a plug-in: when
its class says `uses_direct_prompt = True`). robotapp's **Planner prompt** panel shows it.

## SAGE's prompts from the same generator

`ClosedLoop._make_planner` passes `robot_prompt.action_lines(view)` (`style="action"`:
`MoveTo <place>: move to a place`) and `describe_robot(view)` to `GraceBackend` -> embodiplan
`SAGEPlanner(actions=..., robot_description=...)` (embodiplan >= 0.2.4): the decompose / expand /
refine / repair system prompts list only the robot's actions and describe the robot instead of "a
household robot". `ROBOT_AGENT_SAGE_ROBOT_PROMPT=0` switches this off. SAGE does not use the
direct prompt.

## Evaluating a change

In-context examples alone move success a lot (LoTa-Bench: 9.6-17.8 points), so compare over several
example sets:

```bash
cd scenarios
PYTHONPATH= python tools/direct_variance.py --sets 5 [--k 3] [--frac 0.6] [--loo] [--cases S01,M01] [--json out.json]
GUIDE_VARIANCE=1 GUIDE_VARIANCE_ARGS="--sets 5" ./run_all.sh --live    # live, results/direct_variance.txt
SCEN_PLANNER=llm_direct ./run_all.sh --live                             # T2: the 35 commands through llm_direct
```

`direct_variance.py` builds the prompt per bench command with examples drawn from a
`random.Random(seed)` subset of the pool, runs the answers on the FakeRobot house and prints mean /
stdev / min / max of the goal success rate. The default LLM is a deterministic fake that forgets
what its examples do not show (T1); `--live` asks the model at `$SCEN_LLM_URL`.

## Removed (2026-11-02)

The Planner Guide: `core/guide_manager.py` (`GuideManager`, `resolve_guide`, `render_guide`,
`reconstruct_plan`, `GUIDE_USERS`, `planner_uses_guide`), `api/guides.py` (`/guides`,
`/guides/preview`, ...), the robots' `configs/guide*.py` and kcare's `common/guides.json`, the
structured (`format`) branch of the direct planner, and robotapp's Planner Guide panel. An old
`common/guides.json` left on a robot is never read. KAAIR's plan answers change with this: see the
CHANGELOG for how to compare and what to adjust.

Tests: `tests/test_v2_direct_*.py`, scenarios DP01-DP08 (`scenarios/tests/test_t1_v2_direct.py`).
