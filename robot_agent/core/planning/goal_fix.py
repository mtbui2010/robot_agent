"""Deterministic fixes of grounded goals from the person's words and the ENV (SAGE-v2, no LLM call).

* F1.1 ``kind_from_destination``: the goal kind follows its destination and the words. An ``on`` goal to an
  openable container that the words put the object INTO ("put the milk in the fridge", 냉장고에 넣어 주세요)
  becomes ``in``; an ``in`` goal to a place that is not a container becomes ``on``. "on top of" / 위에 keeps
  ``on``; "put it away on the shelf" stays ``on`` ("away" is not an into-word, lexicon.PUTIN).
* F3.4 ``drop_empty``: a move goal the model gave no object is dropped (it made "Where should I put the ?").
* F4.7 ``repair_destination``: a move goal to a place the words never name, when the words name exactly one
  other place no goal uses, goes to that place.
* F2.5 ``unsaid_speaker``: a move goal to the speaker with no recipient word gets an empty destination (ask).
* F4.4 ``drop_invented``: an ``on`` / ``in`` goal whose OBJECT is an ENV place (an appliance is not carried) is
  dropped, or becomes the open / close goal the words ask for; a ``go`` goal to a place the words never name is
  dropped when other goals remain.

Robot-agnostic: places are the ENV keys (``assist_loop.Places``), the words lists are lexicon.py's.
"""

from __future__ import annotations

import re

from . import lexicon
from .understand import _norm, place_mentions

_EN_INTO = re.compile(r"\b(?:in|into|inside)\s+(?:the\s+|my\s+|your\s+|a\s+|our\s+|her\s+|his\s+)?(?:[\w'-]+\s+){0,2}$")
_EN_ONTOP = re.compile(r"\bon\s+top\s+of\s+(?:the\s+|my\s+|your\s+)?(?:[\w'-]+\s+){0,2}$|\bonto\s+(?:the\s+)?(?:[\w'-]+\s+){0,2}$")
_KO_INTO = re.compile(r"^\s*(?:의\s*)?(?:안|속|안쪽)|^(?:에|에다|에다가)?\s*(?:\S+\s+){0,2}?\S*넣")
_KO_ONTOP = re.compile(r"^\s*(?:의\s*)?위")
_VI_INTO = re.compile(r"\b(?:vào|trong)\s+(?:\S+\s+){0,2}$")


def _mentions_of(key: str, text: str, places) -> list:
    t = _norm(text)
    return [(s, e) for s, e, _p, keys, _src in place_mentions(t, places) if key in keys], t


def says_into(key: str, text: str, places) -> bool:
    """Do the words put something INTO ``key`` (and not on top of it)?"""
    spans, t = _mentions_of(key, text, places)
    for s, e in spans:
        before, after = t[max(0, s - 32):s], t[e:e + 16]
        if _EN_ONTOP.search(before) or _KO_ONTOP.search(after):
            continue
        if _EN_INTO.search(before) or _VI_INTO.search(before) or _KO_INTO.search(after):
            return True
    return False


_EN_ON = re.compile(r"\b(?:on|onto|atop)\s+(?:top\s+of\s+)?(?:the\s+|my\s+|your\s+|a\s+|our\s+)?(?:[\w'-]+\s+){0,2}$")


def says_on(key: str, text: str, places) -> bool:
    """Do the words put something ON ``key`` ("on the fridge", "on top of", 위에)?"""
    spans, t = _mentions_of(key, text, places)
    return any(_EN_ON.search(t[max(0, s - 32):s]) or _KO_ONTOP.search(t[e:e + 16]) for s, e in spans)


