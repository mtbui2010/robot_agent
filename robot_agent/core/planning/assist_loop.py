"""AssistLoop — the assistive layer around the SAGE planner: understanding, clarification, a guard per step.

Planning is SAGE's (paper: "SAGE: Symbolic Action-Gating and Editing for LLM Task Planners"; code identifier
GRACE, ``pyplanner.GRACEPlanner``): every plan and every change of plan comes from it. This loop never adds,
drops or reorders steps itself. Selected with planner ``'sage'`` on ``/ws/agent`` (dashboard: "SAGE + assist")
or ``ROBOT_AGENT_PLANNER=sage``; ``'grace'`` is the plain ``ClosedLoop`` around the same planner.

1. **Understand** (one LLM call, JSON): the task becomes goals ``{type: on|in|open|close, object, from, to}`` or
   a refusal (``unsafe`` / ``infeasible``). Places are grounded on the live ENV without the LLM; a place word
   naming several places, a place the user never said, or "me" without a place is a question for the user
   (the robot's Ask skill, from its manifest); answers are read back ("I will … Is that right?").
2. **Plan**: SAGE ``generate_plan``; the checker (``_check``) simulates the plan as written and reports rule
   violations (wrong place, Pick without Find, Open with a full hand, goals not reached …); critical ones go back
   to SAGE as an edit request (``replan`` from the first bad step), up to ``edit_rounds`` times.
3. **Guard each step**: preconditions before the robot moves (a violation -> SAGE replan); after the skill
   reported success, never the preconditions again: its effects are applied and the skill's VerifySpecs run (the
   manifest's sensors: MoveTo measured against the ENV loc, Pick looked at again; a missing sensor = UNKNOWN), plus
   a Place looked at twice and a Find seen out of reach. Skill-level retries in place stay here (move again, look
   + grasp again), as do requests to the person (clear the way, move the object to the edge, where is it?).
   Anything that needs a different plan — the object is not here, the robot is at the wrong place, the plan ended
   short of the goals — goes to SAGE's suffix repair with an E / R / S reason (``max_replans``). A failure the
   recovery policy does not retry (a skill bug, an e-stop: ``contract.recovery`` by FailureCode), a dropped object
   or the same failure twice stops the run and says why. The robot comes in only through its RobotAdapter
   (``RobotView``): skill names, sensors, geometry, what opens, its phrases.
4. **Done** only when every goal holds in the belief state; the object memory (``object_memory.<host>.json``)
   is updated on every Find / Pick / Place.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Iterator, Optional

from robot_agent.contract import FailureCode, Unbindable
from robot_agent.state import current, host_id
from .loop import _DONE, _NEXT, _STOP, ClosedLoop, _vr_list, reconcile_world
from .records import StepRecord, VerifyResult
from . import action_model
from .robot import run_skill
from .verify import symbolic_check, verdict

_LOC_ACTIONS = {"MoveTo", "Place", "PutIn", "Open", "Close"}
# per-run overrides accepted from /ws/agent `options`
ASSIST_OPTIONS = {"confirm_task", "confirm_answers", "silence_retries", "clarify_rounds", "search_max",
                "pick_retries", "ask_help", "arrive_tol", "find_min_conf", "find_verify", "reach_far", "reach_lateral"}
_USER_WORDS = {"me", "user", "here", "you", "myself", "person"}
# spoken yes / no (ko / vi / en) for read-backs
_YES = re.compile(r"\b(yes|yeah|yep|ok|okay|right|correct|sure|go ahead|네|예|응|맞아|맞아요|좋아|có|đúng|vâng|ừ)\b")
_NO = re.compile(r"\b(no|nope|not|wrong|아니|아니요|아뇨|không|sai)\b")


# ─────────────────────────────────────────────────────────────────────────────
# names
# ─────────────────────────────────────────────────────────────────────────────
def words(name) -> str:
    """'TennisBall' / 'tennis_ball' / ' Tennis  ball ' -> 'tennis ball'."""
    s = str(name or "").replace("_", " ").replace("-", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return " ".join(s.lower().split())


def _strip_article(s: str) -> str:
    return re.sub(r"^(the|a|an|my|your)\s+", "", s or "").strip()


class Places:
    """The live ENV as a closed vocabulary of places. ``openable(key, entry)``: the robot's rule
    (``RobotView.openable``); default, the ENV entry's ``openable`` flag."""

    def __init__(self, env: dict, openable=None):
        self.env = {k: v for k, v in (env or {}).items() if isinstance(v, dict)}
        self.keys = list(self.env)
        self._openable = openable

    @staticmethod
    def head(key: str) -> str:
        return key.split("@")[0].strip()

    @staticmethod
    def room(key: str) -> str:
        return key.split("@")[1].strip() if "@" in key else ""

    def rooms(self) -> set:
        return {self.room(k) for k in self.keys if self.room(k)}

    def openable(self, key: str) -> bool:
        v = self.env.get(key, {})
        if self._openable is not None:
            return bool(self._openable(key, v))
        return bool(v.get("openable"))

    def describe(self) -> str:
        out = []
        for k in self.keys:
            al = self.env[k].get("aliases") or []
            out.append(f"- {k}" + (f" (also: {', '.join(al)})" if al else ""))
        return "\n".join(out)

    def candidates(self, name: str, hint: str = "") -> list:
        """ENV keys *name* may mean. A room name -> ['@room'] marker."""
        w = _strip_article(words(name))
        if not w:
            return []
        try:
            from robot_agent.env_names import resolve_env_name
            k = resolve_env_name(w, self.env)
        except Exception:
            k = None
        if k in self.env:
            from robot_agent.env_names import normalize
            if w == k.lower() or "@" in w or w in {normalize(x) for x in self.env[k].get("aliases") or []}:
                return [k]
        # 'dining table', 'kitchen counter', 'shelf in the restroom'
        room = next((r for r in self.rooms() if r in w), "")
        rest = (w.replace(room, " ") if room else w).replace(" in the ", " ").replace(" in ", " ").replace(" at ", " ")
        rest = " ".join(t for t in rest.split() if t not in ("the", "of", "on", "kitchen's"))
        hits = [k for k in self.keys if rest and (self.head(k) == rest or rest in self.head(k)
                                                     or self.head(k) in rest.split())]
        if not hits and rest:                         # 'table' -> 'dining table', 'test table'
            hits = [k for k in self.keys if any(t == x for t in rest.split() for x in self.head(k).split())]
        if room:
            in_room = [k for k in hits if self.room(k) == room]
            if in_room:
                hits = in_room
            elif not rest or not hits:
                return ["@" + room]
        if not hits and w in self.rooms():
            return ["@" + w]
        if len(hits) > 1 and hint:
            # the user's own words name one of them: an alias (화장실 선반) or the room
            ht = words(hint)
            by_alias = [k for k in hits if any(words(a) in ht for a in self.env[k].get("aliases") or [] if a)]
            if len(by_alias) == 1:
                return by_alias
            hinted = [k for k in hits if self.room(k) and self.room(k) in hint]
            if len(hinted) == 1:
                return hinted
        return hits

    def said(self, key: str, text: str) -> bool:
        """Does *text* name *key* (its name or an alias), not only a word of it?"""
        t = words(text)
        return self.head(key) in t or any(words(a) in t for a in self.env.get(key, {}).get("aliases") or [] if a)

    def ground(self, name: str, hint: str = "") -> Optional[str]:
        c = self.candidates(name, hint)
        return c[0] if len(c) == 1 and not c[0].startswith("@") else None

    def in_room(self, room: str) -> list:
        return [k for k in self.keys if self.room(k) == room]


