"""Option ``answer_align`` (default OFF) and the key ``distinct`` of ``ask_agree``: an answer or a correction is matched to a reading by the words
that tell the readings apart (``names.distinguishing``). Made-up worlds (a loft, a den), made-up objects (jars, a tin box, a reed basket) and a
made-up agglutinative language ``zq`` (a stem plus a particle or an ending: ``-ka`` / ``-ra``; ``ja`` = yes, ``zo`` = no, ``mizuni`` = to me, ``m`` = me (one letter),
``vel`` its "or"); no benchmark text, no benchmark id."""
import json
import re
from types import SimpleNamespace as NS

import pytest

from fakes import FakeRobot, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import dialog_converge, langdata, names, speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options import ask_agree as AA
from robot_agent.options.answer_align import SPEC, AnswerAlign, named_any, options_of, other_script, realign, yes_or_no

ENV = {"ledge@den": {"aliases": ["ledge"], "loc": {"x": 1.0, "y": 0.0, "rz": 0.0}}, "crate@den": {"aliases": ["crate"], "loc": {"x": 2.0, "y": 1.0, "rz": 0.0}},
       "rack@loft": {"aliases": ["rack"], "loc": {"x": 5.0, "y": 0.0, "rz": 0.0}}, "nook@loft": {"aliases": ["nook"], "loc": {"x": 6.0, "y": 1.0, "rz": 0.0}},
       "pat@den": {"person": "pat", "speaker": True, "aliases": ["me"]}}
ZQ = {"particles": ["ka"], "grammar": {"noun_particles": ["ka"], "sep_or": " vel ", "speaker_forms": ["mizuni", "m"]},
      "dialogue": {"yes": ["ja"], "no": ["zo"], "polite": ["pa"], "fillers": ["ee"]}, "dialogue_ext": {"polite_endings": ["ra"]},
      "speech": {"q_pick_one": "{text} ru?", "q_which_reading": "{first} vel {second} ru?", "assumed": True}}


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    return ("zq",)


def mv(obj, to, **kw):
    return {"type": "on", "object": obj, "from": "", "to": to, **kw}


def option(svc=None, **keys):
    cfg = resolve(SPEC, {"answer_align": "1", **{f"answer_align.{k}": v for k, v in keys.items()}}, {}, None)
    svc = svc or NS(labs=[], asked=[])
    svc.lab = lambda *a, **k: svc.labs.append(k)
    return AnswerAlign(cfg, svc), svc


def run_of(dialogue=(), goals=(), lang="en", corrected=0):
    return NS(dialogue=list(dialogue), goals=[dict(g) for g in goals], lang=lang, corrected=corrected, places=Places(ENV),
              ev=lambda name, **f: {"event": name, **f})


# ── the words that tell readings apart (core names) ──────────────────────────────────────────────────────────────────
def test_distinguish_one_word_of_a_name_tells_two_readings_apart_and_a_shared_word_names_neither():
    a, b = names.reading_words(mv("tin box", "rack@loft"), Places(ENV)), names.reading_words(mv("reed basket", "rack@loft"), Places(ENV))
    assert names.distinguishing(a, b) == {"tin", "box"} and names.distinguishing(b, a) == {"reed", "basket"}
    assert names.which_named(["The tin, thanks."], [a, b]) == 0 and names.which_named(["reed"], [a, b]) == 1
    assert names.which_named(["on the rack"], [a, b]) is None                       # a word both readings share: nothing is decided
    assert names.which_named(["the tin or the reed"], [a, b]) is None               # two readings named: nothing is decided
    c = names.reading_words(mv("tin box", "nook@loft"), Places(ENV))
    assert names.which_named(["the nook"], [a, c]) == 1                              # the place's alias tells them apart