def kind_from_destination(goals: list, text: str, places, can_putin: bool = True) -> tuple:
    """(goals, notes): F1.1 (see the module doc). An ``on`` goal to an openable container the words do not put
    ON ("The fridge." as an answer, no preposition) becomes ``in`` too (F1.1b): a container is filled."""
    out, notes = [], []
    for g in goals:
        g = dict(g)
        to = g.get("to")
        if g.get("type") == "on" and to in places.env and places.openable(to) and can_putin \
                and (says_into(to, text, places) or not says_on(to, text, places)):
            g["type"] = "in"
            notes.append(f"on -> in: the words put the {g.get('object')} into {to}")
        elif g.get("type") == "in" and to in places.env and not places.openable(to) \
                and not (places.env.get(to) or {}).get("person"):
            g["type"] = "on"
            notes.append(f"in -> on: {to} is not a container")
        out.append(g)
    return out, notes


def drop_empty(goals: list) -> tuple:
    """(goals, dropped): F3.4."""
    keep = [g for g in goals if g.get("type") not in ("on", "in") or str(g.get("object") or "").strip()]
    return keep, [g for g in goals if g not in keep]


_OPEN = re.compile(r"\bopen\b|열어|열어\s*줘|mở", re.I)
_CLOSE = re.compile(r"\b(?:close|shut)\b|닫아|đóng", re.I)


def drop_invented(goals: list, text: str, places) -> tuple:
    """(goals, dropped): F4.4 (see the module doc)."""
    t = _norm(text)
    said = set().union(*(m[3] for m in place_mentions(t, places))) if place_mentions(t, places) else set()
    out, dropped = [], []
    for g in goals:
        typ, obj = g.get("type"), str(g.get("object") or "")
        if typ in ("on", "in") and obj:
            k = places.ground(obj, obj)
            if k and k in places.env and not (places.env.get(k) or {}).get("person"):
                verb = "open" if _OPEN.search(t) else ("close" if _CLOSE.search(t) else None)
                if verb and not any(h.get("type") == verb and h.get("object") == k for h in goals):
                    out.append({"type": verb, "object": k, "from": "", "to": k})
                dropped.append(g)
                continue
        out.append(g)
    others = [g for g in out if g.get("type") != "go"]
    if others:
        keep = []
        for g in out:
            room = _norm(places.room(g.get("to") or ""))
            if g.get("type") == "go" and g.get("to") in places.env and g.get("to") not in said \
                    and not (room and re.search(r"(?<!\w)" + re.escape(room) + r"(?!\w)", t)):
                dropped.append(g)
                continue
            keep.append(g)
        out = keep
    return out, dropped


def unsaid_speaker(goals: list, text: str, places, robot=None) -> tuple:
    """(goals, notes): F2.5. A move goal to the speaker ("me", the speaker's ENV entry) whose words name no
    recipient (lexicon.RECIPIENT: "bring me", "here", 나한테, 가져다, ...) gets an empty destination, so the robot
    asks where to put it instead of handing it over ("put the milk away" understood as "give the milk to me")."""
    from .persons import is_speaker_word
    if lexicon.has(lexicon.RECIPIENT, text, robot, "recipient_words"):
        return goals, []
    out, notes = [], []
    for g in goals:
        to = str(g.get("to") or "")
        e = places.env.get(to) or {}
        if g.get("type") in ("on", "in") and to and (e.get("speaker") or is_speaker_word(to)
                                                     or _norm(to) in ("me", "user", "you", "myself")):
            g = {**g, "to": ""}
            notes.append(f"destination {to} dropped: the words name no recipient")
        out.append(g)
    return out, notes


def shared_listener(goals: list, text: str, places) -> tuple:
    """(goals, notes): a person the words name once cannot be both the listener of a message and the recipient of a
    delivery: "tell X to ..., and bring me the cup" names X once, the recipient of the cup is the speaker. A
    delivery goal to a person who is also a say goal's listener, with only that one mention of them in the words
    and the speaker known from the ENV, goes to the speaker (the model took the last person it had read)."""
    from .persons import speaker_key
    sk = speaker_key(places)
    if not sk:
        return goals, []
    listeners = {g.get("to") or g.get("person") for g in goals if g.get("type") == "say"} - {None, ""}
    out, notes = [], []
    for g in goals:
        to = g.get("to")
        if g.get("type") in ("on", "in") and to in listeners and to != sk and (places.env.get(to) or {}).get("person"):
            n = sum(1 for _s, _e, _p, keys, _src in place_mentions(_norm(text), places) if to in keys)
            if n < 2:
                g = {**g, "to": sk}
                g.pop("near_person", None)
                notes.append(f"recipient {to} -> {sk}: the words name {to} once, as the listener of a message")
        out.append(g)
    return out, notes


