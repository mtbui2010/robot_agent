"""Option ``ask_focus`` (default off): the robot asks only questions that are needed, about what the world leaves open, and a bounded number of times.

Four rules on the read-back (``understand.find_issues`` and the confirmation rounds of the loop):

* no redundant confirmation: an object the words name that the goals do not list, but that is the name of a place the robot knows or a word of
  a goal's other fields (a tool, a surface the plan reaches by other words), is no open issue; the question is not asked;
* ambiguity from the world: a goal's object that fits two or more objects of the world (the robot's object memory and the manifest's
  ``limits.objects``; none of them named exactly like the goal) is asked about, "which one?", whatever the reader's label said;
* "I don't know" is no refusal: to "Is that right?" it leaves the reading standing (the person did not refuse it, and has nothing better), it does
  not start the correction path; the phrases are the pack's and the language's (``dialog_unstick.unsure_answer``): no data, nothing is claimed;
* a limit on the read-back rounds: key ``max_confirms`` (default 3: the engine's own two, and one more after a correction, stay as they are).

No pack data: the world is what the robot already holds. Hook ``on_issues`` (transform); services ``confirm_limit``, ``unrefused``.
Keys: ``enabled``, ``max_confirms``. Lab flag ``ask_focus``: ``{rule, ...}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import Key, Option, OptionSpec, register

_ARTICLES = ("the ", "a ", "an ")
SPEC = OptionSpec(
    name="ask_focus",
    keys=(Key("enabled", "bool", False, aliases=("ask_focus",)), Key("max_confirms", "int", 3)),
    hooks=("on_issues",),
    order=80,
    enabled="enabled",
    services=("confirm_limit", "unrefused"),
    doc="Purposeful questions: no redundant confirmation, ambiguity read from the world, an \"I don't know\" is no refusal, a bounded number of read-back rounds.",
)


def _w(s) -> str:
    t = " ".join(re.findall(r"[\w']+", str(s or "").lower()))
    return next((t[len(a):] for a in _ARTICLES if t.startswith(a)), t)


def _in(small: str, big: str) -> bool:
    """The words of ``small`` are, in order, whole words of ``big``."""
    return bool(small) and re.search(r"(?<!\w)" + re.escape(small) + r"(?!\w)", big) is not None


def world_names(run, robot) -> list:
    """The objects of the world: the robot's object memory and the manifest's ``limits.objects`` (normalised, no duplicates)."""
    mem = getattr(getattr(run, "memory", None), "data", None) or {}
    lim = getattr(getattr(robot, "manifest", None), "limits", None) or {}
    return list(dict.fromkeys(n for n in map(_w, list(mem.get("objects") or {}) + list(lim.get("objects") or {})) if n))


def place_names(places) -> set:
    """Every head and alias of an ENV place, as whole-word strings."""
    out = set()
    for k, v in (getattr(places, "env", None) or {}).items():
        out.add(_w(places.head(k)))
        out.update(_w(a) for a in (v.get("aliases") or []) if a)
    return {n for n in out if n}


def field_words(goals) -> list:
    """The text of every field of the goals but their object (the places, the tool, whatever the plan reaches by other words)."""
    out = []
    for g in goals:
        for k, v in g.items():
            vals = v if isinstance(v, (list, tuple)) else [v]
            out += [_w(str(x).replace("@", " ")) for x in vals if isinstance(x, str) and k != "object"]
    return [x for x in out if x]


def redundant(issue, places, goals) -> bool:
    """An object issue whose name is a place the robot knows, or a part of what a goal's other fields say."""
    if issue.get("kind") != "object":
        return False
    name = _w(issue.get("what"))
    return bool(name) and (name in place_names(places) or any(_in(name, f) for f in field_words(goals)))


def fits(desc: str, names) -> list:
    """The world's names the description fits (all its words, whole): [] when one of them IS the description (it is named exactly)."""
    hit = [n for n in names if _in(desc, n)]
    return [] if desc in hit else hit


class AskFocus(Option):
    spec = SPEC

    def _asked(self, run) -> set:
        return run.__dict__.setdefault("opt", {}).setdefault("ask_focus", {}).setdefault("asked", set())

    def on_issues(self, issues, run, goals):
        keep = [i for i in issues if not redundant(i, run.places, goals)]
        if len(keep) != len(issues):
            self.svc.lab("ask_focus", rule="redundant", dropped=len(issues) - len(keep))
        names = world_names(run, self.svc.robot)
        for g in goals:
            desc = _w(g.get("object")) if g.get("type") in ("on", "in") else ""
            hit = fits(desc, names) if desc else []
            if len(hit) >= 2 and not any(i.get("kind") == "choice" and i.get("what") == desc for i in keep):
                keep.append({"kind": "choice", "what": desc, "options": hit[:4]})
                self.svc.lab("ask_focus", rule="world_ambiguity", name=desc, n=len(hit))
        return keep

    def confirm_limit(self, run):
        return max(1, int(self.cfg.get("max_confirms")))

    def unrefused(self, run, ans):
        """True when the answer to a read-back is an "I don't know": the reading stands."""
        unsure = getattr(self.svc, "is_unsure", None)
        if not (unsure and ans and unsure(run, ans)):
            return None
        self.svc.lab("ask_focus", rule="unsure_is_no_refusal")
        return True


register(SPEC, AskFocus)
