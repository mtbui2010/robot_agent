"""Places, Memory, words: the live ENV as a closed vocabulary of places, the object / answer memory, and the name words.

What: ``words`` normalises a name ('TennisBall' -> 'tennis ball'); ``Places`` grounds what a person said on the ENV keys
(``candidates``, ``ground``, ``said``, ``room_said``); ``Memory`` keeps where an object was last seen and the last
clarification answers (``object_memory.<host>.json``).
Why: moved verbatim out of ``assist_loop.py`` (core generalization, step S03) so the loop keeps only its procedure;
``assist_loop`` re-exports ``Places``, ``Memory``, ``words`` and ``_strip_article``, so every importer is unchanged.
Proof: ``tests/test_places_diff.py`` compares this module with a verbatim reference copy of the old code.
Switch off: nothing to switch; it is the same code in a new file.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from . import langdata


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


strip_article = _strip_article      # the public name assist_loop imports (the lint forbids importing a _private name)


class Places:
    """The live ENV as a closed vocabulary of places. ``openable(key, entry)``: the robot's rule
    (``RobotView.openable``); default, the ENV entry's ``openable`` flag."""

    def __init__(self, env: dict, openable=None):
        self.env = {k: v for k, v in (env or {}).items() if isinstance(v, dict)}
        self.keys = list(self.env)
        self._openable = openable
        self.ext = None            # option dialog_agree2: the language packs' word endings, correction markers, room names

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
        if w and self.ext is not None and self.ext.rooms:
            w = " ".join(self.ext.rewrite_rooms(w).split())            # a room named in the person's language
        if not w:
            return []
        try:
            from robot_agent.env_names import alias_beats_head, resolve_env_name
            k = resolve_env_name(w, self.env)
        except Exception:
            k = None
        if k in self.env:
            from robot_agent.env_names import normalize
            if w == k.lower() or "@" in w or w in {normalize(x) for x in self.env[k].get("aliases") or []}:
                return [k]
        # 'dining table', 'kitchen counter', 'shelf in the restroom'
        room = next((r for r in self.rooms() if words(r) in w), "")      # "tool crib" says the room tool_crib
        rest = (w.replace(words(room), " ") if room else w).replace(" in the ", " ").replace(" in ", " ").replace(" at ", " ")
        # a whole answer ("It is on the kitchen counter.") grounded to the room: the filler words and the full
        # stop kept 'counter' from matching (sim 2026-10-23)
        rest = re.sub(r"[.,!?;:]", " ", rest)
        rest = " ".join(t for t in rest.split() if t not in ("the", "of", "on", "in", "at", "kitchen's", "it", "is", "it's",
                                                             "its", "there", "a", "an"))
        phrase = lambda h: bool(h) and re.search(r"(?<!\w)" + re.escape(h) + r"(?!\w)", rest)      # noqa: E731
        hd = lambda k: words(self.head(k))                                                     # noqa: E731
        hits = [k for k in self.keys if rest and (self.head(k) == rest or rest in self.head(k)
                                                     or phrase(hd(k)))]
        # the longest name said wins: "in the wine fridge, please" is not the fridge (fixer rerun of m5)
        said_heads = [hd(k) for k in hits if phrase(hd(k))]
        hits = [k for k in hits if not any(h != hd(k) and phrase(hd(k))
                                           and re.search(r"(?<!\w)" + re.escape(hd(k)) + r"(?!\w)", h)
                                           for h in said_heads)]
        hits = alias_beats_head(self.env, w, hits, hd) or hits           # 'bathroom shelf' is the restroom's, not the kitchen's 'shelf'
        if not hits and rest:                         # 'table' -> 'dining table', 'test table'
            hits = [k for k in self.keys if any(t == x for t in rest.split() for x in self.head(k).split())]
        hits = hits or self._alias_hits(w)
        if room:
            in_room = [k for k in hits if self.room(k) == room]
            if in_room:
                hits = in_room
            elif not rest or not hits:
                return ["@" + room]
        if not hits and (r0 := next((r for r in self.rooms() if words(r) == w), "")):
            return ["@" + r0]
        if len(hits) > 1 and hint:
            # the user's own words name one of them: an alias (화장실 선반) or the room
            ht = words(hint)
            by_alias = [k for k in hits if any(words(a) in ht for a in self.env[k].get("aliases") or [] if a)]
            if len(by_alias) == 1:
                return by_alias
            # the room said with this very place ('shelf in the restroom', 'kitchen shelf'), not any room the
            # task mentions elsewhere ('... the kitchen drawer')
            near = [k for k in hits if self.room_said(k, ht)]
            if len(near) == 1:
                return near
            hinted = [k for k in hits if self.room(k) and self.room(k) in hint]
            if len(hinted) == 1:
                return hinted
        return hits

    def _alias_hits(self, w: str) -> list:
        """The keys an alias of which the words ``w`` say inside a sentence ("식탁 위에 있어요": a Hangul alias
        takes particles, so it counts as a substring there; other scripts as a whole word). The longest alias said
        wins; [] when the sentence names none."""
        found = []
        for k in self.keys:
            for al in self.env[k].get("aliases") or []:
                n = words(al)
                if len(n) < 2 or n == w:
                    continue
                hit = (n in w) if langdata.attached_script(n) else bool(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", w))
                if hit:
                    found.append((len(n), k))
        if not found:
            return []
        top = max(n for n, _k in found)
        return list(dict.fromkeys(k for n, k in found if n == top))

    def room_said(self, key: str, text: str) -> bool:
        """Does *text* name *key* with its room ('shelf in the restroom', 'restroom shelf', "kitchen's shelf")?"""
        h, rm = re.escape(words(self.head(key))), re.escape(words(self.room(key)))
        return bool(rm) and bool(re.search(rf"\b{h}\s+(?:in|of|at|from)\s+(?:the\s+)?{rm}\b|\b{rm}(?:'s)?\s+{h}\b",
                                           words(text)))

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

    def __init__(self, path: Path, clock=None):
        self.path, self.clock = Path(path), clock       # clock: the host's (AssistLoop._now), None: wall time
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
        self.data["objects"][words(obj)] = {"place": place, "ts": (self.clock or time.time)(), "how": how}
        self.save()

    def remember_answer(self, task: str, question: str, answer: str):
        if answer:
            self.data["answers"] = (self.data["answers"] + [{"task": task, "q": question, "a": answer,
                                                              "ts": (self.clock or time.time)()}])[-30:]
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