def test_speaker_the_word_the_person_uses_for_themself_names_the_reading_to_them(zq):
    sk, langs = "pat@den", zq + ("en",)
    to_me, to_rack = names.reading_words(mv("jar", sk), Places(ENV), langs, sk), names.reading_words(mv("jar", "rack@loft"), Places(ENV), langs, sk)
    assert names.SPEAKER in to_me and names.SPEAKER not in to_rack
    assert names.which_named(["mizunira"], [to_me, to_rack], langs) == 0             # the speaker word with a declared ending
    assert names.which_named(["mra"], [to_me, to_rack], langs) == 0                  # a one-letter speaker word with an ending
    assert names.which_named(["m rack"], [to_me, to_rack], langs) == 1               # alone it may be another word: it names nothing
    assert names.which_named(["give it to me"], [to_me, to_rack], ("en",)) == 0       # the English pack's own speaker form
    assert names.which_named(["you"], [to_me, to_rack], ("en",)) is None              # a weak form (the robot's word for its addressee) names nothing
    assert names.SPEAKER in names.reading_words(mv("jar", "me"), Places(ENV), ("en",))  # a speaker word as the destination itself


def test_made_up_agglutinative_language_a_name_word_with_one_particle_or_ending_is_said(zq):
    langs = zq
    tin, reed = names.words("tin box", langs), names.words("reed basket", langs)
    assert names.says(["tinka"], tin, langs) and names.says(["tinra"], tin, langs) and names.says(["tin"], tin, langs)
    assert not names.says(["tinkara"], tin, langs)                                   # two endings: not declared as one
    assert not names.says(["tinbo"], tin, langs)                                     # an undeclared ending
    assert names.which_named(["reedka pa"], [tin, reed], langs) == 1
    assert names.endings(langs) == ("ka", "ra") and names.endings(("en",)) == ()     # English declares none: whole words only
    assert yes_or_no("ja pa", langs) and yes_or_no("zora", langs) and not yes_or_no("tinka", langs)
    assert options_of("tinka vel reedka?", langs) == (["tinka", "reedka"], " vel ")


# ── reask: a yes or a no to an either-or ─────────────────────────────────────────────────────────────────────────────
def test_either_or_a_bare_yes_is_asked_again_once_naming_the_options():
    opt, svc = option()
    run = run_of([("robot", "The red jar or the teal jar?"), ("user", "Yes, that's right.")])
    q = opt._reask(run)
    assert q is not None and q.key == "q_pick_one" and q.ask["options"] == ["The red jar", "the teal jar"]
    assert str(q) == "Which one do you mean: The red jar or the teal jar?"
    assert opt._reask(run) is None                                                   # once per run
    for ans in ("The teal one, yes.", "Both of them.", "the jar"):                  # names one (or both / none: no yes or no word) -> not asked
        opt2, _ = option()
        assert opt2._reask(run_of([("robot", "The red jar or the teal jar?"), ("user", ans)])) is None, ans
    q2 = option()[0]._reask(run_of([("robot", speech.LocalText("q_confirm", {"summary": "I will move the red jar or the teal jar."})),
                                     ("user", "yes")]))
    assert q2 is None                                                                # a read-back is no either-or
    declared = speech.LocalText("q_which_known", {"object": "jar"}, ask={"intent": "choose", "options": ["red jar", "teal jar"]})
    assert option()[0]._reask(run_of([("robot", declared), ("user", "no")])).ask["options"] == ["red jar", "teal jar"]


def test_either_or_an_i_dont_know_is_not_one_and_a_key_off_asks_nothing():
    svc = NS(labs=[], unsure_leftover=lambda run, t: "" if "know" in t else None)
    opt, _ = option(svc)
    assert opt._reask(run_of([("robot", "The red jar or the teal jar?"), ("user", "No, I don't know.")])) is None
    opt, _ = option(reask=False)
    run = run_of([("robot", "The red jar or the teal jar?"), ("user", "yes")])
    assert list(opt.on_heard(run)) == [] and not opt._state(run)


