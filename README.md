# robot_agent — A FastAPI Runtime for ROS2 Robots

> One codebase. Three execution modes (HTTP API, CLI, Python). Hot-swappable
> skills. Streaming WebSocket camera + plan execution. Built for production
> mobile manipulators.

`robot_agent` is the lightweight runtime that turns any ROS2 robot into a
network-addressable agent. It ships **without** vision models or hardware
drivers — those are plugged in as devices and skills — and is the backbone of
[`kcare_robot`](../kcare_robot), an assistive mobile manipulator with 23
production skills.

The browser console for any `robot_agent` instance is
[`robotapp`](../robotapp), live at <https://robot.aistations.org>.

---

## What's inside (~4.2 K LOC, 26 files)

```
robot_agent/robot_agent/
├── app_factory.py        FastAPI factory · CORS · NumpyJSONResponse · lifespan
├── runtime.py            bootstrap()  — single init path for UI / CLI / Python
├── state.py              AgentState singleton (DeviceManager, SkillRegistry,
│                                              UnifiedAgent, ConfigManager)
├── cli.py                Console-script entry: `<robot_pkg> find::apple`
├── api/                  FastAPI routers (30+ endpoints, see table below)
│   ├── skills.py         registry CRUD · hot reload · /skill/<name> dispatch
│   ├── connects.py       device CRUD · status pings
│   ├── camera.py         WebSocket RGB + depth streamer (~20 fps)
│   ├── agent.py          WebSocket streaming plan execution
│   ├── diagnostics.py    boot errors · skill importability · env snapshot
│   ├── ros.py · buttons.py · skill_configs.py · llm_config.py
└── core/
    ├── skill_registry.py  dual-mode: internal (importlib) + external (HTTP)
    ├── device_manager.py  ROS pub/sub/service/action · WebRTC · TCP · LLM
    ├── unified_agent.py   plan parsing · streaming step events · log capture
    ├── config_manager.py  per-skill overrides · atomic persistence
    └── button_manager.py  quick-action server-side storage
```

---

## Why it's interesting

### Three execution modes from one core

```python
# runtime.py
def bootstrap(robot_pkg: str, *, node_name: str | None = None) -> AgentState:
    """Idempotent. Builds the singleton AgentState exactly once per process."""
```

| Mode | Entry | Use case |
|---|---|---|
| **HTTP** | `uvicorn <pkg>.main:app` | dashboard, multi-user, REST clients |
| **CLI** | `<pkg> find::apple inputs=apple camera=arm` | scripting, CI, demos |
| **Python** | `from <pkg>.skills.find import find; find('apple')` | notebooks, tests |

All three call the same `bootstrap()`. CLI auto-suffixes the rclpy node name
with `_<pid>` to avoid clashing with a running UI on the same host.

### Skill registry — internal + external in one table

```python
# core/skill_registry.py
SkillDef(name='find', type='internal',
         module='kcare_robot.skills.recognition', func='find')

SkillDef(name='detect_face', type='external',
         url='http://gpu-box:9000/detect', method='POST', timeout=15)
```

`POST /skill/<name>` dispatches identically for both. Internal skills are
imported lazily via `importlib`; external skills round-trip over HTTP. Heavy
vision models live on a GPU host, light skills live on the robot — same
contract either way.

### Device manager — six transport types

```python
ConnectType = Literal['ros_service', 'ros_topic', 'ros_action',
                      'webrtc', 'tcp', 'llm']
```

Encode/decode functions are stored as **Python source strings** in the device
config and `exec()`d at registration time. Adding a new ROS service from the
dashboard takes one HTTP POST — no robot restart, no code change.

- **Thread-safe** registry (`threading.Lock` over the connection dict)
- **Atomic persistence** — write to `.tmp`, rotate `.bak`, replace original
- **Lazy ROS init** — one shared `CustomNode` with 4 callback groups, spun in a
  daemon thread

