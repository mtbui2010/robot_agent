---
sources: [robot_agent:pyproject.toml, robot_agent:Makefile, robot_agent:robot_agent/runtime.py, robot_agent:robot_agent/app_factory.py, robot_agent:robot_agent/connect/llm/__init__.py, robot_agent:tests/fakes.py, kcare_robot:Makefile, kcare_robot:README.md, kcare_robot:CLAUDE.md, kcare_robot:kcare_robot/main.py, kcare_robot:pyproject.toml, robotapp:Makefile, robotapp:CLAUDE.md, robotapp:frontend/lib/api.ts, kcare_sim:README.md, kcare_sim:Makefile, kcare_sim:mk/mujoco.mk, kcare_sim:mk/issac.mk, kcare_sim:docs/mujoco_flow.md, kcare_sim:docs/issac_flow.md, kcare_sim:sim/scripts/setup_sim.sh, kcare_sim:sim/README.md, kcare_sim:sim/scripts/run_house.sh, kcare_sim:sim/scripts/sync_kcare_code.sh, kcare_sim:symbolic/serve.py, symbolic_robot:README.md, symbolic_robot:Makefile, symbolic_robot:symbolic_robot/server.py, embodiplan:pyproject.toml]
verified_at: {robot_agent: 3269923, kcare_robot: d4f5577b342e, robotapp: 4a810a3717ab, kcare_sim: c6e32368f1cf, symbolic_robot: 587664465e76, embodiplan: 76da520}
---
# Run the whole system

This page lists, for four set-ups, what each repo needs to start, in which order, and how to check that each step is up. Only the first set-up was run for this page. The other three are taken from each repo's own Makefile and README and are marked "not run".

!!! abstract "In this page"
    - **A. Fully offline** runs the hub's loop on a fake robot with a scripted language model. No ROS, no GPU, no server. It is the only set-up run for this page, and its real output is shown.
    - **B. Symbolic simulator with the hub**, **C. Physics simulation** and **D. Real robot** are not run. B needs a hub that can start without ROS 2 (the switch `ROBOT_AGENT_ROS=off`, in the hub since `3269923`; see below). C needs a GPU, ROS 2, MuJoCo and docker. D needs the robot and ROS 2.
    - Where the repos do not say what to do, this page says "not documented" and points at the file.

