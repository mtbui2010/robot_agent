"""Step S13: the Find ladder (_recover_find, _relook_why, _relook_here, _relook_budget, _second_look, _splice_leg, _said_of,
_confirm_near, _search_on, _recover_phantom, _refuted and SECOND_LOOKS) moved verbatim from assist_loop.py to the FindLadder mixin
in find_ladder.py. These tests prove nothing changed:
(1) the mixin is in AssistLoop's method resolution order, serves the eleven names and assist_loop.py no longer defines them;
(2) the method bodies are the same syntax trees as the reference copy (tests/legacy_ref/s13_find_ladder.py, copied from 9afe90c);
(3) a differential test: the old and the new class, hosted by the same scripted stub (made-up robot, belief, places), return equal
outcomes, yield equal events, leave the run state equal and make the same calls on the host, over a seeded grid of cases.
Made-up robots only."""

import ast
import copy
import inspect
import random
import types

import pytest

from legacy_ref import s13_find_ladder as old
from robot_agent.contract import Recovery
from robot_agent.contract.recovery import RecoveryRule
from robot_agent.core.planning import action_model, assist_loop, find_ladder, understand
from robot_agent.core.planning.loop import _NEXT, _STOP
from robot_agent.core.planning.phases import PHASE_MIXINS

NAMES = ("_recover_find", "_relook_why", "_relook_here", "_relook_budget", "_second_look", "_splice_leg", "_said_of",
         "_confirm_near", "_search_on", "_recover_phantom", "_refuted", "SECOND_LOOKS")


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_names():
    assert find_ladder.FindLadder in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(find_ladder.FindLadder) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(assist_loop.AssistLoop), n
        assert n in vars(find_ladder.FindLadder), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(find_ladder.FindLadder, n), n


def test_only_the_named_methods_are_in_the_mixin():
    own = {k for k, v in vars(find_ladder.FindLadder).items() if callable(v) or isinstance(v, (staticmethod, int))}
    assert own - {"__module__", "__qualname__"} == set(NAMES)


def test_step_failed_stays_and_still_reaches_the_ladder_through_self():
    assert "_step_failed" in vars(assist_loop.AssistLoop)
    src = inspect.getsource(assist_loop.AssistLoop._step_failed)
    assert "self._recover_find" in src and "self._refuted" in src


def test_outcome_markers_are_the_loops():
    from robot_agent.core.planning import loop
    assert find_ladder._NEXT is loop._NEXT and find_ladder._STOP is loop._STOP


def test_second_looks_constant_is_two():
    assert find_ladder.FindLadder.SECOND_LOOKS == old.RefFindLadder.SECOND_LOOKS == 2


# -- (2) the bodies are the same syntax trees ---------------------------------------------------------------------------------
class _SameImport(ast.NodeTransformer):
    """The reference copy imports fuzzy_place by its absolute module name; the module by the relative one: both mean one module."""

    def visit_ImportFrom(self, node):
        node.module, node.level = node.module.split(".")[-1], 0
        return node


def _methods(cls):
    tree = _SameImport().visit(ast.parse(inspect.getsource(cls)))
    body = tree.body[0].body
    return {n.name: ast.dump(n) for n in body if isinstance(n, ast.FunctionDef)}


def test_method_bodies_equal_the_reference_syntax_trees():
    new, ref = _methods(find_ladder.FindLadder), _methods(old.RefFindLadder)
    assert set(new) == set(ref) == set(NAMES) - {"SECOND_LOOKS"}
    for n in ref:
        assert new[n] == ref[n], n


# -- (3) differential test ----------------------------------------------------------------------------------------------------
class Leg:
    def __init__(self, tag):
        self.tag = tag

    def steps(self, at):
        return [{"action": "MoveTo", "object": self.tag}, {"action": "Find", "object": "cup"}]

    def text(self):
        return f"leg {self.tag}"


class Belief:
    """Scripted search belief: answers come from the case, every call is logged."""

    def __init__(self, sc, log):
        self.sc, self.log = sc, log
        self.places = ["table", "shelf", "desk"]

    def relook_budget(self):
        return self.sc["budget"]

    def relook_beats_next(self, o, here, opened):
        self.log.append(("beats", o, here, sorted(opened)))
        p = self.sc["beat"]
        return types.SimpleNamespace(place=p) if p else None

    def relook_order(self, o, exclude=()):
        self.log.append(("order", o, list(exclude)))
        return list(self.sc["order"])

    def leg_for(self, o, p, opened):
        self.log.append(("leg_for", o, p, sorted(opened)))
        return Leg(p) if p in self.sc["legs"] else None

    def next_leg(self, o, here, opened):
        self.log.append(("next_leg", o, here, sorted(opened)))
        return Leg("next") if self.sc["next"] else None

    def unsearch(self, o, p):
        self.log.append(("unsearch", o, p))

    def missed(self, o, p, opened, count=True):
        self.log.append(("missed", o, p, sorted(opened), count))

    def restore_search(self, o):
        self.log.append(("restore", o))

    def is_searched(self, o, p, opened):
        return p in self.sc["searched_by_belief"]


