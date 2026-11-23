---
sources: [robot_agent:robot_agent/new_skill.py, robot_agent:robot_agent/core/skill_registry.py, robot_agent:robot_agent/api/skills.py, robot_agent:robot_agent/runtime.py, robot_agent:robot_agent/skills/__init__.py, robot_agent:robot_agent/contract/types.py, robot_agent:robot_agent/contract/manifest.py, robot_agent:robot_agent/contract/check_manifest.py, robot_agent:robot_agent/core/planning/robot.py, robot_agent:robot_agent/core/planning/robot_prompt.py, robot_agent:tests/fakes.py, kcare_robot:CLAUDE.md, kcare_robot:kcare_robot/configs/manifest.yaml, kcare_robot:kcare_robot/configs/skills_config.py]
verified_at: {robot_agent: 3269923, kcare_robot: d4f5577b342e}
---

# Add a skill

This guide adds one skill to a robot package: the function, the entry in the manifest, the failure codes, the check, and a run through the hub with no robot. You start from the three-skill `tinybot` of [Add a new robot](add-a-robot.md) and add a fourth skill, `place`.

!!! abstract "In this guide"
    - A skill has **three parts**: a function, a line in `configs/skills_config.py`, and (if the planner should use it) a `SkillSpec` in `configs/manifest.yaml`.
    - The function returns a `SkillResult`. A failure carries one of 13 failure codes. The core decides what to do next from the code.
    - The planner learns about a skill from the manifest only. There is no second list.
    - `check_manifest` and a scripted run through the hub test a skill offline. Neither proves it works on hardware. Not run: a server, a real robot.

