"""SAGE-v2 round 3, milestone-5 round-1 fixes (failure classes, not tasks):

* F4.2b a goal-less capability refusal that comes with the model's own question (a misheard instruction read as
  an impossible action) -> "say it again" once; refused again on the repeated request, the refusal stands.
* F4.1b a capability refusal the contract overrides kept only the goals the model did not refuse (an object
  "slid into" a container was lost): one re-understanding with the robot's skills stated.
* F4.1c the "It cannot" line says ROOM doors; opening a listed place's door is fine (when the robot has Open).
* F3.2b a Hangul place noun made a verb by 하다 ("충전하러") is a whole-word mention (a requested go goal was
  dropped as invented).
* F4.7 symbolic destination repair: a move goal to a place the person never named goes to the one place they did.
* F5.1b no failure code / wrong skill name in a spoken failure; F5.3b the translation glossary has no gloss for the
  listener ("you" = the speaker's entry) and matches whole words only (natural Korean leaves "you" out, so every
  good translation was rejected and the robot fell back to English).

Synthetic places, objects and sentences written for these tests.
"""
import types

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import goal_fix, speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.understand import LLMBudget, place_mentions

DR, DT, DS = "drawer@kitchen", "dining table@kitchen", "desk@study room"


def understood(*goals, status="clear", reason="", reason_type="", question=""):
    return {"status": status, "reason": reason, "reason_type": reason_type, "question": question,
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def G(t, o, f, to, **kw):
    return {"type": t, "object": o, "from": f, "to": to, **kw}


# ── F4.2b ──
def test_unsure_goalless_refusal_asks_to_repeat(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["Put the apple from the desk on the dining table."])
    llm = FakeLLM(understood(status="infeasible", reason="The robot cannot pour liquids.",
                             question="Do you want me to bring a drink?"),
                  understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner), "Could you pot the apple on the dinning table?")
    assert robot.asked and robot.asked[0] == "Sorry, I didn't catch that. Could you say it again?"
    assert done(evs)["status"] == "success"


def test_refusal_of_the_repeated_request_stands(make_assist, env):
    robot = FakeRobot(env, {}, answers=["Please pour me a glass of juice."])
    llm = FakeLLM(understood(status="infeasible", reason="The robot cannot pour.", question="Do you mean a drink?"),
                  understood(status="infeasible", reason="The robot cannot pour.", question="Do you mean a drink?"))
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner([])), "Please pour me a glass of juice."))
    assert d["status"] == "refused" and len(robot.asked) == 1


def test_sure_goalless_refusal_still_refused(make_assist, env):
    llm = FakeLLM(understood(status="infeasible", reason="I cannot cook.", reason_type="unknown_task"))
    robot = FakeRobot(env, {})
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner([])), "Bake a chocolate cake for the party."))
    assert d["status"] == "refused" and robot.asked == []


# ── F4.1b ──
def test_override_recovers_the_refused_part(make_assist, env):
    robot = FakeRobot(env, {"plate": DS})
    llm = FakeLLM(understood(("open", "drawer", "", ""), status="infeasible",
                             reason="The robot cannot slide an object into a drawer.", reason_type="missing_skill"),
                  ("Note: this robot has a skill for every part",
                   understood(("open", "drawer", "", ""), ("in", "plate", "desk", "drawer"))))
    planner = ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find plate", "Pick plate",
                                    f"MoveTo {DR}", f"PutIn {DR}"))
    evs = run_task(make_assist(robot, llm, planner), "Open the drawer, then slide the plate from the desk into it.")
    chk = [e for e in evs if e["event"] == "understand_check" and "override_completed" in e]
    assert chk and chk[0]["override_completed"] is True
    assert done(evs)["status"] == "success" and robot.obj["plate"] == DR + "/in"