_KO_FROM = re.compile(r"(?:에서|에게서|한테서|부터|으로부터|로부터)")


def repair_destination(goals: list, text: str, places) -> tuple:
    """(goals, notes): F4.7 symbolic destination repair. The model put an object somewhere the person never
    named, while the person named exactly one place (one ENV key, not a source, not a person) that no goal
    uses: that place is the destination. Only with one such move goal (two goals and one place: which one is
    unclear, the read-back asks). Example: "put the towel in the cabinet" understood as "in the drawer"."""
    from .understand import _active, _drop_corrected
    t = _active(text)
    ms = _drop_corrected(t, place_mentions(t, places))
    person = lambda k: bool((places.env.get(k) or {}).get("person"))
    said = set().union(*(m[3] for m in ms)) if ms else set()
    used = {g.get(s) for g in goals for s in ("to", "from") if g.get(s)} | \
        {g.get("object") for g in goals if g.get("type") in ("open", "close")}
    free = {next(iter(keys)) for _s, e, _p, keys, src in ms
            if len(keys) == 1 and not src and not _KO_FROM.match(t[e:]) and not person(next(iter(keys)))} - used
    movers = [k for k, g in enumerate(goals) if g.get("type") in ("on", "in") and g.get("to") in places.env
              and not person(g["to"]) and g["to"] not in said and not g.get("near_person") and not g.get("hand")]
    if len(free) != 1 or len(movers) != 1:
        return goals, []
    k, dst = movers[0], next(iter(free))
    out = [dict(g) for g in goals]
    note = f"destination {out[k]['to']} -> {dst}: the words name {dst}, not {out[k]['to']}"
    out[k]["to"] = dst
    return out, [note]


def _tokens(s: str) -> list:
    return [w for w in re.findall(r"[\w']+", _norm(s)) if len(w) > 1]


def _is_request(text: str, task: str, listener_names) -> bool:
    """Is a say goal's text the request itself ("tell dad to come ..." relayed whole), not the message?"""
    t = _norm(text)
    if not t:
        return True
    task_t = set(_tokens(task))
    toks = set(_tokens(t))
    if task_t and len(toks & task_t) >= 0.9 * len(task_t):
        return True
    for n in listener_names:
        n = _norm(n)
        if not n or n.startswith("@"):
            continue
        # the listener as the addressee of a tell ("to dad", "tell dad", 아빠한테 / 아빠에게): the request itself;
        # a vocative ("Dinner is ready, dad.") is part of a message
        if re.search(r"\b(?:to|tell|ask|remind|let)\s+(?:the\s+|my\s+|your\s+)?" + re.escape(n) + r"\b", t) or \
                re.search(r"(?<!\w)" + re.escape(n) + r"\s*(?:한테|에게|께|더러)", t):
            return True
    return lexicon.has(lexicon.TELL, t)


def relay_check(goals: list, task: str, places) -> tuple:
    """(goals, question): F4.6. A say goal whose text is the whole request (high overlap with the instruction,
    the listener's name in it, a tell / ask verb) takes the model's ``content`` (the reported message only) when
    that passes the same check, else the robot asks what to say. A priori threshold 0.9 of the instruction tokens, not tuned on any benchmark."""
    out, q = [], None
    for g in goals:
        if g.get("type") != "say":
            out.append(g)
            continue
        g = dict(g)
        who = g.get("to") or g.get("person") or ""
        names = [who] + list((places.env.get(who) or {}).get("aliases") or []) if who else []
        if _is_request(g.get("object"), task, names):
            c = str(g.get("content") or "").strip()
            if c and not _is_request(c, task, names):
                g["object"] = c
            else:
                q = "What should I say?"
        out.append(g)
    return out, q


