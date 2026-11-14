"""Option ``hazard``, key ``receivers`` (legacy ``hazard_receiver``; default off): a product is refused to a receiver the pack's rule names.
Made-up pack: a product class "glim" (words glim, zonk), a receiver class "tiny_one" (words wibble, tiny), a rule glim x tiny_one with
``enabled_by: hazard.receivers``. Nothing here is a real robot's product or person."""
from types import SimpleNamespace as NS

from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.safety import load_profile
from robot_agent.options.hazard import SPEC, Hazard

PROFILE = """\
profile: made_up_pack
version: 1
checks: ['on']
hazard_classes:
  glim:
    words:
      en:
        assumed: ['\\bglim\\b', '\\bzonk\\b']
receiver_classes:
  tiny_one:
    words:
      en: ['\\b(?:wibble|tiny)\\b']
    fields: [age_group]
    provenance: assumed
rules:
- id: glim_tiny
  hazard: glim
  receivers: [tiny_one]
  say: a glim must not go to a tiny one
  enabled_by: hazard.receivers
  provenance: assumed
"""
ENV = {"wib": {"person": "wib", "aliases": ["wibble"]}, "grown": {"person": "grown", "aliases": ["elder"]},
       "mat@hall": {"aliases": ["mat"]}}


def hazard(tmp_path, receivers, profile_text=PROFILE, **opts):
    cfg = resolve(SPEC, {"hazard_receiver": "1"} if receivers else {}, {}, None)
    prof = None
    if profile_text is not None:
        f = tmp_path / "p.yaml"
        f.write_text(profile_text, encoding="utf-8")
        prof, errs = load_profile(f)
        assert not errs, errs
    robot = NS(manifest=NS(safety={}, safety_profile=prof))
    svc = NS(robot=robot, labs=[])
    svc.lab = lambda *a, **k: svc.labs.append((a, k))
    return Hazard(cfg, svc), Places(dict(ENV))


def goal(obj, to):
    return {"type": "on", "object": obj, "from": "", "to": to}


def test_receivers_is_a_key_of_hazard_and_off_by_default():
    assert not resolve(SPEC, {}, {}, None)["receivers"]
    assert resolve(SPEC, {"hazard_receiver": "1"}, {}, None)["receivers"]
    assert resolve(SPEC, {"hazard.receivers": "1"}, {}, None)["receivers"]


def test_a_product_is_refused_to_a_receiver_the_rule_names(tmp_path):
    opt, ps = hazard(tmp_path, True)
    assert opt.forbids(goal("zonk bottle", "wib"), ps) == "a glim must not go to a tiny one"
    v = opt.verdict(goal("glim jar", "wib"), ps)
    assert v and v.rule == "glim_tiny"


def test_the_rule_needs_both_sides_the_product_and_the_receiver(tmp_path):
    opt, ps = hazard(tmp_path, True)
    assert opt.forbids(goal("zonk bottle", "grown"), ps) is None          # same product, another receiver
    assert opt.forbids(goal("plain cup", "wib"), ps) is None              # same receiver, another product
    assert opt.forbids(goal("zonk bottle", "mat@hall"), ps) is None       # a place is no receiver


def test_receivers_off_keeps_the_default_path(tmp_path):
    opt, ps = hazard(tmp_path, False)
    assert opt.forbids(goal("zonk bottle", "wib"), ps) is None
    assert opt.verdict(goal("zonk bottle", "wib"), ps) is None


def test_a_pack_with_no_profile_or_no_receiver_rules_does_nothing_empty(tmp_path):
    bare, ps = hazard(tmp_path, True, profile_text=None)
    assert bare.forbids(goal("zonk bottle", "wib"), ps) is None
    no_rule = PROFILE.split("rules:")[0] + "rules: []\n"
    opt, ps = hazard(tmp_path, True, profile_text=no_rule)
    assert opt.forbids(goal("zonk bottle", "wib"), ps) is None


def test_a_rule_of_another_pack_names_other_words_and_is_empty_here(tmp_path):
    other = PROFILE.replace("zonk", "plonk").replace("wibble", "snerk")
    opt, ps = hazard(tmp_path, True, profile_text=other)
    assert opt.forbids(goal("zonk bottle", "wib"), ps) is None            # the foreign pack does not know these words


def gate(opt, ps, goals):
    """Drive the gate (``on_goals``) to its end: (its return value, the run)."""
    run = NS(goals=list(goals), places=ps, ev=lambda etype, **kw: (etype, kw))
    gen, out = opt.on_goals(run), None
    try:
        while True:
            next(gen)
    except StopIteration as e:
        out = e.value
    return out, run


def test_hazard_lab_event_names_the_rule_when_the_gate_drops_a_goal(tmp_path):
    opt, ps = hazard(tmp_path, True)
    out, run = gate(opt, ps, [goal("zonk bottle", "wib"), goal("plain cup", "mat@hall")])
    assert out is None and [g["object"] for g in run.goals] == ["plain cup"]
    assert opt.svc.labs == [(("hazard",), {"rules": ["glim_tiny"], "refused": 1, "goals": 2})]


def test_hazard_no_lab_event_when_nothing_is_forbidden_off_and_empty(tmp_path):
    for receivers, text in ((False, PROFILE), (True, None), (True, PROFILE.split("rules:")[0] + "rules: []\n")):
        opt, ps = hazard(tmp_path, receivers, profile_text=text)
        out, run = gate(opt, ps, [goal("zonk bottle", "wib"), goal("plain cup", "mat@hall")])
        assert out is None and len(run.goals) == 2 and opt.svc.labs == []


QUAR = {"profile": "made_up", "version": 1, "checks": ["on", "in"],
        "hazard_classes": {"quar": {"words": {"en": {"assumed": [r"\bquar\b"]}}}},
        "place_classes": {"any_surface": {"words": {"en": [".+"]}, "provenance": "assumed"}},
        "rules": [{"id": "quar_stays", "hazard": "quar", "places": ["any_surface"], "say": "quar stays in its box", "provenance": "assumed"}]}


def test_loop_hazard_drops_the_forbidden_goal_logs_its_rule_and_the_final_state_holds_the_rest(make_assist, env, monkeypatch):
    """A made-up rule forbids "quar" on any surface. Asked for quar and a plain fig, the run ends with the fig moved, the quar untouched, a
    partial-refusal sentence, and one lab event of the gate naming the rule."""
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
    prof, errs = load_profile(QUAR)
    assert not errs
    robot = FakeRobot(env, {"quar": "counter@kitchen", "fig": "counter@kitchen"}, start="counter@kitchen", answers=["yes", "yes"])
    goals = [{"type": "on", "object": o, "from": "counter", "to": "dining table"} for o in ("quar", "fig")]
    plan = steps("MoveTo counter@kitchen", "Find fig", "Pick fig", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    loop = make_assist(robot, FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": goals}), ScriptedPlanner(plan), detector_miss_rate=0)
    monkeypatch.setattr(loop.robot.manifest, "safety_profile", prof, raising=False)
    d = done(run_task(loop, "Put the quar and the fig on the dining table.", max_events=3000))
    assert d["status"] == "success" and d.get("refused_parts") and robot.obj == {"quar": "counter@kitchen", "fig": "dining table@kitchen"}
    assert [e for e in loop.lab_log if e["flag"] == "hazard"] == [{"flag": "hazard", "rules": ["quar_stays"], "refused": 1, "goals": 2}]