def test_override_without_a_left_out_part_makes_no_call(make_assist, env):
    robot = FakeRobot(env, {"plate": DS})
    llm = FakeLLM(understood(("in", "plate", "desk", "drawer"), status="infeasible", reason="I cannot slide things.",
                             reason_type="missing_skill"))
    planner = ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find plate", "Pick plate",
                                    f"MoveTo {DR}", f"PutIn {DR}"))
    evs = run_task(make_assist(robot, llm, planner), "Slide the plate from the desk into the drawer.")
    assert done(evs)["status"] == "success"
    assert not [p for p, _f in llm.calls if "Note: this robot has a skill" in p]


# ── F4.1c ──
def test_cannot_line_names_room_doors(make_assist, env):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]))
    line = loop._cannot_line()
    assert "room doors" in line and ("its door, is fine" in line) == ("Open" in loop.robot.plan_actions)


# ── F3.2b ──
HADA_ENV = {"charging_pad@study": {"aliases": ["charging pad", "충전"]},
            "shelf@study": {"aliases": ["study shelf", "서재 선반"]}}


def test_hangul_noun_with_hada_is_a_mention():
    ps = Places(HADA_ENV)
    assert {k for m in place_mentions("다 끝나면 충전하고 쉬어요.", ps) for k in m[3]} == {"charging_pad@study"}
    assert {k for m in place_mentions("일 끝나고 충전해 줘요", ps) for k in m[3]} == {"charging_pad@study"}
    assert place_mentions("충전기 좀 찾아 줘요", ps) == []                       # 충전기 is another word
    goals = [G("on", "book", "", "shelf@study"), G("go", "", "", "charging_pad@study")]
    kept, dropped = goal_fix.drop_invented(goals, "책을 서재 선반에 올려 두고 충전하러 가요.", ps)
    assert dropped == [] and kept == goals


# ── F4.7 ──
REP_ENV = {"linen_cabinet@hall": {"aliases": ["linen cabinet", "수납장"], "openable": True},
           "drawer@bedroom": {"aliases": ["bedroom drawer", "침실 서랍"], "openable": True},
           "drawer@kitchen": {"aliases": ["kitchen drawer", "부엌 서랍"], "openable": True},
           "desk@study": {"aliases": ["study desk", "책상"]},
           "spot by you": {"aliases": ["me", "나한테"], "person": "u", "speaker": True}}


def test_destination_repaired_to_the_one_named_place():
    ps = Places(REP_ENV)
    for text in ("파란 컵을 수납장에 넣어 줘요.", "Put the blue cup in the linen cabinet."):
        out, notes = goal_fix.repair_destination([G("in", "blue cup", "", "drawer@bedroom")], text, ps)
        assert out[0]["to"] == "linen_cabinet@hall" and notes, text


def test_destination_kept_when_said_or_unclear():
    ps = Places(REP_ENV)
    g = [G("in", "blue cup", "", "drawer@bedroom")]
    assert goal_fix.repair_destination(g, "Put the blue cup in the bedroom drawer.", ps)[1] == []      # said
    assert goal_fix.repair_destination(g, "Put the cup in the linen cabinet or on the study desk.", ps)[1] == []
    assert goal_fix.repair_destination(g, "책상에서 파란 컵 좀 치워 줘요.", ps)[1] == []                  # a source
    assert goal_fix.repair_destination(g, "Take the blue cup from the study desk.", ps)[1] == []
    two = [G("in", "cup", "", "drawer@bedroom"), G("in", "pen", "", "drawer@bedroom")]
    assert goal_fix.repair_destination(two, "Put the cup and the pen in the linen cabinet.", ps)[1] == []
    me = [G("on", "cup", "", "spot by you")]
    assert goal_fix.repair_destination(me, "Bring me the cup from the linen cabinet.", ps)[1] == []