class World:
    def __init__(self, sc):
        self.found, self.arrived = "cup@x", sc["here"]
        self.opened, self.holding, self.holding_since = set(sc["opened"]), sc["holding"], 1.0 if sc["holding"] else None

    def copy(self):
        return copy.deepcopy(self)


class Places:
    def __init__(self, sc, log):
        self.sc, self.log = sc, log

    def openable(self, k):
        return k in self.sc["openable"]

    def ground(self, a, b):
        self.log.append(("ground", a, b))
        return self.sc["grounded"]


class Memory:
    def __init__(self, sc, log):
        self.sc, self.log = sc, log

    def where(self, o):
        return self.sc["mem_where"]

    def remember_answer(self, task, q, ans):
        self.log.append(("remember", task, str(q), ans))

    def see(self, o, k, how):
        self.log.append(("see", o, k, how))


class Robot:
    def __init__(self, sc):
        self.plan_actions = sc["plan_actions"]

    def model_capabilities(self):
        return None


class Options:
    def __init__(self, sc, log):
        self.sc, self.log = sc, log

    def query(self, name, *a, default=None):
        self.log.append(("query", name, a[1:]))
        return self.sc["looks_made"] if name == "looks_made" else default

    def decide_flow(self, name, *a):
        self.log.append(("flow", name, a[1:]))
        if False:
            yield
        return self.sc["flow"].get(name)


class Stub:
    """The host: everything the ladder calls on ``self`` outside itself, scripted by the case."""

    def __init__(self, sc, log):
        self.sc, self.log = sc, log
        self.cfg = dict(sc["cfg"])
        self.options, self.robot = Options(sc, log), Robot(sc)
        self.node = "node"

    def _role(self, role):
        return self.sc["roles"].get(role)

    def _manifest_planner(self, key, default=None):
        return self.sc["recovery"]

    def _lab(self, flag, **info):
        self.log.append(("lab", flag, tuple(sorted(info.items()))))

    def _finish(self, r, status, msg="", **kw):
        return ("finish", status, msg, tuple(sorted(kw.items())))

    def _search_missed(self, r, o, rule):
        self.log.append(("search_missed", o))
        if False:
            yield
        return Leg("missed") if self.sc["missed_leg"] else None

    def _missed_enough(self, r, o):
        return self.sc["missed_enough"]

    def _search_over(self, r, o, ask=False):
        return self.sc["over_ask"] if ask else self.sc["over"]

    def _may_ask(self, r, kind, o):
        return self.sc["may_ask"]

    def _search_total(self, r, o):
        return self.sc["total"]

    def _search_told(self, r, o, k):
        self.log.append(("told", o, k))

    def _ask_help(self, r, st, kind, q):
        self.log.append(("ask_help", kind, str(q), getattr(q, "key", "")))
        if False:
            yield
        return self.sc["help_out"], self.sc["help_ans"]

    def _ask_yes_no(self, r, q):
        self.log.append(("yes_no", str(q)))
        return "a", self.sc["yes"], "rest"

    def _say_name(self, places, k, lang="en"):
        return f"<{k}>"

    def _ground_all(self, r, seq):
        self.log.append(("ground_all", copy.deepcopy(seq)))
        return [dict(x, grounded=True) for x in seq]

    def _holding(self, node, default):
        return self.sc["holding_sensor"]


