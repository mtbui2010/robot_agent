"""Step S09: the grounding of the goals on the ENV (_ground_container, _ground_template, _ground_goals, _ground_speaker,
_person_destination, _say_name) moved verbatim from assist_loop.py to the GroundPhase mixin in phases/ground.py. These tests
prove nothing changed: (1) the mixin is in AssistLoop's method resolution order and serves the six names, and
``AssistLoop._ground_template`` is still callable through the class; (2) the speaker words are the old constant, still
reachable as ``assist_loop._USER_WORDS``; (3) a differential test against the verbatim reference copy
(tests/legacy_ref/s09_ground.py) over a made-up flat, goals of every kind, tasks and answers: the old and the new class return
equal goals, equal questions (text, key, slots) and leave the same state (``_where_asked``, ``_person_at``); the two option
seams (query ``person_where``, transform ``on_ground``) are called with the same arguments. Made-up places only."""

import copy
import itertools

import pytest

from legacy_ref import s09_ground as old
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.phases import PHASE_MIXINS, ground
from robot_agent.core.planning.places import Memory, Places
from robot_agent.core.planning.search import SearchConfig
from robot_agent.core.planning.search_hooks import SearchHooks

NAMES = ("_ground_container", "_ground_template", "_ground_goals", "_ground_speaker", "_person_destination", "_say_name")

ENV = {
    "armchair@lounge": {"loc": {"x": 0.0, "y": 0.0}, "aliases": ["armchair", "reading chair"], "surface": False},
    "lamp table@lounge": {"loc": {"x": 0.8, "y": 0.2}, "aliases": ["lamp table"], "surface": True},
    "bunk@kids room": {"loc": {"x": 6.0, "y": 0.0}, "aliases": ["bunk"], "surface": False},
    "worktop@galley": {"loc": {"x": 3.0, "y": 3.0}, "aliases": ["worktop"], "surface": True},
    "worktop@pantry": {"loc": {"x": 4.0, "y": 3.0}, "aliases": ["worktop"], "surface": True},
    "larder@galley": {"loc": {"x": 3.5, "y": 3.0}, "aliases": ["larder"], "surface": False, "openable": True},
    "dining table@galley": {"loc": {"x": 3.2, "y": 2.0}, "aliases": ["dining table"], "surface": True},
    "coffee table@lounge": {"loc": {"x": 0.2, "y": 0.2}, "aliases": ["coffee table"], "surface": True},
    "auntie": {"loc": {"x": 0.8, "y": 0.2}, "aliases": ["auntie", "me"], "person": "p1", "speaker": True,
               "seat": "armchair@lounge", "spot": "lamp table@lounge"},
    "nephew": {"loc": {"x": 6.2, "y": 0.4}, "aliases": ["nephew"], "person": "p2", "seat": "bunk@kids room", "spot": None},
}
ENV_UNTRACKED = {k: ({kk: vv for kk, vv in v.items() if kk != "loc"} if k in ("auntie", "nephew") else v) for k, v in ENV.items()}
ENV_ONE_DRAWER = {k: v for k, v in ENV.items() if k not in ("worktop@pantry",)}
ENV_NO_SPEAKER = {k: v for k, v in ENV.items() if k != "auntie"}


class Options:
    """Records the two seams the grounding calls; answers like the empty dispatcher unless ``mode`` is set."""

    def __init__(self, mode=""):
        self.mode, self.calls = mode, []

    def query(self, name, *args, default=None):
        self.calls.append(("query", name, repr(args[1:3]), repr(args[3:])))
        return self.mode or default

    def transform(self, name, value, *args):
        self.calls.append(("transform", name, value, repr(args[1:])))
        return "hand" if self.mode == "handover" else value


class Host(SearchHooks):
    def __init__(self, mode=""):
        self.cfg = {"person_seat_m": 1.0, "person_surface_m": 1.2, "search": SearchConfig(enabled=False)}
        self.options, self.robot, self._where_asked = Options(mode), None, None


class Old(old.RefGround, Host):
    pass


class New(ground.GroundPhase, Host):
    pass


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_six_names():
    assert ground.GroundPhase in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(ground.GroundPhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(assist_loop.AssistLoop), n
        assert n in vars(ground.GroundPhase), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(ground.GroundPhase, n), n


def test_ground_template_is_callable_through_the_class():
    g, q = assist_loop.AssistLoop._ground_template({"type": "go", "object": "", "from": "", "to": "armchair"},
                                                   Places(ENV), "wait at the armchair")
    assert q is None and g["to"] == "armchair@lounge" and g["from"] == ""


# -- (2) the data -------------------------------------------------------------------------------------------------------------
def test_user_words_are_the_old_constant():
    assert ground.USER_WORDS == assist_loop._USER_WORDS == old._USER_WORDS == {"me", "user", "here", "you", "myself", "person"}


# -- (3) differential test ----------------------------------------------------------------------------------------------------
def _norm(x):
    """A result as plain data: a question keeps its text, key and slots."""
    if isinstance(x, tuple):
        return tuple(_norm(i) for i in x)
    if isinstance(x, list):
        return [_norm(i) for i in x]
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items()}
    if hasattr(x, "key") and hasattr(x, "slots"):
        return ("LocalText", str(x), x.key, _norm(x.slots), _norm(list(x.parts)), _norm(x.ask))
    return x


def G(t, o, f, to, **kw):
    return {"type": t, "object": o, "from": f, "to": to, **kw}