# ── F5.1b ──
def test_hardware_fault_said_without_code_or_skill(make_assist, env, monkeypatch):
    from robot_agent.core.planning.robot import RobotView
    monkeypatch.setattr(RobotView, "health", lambda self, node=None: {"ok": False, "error": "wheel motor fault"})
    robot = FakeRobot(env, {"apple": DS}, faults=[{"skill": "pick", "kind": "fail", "nth": "all", "msg": "servo fault"}])
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner), "Put the apple from the desk on the dining table."))
    assert d["status"] == "failed" and d["code"] == "hw_fault"
    assert "hw_fault" not in d["say"] and "pick" not in d["say"].lower() and "hardware" in d["say"]


def test_failure_code_not_said(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, faults=[{"skill": "find", "kind": "fail", "obj": "apple", "nth": "all",
                                                   "msg": "find: no image (camera timeout 1 Hz)"}])
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner, health_wait_s=0), "Put the apple from the desk on the table."))
    assert d["status"] == "failed" and "perception_unavailable" not in d["say"]


# ── F5.3b ──
TR_ENV = {"bath_cabinet@bathroom": {"aliases": ["bath cabinet", "욕실 수납장"], "openable": True},
          "person_b": {"aliases": ["Minho", "민호"], "person": "b"},
          "spot by you": {"aliases": ["me", "나한테"], "person": "u", "speaker": True}}


def _loop(llm):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={}), text=lambda k, *a, **kw: k,
                                     has_text=lambda k: False)
    lp._env_places = Places(TR_ENV)
    lp._llm_cfg = {"model": "planner-8b", "url": "http://127.0.0.1:1"}
    lp.cfg = {"translate_model": "", "translate_url": None}
    lp._llm_client = llm
    lp._llm_unreachable = False
    lp._tr_budget = LLMBudget(10)
    return lp


def test_glossary_has_no_gloss_for_you():
    lp = _loop(FakeLLM())
    gl = lp._glossary("Sorry, I didn't hear you. Should I give your towel to Minho?", "ko")
    assert gl == {"Minho": "민호"}
    assert lp._glossary("Minhos and Minhoko", "ko") == {}                    # whole words only


def test_natural_translation_without_you_accepted_first_time():
    llm = FakeLLM(("Translate", "죄송해요, 잘 못 들었어요. 수건을 욕실 수납장에 넣을까요?"))
    lp = _loop(llm)
    out = lp._in_lang("Sorry, I didn't hear you. Should I put the towel in the bath cabinet in the bathroom?", "ko")
    assert out.startswith("죄송해요") and len(llm.calls) == 1 and not getattr(lp, "_say_not_in_lang", 0)
    assert "you =" not in llm.calls[0][0]


# ── F1.3b: hoist an Open over the whole fetch run (the source drawer's Open and the MoveTo there) ──
def test_hoist_open_over_a_fetch_from_another_drawer(env):
    from test_sage_v2_step3_gate import W, run_repair, score_of
    from robot_agent.core.planning.robot import RobotView
    robot = RobotView.load("fakebot")
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot, lp._namemap = robot, robot.adapter.namemap
    SD = "drawer@study room"
    places = Places({**env, SD: {"aliases": ["study drawer"]}}, openable=lambda k, _v: k.startswith("drawer@"))
    goals = [G("in", "scissors", SD, DR), G("open", DR, "", DR)]
    plan = steps(f"MoveTo {SD}", f"Open {SD}", "Find scissors", "Pick scissors", f"MoveTo {DR}", f"Open {DR}",
                 f"PutIn {DR}")
    assert score_of(lp, places, W(), goals)(plan) > 0
    out, _n, counts = run_repair(lp, robot, places, plan, W(), goals)
    assert counts.get("hoist") == 1 and score_of(lp, places, W(), goals)(out) == 0
    assert [(s["action"], s["object"]) for s in out][:4] == [("MoveTo", DR), ("Open", DR), ("MoveTo", SD), ("Open", SD)]