_KO_ACC = re.compile(r"([\uac00-\ud7a3][\uac00-\ud7a3 ]*?[\uac00-\ud7a3])(?:을|를)(?=\s|$|[.,!?])")
_KO_AND = re.compile(r"\s*(?:과|와|하고|이랑|랑|,)\s*")


def ko_object_issues(goals: list, text: str) -> list:
    """F4.5: every Korean accusative noun group (을 / 를, with 과 / 와 / 하고 / 이랑 chains) must be covered by a
    goal's ``said`` (the person's own words for its object, from the same understand call). Only when the goals
    carry ``said`` (the model filled it); a miss is an ``object`` issue (re-sample, then ask)."""
    saids = [_norm(g.get("said")) for g in goals if g.get("said")]
    if not saids or not re.search(r"[\uac00-\ud7a3]", str(text or "")):
        return []
    out = []
    for m in _KO_ACC.finditer(_norm(text)):
        for part in _KO_AND.split(m.group(1)):
            noun = part.strip().split()[-1] if part.strip() else ""
            if len(noun) < 2:
                continue
            if not any(noun in s or s in noun for s in saids):
                out.append({"kind": "object", "what": noun})
    return [x for k, x in enumerate(out) if x not in out[:k]]


# ── grounding chain v2 (options go_steps, kind_anchor, name_canon; all default OFF) ──────────────────────────────
def go_steps(goals: list, task: str, places) -> tuple:
    """(goals, dropped): option ``go_steps``. A ``go`` goal that is a STEP toward another goal, not an end state, is
    dropped when at least one non-``go`` goal remains. It is a step when its target is a person, the speaker, the
    listener of a ``say`` goal, or the place the person named as the SOURCE of a move goal that comes AFTER it ("go
    there first"). A final ``go`` (a dock, a room the person asked to reach) is kept. ENV flags and goal order only."""
    from .persons import speaker_key
    if not any(g.get("type") != "go" for g in goals):
        return goals, []
    sk = speaker_key(places)
    listeners = {g.get("to") or g.get("person") for g in goals if g.get("type") == "say"} - {None, ""}

    def later_sources(i):
        out = set()
        for g in goals[i + 1:]:
            src = g.get("from") or ""
            if g.get("type") in ("on", "in") and src in places.env and places.said(src, task):
                out.add(src)
        return out

    keep, dropped = [], []
    for i, g in enumerate(goals):
        to = g.get("to")
        if g.get("type") == "go" and to and (to in listeners or (sk and to == sk) or to in later_sources(i)
                                              or (places.env.get(to) or {}).get("person")):
            dropped.append(g)
            continue
        keep.append(g)
    return keep, dropped


OPEN_WORDS = {      # words that ask to OPEN something (a robot pack extends them with its i18n key ``open_words``)
    "en": [r"\bopen"],
    # 열다 is l-irregular: the l drops before n, p, s (연다, 엽니다, 여세요, 여는); 열쇠 (key) does not count
    "ko": [r"(?<![가-힣])열(?:[어었고면려기게을은린지자까래도며나겠야렴]|\s+(?:수|거|것|때)|$)", r"(?<![가-힣])(?:여(?:세요|십시오|시|는|니|라|네)|엽니|연다)"],
    "vi": [r"\bmở\b"],
}


def kind_anchor_open(goals: list, text: str, places, robot=None) -> tuple:
    """(goals, dropped): option ``kind_anchor``. An ``open`` goal the person's words carry no open verb for is an
    end state nobody asked for (the plan gate still opens a container as a STEP when a put-in needs it). Dropped
    only when another goal remains, and only for a robot with an Open skill (``plan_actions``)."""
    acts = getattr(robot, "plan_actions", None)
    if acts is not None and "Open" not in acts:
        return goals, []
    if not any(g.get("type") != "open" for g in goals) or not any(g.get("type") == "open" for g in goals):
        return goals, []
    if lexicon.has(OPEN_WORDS, text, robot, "open_words"):
        return goals, []
    return [g for g in goals if g.get("type") != "open"], [g for g in goals if g.get("type") == "open"]


