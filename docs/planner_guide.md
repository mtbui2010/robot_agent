# Planner Guide and the robot's prompts

The **Planner Guide** is the LLM prompt of the *direct* planner: one text that turns a command
into an `skill::input` program, one step per line (or, with a JSON-schema `format`, a structured
answer that `guide_manager.reconstruct_plan` turns into that program). Operators keep versions
of it in `common/guides.json` and edit, add, select them on the dashboard (robotapp: Planner Guide
panel; API: `/guides`). Code: `core/guide_manager.py`, `api/guides.py`.

## Which planners use it

| planner (dashboard / `/ws/agent` `planner`) | uses the guide? | its prompt |
|---|---|---|
| `direct` (the direct path in `core/unified_agent.py`) | yes | the active guide |
| `llm_direct` (backend `core/planning/backends/llm_direct.py`) | yes | the active guide |
| `grace` (SAGE, plain loop) | **no** | SAGE's own prompts, built from the manifest |
| `sage` (SAGE + assist) | **no** (yes only when `ROBOT_AGENT_SAGE_GENERATOR=llm_direct`) | SAGE's own prompts |
| a plug-in (`module:Class`, entry point) | when its class has `uses_guide = True` | its own |

A request without a planner runs the direct path, unless `ROBOT_AGENT_CLOSED_LOOP=1` (then
`ROBOT_AGENT_PLANNER`, default `grace`). `GET /guides?planner=<choice>` answers with
`used_by: ["direct", "llm_direct"]`, `active_planner` and `active_planner_uses_guide`; the
dashboard passes its planner choice and shows a note when the guide does not apply.

## One description of the robot, from the manifest

`core/planning/robot_prompt.py` is the one generator both paths use:

- `describe_robot(view)`: the manifest's top-level `description:` when present, else a sentence
  made from `robot` and `capabilities` (`kaair: a robot that has 1 arm with a hand (right), so it
  holds one object at a time, drives on a mobile base, ...`; a base without an arm: "has no arm, so
  it cannot pick up, put down or open anything").
- `action_lines(view, style)`: one line per plan action (`RobotView.plan_actions`: the skills
  minus Observe / Verify / Ask / Query, plus the robot's no-ops), with the skill's own
  `description:` if it has one. `style="action"` for SAGE (`MoveTo <place>: move to a place`),
  `style="skill"` for the guide (`- move::<place> (MoveTo): move to a place`).

SAGE: `ClosedLoop._make_planner` passes both to `GraceBackend` -> embodiplan
`SAGEPlanner(actions=..., robot_description=...)` (embodiplan >= 0.2.4): the decompose / expand /
refine / repair system prompts list only the robot's actions (with only the matching action
rules and JSON examples) and describe the robot instead of "a household robot".
`ROBOT_AGENT_SAGE_ROBOT_PROMPT=0` switches this off (embodiplan's default household prompts).

## Placeholders

A guide may contain:

| placeholder | filled with |
|---|---|
| `{ROBOT}` | `describe_robot` |
| `{ACTIONS}` | `action_lines(style="skill")`, one per line |
| `{EXAMPLES}` | the k plans most similar to the command, as `Command: ...` / `Program:` blocks in the robot's skill names |
| `LOCATIONS_HERE` (older) | the ENV places with aliases |
| `PLAN_SKILLS_HERE` (older) | the dashboard's plan skills |
| `COMMAND_HERE` | the command (by the LLM client) |

`{EXAMPLES}` comes from the robot's seed pool (manifest `planner.seed_memory`) plus the
**verified** episodes (`verified: true`) of the planner's live memory, ranked by embodiplan's
`MemoryRetriever` (the ranking SAGE uses; `core/planning/backends/examples.py`). Top
`ROBOT_AGENT_GUIDE_EXAMPLES` (default 3). `ROBOT_AGENT_GUIDE_LOO=1` (evaluation) is leave-one-out:
never the command's own task or a near-duplicate of it.

A guide without `{ROBOT}` / `{ACTIONS}` / `{EXAMPLES}` (every legacy guide) is sent exactly as
before, byte for byte (scenario GD04).

`POST /guides/preview` `{name | guide, command, k?, leave_one_out?}` returns
`{rendered, placeholders, legacy, examples}`: what the LLM would receive for that command
(`COMMAND_HERE` replaced literally; the client also lower-cases and drops stop words).

## The shipped versions (kcare_robot)

`guide_struct` (active by default), `guide`, `guide_short`, `guide_struct_kr` hard-code a
robot description ("a two-finger gripper with suction capability, an additional robotic arm")
and fixed few-shot examples of mostly one pattern ("give me X" -> `place::me`).
`guide_v2` keeps only the output format and the placeholders. It is seeded into an existing
`guides.json` once as an extra version, **not active** (`guides.json` remembers the seeded
names in `seeded`, so a deleted version does not come back). Switch to it on the dashboard
(Planner Guide -> `guide_v2` -> Set active) or with `POST /guides/guide_v2/activate`; back with
`POST /guides/guide_struct/activate`. It only matters when the planner is `direct` / `llm_direct`.

Note: `guide_struct`'s `FORMAT` has no `action_types`, which `reconstruct_plan` needs; its
answers do not become a program (the direct run reports "LLM returned no valid task plan"
unless the structured call fails over to freeform). `guide_v2` is freeform.

## Evaluating a guide change

The in-context examples alone move success a lot (LoTa-Bench: 9.6-17.8 points), so compare
guides over several example sets, never one run:

```bash
cd scenarios
PYTHONPATH= python tools/guide_variance.py --guides guide_v2,guide --sets 5 [--k 3] [--frac 0.6] [--loo] \
    [--memory live.jsonl] [--cases S01,M01] [--json out.json]
```

It runs the bench commands that should succeed through each guide (rendered by `render_guide`,
`{EXAMPLES}` drawn from a `random.Random(seed)` subset of the pool), executes the answers on the
FakeRobot house and prints mean / stdev / min / max of the goal success rate per guide. The
default LLM is a deterministic fake that forgets what its examples do not show (T1, scenario
GD06); `--live` asks a real model at `$SCEN_LLM_URL` (`--model`). With the live container of
`run_all.sh --live`: `GUIDE_VARIANCE=1 GUIDE_VARIANCE_ARGS="--sets 5" ./run_all.sh --live`
(writes `results/guide_variance.txt`). The tool never starts an LLM itself.

Change a guide when its mean improves by more than its stdev, over the same sets.

Tests: `tests/test_v2_guide_*.py`, scenarios GD01-GD06 (`scenarios/tests/test_t1_v2_guide.py`).