# ── F2.5: a delivery to the speaker needs a recipient word ──
def test_speaker_destination_without_recipient_is_asked():
    ps = Places(REP_ENV)
    g = [G("on", "jam jar", "", "spot by you")]
    for text in ("Could you put the jam jar away?", "잼 병 좀 정리해 줄래요?"):
        out, notes = goal_fix.unsaid_speaker(g, text, ps)
        assert out[0]["to"] == "" and notes, text
    for text in ("Bring me the jam jar.", "Can you get the jam jar over here?", "잼 병 좀 나한테 가져다줄래요?",
                 "잼 병 좀 줘요."):
        assert goal_fix.unsaid_speaker(g, text, ps) == (g, []), text
    other = [G("on", "jam jar", "", "desk@study")]
    assert goal_fix.unsaid_speaker(other, "Put the jam jar away on the study desk.", ps) == (other, [])


def test_put_away_to_me_asks_where(make_assist, env):
    robot = FakeRobot(env, {"jam jar": DT}, answers=["On the counter, please."])
    llm = FakeLLM(understood(("on", "jam jar", "dining table", "me")),
                  understood(("on", "jam jar", "dining table", "counter")))
    planner = ScriptedPlanner(steps(f"MoveTo {DT}", "Find jam jar", "Pick jam jar", "MoveTo counter@kitchen",
                                    "Place counter@kitchen"))
    evs = run_task(make_assist(robot, llm, planner), "Put the jam jar from the dining table away.")
    assert robot.asked and robot.asked[0] == "Where should I put the jam jar?"
    assert done(evs)["status"] == "success" and robot.obj["jam jar"] == "counter@kitchen"


# ── F3.8: a help question during the run is asked again after silence (as in the dialog phase) ──
def test_pick_help_asked_again_after_silence(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["", "yes"],
                      faults=[{"skill": "pick", "kind": "fail", "nth": [1, 2], "msg": "could not grasp the apple"}])
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    d = done(run_task(make_assist(robot, llm, planner), "Put the apple from the desk on the dining table."))
    assert d["status"] == "success", d
    helps = [q for q in robot.asked if "closer" in q]
    assert len(helps) == 2 and robot.obj["apple"] == DT


# ── F1.3c: a replan's suffix gets the gate's symbolic repair too ──
def test_replan_suffix_open_with_full_hand_is_hoisted(make_assist, env):
    robot = FakeRobot(env, {"spoon": DS},
                      faults=[{"skill": "open_drawer", "kind": "fail", "nth": 1, "msg": "the drawer handle slipped"}])
    llm = FakeLLM(understood(("in", "spoon", "desk", "drawer")))
    planner = ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find spoon", "Pick spoon",
                                    f"MoveTo {DR}", f"PutIn {DR}"),
                              replans=[steps(f"MoveTo {DS}", "Find spoon", "Pick spoon", f"MoveTo {DR}", f"Open {DR}",
                                             f"PutIn {DR}")] * 3)
    evs = run_task(make_assist(robot, llm, planner), "Put the spoon from the desk in the drawer.")
    d = done(evs)
    assert d["status"] == "success", d
    assert robot.obj["spoon"] == DR + "/in"
    opens = [i for i, t in enumerate(robot.trace) if t[0] == "open_drawer" and t[2]]
    picks = [i for i, t in enumerate(robot.trace) if t[0] == "pick" and t[2]]
    assert opens and picks and opens[0] < picks[0]


# ── F4.8: a Korean request's English object names checked against the person's words ──
def _run_state(task, goals, lang="ko", dialogue=()):
    return types.SimpleNamespace(lang=lang, task=task, goals=goals, dialogue=list(dialogue))


def test_misheard_korean_object_is_read_back():
    llm = FakeLLM(("usual Korean name", "회색 양말"))
    lp = _loop(llm)
    lp.original = None
    r = _run_state("회색 슈건을 욕실 수납장에 넣어 줄래요?", [G("in", "gray sock", "", "bath_cabinet@bathroom")])
    assert lp._unverified_objects(r) == ["gray sock"]
    assert lp._unverified_objects(r) == ["gray sock"] and len(llm.calls) == 1          # cached


