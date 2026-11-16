"""Option ``heat_safety`` (default off): a goal that puts a thing that can burn on or in a heat-source place is refused.
Made-up flats, objects and words only: a galley with a hob (heat place), a yard with a brazier (a second made-up pack), burnable words
"papyrine" / "wisp" / "tinderleaf", a made-up language ``zq``. Nothing here is a real robot's place, object or sentence."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import langdata
from robot_agent.options import heat_safety as H

HOB, SHELF, BRAZIER, BENCH = "hob@galley", "shelf@galley", "brazier@yard", "bench@yard"
ENV = {HOB: {"aliases": ["hob"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       SHELF: {"aliases": ["shelf"], "loc": {"x": 1.6, "y": 1.0}, "surface": True},
       BRAZIER: {"aliases": ["brazier"], "loc": {"x": 8.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 9.0, "y": 1.0}, "surface": True}}
GALLEY = {"places": {HOB: {"clearance_m": 0.5, "assumed": True}}, "burnable": {"words": ["papyrine", "wisp"], "assumed": True}}
YARD = {"places": {"brazier": {"clearance_m": 1.2, "source": "made-up safety sheet"}},
        "burnable": {"words": ["tinderleaf"], "source": "made-up safety sheet"}}
ZQ = {"heat_safety": {"refuse": "zq-nek {m} zq-hot", "part": "zq-semi {m} zq-hot", "assumed": True}}
PLAN = steps(f"MoveTo {SHELF}", "Find zorb", "Pick zorb", f"MoveTo {BENCH}", f"Place {BENCH}")


def goal(obj, to, kind="on", said=""):
    return {"type": kind, "object": obj, "from": "", "to": to, "said": said}


def read(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": list(goals)}


@pytest.fixture
def env():
    return dict(ENV)


def _run(make_assist, goals, data=GALLEY, objects=None, task="Put the papyrine scroll on the hob.", lang="en", **opts):
    robot = FakeRobot(ENV, objects or {"papyrine scroll": SHELF, "zorb": SHELF}, start=SHELF)
    loop = make_assist(robot, FakeLLM(read(*goals), default="{}"), ScriptedPlanner(PLAN), search_legs=1, ask_help=False, detector_miss_rate=0, **opts)
    if data is not None:
        loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"heat_safety": data}}
    return loop, robot, run_task(loop, task, lang=lang, max_events=3000)


def _flags(loop):
    return [e for e in loop.lab_log if e["flag"] == H.FLAG]


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at", "plan_sec")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


# ── pure functions on small made-up tables ──────────────────────────────────────────────────────────
HEAT, BURN = H.heat_places(GALLEY["places"]), H.burnable_words(GALLEY["burnable"])


@pytest.mark.parametrize("g, want", [
    (goal("papyrine scroll", HOB), (HOB, 0.5)),                       # burnable on the heat place
    (goal("wisps", HOB, "in"), (HOB, 0.5)),                           # into it; a plural folds to the declared word
    (goal("papyrine_scroll_1", HOB), (HOB, 0.5)),                     # an object id: '_' is a space
    (goal("scroll", HOB, said="papyrine scroll"), (HOB, 0.5)),        # the person's own words
    (goal("brass zorb", HOB), None),                                  # decoy: nothing burnable on the heat place
    (goal("papyrine scroll", SHELF), None),                           # decoy: burnable away from the heat place
    (goal("papyrine scroll", "hob@yard"), None),                      # another room's place with the same head
    (goal("papyriner", HOB), None),                                   # whole words only
    ({"type": "go", "object": "", "from": "", "to": HOB}, None),     # not a put
])
def test_heat_safety_verdict_table(g, want):
    assert H.heat_verdict(g, HEAT, BURN) == want


def test_heat_safety_declared_head_covers_every_room_and_rooms_must_agree():
    assert H.same_place("brazier", BRAZIER) and H.same_place(BRAZIER, "brazier") and not H.same_place(BRAZIER, "brazier@den")


@pytest.mark.parametrize("places, burnable", [
    ({HOB: {"clearance_m": 0.5}}, GALLEY["burnable"]),                              # place without a label
    ({HOB: {"clearance_m": 0, "assumed": True}}, GALLEY["burnable"]),               # no distance, no rule
    ({HOB: {"clearance_m": True, "assumed": True}}, GALLEY["burnable"]),
    (GALLEY["places"], {"words": ["papyrine"]}),                                    # words without a label
    (GALLEY["places"], {"words": "papyrine", "assumed": True}),
    ({}, {}),
])
def test_heat_safety_unlabelled_or_incomplete_data_is_no_rule_empty(places, burnable):
    heat, burn = H.heat_places(places), H.burnable_words(burnable)
    assert not (heat and burn) or H.heat_verdict(goal("papyrine scroll", HOB), heat, burn) is None


def test_heat_safety_sentence_of_a_made_up_language_zq_and_none_without_a_label(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    assert H.sentence("zq", "refuse", 1.2) == "zq-nek 1.2 zq-hot" and H.sentence("zq", "part", 0.5) == "zq-semi 0.5 zq-hot"
    monkeypatch.setitem(langdata._CACHE, "zq", {"heat_safety": {"refuse": "zq-nek {m}"}})
    assert H.sentence("zq", "refuse", 1.2) is None


def test_heat_safety_is_off_by_default_and_on_by_its_run_option():
    assert not resolve(H.SPEC, {}, {}, None).on and resolve(H.SPEC, {"heat_safety": "1"}, {}, None).on


# ── through the loop ────────────────────────────────────────────────────────────────────────────────
def test_heat_safety_loop_final_burnable_on_the_heat_place_is_refused_and_nothing_moves(make_assist):
    """The failure class: the reader says ``clear`` with on(<burnable> -> <heat place>); the robot must not carry it out."""
    loop, robot, evs = _run(make_assist, [goal("papyrine scroll", HOB)], heat_safety="1")
    d = done(evs)
    assert d["status"] == "refused" and d["refusal"] == "safety" and "0.5 m" in d["say"]
    assert robot.obj["papyrine scroll"] == SHELF and not [t for t in robot.trace if t[0] in ("pick", "place")]
    assert [r["rule"] for r in d["refusals"]] == [f"heat_safety:{HOB}"]
    assert _flags(loop) == [{"flag": "heat_safety", "part": "refuse", "places": [HOB], "refused": 1, "goals": 1}]


def test_heat_safety_loop_final_decoy_non_burnable_on_the_heat_place_is_done(make_assist):
    plan_hob = steps(f"MoveTo {SHELF}", "Find zorb", "Pick zorb", f"MoveTo {HOB}", f"Place {HOB}")
    robot = FakeRobot(ENV, {"zorb": SHELF}, start=SHELF)
    loop = make_assist(robot, FakeLLM(read(goal("zorb", HOB)), default="{}"), ScriptedPlanner(plan_hob), search_legs=1, ask_help=False,
                       detector_miss_rate=0, heat_safety="1")
    loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"heat_safety": GALLEY}}
    d = done(run_task(loop, "Put the zorb on the hob.", max_events=3000))
    assert d["status"] == "success" and robot.obj["zorb"] == HOB and _flags(loop) == []


def test_heat_safety_loop_final_partial_drops_the_burnable_goal_and_does_the_rest(make_assist):
    loop, robot, evs = _run(make_assist, [goal("papyrine scroll", HOB), goal("zorb", BENCH)], heat_safety="1",
                            task="Put the papyrine scroll on the hob and the zorb on the bench.")
    d = done(evs)
    assert d["status"] == "success" and d.get("refused_parts") and robot.obj == {"papyrine scroll": SHELF, "zorb": BENCH}
    assert d["refusals"][0]["goal"]["object"] == "papyrine scroll" and _flags(loop)[0]["part"] == "drop"


def test_heat_safety_loop_final_second_pack_brazier_by_data_alone_in_zq(make_assist, monkeypatch):
    """A second made-up pack (a brazier in a yard, other words, a cited source) and a made-up language: the same code, other data."""
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    loop, robot, evs = _run(make_assist, [goal("tinderleaf bundle", BRAZIER)], data=YARD, objects={"tinderleaf bundle": BENCH},
                            task="Put the tinderleaf bundle on the brazier.", lang="zq", heat_safety="1")
    d = done(evs)
    assert d["status"] == "refused" and d["say"] == "zq-nek 1.2 zq-hot" and robot.obj["tinderleaf bundle"] == BENCH
    loop2, _r, evs2 = _run(make_assist, [goal("papyrine scroll", BRAZIER)], data=YARD, task="Put the papyrine scroll on the brazier.",
                           lang="zq", heat_safety="1")
    assert done(evs2)["status"] != "refused" and _flags(loop2) == []                 # the galley's words mean nothing to this pack


def test_heat_safety_burnable_on_the_heat_place_is_not_refused_off(make_assist):
    loop, robot, evs = _run(make_assist, [goal("papyrine scroll", HOB)])
    assert done(evs)["status"] != "refused" and _flags(loop) == []


@pytest.mark.parametrize("data", [None, {}, {"places": {}, "burnable": {}}, {"places": GALLEY["places"]}, {"burnable": GALLEY["burnable"]}])
def test_heat_safety_loop_final_empty_data_is_byte_identical_to_off_empty(make_assist, data):
    def run(**o):
        loop, robot, evs = _run(make_assist, [goal("papyrine scroll", HOB)], data=data, **o)
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log
    off, on = run(), run(heat_safety="1")
    assert on == off


def test_heat_safety_no_phrase_in_any_language_does_nothing_but_logs(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "en", {**langdata.get("en"), "heat_safety": {}})
    loop, robot, evs = _run(make_assist, [goal("papyrine scroll", HOB)], heat_safety="1")
    assert done(evs)["status"] != "refused" and [f["part"] for f in _flags(loop)] == ["no_phrase"]
