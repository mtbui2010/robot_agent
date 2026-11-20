"""Option ``ask_agree`` (default OFF): no ask loop after a bare no and the same request, an "I don't know" keeps only a clean reading, and an either-or when
the reading changed a value the person gave. Made-up worlds, a made-up agglutinative language ``zq`` (a stem plus an ending; ``zo`` = no, ``mizu`` = I,
``kani`` = do not know, ending ``-ra``) and the shipped English pack; no benchmark text."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import dialog_converge, langdata
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.ask_agree import SPEC, AskAgree, bare_no, clean_leftover, one_change

ENV = {"stool@loft": {"aliases": ["stool"]}, "rack@loft": {"aliases": ["rack"]}, "plinth@loft": {"aliases": ["plinth"]}}
ZQ = {"dialogue": {"fillers": ["ee"], "no": ["zo"], "polite": ["pa"]}, "words": {"first_person": [r"^mizu"]},
      "dialogue_ext": {"answer_unsure": ["kani"], "polite_endings": ["ra"]}}


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    return ("zq",)


def g(obj="mug", to="stool@loft"):
    return {"type": "on", "object": obj, "from": "", "to": to}


def make(first=None, goals=None, unsure=None, answer=None, either=True, restate=True, task="Put the mug on the stool.", dialogue=(), pick=None, limit=None):
    """The option with a fake host: ``answer`` is the "I don't know" text it recognises, ``pick`` what the person says to the either-or."""
    cfg = resolve(SPEC, {"ask_agree": "1", "ask_agree.either_or": either, "ask_agree.restate_confirm": restate}, {}, None)
    asked = []
    svc = NS(lab=lambda *a, **k: svc.labs.append((a, k)), labs=[], asked=asked,
             unsure_leftover=lambda run, t: t.lower().replace(answer, " ").strip(" ,.") if answer and answer in t.lower() else None,
             ask=lambda run, q: asked.append(q) or pick, goal_phrase=lambda goal, places: f"put the {goal['object']} on the {goal['to'].split('@')[0]}",
             readback_left=lambda run: dialog_converge.readback_left(run, True, limit))
    run = NS(places=Places(ENV), goals=goals if goals is not None else [g()], readings=[], rejected=[], confirms=1, unsure=unsure, task=task,
             dialogue=list(dialogue), lang="en", patched=None, allow_repeat=False)
    opt = AskAgree(cfg, svc)
    st = run.__dict__.setdefault("opt", {}).setdefault("ask_agree", {"first": None, "bare": set(), "content": set(), "used": False, "asked": False})
    st["first"] = [dict(x) for x in (first or run.goals)]
    return opt, run, svc


def test_registered_and_off_by_default_nothing_is_active_off(make_assist, env):
    assert "ask_agree" in ASSIST_OPTIONS and REGISTRY.get("ask_agree").key("enabled").default is False
    loop, d = _loop(make_assist, env, on=False, answers=["No.", "Put the mug on the dining table.", "yes"])
    assert not [e for e in loop.lab_log if e["flag"] == "ask_agree"]
    assert not loop.options.provides("on_goals") or loop.options.option("ask_agree") is None
    assert d["status"] == "failed", d                                             # off: the engine asks "which part" and stops


def _loop(make_assist, env, on, answers=(), **extra):
    robot = FakeRobot(env, {"mug": "counter@kitchen"}, start="counter@kitchen", answers=list(answers))
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "counter", "to": "dining table"}]}
    plan = steps("MoveTo counter@kitchen", "Find mug", "Pick mug", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, confirm_task="1", **({"ask_agree": "1"} if on else {}), **extra)
    loop.fake = robot
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    return loop, done(run_task(loop, "Put the mug on the dining table.", max_events=3000))


def test_restate_a_bare_no_then_the_same_request_is_read_back_once_more_and_a_yes_acts(make_assist, env):
    loop, d = _loop(make_assist, env, on=True, answers=["No.", "Put the mug on the dining table.", "yes"])
    assert [e for e in loop.lab_log if e["flag"] == "ask_agree" and e.get("rule") == "restated_after_bare_no"]
    assert d["status"] == "success", d
    assert sum(1 for q in loop.fake.asked if "right?" in q) == 2 and not any("which part" in q.lower() for q in loop.fake.asked)


def test_restate_a_second_no_still_stops_never_counted_as_agreement(make_assist, env):
    loop, d = _loop(make_assist, env, on=True, answers=["No.", "Put the mug on the dining table.", "No."])
    assert d["status"] == "failed", d


def test_restate_once_per_run_and_only_after_a_bare_no_not_after_a_content_refusal():
    opt, run, svc = make(answer="no idea")
    sig_mod = __import__("robot_agent.core.planning.dialog_converge", fromlist=["goal_sig"])
    sig = sig_mod.goal_sig(run.goals)
    run.readings, run.rejected = [sig], [sig]
    assert opt.unrefused(run, "no, the rack") is None                              # a content refusal ("no, the X"): not bare
    list(opt.on_goals(run))
    assert run.allow_repeat is False
    opt2, run2, _ = make(answer="no idea")
    run2.readings = run2.rejected = [sig]
    assert opt2.unrefused(run2, "No.") is None
    list(opt2.on_goals(run2))
    assert run2.allow_repeat is True
    run2.allow_repeat = False
    list(opt2.on_goals(run2))
    assert run2.allow_repeat is False                                              # once per run
    opt3, run3, _ = make(answer="no idea")
    run3.readings = run3.rejected = [sig]
    run3.confirms = 2
    opt3.unrefused(run3, "No.")
    list(opt3.on_goals(run3))
    assert run3.allow_repeat is False                                              # the engine's own two read-backs are used up


def test_restate_follows_the_loops_own_read_back_cap_with_confirm_limit_set(make_assist, env):
    sig = dialog_converge.goal_sig([g()])
    for limit, repeat in ((None, True), (1, False)):
        opt, run, _ = make(answer="no idea", limit=limit)
        run.readings = run.rejected = [sig]
        opt.unrefused(run, "No.")
        list(opt.on_goals(run))
        assert run.allow_repeat is repeat, limit                                   # the limit of the service, not a copy of the number
    opt, run, svc = make(answer="no idea")
    del svc.readback_left                                                          # a host without the service: never repeats
    run.readings = run.rejected = [sig]
    opt.unrefused(run, "No.")
    list(opt.on_goals(run))
    assert run.allow_repeat is False
    answers = ["No.", "Put the mug on the dining table.", "yes"]
    loop, d = _loop(make_assist, env, on=True, answers=answers, ask_focus="1", **{"ask_focus.max_confirms": 1})
    assert not [e for e in loop.lab_log if e.get("rule") == "restated_after_bare_no"] and d["status"] == "failed", d     # one read-back allowed: none repeated
    loop, d = _loop(make_assist, env, on=True, answers=answers, ask_focus="1", **{"ask_focus.max_confirms": 3})
    assert [e for e in loop.lab_log if e.get("rule") == "restated_after_bare_no"] and d["status"] == "success", d


def test_the_read_back_rule_is_one_rule_for_the_loop_and_the_service():
    run = NS(confirms=2, readings=["a", "b"], corrected=0)
    assert dialog_converge.readback_left(run, True) and not dialog_converge.readback_left(run, False)      # a new reading earns one more
    assert not dialog_converge.readback_left(run, True, limit=2) and dialog_converge.readback_left(NS(confirms=2, readings=["a"], corrected=2), False)
    assert not dialog_converge.readback_left(NS(confirms=3, readings=["a"], corrected=3), False)          # past the cap: one more after a correction only


def test_restate_off_by_key_does_nothing():
    opt, run, _ = make(answer="no idea", restate=False)
    sig = __import__("robot_agent.core.planning.dialog_converge", fromlist=["goal_sig"]).goal_sig(run.goals)
    run.readings = run.rejected = [sig]
    opt.unrefused(run, "No.")
    list(opt.on_goals(run))
    assert run.allow_repeat is False


def test_made_up_agglutinative_language_a_stem_with_a_declared_ending_stands(zq):
    assert clean_leftover("ee mizura", zq) and clean_leftover("ra", zq) and clean_leftover("kanira ee", zq)
    assert not clean_leftover("zo", zq) and not clean_leftover("kani zo", zq)          # a no-word: a refusal
    assert not clean_leftover("tabura", zq)                                            # an undeclared stem with a declared ending: a refusal
    assert not clean_leftover("ee mizu tabu", zq)
    assert bare_no("zo pa", zq) and not bare_no("zo tabu", zq) and not bare_no("pa", zq)


def test_made_up_language_through_the_service_is_no_refusal_when_clean_and_a_refusal_with_more(zq):
    opt, run, svc = make(answer="kani")
    run.lang = "zq"
    assert opt.unrefused(run, "ee kani ra") is True and opt.unrefused(run, "kani, tabura") is False


def test_an_i_dont_know_to_a_read_back_with_the_robots_own_doubt_is_a_refusal_no_veto_bypass():
    opt, run, svc = make(answer="no idea", unsure=["which mug"])
    assert opt.unrefused(run, "no idea") is False
    assert [k for a, k in svc.labs if k.get("rule") == "veto_own_doubt"]
    opt2, run2, _ = make(answer="no idea")
    assert opt2.unrefused(run2, "no idea") is True


def test_either_or_names_both_readings_and_the_earlier_value_is_read_back_again_not_blocked():
    first, now = [g("mug", "stool@loft")], [g("mug", "rack@loft")]
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool", task="Put the mug on the stool.")
    run.readings = ["a", "b"]
    assert opt.unrefused(run, "no idea") is False
    assert len(svc.asked) == 1 and svc.asked[0].ask["intent"] == "choose" and len(svc.asked[0].ask["options"]) == 2
    assert "stool" in svc.asked[0] and "rack" in svc.asked[0]
    assert run.patched == [g("mug", "stool@loft")] and run.allow_repeat is True
    opt2, run2, svc2 = make(first=first, goals=now, answer="no idea", pick="the rack", task="Put the mug on the stool.")
    run2.readings = ["a", "b"]
    assert opt2.unrefused(run2, "no idea") is True and run2.patched is None          # the current reading was picked: it stands


def test_either_or_emits_its_clarify_event_and_a_stopped_run_decides_nothing():
    first, now = [g("mug", "stool@loft")], [g("mug", "rack@loft")]
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool")
    run.readings, run.asked, events = ["a", "b"], 0, []
    svc.emit = lambda r, name, **f: events.append({"event": name, **f})
    svc.cancelled = lambda r: None
    assert opt.unrefused(run, "no idea") is False and run.patched == [g("mug", "stool@loft")]
    assert [(e["event"], e["kind"], e["round"]) for e in events] == [("clarify", "ask_agree", 1)] and events[0]["question"] is svc.asked[0]
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool")
    run.readings, dialogue = ["a", "b"], list(run.dialogue)
    svc.cancelled = lambda r: {"event": "done", "status": "cancelled"}
    assert opt.unrefused(run, "no idea") is False and len(svc.asked) == 1
    assert run.patched is None and run.allow_repeat is False and run.dialogue == dialogue          # stopped: no answer kept, nothing picked
    assert [k for _a, k in svc.labs if k.get("rule") == "either_or_stopped"]


def test_the_emit_service_logs_the_event_and_pushes_it_live_only_when_the_host_has_a_channel():
    from robot_agent.core.planning.option_services import LoopServices
    logged, pushed = [], []
    run = NS(ev=lambda name, **f: logged.append({"event": name, **f}) or logged[-1])
    assert LoopServices(NS(_emit=None)).emit(run, "clarify", kind="toy") == {"event": "clarify", "kind": "toy"} and not pushed
    LoopServices(NS(_emit=pushed.append)).emit(run, "clarify", kind="toy")
    assert pushed == [{"event": "clarify", "kind": "toy"}] and len(logged) == 2


def test_the_loop_counts_the_either_or_as_a_question_of_the_run(make_assist, env):
    loop, d = _loop(make_assist, env, on=True, answers=["no, put it on the counter", "no idea", "the dining table"])
    assert d["status"] == "success" and loop._ctx.asked == 1, (d, loop._ctx.asked)               # the either-or, through the service ``ask``


def test_either_or_is_the_core_phrase_said_from_pack_data_and_not_asked_without_a_text_in_the_language(monkeypatch):
    from robot_agent.core.planning import speech
    first, now = [g("mug", "stool@loft")], [g("mug", "rack@loft")]
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool")
    run.readings = ["a", "b"]
    opt.unrefused(run, "no idea")
    q = svc.asked[0]
    assert q.key == "q_which_reading" and str(q) == speech.CORE_PHRASES["q_which_reading"].format(first=q.slots["first"], second=q.slots["second"])
    monkeypatch.setitem(langdata._CACHE, "qv", {"dialogue": {"no": ["vo"]}})            # a made-up language with no phrase of its own
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool")
    run.readings, run.lang = ["a", "b"], "qv"
    assert opt.unrefused(run, "no idea") is False and not svc.asked and run.patched is None
    assert [k for _a, k in svc.labs if k.get("rule") == "either_or_no_phrase"]
    monkeypatch.setitem(langdata._CACHE, "qv", {"dialogue": {"no": ["vo"]}, "speech": {"q_which_reading": "qv {first} ka {second} ru?", "assumed": True}})
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool")
    run.readings, run.lang = ["a", "b"], "qv"
    opt.unrefused(run, "no idea")
    assert len(svc.asked) == 1 and speech.render(svc.asked[0], "qv") == "qv put the mug on the stool ka put the mug on the rack ru?"    # no translator


def test_the_korean_pack_declares_the_two_readings_phrase_with_both_slots():
    from robot_agent.core.planning import speech
    ko = speech.phrase(None, "q_which_reading", "ko")
    assert ko and "{first}" in ko and "{second}" in ko and langdata.block("ko", "speech").get("assumed") is True
    q = speech.LocalText("q_which_reading", {"first": speech.LocalText("x", text="A"), "second": speech.LocalText("y", text="B")})
    assert speech.phrase(None, "q_which_reading", "en") == speech.CORE_PHRASES["q_which_reading"] and str(q).startswith("I can read that two ways")


def test_either_or_a_plain_yes_or_no_never_picks_one_and_it_is_asked_once():
    first, now = [g("mug", "stool@loft")], [g("mug", "rack@loft")]
    for pick in ("yes", "no", "", "the plinth"):
        opt, run, svc = make(first=first, goals=now, answer="no idea", pick=pick)
        run.readings = ["a", "b"]
        assert opt.unrefused(run, "no idea") is False and run.patched is None and run.allow_repeat is False, pick
        assert opt.unrefused(run, "no idea") is False and len(svc.asked) == 1            # a second time: no question, a refusal


def test_either_or_only_for_one_changed_value_the_person_said_and_with_the_key_on():
    first, now = [g("mug", "stool@loft")], [g("mug", "rack@loft")]
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool", task="Put the mug somewhere.")
    run.readings = ["a", "b"]
    assert opt.unrefused(run, "no idea") is False and not svc.asked                      # the person never said the earlier value
    opt, run, svc = make(first=[g("mug", "stool@loft")], goals=[g("cup", "rack@loft")], answer="no idea", pick="the stool")
    run.readings = ["a", "b"]
    assert opt.unrefused(run, "no idea") is False and not svc.asked                      # two values changed: the veto alone
    opt, run, svc = make(first=first, goals=now, answer="no idea", pick="the stool", either=False)
    run.readings = ["a", "b"]
    assert opt.unrefused(run, "no idea") is False and not svc.asked
    assert one_change([g("a")], [g("a")]) is None and one_change([g("a")], [g("b")]) == (0, "object") and one_change([g("a")], []) is None


def test_an_empty_or_foreign_pack_does_nothing_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "xx", {})
    assert not clean_leftover("tabu", ("xx",)) and clean_leftover("", ("xx",)) and not bare_no("no", ("xx",))
    opt, run, svc = make(goals=[])
    assert list(opt.on_goals(run)) == [] and run.allow_repeat is False
    svc.unsure_leftover = lambda run, t: None                                            # no phrase data: not an "I don't know"
    assert opt.unrefused(run, "tabu tabu") is None
    opt.svc = NS(lab=lambda *a, **k: None)                                               # a host without the service: nothing claimed
    assert opt.unrefused(run, "no idea") is None


def test_the_loop_leaves_a_clean_i_dont_know_standing(make_assist, env):
    loop, d = _loop(make_assist, env, on=True, answers=["no idea"])
    assert d["status"] == "success", d
    assert [e for e in loop.lab_log if e["flag"] == "ask_agree" and e.get("rule") == "unsure_is_no_refusal"]


def test_the_loop_asks_the_either_or_after_a_changed_value_and_reads_the_earlier_value_back_again(make_assist, env):
    answers = ["no, put it on the counter", "no idea", "the dining table"]
    loop, d = _loop(make_assist, env, on=True, answers=answers)
    rules = [e.get("rule") for e in loop.lab_log if e["flag"] == "ask_agree"]
    assert "either_or" in rules and any("two ways" in q for q in loop.fake.asked)
    assert sum(1 for q in loop.fake.asked if "right?" in q) == 3, loop.fake.asked          # the first, the changed one, the earlier value again
    assert d["status"] == "success", d
    loop0, d0 = _loop(make_assist, env, on=False, answers=answers)
    assert not any("two ways" in q for q in loop0.fake.asked)


def test_the_place_given_up_in_the_either_or_is_no_you_mentioned_question():
    opt, run, _ = make()
    issues = [{"kind": "place", "what": "rack", "keys": ["rack@loft"]}, {"kind": "object", "what": "cup"}]
    assert opt.on_issues(issues, run, run.goals) is None                                 # nothing given up: unchanged
    run.__dict__["opt"]["ask_agree"]["gave_up"] = {"rack@loft"}
    assert opt.on_issues(issues, run, run.goals) == [{"kind": "object", "what": "cup"}]
