"""Option ``safety_profile`` (D4 layer L21; default off): hazard rules declared as the robot's data (``planner.options.safety_profile.profile``),
with a cited source and a map distance (``within_m``), checked by option ``hazard``'s one gate through the service ``safety_rule``.

Made-up pack: a made-up language ``xx``; a class of things that "flare" (words flimmer, pappel); a place class "glow source" (words glowbox,
ember stand); the rule: a flarable thing never goes on, or less than 1 m from, a glow source; its source is a made-up leaflet with a year.
Nothing here is a real robot's object, place, rule or document."""
from types import SimpleNamespace as NS

from robot_agent.core.options import STOP
from robot_agent.core.options.config import resolve
from robot_agent.core.options.dispatch import Dispatcher
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.safety import load_profile, near_class
from robot_agent.options import hazard as hazard_mod
from robot_agent.options.safety_profile import SPEC

SRC = "Made-up Hearth Leaflet, edition 2, 2031"
PROFILE = {
    "profile": "made_up_hearth", "version": 1,
    "sources": {SRC: {"url": "https://example.invalid/hearth", "opened": "2031-01-01", "quote": "keep what can flare 1 m from a glowbox"}},
    "hazard_classes": {"flarable": {"words": {"xx": {"assumed": [r"\bflimmer\b", r"\bpappel\b"]}}, "sources": [SRC]}},
    "place_classes": {"glow_source": {"words": {"xx": [r"\bglowbox\b", r"\bember ?stand\b"]}, "provenance": {"source": SRC}}},
    "rules": [{"id": "flare_glow", "hazard": "flarable", "places": ["glow_source"], "within_m": 1.0,
               "say": "a flarable thing stays a metre away from a glow source", "provenance": {"source": SRC}}],
}
ENV = {"glowbox@den": {"loc": {"x": 0.0, "y": 0.0}, "aliases": ["glowbox"]},
       "ledge@den": {"loc": {"x": 0.5, "y": 0.0}, "aliases": ["ledge"]},
       "far rack@den": {"loc": {"x": 3.0, "y": 0.0}, "aliases": ["far rack"]},
       "ledge@nook": {"loc": {"x": 0.4, "y": 0.1}, "aliases": ["nook ledge"]},
       "pal": {"person": "pal", "loc": {"x": 0.2, "y": 0.0}, "aliases": ["pal"]},
       "mat@den": {"aliases": ["mat"]}}


def goal(obj, to, kind="on"):
    return {"type": kind, "object": obj, "from": "", "to": to}


def gate(overrides, profile=PROFILE, planner=None):
    """Option ``hazard`` with the services a loop gives it: the ``safety_rule`` answer of the enabled options (a real Dispatcher)."""
    planner = planner if planner is not None else {"options": {"safety_profile": {"profile": profile}}}
    svc = NS(robot=NS(manifest=NS(safety={}, safety_profile=None, planner=planner)), labs=[])
    svc.lab = lambda *a, **k: svc.labs.append((a, k))
    svc.finish = lambda run, status, **kw: {"event": "done", "status": status, **kw}
    disp = Dispatcher(svc, overrides, {}, planner)
    svc.safety_rule = lambda g, p: disp.query("safety_rule", g, p)
    return disp.option("hazard"), svc, Places(dict(ENV))


def run_gate(opt, ps, goals):
    run = NS(goals=list(goals), places=ps, ev=lambda etype, **kw: (etype, kw))
    gen, out = opt.on_goals(run), None
    try:
        while True:
            next(gen)
    except StopIteration as e:
        out = e.value
    return out, run


def test_safety_profile_off_by_default_and_the_gate_does_not_see_its_rules_off():
    assert not resolve(SPEC, {}, {}, None)["enabled"] and resolve(SPEC, {"safety_profile": "1"}, {}, None)["enabled"]
    opt, svc, ps = gate({})
    assert opt.verdict(goal("flimmer", "glowbox@den"), ps) is None and svc.labs == []


def test_safety_profile_no_profile_an_empty_one_or_a_foreign_one_forbids_nothing_empty():
    foreign = {**PROFILE, "hazard_classes": {"flarable": {"words": {"yy": {"assumed": [r"\bsnorkel\b"]}}, "sources": [SRC]}}}
    for planner in ({}, {"options": {"safety_profile": {"profile": {}}}}, {"options": {"safety_profile": {"profile": foreign}}}):
        opt, svc, ps = gate({"safety_profile": "1"}, planner=planner)
        assert opt.verdict(goal("flimmer", "glowbox@den"), ps) is None and svc.labs == []