GOALS = [
    G("go", "", "", "kitchen"), G("go", "", "", "galley"), G("go", "", "", "worktop"), G("go", "", "", ""),
    G("go", "", "", "the lounge"), G("go", "", "", "nowhere"),
    G("say", "dinner is ready", "", "nephew"), G("say", "dinner is ready", "", "lounge"), G("say", "", "", "armchair"),
    G("say", "hello", "", ""),
    G("deliver", "cup", "", "armchair"), G("deliver", "cup", "", ""),
    G("open", "larder", "", ""), G("open", "drawer", "", "larder"), G("open", "thing", "", "none"), G("close", "larder", "", ""),
    G("on", "cup", "worktop@galley", "lamp table"), G("on", "cup", "", "armchair"), G("on", "cup", "worktop", "armchair"),
    G("on", "cup", "", "me"), G("on", "cup", "", "here"), G("on", "cup", "", "nephew"), G("on", "cup", "armchair", "auntie"),
    G("on", "cup", "", ""), G("on", "cup", "lounge", "kids room"), G("on", "cup", "worktop@galley", "worktop@galley"),
    G("on", "cup", "", "worktop"), G("on", "cup", "bunk", "lamp table", said="mug"),
    G("in", "pen", "", "larder"), G("in", "pen", "", "armchair"), G("in", "pen", "lounge", "larder"),
    G("in", "pen", "worktop", "larder@galley"),
    {"type": "on", "object": "cup", "from": "", "to": "me", "hand": True},
    G("go", "", "", "table"), G("go", "", "", "the galley"), G("say", "hi", "", "table"), G("deliver", "cup", "", "table"),
    G("on", "cup", "", "table"), G("on", "cup", "table", "armchair"), G("on", "cup", "armchair", "galley"),
    G("on", "cup", "lounge", "table"), G("in", "pen", "table", "larder"), G("in", "pen", "", "table"),
    G("open", "table", "", ""),
]
TASKS = ["bring me the cup", "put the cup on the worktop in the galley", "I am on the armchair, bring the cup to me",
         "take the cup to the lounge, I am at the lamp table"]
LATESTS = ["", "the galley one", "the coffee table", "I am on the armchair", "I am by the lamp table"]
HOSTS = [(ENV, ""), (ENV_UNTRACKED, ""), (ENV_ONE_DRAWER, ""), (ENV_NO_SPEAKER, ""), (ENV, "keep"), (ENV, "handover"),
         (ENV_UNTRACKED, "q_where_is_you")]


def _run(cls, env, mode, goals, task, latest, asked=None, known=None):
    h = cls(mode)
    h._where_asked = asked
    if known:
        h._person_at = dict(known)
    mem = Memory("/nonexistent/m.json")
    out = _norm(h._ground_goals(copy.deepcopy(goals), Places(copy.deepcopy(env)), mem, task, latest=latest))
    return out, h._where_asked, dict(h.__dict__.get("_person_at", {})), h.options.calls


@pytest.mark.parametrize("env,mode", HOSTS)
def test_ground_goals_equal_the_reference(env, mode):
    for g, task, latest in itertools.product(GOALS, TASKS, LATESTS):
        for asked in (None, "auntie", "nephew"):
            a = _run(Old, env, mode, [g], task, latest, asked)
            b = _run(New, env, mode, [g], task, latest, asked)
            assert a == b, (g, task, latest, asked, mode)


def test_several_goals_and_remembered_state_equal_the_reference():
    goals = [GOALS[16], GOALS[21], GOALS[2], GOALS[12]]
    for env, mode in HOSTS:
        for task, latest in itertools.product(TASKS, LATESTS):
            assert _run(Old, env, mode, goals, task, latest, "nephew", {"nephew": "bunk@kids room"}) == \
                _run(New, env, mode, goals, task, latest, "nephew", {"nephew": "bunk@kids room"}), (task, latest, mode)


def test_the_object_memory_decides_the_source_equally(tmp_path):
    mem_old, mem_new = Memory(tmp_path / "a.json"), Memory(tmp_path / "b.json")
    for m in (mem_old, mem_new):
        m.data["objects"]["cup"] = {"place": "worktop@galley", "ts": 0, "how": "seen"}
    g = [G("on", "cup", "worktop", "armchair"), G("on", "cup", "armchair", "lamp table")]
    for task in TASKS:
        a = Old("")._ground_goals(copy.deepcopy(g), Places(ENV), mem_old, task)
        b = New("")._ground_goals(copy.deepcopy(g), Places(ENV), mem_new, task)
        assert _norm(a) == _norm(b), task


def test_pure_methods_and_names_equal_the_reference():
    places = Places(ENV)
    for g, task, latest in itertools.product(GOALS, TASKS, LATESTS):
        for name in ("_ground_container", "_ground_template"):
            args = (copy.deepcopy(g), places, task) + ((latest,) if name == "_ground_template" else ())
            if name == "_ground_template" and g["type"] not in ("go", "say", "deliver"):
                continue
            assert _norm(getattr(Old, name)(*args)) == _norm(getattr(New, name)(*copy.deepcopy(args))), (name, g, task)
        a, b = Old("")._ground_speaker(copy.deepcopy(g), "to", places, task, latest), New("")._ground_speaker(copy.deepcopy(g), "to", places, task, latest)
        assert _norm(a) == _norm(b)
    for key in list(ENV) + ["nowhere"]:
        for lang in ("en", "ko", "de"):
            assert Old._say_name(places, key, lang) == New._say_name(places, key, lang)
