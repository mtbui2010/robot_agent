"""Test doubles: a symbolic fake robot, a scripted LLM and a scripted planner.

Adapted from the offline SAGE bench harness (FakeRobot driving the real
AssistLoop), without ollama / embodiplan: every LLM answer and every plan is
scripted by the test. Import with ``from fakes import ...`` (tests/ is on
sys.path, see conftest.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from robot_agent.core.planning.base import WorldState

DATA = Path(__file__).resolve().parent / "data"


def load_env() -> dict:
    """The sim_house ENV (13 places, aliases in ko) used by the SAGE bench."""
    return json.loads((DATA / "env_sim_house.json").read_text())


# ─────────────────────────── fake LLM ───────────────────────────
class FakeLLM:
    """Answers ``chat(prompt=..., format=...)`` from a script.

    ``script`` items are matched in order: a ``(regex, answer)`` pair answers the
    first prompt the regex finds, a bare string / dict answers the next call.
    Dict answers are returned as JSON text (what an LLM with ``format`` gives).
    Every call is recorded in ``calls`` as ``(prompt, format)``.
    """

    def __init__(self, *script, default=None):
        self.script = list(script)
        self.default = default
        self.calls: list[tuple[str, object]] = []

    def _text(self, a):
        return json.dumps(a) if isinstance(a, (dict, list)) else str(a)

    def chat(self, prompt="", format=None, **kw):
        self.calls.append((prompt, format))
        for k, item in enumerate(self.script):
            if isinstance(item, tuple):
                rx, ans = item
                if re.search(rx, prompt, re.S):
                    del self.script[k]
                    return self._text(ans)
            else:
                del self.script[k]
                return self._text(item)
        if self.default is not None:
            return self._text(self.default)
        raise AssertionError(f"FakeLLM: no scripted answer for prompt {prompt[:120]!r}")

    def chat_guide(self, prompt, guide=None, reuse=False, **kw):
        return self.chat(prompt=prompt, **kw)


# ─────────────────────────── scripted planner ───────────────────────────
class ScriptedPlanner:
    """Planner protocol double: ``generate_plan`` returns ``plan``, each
    ``replan`` the next item of ``replans`` (``[]`` once they run out)."""

    def __init__(self, plan, replans=()):
        self.plan = [dict(s) for s in plan]
        self.replans = [[dict(s) for s in r] for r in replans]
        self.calls: list[tuple] = []

    def generate_plan(self, task, obs, visible_objects):
        self.calls.append(("generate_plan", task))
        return [dict(s) for s in self.plan], {"method": "scripted"}

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        self.calls.append(("replan", dict(failed_step), failure_reason))
        return (self.replans.pop(0) if self.replans else []), {"method": "scripted"}


def steps(*items: str) -> list[dict]:
    """``steps('MoveTo desk@study room', 'Find apple')`` -> PlanStep dicts."""
    out = []
    for it in items:
        a, _, o = it.partition(" ")
        out.append({"action": a, "object": o})
    return out


# ─────────────────────────── fake robot (symbolic house) ───────────────────────────
class FakeRobot:
    """Symbolic house + kcare-like skills (move, find, pick, place, open_drawer,
    close_drawer, ask, mobile_pose, grasp_succeed).

    ``objects``: name -> place, or ``'<place>/in'`` for inside a drawer.
    ``faults``: dicts ``{skill, obj (substring or None), nth (1-based call count
    of that skill+obj, a list, or 'all'), kind, ...}``; kinds: fail | raise |
    slip | wrong_pose | false_positive | drop | place_fall | partial_open |
    swerr | move_object (side effect, then the call proceeds) | no_pose.
    """

    def __init__(self, env, objects, answers=(), faults=(), opened=(), start="entrance", reply_rules=None):
        self.env = env
        self.obj = dict(objects)
        self.opened = set(opened)
        self.partial = set()
        self.at = start
        self.pose_off = 0.0
        self.holding = None
        self.found = None
        self.answers = list(answers)
        self.reply_rules = reply_rules or []
        self.faults = [dict(f) for f in faults]
        self.counts = {}
        self.trace = []
        self.asked = []

    def _fault(self, skill, o):
        key = (skill, o)
        self.counts[key] = self.counts.get(key, 0) + 1
        n = self.counts[key]
        for f in self.faults:
            if f["skill"] != skill or (f.get("obj") and f["obj"] not in (o or "")):
                continue
            nth = f.get("nth", 1)
            if nth == "all" or nth == n or (isinstance(nth, (list, tuple)) and n in nth):
                return f
        return None

    def _visible_here(self, o):
        p = self.obj.get(o)
        if p is None:
            return False
        if p.endswith("/in"):
            return p[:-3] == self.at and p[:-3] in self.opened and p[:-3] not in self.partial
        return p == self.at

    def _match(self, name):
        n = (name or "").strip().lower()
        if n in self.obj:
            return n
        c = [k for k in self.obj if n and (n in k or k in n)]
        return c[0] if len(c) == 1 else None

    def execute(self, skill, params, node=None, log_fn=None):
        o = (params or {}).get("inputs", "")
        r = self._exec(skill, o, params or {})
        if skill not in ("mobile_pose", "grasp_succeed"):
            self.trace.append((skill, o, bool(r.get("isdone")), r.get("msg", "")))
        return r

    def _exec(self, skill, o, params):
        if skill == "ask":
            q = o
            self.asked.append(q)
            for rule in self.reply_rules:
                if re.search(rule["if"], q, re.I):
                    return {"isdone": bool(rule["then"]), "text": rule["then"]}
            if re.search(r"is that right\?", q, re.I) and not (self.answers and self.answers[0].lower().startswith("no")):
                return {"isdone": True, "text": "yes"}
            if self.answers:
                a = self.answers.pop(0)
                return {"isdone": bool(a), "text": a}
            return {"isdone": False, "text": ""}
        if skill == "mobile_pose":
            f = self._fault("mobile_pose", None)
            if f and f["kind"] == "no_pose":
                raise RuntimeError("localization lost: no /odom")
            loc = (self.env.get(self.at) or {}).get("loc") or {"x": 0, "y": 0, "rz": 0}
            return {"isdone": True, "pose": (loc["x"] + self.pose_off, loc["y"], loc.get("rz", 0))}
        if skill == "grasp_succeed":
            f = self._fault("grasp_succeed", self.holding)
            if f and f["kind"] == "drop" and self.holding:
                self.obj[self.holding] = "floor"
                self.holding = None
            return {"isdone": self.holding is not None}

        f = self._fault(skill, o)
        if f and f["kind"] == "move_object":
            self.obj[f["what"]] = f["to"]
            f = None
        if f and f["kind"] == "raise":
            raise RuntimeError(f.get("msg", "action server timeout"))
        if f and f["kind"] == "swerr":
            return {"isdone": False, "msg": "TypeError: execute() got an unexpected keyword argument 'type'"}
        if f and f["kind"] == "fail":
            return {"isdone": False, "msg": f.get("msg", f"{skill} failed")}

        if skill == "move":
            if o not in self.env:
                return {"isdone": False, "msg": f"unknown location {o}"}
            self.at = o
            self.pose_off = 1.0 if (f and f["kind"] == "wrong_pose") else 0.0
            self.found = None
            return {"isdone": True, "msg": f"arrived at {o}"}
        if skill == "find":
            m = self._match(o)
            if f and f["kind"] == "false_positive":
                self.found = m or o
                return {"isdone": True, "ins": {o: {"pose_3d": [0.6, 0.0, 0.8]}}}
            if m and self._visible_here(m):
                self.found = m
                return {"isdone": True, "ins": {o: {"pose_3d": [0.6 + self.pose_off, 0.05, 0.8]}}}
            return {"isdone": False, "msg": f"{o} not found"}
        if skill in ("pick", "place", "open_drawer", "close_drawer") and self.pose_off > 0.3:
            return {"isdone": False, "msg": f"{skill}: IK failed, target {self.pose_off + 0.6:.2f} m away (out of reach)"}
        if skill == "pick":
            m = self._match(o)
            if self.holding:
                return {"isdone": False, "msg": "gripper not empty"}
            if not m or not self._visible_here(m):
                return {"isdone": False, "msg": f"grasp failed: no {o} in view"}
            if f and f["kind"] == "slip":
                return {"isdone": True, "msg": "grasped"}          # reported success, empty gripper
            self.holding = m
            self.obj[m] = "robot"
            return {"isdone": True, "msg": "grasped"}
        if skill == "place":
            if not self.holding:
                return {"isdone": False, "msg": "nothing in the gripper"}
            if o != self.at:
                return {"isdone": False, "msg": f"not at {o}"}
            if ("drawer" in o) and (o not in self.opened or o in self.partial):
                return {"isdone": False, "msg": f"{o} is closed"}
            dest = o + "/in" if "drawer" in o else o
            if f and f["kind"] == "place_fall":
                dest = "floor"
            self.obj[self.holding] = dest
            self.holding = None
            return {"isdone": True, "msg": "placed"}
        if skill == "open_drawer":
            if self.at != o:
                return {"isdone": False, "msg": f"not at {o}"}
            if self.holding:
                return {"isdone": False, "msg": "hand is full"}
            self.opened.add(o)
            if f and f["kind"] == "partial_open":
                self.partial.add(o)
            return {"isdone": True, "msg": "opened"}
        if skill == "close_drawer":
            if self.at != o:
                return {"isdone": False, "msg": f"not at {o}"}
            self.opened.discard(o)
            self.partial.discard(o)
            return {"isdone": True, "msg": "closed"}
        return {"isdone": False, "msg": f"unknown skill {skill}"}


class FakeSkillRegistry:
    """``SkillRegistry.execute`` over a :class:`FakeRobot`."""

    def __init__(self, robot):
        self.robot = robot

    def execute(self, skill, params, node=None, log_fn=None):
        return self.robot.execute(skill, params, node, log_fn)


class FakeState:
    """The bits of ``robot_agent.state.AgentState`` the planning loops read."""

    robot_pkg = "fakebot"

    def __init__(self, common_dir):
        self.common_dir = str(common_dir)
        self.world = WorldState()
        self.saved = 0

    def save_world(self):
        self.saved += 1


def run_task(loop, task, lang="en", plan_only=False, max_events=400) -> list[dict]:
    """Drain ``loop.run_blocking`` into a list of events (bulky world / audio dropped)."""
    evs = []
    for ev in loop.run_blocking(task, lang=lang, plan_only=plan_only):
        evs.append({k: v for k, v in ev.items() if k not in ("world", "say_audio")})
        if len(evs) > max_events:
            raise AssertionError("run_blocking did not finish (too many events)")
    return evs


def done(evs) -> dict:
    """The final ``done`` event of a run."""
    d = [e for e in evs if e["event"] in ("done", "error")]
    assert d, f"no done event in {[e['event'] for e in evs]}"
    return d[-1]