def _case(rng):
    """One random case; every field the ladder reads, drawn from small sets so the branches are all reached."""
    here = rng.choice([None, "table", "shelf", "desk"])
    return {
        "cfg": {"relook": rng.random() < 0.4, "search_legs": rng.random() < 0.6, "search_max": rng.choice([0, 1, 2])},
        "here": here, "opened": rng.choice([(), ("table",), ("shelf", "desk")]), "holding": rng.choice([None, "cup"]),
        "openable": rng.choice([(), ("table",), ("table", "shelf", "desk")]),
        "budget": rng.choice([0, 1, 2]), "beat": rng.choice([None, "table", "shelf", "desk"]),
        "order": rng.sample(["table", "shelf", "desk", "bed"], rng.randint(0, 4)),
        "legs": rng.sample(["table", "shelf", "desk", "bed"], rng.randint(0, 4)), "next": rng.random() < 0.5,
        "searched_by_belief": rng.sample(["table", "shelf", "desk"], rng.randint(0, 3)),
        "grounded": rng.choice([None, "shelf", "desk"]), "mem_where": rng.choice([None, "table", "shelf"]),
        "plan_actions": rng.choice([["Find", "MoveTo", "Open", "Close"], ["Find", "MoveTo"], ["find", "moveto", "wait"]]),
        "roles": rng.choice([{}, {"close": "Close", "open": "Open"}, {"close": "Close"}]),
        "recovery": rng.choice([None, {}, {"wait": {"action": "Wait", "schedule_s": [2.0]}}, {"wait": {"action": "Nap", "schedule_s": [1]}},
                                {"wait": {"action": "Wait", "schedule_s": []}}]),
        "looks_made": rng.choice([0, 1, 2, 3]), "total": rng.choice([1, 2, 3, 4, 6]),
        "missed_leg": rng.random() < 0.5, "missed_enough": rng.random() < 0.3, "over": rng.random() < 0.2,
        "over_ask": rng.random() < 0.2, "may_ask": rng.random() < 0.6, "help_out": rng.choice([None, None, None, (_NEXT, "")]),
        "help_ans": rng.choice([None, "", "the shelf", "kitchen"]), "yes": rng.choice([True, False, None]),
        "flow": rng.choice([{}, {"on_where_answer": "desk"}, {"on_where_answer": (_NEXT, "")}]),
        "holding_sensor": rng.random() < 0.5, "model_errors": rng.random() < 0.3,
        "has_belief": rng.random() < 0.7, "found_at_here": rng.random() < 0.3, "relooked": rng.random() < 0.2,
        "doubted": rng.random() < 0.2, "failure_code": rng.choice(["", "ABSENT", "UNREACHABLE"]),
        "rule": (rng.choice([Recovery.CONTINUE_SEARCH, Recovery.REAPPROACH, Recovery.RETRY]), rng.random() < 0.7),
        "searched": rng.sample(["table", "shelf", "desk"], rng.randint(0, 3)), "find_fails": rng.choice([0, 1, 3]),
        "seen_far": rng.choice([[], ["table"]]), "told": rng.random() < 0.2, "goals": rng.choice([
            [], [{"object": "cup", "from": here, "said": "mug"}], [{"object": "cup", "from_any": ["table"]}], [{"object": "pen"}]]),
        "second_looks": rng.choice([None, {}, {"cup": ["desk"]}, {"cup": ["desk", "shelf", "table"]}]),
        "relook_done": rng.random() < 0.15, "extra_steps": rng.randint(0, 2),
    }


def _run_state(sc, log):
    r = types.SimpleNamespace()
    r.ev = lambda name, /, **kw: ("ev", name, tuple(sorted(kw.items())))
    r.i, r.task, r.node = 0, "fetch the cup", "node"
    r.world, r.places, r.memory = World(sc), Places(sc, log), Memory(sc, log)
    r.robot = Robot(sc)
    r.steps = [{"action": "Find", "object": "cup"}] + [{"action": "Say", "object": f"s{i}"} for i in range(sc["extra_steps"])]
    r.failures = [{"code": sc["failure_code"]}] if sc["failure_code"] else []
    r.found_at = {"cup": sc["here"]} if sc["found_at_here"] else {}
    r.relooked = {("cup", sc["here"])} if sc["relooked"] else set()
    r.doubted = {sc["here"]} if sc["doubted"] else set()
    r.searched, r.find_fails, r.seen_far = list(sc["searched"]), {"cup": sc["find_fails"]}, list(sc["seen_far"])
    r.goals, r.told, r.seen_at = sc["goals"], ({("cup", sc["here"])} if sc["told"] else set()), {"cup": "x"}
    if sc["has_belief"]:
        r.search = Belief(sc, log)
    if sc["second_looks"] is not None:
        r.second_looks = copy.deepcopy(sc["second_looks"])
    if sc["relook_done"]:
        r.relook_done = {("cup", sc["here"])}
    return r


def _drive(gen):
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, ("ok", stop.value)
    except Exception as e:  # the same exception must come out of both classes
        return events, ("raised", type(e).__name__, str(e))


