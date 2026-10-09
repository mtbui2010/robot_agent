"""AssistLoop — the assistive layer around the SAGE planner: understanding, clarification, a guard per step.

Planning is SAGE's (paper: "SAGE: Symbolic Action-Gating and Editing for LLM Task Planners"; code identifier
GRACE, ``pyplanner.GRACEPlanner``): every plan and every change of plan comes from it. This loop never adds,
drops or reorders steps itself. Selected with planner ``'sage'`` on ``/ws/agent`` (dashboard: "SAGE + assist")
or ``ROBOT_AGENT_PLANNER=sage``; ``'grace'`` is the plain ``ClosedLoop`` around the same planner.

1. **Understand** (one LLM call, JSON): the task becomes goals ``{type: on|in|open|close, object, from, to}`` or
   a refusal (``unsafe`` / ``infeasible``). Places are grounded on the live ENV without the LLM; a place word
   naming several places, a place the user never said, or "me" without a place is a question for the user
   (HRI ``ask`` skill); answers are read back ("I will … Is that right?").
2. **Plan**: SAGE ``generate_plan``; the checker (``_check``) simulates the plan as written and reports rule
   violations (wrong place, Pick without Find, Open with a full hand, goals not reached …); critical ones go back
   to SAGE as an edit request (``replan`` from the first bad step), up to ``edit_rounds`` times.
3. **Guard each step**: preconditions before the robot moves (a violation -> SAGE replan); after the skill, its
   own result + symbolic + extra checks (MoveTo measured against the ENV loc, Pick looked at again, Place looked at
   twice, a Find seen out of reach). Skill-level retries in place stay here (move again, look + grasp again), as do
   requests to the person (clear the way, move the object to the edge, where is it?). Anything that needs a
   different plan — the object is not here, the robot is at the wrong place, the plan ended short of the goals —
   goes to SAGE's suffix repair with an E / R / S reason (``max_replans``). A skill bug, a dropped object or the
   same failure twice stops the run and says why.
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

from robot_agent.state import current, host_id
from .announcer import Announcer
from .loop import ClosedLoop, _vr_list, reconcile_world
from .mapper import ActionMapper, Unmappable
from .records import RunLogger, StepRecord, VerifyResult, new_task_record
from .verify import StepVerifier, symbolic_check, verdict

_LOC_ACTIONS = {"MoveTo", "Place", "PutIn", "Open", "Close"}
# per-run overrides accepted from /ws/agent `options`
ASSIST_OPTIONS = {"confirm_task", "confirm_answers", "silence_retries", "clarify_rounds", "search_max",
                "pick_retries", "ask_help", "arrive_tol", "find_min_conf", "find_verify", "reach_far", "reach_lateral"}
_USER_WORDS = {"me", "user", "here", "you", "myself", "person"}
# spoken yes / no (ko / vi / en) for read-backs
_YES = re.compile(r"\b(yes|yeah|yep|ok|okay|right|correct|sure|go ahead|네|예|응|맞아|맞아요|좋아|có|đúng|vâng|ừ)\b")
_NO = re.compile(r"\b(no|nope|not|wrong|아니|아니요|아뇨|không|sai)\b")
# a bug in a skill, not the world: retrying / replanning cannot help
_SOFTWARE_ERR = re.compile(r"unexpected keyword argument|missing \d+ required|is not defined|has no attribute|"
                           r"TypeError|NameError|AttributeError|ImportError|SyntaxError")


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
    """The live ENV as a closed vocabulary of places."""

    def __init__(self, env: dict):
        self.env = {k: v for k, v in (env or {}).items() if isinstance(v, dict)}
        self.keys = list(self.env)

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
        return bool(v.get("openable")) or "drawer" in key.lower()

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

UNDERSTAND_PROMPT = """You turn a household robot task into goals. The robot is a mobile manipulator. It can:
move to a place, find an object, pick it up, place it on a surface, open / close a drawer, put an object in an open drawer.
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
            # MoveTo: farther than this from the ENV loc counts as not arrived (nav "succeeded" off the spot)
            "arrive_tol": float(overrides.get("arrive_tol", env("ROBOT_AGENT_ARRIVE_TOL", "0.3"))),
            # Find: detections below this score are treated as not seen (kcare's own default is 0). Sim, 109
            # finds: 0 -> absent objects "found" 48/82, real ones right 18/27; 0.55 -> 6/82 and 16/27
            "find_min_conf": float(overrides.get("find_min_conf", env("ROBOT_AGENT_FIND_MIN_CONF", "0.55"))),
            # Find: a VLM checks the crop of each low-scoring detection (kcare find::verify). Off: in the sim it
            # removed no more absent objects than the 0.55 threshold, lost real ones and cost ~8 s per find
            "find_verify": overrides.get("find_verify", env("ROBOT_AGENT_FIND_VERIFY", "0") == "1"),
            # base-frame distances: beyond reach_far a detection is "over there" (look-and-go); a pick whose
            # target is more than reach_lateral to the side is not retried (kcare: 0/23 beyond 0.74 m)
            "reach_far": float(overrides.get("reach_far", env("ROBOT_AGENT_REACH_FAR", "1.0"))),
            "reach_lateral": float(overrides.get("reach_lateral", env("ROBOT_AGENT_REACH_LATERAL", "0.78"))),
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
        """Ask the user through the HRI `ask` skill. '' when there is no answer / no channel."""
        try:
            r = self.sr.execute("ask", {"inputs": self._in_lang(question, lang), "retries": 0, "lang": lang},
                                node, log_fn=log_fn)
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
        Critical problems go back to SAGE for an edit; the others are only mentioned alongside them."""
        probs = []
        P = lambda i, txt, crit=True: probs.append((i, txt, crit))
        need_open = {g["to"] for g in goals if g["type"] == "in"} | {g["object"] for g in goals if g["type"] == "open"}
        need_close = {g["object"] for g in goals if g["type"] == "close"}
        src_of = {g["object"]: g.get("from") for g in goals}
        known = set(getattr(self._namemap, "SKILL_MAP", {})) | set(getattr(self._namemap, "NOOP_ACTIONS", set()))
        nice = lambda k: (k or "").replace("@", " in the ")
        at, found, holding, opened, placed_ok = world.arrived, world.found, world.holding, set(world.opened), {}
        for i, st in enumerate(steps):
            a, o = st.get("action"), st.get("object") or ""
            bad_place = not st.get("grounded", True) or o.startswith("@")
            if a not in known:
                P(i, f"step {i + 1} '{a} {o}': the robot has no action {a} (use MoveTo, Find, Pick, Place, PutIn, Open, Close)")
                continue
            if a == "MoveTo":
                if bad_place:
                    P(i, f"step {i + 1} 'MoveTo {o}': {o.lstrip('@')} is not a place the robot can go to; use one of the listed places")
                else:
                    if o == at:
                        P(i, f"step {i + 1} 'MoveTo {o}': the robot is already there", False)
                    at, found = o, None
            elif a in ("Open", "Close"):
                if not places.openable(o):
                    P(i, f"step {i + 1} '{a} {o}': {nice(o)} cannot be opened or closed")
                    continue
                if o not in (need_open if a == "Open" else need_close) and o not in src_of.values():
                    P(i, f"step {i + 1} '{a} {o}': no goal needs it; remove it")
                if holding:
                    P(i, f"step {i + 1} '{a} {o}': the robot cannot {a.lower()} it while holding the {holding}; do it before the Pick")
                if at != o:
                    P(i, f"step {i + 1} '{a} {o}': the robot must MoveTo {o} first")
                (opened.add if a == "Open" else opened.discard)(o)
            elif a == "Find":
                if holding == o:
                    P(i, f"step {i + 1} 'Find {o}': already done (the {o} is in the hand); delete this step")
                if src_of.get(o) and at != src_of[o]:
                    P(i, f"step {i + 1} 'Find {o}': the user said it is on {nice(src_of[o])}; MoveTo {src_of[o]} first")
                found = o
            elif a == "Pick":
                g_ = next((g for g in goals if g["object"] == o), None)
                if g_ and placed_ok.get(o) == g_.get("to"):
                    P(i, f"step {i + 1} 'Pick {o}': the {o} was already placed on {g_['to']} by an earlier step; delete the steps that move it again")
                if holding == o:
                    P(i, f"step {i + 1} 'Pick {o}': already done by an earlier step (the {o} is in the hand); delete this step")
                elif holding:
                    P(i, f"step {i + 1} 'Pick {o}': the hand already holds the {holding}; Place it first")
                elif found != o:
                    P(i, f"step {i + 1} 'Pick {o}': needs 'Find {o}' just before it, at the same place")
                holding, found = (holding or o), None
            elif a in ("Place", "PutIn"):
                g = next((g for g in goals if g["object"] == holding), None)
                if not holding:
                    P(i, f"step {i + 1} '{a} {o}': nothing is in the hand")
                if bad_place:
                    P(i, f"step {i + 1} '{a} {o}': not a place" + (f"; the destination is {g['to']}" if g else ""))
                elif g and g.get("to") and o != g["to"]:
                    P(i, f"step {i + 1} '{a} {o}': the {holding} must go to {g['to']}, not {o}")
                elif at != o:
                    P(i, f"step {i + 1} '{a} {o}': the robot must MoveTo {o} first")
                if a == "PutIn" and o not in opened:
                    P(i, f"step {i + 1} 'PutIn {o}': {nice(o)} is closed; Open it before the Pick")
                if holding:
                    placed_ok[holding] = o
                holding = None
        missing = self._missing(steps, world, goals)
        if missing:
            P(len(steps), "the plan does not achieve: " + "; ".join(missing))
        else:
            for k in range(len(steps)):
                if self._reaches(steps[:k], world, goals):
                    P(k, f"the goals are done after step {k}; remove the steps after it (do not repeat the task)"); break
        return probs

    def _sage_edit(self, planner, task, obs, ground, world, places, goals, goal_txt):
        """SAGE's own Editing, driven by the checker: every sub-goal block with a critical finding is re-done by
        GRACEPlanner._refine (sub-goal-local repair) with that finding as feedback; a block whose sub-goal the
        earlier blocks already achieved is asked for no steps. Returns (plan, [edit notes]); the plan is
        SAGE's ``_flatten`` of its blocks, as generate_plan returns it."""
        inner = getattr(planner, "_p", planner)
        blocks = getattr(inner, "_last_blocks", None)
        if not blocks or not hasattr(inner, "_refine"):
            return None, []
        from pyplanner.grace import _flatten
        from pyplanner.verifier import SymbolicState, simulate
        ex_block = inner._retrieve_block(task)[1] if hasattr(inner, "_retrieve_block") else ""
        notes = []
        for _round in range(self.cfg["edit_rounds"]):
            per_block, n_before, done_at = {}, 0, None
            for k in range(len(blocks)):
                flat_k = ground(_flatten(blocks[:k + 1]))
                if done_at is None and blocks[k].steps and k > 0 and self._reaches(ground(_flatten(blocks[:k])), world, goals):
                    done_at = k
                rng = range(n_before, len(flat_k))
                per_block[k] = rng
                n_before = len(flat_k)
            flat = ground(_flatten(blocks))
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
                state = simulate(prev, initial_state=SymbolicState(), stop_on_error=False).final_state or SymbolicState()
                fb = ("Checker findings (fix them; places must be exactly the listed ones):\n"
                      + "\n".join(f"- {t}" for t in items) + f"\nGoals of the whole task: {goal_txt}")
                try:
                    new_steps = inner._refine(blocks[k].name, task, blocks[k].steps, fb, state, obs, [], ex_block)[0]
                except Exception as e:
                    notes.append(f"sub-goal {k + 1} edit failed: {e}"); continue
                empty_ok = any('"steps": []' in t for t in items)
                if new_steps or empty_ok:
                    blocks[k].steps = new_steps
                notes.append(f"sub-goal {k + 1} '{blocks[k].name}': " + "; ".join(items))
        return ground(_flatten(blocks)), notes

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
    def _params(self, st, world):
        a, o = st["action"], st["object"]
        p = self._build_params(a, o, world, {})
        if a in _LOC_ACTIONS:
            p["inputs"] = o
        elif a in ("Find", "Pick"):
            p["inputs"] = o
        if a == "Find" and self.cfg["find_min_conf"] > 0:
            p["min_conf"] = self.cfg["find_min_conf"]
        if a == "Find" and self.cfg["find_verify"]:
            p["verify"] = True
        return p

    def _dist_to_place(self, key, node, places):
        """Base distance (m) from the ENV loc of `key`, or None when unknown."""
        l = (places.env.get(key) or {}).get("loc") or {}
        if "x" not in l or "y" not in l:
            return None
        try:
            x, y, _ = self.sr.execute("mobile_pose", {}, node)["pose"]
        except Exception:
            return None
        return math.hypot(x - l["x"], y - l["y"])

    def _effect(self, st, world, memory, placed):
        a, o = st["action"], st["object"]
        if a == "MoveTo":
            world.arrived = o
            world.found = None                       # what was found is behind us
        elif a == "Find":
            world.found = o; memory.see(o, world.arrived, "found")
        elif a == "Pick":
            world.holding, world.found, world.holding_since = o, None, time.time()
            memory.see(o, "robot", "picked")
        elif a in ("Place", "PutIn"):
            if world.holding:
                placed[world.holding] = o; memory.see(world.holding, o, "placed")
            world.holding = world.holding_since = None; world.holding_pose = None
        elif a == "Open":
            world.opened.add(o)
        elif a == "Close":
            world.opened.discard(o)
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
        try:
            x, y, rz = self.sr.execute("mobile_pose", {}, node)["pose"]
        except Exception:
            return None
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

    # ── main ──────────────────────────────────────────────────────────────
    def run_blocking(self, task: str, lang: str = "en", plan_only: bool = False) -> Iterator[dict]:
        nm = self._namemap
        mapper = ActionMapper(nm)
        verifier = StepVerifier(self.cfg["vlm_enabled"], set(getattr(nm, "VLM_ACTIONS", set())),
                                getattr(nm, "vlm_hook", None))
        announcer = Announcer(lang=lang, speak_backend=self.cfg["speak_backend"])
        world = current().world
        node = getattr(self.dm, "_ros_node", None)
        reconcile_world(node, world)
        from robot_agent.skill_configs import ENV
        places = Places(dict(ENV))
        memory = Memory(self.cfg["memory_path"])

        muted = False
        if self.cfg["mute_skill_tts"]:
            try:
                from robot_agent.utils import set_skill_tts_muted
                set_skill_tts_muted(True); muted = True
            except Exception:
                pass
        now = time.time()
        run_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
        run = new_task_record(run_id, task, lang=lang, planner="sage+assist", started_at=now)
        logger = RunLogger(run, self.cfg["log_dir"] / f"{run_id}.jsonl")

        def ev(event: str, say: Optional[str] = None, **fields) -> dict:
            d = {"event": event, **fields}
            if say is not None:
                d["say"] = say
            try:
                logger.event(event, t=time.time(), **{k: v for k, v in d.items() if k != "event"})
            except Exception:
                pass
            return d

        def log_fn_for(i):
            if self._emit is None:
                return None
            from ..unified_agent import _make_log_fn
            return _make_log_fn(self._emit, i, allow_say=False)

        from ..run_control import cancel_requested

        def finish(status, msg="", **kw):
            run.status = status; run.ended_at = time.time()
            kind = "done_success" if status == "success" else "done_fail"
            say = kw.pop("say", None)
            if say is None and status in ("failed", "refused") and msg and not msg.startswith(("internal error", "plan finished")):
                # tell the user why (dropped it, can't find it, ...), without the skill's error text
                say = re.split(r": [\'\"{]|: Exception| \(replan| \(searched", msg)[0].strip()
            return ev("done", status=status, run_id=run_id, msg=msg,
                      say=self._in_lang(say, lang) if say else announcer.announce(kind), world=world.to_dict(), **kw)

        try:
            yield ev("task_start", say=announcer.announce("task_start"), task=task, run_id=run_id,
                     world=world.to_dict())

            # ── 1. understand + clarify ───────────────────────────────────
            dialogue, asked, confirms, pin_to = [], 0, 0, None
            while True:
                yield ev("status", msg="Understanding the task...")
                u = self._understand(task, places, memory, world, dialogue)
                if pin_to:
                    # "no, the shelf in the restroom" after a read-back names the destination (a 7B model put
                    # it in `from`): pin it instead of trusting the model's slots
                    for g in u["goals"]:
                        if g.get("type") in ("on", "in"):
                            if places.ground(g.get("from") or "", g.get("from") or "") == pin_to:
                                g["from"] = ""
                            g["to"] = pin_to
                answers = [t for w_, t in dialogue if w_ == "user"]
                goals, q = self._ground_goals(u["goals"], places, memory,
                                              " ".join([task, self.original or ""] + answers),
                                              latest=answers[-1] if answers else "")
                yield ev("understand", status=u["status"], reason=u.get("reason"), goals=goals,
                         question=u.get("question") or q)
                if u["status"] == "infeasible" and dialogue and goals:
                    u["status"] = "clear"                # the user already answered: a 7B model called a plain
                                                         # move "infeasible" after "no, the shelf in the restroom"
                if u["status"] in ("unsafe", "infeasible"):
                    msg = u.get("reason") or "I can't do that."
                    yield finish("refused", msg=msg, say=f"I can't do that: {msg}")
                    return
                # the grounding decides about places; the model's "ambiguous" counts only when it found nothing
                # to do (after "no, the shelf in the restroom" a 7B model kept asking which shelf)
                if u["status"] == "ambiguous" and u.get("question") and not goals:
                    q = u["question"] if not q else f"{u['question']} {q}"
                if not goals and not q:
                    q = u.get("question") or "What should I do exactly?"
                if not q:
                    # read the goals back when the user answered something (a misheard answer is only caught
                    # here) or always (`confirm_task`, a voice user whose command itself may be misheard)
                    if not ((dialogue and self.cfg["confirm_answers"]) or self.cfg["confirm_task"]):
                        break
                    if confirms >= 2:
                        yield finish("failed", msg="the user did not confirm the task"); return
                    confirms += 1
                    cq = f"{self.summary(goals)} Is that right?"
                    yield ev("clarify", question=cq, round=confirms, kind="confirm")
                    ans, _n = self._ask_heard(cq, node, log_fn_for(-1), lang)
                    if cancel_requested():
                        yield finish("aborted", msg="Cancelled by user"); return
                    ok_, rest = self.yes_no(ans)
                    if ok_ is True:
                        break
                    if ok_ is None:
                        yield finish("failed", msg=f"no answer to: {cq}"); return
                    dialogue += [("robot", cq), ("user", ans)]
                    if not rest:                     # a bare "no": ask what instead
                        q = "Sorry. What should I do?"
                    else:
                        memory.remember_answer(task, cq, ans)
                        k = places.ground(rest, rest)
                        if k and not re.search(r"\b(from|take|pick)\b", rest):
                            pin_to = k
                        continue
                if asked >= self.cfg["clarify_rounds"]:
                    yield finish("failed", msg=f"still unclear after {asked} questions: {q}")
                    return
                asked += 1
                yield ev("clarify", question=q, round=asked)
                ans, _n = self._ask_heard(q, node, log_fn_for(-1), lang)
                if cancel_requested():
                    yield finish("aborted", msg="Cancelled by user"); return
                if not ans:
                    yield finish("failed", msg=f"no answer to: {q}")
                    return
                memory.remember_answer(task, q, ans)
                dialogue += [("robot", q), ("user", ans)]

            run.obs = "goals: " + json.dumps(goals, ensure_ascii=False)

            # ── 2. plan: SAGE generates, the checker reports, SAGE edits ──
            yield ev("status", msg="Generating task plan...")
            restated = task + ("" if not dialogue else " (" + "; ".join(t for w_, t in dialogue if w_ == "user") + ")")
            goal_txt = "; ".join(f"{g['type']} {g['object']}" + (f" from {g['from']}" if g.get('from') else "")
                                 + (f" to {g['to']}" if g.get('to') else "") for g in goals)
            searched, seen_far = [], []

            def make_obs():
                txt = ("Places the robot can go to (use exactly these):\n" + "\n".join(f"- {k}" for k in places.keys)
                       + "\nGoals: " + goal_txt + "\nRobot state: " + world.as_text())
                if searched:
                    txt += "\nAlready looked (object not there): " + ", ".join(searched)
                return txt

            planner = self._make_planner()
            try:
                inner = getattr(planner, "_p", planner)
                if self._emit is not None and hasattr(inner, "progress"):
                    _emit = self._emit
                    inner.progress = lambda d: _emit({"event": "plan_step", **d})
            except Exception:
                pass
            ground = lambda seq: [self._ground_step(x, places, goals, task) for x in seq]
            t_plan = time.time()
            try:
                raw, meta = planner.generate_plan(restated, make_obs(), [])
            except Exception as e:
                raw, meta = [], {"error": str(e)}
            steps = ground(raw)
            probs = self._check(steps, world, places, goals)
            yield ev("gate", round=0, problems=[p_[1] for p_ in probs], plan=[f"{x['action']} {x['object']}" for x in steps])
            edits = []
            if any(p_[2] for p_ in probs):
                # SAGE's sub-goal-local Editing with the checker's findings (GRACEPlanner._refine per block)
                edited, edits = self._sage_edit(planner, restated, make_obs(), ground, world, places, goals, goal_txt)
                if edited is not None:
                    steps = edited
                probs = self._check(steps, world, places, goals)
                yield ev("gate", round=1, edits=edits, problems=[p_[1] for p_ in probs],
                         plan=[f"{x['action']} {x['object']}" for x in steps])
            run.plan_meta = dict(meta or {}, plan_sec=round(time.time() - t_plan, 2), llm_plan=ground(raw), edits=edits)
            plan_text = "\n".join(f"{nm.SKILL_MAP.get(x['action'], x['action'])}::{x['object']}" for x in steps)
            yield ev("plan", steps=steps, plan_meta=run.plan_meta, warnings=[], plan_text=plan_text,
                     world=world.to_dict())
            if plan_only:
                yield finish("planned", say=announcer.announce("plan_ready")); return
            if not steps:
                yield finish("failed", msg="the planner produced no plan for the goals"); return

            # ── 3. execute: guard each step; new plans only from SAGE ─────
            completed, placed = [], {}
            pick_tries, move_tries, helped, seen_fail, seen_at, find_fails = {}, {}, set(), {}, {}, {}
            replans, i = 0, 0

            def sage_replan(failed, reason):
                """SAGE suffix repair from step i. Returns the event to yield, or None when out of budget."""
                nonlocal steps, replans
                if replans >= self.cfg["max_replans"]:
                    return None
                replans += 1
                ers = (f"E: {failed.get('action')} {failed.get('object')} failed. R: {reason}. "
                       f"S: robot at {world.arrived}, holding {world.holding or 'nothing'}; goals: {goal_txt}")
                try:
                    suffix, _m = planner.replan(restated, completed, failed, ers, make_obs(), [])
                    suffix = ground(suffix)
                except Exception as e:
                    suffix = []; ers += f"; replan error: {e}"
                probs = [p_[1] for p_ in self._check(completed + suffix, world_at_start, places, goals) if p_[2]]
                run.replans.append({"at_index": i, "failed": failed, "reason": ers, "suffix_len": len(suffix),
                                    "problems": probs})
                steps = steps[:i] + suffix
                return ev("replan", at_index=i, reason=ers, problems=probs,
                          plan=[f"{x['action']} {x['object']}" for x in suffix],
                          say=announcer.announce("replan"), world=world.to_dict())

            world_at_start = world.copy()
            while True:
                if i >= len(steps):
                    if self._goals_hold(goals, world, placed):
                        break
                    e_ = sage_replan({"action": "Check", "object": "goals"},
                                     "the plan ended but the goals are not reached: "
                                     + "; ".join(self._missing(completed, world_at_start, goals)))
                    if e_ is None or i >= len(steps):
                        yield finish("failed", msg="plan finished but the goals do not hold: " + goal_txt); return
                    yield e_
                    continue
                if cancel_requested():
                    yield finish("aborted", msg="Cancelled by user"); return
                st = steps[i]
                a, o = st["action"], st["object"]
                started = time.time()

                # ── preconditions, before the robot moves (violations go to SAGE) ──
                why = ""
                if a in ("Place", "PutIn", "Open", "Close") and o in places.env and world.arrived != o:
                    why = f"the robot is at {world.arrived}, not at {o}"
                elif a in ("Open", "Close") and world.holding:
                    why = f"the robot cannot {a.lower()} {o} while holding the {world.holding}"
                else:
                    ok_pre, why_ = symbolic_check(st, world)
                    why = "" if ok_pre else why_
                if not why and a in ("Place", "PutIn") and world.holding:
                    # still in the hand? (dropped on the way: `place` would otherwise go and pick something up)
                    try:
                        in_hand = bool(self.sr.execute("grasp_succeed", {}, node).get("isdone"))
                    except Exception:
                        in_hand = True
                    if not in_hand:
                        lost = world.holding
                        memory.see(lost, None, "dropped")
                        world.holding = world.holding_since = None
                        yield ev("recover", at_index=i, kind="dropped", msg=f"{lost} is no longer in the gripper")
                        yield finish("failed", msg=f"I dropped the {lost} on the way to {o.replace('@', ' in the ')}. "
                                                   f"Please pick it up; I can't pick things up from the floor.")
                        return
                if why:
                    sig = ("pre", a, o, why)
                    seen_fail[sig] = seen_fail.get(sig, 0) + 1
                    e_ = sage_replan(st, f"precondition: {why}") if seen_fail[sig] <= 1 else None
                    if e_ is None:
                        yield finish("failed", msg=f"{a} {o}: {why}"); return
                    yield e_
                    continue

                try:
                    mapped = mapper.to_skill(dict(st), world)
                except Unmappable as e:
                    mapped = e
                if mapped is None:                                   # no-op action
                    run.steps.append(StepRecord(index=i, action=a, object=o, skill=None, status="success",
                                                started_at=started, ended_at=time.time(),
                                                verifies=[VerifyResult("isdone", True, "no-op")]))
                    completed.append(st); i += 1
                    yield ev("step_done", index=i - 1, status="success", skill=None, world=world.to_dict())
                    continue
                if isinstance(mapped, Exception):
                    e_ = sage_replan(st, f"no skill for this step: {mapped}")
                    if e_ is None:
                        yield finish("failed", msg=f"{a} {o}: {mapped}"); return
                    yield e_
                    continue

                skill = mapped[0]
                params = self._params(st, world)
                rec = StepRecord(index=i, action=a, object=o, skill=skill, params=params,
                                 status="running", started_at=started)
                yield ev("step_start", index=i, action=a, object=o, skill=skill,
                         say=announcer.announce("step_start", **self._say(st, a, o)))
                try:
                    result = self.sr.execute(skill, params, node, log_fn=log_fn_for(i))
                except Exception as e:
                    result = {"isdone": False, "msg": str(e)}
                rec.result = result if isinstance(result, dict) else {"result": result}
                if cancel_requested():
                    rec.status = "aborted"; run.steps.append(rec)
                    yield finish("aborted", msg="Cancelled by user"); return

                # ── post-checks ──
                vres = verifier.verify(st, rec.result, world, node)
                if (a in ("Place", "PutIn") and vres and not vres[0].ok
                        and "still in the gripper" in str(vres[0].detail)):
                    # place's own empty-gripper check fired right after the release; look once more
                    try:
                        if not self.sr.execute("grasp_succeed", {}, node).get("isdone"):
                            vres = [VerifyResult("isdone", True, "place: second look, gripper empty"),
                                    VerifyResult("recheck", True, f"first check: {vres[0].detail}")]
                    except Exception:
                        pass
                if a == "Pick" and vres and vres[0].ok:
                    # pick's own check passed on an empty gripper ~1 in 10 sim runs: look again once the arm is folded
                    try:
                        if not self.sr.execute("grasp_succeed", {}, node).get("isdone"):
                            vres.append(VerifyResult("in_hand", False, "pick reported success but the gripper is empty"))
                    except Exception:
                        pass
                if a == "MoveTo" and vres and vres[0].ok:
                    # navigation may report success off the spot: measure it
                    d_off = self._dist_to_place(o, node, places)
                    if d_off is not None and d_off > self.cfg["arrive_tol"]:
                        vres.append(VerifyResult("localization", False, f"stopped {d_off:.2f} m from {o}"))
                if a == "Find" and vres and vres[0].ok:
                    p3 = self._found_point(rec.result, o)
                    seen_at[o] = p3
                    if p3 and math.hypot(p3[0], p3[1]) > self.cfg["reach_far"]:
                        near = self._place_of_far_point(p3, node, places, world)
                        vres.append(VerifyResult("reach", False, f"the {o} was seen {math.hypot(p3[0], p3[1]):.1f} m away"
                                                 + (f", near {near}" if near else ", not near any place") + ", out of reach from here"))
                        if near:
                            seen_far.append(near); memory.see(o, near, "seen from afar")
                rec.verifies = vres
                ok, reason = verdict(vres)
                rec.ended_at = time.time()
                yield ev("step_verify", index=i, verifies=_vr_list(vres))

                if ok:
                    rec.status = "success"; run.steps.append(rec)
                    self._effect(st, world, memory, placed)
                    completed.append(st)
                    yield ev("step_done", index=i, status="success", result=rec.result,
                             say=announcer.announce("step_success", **self._say(st, a, o)), world=world.to_dict())
                    i += 1
                    if i < len(steps) and self._goals_hold(goals, world, placed):
                        # the user's goals are met: end the task here rather than run what is left of the plan
                        yield ev("status", msg=f"goals reached; {len(steps) - i} remaining plan steps not run")
                        break
                    continue

                rec.status = "failed"; run.steps.append(rec)
                yield ev("step_done", index=i, status="failed", result=rec.result, reason=reason,
                         say=announcer.announce("step_fail", **self._say(st, a, o), reason=reason),
                         world=world.to_dict())

                # ── stop at once: a skill bug, or the same failure twice ──
                if _SOFTWARE_ERR.search(str(reason)):
                    yield finish("failed", msg=f"internal error in the {skill} skill (not retried): {reason}"); return
                sig = (a, o, world.arrived, re.sub(r"[-\d.]+", "#", str(reason))[:160])
                seen_fail[sig] = seen_fail.get(sig, 0) + 1
                if seen_fail[sig] > 2:
                    yield finish("failed", msg=f"{a} {o} failed again the same way: {reason}"); return

                # ── skill-level retry in place / ask the person (not planning) ──
                extra = ""
                if a == "Find":
                    world.found = None
                    if world.arrived and world.arrived not in searched and "out of reach" not in reason:
                        searched.append(world.arrived)
                    find_fails[o] = find_fails.get(o, 0) + 1
                    if find_fails[o] > self.cfg["search_max"] and self.cfg["ask_help"] and ("where", o) not in helped:
                        helped.add(("where", o))
                        q = f"I couldn't find the {o}. Where is it?"
                        yield ev("clarify", question=q, round=0)
                        ans = self._ask(q, node, log_fn_for(i), lang)
                        k = places.ground(ans, ans) if ans else None
                        if not k:
                            yield finish("failed", msg=f"could not find the {o} (looked at {', '.join(searched)})"); return
                        memory.remember_answer(task, q, ans); memory.see(o, k, "user said")
                        if k in searched:
                            searched.remove(k)
                        extra = f"; the user says the {o} is on {k}"
                    elif find_fails[o] > self.cfg["search_max"] + 1:
                        yield finish("failed", msg=f"could not find the {o} (looked at {', '.join(searched)})"); return
                    else:
                        extra = (f"; it is not at {world.arrived}. Choose where to look next among the places not yet "
                                 f"searched" + (f" (it was seen near {seen_far[-1]})" if seen_far else ""))
                elif a == "Pick":
                    try:
                        in_hand = bool(self.sr.execute("grasp_succeed", {}, node).get("isdone"))
                    except Exception:
                        in_hand = False
                    if in_hand:
                        # failed, yet something is in the gripper (a neighbour?): never carry on with an unknown object
                        world.holding = f"unknown object (picking {o})"
                        yield finish("failed", msg=f"the pick of the {o} failed but something is in my gripper; "
                                                   f"please take it from me: {reason}"); return
                    pick_tries[o] = pick_tries.get(o, 0) + 1
                    p3 = seen_at.get(o)
                    out_of_reach = bool(p3) and abs(p3[1]) > self.cfg["reach_lateral"]
                    if pick_tries[o] <= self.cfg["pick_retries"] and not out_of_reach:
                        # retry the same skill pair in place: look again, grasp again
                        steps[i:i] = [{"action": "Find", "object": o}]
                        world.found = None
                        yield ev("recover", at_index=i, kind="retry", msg=f"pick {o} failed; looking again")
                        continue
                    if self.cfg["ask_help"] and ("pick", o) not in helped:
                        helped.add(("pick", o))
                        q = (f"The {o} is too far for my arm. Could you move it closer to the edge, then say yes?"
                             if out_of_reach else f"I can't pick up the {o}. Could you move it closer to the edge, then say yes?")
                        yield ev("clarify", question=q, round=0)
                        ans = self._ask(q, node, log_fn_for(i), lang)
                        k = places.ground(ans, ans) if ans else None
                        if ans and self.yes_no(ans)[0] is True:
                            steps[i:i] = [{"action": "Find", "object": o}]
                            world.found = None
                            yield ev("recover", at_index=i, kind="asked", msg="user helped; trying again")
                            continue
                        if k and k != world.arrived:
                            memory.see(o, k, "user said"); world.found = None
                            extra = f"; the user says the {o} is on {k}"
                    if not extra:
                        yield finish("failed", msg=f"could not pick up the {o}: {reason}"); return
                elif a == "MoveTo":
                    move_tries[o] = move_tries.get(o, 0) + 1
                    if move_tries[o] <= 1:
                        yield ev("recover", at_index=i, kind="retry", msg=f"move to {o} again"); continue
                    if self.cfg["ask_help"] and ("move", o) not in helped:
                        helped.add(("move", o))
                        q = (f"I can't get to the {o.replace('@', ' in the ')}. Is something in the way? "
                             f"Please clear the way and say yes.")
                        yield ev("clarify", question=q, round=0)
                        ans, _n = self._ask_heard(q, node, log_fn_for(i), lang)
                        if self.yes_no(ans)[0] is True:
                            yield ev("recover", at_index=i, kind="asked", msg="user cleared the way; trying again")
                            continue
                    yield finish("failed", msg=f"could not get to {o}: {reason}"); return
                elif a in ("Place", "PutIn"):
                    try:
                        held = bool(self.sr.execute("grasp_succeed", {}, node).get("isdone"))
                    except Exception:
                        held = True
                    if not held:
                        if world.holding:
                            memory.see(world.holding, None, "dropped")
                        world.holding = world.holding_since = None
                        yield finish("failed", msg=f"place failed and the object is no longer in the hand: {reason}"); return

                # ── a new plan: SAGE suffix repair ──
                e_ = sage_replan(st, f"{reason}{extra}")
                if e_ is None:
                    yield finish("failed", msg=f"{a} {o} failed: {reason} (replan budget used)"); return
                yield e_

            # ── 4. done only when the goals hold ──────────────────────────
            if self._goals_hold(goals, world, placed):
                try:
                    rec_fn = getattr(planner, "record_episode", None)
                    if callable(rec_fn):
                        rec_fn(restated, completed)
                except Exception:
                    pass
                yield finish("success")
            else:
                yield finish("failed", msg="plan finished but the goals do not hold: "
                             + json.dumps(goals, ensure_ascii=False))
        except Exception as e:
            run.status = "aborted"; run.ended_at = time.time()
            yield {"event": "error", "msg": str(e), "trace": traceback.format_exc()}
        finally:
            if muted:
                try:
                    from robot_agent.utils import set_skill_tts_muted
                    set_skill_tts_muted(False)
                except Exception:
                    pass
            try:
                logger.snapshot()
            except Exception:
                pass