def test_supported_korean_object_not_read_back():
    llm = FakeLLM(("usual Korean name", "가위\n우유 팩"))
    lp = _loop(llm)
    lp.original = None
    r = _run_state("가위랑 우유를 서랍에 넣어 줘요.", [G("in", "scissors", "", "bath_cabinet@bathroom"),
                                                 G("in", "milk carton", "", "bath_cabinet@bathroom")])
    assert lp._unverified_objects(r) == []
    # English requests and an over-budget translator: no claim, no call
    assert lp._unverified_objects(_run_state("Put the scissors away.", r.goals, lang="en")) == []
    lp2 = _loop(FakeLLM())
    lp2.original, lp2._tr_budget = None, LLMBudget(0)
    assert lp2._unverified_objects(_run_state("회색 슈건을 넣어 줘요.", [G("in", "gray sock", "", "")])) == []


# ── F5.1c: no raw ENV key in a question in the model's own words or in the two-destinations question ──
def test_model_question_and_conflict_said_with_names(make_assist, env):
    from robot_agent.core.planning import goals as goal_kinds
    ps = Places(TR_ENV)
    _g, q = goal_kinds.conflicts([G("on", "cup", "", "bath_cabinet@bathroom"), G("on", "cup", "", "person_b")],
                                 name=lambda k: speech.say_name(ps, k, "en"))
    assert "bath_cabinet" not in q and "bath cabinet" in q and "Minho" in q
    robot = FakeRobot(env, {}, answers=["The apple, on the dining table."])
    llm = FakeLLM(understood(status="ambiguous", question=f"What should I put on {DT}?"),
                  understood(("on", "apple", "", "dining table")))
    loop = make_assist(robot, llm, ScriptedPlanner([]))
    run_task(loop, "Put it there.")
    assert robot.asked and "@" not in robot.asked[0] and "dining table" in robot.asked[0]


# ── F3.9: no help with a failed grasp: the policy's replan (GRASP_FAILED then=REPLAN), not the end of the run ──
def test_grasp_without_help_replans(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["I'm not sure what you mean."],
                      faults=[{"skill": "pick", "kind": "fail", "nth": [1, 2], "msg": "could not grasp the apple"}])
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"),
                              replans=[steps("Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")])
    evs = run_task(make_assist(robot, llm, planner), "Put the apple from the desk on the dining table.")
    assert done(evs)["status"] == "success", done(evs)
    assert [c for c in planner.calls if c[0] == "replan"] and robot.obj["apple"] == DT


def test_grasp_without_help_stops_when_replans_are_used(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["no"] * 3,
                      faults=[{"skill": "pick", "kind": "fail", "nth": "all", "msg": "could not grasp the apple"}])
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"),
                              replans=[steps("Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")] * 10)
    d = done(run_task(make_assist(robot, llm, planner), "Put the apple from the desk on the dining table."))
    assert d["status"] == "failed" and [s for s, *_ in robot.trace].count("pick") <= 6


# ── grounding: the longest place name said wins; F1.1b an unstated preposition into a container is "in" ──
def test_longest_place_name_wins():
    env = {"fridge@kitchen": {"aliases": ["fridge"]}, "wine_fridge@kitchen": {"aliases": ["wine fridge"]},
           "counter@kitchen": {"aliases": ["counter"]}}
    ps = Places(env)
    assert ps.ground("no, in the wine fridge, please", "") == "wine_fridge@kitchen"
    assert ps.ground("in the fridge, please", "") == "fridge@kitchen"


def test_answer_without_preposition_puts_into_container():
    env = {"fridge@kitchen": {"aliases": ["fridge"], "openable": True}, "counter@kitchen": {"aliases": ["counter"]}}
    ps = Places(env, openable=lambda k, v: bool(v.get("openable")))
    out, _ = goal_fix.kind_from_destination([G("on", "juice", "", "fridge@kitchen")], "Tidy the juice. The fridge.", ps)
    assert out[0]["type"] == "in"
    kept, _ = goal_fix.kind_from_destination([G("on", "juice", "", "fridge@kitchen")], "Put the juice on the fridge.", ps)
    assert kept[0]["type"] == "on"