def _snapshot(r):
    d = {k: v for k, v in vars(r).items() if k not in ("ev", "search", "places", "memory", "robot")}
    d["world"] = vars(r.world)
    return repr(sorted(d.items(), key=lambda kv: kv[0]))


def _fake_model(sc, calls):
    def check(step, world, **kw):
        calls.append(("check", step["action"], step["object"]))
        return [types.SimpleNamespace(is_error=True)] if sc["model_errors"] and step["object"] == "cup" else []

    def apply(step, world, **kw):
        calls.append(("apply", step["action"], step["object"]))
        world.found = step["object"] if step["action"] == "Find" else world.found
    return check, apply


def _fake_fuzzy(sc):
    def fuzzy(places, text, threshold=0.25, typo=False):
        return (sc["grounded"], "n") if text and "shelf" in text else (None, "")
    return fuzzy


def _outcome(cls_, method, sc, args, monkeypatch):
    log = []
    host = type("Host", (Stub, cls_), {})(sc, log)
    r = _run_state(sc, log)
    check, apply = _fake_model(sc, log)
    monkeypatch.setattr(action_model, "check", check)
    monkeypatch.setattr(action_model, "apply", apply)
    monkeypatch.setattr(understand, "fuzzy_place", _fake_fuzzy(sc))
    st = r.steps[0]
    rule = RecoveryRule(sc["rule"][0], mark_searched=sc["rule"][1])
    fn = getattr(host, method)
    call = {"_recover_find": lambda: fn(r, st, "not seen", rule), "_recover_phantom": lambda: fn(r, st, "nothing", rule),
            "_search_on": lambda: fn(r, "cup"), "_relook_here": lambda: fn(r, "cup", sc["here"] or "table"),
            "_confirm_near": lambda: fn(r, args or "the shelf", typo=bool(sc["yes"])),
            "_second_look": lambda: iter(()), "_splice_leg": lambda: iter(())}[method]
    if method == "_second_look":
        res = ("ok", [fn(r, "cup"), fn(r, "cup", True), fn(r, "cup")])
        return [], res, log, _snapshot(r)
    if method == "_splice_leg":
        res = ("ok", [fn(r, Leg("shelf")), fn(r, Leg("desk"))])
        return [], res, log, _snapshot(r)
    events, res = _drive(call())
    return events, res, log, _snapshot(r)


CASES = [_case(random.Random(13 + i)) for i in range(1500)]


@pytest.mark.parametrize("method", ["_recover_find", "_recover_phantom", "_search_on", "_relook_here", "_confirm_near",
                                    "_second_look", "_splice_leg"])
def test_generators_equal_the_reference_over_a_seeded_grid(method, monkeypatch):
    for n, sc in enumerate(CASES):
        a = _outcome(old.RefFindLadder, method, sc, None, monkeypatch)
        b = _outcome(find_ladder.FindLadder, method, sc, None, monkeypatch)
        assert a == b, (method, n)


def test_grid_reaches_every_branch_of_the_main_rung(monkeypatch):
    """The seeded grid is not a smoke test: it sees a relook, a doubt, a stop, a leg splice, an ask and a continue."""
    kinds = set()
    for sc in CASES:
        events, res, _log, _snap = _outcome(find_ladder.FindLadder, "_recover_find", sc, None, monkeypatch)
        kinds.update(e[1] if e[0] == "ev" else e[0] for e in events)
        if res[0] == "ok":
            v = res[1]
            kinds.add("next" if v[0] is _NEXT else "stop" if v[0] is _STOP else "hint")
    assert {"recover", "finish", "next", "stop", "hint"} <= kinds, kinds


def test_pure_helpers_equal_the_reference(monkeypatch):
    for sc in CASES[:400]:
        res = []
        for cls_ in (old.RefFindLadder, find_ladder.FindLadder):
            log = []
            host = type("Host", (Stub, cls_), {})(sc, log)
            r = _run_state(sc, log)
            out = [host._relook_why(r, "cup", sc["here"] or "table"), host._relook_budget(r), cls_._said_of(r, "cup"),
                   cls_._said_of(r, "pen"), host._refuted(r, r.steps[0], None), _snapshot(r), log]
            no = types.SimpleNamespace(detail="x")
            for step in ({"action": "Place", "object": "cup"}, {"action": "PutIn", "object": "box"}, {"action": "Pick", "object": "cup"}):
                out.append(host._refuted(r, step, no))
                out.append(_snapshot(r))
            out.append(log)
            res.append(out)
        assert res[0] == res[1]