def _plain(s) -> str:
    return re.sub(r"^(?:the|a|an|some|my|your)\s+", "", _norm(s)).replace("-", " ").strip()


def _singular(w: str) -> str:
    w = w.strip()
    for suf, rep in (("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("ses", "s"), ("xes", "x"), ("s", "")):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)] + rep
    return w


def canon_names(goals: list, memory_objects: dict, places) -> tuple:
    """(goals, which, notes): option ``name_canon``. The model's English object name of a move goal that is not in
    the robot's object memory, a place or a person ("mugs", "red cup") is mapped onto a memory name (Latin script)
    that equals it, its singular, or its head noun. The candidates are grouped by remembered place: one group
    renames the object (the original stays in ``said``); several groups give ``which`` = (said, [one name per
    group]) for a which-one question. Robot-side memory only, no benchmark vocabulary."""
    names = {n: (v or {}).get("place") for n, v in (memory_objects or {}).items() if n and str(n).isascii()}
    if not names:
        return goals, None, []
    used = {_plain(g.get("object")) for g in goals if _plain(g.get("object")) in names}
    out, notes, which = [], [], None
    for g in goals:
        if g.get("type") not in ("on", "in"):
            out.append(g)
            continue
        o = _plain(g.get("object"))
        if not o or o in names or places.env.get(o) or places.candidates(o, ""):
            out.append(g)
            continue
        so, head = _singular(o), _singular(o.split()[-1])
        cand = [n for n in names if n not in used and (
            _singular(n) == so or _singular(n.split()[-1]) == so or (len(o.split()) > 1 and _singular(n) == head))]
        groups: dict = {}
        for n in cand:
            groups.setdefault(names[n], []).append(n)
        if len(groups) == 1:
            best = min(next(iter(groups.values())), key=len)
            notes.append(f"object {g.get('object')} -> {best}: the robot's memory names it so")
            g = {**g, "object": best, "said": g.get("said") or g.get("object")}
        elif len(groups) > 1 and which is None:
            which = (g.get("said") or g.get("object"), [min(v, key=len) for v in groups.values()])
        out.append(g)
    return out, which, notes


def fold_rules(robot, lang: str) -> list:
    """[(suffix, replacement)] the pack declares to fold a plural onto a singular label of `lang`: its i18n key
    ``plural_fold`` (``"ies>y|ches>ch|s>"``). English has the core default. None declared: no folding."""
    if lang == "en":
        return [("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("ses", "s"), ("xes", "x"), ("s", "")]
    try:
        phrases = (getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get("plural_fold") or {}
    except Exception:
        return []
    rules = [tuple(x.split(">", 1)) for x in str(phrases.get(lang) or "").split("|") if ">" in x]
    return [(a, b) for a, b in rules if a]


def _fold(w: str, rules: list) -> str:
    w = w.strip()
    for suf, rep in rules:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)] + rep
    return w


def canon_inventory(goals: list, memory_objects: dict, places, lang: str = "en", rules=None, english: str = "") -> tuple:
    """(goals, which, notes): option ``inventory_ask``, ``name_canon`` made general. The model's object name of a move
    goal that is not in the robot's object memory, a place or a person is matched with the memory's names (equal after
    plural folding, or the same head word). ONE match renames the goal (the original stays in ``said``); several
    distinct instances are narrowed first by the user's own words (``english``: their English reading, when there is
    one; a remembered name's extra words must all be said), and when more than one still fits the person is asked once
    (``which`` = (said, [names])), wherever the instances are. ``lang`` is the language of the core's object labels
    (pack data ``planner.canonical_lang``): ``en`` keeps Latin-script names only; ``none`` skips the rule; another code
    uses the pack's ``plural_fold`` rules (none: exact or head-word match). Robot-side memory only."""
    if lang in ("none", "off"):
        return goals, None, []
    rules = fold_rules(None, lang) if rules is None else rules
    names = [n for n in (memory_objects or {}) if n and (lang != "en" or str(n).isascii())]
    if not names:
        return goals, None, []
    sing = lambda w: _fold(w, rules)                                                    # noqa: E731
    said = {sing(t) for t in _norm(english).split()}
    used = {_plain(g.get("object")) for g in goals if _plain(g.get("object")) in names}
    out, notes, which = [], [], None
    for g in goals:
        o = _plain(g.get("object"))
        if g.get("type") not in ("on", "in") or not o or o in names or places.env.get(o) or places.candidates(o, ""):
            out.append(g)
            continue
        so, head = sing(o), sing(o.split()[-1])
        cand = [n for n in names if n not in used and (
            sing(n) == so or sing(n.split()[-1]) == so or (len(o.split()) > 1 and sing(n) == head))]
        if len(cand) > 1 and said:                                                      # the user's words first
            fit = [n for n in cand if {sing(t) for t in n.split()} - {so} <= said and {sing(t) for t in n.split()} - {so}]
            cand = fit if len(fit) == 1 else cand
        if len(cand) == 1:
            notes.append(f"object {g.get('object')} -> {cand[0]}: the robot's memory names it so")
            g = {**g, "object": cand[0], "said": g.get("said") or g.get("object")}
        elif len(cand) > 1 and which is None:
            which = (g.get("said") or g.get("object"), sorted(cand, key=lambda n: (len(n), n)))
        out.append(g)
    return out, which, notes


# ── abstention audit (option reask_abstain, default OFF) ─────────────────────────────────────────────────────────
# the model's own English question or reason asks where an OBJECT is now (a search can answer that), not where to PUT it
_WHERE_NOW = re.compile(
    r"where (?:is|are|was|were|can i find|could i find|do i find|would i find|might)\b|\blocation of\b|"
    r"\b(?:is|are) (?:located|currently)\b|\bcurrent(?:ly)? (?:location|place)\b|"
    r"(?:not|n't) (?:know|sure|specified|mentioned|clear|given|stated)\b[^.?]*\bwhere (?:the|it|they|this|that|those|these)\b|"
    r"(?:location|whereabouts)[^.?]*(?:unknown|not (?:known|specified|mentioned|given|clear|stated|provided))", re.I)
_WHERE_PUT = re.compile(r"where (?:should|shall|do you want|would you like|to|must)\b[^.?]*\b"
                        r"(?:put|place|leave|bring|take|deliver|move|go|set)", re.I)
_WHERE_OBJ = re.compile(r"where (?:is|are|was|were) (?:the |my |your |a |an |some )?([a-z][a-z -]*?)"
                        r"(?: located| kept|\?| now| currently|,| and | or )", re.I)


def abstains_on_location(u: dict):
    """None, or the objects (maybe empty) the model's question named: ``u`` has no goals, a status of ambiguous /
    infeasible, and its own question or reason asks where an object is now rather than where to put it."""
    if not isinstance(u, dict) or u.get("goals") or u.get("status") not in ("ambiguous", "infeasible"):
        return None
    q, why = str(u.get("question") or ""), str(u.get("reason") or "")
    if not _WHERE_NOW.search(q + " " + why) or _WHERE_PUT.search(q):
        return None
    return [_plain(m.group(1)) for m in _WHERE_OBJ.finditer(q + " " + why) if _plain(m.group(1))]


def covers_objects(goals: list, objects: list) -> bool:
    """Do the move goals' objects cover every named object (same singular, or the same head noun)? Vacuous without names."""
    have = [_plain(g.get("object")) for g in goals if g.get("type") in ("on", "in")]
    key = lambda w: (_singular(w), _singular(w.split()[-1]) if w else "")      # noqa: E731
    return all(any(key(o)[0] == key(h)[0] or key(o)[1] == key(h)[1] for h in have) for o in objects)


__all__ = ["abstains_on_location", "covers_objects", "shared_listener", "go_steps", "kind_anchor_open", "canon_names", "canon_inventory", "fold_rules", "OPEN_WORDS", "says_into", "kind_from_destination", "drop_empty", "drop_invented", "relay_check", "ko_object_issues",
           "repair_destination", "unsaid_speaker"]
