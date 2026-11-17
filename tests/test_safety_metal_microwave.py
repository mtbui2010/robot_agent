"""The rule 'no metal in a microwave oven' as DATA of option ``hazard`` (docs/metal_microwave.md): no engine code of its own.
Made-up objects ("zorb ..."), a made-up galley with a microwave (pack 1: the safety-profile form, words of the cited pages) and a made-up
yard with a "zapbox" (pack 2: the manifest list ``safety.hazards``, a made-up material word "brassine", a made-up language ``zq``)."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import langdata
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.safety import EMPTY, find_rule, load_profile

OVEN, SHELF, ZAPBOX, BENCH = "microwave@galley", "shelf@galley", "zapbox@yard", "bench@yard"
ENV = {OVEN: {"aliases": ["microwave"], "loc": {"x": 1.0, "y": 1.0}, "surface": True, "openable": True},
       SHELF: {"aliases": ["shelf"], "loc": {"x": 1.6, "y": 1.0}, "surface": True},
       ZAPBOX: {"aliases": ["zapbox"], "loc": {"x": 8.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 9.0, "y": 1.0}, "surface": True}}
# a metal word, whole word ('_' and '-' end a word), not negated ("non-", "no ", "without ", "-free"): the shape of the doc's recipe
NOT_NEG, FREE = r"(?<!non[-_ ])(?<!\bno[ _])(?<!without[ _])(?<![a-z0-9])", r"(?![a-z0-9])(?![-_ ]?free(?![a-z0-9]))"
URL_NIH = "https://news2use.ors.nih.gov/Pages/Fire-Safety-Tips-for-Using-Microwave-Ovens.aspx"
URL_UCB = "https://ehs.berkeley.edu/node/1128"
SAY = "metal in a microwave oven can cause sparks and a fire"
GALLEY = {"profile": "made_up_galley", "version": 1,
          "sources": {"nih": {"url": URL_NIH, "quote": "Never use metallic articles and aluminum foil in a microwave oven."},
                      "ucb": {"url": URL_UCB, "quote": "Do not use aluminum foil or any metal containers, metal utensils, metal objects, "
                                                       "or objects with metal or foil trim in the oven."}},
          "hazard_classes": {"metal": {"words": {"en": {"sourced": [NOT_NEG + r"(?:metal(?:s|lic)?|foils?|alumin(?:um|ium))" + FREE],
                                                       "assumed": [NOT_NEG + r"(?:steel|stainless|irons?|tins?)" + FREE]}},
                                       "sources": ["nih", "ucb"]}},
          "place_classes": {"microwave_oven": {"words": {"en": [r"\bmicrowave\b"]}, "provenance": "assumed"}},
          "rules": [{"id": "metal_microwave", "hazard": "metal", "places": ["microwave_oven"], "say": SAY, "provenance": {"source": "ucb"}}]}
YARD = [{"objects": [r"(?<![a-z])brassine(?![a-z])"], "places": [r"^zapbox(?:@|$)"], "say": "brassine sparks in a zapbox",
         "source": "made-up yard sheet"}]
ZQ = {"name": "Zqish"}
PLAN = steps(f"MoveTo {SHELF}", "Find zorb", "Pick zorb", f"MoveTo {BENCH}", f"Place {BENCH}")


@pytest.fixture
def env():
    return dict(ENV)


def profile(data=GALLEY):
    prof, errs = load_profile(data)
    assert not errs, errs
    return prof


def goal(obj, to=OVEN, kind="in", said=""):
    return {"type": kind, "object": obj, "from": "", "to": to, "said": said}


def gate(g, prof=None, legacy=(), mode="profile"):
    v = find_rule(g, Places(dict(ENV)), prof if prof is not None else profile(), legacy, mode)
    return v.rule if v else None


# ── the decision on small made-up tables (find_rule, the engine's one rule matcher) ─────────────────
@pytest.mark.parametrize("g", [
    goal("foil-wrapped zorb"), goal("aluminium_foil"), goal("zorb tray with metal trim"), goal("metallic zorb dish"),
    goal("zorb", said="the zorb still in its foil"),                 # the person's own words
    goal("a ceramic zorb mug with a metal handle"),                   # metal trim: no material exemption (the pages name metal trim)
    goal("metal-free zorb bowl with a foil lid"),                     # one negated word does not clear another
    goal("microwave-safe metal zorb rack"),                           # a label the robot cannot check does not clear a metal word
    goal("stainless steel zorb bowl"), goal("foil zorb", kind="on"),  # an assumed material; a put ON the place is checked too
])
def test_metal_microwave_a_metal_word_into_the_microwave_is_refused(g):
    assert gate(g) == "metal_microwave"


@pytest.mark.parametrize("g", [
    goal("metal-free glass zorb bowl"), goal("non-metal zorb bowl"), goal("nonmetallic zorb bowl"), goal("zorb bowl without metal"),
    goal("plastic zorb fork"), goal("zorb fork"),                     # no metal word: not decided by this rule (a known gap for a bare fork)
    goal("foil zorb", to=SHELF), goal("ironing zorb"),                # another place; whole words only
    {"type": "go", "object": "", "from": "", "to": OVEN},
])
def test_metal_microwave_decoys_without_an_undenied_metal_word_or_elsewhere_pass(g):
    assert gate(g) is None


def test_metal_microwave_second_pack_by_data_alone_other_place_other_word():
    assert find_rule(goal("brassine zorb", ZAPBOX), Places(dict(ENV)), EMPTY, YARD).say == "brassine sparks in a zapbox"
    assert gate(goal("foil zorb", ZAPBOX), EMPTY, YARD) is None and gate(goal("brassine zorb"), EMPTY, YARD) is None


@pytest.mark.parametrize("legacy", [(), [], [{"objects": [], "places": [r"^zapbox"], "say": "x"}]])
def test_metal_microwave_no_data_decides_nothing_empty(legacy):
    assert gate(goal("foil-wrapped zorb"), EMPTY, legacy) is None and gate(goal("brassine zorb", ZAPBOX), EMPTY, legacy) is None


def test_metal_microwave_rules_off_decides_nothing_off():
    assert gate(goal("foil-wrapped zorb"), mode="off") is None and gate(goal("brassine zorb", ZAPBOX), EMPTY, YARD, mode="off") is None


# ── through the loop (the engine's safety gate: option hazard) ─────────────────────────────────────
def read(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": list(goals)}


def _run(make_assist, goals, prof=None, legacy=None, task="Heat the foil-wrapped zorb in the microwave.", lang="en", llm=None, **opts):
    robot = FakeRobot(ENV, {"foil-wrapped zorb": SHELF, "zorb": SHELF, "brassine zorb": BENCH}, start=SHELF)
    planner = ScriptedPlanner(PLAN)
    loop = make_assist(robot, llm or FakeLLM(read(*goals), default="{}"), planner, search_legs=1, ask_help=False, detector_miss_rate=0, **opts)
    loop.robot.manifest.safety_profile = prof
    if legacy is not None:
        loop.robot.manifest.safety = {**(loop.robot.manifest.safety or {}), "hazards": legacy}
    return loop, robot, planner, run_task(loop, task, lang=lang, max_events=3000)


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at", "plan_sec")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


def test_metal_microwave_loop_final_foil_into_the_microwave_is_refused_and_nothing_moves(make_assist):
    """The failure class: the reader says ``clear`` with open / in(<metal thing> -> microwave) / close; the robot must refuse it all."""
    goals = [{"type": "open", "object": OVEN}, goal("foil-wrapped zorb"), {"type": "close", "object": OVEN}]
    loop, robot, planner, evs = _run(make_assist, goals, prof=profile())
    d = done(evs)
    assert d["status"] == "refused" and d["refusal"] == "safety" and SAY in d["say"]
    assert [r["rule"] for r in d["refusals"]] == ["metal_microwave"] and not robot.trace and planner.calls == []
    assert [e["rules"] for e in loop.lab_log if e["flag"] == "hazard"] == [["metal_microwave"]]


def test_metal_microwave_loop_final_partial_drops_the_metal_goal_and_does_the_rest(make_assist):
    loop, robot, _p, evs = _run(make_assist, [goal("foil-wrapped zorb"), goal("zorb", BENCH, "on")], prof=profile(),
                                task="Heat the foil-wrapped zorb in the microwave and put the zorb on the bench.")
    d = done(evs)
    assert d["status"] == "success" and d.get("refused_parts") and robot.obj["zorb"] == BENCH and robot.obj["foil-wrapped zorb"] == SHELF
    assert SAY in d["say"] and d["refusals"][0]["goal"]["object"] == "foil wrapped zorb"


def test_metal_microwave_loop_final_second_pack_refuses_in_the_persons_language_zq(make_assist, monkeypatch):
    """Pack 2 (manifest list, other place and word) in a made-up language: the rule's sentence is said through the engine's translator."""
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    zq = "zq-nek zq-brassine zq-zap"           # a Latin-script made-up language cannot be told from English: said text is passed once more
    llm = FakeLLM((r"Zqish.*brassine sparks in a zapbox", zq), (r"Zqish.*" + zq, zq), (r"into goals", read(goal("brassine zorb", ZAPBOX))),
                  default="zq-ok")
    loop, robot, planner, evs = _run(make_assist, None, legacy=YARD, task="Put the brassine zorb in the zapbox.", lang="zq", llm=llm)
    d = done(evs)
    assert d["status"] == "refused" and d["say"] == zq and robot.obj["brassine zorb"] == BENCH and not robot.trace


@pytest.mark.parametrize("prof, legacy", [(None, None), (None, []), (EMPTY, []), (None, [{"objects": [], "places": [], "say": ""}])])
def test_metal_microwave_loop_final_empty_data_is_byte_identical_to_no_data_empty(make_assist, prof, legacy):
    def run(p, lg):
        loop, robot, _pl, evs = _run(make_assist, [goal("foil-wrapped zorb")], prof=p, legacy=lg)     # a goal the rule would refuse
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log
    assert run(prof, legacy) == run(None, None)


def test_metal_microwave_loop_final_rules_off_is_byte_identical_to_no_data_off(make_assist):
    def run(p, lg, rules="off"):
        loop, robot, _pl, evs = _run(make_assist, [goal("brassine zorb", ZAPBOX, "on")], prof=p, legacy=lg, hazard_rules=rules,
                                     task="Put the brassine zorb on the zapbox.")
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log
    assert run(profile(), YARD) == run(None, None) and run(profile(), YARD, "core") != run(None, None, "core")