# ─────────────────────────────────────────────────────────────────────────────
# memory (object -> last place, clarification answers)
# ─────────────────────────────────────────────────────────────────────────────
class Memory:
    _lock = threading.Lock()

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            self.data = {}
        self.data.setdefault("objects", {})
        self.data.setdefault("answers", [])

    def where(self, obj: str) -> Optional[str]:
        e = self.data["objects"].get(words(obj))
        return e.get("place") if e else None

    def see(self, obj: str, place: Optional[str], how: str):
        if not obj:
            return
        self.data["objects"][words(obj)] = {"place": place, "ts": time.time(), "how": how}
        self.save()

    def remember_answer(self, task: str, question: str, answer: str):
        if answer:
            self.data["answers"] = (self.data["answers"] + [{"task": task, "q": question, "a": answer,
                                                              "ts": time.time()}])[-30:]
            self.save()

    def objects_text(self) -> str:
        o = self.data["objects"]
        return "\n".join(f"- {k}: {v.get('place') or 'unknown'}" for k, v in o.items()) or "(nothing yet)"

    def answers_text(self) -> str:
        return "\n".join(f"- task '{a['task']}': asked '{a['q']}' -> '{a['a']}'" for a in self.data["answers"][-8:]) or "(none)"

    def save(self):
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(f".tmp{os.getpid()}")
                tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
                os.replace(tmp, self.path)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# task understanding
# ─────────────────────────────────────────────────────────────────────────────
UNDERSTAND_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["clear", "ambiguous", "unsafe", "infeasible"]},
        "reason": {"type": "string"},
        "question": {"type": "string"},
        "goals": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["on", "in", "open", "close"]},
            "object": {"type": "string"},
            "from": {"type": "string"},
            "to": {"type": "string"}},
            "required": ["type", "object", "from", "to"]}},
    },
    "required": ["status", "reason", "question", "goals"],
}

UNDERSTAND_PROMPT = """You turn a household robot task into goals. {abilities}
It cannot: throw, pour, cook, wash, climb, open windows or doors, use tools, handle knives near people, or leave the house.

Places (use these names):
{places}

Earlier answers from this user:
{answers}

Robot now: {robot}

Return JSON:
- status: "unsafe" if it could hurt someone or damage things (throwing, out of a window, near fire...);
  "infeasible" only if it needs an action the robot does not have (any object may be moved; unknown objects are
  fine); "ambiguous" only if WHAT to do is unclear (e.g. "clean the table" without saying what to move where) — not
  for a missing or unclear place or an object whose location is unknown, those are handled separately; else "clear".
- reason: one short sentence.
- question: when ambiguous, one short question to the user; else "".
- goals: one per object to move / container to open, in order:
  {{"type": "on", "object": <object>, "from": <place it is on now, or "">, "to": <place to put it, or "me" when the user
  wants it given to them>}}; "in" = put into a drawer (to = the drawer); "open" / "close": object = the drawer, from/to "".
  Object names: lower case, as the user said them ("tennis ball", "red cup").

Task: {task}{original}
{dialogue}"""