# ── F5.3c: joined phrases translated part by part ──
def test_silence_retry_reuses_the_translated_question():
    llm = FakeLLM(("Translate", "수건을 어디에 둘까요?"))
    lp = _loop(llm)
    lp.robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"q_silence": {"ko": "못 들었어요."}}),
                                     text=lambda k, *a, **kw: k, has_text=lambda k: False)
    q = "Where should I keep the towel, do you think?"
    assert lp._in_lang(q, "ko") == "수건을 어디에 둘까요?"
    again = speech.LocalText.join(speech.LocalText("q_silence"), q)
    assert lp._in_lang(again, "ko") == "못 들었어요. 수건을 어디에 둘까요?" and len(llm.calls) == 1


# ── a correction the robot misheard: one more read-back ──
def test_third_readback_after_two_corrections(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["No, the apple, on the dining table.",
                                                   "No, the apple on the dining table.", "yes"])
    apple = understood(("on", "apple", "desk", "dining table"))
    llm = FakeLLM((r"User: No, the apple on the dining table\.", apple), (r"User: No, the apple on the dining table\.", apple),
                  (r"User: No, the apple on the dining table\.", apple), default=understood(("on", "pear", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, confirm_task=True, goal_readback=False)
    d = done(run_task(loop, "Put the apple from the desk on the dining table."))
    assert d["status"] == "success", d
    assert len([q for q in robot.asked if "Is that right?" in q]) == 3


# ── F6.3: one look is not proof of absence: the most likely places get a second look ──
def test_second_look_after_a_detector_miss(make_assist, env):
    robot = FakeRobot(env, {"apple": CT_}, answers=["I don't know."] * 3,
                      faults=[{"skill": "find", "kind": "fail", "obj": "apple", "nth": 1, "msg": "apple not found"}])
    llm = FakeLLM(understood(("on", "apple", "", "dining table")), default={"ranked": [CT_, DS, DT]})
    planner = ScriptedPlanner(steps(f"MoveTo {CT_}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner, search_legs=1, search_rank="llm", search_max=1, search_total=6),
                   "Put the apple on the dining table.", max_events=3000)
    assert done(evs)["status"] == "success", done(evs)
    assert any(e["event"] == "recover" and e.get("kind") == "second_look" for e in evs)
    assert len([t for t in robot.trace if t[0] == "find"]) <= 7


CT_ = "counter@kitchen"


# ── F5.5: the understanding sees the dialogue as the person heard it ──
def test_dialogue_given_to_the_model_as_heard(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["아니요, 사과를 식탁에 놔 주세요."])
    llm = FakeLLM(("Translate", "배를 식탁에 놓을게요. 맞아요?"),
                  understood(("on", "pear", "", "dining table")),
                  (r"Robot: 배를 식탁에 놓을게요", understood(("on", "apple", "", "dining table"))),
                  default=understood(("on", "apple", "", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, llm, planner, confirm_task=True, goal_readback=False, object_check="0")
    run_task(loop, "책상에 있는 배를 식탁에 놔 주세요.", lang="ko")
    prompts = [p for p, _f in llm.calls if "You already asked the user" in p]
    dlg = prompts[0].split("You already asked the user", 1)[1] if prompts else ""
    assert "Robot: 배를 식탁에 놓을게요. 맞아요?" in dlg and "Is that right?" not in dlg


def test_korean_name_used_in_the_read_back():
    llm = FakeLLM(("usual Korean name", "회색 양말"))
    lp = _loop(llm)
    lp.original = None
    goals = [G("in", "gray sock", "", "bath_cabinet@bathroom")]
    lp._unverified_objects(_run_state("회색 슈건을 욕실 수납장에 넣어 줄래요?", goals))
    assert goals[0]["name_local"] == "회색 양말"
    rb = AssistLoop.summary(goals, Places(TR_ENV))
    assert rb.slots["what"].parts[0].slots["object"] == "회색 양말"