### Real-time WebSocket camera streaming

`GET /ws/camera/{connect_id}` ([api/camera.py](robot_agent/api/camera.py)) is a
~250-line piece worth opening:

- Supports **ROS topic polling**, **WebRTC continuous fetch**, and **ROS
  service/action triggered capture**, behind one client API
- Decodes ROS `sensor_msgs/CompressedImage` (incl. the 12-byte `compressedDepth`
  header), `Image` (`16uc1`, `32fc1`), and raw numpy
- **Auto-ranges depth** via 2nd/98th percentile clipping
- Two depth modes: **TURBO colormap JPEG** (human-friendly) or **zlib uint16
  raw** (decoded in the browser for pixel-accurate mm hover)
- Bridges background stream thread → asyncio send loop via
  `asyncio.run_coroutine_threadsafe()`

### Streaming task execution

`GET /ws/agent` ([api/agent.py](robot_agent/api/agent.py)) yields a typed event
stream as the LLM plan executes:

```
start → status → translated → plan_raw → plan
  → step_start → step_log (with base64 JPEG) → step_done
  → step_start → … → done | error
```

`step_log` carries inline frames (e.g. detection visualisations) so the
dashboard can render them next to the step that produced them.

`_serialize_result()` in [core/unified_agent.py](robot_agent/core/unified_agent.py)
flattens numpy scalars, NaN / Inf, PIL Images, and oversized arrays into
JSON-safe payloads — solving the "you can't `json.dumps` a frame" problem
once, for everyone.

Every run also streams a live **`world`** snapshot. The closed-loop driver
attaches `world=world.to_dict()` to `task_start`/`plan`/`step_done`/`replan`/`done`;
the open-loop / direct path emits a dedicated `world` event (initial + after each
step) via `_emit_world`. So the dashboard "Robot State" panel stays current in
every execution mode.

### Persistent world state — a belief that outlives the plan

A symbolic [`WorldState`](robot_agent/core/planning/base.py)
(`arrived`, `found`, `holding`, `opened`, `on`, `holding_since`, `found_pose`,
`holding_pose`) lives on `AgentState.world` — **one instance per process** (E1 = one robot per
process), so a plan sees what the previous one left behind, instead of starting
blank each time.

- **Survives restart** — `save_world()` / `load_world()` persist it to
  `common_dir/world_state.json` (written after each closed-loop effect, each
  open-loop step, and on `PUT /agent/world`). On reload `arrived` is dropped
  (re-reconciled from the localizer) and the transient `found_pose_stale` flag is
  discarded.
- **Sensor vs belief** — only `arrived` is sensor-derived: `reconcile_world(node,
  world)` matches the localizer against the nearest configured location at each
  plan start. It is robot-overridable through three optional `grace_namemap`
  hooks (`reconcile_world` full override · `robot_xy` pose reader · else a generic
  `mobile_pose` + ENV fallback). `found`/`holding`/`opened`/`on` are **beliefs**
  (no gripper sensor) — `holding_since` timestamps the grasp belief for staleness.
- **`found_pose`** is the detection-time, base-frame geometry of the found object
  (`loc_3d/pose_3d/grasppose/ts/robot_pose`); it is flagged stale once the base
  moves and is **display-only** (a pick that reuses it is a planned follow-up,
  not implemented).

Operators see and correct it in the dashboard via `GET`/`PUT /agent/world`.

### Diagnostics

```bash
curl http://localhost:8001/diagnostics       # python ver, ROS distro,
                                              # skill importability, device status,
                                              # boot error count, log path
curl http://localhost:8001/diagnostics/boot   # full tracebacks, timestamps
```

Boot errors are captured during `bootstrap()` even when the process is
otherwise healthy — failures don't disappear into stderr.

---

## HTTP surface (30+ endpoints)