# ─────────────────────────────────────────────────────────────────────────────
# the loop
# ─────────────────────────────────────────────────────────────────────────────
class AssistLoop(ClosedLoop):
    """See the module docstring."""

    def __init__(self, agent, emit=None, **overrides) -> None:
        overrides = dict(overrides)
        self.original = overrides.pop("original", None)
        overrides["planner"] = overrides.get("generator") or os.environ.get("ROBOT_AGENT_SAGE_GENERATOR", "grace")
        super().__init__(agent, emit=emit, **overrides)
        env = os.environ.get
        common = Path(getattr(current(), "common_dir", "."))
        geo = self.robot.geometry
        find = self.robot.spec("Find")
        find_props = (find.params.get("properties") or {}) if find is not None else {}
        self._find_params = set(find_props)
        min_conf = (find_props.get("min_conf") or {}).get("default", 0)
        self.cfg.update({
            "clarify_rounds": int(overrides.get("clarify_rounds", env("ROBOT_AGENT_CLARIFY_ROUNDS", "3"))),
            # Find failures before asking the person where the object is (each one goes to SAGE first)
            "search_max": int(overrides.get("search_max", env("ROBOT_AGENT_SEARCH_MAX", "3"))),
            # searches now go through SAGE's replan, so the budget is larger than the plain loop's 3
            "max_replans": int(overrides.get("max_replans", env("ROBOT_AGENT_MAX_REPLANS", "6"))),
            "pick_retries": int(overrides.get("pick_retries", 1)),
            # SAGE edits of the first plan driven by the checker's findings
            "edit_rounds": int(overrides.get("edit_rounds", env("ROBOT_AGENT_EDIT_ROUNDS", "3"))),
            "silence_retries": int(overrides.get("silence_retries", env("ROBOT_AGENT_SILENCE_RETRIES", "1"))),
            "confirm_answers": overrides.get("confirm_answers", env("ROBOT_AGENT_CONFIRM_ANSWERS", "1") == "1"),
            "confirm_task": overrides.get("confirm_task", env("ROBOT_AGENT_CONFIRM_TASK", "0") == "1"),
            # MoveTo: farther than this from the ENV loc counts as not arrived (nav "succeeded" off the spot);
            # default: the robot's geometry (manifest arrive_tol_m)
            "arrive_tol": float(overrides.get("arrive_tol", env("ROBOT_AGENT_ARRIVE_TOL", str(geo.arrive_tol_m)))),
            # Find: detections below this score are treated as not seen; sent only when the robot's Find takes a
            # `min_conf` param, default its manifest default (the robot's measurements live there)
            "find_min_conf": float(overrides.get("find_min_conf", env("ROBOT_AGENT_FIND_MIN_CONF", str(min_conf)))),
            # Find: a VLM checks the crop of each low-scoring detection (the Find skill's `verify` param, if any)
            "find_verify": overrides.get("find_verify", env("ROBOT_AGENT_FIND_VERIFY", "0") == "1"),
            # base-frame distances (manifest reach_far_m / reach_m): beyond reach_far a detection is "over there"
            # (look-and-go); a pick whose target is more than reach_lateral to the side is not retried
            "reach_far": float(overrides.get("reach_far", env("ROBOT_AGENT_REACH_FAR", str(geo.reach_far_m)))),
            "reach_lateral": float(overrides.get("reach_lateral", env("ROBOT_AGENT_REACH_LATERAL", str(geo.reach_m)))),
            "ask_help": overrides.get("ask_help", env("ROBOT_AGENT_ASK_HELP", "1") == "1"),
            "memory_path": Path(overrides.get("memory_path") or (common / f"object_memory.{host_id()}.json")),
            "generator": self.cfg["planner"],
            "planner": "sage",
        })

    def _make_planner(self):
        name, self.cfg["planner"] = self.cfg["planner"], self.cfg["generator"]
        try:
            return super()._make_planner()
        finally:
            self.cfg["planner"] = name

    # ── LLM ───────────────────────────────────────────────────────────────
    def _llm(self):
        if getattr(self, "_llm_client", None) is None:
            from ...connect.llm import init_llm_client
            self._llm_client = init_llm_client(cfg=self._llm_cfg)
        return self._llm_client

    def _understand(self, task, places, memory, world, dialogue) -> dict:
        prompt = UNDERSTAND_PROMPT.format(
            abilities=self.robot.robot_description(),
            # no object memory here: a 7B model refused / asked about every object it did not list
            # (the memory grounds `from` after this call instead)
            places=places.describe(), answers=memory.answers_text(),
            robot=world.as_text() if hasattr(world, "as_text") else "",
            dialogue=("You already asked the user, and the answers below complete the task (a later answer corrects "
                      "an earlier one, and a \"no, ...\" corrects what the robot read back): take the object, source "
                      "and destination from them, and say \"clear\" unless something is still missing.\n"
                      + "\n".join(f"{'Robot' if who == 'robot' else 'User'}: {t}" for who, t in dialogue))
            if dialogue else "", task=task,
            # the user's own words: a translation loses names (식탁 'dining table' came out as 'table')
            original=(f"\n(the user's own words: {self.original})" if self.original and self.original != task else ""))
        raw = self._llm().chat(prompt=prompt, format=UNDERSTAND_SCHEMA)
        try:
            d = json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", str(raw), re.S)
            d = json.loads(m.group(0)) if m else {}
        d.setdefault("status", "clear"); d.setdefault("goals", []); d.setdefault("question", "")
        for g in d["goals"]:
            g["object"] = _strip_article(words(g.get("object")))
            for k in ("from", "to"):
                g[k] = (g.get(k) or "").strip()
        return d

    def _ground_goals(self, goals, places, memory, task, latest=""):
        """Ground every goal's places on ENV. Returns (goals, question|None). `latest`: the user's last answer,
        which wins over earlier words ("the kitchen one" ... "no, the restroom")."""
        out, questions = [], []
        for g in goals:
            g = dict(g)
            t = g.get("type")
            if t in ("open", "close"):
                k = places.ground(g["object"], task) or places.ground(g.get("to") or "", task)
                if not k:
                    drawers = [p for p in places.keys if places.openable(p)]
                    k = drawers[0] if len(drawers) == 1 else None
                if not k:
                    questions.append(f"Which drawer should I {t}?")
                g["object"] = k or g["object"]; g["to"] = k or ""
                out.append(g); continue
            for slot in ("to", "from"):
                v = g.get(slot) or ""
                if not v:
                    if slot == "to":
                        questions.append(f"Where should I put the {g['object']}?")
                    continue
                if words(v) in _USER_WORDS:
                    # "to me ... at the sofa": the task names the user's place
                    m = re.search(r"\b(?:at|on|by|near) (?:the )?([a-z ]+?)(?:[.,!?]|$)", task.lower())
                    k = places.ground(m.group(1), task) if m else None
                    if k:
                        g[slot] = k
                    else:
                        questions.append("Where are you? Which place should I bring it to?")
                    continue
                c = places.candidates(v, task)
                if len(c) > 1 and latest:
                    c2 = places.candidates(v, latest)
                    if len(c2) == 1:
                        c = c2
                if len(c) == 1 and c[0] in places.env:
                    # the LLM may pick a key ('shelf@kitchen') the user never said: is its name unique in the words?
                    alt = places.candidates(places.head(c[0]), task)
                    if len(alt) > 1 and latest and places.candidates(places.head(c[0]), latest) == [c[0]]:
                        alt = [c[0]]                     # the last answer settles it
                    if len(alt) > 1:
                        c, v = alt, places.head(c[0])
                    elif not places.said(c[0], task):
                        # 'test table' for 식탁 translated to 'table': the user's words (an alias) may name another
                        # place of that kind -> take it; several -> ask instead of guessing
                        alt = places.candidates(places.head(c[0]).split()[-1], task)
                        if len(alt) == 1 and alt[0] != c[0]:
                            c = alt
                        elif len(alt) > 1:
                            c, v = alt, places.head(c[0]).split()[-1]
                if (slot == "from" and len(c) == 1 and c[0] in places.env and not places.said(c[0], task)
                        and memory.where(g["object"]) != c[0]):
                    c = []                                # the model made up where it is: search instead
                if len(c) == 1 and not c[0].startswith("@"):
                    g[slot] = c[0]
                elif slot == "from" and not c:
                    g[slot] = ""                      # unknown source: search for it
                else:
                    opts = places.in_room(c[0][1:]) if len(c) == 1 else c
                    if slot == "from" and opts:
                        g["from_any"] = opts; g[slot] = ""   # 'from the kitchen' / 'the shelf': search them
                    else:
                        nice = [k.replace("@", " in the ") for k in (opts or places.keys[:4])]
                        questions.append(f"Which {places.head(v) if '@' in v else words(v)} do you mean: {' or '.join(nice)}?")
            if g.get("from") and g.get("from") == g.get("to"):
                g["from"] = ""                       # 'from the desk to the desk': the model's slip, search instead
            if t == "in" and g.get("to") and not places.openable(g["to"]):
                drawers = [p for p in places.keys if places.openable(p)]
                if len(drawers) == 1:
                    g["to"] = drawers[0]
            if not g.get("from") and not g.get("from_any"):
                g["from"] = memory.where(g["object"]) or ""
            if g.get("from") and g.get("from") == g.get("to"):
                g["from"] = ""                       # already where it should go? search rather than trust it
            out.append(g)
        return out, (" ".join(dict.fromkeys(questions)) if questions else None)

    _LANGS = {"ko": "Korean", "vi": "Vietnamese", "ja": "Japanese", "zh": "Chinese"}

    def _in_lang(self, text, lang) -> str:
        """`text` (English) in the user's language: the questions and read-backs are written in English."""
        name = self._LANGS.get((lang or "en")[:2])
        if not name or not text:
            return text
        cache = self.__dict__.setdefault("_tr_cache", {})
        if (text, name) not in cache:
            try:
                out = str(self._llm().chat(prompt=f"Translate into natural spoken {name}. Keep it short and "
                                                  f"polite. Output only the translation:\n{text}")).strip().strip('"')
            except Exception:
                out = ""
            cache[(text, name)] = out or text
        return cache[(text, name)]

    def _ask(self, question, node, log_fn, lang) -> str:
        """Ask the user through the robot's Ask skill (manifest). '' when there is no answer / no channel."""
        spec = self.robot.ask_spec()
        if spec is None:
            return ""
        params = {(spec.required_params[:1] or ["inputs"])[0]: self._in_lang(question, lang)}
        for k, v in (("retries", 0), ("lang", lang)):            # optional params the skill may take
            if k in spec.param_names:
                params[k] = v
        try:
            r = self.sr.execute(spec.name, params, node, log_fn=log_fn)
            return str((r or {}).get("text") or (r or {}).get("answer") or "").strip() if (r or {}).get("isdone") else ""
        except Exception:
            return ""

    def _ask_heard(self, question, node, log_fn, lang):
        """Ask; on silence ask again (`silence_retries`). Returns (answer, n_asked)."""
        n = 0
        for k in range(1 + self.cfg["silence_retries"]):
            n += 1
            ans = self._ask(question if k == 0 else f"Sorry, I didn't hear you. {question}", node, log_fn, lang)
            if ans:
                return ans, n
        return "", n

    @staticmethod
    def yes_no(text):
        """(True | False | None, correction text) from a spoken yes / no answer."""
        t = (text or "").strip()
        if not t:
            return None, ""
        low = t.lower()
        if _NO.search(low):
            rest = _NO.sub(" ", low, count=1).strip(" ,.!?")
            return False, rest
        if _YES.search(low):
            return True, ""
        return False, t                              # anything else: a correction ("the restroom one")

    @staticmethod
    def summary(goals):
        nice = lambda k: (k or "").replace("@", " in the ")
        out = []
        for g in goals:
            t = g["type"]
            if t == "on":
                out.append(f"put the {g['object']}" + (f" from the {nice(g['from'])}" if g.get("from") else "")
                           + f" on the {nice(g['to'])}")
            elif t == "in":
                out.append(f"put the {g['object']} in the {nice(g['to'])}")
            else:
                out.append(f"{t} the {nice(g['object'])}")
        return "I will " + ", then ".join(out) + "."

    # ── plan grounding + gate ──────────────────────────────────────────────
    def _ground_step(self, st, places, goals, task):
        st = dict(st)
        a = st.get("action", "")
        o = st.get("object") or st.get("target") or ""
        if a in _LOC_ACTIONS:
            hint = " ".join([task] + [g.get("to", "") + " " + g.get("from", "") for g in goals])
            c = places.candidates(o, hint)
            goal_places = [g.get(s) for g in goals for s in ("to", "from") if g.get(s)]
            if len(c) > 1:
                c = [k for k in c if k in goal_places] or c[:1]
            st["object"] = c[0] if c else words(o)
            st["grounded"] = bool(c)
        else:
            st["object"] = _strip_article(words(o))
            # the plan's 'cup' for the goal's 'red cup'
            for g in goals:
                if st["object"] and st["object"] != g["object"] and st["object"] in g["object"].split():
                    st["object"] = g["object"]
        return st

    def _check(self, steps, world, places, goals):
        """The SAGE plan, simulated as written (nothing is changed): [(step index, problem, critical)].
        Critical problems go back to SAGE for an edit; the others are only mentioned alongside them.
        The action rules are the shared action model's; this adds the goal / ENV findings and the wording."""
        probs = []
        P = lambda i, txt, crit=True: probs.append((i, txt, crit))
        src_of = {g["object"]: g.get("from") for g in goals}
        c = {"need_open": {g["to"] for g in goals if g["type"] == "in"} | {g["object"] for g in goals if g["type"] == "open"},
             "need_close": {g["object"] for g in goals if g["type"] == "close"}, "src_of": src_of,
             "ctx": dict(capabilities=self.robot.model_capabilities(), actions=self.robot.plan_actions,
                         openable=places.openable, containers=places.openable,
                         object_places={o: f for o, f in src_of.items() if f})}
        w, placed_ok = world.copy(), {}
        for i, st in enumerate(steps):
            if self._check_step(i, st, w, goals, c, placed_ok, P):
                action_model.apply(st, w, inplace=True)
        missing = self._missing(steps, world, goals)
        if missing:
            P(len(steps), "the plan does not achieve: " + "; ".join(missing))
        else:
            for k in range(len(steps)):
                if self._reaches(steps[:k], world, goals):
                    P(k, f"the goals are done after step {k}; remove the steps after it (do not repeat the task)"); break
        return probs

    def _check_step(self, i, st, w, goals, c, placed_ok, P) -> bool:
        """The findings of step i in the simulated world `w`, worded for SAGE. False: skip its effect."""
        a, o = st.get("action"), st.get("object") or ""
        vs, said = {v.code: v for v in action_model.check(st, w, **c["ctx"])}, set()

        def has(*codes):                             # did the model find one of these? (marks them worded)
            hit = [c_ for c_ in codes if c_ in vs]
            said.update(hit)
            return bool(hit)
        if not self._word_step(i, a, o, st, w.holding, has, goals, c, placed_ok, P):
            return False
        for code, v in vs.items():                   # any other rule of the model, in its own words
            if code not in said:
                P(i, f"step {i + 1} '{a} {o}': {v.reason}", v.is_error)
        return True

    def _word_step(self, i, a, o, st, holding, has, goals, c, placed_ok, P) -> bool:
        """The checker's own wording of the model's findings for the step's action. False: skip the step."""
        bad_place = not st.get("grounded", True) or o.startswith("@")
        nice = lambda k: (k or "").replace("@", " in the ")
        src_of = c["src_of"]
        if has("unknown_action"):
            P(i, f"step {i + 1} '{a} {o}': the robot has no action {a} (use MoveTo, Find, Pick, Place, PutIn, Open, Close)")
            return False
        if a == "MoveTo":
            if bad_place:
                P(i, f"step {i + 1} 'MoveTo {o}': {o.lstrip('@')} is not a place the robot can go to; use one of the listed places")
                return False
            if has("already_there"):
                P(i, f"step {i + 1} 'MoveTo {o}': the robot is already there", False)
        elif a in ("Open", "Close"):
            if has("not_openable"):
                P(i, f"step {i + 1} '{a} {o}': {nice(o)} cannot be opened or closed")
                return False
            if o not in (c["need_open"] if a == "Open" else c["need_close"]) and o not in src_of.values():
                P(i, f"step {i + 1} '{a} {o}': no goal needs it; remove it")
            if has("hand_full"):
                P(i, f"step {i + 1} '{a} {o}': the robot cannot {a.lower()} it while holding the {holding}; do it before the Pick")
            if has("interact_without_find"):
                P(i, f"step {i + 1} '{a} {o}': the robot must MoveTo {o} first")
            if has("already_open"):
                P(i, f"step {i + 1} 'Open {o}': {nice(o)} is already open; remove this step")
            if has("close_not_opened"):
                P(i, f"step {i + 1} 'Close {o}': {nice(o)} is not open; remove this step")
        elif a == "Find":
            if has("find_held"):
                P(i, f"step {i + 1} 'Find {o}': already done (the {o} is in the hand); delete this step")
            if has("find_wrong_place"):
                P(i, f"step {i + 1} 'Find {o}': the user said it is on {nice(src_of[o])}; MoveTo {src_of[o]} first")
        elif a == "Pick":
            self._word_pick(i, o, holding, has, goals, placed_ok, P)
        elif a in ("Place", "PutIn"):
            self._word_place(i, a, o, holding, bad_place, has, goals, placed_ok, P)
        return True

    @staticmethod
    def _word_pick(i, o, holding, has, goals, placed_ok, P) -> None:
        g_ = next((g for g in goals if g["object"] == o), None)
        if g_ and placed_ok.get(o) == g_.get("to"):
            P(i, f"step {i + 1} 'Pick {o}': the {o} was already placed on {g_['to']} by an earlier step; delete the steps that move it again")
        if has("already_holding", "no_find_before_pick", "pick_not_found"):
            if holding and holding == o:
                P(i, f"step {i + 1} 'Pick {o}': already done by an earlier step (the {o} is in the hand); delete this step")
            elif holding:
                P(i, f"step {i + 1} 'Pick {o}': the hand already holds the {holding}; Place it first")
            else:
                P(i, f"step {i + 1} 'Pick {o}': needs 'Find {o}' just before it, at the same place")

    @staticmethod
    def _word_place(i, a, o, holding, bad_place, has, goals, placed_ok, P) -> None:
        nice = lambda k: (k or "").replace("@", " in the ")
        g = next((g for g in goals if g["object"] == holding), None)
        if has("place_without_holding"):
            P(i, f"step {i + 1} '{a} {o}': nothing is in the hand")
        if bad_place:
            has("empty_object", "place_wrong_location")
            P(i, f"step {i + 1} '{a} {o}': not a place" + (f"; the destination is {g['to']}" if g else ""))
        elif g and g.get("to") and o != g["to"]:
            has("place_wrong_location")
            P(i, f"step {i + 1} '{a} {o}': the {holding} must go to {g['to']}, not {o}")
        elif has("place_wrong_location"):
            P(i, f"step {i + 1} '{a} {o}': the robot must MoveTo {o} first")
        if has("putin_closed_container"):
            P(i, f"step {i + 1} 'PutIn {o}': {nice(o)} is closed; Open it before the Pick")
        if holding:
            placed_ok[holding] = o

    def _sage_edit(self, planner, task, obs, ground, world, places, goals, goal_txt):
        """SAGE's own Editing, driven by the checker: every sub-goal block with a critical finding is re-done by
        SAGEPlanner.edit_block (sub-goal-local repair) with that finding as feedback; a block whose sub-goal the
        earlier blocks already achieved is asked for no steps. Returns (plan, [edit notes]); the plan is
        SAGE's ``flatten`` of its blocks, as generate_plan returns it. Uses embodiplan's public API only."""
        inner = getattr(planner, "_p", planner)
        blocks = getattr(inner, "last_blocks", None)
        if not blocks or not hasattr(inner, "edit_block"):
            return None, []
        from embodiplan.sage import flatten
        ex_block = inner.retrieve_examples(task)[1]
        notes = []
        for _round in range(self.cfg["edit_rounds"]):
            per_block, n_before, done_at = {}, 0, None
            for k in range(len(blocks)):
                flat_k = ground(flatten(blocks[:k + 1]))
                if done_at is None and blocks[k].steps and k > 0 and self._reaches(ground(flatten(blocks[:k])), world, goals):
                    done_at = k
                rng = range(n_before, len(flat_k))
                per_block[k] = rng
                n_before = len(flat_k)
            flat = ground(flatten(blocks))
            probs = [p_ for p_ in self._check(flat, world, places, goals) if p_[2]]
            todo = {}
            for k in range(len(blocks)):
                if done_at is not None and k >= done_at and blocks[k].steps:
                    todo[k] = [f"the goals ({goal_txt}) are already achieved by the earlier sub-goals, so this "
                               f'sub-goal needs no steps: return {{"steps": []}}']
            for i_, txt, _c in probs:
                if txt.startswith("the goals are done after step"):
                    continue                             # handled per block above
                k = next((k for k, r in per_block.items() if i_ in r), len(blocks) - 1)
                if txt.startswith("the plan does not achieve"):
                    k = max((k for k in range(len(blocks)) if blocks[k].steps), default=len(blocks) - 1)
                    txt = txt.replace("the plan does not achieve", "this sub-goal must also achieve")
                todo.setdefault(k, []).append(txt)
            if not todo:
                break
            for k, items in sorted(todo.items()):
                prev = [s_ for b in blocks[:k] for s_ in b.steps]
                state = action_model.simulate(prev, stop_on_error=False,
                                              capabilities=self.robot.model_capabilities()).final_state
                fb = ("Checker findings (fix them; places must be exactly the listed ones):\n"
                      + "\n".join(f"- {t}" for t in items) + f"\nGoals of the whole task: {goal_txt}")
                try:
                    new_steps = inner.edit_block(blocks[k], fb, task=task, state=state, obs=obs, examples_block=ex_block)
                except Exception as e:
                    notes.append(f"sub-goal {k + 1} edit failed: {e}"); continue
                empty_ok = any('"steps": []' in t for t in items)
                if new_steps or empty_ok:
                    blocks[k].steps = new_steps
                notes.append(f"sub-goal {k + 1} '{blocks[k].name}': " + "; ".join(items))
        return ground(flatten(blocks)), notes

    def _missing(self, steps, world, goals):
        holding, opened, on = world.holding, set(world.opened), {}
        for st in steps:
            a, o = st.get("action"), st.get("object")
            if a == "Pick":
                holding = o
            elif a in ("Place", "PutIn") and holding:
                on[holding] = (a, o); holding = None
            elif a == "Open":
                opened.add(o)
            elif a == "Close":
                opened.discard(o)
        out = []
        for g in goals:
            t = g["type"]
            if t == "on" and on.get(g["object"]) != ("Place", g["to"]):
                out.append(f"Place the {g['object']} on {g['to']}")
            elif t == "in" and on.get(g["object"]) != ("PutIn", g["to"]):
                out.append(f"PutIn the {g['object']} into {g['to']}")
            elif t == "open" and g["object"] not in opened:
                out.append(f"Open {g['object']}")
            elif t == "close" and g["object"] in opened:
                out.append(f"Close {g['object']}")
        return out

    @staticmethod
    def _reaches(steps, world, goals) -> bool:
        holding, opened, on = world.holding, set(world.opened), {}
        for st in steps:
            a, o = st.get("action"), st.get("object")
            if a == "Pick":
                holding = o
            elif a in ("Place", "PutIn") and holding:
                on[holding] = (a, o); holding = None
            elif a == "Open":
                opened.add(o)
            elif a == "Close":
                opened.discard(o)
        for g in goals:
            t = g["type"]
            if t == "on" and on.get(g["object"]) != ("Place", g["to"]):
                return False
            if t == "in" and on.get(g["object"]) != ("PutIn", g["to"]):
                return False
            if t == "open" and g["object"] not in opened:
                return False
            if t == "close" and g["object"] in opened:
                return False
        return bool(goals) or bool(steps)

    def _goals_hold(self, goals, world, placed) -> bool:
        for g in goals:
            t = g["type"]
            if t in ("on", "in") and (placed.get(g["object"]) != g["to"] or world.holding == g["object"]):
                return False
            if t == "open" and g["object"] not in world.opened:
                return False
            if t == "close" and g["object"] in world.opened:
                return False
        return True

    # ── execution helpers ─────────────────────────────────────────────────
    def _bind(self, st, world):
        """``(skill, params)`` of a grounded step (None: a no-op); raises Unbindable. The step's object is
        already the robot's name (an ENV key / the object), so it goes in as is."""
        mapped = self.robot.bind(dict(st), world)
        if mapped is None:
            return None
        skill, p = mapped[0], dict(mapped[1] or {})
        a, o = st["action"], st["object"]
        if a in _LOC_ACTIONS or a in ("Find", "Pick"):
            p["inputs"] = o
        if a == "Find" and self.cfg["find_min_conf"] > 0 and "min_conf" in self._find_params:
            p["min_conf"] = self.cfg["find_min_conf"]
        if a == "Find" and self.cfg["find_verify"] and "verify" in self._find_params:
            p["verify"] = True
        return skill, p

    def _params(self, st, world):
        """The params of a grounded step (legacy entry point; see ``_bind``)."""
        mapped = self._bind(st, world)
        return dict(mapped[1]) if mapped else {}

    def _holding(self, node, default: bool) -> bool:
        """Is something in the hand? The robot's holding sensor (the one that verifies its Pick);
        ``default`` when it has none or it cannot be read."""
        skill = self.robot.holding_sensor()
        if skill is None:
            return default
        try:
            return bool(self.sr.execute(skill, {}, node).get("isdone"))
        except Exception:
            return default

    def _base_pose(self, node):
        """[x, y, yaw_deg] from the robot's pose sensor, or None."""
        skill = self.robot.pose_sensor()
        if skill is None:
            return None
        try:
            return list(self.sr.execute(skill, {}, node)["pose"])[:3]
        except Exception:
            return None

    def _dist_to_place(self, key, node, places):
        """Base distance (m) from the ENV loc of `key`, or None when unknown."""
        l = (places.env.get(key) or {}).get("loc") or {}
        if "x" not in l or "y" not in l:
            return None
        pose = self._base_pose(node)
        if pose is None:
            return None
        return math.hypot(pose[0] - l["x"], pose[1] - l["y"])

    def _effect(self, st, world, memory, placed):
        a, o, held = st["action"], st["object"], world.holding
        action_model.apply(st, world, inplace=True)     # the symbolic effect is the shared action model's
        if a == "Find":
            memory.see(o, world.arrived, "found")
        elif a == "Pick":
            world.holding_since = time.time()
            memory.see(world.holding, "robot", "picked")
        elif a in ("Place", "PutIn"):
            if held:
                placed[held] = o; memory.see(held, o, "placed")
            world.holding_since = None; world.holding_pose = None
        try:
            current().save_world()
        except Exception:
            pass

    @staticmethod
    def _found_point(result, obj):
        """Base-frame [x, y, z] of what Find detected (``ins[obj].pose_3d``), or None."""
        ins = (result or {}).get("ins") if isinstance(result, dict) else None
        if not isinstance(ins, dict) or not ins:
            return None
        e = ins.get(obj) or next(iter(ins.values()))
        p = e.get("pose_3d") or e.get("loc_3d") if isinstance(e, dict) else None
        try:
            return [float(v) for v in p[:3]]
        except Exception:
            return None

    def _place_of_far_point(self, p3, node, places, world):
        """The ENV place to stand at for a point seen beyond reach (None when it is within reach / unknown)."""
        if math.hypot(p3[0], p3[1]) <= self.cfg["reach_far"]:
            return None
        pose = self._base_pose(node)
        if pose is None or len(pose) < 3:
            return None
        x, y, rz = pose
        c, s_ = math.cos(math.radians(rz)), math.sin(math.radians(rz))
        wx, wy = x + c * p3[0] - s_ * p3[1], y + s_ * p3[0] + c * p3[1]
        best = None
        for k in places.keys:
            l = places.env[k].get("loc") or {}
            if "x" in l and "y" in l and not places.openable(k):
                d = math.hypot(l["x"] - wx, l["y"] - wy)
                if best is None or d < best[0]:
                    best = (d, k)
        if best and best[0] < self.cfg["reach_far"] + 0.5 and best[1] != world.arrived:
            return best[1]
        return None

    # ── main: the phases of one run ───────────────────────────────────────
    def run_blocking(self, task: str, lang: str = "en", plan_only: bool = False) -> Iterator[dict]:
        r = self._open_run(task, lang, plan_only, "sage+assist")
        reconcile_world(r.node, r.world)
        from robot_agent.skill_configs import ENV
        r.places = Places(dict(ENV), openable=r.robot.openable)
        r.verifier = self._verifier(r.node, r.places.env)
        r.memory = Memory(self.cfg["memory_path"])
        self._mute(r)
        try:
            yield r.ev("task_start", say=r.announcer.announce("task_start"), task=task, run_id=r.run_id,
                       world=r.world.to_dict())
            if (yield from self._understand_phase(r)):            # 1. understand + clarify
                return
            if (yield from self._plan_phase(r)):                  # 2. SAGE plans, the checker certifies
                return
            if (yield from self._execute_phase(r)):               # 3. guard each step, recover, replan
                return
            yield self._final_check(r)                            # 4. done only when the goals hold
        except Exception as e:
            yield self._crashed(r, e)
        finally:
            self._close_run(r)

    def _finish(self, r, status, msg="", **kw) -> dict:
        """The run's `done` event; a failure / refusal tells the user why (in their language)."""
        r.run.status = status; r.run.ended_at = time.time()
        kind = "done_success" if status == "success" else "done_fail"
        say = kw.pop("say", None)
        if say is None and status in ("failed", "refused") and msg and not msg.startswith(("internal error", "plan finished")):
            # tell the user why (dropped it, can't find it, ...), without the skill's error text
            say = re.split(r": [\'\"{]|: Exception| \(replan| \(searched", msg)[0].strip()
        return r.ev("done", status=status, run_id=r.run_id, msg=msg,
                    say=self._in_lang(say, r.lang) if say else r.announcer.announce(kind), world=r.world.to_dict(), **kw)

    def _cancelled(self, r) -> Optional[dict]:
        from ..run_control import cancel_requested
        return self._finish(r, "aborted", msg="Cancelled by user") if cancel_requested() else None

    # ── 1. understand + clarify ───────────────────────────────────────────
    def _understand_phase(self, r):
        """Goals from the task and the user's answers. True when the run ended."""
        r.dialogue, r.asked, r.confirms, r.pin_to, r.goals = [], 0, 0, None, []
        while True:
            out = yield from self._understand_round(r)
            if out is _STOP:
                return True
            if out is _DONE:
                break
        r.run.obs = "goals: " + json.dumps(r.goals, ensure_ascii=False)
        return False

    def _understand_round(self, r):
        """One understanding of the task: _DONE (goals settled), _NEXT (asked something) or _STOP."""
        yield r.ev("status", msg="Understanding the task...")
        u = self._understand(r.task, r.places, r.memory, r.world, r.dialogue)
        if r.pin_to:
            # "no, the shelf in the restroom" after a read-back names the destination (a 7B model put
            # it in `from`): pin it instead of trusting the model's slots
            for g in u["goals"]:
                if g.get("type") in ("on", "in"):
                    if r.places.ground(g.get("from") or "", g.get("from") or "") == r.pin_to:
                        g["from"] = ""
                    g["to"] = r.pin_to
        answers = [t for w_, t in r.dialogue if w_ == "user"]
        r.goals, q = self._ground_goals(u["goals"], r.places, r.memory,
                                        " ".join([r.task, self.original or ""] + answers),
                                        latest=answers[-1] if answers else "")
        yield r.ev("understand", status=u["status"], reason=u.get("reason"), goals=r.goals,
                   question=u.get("question") or q)
        if u["status"] == "infeasible" and r.dialogue and r.goals:
            u["status"] = "clear"                # the user already answered: a 7B model called a plain
                                                 # move "infeasible" after "no, the shelf in the restroom"
        if u["status"] in ("unsafe", "infeasible"):
            msg = u.get("reason") or "I can't do that."
            yield self._finish(r, "refused", msg=msg, say=f"I can't do that: {msg}")
            return _STOP
        # the grounding decides about places; the model's "ambiguous" counts only when it found nothing
        # to do (after "no, the shelf in the restroom" a 7B model kept asking which shelf)
        if u["status"] == "ambiguous" and u.get("question") and not r.goals:
            q = u["question"] if not q else f"{u['question']} {q}"
        if not r.goals and not q:
            q = u.get("question") or "What should I do exactly?"
        if not q:
            out, q = yield from self._read_back(r)
            if out is not None:
                return out
        return (yield from self._ask_clarify(r, q))

    def _read_back(self, r):
        """Read the goals back when the user answered something (a misheard answer is only caught
        here) or always (`confirm_task`, a voice user whose command itself may be misheard).
        Returns (outcome or None, the question to ask next)."""
        if not ((r.dialogue and self.cfg["confirm_answers"]) or self.cfg["confirm_task"]):
            return _DONE, None
        if r.confirms >= 2:
            yield self._finish(r, "failed", msg="the user did not confirm the task"); return _STOP, None
        r.confirms += 1
        cq = f"{self.summary(r.goals)} Is that right?"
        yield r.ev("clarify", question=cq, round=r.confirms, kind="confirm")
        ans, _n = self._ask_heard(cq, r.node, self._log_fn(-1), r.lang)
        stop = self._cancelled(r)
        if stop:
            yield stop; return _STOP, None
        ok_, rest = self.yes_no(ans)
        if ok_ is True:
            return _DONE, None
        if ok_ is None:
            yield self._finish(r, "failed", msg=f"no answer to: {cq}"); return _STOP, None
        r.dialogue += [("robot", cq), ("user", ans)]
        if not rest:                     # a bare "no": ask what instead
            return None, "Sorry. What should I do?"
        r.memory.remember_answer(r.task, cq, ans)
        k = r.places.ground(rest, rest)
        if k and not re.search(r"\b(from|take|pick)\b", rest):
            r.pin_to = k
        return _NEXT, None

    def _ask_clarify(self, r, q):
        """Ask the user `q` (bounded by clarify_rounds): _NEXT with the answer in the dialogue, or _STOP."""
        if r.asked >= self.cfg["clarify_rounds"]:
            yield self._finish(r, "failed", msg=f"still unclear after {r.asked} questions: {q}")
            return _STOP
        r.asked += 1
        yield r.ev("clarify", question=q, round=r.asked)
        ans, _n = self._ask_heard(q, r.node, self._log_fn(-1), r.lang)
        stop = self._cancelled(r)
        if stop:
            yield stop; return _STOP
        if not ans:
            yield self._finish(r, "failed", msg=f"no answer to: {q}")
            return _STOP
        r.memory.remember_answer(r.task, q, ans)
        r.dialogue += [("robot", q), ("user", ans)]
        return _NEXT

    # ── 2. plan: SAGE generates, the checker reports, SAGE edits ──────────
    def _obs(self, r) -> str:
        txt = ("Places the robot can go to (use exactly these):\n" + "\n".join(f"- {k}" for k in r.places.keys)
               + "\nGoals: " + r.goal_txt + "\nRobot state: " + r.world.as_text())
        if r.searched:
            txt += "\nAlready looked (object not there): " + ", ".join(r.searched)
        return txt

    def _ground_all(self, r, seq) -> list:
        return [self._ground_step(x, r.places, r.goals, r.task) for x in seq]

    def _plan_phase(self, r):
        """The certified plan in r.steps. True when the run ended (plan only / no plan)."""
        yield r.ev("status", msg="Generating task plan...")
        r.restated = r.task + ("" if not r.dialogue else " (" + "; ".join(t for w_, t in r.dialogue if w_ == "user") + ")")
        r.goal_txt = "; ".join(f"{g['type']} {g['object']}" + (f" from {g['from']}" if g.get('from') else "")
                               + (f" to {g['to']}" if g.get('to') else "") for g in r.goals)
        r.searched, r.seen_far = [], []
        r.planner = self._make_planner()
        self._hook_progress(r.planner)
        yield from self._generate_and_certify(r)
        skill_map = r.robot.skill_map
        plan_text = "\n".join(f"{skill_map.get(x['action'], x['action'])}::{x['object']}" for x in r.steps)
        yield r.ev("plan", steps=r.steps, plan_meta=r.run.plan_meta, warnings=[], plan_text=plan_text,
                   world=r.world.to_dict())
        if r.plan_only:
            yield self._finish(r, "planned", say=r.announcer.announce("plan_ready")); return True
        if not r.steps:
            yield self._finish(r, "failed", msg="the planner produced no plan for the goals"); return True
        return False

    def _generate_and_certify(self, r):
        """SAGE's plan, checked as written; critical findings go back to SAGE's sub-goal editing."""
        t_plan = time.time()
        try:
            raw, meta = r.planner.generate_plan(r.restated, self._obs(r), [])
        except Exception as e:
            raw, meta = [], {"error": str(e)}
        r.steps = self._ground_all(r, raw)
        probs = self._check(r.steps, r.world, r.places, r.goals)
        yield r.ev("gate", round=0, problems=[p_[1] for p_ in probs], plan=[f"{x['action']} {x['object']}" for x in r.steps])
        edits = []
        if any(p_[2] for p_ in probs):
            # SAGE's sub-goal-local Editing with the checker's findings (SAGEPlanner.edit_block per block)
            edited, edits = self._sage_edit(r.planner, r.restated, self._obs(r), lambda s: self._ground_all(r, s),
                                            r.world, r.places, r.goals, r.goal_txt)
            if edited is not None:
                r.steps = edited
            probs = self._check(r.steps, r.world, r.places, r.goals)
            yield r.ev("gate", round=1, edits=edits, problems=[p_[1] for p_ in probs],
                       plan=[f"{x['action']} {x['object']}" for x in r.steps])
        r.run.plan_meta = dict(meta or {}, plan_sec=round(time.time() - t_plan, 2), llm_plan=self._ground_all(r, raw),
                               edits=edits)

    # ── 3. execute: guard each step; new plans only from SAGE ─────────────
    def _sage_replan(self, r, failed, reason) -> Optional[dict]:
        """SAGE suffix repair from step r.i. Returns the event to yield, or None when out of budget."""
        if r.replans >= self.cfg["max_replans"]:
            return None
        r.replans += 1
        world = r.world
        ers = (f"E: {failed.get('action')} {failed.get('object')} failed. R: {reason}. "
               f"S: robot at {world.arrived}, holding {world.holding or 'nothing'}; goals: {r.goal_txt}")
        try:
            suffix, _m = r.planner.replan(r.restated, r.completed, failed, ers, self._obs(r), [])
            suffix = self._ground_all(r, suffix)
        except Exception as e:
            suffix = []; ers += f"; replan error: {e}"
        probs = [p_[1] for p_ in self._check(r.completed + suffix, r.world_at_start, r.places, r.goals) if p_[2]]
        r.run.replans.append({"at_index": r.i, "failed": failed, "reason": ers, "suffix_len": len(suffix),
                              "problems": probs})
        r.steps = r.steps[:r.i] + suffix
        return r.ev("replan", at_index=r.i, reason=ers, problems=probs,
                    plan=[f"{x['action']} {x['object']}" for x in suffix],
                    say=r.announcer.announce("replan"), world=world.to_dict())

    def _replan_or_fail(self, r, failed, reason, fail_msg):
        """SAGE replan, else end the run with `fail_msg`: _NEXT or _STOP."""
        e_ = self._sage_replan(r, failed, reason)
        if e_ is None:
            yield self._finish(r, "failed", msg=fail_msg); return _STOP
        yield e_
        return _NEXT

    def _execute_phase(self, r):
        """Run the plan step by step. True when the run ended."""
        from ..run_control import wait_if_paused
        r.placed = {}
        r.pick_tries, r.move_tries, r.helped, r.seen_fail, r.seen_at, r.find_fails = {}, {}, set(), {}, {}, {}
        r.completed, r.replans, r.i = [], 0, 0
        r.world_at_start = r.world.copy()
        while True:
            if r.i >= len(r.steps):
                out = yield from self._plan_ended(r)
            else:
                wait_if_paused()             # a paused run holds here, between two steps
                stop = self._cancelled(r)
                if stop:
                    yield stop; return True
                out = yield from self._run_step(r, r.steps[r.i])
            if out is _STOP:
                return True
            if out is _DONE:
                return False

    def _plan_ended(self, r):
        """The plan ran out: _DONE when the goals hold, else a replan (_NEXT) or _STOP."""
        if self._goals_hold(r.goals, r.world, r.placed):
            return _DONE
        e_ = self._sage_replan(r, {"action": "Check", "object": "goals"},
                               "the plan ended but the goals are not reached: "
                               + "; ".join(self._missing(r.completed, r.world_at_start, r.goals)))
        if e_ is None or r.i >= len(r.steps):
            yield self._finish(r, "failed", msg="plan finished but the goals do not hold: " + r.goal_txt); return _STOP
        yield e_
        return _NEXT

    def _precondition(self, r, st) -> str:
        """Why the step cannot run in the belief ('' when it can)."""
        a, o, world = st["action"], st["object"], r.world
        if a in ("Place", "PutIn", "Open", "Close") and o in r.places.env and world.arrived != o:
            return f"the robot is at {world.arrived}, not at {o}"
        if a in ("Open", "Close") and world.holding:
            return f"the robot cannot {a.lower()} {o} while holding the {world.holding}"
        ok_pre, why_ = symbolic_check(st, world, openable=r.places.openable, capabilities=r.robot.model_capabilities())
        return "" if ok_pre else why_

    def _guard(self, r, st):
        """Preconditions before the robot moves (violations go to SAGE): None (go on), _NEXT, _STOP."""
        a, o, world = st["action"], st["object"], r.world
        why = self._precondition(r, st)
        if not why and a in ("Place", "PutIn") and world.holding:
            # still in the hand? (dropped on the way: `place` would otherwise go and pick something up)
            if not self._holding(r.node, default=True):
                lost = world.holding
                r.memory.see(lost, None, "dropped")
                world.holding = world.holding_since = None
                yield r.ev("recover", at_index=r.i, kind="dropped", msg=f"{lost} is no longer in the gripper")
                yield self._finish(r, "failed", msg=f"I dropped the {lost} on the way to {o.replace('@', ' in the ')}. "
                                                    f"Please pick it up; I can't pick things up from the floor.")
                return _STOP
        if not why:
            return None
        sig = ("pre", a, o, why)
        r.seen_fail[sig] = r.seen_fail.get(sig, 0) + 1
        e_ = self._sage_replan(r, st, f"precondition: {why}") if r.seen_fail[sig] <= 1 else None
        if e_ is None:
            yield self._finish(r, "failed", msg=f"{a} {o}: {why}"); return _STOP
        yield e_
        return _NEXT

    def _run_step(self, r, st):
        """Guard, bind, run and check one step: _NEXT, _DONE (goals reached) or _STOP."""
        a, o, started = st["action"], st["object"], time.time()
        out = yield from self._guard(r, st)
        if out is not None:
            return out
        try:
            mapped = self._bind(st, r.world)
        except Unbindable as e:
            return (yield from self._replan_or_fail(r, st, f"no skill for this step: {e}", f"{a} {o}: {e}"))
        if mapped is None:                                   # no-op action
            r.run.steps.append(StepRecord(index=r.i, action=a, object=o, skill=None, status="success",
                                          started_at=started, ended_at=time.time(),
                                          verifies=[VerifyResult("isdone", True, "no-op")]))
            r.completed.append(st); r.i += 1
            yield r.ev("step_done", index=r.i - 1, status="success", skill=None, world=r.world.to_dict())
            return _NEXT
        skill, params = mapped
        rec = StepRecord(index=r.i, action=a, object=o, skill=skill, params=params,
                         status="running", started_at=started)
        yield r.ev("step_start", index=r.i, action=a, object=o, skill=skill,
                   say=r.announcer.announce("step_start", **self._say(st, a, o)))
        res = run_skill(self.sr, skill, params, r.node, log_fn=self._log_fn(r.i))
        rec.result = res.to_legacy()
        stop = self._cancelled(r)
        if stop:
            rec.status = "aborted"; r.run.steps.append(rec)
            yield stop; return _STOP
        vres = self._post_check(r, st, skill, res, params, rec)
        rec.verifies = vres
        ok, reason = verdict(vres)
        rec.ended_at = time.time()
        yield r.ev("step_verify", index=r.i, verifies=_vr_list(vres))
        if ok:
            return (yield from self._step_succeeded(r, st, rec))
        return (yield from self._step_failed(r, st, skill, rec, res, reason))

    def _post_check(self, r, st, skill, res, params, rec) -> list:
        """After a reported success, the effects + the skill's VerifySpecs (manifest: Pick looked at again
        once the arm is folded, MoveTo measured against the ENV loc); a release looked at twice; a Find
        seen out of reach."""
        a, o = st["action"], st["object"]
        vres = r.verifier.verify(st, res, r.world, r.node, params=params)
        if (a in ("Place", "PutIn") and vres and not vres[0].ok
                and res.code is FailureCode.GRASP_FAILED and not self._holding(r.node, default=True)):
            # the release's own empty-gripper check fired right after letting go; looked once more
            vres = [VerifyResult("isdone", True, f"{skill}: second look, gripper empty"),
                    VerifyResult("recheck", True, f"first check: {vres[0].detail}")]
        if a == "Find" and vres and vres[0].ok:
            p3 = self._found_point(rec.result, o)
            r.seen_at[o] = p3
            if p3 and math.hypot(p3[0], p3[1]) > self.cfg["reach_far"]:
                near = self._place_of_far_point(p3, r.node, r.places, r.world)
                vres.append(VerifyResult("reach", False, f"the {o} was seen {math.hypot(p3[0], p3[1]):.1f} m away"
                                         + (f", near {near}" if near else ", not near any place") + ", out of reach from here"))
                if near:
                    r.seen_far.append(near); r.memory.see(o, near, "seen from afar")
        return vres

    def _step_succeeded(self, r, st, rec):
        a, o = st["action"], st["object"]
        rec.status = "success"; r.run.steps.append(rec)
        self._effect(st, r.world, r.memory, r.placed)
        r.completed.append(st)
        yield r.ev("step_done", index=r.i, status="success", result=rec.result,
                   say=r.announcer.announce("step_success", **self._say(st, a, o)), world=r.world.to_dict())
        r.i += 1
        if r.i < len(r.steps) and self._goals_hold(r.goals, r.world, r.placed):
            # the user's goals are met: end the task here rather than run what is left of the plan
            yield r.ev("status", msg=f"goals reached; {len(r.steps) - r.i} remaining plan steps not run")
            return _DONE
        return _NEXT

    def _step_failed(self, r, st, skill, rec, res, reason):
        """A failed step: stop (policy / same failure again), recover in place, or SAGE replan."""
        a, o = st["action"], st["object"]
        rec.status = "failed"; r.run.steps.append(rec)
        yield r.ev("step_done", index=r.i, status="failed", result=rec.result, reason=reason,
                   say=r.announcer.announce("step_fail", **self._say(st, a, o), reason=reason),
                   world=r.world.to_dict())
        # ── stop at once: the recovery policy does not retry it (a skill bug, an e-stop), or the same
        # failure twice ──
        code = r.robot.failure_code(res, reason)
        rule = r.robot.policy.rule_for(code)
        if not rule.retry_allowed:
            what = "internal error in" if code is FailureCode.SOFTWARE else f"{code.value} in"
            yield self._finish(r, "failed", msg=f"{what} the {skill} skill (not retried): {reason}"); return _STOP
        sig = (a, o, r.world.arrived, re.sub(r"[-\d.]+", "#", str(reason))[:160])
        r.seen_fail[sig] = r.seen_fail.get(sig, 0) + 1
        if r.seen_fail[sig] > 2:
            yield self._finish(r, "failed", msg=f"{a} {o} failed again the same way: {reason}"); return _STOP
        # ── skill-level retry in place / ask the person (not planning) ──
        recover = {"Find": self._recover_find, "Pick": self._recover_pick, "MoveTo": self._recover_move,
                   "Place": self._recover_place, "PutIn": self._recover_place}.get(a)
        out, extra = (yield from recover(r, st, reason, rule)) if recover else (None, "")
        if out is not None:
            return out
        # ── a new plan: SAGE suffix repair ──
        return (yield from self._replan_or_fail(r, st, f"{reason}{extra}",
                                                f"{a} {o} failed: {reason} (replan budget used)"))

    def _recover_find(self, r, st, reason, rule):
        """Not seen here: mark the place searched (absence only, the policy says), search on through
        SAGE, ask the person where it is after search_max. Returns (outcome or None, replan hint)."""
        o, world = st["object"], r.world
        world.found = None
        # absent: the place counts as searched; could not look / out of reach: it does not (policy)
        if world.arrived and world.arrived not in r.searched and rule.mark_searched:
            r.searched.append(world.arrived)
        r.find_fails[o] = r.find_fails.get(o, 0) + 1
        if r.find_fails[o] > self.cfg["search_max"] and self.cfg["ask_help"] and ("where", o) not in r.helped:
            r.helped.add(("where", o))
            q = f"I couldn't find the {o}. Where is it?"
            yield r.ev("clarify", question=q, round=0)
            ans = self._ask(q, r.node, self._log_fn(r.i), r.lang)
            k = r.places.ground(ans, ans) if ans else None
            if not k:
                yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})")
                return _STOP, ""
            r.memory.remember_answer(r.task, q, ans); r.memory.see(o, k, "user said")
            if k in r.searched:
                r.searched.remove(k)
            return None, f"; the user says the {o} is on {k}"
        if r.find_fails[o] > self.cfg["search_max"] + 1:
            yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})")
            return _STOP, ""
        return None, (f"; it is not at {world.arrived}. Choose where to look next among the places not yet "
                      f"searched" + (f" (it was seen near {r.seen_far[-1]})" if r.seen_far else ""))

    def _recover_pick(self, r, st, reason, rule):
        """Look + grasp again in place (pick_retries, not when out of lateral reach), then ask the person
        to move it closer. Returns (outcome or None, replan hint)."""
        o, world = st["object"], r.world
        if self._holding(r.node, default=False):
            # failed, yet something is in the gripper (a neighbour?): never carry on with an unknown object
            world.holding = f"unknown object (picking {o})"
            yield self._finish(r, "failed", msg=f"the pick of the {o} failed but something is in my gripper; "
                                                f"please take it from me: {reason}")
            return _STOP, ""
        r.pick_tries[o] = r.pick_tries.get(o, 0) + 1
        p3 = r.seen_at.get(o)
        out_of_reach = bool(p3) and abs(p3[1]) > self.cfg["reach_lateral"]
        if r.pick_tries[o] <= self.cfg["pick_retries"] and not out_of_reach:
            # retry the same skill pair in place: look again, grasp again
            r.steps[r.i:r.i] = [{"action": "Find", "object": o}]
            world.found = None
            yield r.ev("recover", at_index=r.i, kind="retry", msg=f"pick {o} failed; looking again")
            return _NEXT, ""
        extra = ""
        if self.cfg["ask_help"] and ("pick", o) not in r.helped:
            r.helped.add(("pick", o))
            q = (f"The {o} is too far for my arm. Could you move it closer to the edge, then say yes?"
                 if out_of_reach else f"I can't pick up the {o}. Could you move it closer to the edge, then say yes?")
            yield r.ev("clarify", question=q, round=0)
            ans = self._ask(q, r.node, self._log_fn(r.i), r.lang)
            k = r.places.ground(ans, ans) if ans else None
            if ans and self.yes_no(ans)[0] is True:
                r.steps[r.i:r.i] = [{"action": "Find", "object": o}]
                world.found = None
                yield r.ev("recover", at_index=r.i, kind="asked", msg="user helped; trying again")
                return _NEXT, ""
            if k and k != world.arrived:
                r.memory.see(o, k, "user said"); world.found = None
                extra = f"; the user says the {o} is on {k}"
        if not extra:
            yield self._finish(r, "failed", msg=f"could not pick up the {o}: {reason}")
            return _STOP, ""
        return None, extra

    def _recover_move(self, r, st, reason, rule):
        """Move again once, then ask the person to clear the way; else stop."""
        o = st["object"]
        r.move_tries[o] = r.move_tries.get(o, 0) + 1
        if r.move_tries[o] <= 1:
            yield r.ev("recover", at_index=r.i, kind="retry", msg=f"move to {o} again")
            return _NEXT, ""
        if self.cfg["ask_help"] and ("move", o) not in r.helped:
            r.helped.add(("move", o))
            q = (f"I can't get to the {o.replace('@', ' in the ')}. Is something in the way? "
                 f"Please clear the way and say yes.")
            yield r.ev("clarify", question=q, round=0)
            ans, _n = self._ask_heard(q, r.node, self._log_fn(r.i), r.lang)
            if self.yes_no(ans)[0] is True:
                yield r.ev("recover", at_index=r.i, kind="asked", msg="user cleared the way; trying again")
                return _NEXT, ""
        yield self._finish(r, "failed", msg=f"could not get to {o}: {reason}")
        return _STOP, ""

    def _recover_place(self, r, st, reason, rule):
        """A failed release with an empty hand: the object is gone, stop; else SAGE replans."""
        world = r.world
        if not self._holding(r.node, default=True):
            if world.holding:
                r.memory.see(world.holding, None, "dropped")
            world.holding = world.holding_since = None
            yield self._finish(r, "failed", msg=f"place failed and the object is no longer in the hand: {reason}")
            return _STOP, ""
        return None, ""

    # ── 4. done only when the goals hold ──────────────────────────────────
    def _final_check(self, r) -> dict:
        if self._goals_hold(r.goals, r.world, r.placed):
            self._record_episode(r.planner, r.restated, r.completed)
            return self._finish(r, "success")
        return self._finish(r, "failed", msg="plan finished but the goals do not hold: "
                            + json.dumps(r.goals, ensure_ascii=False))