def test_either_or_made_up_script_an_answer_with_own_words_in_another_script_is_not_asked_again(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "qy", {"script": {"letters": "[\u03b1-\u03c9]"}, "dialogue": {"yes": ["\u03bd\u03b1"], "no": ["\u03bf\u03c7\u03b9"]},
                                                 "speech": {"q_pick_one": "{text} ;", "assumed": True}})
    yes, own = "\u03bd\u03b1", "\u03ba\u03bf\u03ba\u03ba\u03b9\u03bd\u03bf"          # a made-up yes word and a made-up colour word in a made-up script
    ask = [("robot", "The red jar or the teal jar?")]
    assert option()[0]._reask(run_of(ask + [("user", yes + " ee")], lang="qy")) is not None       # a bare yes in its own script
    assert option()[0]._reask(run_of(ask + [("user", f"{yes}, {own}")], lang="qy")) is None       # a word of its own: maybe an option
    assert other_script(f"{yes} {own}", ["red jar", "teal jar"], ("qy",)) and not other_script(yes, ["red jar", "teal jar"], ("qy",))


def test_either_or_in_a_made_up_language_is_asked_with_its_own_phrase_and_not_without_one(zq, monkeypatch):
    opt, svc = option()
    q = opt._reask(run_of([("robot", "tinka vel reedka?"), ("user", "ja")], lang="zq"))
    assert q is not None and speech.render(q, "zq") == "tinka vel reedka ru?"
    monkeypatch.setitem(langdata._CACHE, "zq", {k: v for k, v in ZQ.items() if k != "speech"})
    opt, svc = option()
    assert opt._reask(run_of([("robot", "tinka vel reedka?"), ("user", "ja")], lang="zq")) is None
    assert svc.labs == [{"rule": "reask_no_phrase"}]


# ── correction: only the goal the correction names changes ───────────────────────────────────────────────────────────
def test_correction_that_names_one_object_leaves_the_other_goal_where_it_was():
    prev = [{"type": "in", "object": "tin box", "from": "", "to": "crate@den"}]
    now = [mv("tin box", "ledge@den"), mv("reed basket", "ledge@den")]
    out = realign(prev, now, ["No, the reed basket on the ledge."], {}, ("en",))
    assert out == [{"type": "in", "object": "tin box", "from": "", "to": "crate@den"}, mv("reed basket", "ledge@den")]
    assert realign(prev, now, ["No, both on the ledge."], {}, ("en",)) is None          # names no object: nothing decided
    assert realign(prev, now, ["No, the tin and the reed on the ledge."], {}, ("en",)) is None     # two objects: nothing decided
    assert realign(prev, now, ["No, the tin box on the ledge."], {}, ("en",)) is None  # it names the goal that changed: as read
    two = [mv("tin box", "rack@loft"), mv("reed basket", "rack@loft")]
    assert realign(two, [mv("tin box", "nook@loft"), mv("reed basket", "rack@loft")], ["No, the reed one."], {}, ("en",)) is None   # = the refused reading
    moved = [{"type": "on", "object": "tin box", "from": "crate@den", "to": "pat@den"}, mv("reed basket", "pat@den")]
    assert realign([mv("tin box", "crate@den", **{"from": "ledge@den"})], moved, ["No, the reed basket to me."], {}, ("en",))[0] == \
        mv("tin box", "crate@den", **{"from": "ledge@den"})                       # the place it is taken from comes back with the destination


def test_correction_matched_by_the_persons_own_words_in_a_made_up_language(zq):
    prev = [mv("tin box", "crate@den", said="ulo"), mv("reed basket", "rack@loft", said="pema")]
    now = [mv("tin box", "nook@loft"), mv("reed basket", "nook@loft")]
    opt, svc = option()
    run = run_of(goals=prev, lang="zq")
    list(opt.on_goals(run))                                                           # the first reading: its words are learnt
    run.goals, run.corrected = [dict(g) for g in now], 1
    run.dialogue = [("robot", "the read-back"), ("user", "zo, pemaka nook")]
    list(opt.on_goals(run))
    assert run.goals == [mv("tin box", "crate@den"), mv("reed basket", "nook@loft")] and svc.labs == [{"rule": "correction", "kept": 1}]
    opt, svc = option(correction=False)
    run = run_of(goals=prev, lang="zq")
    list(opt.on_goals(run))
    run.goals, run.corrected, run.dialogue = [dict(g) for g in now], 1, [("robot", "q"), ("user", "zo, pemaka nook")]
    list(opt.on_goals(run))
    assert run.goals == now and not svc.labs


