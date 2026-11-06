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

from . import langdata, lexicon
from .understand import _norm, place_mentions

_EN_INTO = re.compile(r"\b(?:in|into|inside)\s+(?:the\s+|my\s+|your\s+|a\s+|our\s+|her\s+|his\s+)?(?:[\w'-]+\s+){0,2}$")
_EN_ONTOP = re.compile(r"\bon\s+top\s+of\s+(?:the\s+|my\s+|your\s+)?(?:[\w'-]+\s+){0,2}$|\bonto\s+(?:the\s+)?(?:[\w'-]+\s+){0,2}$")
_KO_INTO = re.compile(r"^\s*(?:의\s*)?(?:안|속|안쪽)|^(?:에|에다|에다가)?\s*(?:\S+\s+){0,2}?\S*넣")
_KO_ONTOP = re.compile(r"^\s*(?:의\s*)?위")


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
        if _EN_INTO.search(before) or _KO_INTO.search(after):
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


_OPEN = re.compile(r"\bopen\b|열어|열어\s*줘", re.I)
_CLOSE = re.compile(r"\b(?:close|shut)\b|닫아", re.I)


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


def drop_echo_say(goals: list, task: str, places) -> tuple:
    """(goals, dropped): option ``understand_give_kind``. A ``say`` goal addressed to the SPEAKER (a speaker word, or the
    speaker's ENV entry) whose text is the request itself (``_is_request``: high overlap with the instruction, a tell /
    ask verb) is the model reading the request back at the person, not a message to pass on: it is dropped instead of
    asking "What should I say?". A message to anyone else, or to the speaker with words of its own, is kept. ENV flags and
    the language data only."""
    from .persons import speaker_key
    from . import goals as goal_kinds
    me = speaker_key(places)
    out, dropped = [], []
    for g in goals:
        to = g.get("to") or g.get("person") or ""
        if g.get("type") == "say" and to and (goal_kinds.is_speaker(to) or (me is not None and to == me)) \
                and _is_request(g.get("object"), task, []):
            dropped.append(f"say to the speaker dropped: it repeats the request ({str(g.get('object') or '')[:40]})")
            continue
        out.append(g)
    return (out, dropped) if dropped else (goals, [])


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


OPEN_WORDS = lexicon.OPEN_WORDS     # words that ask to OPEN something: language-pack data (+ a robot pack's i18n ``open_words``)


def open_words_for(lang, robot=None) -> list:
    """The regexes that say "open" in run language ``lang``, or None when there is no data for it. ``lang`` None: every
    language with core data plus the pack's words. A language with core data uses all of it (a mixed utterance); any
    other language only has what the pack declares (i18n ``open_words``, read for that language)."""
    if lang is None:
        return lexicon.patterns(OPEN_WORDS, robot, "open_words")
    lg = str(lang or "")[:2].lower()
    if lg in OPEN_WORDS:
        return lexicon.patterns(OPEN_WORDS, robot, "open_words")
    out = lexicon.patterns(OPEN_WORDS, robot, "open_words", langs=(lg,))
    return out or None


def unrequested_open(goals: list, text: str, places, robot=None, lang=None, english: str = "") -> list:
    """The ``open`` goals the person's words carry no open word for ([] when the check cannot say: no Open skill, no other
    goal, no open-word data for ``lang``). ``english`` is a trusted English reading, checked with the English words only
    when ``lang`` has no data. Never a guess."""
    acts = getattr(robot, "plan_actions", None)
    if acts is not None and "Open" not in acts:
        return []
    if not any(g.get("type") != "open" for g in goals) or not any(g.get("type") == "open" for g in goals):
        return []
    pats = open_words_for(lang, robot)
    if pats is None:
        eng = str(english or "")
        pivot = langdata.PROMPT_LANG
        if not eng or eng == text or not eng.isascii() or pivot not in OPEN_WORDS:
            return []
        pats, text = lexicon.patterns(OPEN_WORDS, robot, "open_words", langs=(pivot,)), eng
    t = " ".join(str(text or "").lower().split())
    if any(re.search(p, t) for p in pats):
        return []
    return [g for g in goals if g.get("type") == "open"]


def kind_anchor_open(goals: list, text: str, places, robot=None, lang=None, english: str = "") -> tuple:
    """(goals, dropped): option ``kind_anchor``. An ``open`` goal the person's words carry no open word for is an
    end state nobody asked for (the plan gate still opens a container as a STEP when a put-in needs it). Dropped
    only when another goal remains, and only for a robot with an Open skill (``plan_actions``).

    ``lang`` is the run language. With no open-word data for it (no language pack words, no pack ``open_words`` for
    it) the words cannot show that nobody asked, so nothing is dropped, unless ``english`` is a trusted English reading
    that differs from ``text``: then that reading is checked with the English words. Never a guess."""
    gone = unrequested_open(goals, text, places, robot, lang, english)
    return ([g for g in goals if not any(g is x for x in gone)], gone) if gone else (goals, [])