def test_refuse_a_flarable_thing_on_a_glow_source_and_near_one_within_m():
    opt, svc, ps = gate({"safety_profile": "1"})
    for to in ("glowbox@den", "ledge@den"):                                 # on it; 0.5 m from it
        v = opt.verdict(goal("pappel roll", to), ps)
        assert v and v.rule == "flare_glow" and opt.forbids(goal("pappel roll", to), ps) == PROFILE["rules"][0]["say"]
    assert ("safety_profile",) in [a for a, _k in svc.labs]


def test_over_refusal_decoy_table_nothing_else_is_refused():
    """The decoys: a thing that does not flare next to the glow source, a flarable thing far from it, one in the next room that is near on
    the map, one handed to a person standing near it, one to a place with no position: none is refused."""
    opt, svc, ps = gate({"safety_profile": "1"})
    for g in (goal("stone mug", "ledge@den"), goal("stone mug", "glowbox@den"), goal("flimmer", "far rack@den"),
              goal("flimmer", "ledge@nook"), goal("flimmer", "pal"), goal("flimmer", "mat@den")):
        assert opt.verdict(g, ps) is None, g
    assert svc.labs == []


def test_source_a_rule_cites_a_declared_source_or_the_whole_profile_is_refused_and_forbids_nothing():
    prof, errs = load_profile(PROFILE)
    assert not errs and prof.rules[0].provenance == "sourced" and prof.rules[0].within_m == 1.0
    bad = {**PROFILE, "rules": [{**PROFILE["rules"][0], "provenance": {"source": "an undeclared leaflet"}}]}
    opt, svc, ps = gate({"safety_profile": "1"}, profile=bad)
    assert opt.verdict(goal("flimmer", "glowbox@den"), ps) is None
    assert [k["errors"][0] for _a, k in svc.labs][0].endswith("provenance: must be 'assumed' or {source: <id of sources>}, got {'source': 'an undeclared leaflet'}")


def test_within_m_must_be_a_distance_of_a_place_rule():
    for v in (-1, True, "near"):
        _p, errs = load_profile({**PROFILE, "rules": [{**PROFILE["rules"][0], "within_m": v}]})
        assert any("within_m" in e for e in errs), v
    recv = {**PROFILE, "receiver_classes": {"kid": {"words": {"xx": ["kid"]}, "provenance": "assumed"}},
            "rules": [{"id": "r", "hazard": "flarable", "receivers": ["kid"], "within_m": 1.0, "say": "no", "provenance": "assumed"}]}
    assert any("within_m" in e for e in load_profile(recv)[1])


def test_near_class_measures_on_the_map_in_the_same_room_and_fails_open():
    prof, _e = load_profile(PROFILE)
    ps = Places(dict(ENV))
    assert near_class(ps, "ledge@den", ("glow_source",), 1.0, prof) == "glowbox@den"
    assert near_class(ps, "ledge@den", ("glow_source",), 0.5, prof) == ""           # at 0.5 m: not LESS than 0.5 m
    assert near_class(ps, "ledge@den", ("glow_source",), 0.0, prof) == ""           # no radius
    assert near_class(ps, "mat@den", ("glow_source",), 9.0, prof) == ""             # no position
    assert near_class(ps, "ledge@den", ("glow_source",), 1.0) == ""                 # empty profile: no class


def test_refuse_the_whole_request_or_drop_the_part_through_the_hazard_gate():
    """The failure class: a request that puts a thing that can burn on a heat source was carried out. Alone, it is refused (STOP); with
    another goal, that part is dropped and the rest kept."""
    opt, svc, ps = gate({"safety_profile": "1"})
    out, run = run_gate(opt, ps, [goal("flimmer", "glowbox@den")])
    assert out == STOP and run.opt["hazard"]["refusals"][0]["rule"] == "flare_glow"
    out, run = run_gate(opt, ps, [goal("pappel", "ledge@den"), goal("stone mug", "ledge@den")])
    assert out is None and [g["object"] for g in run.goals] == ["stone mug"]
    assert opt.refused_goals(run)[0]["object"] == "pappel"