def test_answer_align_empty_pack_or_no_data_does_nothing_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "xx", {})
    assert options_of("tinka vel reedka?", ("xx",)) == ([], "") and not yes_or_no("ja", ("xx",)) and not named_any("ja", ["a b", "c d"], ("xx",))
    assert names.endings(("xx",)) == () and names.speaker_words(("xx",)) == set() and not names.says(["x"], set(), ("xx",))
    opt, svc = option()
    run = run_of([("robot", "tinka vel reedka?"), ("user", "ja")], lang="xx")
    assert list(opt.on_heard(run)) == [] and list(opt.on_goals(run)) == [] and not svc.labs
    run = run_of(goals=[], corrected=1)
    assert list(opt.on_goals(run)) == [] and run.goals == []


# ── ask_agree key ``distinct``: the read-back's either-or ───────────────────────────────────────────────────────────
def agree(distinct, task, pick=None, lang="en"):
    cfg = resolve(AA.SPEC, {"ask_agree": "1", "ask_agree_distinct": "1" if distinct else "0"}, {}, None)
    svc = NS(labs=[], asked=[], lab=lambda *a, **k: None, unsure_leftover=lambda run, t: "" if "know" in t.lower() else None,
             ask=lambda run, q: svc.asked.append(q) or pick, goal_phrase=lambda g, p: f"bring the {g['object']}")
    first, now = [mv("tin box", "pat@den")], [mv("reed basket", "pat@den")]
    run = NS(places=Places(ENV), goals=now, readings=["a", "b"], rejected=[], confirms=2, unsure=None, task=task, lang=lang,
             dialogue=[("user", "The tin, please."), ("user", "No, the reed basket.")], patched=None, allow_repeat=False)
    run.__dict__["opt"] = {"ask_agree": {"first": first, "bare": set(), "content": set(), "used": False, "asked": False}}
    return AA.AskAgree(cfg, svc), run, svc


def test_distinguish_in_the_read_back_either_or_the_earlier_value_said_by_one_word_is_asked_ask_agree_distinct():
    opt, run, svc = agree(True, "Bring me something to hold.", pick="the tin")
    assert opt.unrefused(run, "I don't know") is False and len(svc.asked) == 1      # "the tin" said the earlier reading: the either-or
    assert run.patched == [mv("tin box", "pat@den")] and run.allow_repeat is True
    opt, run, svc = agree(False, "Bring me something to hold.", pick="the tin")
    assert opt.unrefused(run, "I don't know") is False and not svc.asked           # key off: the whole name was never said, no question


def test_distinguish_an_object_changed_with_its_own_place_and_words_is_one_change_ask_agree_distinct():
    first = [mv("tin box", "pat@den", **{"from": "crate@den", "said": "tin"})]
    now = [mv("reed basket", "pat@den", **{"from": "rack@loft"})]
    assert AA.one_change(first, now) is None and AA.object_change(first, now) == (0, "object")
    assert AA.object_change([mv("tin box", "nook@loft")], [mv("reed basket", "rack@loft")]) is None    # the destination changed too: two changes
    for distinct, asked in ((True, 1), (False, 0)):
        opt, run, svc = agree(distinct, "Bring me something to hold.", pick="the tin")
        run.__dict__["opt"]["ask_agree"]["first"], run.goals = [dict(g) for g in first], [dict(g) for g in now]
        assert opt.unrefused(run, "I don't know") is False and len(svc.asked) == asked
        assert run.patched == ([dict(first[0])] if distinct else None)                # the tin box comes back with its own place


