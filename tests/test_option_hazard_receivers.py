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
    return Hazard(cfg, NS(robot=robot)), Places(dict(ENV))


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
