"""Option ``heat_safety`` (default off): a goal that puts a thing that can burn on or in a heat-source place is refused.
Made-up flats, objects and words only: a galley with a hob (heat place), a yard with a brazier (a second made-up pack), burnable words
"papyrine" / "wisp" / "tinderleaf", cookware heads "crockle" / "skorrel", a made-up language ``zq``. Nothing here is a real robot's place,
object or sentence."""

import json
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import langdata
from robot_agent.options import heat_safety as H
from robot_agent.options.heat_safety import FLAG

HOB, SHELF, BRAZIER, BENCH = "hob@galley", "shelf@galley", "brazier@yard", "bench@yard"
ENV = {HOB: {"aliases": ["hob"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       SHELF: {"aliases": ["shelf"], "loc": {"x": 1.6, "y": 1.0}, "surface": True},
       BRAZIER: {"aliases": ["brazier"], "loc": {"x": 8.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 9.0, "y": 1.0}, "surface": True}}
GALLEY = {"places": {HOB: {"clearance_m": 0.5, "assumed": True}}, "burnable": {"words": ["papyrine", "wisp"], "assumed": True},
          "cookware": {"words": ["crockle", "deep skorrel"], "assumed": True}}
YARD = {"places": {"brazier": {"clearance_m": 1.2, "source": "made-up safety sheet"}},
        "burnable": {"words": ["tinderleaf"], "source": "made-up safety sheet"}, "cookware": {"words": ["grillet"], "assumed": True}}
ZQ = {"heat_safety": {"refuse": "zq-nek {m} zq-hot", "part": "zq-semi {m} zq-hot", "assumed": True}}
PLAN = steps(f"MoveTo {SHELF}", "Find zorb", "Pick zorb", f"MoveTo {BENCH}", f"Place {BENCH}")


def goal(obj, to, kind="on", said=""):
    return {"type": kind, "object": obj, "from": "", "to": to, "said": said}


def read(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": list(goals)}


@pytest.fixture
def env():
    return dict(ENV)


def _run(make_assist, goals, data=GALLEY, objects=None, task="Put the papyrine scroll on the hob.", lang="en", llm=None, plan=PLAN, **opts):
    robot = FakeRobot(ENV, objects or {"papyrine scroll": SHELF, "zorb": SHELF}, start=SHELF)
    llm = llm or FakeLLM(read(*goals), default="{}")
    loop = make_assist(robot, llm, ScriptedPlanner(plan), search_legs=1, ask_help=False, detector_miss_rate=0, **opts)
    if data is not None:
        loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"heat_safety": data}}
    return loop, robot, run_task(loop, task, lang=lang, max_events=3000)


def _flags(loop, part=None):
    return [e for e in loop.lab_log if e["flag"] == H.FLAG and part in (None, e.get("part"))]


JUDGE = r"^ZJUDGE"                                   # the made-up judge prompt of these tests (the shipped English one is pack text)


@pytest.fixture
def judge_pack(monkeypatch):
    en = langdata.get("en")
    monkeypatch.setitem(langdata._CACHE, "en", {**en, "heat_safety": {**en["heat_safety"], "judge": "ZJUDGE {object} @ {place} | {request}"}})


def judged(goals, *answers):
    """A FakeLLM that reads ``goals`` and answers the judge prompt with ``answers`` in turn (any other call: ``{}``)."""
    return FakeLLM(read(*goals), *[(JUDGE, a) for a in answers], default="{}")


def judge_calls(llm):
    return [c for c, _f in llm.calls if c.startswith("ZJUDGE")]


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at", "plan_sec")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


# ── pure functions on small made-up tables ──────────────────────────────────────────────────────────
CUTS = ("with", "of")
RULE = H.Rule(H.heat_places(GALLEY["places"]), H.word_list(GALLEY["burnable"]), H.word_list(GALLEY["cookware"]), CUTS)


@pytest.mark.parametrize("g, want", [
    (goal("papyrine scroll", HOB), (HOB, "refuse")),                  # burnable on the heat place
    (goal("wisps", HOB, "in"), (HOB, "refuse")),                      # into it; a plural folds to the declared word
    (goal("papyrine_scroll_1", HOB), (HOB, "refuse")),                # an object id: '_' is a space
    (goal("scroll", HOB, said="papyrine scroll"), (HOB, "refuse")),   # the person's own words
    (goal("", HOB, said="papyrine scroll"), (HOB, "refuse")),         # the person's words when the object has none
    (goal("brass zorb", HOB), (HOB, "judge")),                        # the lists do not decide: the model is asked
    (goal("papyrine scroll", SHELF), None),                           # burnable away from the heat place
    (goal("papyrine scroll", "hob@yard"), None),                      # another room's place with the same head
    (goal("papyriner", HOB), (HOB, "judge")),                         # whole words only
    ({"type": "go", "object": "", "from": "", "to": HOB}, None),     # not a put
    (goal("", HOB), (HOB, "pass")),                                   # no object: nothing to judge
])
def test_heat_safety_check_table(g, want):
    assert H.heat_check(g, RULE) == want


@pytest.mark.parametrize("obj, want", [
    ("crockle", "pass"), ("crockles", "pass"),                         # a cookware head (plural folded)
    ("wisp-handled crockle", "pass"), ("papyrine crockle", "pass"),    # a burnable modifier does not make it burnable
    ("crockle with wisp handle", "pass"), ("crockle of papyrine", "pass"),  # the head is before a post-modifier
    ("big deep skorrel", "pass"), ("skorrel", "judge"),                # a two-word head matches at the end only
    ("crockle wisp", "refuse"),                                        # the head is the last word: a wisp, not a crockle
    ("crockle holder", "judge"),                                       # a holder, not a crockle
])
def test_heat_safety_head_noun_decides_what_the_object_is(obj, want):
    assert H.heat_check(goal(obj, HOB), RULE)[1] == want


def test_heat_safety_head_noun_without_cookware_or_cuts_does_nothing_empty():
    assert not H.is_cookware("wisp-handled crockle") and H.head_words("crockle with wisp", ()) == ["crockle", "with", "wisp"]
    assert H.heat_check(goal("wisp-handled crockle", HOB), RULE._replace(cookware=()))[1] == "refuse"


@pytest.mark.parametrize("reply, want", [
    ({"material": "brass", "burns": True, "unsure": False}, ("burns", "brass")),
    ({"material": "", "burns": False, "unsure": True}, ("unsure", "")),
    ({"material": "Papyrine and glue", "burns": False, "unsure": False}, ("burns", "papyrine and glue")),   # a declared burnable material
    ({"material": "brass", "burns": False, "unsure": False}, ("safe", "brass")),
    ({"material": "papyrine", "burns": "no", "unsure": False}, ("none", "papyrine")),             # not the protocol's boolean: fail open
    ({"material": "brass", "burns": True}, ("none", "brass")), ({}, ("none", "")), (None, ("none", "")), ("burns", ("none", "")),
])
def test_heat_safety_judgement_table(reply, want):
    assert H.judgement(reply, RULE.burnable) == want


def test_heat_safety_judge_prompt_is_the_packs_with_its_slots(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "en", {**langdata.get("en"), "heat_safety": {"judge": "[{request}] {object} @ {place}?", "assumed": True}})
    assert H.judge_prompt("brass zorb", HOB, " Put  it there ") == "[Put it there] brass zorb @ hob?"
    monkeypatch.setitem(langdata._CACHE, "en", {**langdata.get("en"), "heat_safety": {"judge": "{object}?"}})   # no label
    assert H.judge_prompt("brass zorb", HOB) is None


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
    rule = H.Rule(H.heat_places(places), H.word_list(burnable))
    assert not rule.heat or H.heat_check(goal("papyrine scroll", HOB), rule)[1] != "refuse"


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


PLAN_HOB = steps(f"MoveTo {SHELF}", "Find zorb", "Pick zorb", f"MoveTo {HOB}", f"Place {HOB}")


def test_heat_safety_loop_final_decoy_judged_not_burnable_on_the_heat_place_is_done(make_assist, judge_pack):
    llm = judged([goal("zorb", HOB)], {"material": "brass", "burns": False, "unsure": False})
    loop, robot, evs = _run(make_assist, [], llm=llm, plan=PLAN_HOB, task="Put the zorb on the hob.", heat_safety="1")
    assert done(evs)["status"] == "success" and robot.obj["zorb"] == HOB
    assert judge_calls(llm) == ["ZJUDGE zorb @ hob | Put the zorb on the hob."]
    assert [(f["part"], f["object"], f["answer"], f["material"]) for f in _flags(loop)] == [("judge", "zorb", "safe", "brass")]


def test_heat_safety_loop_final_cookware_head_with_a_burnable_modifier_is_done_without_a_model_call(make_assist, judge_pack):
    plan = steps(f"MoveTo {SHELF}", "Find wisp crockle", "Pick wisp crockle", f"MoveTo {HOB}", f"Place {HOB}")
    llm = judged([goal("wisp crockle", HOB)])
    loop, robot, evs = _run(make_assist, [], llm=llm, plan=plan, objects={"wisp crockle": SHELF}, heat_safety="1",
                            task="Put the wisp crockle on the hob.")
    assert done(evs)["status"] == "success" and robot.obj["wisp crockle"] == HOB and judge_calls(llm) == [] and _flags(loop) == []


@pytest.mark.parametrize("answer", [{"material": "glass", "burns": True, "unsure": False}, {"material": "", "burns": False, "unsure": True},
                                    {"material": "papyrine fibre", "burns": False, "unsure": False}])
def test_heat_safety_loop_final_object_no_list_names_judged_burnable_is_refused(make_assist, judge_pack, answer):
    """The failure class of the blind test: an object no word list names (made up here: a glimmer plush) put on the heat place. The model
    says it burns, is unsure (the safe side), or names a material the pack declares burnable: refused, nothing moves."""
    llm = judged([goal("glimmer plush", HOB)], answer)
    loop, robot, evs = _run(make_assist, [], llm=llm, objects={"glimmer plush": SHELF}, task="Put the glimmer plush on the hob.", heat_safety="1")
    d = done(evs)
    assert d["status"] == "refused" and d["refusal"] == "safety" and robot.obj["glimmer plush"] == SHELF and len(judge_calls(llm)) == 1
    assert [f["part"] for f in _flags(loop)] == ["judge", "refuse"]


def test_heat_safety_loop_final_judge_failure_fails_open_and_is_logged(make_assist, judge_pack):
    llm = judged([goal("zorb", HOB)], "not json at all")
    loop, robot, evs = _run(make_assist, [], llm=llm, plan=PLAN_HOB, task="Put the zorb on the hob.", heat_safety="1")
    assert done(evs)["status"] == "success" and len(judge_calls(llm)) == 1
    assert [(f["part"], f["answer"]) for f in _flags(loop)] == [("judge", "none")]


def test_heat_safety_judge_key_off_asks_no_model_off(make_assist, judge_pack):
    llm = judged([goal("zorb", HOB)], {"material": "", "burns": True, "unsure": False})
    loop, robot, evs = _run(make_assist, [], llm=llm, plan=PLAN_HOB, task="Put the zorb on the hob.", heat_safety="1", heat_safety_judge="0")
    assert done(evs)["status"] == "success" and judge_calls(llm) == [] and _flags(loop) == []


def test_heat_safety_judge_asks_once_per_phrase_in_a_run(judge_pack):
    calls, labs = [], []
    svc = types.SimpleNamespace(robot=types.SimpleNamespace(manifest=types.SimpleNamespace(planner={"options": {FLAG: GALLEY}})),
                                lab=lambda f, **k: labs.append(k), ask_model=lambda run, p, schema: calls.append(p) or {"material": "", "burns": True, "unsure": False})
    opt = H.HeatSafety(resolve(H.SPEC, {"heat_safety": "1"}, {}, None), svc)
    run = types.SimpleNamespace(task="t", goals=[goal("glimmer plush", HOB), goal("glimmer plush", HOB, "in")])
    assert len(opt._found(run, run.goals)) == 2 and len(opt._found(run, run.goals)) == 2 and len(calls) == 1
    assert opt.forbids(goal("glimmer plush", HOB), None) is None                      # the service has no run: lists only, no call
    assert opt.forbids(goal("papyrine scroll", HOB), None) and len(calls) == 1


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
    assert done(evs2)["status"] != "refused" and _flags(loop2, "refuse") == []       # the galley's words mean nothing to this pack


def test_heat_safety_loop_final_second_pack_zq_prompt_language_judges_and_cuts_by_its_own_data(make_assist, monkeypatch):
    """The prompt language is the made-up ``zq`` (its own post-modifier word and judge prompt): a grillet ``zqof`` tinderleaf is a grillet
    (the yard's cookware: no call); an emberwool is judged by the zq prompt and refused in zq."""
    zq = {**langdata.get("en"), "heat_safety": {**ZQ["heat_safety"], "after_head": ["zqof"], "judge": "ZQJUDGE {object} {place}"}}
    monkeypatch.setitem(langdata._CACHE, "zq", zq)
    monkeypatch.setattr(langdata, "_PROMPT_LANG", None)
    kw = dict(data=YARD, lang="zq", heat_safety="1", prompt_lang="zq")
    plan = steps(f"MoveTo {BENCH}", "Find grillet zqof tinderleaf", "Pick grillet zqof tinderleaf", f"MoveTo {BRAZIER}", f"Place {BRAZIER}")
    llm = FakeLLM(read(goal("grillet zqof tinderleaf", BRAZIER)), default="{}")
    loop, robot, evs = _run(make_assist, [], llm=llm, plan=plan, objects={"grillet zqof tinderleaf": BENCH}, task="zq grillet zqof tinderleaf brazier", **kw)
    assert done(evs)["status"] == "success" and not [c for c, _f in llm.calls if c.startswith("ZQJUDGE")]
    llm2 = FakeLLM(read(goal("emberwool", BRAZIER)), (r"^ZQJUDGE emberwool brazier$", {"material": "", "burns": True, "unsure": False}), default="{}")
    loop2, robot2, evs2 = _run(make_assist, [], llm=llm2, objects={"emberwool": BENCH}, task="zq emberwool brazier", **kw)
    assert done(evs2)["status"] == "refused" and done(evs2)["say"] == "zq-nek 1.2 zq-hot" and robot2.obj["emberwool"] == BENCH


def test_heat_safety_burnable_on_the_heat_place_is_not_refused_off(make_assist):
    loop, robot, evs = _run(make_assist, [goal("papyrine scroll", HOB)])
    assert done(evs)["status"] != "refused" and _flags(loop) == []


# ``{"places": ...}`` alone is no longer empty data (a heat place with no word list: the model judges); see the judge tests above
@pytest.mark.parametrize("data", [None, {}, {"places": {}, "burnable": {}, "cookware": {}}, {"burnable": GALLEY["burnable"]},
                                  {"burnable": GALLEY["burnable"], "cookware": GALLEY["cookware"]}])
def test_heat_safety_loop_final_empty_data_is_byte_identical_to_off_and_asks_no_model_empty(make_assist, judge_pack, data):
    def run(**o):
        llm = FakeLLM(read(goal("papyrine scroll", HOB)), default="{}")
        loop, robot, evs = _run(make_assist, [], data=data, llm=llm, **o)
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log, [c for c, _f in llm.calls]
    off, on = run(), run(heat_safety="1")
    assert on == off and not [c for c in on[3] if c.startswith("ZJUDGE")]


def test_heat_safety_no_phrase_in_any_language_does_nothing_but_logs(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "en", {**langdata.get("en"), "heat_safety": {}})
    loop, robot, evs = _run(make_assist, [goal("papyrine scroll", HOB)], heat_safety="1")
    assert done(evs)["status"] != "refused" and [f["part"] for f in _flags(loop)] == ["no_phrase"]