!!! note "A hub that starts without ROS 2"
    From `3269923` the hub reads `ROBOT_AGENT_ROS`. With `off` (also `0`, `false`, `no`) it imports no `rclpy` and starts no node; the default is `on`, so a real robot behaves as before ([`runtime.py:141-150`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/runtime.py#L141-L150)). The older pin `ad501cf15ece` had no switch: it stopped with an error when ROS 2 was missing. The two symbolic simulators set the switch ([`symbolic_robot/server.py:74`](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/symbolic_robot/server.py#L74), [`kcare_sim/symbolic/serve.py:56`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/symbolic/serve.py#L56)), so they need a hub at that revision or later. The MuJoCo set-up in this workspace runs against a synced copy of the hub; it was not compared with `3269923`. The switch was read in the code; no simulator was started. See [known pitfalls](../reference/known-pitfalls.md).

## What each set-up needs

| | A. Offline fake robot | B. Symbolic simulator | C. Physics simulation | D. Real robot |
|---|---|---|---|---|
| Hub (`robot_agent`) | Python source tree only | Yes, `3269923` or later (it has the ROS switch) | Yes, a copy of it on the host (`KCARE_CODE` or the synced copy), used in a container | Yes, `main` |
| ROS 2 | No | No | Yes, Humble, in the container | Yes |
| GPU | No | No | Yes (MuJoCo rendering with EGL; a vision server if you call `find`) | Not documented for the robot; the vision service is not documented |
| MuJoCo | No | No | Yes | No |
| Language model server | No (scripted) | Yes for planner runs: Ollama; the default address is on the same machine | Yes: connections point at the model servers | Yes: an `llm` entry in the site's `connections.json` |
| Dashboard | Cannot connect (no server) | Yes | Yes | Yes |
| Run for this page | **Yes** | No | No | No |

The language model is chosen by an `llm` entry in the site's [`connections.json`](glossary.md#connections-json). The hub starts the backend named in the entry's `name` key: `llama`, `chatgpt` or `openai`, or `gemini` ([`connect/llm/__init__.py:6-10`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/connect/llm/__init__.py#L6-L10)).

## A. Fully offline with the hub's fake robot

This runs the [AssistLoop](glossary.md#assistloop) from the hub on the hub's symbolic fake robot. The language model and the planner are scripted by the test code (the fakes are in [`tests/fakes.py`](https://github.com/mtbui2010/robot_agent/blob/3269923/tests/fakes.py)). Nothing is started, so the dashboard cannot connect to this set-up. It shows what the loop does with understanding, the [gate](glossary.md#certified-gate), a failed step and a [replan](glossary.md#replan).

What it needs: a Python 3 with `pyyaml`. It needs no `numpy`, no `fastapi`, no ROS and no network. The script stubs the module `requests`, because `embodiplan` imports it and the scripted planner never calls it. On the machine used to check this page the default `python3` has no `pyyaml`, so the run used `/usr/bin/python3`.

1. Put the hub and the planner library side by side: a folder `<workdir>` that holds `robot_agent` and `embodiplan`. (`embodiplan` does not have to be installed; the script adds both folders to `sys.path`.)
2. Save this script as `fake_run.py` outside both repos. It mirrors `make_assist` in the hub's `tests/conftest.py`, except that it leaves the engine's default options as they are. `look_then_ask`, `bring_ask` and `bt_goal` are on by default; `conftest.py` now turns them off for the older tests. For this request they change nothing in the output below.

```python
"""AssistLoop (planner 'sage') on robot_agent's symbolic FakeRobot: scripted LLM + scripted planner, no ROS/LLM/network.
Usage: REPOS=<dir holding robot_agent and embodiplan> python -B fake_run.py <out_dir>   (mirrors tests/conftest.py make_assist)"""
import json, os, sys, types
from pathlib import Path
REPOS, OUT = Path(os.environ["REPOS"]), sys.argv[1]
sys.path[:0] = [str(REPOS / "robot_agent"), str(REPOS / "robot_agent" / "tests"), str(REPOS / "embodiplan")]
sys.modules.setdefault("requests", types.ModuleType("requests"))   # embodiplan imports it; nothing here calls it
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, FakeState, FakeSkillRegistry, load_env, run_task, done, steps
import robot_agent.state as state_mod, robot_agent.core.planning.assist_loop as AL, robot_agent.core.planning.loop as LP
import robot_agent.skill_configs as sc
from robot_agent.core.run_control import begin_run
st = FakeState(Path(OUT))
for m in (AL, LP, state_mod): m.current = lambda: st
for m in (AL, LP): m.host_id = lambda: "test"
env = load_env(); sc.ENV = env
AL.reconcile_world = lambda node, world, threshold=0.8: world
begin_run()
DT, DS, CT = "dining table@kitchen", "desk@study room", "counter@kitchen"
robot = FakeRobot(env, {"apple": CT, "mug": DS, "banana": DT})          # the apple is NOT on the desk
llm = FakeLLM({"status": "clear", "reason": "", "question": "",
               "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]})
planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"),
                          replans=[steps(f"MoveTo {CT}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")])
agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(robot), device_manager=types.SimpleNamespace(_ros_node=None),
                              _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
loop = AL.AssistLoop(agent, emit=None, log_dir=OUT + "/runs", live_path=OUT + "/live.jsonl", memory_path=OUT + "/om.json",
                     mute_skill_tts=False, speak_backend=False, vlm_enabled=False)
loop._llm_client, loop._make_planner = llm, (lambda: planner)
evs = run_task(loop, "Bring the apple from the desk to the dining table.")
for e in evs:
    print(json.dumps({k: e[k] for k in ("event", "index", "action", "object", "skill", "status", "ok", "reason", "problems") if k in e})[:200])
print("TRACE", robot.trace)
print("DONE", done(evs).get("status"))
```

3. Run it from the folder that holds the script, not from inside a repo, and give it an **empty** `<out_d>` each time: the loop writes its object memory there, and a second run in the same folder searches in another order (in the check for this page a second run ended `failed`). `-B` and `PYTHONDONTWRITEBYTECODE=1` keep Python from writing bytecode files into the repos. Do not add `-I`: it ignores `PYTHONDONTWRITEBYTECODE`.

```bash
REPOS=<workdir> PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B fake_run.py <out_dir>
```

4. Check the output. A good run prints 33 event lines, then `TRACE` and `DONE success`. This is the real output of the run for this page, repeated at `robot_agent` `3269923` with `embodiplan` `76da520` and identical to the earlier run at `ad501cf15ece` (the script cuts each line at 200 characters, so the `replan` line is cut):

```text
teleop stop_all: 'FakeState' object has no attribute 'cm'
{"event": "task_start"}
{"event": "reconcile"}
{"event": "status"}
{"event": "understand", "status": "clear", "reason": ""}
{"event": "status"}
{"event": "gate", "problems": []}
{"event": "plan"}
{"event": "step_start", "index": 0, "action": "MoveTo", "object": "desk@study room", "skill": "move"}
{"event": "step_verify", "index": 0}
{"event": "step_done", "index": 0, "status": "success"}
{"event": "step_start", "index": 1, "action": "Find", "object": "apple", "skill": "find"}
{"event": "step_verify", "index": 1}
{"event": "step_done", "index": 1, "status": "failed", "reason": "apple not found"}
{"event": "search", "object": "apple"}
{"event": "replan", "reason": "E: Find apple failed. R: apple not found; it is not at desk@study room. Look next at sofa@living room. S: robot at desk@study room, holding nothing; goals: on apple from
{"event": "step_start", "index": 1, "action": "MoveTo", "object": "counter@kitchen", "skill": "move"}
{"event": "step_verify", "index": 1}
{"event": "step_done", "index": 1, "status": "success"}
{"event": "step_start", "index": 2, "action": "Find", "object": "apple", "skill": "find"}
{"event": "step_verify", "index": 2}
{"event": "step_done", "index": 2, "status": "success"}
{"event": "step_start", "index": 3, "action": "Pick", "object": "apple", "skill": "pick"}
{"event": "step_verify", "index": 3}
{"event": "step_done", "index": 3, "status": "success"}
{"event": "step_start", "index": 4, "action": "MoveTo", "object": "dining table@kitchen", "skill": "move"}
{"event": "step_verify", "index": 4}
{"event": "step_done", "index": 4, "status": "success"}
{"event": "step_start", "index": 5, "action": "Place", "object": "dining table@kitchen", "skill": "place"}
{"event": "step_verify", "index": 5}
{"event": "step_done", "index": 5, "status": "success"}
{"event": "status"}
{"event": "goal_check", "ok": true}
{"event": "done", "status": "success"}
TRACE [('move', 'desk@study room', True, 'arrived at desk@study room'), ('find', 'apple', False, 'apple not found'), ('move', 'counter@kitchen', True, 'arrived at counter@kitchen'), ('find', 'apple', True, ''), ('pick', 'apple', True, 'grasped'), ('move', 'dining table@kitchen', True, 'arrived at dining table@kitchen'), ('place', 'dining table@kitchen', True, 'placed'), ('find', 'apple', True, '')]
DONE success
```

How to read it:

- The first line is a warning from the test double (`FakeState` has no `cm`). It is harmless for this run and is printed first because it goes to the error stream.
- `understand` with `clear`, then `gate` with an empty `problems` list: the command was understood and the plan passed the gate. See [Checks before motion](../concepts/checks-before-motion.md).
- The first `Find apple` fails ("apple not found", the apple is not on the desk). The loop sends `search` and `replan`, and the second plan goes to the counter. See [Closed loop](../concepts/closed-loop.md) and [Belief and reconcile](../concepts/belief-and-reconcile.md).
- `goal_check` with `ok: true`, then `done` with `success`. The `TRACE` line is what the fake robot was asked to do, in order.

This is a scripted result on a fake robot. It says nothing about a real robot or a simulator. The repeat ran in a clean checkout of `3269923` (only the site files differed). `pytest` is not installed here, so `tests/test_assist_loop_fake_robot.py` was not run.

## B. The symbolic simulator with the hub

Not run. It needs a hub with the ROS switch (see the note at the top): `3269923` or later. The simulator serves the hub's API.

Two symbolic simulators exist. `symbolic_robot` is the official one (and the benchmark). The `symbolic/` folder in `kcare_sim` is the older copy that `symbolic_robot` replaces. Its README says its server is a headless backend for benchmark runs and not for live demos ([README](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/README.md#L3-L5)); it offers the hub's API (`/ws/agent`, `/skill/<name>`), so the dashboard can connect to it, but connecting the dashboard to it is not documented. The `kcare_sim` copy does document it: its `make run` message tells you to add the URL as a robot in the dashboard.

| Order | Piece | Needs | Command (from the repo) | How to check |
|---|---|---|---|---|
| 1 | `embodiplan` | Python | `pip install -e ../embodiplan` (not on PyPI; [`pyproject.toml:22-24`](https://github.com/mtbui2010/robot_agent/blob/3269923/pyproject.toml#L22-L24) of the hub) | `import embodiplan` works |
| 2 | `robot_agent` with the ROS switch | Python, `numpy==1.26.4`. The switch is in `3269923` and later; the symbolic_robot README does not name a revision | The simulator is pointed at it with `ROBOT_AGENT_DIR` in `env.local.mk` ([Makefile](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/Makefile#L16-L32)) | Not documented |
| 3 | `symbolic_robot` | Python 3.10 or newer, `pip install -e .[server]` ([README](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/README.md#L33-L35)) | none; it is started in step 5 | `python -m symbolic_robot list` prints domains and profiles |
| 4 | a language model server | Ollama; the server writes an `llm` entry for it into the site's `connections.json` before the hub boots ([`server.py:83-88`](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/symbolic_robot/server.py#L83-L88)). The address defaults to the same machine ([`server.py:17`](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/symbolic_robot/server.py#L17)) | start Ollama (not documented in this repo) | Not documented |
| 5 | the simulator server | the above | `make run ROBOT=home PORT=8121` (`ROBOT=factory` for the factory domain, default port 8091) | The Makefile waits up to 90 seconds for `GET /agent/mobile_pose/get` and prints `up: ...`; `make status` and `make logs` also work ([`Makefile`](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/Makefile#L54-L72)) |
| 6 | the dashboard | Node (section D, step 6) | `make run-frontend` in `robotapp` | Add `http://<hub_host>:<port>` as a robot in the dashboard (documented for the `kcare_sim` copy; not documented for `symbolic_robot`) |

To stop it, run `make terminate PORT=8121`. It stops only the instance this Makefile started, by its pid file.

The older copy in `kcare_sim` is started with `make run SIM=symbolic PORT=8001` after `make install` (a conda environment with `pyyaml`, `pytest` and `numpy`). It needs `robot_agent` and the `kcare_robot` configs on disk, and a file `symbolic/env.local.mk` that sets `PY`, `ROBOT_AGENT_DIR` and `KCARE_ROBOT_DIR` ([README](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/README.md#L116-L121)). `make run` first checks the imports and says what is missing ([`Makefile:103`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/Makefile#L103)). It uses the same check URL and the same Ollama default.

Safety: a symbolic simulator moves nothing. Still, the dashboard sends real commands to whatever URL is active; check the active robot's name before you press Run.

## C. The physics simulation (`kcare_sim`)

Not run. Everything in this section is described from the committed files. It needs docker, the NVIDIA container runtime, ROS 2 Humble (inside the container), MuJoCo and a GPU for rendering. The container is built on the robot maker's base image, which is not in this repo. If the image is missing, `make build-mujoco` stops and says how to build it ([`mk/mujoco.mk:47-51`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/mk/mujoco.mk#L44-L48)). The unmodified `kcare_robot` software drives the simulated robot, because the simulator answers on the real robot's ROS 2 names. The container is where ROS runs; on the host nothing needs ROS. Its own docs are at <https://mtbui2010.github.io/kcare_sim/>.

The house starts with three commands from the repo root. The README and [`docs/mujoco_flow.md`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/docs/mujoco_flow.md) give them ([`README.md:46-56`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/README.md#L46-L56)). They were not run here.

| Order | Command (repo root) | What it does, from the files | How to check |
|---|---|---|---|
| 1 | `make build-mujoco` | Builds the image `kcare-sim:humble` from the maker's base image, makes the data folder, creates the container `kcare_sim` and, inside it, runs `sim/scripts/setup_sim.sh setup`. That script builds the ROS messages, the house scene and the kcare site. Each step skips what is already done and the first failing step stops the build ([`mk/mujoco.mk:38-79`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/mk/mujoco.mk#L35-L76)). | It ends with `[build-mujoco] ready: ...`; a missing item is listed as `MISSING` with the command that makes it ([`mujoco_flow.md:40`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/docs/mujoco_flow.md#L40)) |
| 2 | `make run-sim` | Opens a shell in the container at `/sim`, with ROS 2 sourced. It needs a terminal and starts a stopped container ([`mk/mujoco.mk:81-85`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/mk/mujoco.mk#L78-L82)). | Not documented beyond the message it prints |
| 3 | `make run SIM=mujoco` | Starts the house: it starts a stopped container, runs `setup_sim.sh check`, refuses if another process holds the API port, then runs `run_house.sh start` (the simulator bridge and the `kcare_robot` API on port 8001; `PORT=<n>` changes the port) and prints the URL and whether the VLA node is on or off ([`mk/mujoco.mk:87-98`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/mk/mujoco.mk#L84-L95)). Before this change the command only printed instructions. | `make status SIM=mujoco` (use the same `PORT`); the script waits for `sim ready`, then for the API and for joint states |

`make run SIM=mujoco VLA=1` also starts the VLA drawer node. It is **off by default**, and it needs `vla-serve` on port 11450 with a policy loaded. `make terminate SIM=mujoco` stops the house and leaves the container running. `make logs SIM=mujoco` shows the log tails ([`mk/mujoco.mk:100-112`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/mk/mujoco.mk#L100-L112)). `make build-container` is the old name: it only prints that it was renamed and fails ([`Makefile:80-82`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/Makefile#L80-L82)).

Some things the build does not do by itself. The network is used only when you ask: `INSTALL_KCARE_VENV=1` runs pip for the venv that the `kcare_robot` API needs, and `FETCH_ASSETS=1` fetches the object scans (about 450 MB); without the scans the house has no objects ([`mujoco_flow.md:34-37`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/docs/mujoco_flow.md#L34-L37)). The robot software (`robot_agent`, `kcare_robot`, `embodiplan`) is not in the container; the local compose file mounts it from a host folder, or you copy it with the sync script described below ([`mujoco_flow.md:35`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/docs/mujoco_flow.md#L35)). The pip list for the venv is in [`setup_sim.sh:23-24`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/sim/scripts/setup_sim.sh#L23-L24) (fastapi, uvicorn, pydantic, msgpack, requests, ollama, openai, pyzmq, websockets, visionserve).

After the house is up:

| Order | Piece | Needs | Command | How to check |
|---|---|---|---|---|
| 4 | a call to test | the model and vision servers if you call `find` | `POST :8001/skill/move {"inputs": "dining table@kitchen"}` ([`sim/README.md:124`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/sim/README.md#L124)) | The skill returns a result |
| 5 | the dashboard | Node | as in D, step 6; add `http://<hub_host>:8001` | |

The two VLA nodes import a ROS action (`max_interfaces.action.OpenDrawer`). Earlier the repo did not have it and the nodes could not start. It is now rebuilt in `sim/ros/max_interfaces` from the fields the skills use and built by `make msgs`; the repo says it is **unverified against the real robot's package**. `setup_sim.sh` reports it as a note, never as `MISSING` ([`mujoco_flow.md:62-70`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/docs/mujoco_flow.md#L62-L70)). Not run here.

**Isaac Lab (not run, unverified by its authors).** The repo also has an Isaac Lab mode in the same container, in its own venv. `make build-issac ISSAC_SPEC='isaaclab[isaacsim,all]==<version>'` prepares it and `make run SIM=issac` loads the robot's URDF on a ground plane without a window and writes a JSON file of joint data. The README says "Nothing of this has been run against a real Isaac Lab yet", so treat it as unverified ([`README.md:58-68`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/README.md#L58-L68), [`docs/issac_flow.md`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/docs/issac_flow.md)). It shares the GPU with the house: run one at a time.

Two things about the order. `run_house.sh start` runs the starts one after the other (the bridge, the VLA node only when `VLA=1`, then the `kcare_robot` API), so a missing `vla-serve` prints a message and the `kcare_robot` start still goes on. And the script's own comment says the container uses `ROS_DOMAIN_ID` 77, "nothing here can reach the real robot" ([`run_house.sh:12`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/sim/scripts/run_house.sh#L12)); this is the script's statement, not checked here.

The code in `sim/kcare_code` is a copy made by the sync script, not your checkout. It is not tracked by git. After you change `robot_agent` or `kcare_robot` on the host, run the sync script and `run_house.sh restart-kcare` ([script header](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/sim/scripts/sync_kcare_code.sh#L2-L3)).

## D. The real robot

Not run, and not run on a real robot. The robot package here is `kcare_robot`. Read its [`CLAUDE.md`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/CLAUDE.md#L4) first: it says the package runs on the real robot, so changes must stay backward compatible.

| Order | Piece | Needs | Command (from the repo) | How to check |
|---|---|---|---|---|
| 1 | the robot's own ROS 2 drivers and workspace | ROS 2 Humble and the robot maker's colcon workspace (`rosinterfaces` and the maker's messages) | Not documented in these repos | Not documented |
| 2 | `embodiplan`, `robot_agent`, `visionserve` | Python; `numpy==1.26.4`; Python version equal to the one ROS was built for | `make install` in `kcare_robot` creates a conda env and installs the three from sibling folders or PyPI ([`Makefile:541-556`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/Makefile#L541-L556)); `make check-deps` checks they exist first | The install ends by importing every non-ROS dependency ([README](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/README.md#L159-L170)) |
| 3 | the pre-flight check | ROS 2 sourced | `make doctor` | It checks ROS 2, `rosinterfaces` and that every skill imports ([`Makefile:676-677`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/Makefile#L676-L677)) |
| 4 | a vision service and a language model server | `visionserve` is a dependency of `kcare_robot`; the `llm` entries are in the site's `connections.json` | Not documented in these repos how to start them | Not documented |
| 5 | the hub with the robot package | the above | `make run` in `kcare_robot` starts `uvicorn kcare_robot.main:app --host 0.0.0.0 --port 8001 --reload` ([`Makefile:609-610`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/Makefile#L609-L610)) | `make smoke-live` lists the registered skills with `curl` against `http://localhost:8001/skills` ([`Makefile:685-687`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/Makefile#L685-L687)) |
| 6 | the dashboard | Node and npm | `make install-frontend` then `make run-frontend` in `robotapp` (port 3007, [`Makefile:14-18`](https://github.com/mtbui2010/robotapp/blob/4a810a3717ab/Makefile#L14-L18)) | Open the dashboard and add the hub as a robot with its URL `http://<hub_host>:8001`. The dashboard keeps the list of robots in the browser ([`api.ts:11-61`](https://github.com/mtbui2010/robotapp/blob/4a810a3717ab/frontend/lib/api.ts#L11-L61)) |

The hub itself has no entry point; the robot package starts it by calling `create_app` ([`app_factory.py:1-12`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/app_factory.py#L1-L12), [`kcare_robot/main.py:29`](https://github.com/mtbui2010/kcare_robot/blob/d4f5577b342e/kcare_robot/main.py#L29)). Install the hub's own parts with `pip install -e .` or `make install` ([`Makefile:132-137`](https://github.com/mtbui2010/robot_agent/blob/3269923/Makefile#L132-L137)); `make install` does not install `embodiplan`, so install it first.

Safety, from the repos' own rules:

- Two dashboards driving one robot is unsafe: Cancel stops both ([`robotapp/CLAUDE.md` rule 7](https://github.com/mtbui2010/robotapp/blob/4a810a3717ab/CLAUDE.md#L14)).
- Ask before motion on a real robot ([rule 8](https://github.com/mtbui2010/robotapp/blob/4a810a3717ab/CLAUDE.md#L15)).
- Start with a plan-only run, if the dashboard offers it: the `plan_only` field of `/ws/agent` generates a plan without executing it ([`agent.py:442`](https://github.com/mtbui2010/robot_agent/blob/3269923/robot_agent/api/agent.py#L442)).

## Not documented

| Question | Where it should be, and what is there |
|---|---|
| Which `robot_agent` revision do the simulators run against? | A hub with the `ROBOT_AGENT_ROS=off` switch: `3269923` or later. [`kcare_sim/symbolic/serve.py`](https://github.com/mtbui2010/kcare_sim/blob/c6e32368f1cf/symbolic/serve.py) and [`symbolic_robot/README.md`](https://github.com/mtbui2010/symbolic_robot/blob/587664465e76/README.md#L35) only say "with the `ROBOT_AGENT_ROS=off` switch" and name no revision. What the MuJoCo set-up's synced copy contains was not compared. |
| In which order do the robot drivers, the vision service, the language model server and the hub start on the real robot? | Not in `kcare_robot/README.md` or its `Makefile`. They give `make doctor` then `make run` only. |
| How to start Ollama for the symbolic set-up? | Not in `symbolic_robot` or `kcare_sim`. They only write the entry into `connections.json`. |

See also [Architecture](architecture.md), [Robot, hardware and simulators](../contracts/robot-and-simulators.md) and [Repo map](repo-map.md).
