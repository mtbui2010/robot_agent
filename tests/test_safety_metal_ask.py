"""Option ``hazard`` key ``ask`` (docs/metal_microwave.md): an ``ask`` entry of the manifest list ``safety.hazards`` (a bare utensil word x a
microwave place) makes the robot ASK before it puts the thing in, instead of refusing or doing it silently. Made-up objects ("zorb ..."):
pack 1 a galley with a microwave (en words of the shape of the doc's data), pack 2 a yard with a "zapbox", made-up utensil words
("glimmet") and material words ("brassine" refuses, "fluffin" is a declared safe material), a made-up language ``zq``."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, scrub, steps
from robot_agent.core.planning import langdata
from robot_agent.options.hazard import answer_says, unless_hit

OVEN, SHELF, ZAPBOX, BENCH = "microwave@galley", "shelf@galley", "zapbox@yard", "bench@yard"
ENV = {OVEN: {"aliases": ["microwave"], "loc": {"x": 1.0, "y": 1.0}, "surface": True, "openable": True},
       SHELF: {"aliases": ["shelf"], "loc": {"x": 1.6, "y": 1.0}, "surface": True},
       ZAPBOX: {"aliases": ["zapbox"], "loc": {"x": 8.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 9.0, "y": 1.0}, "surface": True}}
W0, W1 = r"(?<![a-z0-9])", r"(?![a-z0-9])"
SAY = "metal in a microwave oven can cause sparks and a fire"
METAL = {"objects": [W0 + r"(?:metal|steel|foil)" + W1], "places": [r"^microwave(?:@|$)"], "say": SAY, "assumed": True}
SPOON = {"ask": "q_metal_microwave", "objects": [W0 + r"(?:spoons?|forks?)" + W1], "places": [r"^microwave(?:@|$)"], "say": SAY,
         "unless": [W0 + r"(?:plastic|wooden)" + W1], "assumed": True}
GALLEY = [METAL, SPOON]
YARD = [{"objects": [W0 + "brassine" + W1], "places": [r"^zapbox(?:@|$)"], "say": "brassine sparks in a zapbox", "source": "made-up sheet"},
        {"ask": "q_zap_glimmet", "objects": [W0 + "glimmets?" + W1], "places": [r"^zapbox(?:@|$)"], "say": "brassine sparks in a zapbox",
         "unless": [W0 + "fluffin" + W1], "source": "made-up sheet"}]
ZQ = {"name": "Zqish", "speech": {"q_zap_glimmet": "zq-glimmet {object} zq-brassine?"}}
Q = r"metal\?"                         # the en question of pack 1 (language pack en, phrase q_metal_microwave)


@pytest.fixture
def env():
    return dict(ENV)


def goal(obj, to=OVEN, kind="on", said=""):
    return {"type": kind, "object": obj, "from": "", "to": to, "said": said}


def read(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": list(goals)}


def _run(make_assist, goals, legacy=None, answer=None, task="My zorb soup went cold, put the zorb spoon on the microwave.", lang="en",
         llm=None, obj="zorb spoon", to=OVEN, **opts):
    rules = [{"if": Q, "then": answer}] if answer is not None else []
    robot = FakeRobot(ENV, {obj: SHELF}, start=SHELF, reply_rules=rules)
    planner = ScriptedPlanner(steps(f"MoveTo {SHELF}", f"Find {obj}", f"Pick {obj}", f"MoveTo {to}", f"Place {to}"))
    loop = make_assist(robot, llm or FakeLLM(read(*goals), default="{}"), planner, search_legs=1, ask_help=False, detector_miss_rate=0, **opts)
    if legacy is not None:
        loop.robot.manifest.safety = {**(loop.robot.manifest.safety or {}), "hazards": legacy}
    return loop, robot, run_task(loop, task, lang=lang, max_events=3000)


def asked_metal(robot) -> int:
    return sum(1 for q in robot.asked if "metal?" in q)


# ── the decisions (pure) ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("answer, metal, reply, safe, says", [
    ("", False, (None, ""), False, "unanswered"), ("  ", False, (None, ""), False, "unanswered"),
    ("yes", False, (True, ""), False, "refuse"), ("it is steel", True, (False, "it is steel"), False, "refuse"),
    ("no, it is steel", True, (False, "it is steel"), False, "refuse"),           # a refusing word wins over the no
    ("I don't know", False, (False, "I don't know"), False, "refuse"),             # the robot cannot rule metal out
    ("Oh. Never mind then.", False, (False, "Oh. Never mind then."), False, "refuse"),   # not a clear no: fail safe
    ("hmm", False, (None, ""), False, "refuse"),
    ("no", False, (False, ""), False, "proceed"), ("plastic", False, (False, "plastic"), True, "proceed"),
    ("No, it is wooden.", False, (False, "it is wooden."), True, "proceed"),
])
def test_metal_ask_answer_table(answer, metal, reply, safe, says):
    assert answer_says(answer, metal, reply, safe) == says


@pytest.mark.parametrize("answer, says", [("yes", "refuse"), ("no", "proceed"), ("No, it is plastic.", "proceed"), ("it's plastic", "proceed"),
                                          ("I don't know", "refuse"), ("Oh, never mind.", "refuse"), ("no, it is steel", "refuse")])
def test_metal_ask_answer_table_with_the_engines_yes_no_reader(answer, says):
    from robot_agent.core.run_control import yes_no
    metal = any(__import__("re").search(p, answer.lower()) for p in METAL["objects"])
    assert answer_says(answer, metal, yes_no(answer), unless_hit(SPOON, {"object": answer})) == says


@pytest.mark.parametrize("g, hit", [(goal("plastic zorb spoon"), True), (goal("zorb spoon", said="the wooden one"), True),
                                    (goal("zorb spoon"), False), (goal("plasticky zorb spoon"), False)])
def test_metal_ask_unless_words_are_whole_words_of_object_or_said(g, hit):
    assert unless_hit(SPOON, g) is hit and unless_hit({}, g) is False


# ── through the loop ───────────────────────────────────────────────────────────────────────────────
def test_metal_ask_loop_final_bare_spoon_yes_is_refused_with_the_rule_sentence(make_assist):
    """The failure class: the reader says clear with open / in(spoon -> microwave) / close; a read-back is no safety check. Now one
    question; "yes" refuses all of it (the oven's open / close only served the refused put) and nothing moves."""
    goals = [{"type": "open", "object": OVEN}, goal("zorb spoon", kind="in"), {"type": "close", "object": OVEN}]
    loop, robot, evs = _run(make_assist, goals, GALLEY, "yes", hazard_ask=True)
    d = done(evs)
    assert asked_metal(robot) == 1 and "Is the zorb spoon metal?" in robot.asked[0]
    assert d["status"] == "refused" and d["refusal"] == "safety" and SAY in d["say"] and d["refusals"][0]["goal"]["object"] == "zorb spoon"
    assert not [t for t in robot.trace if t[0] != "ask"]
    assert {"flag": "hazard", "asked": "zorb spoon", "answer": "refuse"}.items() <= next(e for e in loop.lab_log if "asked" in e).items()


@pytest.mark.parametrize("answer", ["no", "No, it is plastic."])
def test_metal_ask_loop_final_bare_spoon_no_proceeds(make_assist, answer):
    loop, robot, evs = _run(make_assist, [goal("zorb spoon")], GALLEY, answer, hazard_ask=True)
    assert asked_metal(robot) == 1 and done(evs)["status"] == "success" and robot.obj["zorb spoon"] == OVEN


def test_metal_ask_loop_final_the_gate_running_again_after_a_correction_does_not_ask_again(make_assist):
    robot = FakeRobot(ENV, {"zorb spoon": SHELF}, answers=["no, on the microwave"], start=SHELF, reply_rules=[{"if": Q, "then": "no"}])
    loop = make_assist(robot, FakeLLM(read(goal("zorb spoon")), read(goal("zorb spoon")), default="{}"),       # read again after the "no"
                       ScriptedPlanner(steps(f"MoveTo {SHELF}", "Find zorb spoon", "Pick zorb spoon", f"MoveTo {OVEN}", f"Place {OVEN}")),
                       search_legs=1, ask_help=False, detector_miss_rate=0, hazard_ask=True, confirm_task=True)
    loop.robot.manifest.safety = {"hazards": GALLEY}
    evs = run_task(loop, "Put the zorb spoon on the microwave.", max_events=3000)
    assert sum(e["event"] == "understand" for e in evs) == 2 and asked_metal(robot) == 1 and "right?" in robot.asked[1]


def test_metal_ask_loop_final_no_answer_stops_safely_as_the_engine_does(make_assist):
    """Silence: the engine's own unanswered-question end (status failed, "no answer to: ...", the say_no_answer sentence)."""
    loop, robot, evs = _run(make_assist, [goal("zorb spoon")], GALLEY, "", hazard_ask=True)
    d = done(evs)
    assert asked_metal(robot) >= 1 and d["status"] == "failed" and d["msg"].startswith("no answer to:") and robot.obj["zorb spoon"] == SHELF
    assert not [t for t in robot.trace if t[0] != "ask"]


@pytest.mark.parametrize("obj, status, where", [("plastic zorb spoon", "success", OVEN), ("steel zorb spoon", "refused", SHELF)])
def test_metal_ask_loop_final_a_material_word_decides_without_a_question(make_assist, obj, status, where):
    loop, robot, evs = _run(make_assist, [goal(obj)], GALLEY, "yes", task=f"Put the {obj} on the microwave.", obj=obj, hazard_ask=True)
    assert asked_metal(robot) == 0 and done(evs)["status"] == status and robot.obj[obj] == where


def test_metal_ask_loop_final_spoon_elsewhere_is_not_asked(make_assist):
    loop, robot, evs = _run(make_assist, [goal("zorb spoon", ZAPBOX)], GALLEY, "yes", to=ZAPBOX, task="Put the zorb spoon on the zapbox.",
                            hazard_ask=True)
    assert asked_metal(robot) == 0 and done(evs)["status"] == "success" and robot.obj["zorb spoon"] == ZAPBOX


def test_metal_ask_loop_final_second_pack_in_the_persons_language_zq(make_assist, monkeypatch):
    """Pack 2 (other place, other words) with its question in the made-up language's own pack; the refusal through the translator."""
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    zq, q = "zq-nek zq-brassine zq-zap", "zq-glimmet glimmet zorb zq-brassine?"      # q: the zq pack's phrase with the object
    llm = FakeLLM((r"Zqish.*brassine sparks in a zapbox", zq), (r"Zqish.*" + zq, zq), (r"Zqish.*zq-glimmet", q),   # said text passed once more
                  (r"into goals", read(goal("glimmet zorb", ZAPBOX))), default="zq-ok")
    robot = FakeRobot(ENV, {"glimmet zorb": SHELF}, start=SHELF, reply_rules=[{"if": "zq-glimmet", "then": "yes"}])
    loop = make_assist(robot, llm, ScriptedPlanner(steps(f"MoveTo {ZAPBOX}")), search_legs=1, ask_help=False, detector_miss_rate=0,
                       hazard_ask=True)
    loop.robot.manifest.safety = {"hazards": YARD}
    d = done(run_task(loop, "zq glimmet zorb zapbox", lang="zq", max_events=3000))
    assert robot.asked[0] == q and d["status"] == "refused" and d["say"] == zq
    assert robot.obj["glimmet zorb"] == SHELF


def test_metal_ask_loop_final_second_pack_safe_material_word_is_not_asked(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    loop, robot, evs = _run(make_assist, [goal("fluffin glimmet zorb", ZAPBOX)], YARD, "yes", obj="fluffin glimmet zorb", to=ZAPBOX,
                            task="Put the fluffin glimmet zorb on the zapbox.", hazard_ask=True)
    assert not [q for q in robot.asked if "zq-glimmet" in q] and done(evs)["status"] == "success"


def _bytes(make_assist, legacy, **opts):
    loop, robot, evs = _run(make_assist, [goal("zorb spoon")], legacy, "yes", **opts)
    return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log


def test_metal_ask_loop_final_key_off_ignores_ask_entries_byte_identical_off(make_assist):
    """``hazard.ask`` off (the default): an ``ask`` entry neither asks nor refuses; the run is the run without it."""
    assert _bytes(make_assist, GALLEY) == _bytes(make_assist, [METAL]) == _bytes(make_assist, None)


@pytest.mark.parametrize("legacy", [None, [], [METAL]])
def test_metal_ask_loop_final_key_on_without_ask_entries_is_byte_identical_empty(make_assist, legacy):
    assert _bytes(make_assist, legacy, hazard_ask=True) == _bytes(make_assist, legacy)


def test_metal_ask_loop_final_entry_without_a_phrase_in_any_pack_asks_nothing_empty(make_assist):
    no_phrase = [METAL, {**SPOON, "ask": "q_no_such_phrase"}]
    assert _bytes(make_assist, no_phrase, hazard_ask=True) == _bytes(make_assist, [METAL])


def test_metal_ask_loop_final_rules_off_asks_nothing_off(make_assist):
    assert _bytes(make_assist, GALLEY, hazard_ask=True, hazard_rules="off") == _bytes(make_assist, None, hazard_rules="off")