def _plain(s, lang: str = langdata.PROMPT_LANG) -> str:
    arts = langdata.phrases(lang, "articles")
    out = _norm(s)
    if arts:
        out = re.sub(r"^(?:" + "|".join(re.escape(a) for a in arts) + r")\s+", "", out)
    return out.replace("-", " ").strip()


def _singular(w: str, lang: str = langdata.PROMPT_LANG) -> str:
    return _fold(w, _data_rules(lang))


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


def _data_rules(lang) -> list:
    """The singular rules of ``lang``'s language pack (``plural_rules``: [[suffix, replacement], ...])."""
    return [(str(r[0]), str(r[1])) for r in (langdata.value(lang, "plural_rules", []) or []) if isinstance(r, list) and len(r) == 2 and r[0]]


def fold_rules(robot, lang: str) -> list:
    """[(suffix, replacement)] that fold a plural onto a singular label of `lang`: the language pack's ``plural_rules`` when
    it has them, else the robot pack's i18n key ``plural_fold`` (``"ies>y|ches>ch|s>"``). None declared: no folding."""
    core = _data_rules(lang)
    if core:
        return core
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


def alias_groups(robot, lang: str) -> list:
    """[[phrase, alias, ...]] the pack declares for objects of `lang` that are the same thing under two names: its i18n
    key ``object_aliases`` (``{lang: ["mug|coffee mug", ...]}``, the first phrase is the canonical one). None declared:
    no aliases."""
    try:
        by = (getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get("object_aliases") or {}
    except Exception:
        return []
    return [[_norm(x) for x in str(g).split("|") if _norm(x)] for g in (by.get(lang) or [])
            if len([x for x in str(g).split("|") if _norm(x)]) > 1]


def _alias_form(name: str, groups: list) -> str:
    """`name` with every alias phrase of a group said as the group's canonical phrase (word boundaries):
    "blue coffee mug" -> "blue mug" with the group ``mug|coffee mug``."""
    n = _norm(name)
    for g in groups:
        for al in sorted(g[1:], key=len, reverse=True):
            n = re.sub(rf"(?<!\w){re.escape(al)}(?!\w)", g[0], n)
    return " ".join(n.split())


def instances(names: list, memory_objects: dict, groups=None) -> list:
    """[[name, ...]] the remembered instances behind `names`: names that refer to the same one are collapsed. Two names
    are the same instance when their memory records carry the same instance id (``instance`` / ``id`` / ``sighting``
    of the record: one sighting of one thing), or when they are equal after the pack's object aliases are said as
    their canonical phrase. Each group lists its names shortest first; the groups keep the order of `names`."""
    groups = groups or []
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    ident, form = {}, {}
    for n in names:
        rec = (memory_objects or {}).get(n) or {}
        i = next((rec[k] for k in ("instance", "id", "sighting") if rec.get(k) not in (None, "")), None)
        for key, table in ((("id", str(i)) if i is not None else None, ident), (_alias_form(n, groups), form)):
            if key is None:
                continue
            if key in table:
                parent[find(n)] = find(table[key])
            else:
                table[key] = n
    out: dict = {}
    for n in names:
        out.setdefault(find(n), []).append(n)
    return [sorted(g, key=lambda n: (len(n), n)) for g in out.values()]


def canon_inventory(goals: list, memory_objects: dict, places, lang: str = "en", rules=None, english: str = "",
                    aliases=None) -> tuple:
    """(goals, which, notes): option ``inventory_ask``, ``name_canon`` made general. The model's object name of a move
    goal that is not in the robot's object memory, a place or a person is matched with the memory's names (equal after
    plural folding, or the same head word). Instances are counted, not names: names of one remembered instance (the
    same record id, or related by the pack's ``aliases``, see :func:`instances`) are ONE candidate, listed by its
    shortest name. ONE instance renames the goal (the original stays in ``said``); several are narrowed first by the
    user's own words (``english``: their English reading, when there is one; a remembered name's extra words must all
    be said), and when more than one still fits the person is asked once (``which`` = (said, [one name per
    instance])), wherever the instances are. ``lang`` is the language of the core's object labels (pack data
    ``planner.canonical_lang``): ``en`` keeps Latin-script names only; ``none`` skips the rule; another code uses the
    pack's ``plural_fold`` rules (none: exact or head-word match). Robot-side memory only."""
    if lang in ("none", "off"):
        return goals, None, []
    rules = fold_rules(None, lang) if rules is None else rules
    names = [n for n in (memory_objects or {}) if n and (lang != "en" or str(n).isascii())]
    if not names:
        return goals, None, []
    sing = lambda w: _fold(w, rules)                                                    # noqa: E731
    said = {sing(t) for t in _norm(english).split()}
    used = {_plain(g.get("object"), lang) for g in goals if _plain(g.get("object"), lang) in names}
    out, notes, which = [], [], None
    for g in goals:
        o = _plain(g.get("object"), lang)
        if g.get("type") not in ("on", "in") or not o or o in names or places.env.get(o) or places.candidates(o, ""):
            out.append(g)
            continue
        so, head = sing(o), sing(o.split()[-1])
        cand = [n for n in names if n not in used and (
            sing(n) == so or sing(n.split()[-1]) == so or (len(o.split()) > 1 and sing(n) == head))]
        inst = instances(cand, memory_objects, aliases)                                 # one entry per instance
        if len(inst) > 1 and said:                                                      # the user's words first
            def fits(grp):
                return any({sing(t) for t in n.split()} - {so} <= said and {sing(t) for t in n.split()} - {so} for n in grp)
            fit = [grp for grp in inst if fits(grp)]
            inst = fit if len(fit) == 1 else inst
        if len(inst) == 1:
            best = inst[0][0]
            notes.append(f"object {g.get('object')} -> {best}: the robot's memory names it so")
            g = {**g, "object": best, "said": g.get("said") or g.get("object")}
        elif len(inst) > 1 and which is None:
            which = (g.get("said") or g.get("object"), sorted((grp[0] for grp in inst), key=lambda n: (len(n), n)))
        out.append(g)
    return out, which, notes


# ── abstention audit (option reask_abstain, default OFF) ─────────────────────────────────────────────────────────
# the model's own English question or reason asks where an OBJECT is now (a search can answer that), not where to PUT it
def _rx(key: str):
    """The model-question pattern ``key`` of the prompt language's pack, compiled (None when the pack has none)."""
    pats = langdata.words(langdata.PROMPT_LANG, key)
    return re.compile("|".join(pats), re.I) if pats else None


def abstains_on_location(u: dict):
    """None, or the objects (maybe empty) the model's question named: ``u`` has no goals, a status of ambiguous /
    infeasible, and its own question or reason asks where an object is now rather than where to put it."""
    if not isinstance(u, dict) or u.get("goals") or u.get("status") not in ("ambiguous", "infeasible"):
        return None
    q, why = str(u.get("question") or ""), str(u.get("reason") or "")
    now, put, obj = _rx("where_now"), _rx("where_put"), _rx("where_obj")
    if now is None or obj is None:                    # no data for the prompt language: the check does nothing
        return None
    if not now.search(q + " " + why) or (put is not None and put.search(q)):
        return None
    return [_plain(m.group(1)) for m in obj.finditer(q + " " + why) if _plain(m.group(1))]


def anchored(goals: list, text: str, places) -> bool:
    """Is every PLACE the move goals name in the person's own words? A destination or source that is a place (not a person,
    not the speaker, not empty) must be an ENV key one of the words' place mentions can mean (the ENV key head or one of its
    aliases, matched as ``understand.place_mentions`` matches them: whole words, particles, substring for scripts without
    spaces); a name that grounds on no place is not anchored. Nothing to check (no place in the goals): True. A places set
    without names the words can match: no place is anchored, so the caller keeps today's question (fails safe)."""
    from .persons import is_speaker_word
    said = set().union(*(m[3] for m in place_mentions(text, places))) if text else set()
    for g in goals:
        if g.get("type") not in ("on", "in"):
            continue
        for slot in ("from", "to"):
            v = str(g.get(slot) or "").strip()
            if not v:
                continue
            key = v if v in places.env else places.ground(v, text)
            if key is None:
                if is_speaker_word(v):
                    continue
                return False
            e = places.env.get(key) or {}
            if e.get("person") or e.get("speaker"):
                continue
            if key not in said:
                return False
    return True


def covers_objects(goals: list, objects: list) -> bool:
    """Do the move goals' objects cover every named object (same singular, or the same head noun)? Vacuous without names."""
    have = [_plain(g.get("object")) for g in goals if g.get("type") in ("on", "in")]
    key = lambda w: (_singular(w), _singular(w.split()[-1]) if w else "")      # noqa: E731
    return all(any(key(o)[0] == key(h)[0] or key(o)[1] == key(h)[1] for h in have) for o in objects)


__all__ = ["abstains_on_location", "anchored", "covers_objects", "shared_listener", "go_steps", "kind_anchor_open", "canon_names", "canon_inventory", "alias_groups", "instances", "fold_rules", "OPEN_WORDS", "says_into", "kind_from_destination", "drop_empty", "drop_invented", "relay_check", "ko_object_issues",
           "repair_destination", "unsaid_speaker"]