| Group | Endpoints |
|---|---|
| **Skills** | `GET /skills` · `GET /skills/status` · `POST /skill/{name}` · `POST /skills` · `PUT /skills/{name}` · `DELETE /skills/{name}` · `POST /skills/reload` |
| **Skill configs** | `GET /skill-configs` · `GET /skill-configs/{name}` · `PUT /skill-configs/{name}` |
| **Devices / connections** | `GET /connects` · `GET /connects/status` · `POST /connects` · `PUT /connects/{id}` · `DELETE /connects/{id}` · `POST /connects/{id}/set_active` |
| **Direct ROS dispatch** | `POST /agent/{name}/send` |
| **ROS discovery** | `GET /ros/scan` |
| **Streaming** | `WS /ws/camera/{id}` · `WS /ws/agent` |
| **Agent / LLM** | `POST /agent/llm-config` · `POST /agent/api-key` · `GET /agent/api-keys` |
| **World state** | `GET /agent/world` · `PUT /agent/world` (partial edit of the persistent robot belief) |
| **Planner guides** | `GET /guides` · `GET /guides/{name}` · `POST /guides` · `PUT /guides/{name}` · `DELETE /guides/{name}` · `POST /guides/{name}/activate` (versioned LLM guide) |
| **Quick buttons** | `GET /buttons` · `POST /buttons` · `PUT /buttons/{id}` · `DELETE /buttons/{id}` · `POST /buttons/reorder` · `POST /buttons/bulk` |
| **Diagnostics** | `GET /diagnostics` · `GET /diagnostics/boot` |

Live OpenAPI / Swagger at `http://<host>:8001/docs`.

---

## Install & run

```bash
pip install -e .                       # only fastapi + uvicorn[standard]
                                       # (ROS2, pyconnect supplied by host env)

# Provide a skill package via env var, then start:
export ROBOT_SKILLS_PKG=kcare_robot
uvicorn kcare_robot.main:app --host 0.0.0.0 --port 8001
```

The skill package only needs to export a flat `SKILL_CONFIGS: dict[str,
tuple[module, func_name]]` — see [`kcare_robot/configs/skills_config.py`](../kcare_robot/kcare_robot/configs/skills_config.py)
for a 23-entry example, or scaffold a new one with
[`robot_template`](../robot_template).

### Environment

| Var | Purpose |
|---|---|
| `ROBOT_SKILLS_PKG` | Python package providing `configs.skills_config:SKILL_CONFIGS` |
| `ROBOT_SKILLS_PATH` | Optional `sys.path` fallback |
| `ROBOT_AGENT_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
| `ROBOT_AGENT_DEBUG_RESPONSE` | `1` to attach full tracebacks to every skill error response |

---

## Design choices worth defending in an interview

- **No decorators for skill registration** — `SKILL_CONFIGS` is a plain dict;
  static, greppable, no import-time magic, no metaclasses.
- **`AgentState` is a singleton, not a global** — passed explicitly via
  `app.state.agent_state`; a process-local `_CURRENT` ref exists only for
  callbacks that run off-request (e.g. ROS subscriptions).
- **Encode/decode as code, not config** — `exec()` of trusted source strings
  is the right primitive for transforming ROS messages without forcing a
  per-message schema language.
- **Atomic persistence everywhere** — `.tmp` → `.bak` → final, on every save
  of `skills.json`, `connects.json`, `buttons.json`, `skill_configs.json`.
- **One ROS node per process** with 4 callback groups, instead of a node per
  client — discovers everything, spins once, cleans up in FastAPI's
  `lifespan` shutdown.

---

## Related

- [`kcare_robot`](../kcare_robot) — reference robot package (23 skills,
  RealSense D405 wrist cam, Femto Bolt head stereo, Nav2 base, RB-class arm)
- [`robotapp`](../robotapp) — Next.js 14 ops dashboard
  (<https://robot.aistations.org>)
- [`robot_template`](../robot_template) — cookiecutter scaffold for new robot
  packages