!!! code "Where in the code"
    - Scaffolding: [`new_skill.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/new_skill.py#L1-L24), [`_register_in_skills_config`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/new_skill.py#L404-L416)
    - Registry and call: [`skill_registry.py:227`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/skill_registry.py#L227) (`func(node=node, **params)`), [`load_from_skill_configs`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/skill_registry.py#L334-L345)
    - Result and spec types: [`contract/types.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/contract/types.py#L62-L219), check: [`contract/check_manifest.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/contract/check_manifest.py#L1-L26)
    - What the planner is told: [`robot_prompt.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/robot_prompt.py#L80-L133)
    - Server side: [`api/skills.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/api/skills.py#L39-L71), [`runtime.py:195-204`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/runtime.py#L195-L204)
    - A real robot's rules: [`kcare_robot/CLAUDE.md`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/CLAUDE.md), [`configs/skills_config.py`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/kcare_robot/configs/skills_config.py#L1-L12)

## 1. Decide what the skill is

Write down four things before any code. The `place` skill of this guide, as an example:

| Question | Answer for `place` | Where it goes |
|---|---|---|
| What is its name in the registry? | `place` | `skills_config.py` and the manifest `name` |
| Which abstract action does it realise? | `Place` (a capitalised word) | the manifest `action` |
| What does it need before, and what is true after? | before: the robot is at the place and holds something. After: the hand is empty. | `pre` and `effects` |
| How can it fail? | nothing in the gripper (`grasp_failed`), the place is not the robot's place (`blocked`) | `errors` and the function |

The abstract actions the planner knows are `MoveTo`, `Find`, `Pick`, `Place`, `PutIn`, `Open`, `Close`, `Say`, `WaitForUnload` and `Dock`; a robot can add its own. For a known action, keep `pre` and `effects` the same as in the shared [action model](../start/glossary.md#action-model). The kcare rule is the same: every skill the planner uses gets a SkillSpec and the manifest stays in sync ([`CLAUDE.md`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/CLAUDE.md), rule 2). A skill that is only a helper (a sensor read) does not need to be planned, but it still needs a spec if another skill names it in `verify`.

## 2. Scaffold the function

The hub can write the first lines for you. This run is on a copy of `tinybot` in a scratch folder; the tool changes the package you name, so never point it at a repository you do not want edited.

```text
$ python -m robot_agent.new_skill tinybot place basic
────────────────────────────────────────────────────────────────
  Skill 'place' scaffolded (template: generic)
────────────────────────────────────────────────────────────────
  ✓ Appended    def place()  → tinybot/skills/basic.py
  ✓ Registered  tinybot/configs/skills_config.py

  Next steps:
    1. Restart the agent:  make terminate && make run
    2. Refresh the UI (or click ⟳ in the Skills panel) to pick up the new skill
────────────────────────────────────────────────────────────────
```

The third word, `basic`, is the file to append to (`skills/basic.py`). Without it the tool creates `skills/place.py`. It refuses if the name is already in `SKILL_CONFIGS`, or if the function already exists ([`new_skill.py:18-20`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/new_skill.py#L18-L20)). `--template detect` makes a detector-style skill instead.

What it wrote, real:

```python
def place(node, **params) -> dict:
    """Skill `place`. TODO: replace this stub."""
    log_data({'msg': f'place called with params={params}'})
    return {'isdone': True, 'msg': 'place stub'}
```

and, in `skills_config.py`, the line `'place': (f'{_PKG}.basic', 'place'),`.

!!! warning "The tool needs `_PKG`, and the stub always succeeds"
    The inserted line uses `{_PKG}`, so your `skills_config.py` must define `_PKG = '<pkg>.skills'` first, as kcare's does. On a `tinybot` copy whose table used full paths and no `_PKG`, the tool wrote an entry that `check_manifest` could not read (`no literal SKILL_CONFIGS dict`). Also, the stub returns success whatever the robot does. Run through the registry, it gives `SkillResult(ok=True, code=None, msg='place stub', ...)`. Replace the body before you trust any run.

The `Next steps` text is for a running agent; see step 8.

## 3. Write the function and its result

Replace the stub. The rules, from the registry code and the contract:

- The registry calls the function as `func(node=node, **params)`. The `params` are the keys of the manifest's `params` schema. The `node` is the ROS node (or `None` offline). Accept it, and pass it on when your skill calls another skill.
- Return a [SkillResult](../start/glossary.md#skillresult): `SkillResult.success(msg, **data)` or `SkillResult.failure(code, msg)`. Older skills return `{'isdone': bool, 'msg': str}`; both work, because the core turns every return value into a `SkillResult` in one place (`run_skill`). A function that returns `None` counts as a failure in the planner (`code` `unknown`). Prefer returning a code yourself; a text-only failure is read by regular expressions and may come out as `unknown`.
- If the function raises, the registry catches it and the failure text is the exception message. The code is read off that text.
- The failure codes are fixed. The ones a skill uses most:

| Code | Return it when |
|---|---|
| `absent` | You looked and the thing is not there. |
| `unreachable` | You see it but cannot reach it from here. |
| `blocked` | The path or the spot is occupied. |
| `grasp_failed` | The gripper closed on the wrong thing, or on nothing. |
| `perception_unavailable` | You could not look (camera or detector down). This is not evidence that the thing is absent. |
| `hw_fault`, `timeout`, `estop`, `cancelled` | A device fault, no answer in time, emergency stop, the user stopped it. |
| `software` | A bug in the skill. It is never retried. |

All 13 codes and the default recovery for each are on [Skills, results, failure codes](../concepts/skills-and-failures.md#the-failure-codes). The skill's own **precondition check** is not in the hub. It lives in each robot's skills: the hub sees only a failed result with a code.

`tinybot/skills/basic.py`, the `place` function:

```python
def place(node=None, inputs: str = "", **_) -> SkillResult:
    if WORLD["holding"] is None:
        return SkillResult.failure(FailureCode.GRASP_FAILED, "nothing in the gripper")
    if WORLD["at"] != inputs:
        return SkillResult.failure(FailureCode.BLOCKED, f"not at {inputs}")
    WORLD["objects"][WORLD["holding"]] = inputs
    WORLD["holding"] = None
    return SkillResult.success(f"placed at {inputs}")
```

(`WORLD` is the dict of the pretend robot in [Add a new robot](add-a-robot.md#3-write-the-smallest-package).) Real skills do the work through ROS agents, and kcare wraps them with an exception handler (kcare rule 7); `tinybot` does neither.

## 4. Describe it: the SkillSpec

Append an entry to `skills:` in `tinybot/configs/manifest.yaml`:

```yaml
  - name: place
    action: Place
    description: "put the held object down at the place the robot is at"
    params:
      type: object
      properties:
        inputs: {type: string, description: "name of the place"}
      required: [inputs]
    pre: ["arrived(?inputs)", "not hand_empty()"]
    effects: ["hand_empty()", "not holding(*)"]
    verify: [{kind: symbolic}]
    interruptible: false
    errors: [grasp_failed, blocked, software]
```

Each field is explained on [Robot package and core](../contracts/robot-package.md#the-manifest-and-skillspec). The ones to get right:

| Field | What to put |
|---|---|
| `params` | A JSON schema of the keyword arguments. `check_manifest` compares it with the function (step 5). |
| `pre`, `effects` | Predicates. They must agree with the shared action model for a known action. |
| `errors` | Only the codes in the table above, the 13 members of [FailureCode](../start/glossary.md#failurecode). Any other word stops the manifest from loading. |
| `interruptible` | `false` when a stop in the middle is unsafe (the arm holds something): the stop waits until the skill ends. See [interruptible](../start/glossary.md#interruptible). |
| `description` | Optional. When present, the planner's prompt line uses it (step 7). |

**The verify spec.** After a skill reports success, the core can check the outcome. `verify` is a list; each item has a `kind`:

| `kind` | What it does | Example |
|---|---|---|
| `symbolic` | Checks that the declared `effects` hold in the world model. Cheap, always safe. | `{kind: symbolic}` |
| `sensor` | Calls another skill of the robot that reads the outcome. It passes when that skill's `isdone` equals `expect` (default `true`). | `{kind: sensor, skill: grasp_succeed, expect: false}` (kcare's `place`: the gripper must read empty) |
| `vlm` | Asks a yes/no question about a camera image. | `{kind: vlm, prompt: "Is the drawer open?"}` |

The meanings are from [`VerifySpec`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/contract/types.py#L159-L187); the layers are on [Checks before motion](../concepts/checks-before-motion.md) and in the [glossary](../start/glossary.md#verify-layers). `tinybot` has no sensor, so `place` uses `symbolic` only. A `sensor` verify names a second skill, which must also be in the manifest and in `SKILL_CONFIGS`; step 5 shows the error if it is not.

## 5. Check it offline

```text
$ python -m robot_agent.contract.check_manifest <scratch>/repo2/tinybot --strict
manifest <scratch>/repo2/tinybot/tinybot/configs/manifest.yaml (tinybot, 4 skills): 0 error(s), 0 warning(s)
```

Success is `0 error(s)`. Note what the tool checks: every skill **in the manifest** must be in the registry with a matching function. A skill that is in `SKILL_CONFIGS` but not in the manifest is not an error: right after step 2, with the stub and no manifest entry, the check on `tinybot` still printed `3 skills: 0 error(s), 0 warning(s)`. The planner then cannot use the skill (step 7).

Real errors for the usual mistakes. Each was made on a copy of `tinybot` with `place`, then checked with `--strict` (paths shortened):

| Mistake | Output |
|---|---|
| The manifest has `place`, but the table has no `place` line | `ERROR   skill 'place' (Place): not in the skill registry (configs/skills_config.py / skills.json)` |
| A word in `errors:` that is not a code | `ERROR   manifest: skills[3] (place).errors[1]: 'oops' is not a FailureCode (valid: absent, perception_unavailable, unreachable, blocked, grasp_failed, dropped, hw_fault, estop, timeout, cancelled, software, unknown, budget_exceeded)` |
| The function takes no `inputs`, the manifest declares it | `ERROR   skill 'place' (Place): param 'inputs' is not accepted by place() (takes nothing)` |
| The function needs `mode` (no default), the manifest does not declare it | `ERROR   skill 'place' (Place): place() requires 'mode', which the manifest params do not declare` |
| `verify` names a sensor skill the robot does not have | `ERROR   skill 'place' (Place): verify skill 'gripper_full' is not a registered skill` |

Each of these ended with `1 error(s)` and exit code 1. A function parameter **with** a default that the manifest does not declare is accepted (checked: `speed=1.0`, no error). The rules are in [`check_manifest.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/contract/check_manifest.py#L1-L26).

Run the skill alone through the real registry, with a temporary data folder:

```text
>>> run_skill(reg, 'place', {'inputs': 'table'})      # the robot holds nothing yet
SkillResult(ok=False, code=<FailureCode.GRASP_FAILED: 'grasp_failed'>, msg='nothing in the gripper', device=None, data={})
```

That is the failure path of your function. With the stub of step 2 instead, the same call printed `ok=True` and `msg='place stub'`.

## 6. Run a whole task offline

Use the script of [Add a new robot](add-a-robot.md#7-run-a-whole-task-against-the-fake-robot) with four changes: a second place in the map, a goal that moves an object, a planner that is scripted (the manipulation goals need one), and an import. The goal and the plan use all four skills.

```python
from fakes import FakeLLM, FakeState, ScriptedPlanner, run_task, done, steps   # was: no ScriptedPlanner, steps

sc.ENV = {"kitchen": {"loc": {"x": 1.0, "y": 2.0, "rz": 0.0}}, "table": {"loc": {"x": 3.0, "y": 2.0, "rz": 0.0}}}
llm = FakeLLM({"status": "clear", "reason": "", "question": "",
               "goals": [{"type": "on", "object": "apple", "from": "kitchen", "to": "table"}]})
...
loop._llm_client = llm
loop._make_planner = lambda: ScriptedPlanner(steps("MoveTo kitchen", "Find apple", "Pick apple",
                                                   "MoveTo table", "Place table"))
evs = run_task(loop, "Put the apple from the kitchen on the table.")
```

The script ran with the `tinybot` that has `place`, and it also printed the final `WORLD` of the pretend robot. Real output (the first, harmless warning line of the previous guide is also printed first):

```text
{"event": "task_start"}
{"event": "status"}
{"event": "understand", "status": "clear", "reason": ""}
{"event": "status"}
{"event": "gate", "problems": []}
{"event": "plan"}
{"event": "step_start", "index": 0, "action": "MoveTo", "object": "kitchen", "skill": "move"}
{"event": "step_verify", "index": 0}
{"event": "step_done", "index": 0, "status": "success"}
{"event": "step_start", "index": 1, "action": "Find", "object": "apple", "skill": "find"}
{"event": "step_verify", "index": 1}
{"event": "step_done", "index": 1, "status": "success"}
{"event": "step_start", "index": 2, "action": "Pick", "object": "apple", "skill": "pick"}
{"event": "step_verify", "index": 2}
{"event": "step_done", "index": 2, "status": "success"}
{"event": "step_start", "index": 3, "action": "MoveTo", "object": "table", "skill": "move"}
{"event": "step_verify", "index": 3}
{"event": "step_done", "index": 3, "status": "success"}
{"event": "step_start", "index": 4, "action": "Place", "object": "table", "skill": "place"}
{"event": "step_verify", "index": 4}
{"event": "step_done", "index": 4, "status": "success"}
{"event": "status"}
{"event": "goal_check", "ok": true}
{"event": "done", "status": "success"}
DONE success
WORLD {'at': 'table', 'holding': None, 'objects': {'apple': 'table'}}
```

`step_start` at index 4 shows `"skill": "place"`: `bind` found your new skill from the manifest. The world shows the apple at the table and the gripper empty, so the function did its work. `goal_check` is `true`: the goal holds after the run.

**A failure.** Same script, but the apple is in the pantry, not the kitchen (`tinybot.skills.basic.WORLD["objects"]["apple"] = "pantry"` before the registry is built; and each printed event also shows `code` and `msg` when it has them). The scripted planner has no repair plan. Real output, the end of the run; each line is cut to 200 characters by the script:

```text
{"event": "step_done", "index": 1, "status": "failed", "reason": "apple not seen at kitchen"}
{"event": "search", "object": "apple"}
{"event": "replan", "reason": "E: Find apple failed. R: apple not seen at kitchen; it is not at kitchen. Look next at table. S: robot at kitchen, holding nothing; goals: on apple from kitchen to table
{"event": "done", "status": "failed", "code": "absent", "msg": "plan finished but the goals do not hold: on apple from kitchen to table"}
DONE failed
WORLD {'at': 'kitchen', 'holding': None, 'objects': {'apple': 'pantry'}}
```

The message `apple not seen at kitchen` is the text `find` returned, and the code of the final `done` is `absent`: the code your skill gave. The loop marked the kitchen as searched, asked the planner for a repair, got none, and ended with `failed`. How the loop reacts to each code is on [The closed loop](../concepts/closed-loop.md) and [Skills, results, failure codes](../concepts/skills-and-failures.md).

## 7. How the planner learns about the skill

There is **no separate list** to update for the planner. The hub builds the planner's view of the robot from the manifest: the description from `capabilities`, and one line per plan action from the `skills`. The functions are in [`robot_prompt.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/robot_prompt.py#L80-L133); a skill is a plan action when it has a manifest entry whose `action` is not `Observe`, `Verify`, `Ask` or `Query` ([`robot.py:38, 85-89`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/core/planning/robot.py#L85-L89)). Real output of `describe_robot` and `action_lines` on `tinybot`, before and after the `place` entry:

```text
before (3 skills)
tinybot: a robot that has 1 arm with a hand (right), so it holds one object at a time and drives on a mobile base.
MoveTo <place>: move to a place
Find <object>: look for an object where the robot is
Pick <object>: pick up the found object
['Find', 'MoveTo', 'Pick']

after (4 skills)
tinybot: a robot that has 1 arm with a hand (right), so it holds one object at a time and drives on a mobile base.
MoveTo <place>: move to a place
Find <object>: look for an object where the robot is
Pick <object>: pick up the found object
Place <place>: put the held object down at the place the robot is at
['Find', 'MoveTo', 'Pick', 'Place']
```

The text of the `Place` line is the `description` you gave. Without it, the line uses a default sentence for known actions (`put the held object on a place the robot is at`). A skill that is in `skills_config.py` but not in the manifest is **not** in this list, so the planner never proposes it. The robot can still run it when a user or the dashboard calls it by name.

This prompt text is the one that [SAGE and the direct planner](../concepts/planners.md) receive; the hub passes it to the planner and does not rewrite it. Whether a real planner picks the new skill sensibly was not tested: no LLM was called.

## 8. Load it in a running server

Not run here: a server needs ROS (see [Add a new robot](add-a-robot.md#8-what-the-offline-checks-cannot-tell-you)). Read from the code:

1. Restart the agent, as the scaffold says.
2. **If `configs/common/skills.json` exists, also call `POST /skills/reload`.** At boot the hub loads that saved file and skips `skills_config.py` ([`runtime.py:195-204`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/runtime.py#L195-L204)). kcare keeps a `skills.json` in git. The reload re-imports `skills_config`, rebuilds the registry from `SKILL_CONFIGS`, and keeps the plan skills and the aliases of code skills ([`api/skills.py:44-71`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/api/skills.py#L44-L71)). I did not check what happens to a skill added only through `POST /skills`; the code clears the registry before it reloads, so do not rely on it. Then check `GET /skills`.
3. Call the skill alone: `POST /skill/place` with the JSON body `{"inputs": "table"}` ([`api/skills.py:199-205`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/api/skills.py#L199-L205)). This calls the skill through the registry with the robot's ROS node.
4. On the robot, test the skill with the real hardware before a planner uses it. The offline runs above cannot show that your failure codes match what the device does.

Known pitfall: after adding a line to `SKILL_CONFIGS` and restarting, the skill may still be missing. The cause is the saved `skills.json`. See [Robot package and core](../contracts/robot-package.md#current-behaviour-to-know).

## Done when

- `check_manifest <pkg> --strict` prints `0 error(s), 0 warning(s)`.
- The skill returns a code for each way it can fail, and every code is in its `errors:` list.
- A scripted run that uses the skill ends with the effect you expect, and a run where it fails ends with its code.
- In a real server: `GET /skills` lists it after a reload, and a test on the robot passes. (Not run here.)

Back to [Add a new robot](add-a-robot.md). For the idea behind skills as data, see [A robot as data](../concepts/robot-as-data.md).