def test_hazard_rules_off_turns_the_declared_rules_off_too_off():
    opt, svc, ps = gate({"safety_profile": "1", "hazard_rules": "off"})
    assert opt.verdict(goal("flimmer", "glowbox@den"), ps) is None


def test_hazard_without_the_service_keeps_its_old_answer_off():
    cfg = resolve(hazard_mod.SPEC, {}, {}, None)
    svc = NS(robot=NS(manifest=NS(safety={}, safety_profile=None)), lab=lambda *a, **k: None)
    assert hazard_mod.Hazard(cfg, svc).verdict(goal("flimmer", "glowbox@den"), Places(dict(ENV))) is None


# ── the real loop ─────────────────────────────────────────────────────────────────────────────────────────────────────
GLOW = {"default_mode": "left", "height": 0.5, "loc": {"x": -0.8, "y": -2.3, "rz": 0.0}, "aliases": ["glowbox"]}


def _loop(make_assist, env, goals, plan, on=True):
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner
    env["glowbox@kitchen"] = dict(GLOW)                  # 0.74 m from the dining table, 1.36 m from the kitchen shelf
    robot = FakeRobot(env, {"flimmer": "counter@kitchen", "stone mug": "counter@kitchen"}, start="counter@kitchen", answers=["yes", "yes"])
    llm = FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": goals})
    loop = make_assist(robot, llm, ScriptedPlanner(plan), detector_miss_rate=0, **({"safety_profile": "1"} if on else {}))
    loop.robot.manifest.planner.setdefault("options", {})["safety_profile"] = {"profile": PROFILE}
    return loop, robot


def test_loop_final_refuses_the_flarable_thing_near_the_glow_source_and_leaves_it_where_it_was(make_assist, env):
    from fakes import done, run_task, steps
    goals = [{"type": "on", "object": "flimmer", "from": "counter", "to": "dining table"}]
    plan = steps("MoveTo counter@kitchen", "Find flimmer", "Pick flimmer", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    loop, robot = _loop(make_assist, env, goals, plan)
    d = done(run_task(loop, "Put the flimmer on the dining table.", max_events=3000))
    assert d["status"] == "refused" and robot.obj["flimmer"] == "counter@kitchen" and robot.holding is None
    assert "a metre away from a glow source" in d.get("say", "") and d["refusals"][0]["rule"] == "flare_glow"
    assert [e["part"] for e in loop.lab_log if e["flag"] == "safety_profile"][:2] == ["loaded", "rule"]


def test_loop_final_off_the_same_request_is_carried_out_as_before_off(make_assist, env):
    from fakes import done, run_task, steps
    goals = [{"type": "on", "object": "flimmer", "from": "counter", "to": "dining table"}]
    plan = steps("MoveTo counter@kitchen", "Find flimmer", "Pick flimmer", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    loop, robot = _loop(make_assist, env, goals, plan, on=False)
    d = done(run_task(loop, "Put the flimmer on the dining table.", max_events=3000))
    assert d["status"] == "success" and robot.obj["flimmer"] == "dining table@kitchen" and not d.get("refusals")


def test_loop_final_over_refusal_decoys_both_are_carried_out(make_assist, env):
    """The decoy table in the real loop: the stone mug goes next to the glow source, the flimmer to the shelf 1.36 m away; both are done."""
    from fakes import done, run_task, steps
    goals = [{"type": "on", "object": "stone mug", "from": "counter", "to": "dining table"},
             {"type": "on", "object": "flimmer", "from": "counter", "to": "shelf@kitchen"}]
    plan = steps("MoveTo counter@kitchen", "Find stone mug", "Pick stone mug", "MoveTo dining table@kitchen", "Place dining table@kitchen",
                 "MoveTo counter@kitchen", "Find flimmer", "Pick flimmer", "MoveTo shelf@kitchen", "Place shelf@kitchen")
    loop, robot = _loop(make_assist, env, goals, plan)
    d = done(run_task(loop, "Put the stone mug on the dining table and the flimmer on the kitchen shelf.", max_events=3000))
    assert d["status"] == "success" and robot.obj == {"stone mug": "dining table@kitchen", "flimmer": "shelf@kitchen"}
    assert not d.get("refusals") and [e["part"] for e in loop.lab_log if e["flag"] == "safety_profile"] == ["loaded"]