def test_distinguish_an_earlier_name_all_of_whose_words_the_current_reading_shares_is_not_said_ask_agree_distinct():
    first, now = [mv("jar", "pat@den")], [mv("teal jar", "pat@den", **{"from": "rack@loft"})]
    for distinct, asked in ((True, 0), (False, 0)):
        opt, run, svc = agree(distinct, "Bring me a jar.", pick="the jar")
        run.dialogue = [("user", "The red jar."), ("user", "No, the teal one.")]
        run.__dict__["opt"]["ask_agree"]["first"], run.goals = [dict(g) for g in first], [dict(g) for g in now]
        assert opt.unrefused(run, "I don't know") is False and len(svc.asked) == asked and run.patched is None
    opt, run, svc = agree(True, "Bring me a jar.")
    assert not opt._said(run, mv("jar", "pat@den"), mv("teal jar", "pat@den"), "object")    # "jar" said, but "teal jar" has it too
    opt, run, svc = agree(False, "Bring me a jar.")
    assert opt._said(run, mv("jar", "pat@den"), mv("teal jar", "pat@den"), "object")        # off: the whole phrase, as before


def test_distinguish_the_either_or_answer_by_the_readings_words_and_a_yes_picks_nothing(zq):
    opt, run, svc = agree(True, "Bring me something.", pick="reedka pa", lang="zq")
    run.dialogue = [("user", "tinka pa"), ("user", "zo, reedka")]
    assert opt.unrefused(run, "I don't know") is True and run.patched is None       # the current reading named in zq: it stands
    opt, run, svc = agree(True, "Bring me something.", pick="ja", lang="zq")
    run.dialogue = [("user", "tinka pa"), ("user", "zo, reedka")]
    assert opt.unrefused(run, "I don't know") is False and run.patched is None and run.allow_repeat is False   # a yes names neither


# ── through the real loop: the final state ──────────────────────────────────────────────────────────────────────────
class RuleLLM:
    """The reader of a made-up house: asks "red or teal?", reads a bare yes as the red jar (a pick from nothing), the teal one when said."""

    def __init__(self):
        self.calls = []

    def chat(self, prompt="", format=None, **kw):
        self.calls.append(prompt)
        ans = re.findall(r"-> '([^']*)'", prompt)
        if any("teal" in a.lower() for a in ans):
            out = {"status": "clear", "reason": "", "question": "", "goals": [mv("teal jar", "crate@den")]}
        elif ans:
            out = {"status": "clear", "reason": "", "question": "", "goals": [mv("red jar", "crate@den")]}
        else:
            out = {"status": "ambiguous", "reason": "which jar", "question": "The red jar or the teal jar?", "goals": []}
        return json.dumps(out)

    def chat_guide(self, prompt, guide=None, reuse=False, **kw):
        return self.chat(prompt=prompt, **kw)


class GoalPlanner:
    """Plans the move of the jar its goals name (no model)."""

    def __init__(self):
        self.calls = []

    def generate_plan(self, task, obs, visible_objects):
        jar = "teal jar" if "teal jar" in json.dumps([task, obs], default=str) else "red jar"
        self.calls.append(jar)
        return steps("MoveTo ledge@den", f"Find {jar}", f"Pick {jar}", "MoveTo crate@den", "Place crate@den"), {"method": "rule"}

    def replan(self, *a, **k):
        return [], {"method": "rule"}


def _loop(make_assist, monkeypatch, on, answers):
    import robot_agent.skill_configs as sc
    monkeypatch.setattr(sc, "ENV", ENV)
    robot = FakeRobot(ENV, {"red jar": "ledge@den", "teal jar": "ledge@den"}, start="ledge@den", answers=list(answers))
    loop = make_assist(robot, RuleLLM(), GoalPlanner(), detector_miss_rate=0, **({"answer_align": "1"} if on else {}))
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    return robot, loop, done(run_task(loop, "Put the jar in the crate.", max_events=3000))


def test_loop_a_yes_to_the_either_or_is_asked_again_and_the_final_state_holds_the_jar_named(make_assist, monkeypatch):
    assert "answer_align" in ASSIST_OPTIONS and REGISTRY.get("answer_align").key("enabled").default is False
    robot, loop, d = _loop(make_assist, monkeypatch, True, ["Yes, that's right.", "The teal one."])
    assert any(q.startswith("Which one do you mean") for q in robot.asked), robot.asked
    assert d["status"] == "success" and robot.obj["teal jar"] == "crate@den" and robot.obj["red jar"] == "ledge@den", (d, robot.obj)
    assert [e for e in loop.lab_log if e["flag"] == "answer_align" and e.get("rule") == "reask"]


def test_loop_answer_align_off_the_yes_is_read_as_a_pick_final_state_unchanged_off(make_assist, monkeypatch):
    robot, loop, d = _loop(make_assist, monkeypatch, False, ["Yes, that's right.", "The teal one."])
    assert not any(q.startswith("Which one do you mean") for q in robot.asked) and not [e for e in loop.lab_log if e["flag"] == "answer_align"]
    assert robot.obj["teal jar"] == "ledge@den" and robot.obj["red jar"] == "crate@den", (robot.obj, robot.trace, d)          # the default path: the pick from nothing


class CorrectionLLM:
    """The reader of a made-up house: the tin box on the crate; after "no, the reed basket ... too" it sends BOTH to the nook (the misapplied correction)."""

    def chat(self, prompt="", format=None, **kw):
        ans = re.findall(r"-> '([^']*)'", prompt)
        both = any("reed" in a.lower() for a in ans)
        goals = [mv("tin box", "nook@loft"), mv("reed basket", "nook@loft")] if both else [mv("tin box", "crate@den")]
        return json.dumps({"status": "clear", "reason": "", "question": "", "goals": goals})

    def chat_guide(self, prompt, guide=None, reuse=False, **kw):
        return self.chat(prompt=prompt, **kw)


class LinePlanner:
    """Plans a fetch from the rack for every ``on`` goal of the observation's goal line (no model)."""

    def generate_plan(self, task, obs, visible_objects):
        line = re.search(r"Goals: (.*)", str(obs))
        goals = re.findall(r"on (.+?) to ([\w@]+)", line.group(1)) if line else []
        return steps(*[s for o, to in goals for s in ("MoveTo rack@loft", f"Find {o}", f"Pick {o}", f"MoveTo {to}", f"Place {to}")]), {"method": "rule"}

    def replan(self, *a, **k):
        return [], {"method": "rule"}


@pytest.mark.parametrize("on", [True, False])
def test_correction_loop_final_the_object_the_correction_does_not_name_stays_where_it_was_said(make_assist, monkeypatch, on):
    import robot_agent.skill_configs as sc
    monkeypatch.setattr(sc, "ENV", ENV)
    robot = FakeRobot(ENV, {"tin box": "rack@loft", "reed basket": "rack@loft"}, start="rack@loft",
                      answers=["No, the reed basket goes in the nook too.", "Yes."])
    loop = make_assist(robot, CorrectionLLM(), LinePlanner(), detector_miss_rate=0, confirm_task="1", **({"answer_align": "1"} if on else {}))
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    d = done(run_task(loop, "Put the tin box on the crate.", max_events=3000))
    assert d["status"] == "success" and robot.obj["reed basket"] == "nook@loft", (d, robot.obj)
    assert robot.obj["tin box"] == ("crate@den" if on else "nook@loft"), (robot.obj, robot.asked)      # off: the correction moved the tin box too
    assert robot.asked[-1].startswith("I will put the tin box on the " + ("crate" if on else "nook"))
    assert bool([e for e in loop.lab_log if e["flag"] == "answer_align" and e.get("rule") == "correction"]) is on


def test_correction_settles_only_the_unsaid_issue_of_the_goal_it_kept():
    opt, _ = option()
    run = run_of(goals=[mv("tin box", "crate@den")])
    opt._state(run)["kept"] = {("tin box", "crate@den")}
    issues = [{"kind": "unsaid", "what": "crate", "object": "tin box"}, {"kind": "unsaid", "what": "nook", "object": "reed basket"},
              {"kind": "place", "what": "crate", "keys": ["crate@den"]}]
    goals = [mv("tin box", "crate@den"), mv("reed basket", "nook@loft")]
    assert opt.on_issues(issues, run, goals) == issues[1:]
    assert opt.on_issues(issues, run, [mv("tin box", "nook@loft")]) is None           # the goal was sent elsewhere since: its issue stays
    assert option()[0].on_issues(issues, run_of(), goals) is None                       # nothing kept: nothing settled
