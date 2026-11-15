"""Option ``fault_ladder`` (default OFF): a device fault that stops the run, after the wait and the tries, gets one help question naming the device,
one retry by a declared other skill while the robot's state is known, then a stop that names the device, the attempts and the steps done.
Made-up packs: devices "eye unit" / "grip pad" / "wheel set", the other skill "scan" (does what find does); the apple, the cup and the tables are
the symbolic FakeRobot's own. Health values carry made-up per-device texts: the option reads only the contract's ``ok``."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.types import FailureCode
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.options.fault_ladder import SPEC, FaultLadder, entries, name_in, phrase_text

DS, DT = "desk@study room", "dining table@kitchen"
EYE = {"devices": {"find": {"en": "eye unit", "ko": "눈 장치"}, "pick": "grip pad", "assumed": True}}
SCAN = {"alternatives": {"find": "scan", "assumed": True}}
APPLE = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


class FaultRobot(FakeRobot):
    """The FakeRobot whose ``broken`` skills fail with a contract code (after ``skip`` calls that work); ``scan`` does what find does; an answer
    to a question sets ``health`` to ``fixed`` when given (the person repaired the device)."""

    def __init__(self, env, objects, broken=None, health=None, fixed=None, **kw):
        super().__init__(env, objects, **kw)
        self.broken, self.calls, self.health, self.fixed = dict(broken or {}), {}, dict(health or {"ok": True}), fixed

    def _exec(self, skill, o, params):
        b = self.broken.get(skill)
        if b:
            self.calls[skill] = self.calls.get(skill, 0) + 1
            if self.calls[skill] > b.get("skip", 0):
                return {"isdone": False, "msg": f"{skill}: device fault", "code": b["code"], **({"device": b["device"]} if b.get("device") else {})}
        if skill == "ask" and self.fixed is not None:
            self.health = dict(self.fixed)
        return super()._exec("find" if skill == "scan" else skill, o, params)


def _loop(make_assist, env, monkeypatch, data=None, broken=None, health=None, fixed=None, answers=(), on=True, task_goals=APPLE, plan=PLAN, extra=None,
          **opts):
    from robot_agent.core.planning.robot import RobotView
    robot = FaultRobot(env, {"apple": DS, "cup": DS}, broken=broken, health=health, fixed=fixed, answers=answers)
    monkeypatch.setattr(RobotView, "health", lambda self, node=None: dict(robot.health))
    known = RobotView.knows_skill
    monkeypatch.setattr(RobotView, "knows_skill", lambda self, name: name == "scan" or known(self, name))
    loop = make_assist(robot, FakeLLM(task_goals), ScriptedPlanner([dict(s) for s in plan]), health_wait_s=0,
                       **({"fault_ladder": "1"} if on else {}), **opts)
    blocks = {**(extra or {}), **({"fault_ladder": data} if data is not None else {})}
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, options={**(loop.robot.manifest.planner or {}).get("options", {}), **blocks})
    return run_task(loop, "Bring the apple from the desk to the dining table."), robot, loop


def laddered(loop, part=None):
    return [e for e in loop.lab_log if e["flag"] == "fault_ladder" and (part is None or e.get("part") == part)]


def find_calls_before_ask(robot):
    out = []
    for s, *_ in robot.trace:
        if s == "ask":
            break
        out.append(s)
    return out.count("find")


# ── default off, empty pack ──────────────────────────────────────────────────────────────────────────────────────────
def test_registered_default_off_and_the_engine_stop_stands_off(make_assist, env, monkeypatch):
    assert "fault_ladder" in ASSIST_OPTIONS and REGISTRY.get("fault_ladder").key("enabled").default is False
    broken = {"find": {"code": "hw_fault"}}
    evs, robot, loop = _loop(make_assist, env, monkeypatch, {**EYE, **SCAN}, broken, health={"ok": False}, on=False)
    d = done(evs)
    assert d["status"] == "failed" and "hardware" in d["say"] and not robot.asked and not laddered(loop)


def test_an_empty_or_unlabelled_pack_does_nothing_empty(make_assist, env, monkeypatch):
    broken = {"find": {"code": "hw_fault"}}
    off, _, _ = _loop(make_assist, env, monkeypatch, None, broken, health={"ok": False}, on=False)
    for data in (None, {}, {"devices": {"find": "eye unit"}}, {"devices": {"assumed": True}}, {"alternatives": {"find": "scan", "source": " "}}):
        evs, robot, loop = _loop(make_assist, env, monkeypatch, data, broken, health={"ok": False})
        assert done(evs)["say"] == done(off)["say"] and not robot.asked and not laddered(loop), data
        assert [e["event"] for e in evs] == [e["event"] for e in off]


# ── the run end through the real loop ────────────────────────────────────────────────────────────────────────────────
def test_loop_unhealthy_robot_final_failed_one_question_naming_the_device(make_assist, env, monkeypatch):
    evs, robot, loop = _loop(make_assist, env, monkeypatch, EYE, {"find": {"code": "hw_fault"}}, health={"ok": False}, answers=("I can't, sorry",))
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "hw_fault"
    assert len(robot.asked) == 1 and "eye unit" in robot.asked[0]
    assert "eye unit" in d["say"] and "hardware" not in d["say"] and robot.obj["apple"] == DS
    assert [e["kind"] for e in evs if e["event"] == "clarify"] == ["fault_ladder"]


def test_loop_help_yes_health_ok_and_a_declared_alternative_final_success_goal_held(make_assist, env, monkeypatch):
    evs, robot, loop = _loop(make_assist, env, monkeypatch, {**EYE, **SCAN}, {"find": {"code": "hw_fault"}}, health={"ok": False},
                             fixed={"ok": True}, answers=("yes, it is fixed now",))
    assert done(evs)["status"] == "success" and robot.obj["apple"] == DT
    assert len(robot.asked) == 1 and ("scan", "apple") in [(s, o) for s, o, *_ in robot.trace]
    assert laddered(loop, "alternative") and not laddered(loop, "stop")


def test_alternative_only_when_health_reads_ok_true_and_none_declared_is_no_retry(make_assist, env, monkeypatch):
    for health in ({"ok": False}, {"ok": None}, {}):                      # the person answered, the robot still does not read ok: no retry
        evs, robot, loop = _loop(make_assist, env, monkeypatch, {**EYE, **SCAN}, {"find": {"code": "hw_fault"}}, health={"ok": False},
                                 fixed=health, answers=("yes",))
        assert done(evs)["status"] == "failed" and "scan" not in [s for s, *_ in robot.trace] and laddered(loop, "stop"), health
    evs, robot, loop = _loop(make_assist, env, monkeypatch, EYE, {"find": {"code": "hw_fault"}}, health={"ok": False}, fixed={"ok": True}, answers=("yes",))
    assert done(evs)["status"] == "failed" and not laddered(loop, "alternative")      # nothing declared: no retry, made-up or not


def test_alternative_of_a_hand_step_needs_a_hand_reading_that_fits_the_belief_pure():
    opt, run, svc = _pure({**EYE, "alternatives": {"pick": "pinch", "assumed": True}}, hand=None)
    st = run.steps[0] = {"action": "Pick", "object": "cup"}
    assert opt._retry(run, st, "pick", "pinch") is False                  # no reading: the state of the hand is unknown
    opt, run, svc = _pure({**EYE, "alternatives": {"pick": "pinch", "assumed": True}}, hand=True)
    st = run.steps[0] = {"action": "Pick", "object": "cup"}
    assert opt._retry(run, st, "pick", "pinch") is False                  # the hand holds something the belief does not
    opt, run, svc = _pure({**EYE, "alternatives": {"pick": "pinch", "assumed": True}}, hand=False)
    st = run.steps[0] = {"action": "Pick", "object": "cup"}
    assert opt._retry(run, st, "pick", "pinch") is True and run.steps[0]["skill"] == "pinch" and run.steps[0]["action"] == "Pick"
    assert opt._retry(run, run.steps[0], "pick", "pinch") is False        # once per run


def test_ask_once_an_unsure_answer_is_not_asked_again(make_assist, env, monkeypatch):
    broken = {"find": {"code": "perception_unavailable"}, "scan": {"code": "perception_unavailable"}}
    evs, robot, loop = _loop(make_assist, env, monkeypatch, {**EYE, **SCAN}, broken, health={"ok": True}, answers=("I'm not sure",))
    d = done(evs)
    assert d["status"] == "failed" and len(robot.asked) == 1                 # the step failed again after the other skill: no second question
    assert [e.get("answer") for e in laddered(loop, "ask")] == ["unsure"] and laddered(loop, "alternative")
    assert "Attempts: 4." in d["say"]                                       # three tries of find, one of scan


def test_ask_once_capped_by_question_cap_no_question_the_stop_message_still_comes(make_assist, env, monkeypatch):
    evs, robot, loop = _loop(make_assist, env, monkeypatch, EYE, {"find": {"code": "hw_fault"}}, health={"ok": False}, confirm_task=True,
                             question_cap="1", **{"question_cap.max_questions": "1", "question_cap.assumed": "1"})
    d = done(evs)
    assert not [q for q in robot.asked if "eye unit" in q] and "eye unit" in d["say"] and d["status"] == "failed"
    assert laddered(loop, "ask") == [] and not [e for e in evs if e.get("kind") == "fault_ladder"]       # not put: no ask event of its own
    assert [e["why"] for e in loop.lab_log if e["flag"] == "question_cap"] == ["cap"]


def test_stop_message_names_device_tries_and_steps_done_no_health_text_no_env_key(make_assist, env, monkeypatch):
    evs, robot, loop = _loop(make_assist, env, monkeypatch, EYE, {"find": {"code": "hw_fault", "device": "eye_unit_2"}},
                             health={"ok": False, "eye_unit_2": "fault-zz9 stuck"})
    d = done(evs)
    assert "eye unit 2" in d["say"] and "Attempts: 1." in d["say"] and "before that: 1." in d["say"]     # reported device first; MoveTo done
    for bad in ("@", "zz9", "stuck", "{", "ok"):
        assert bad not in d["say"] and bad not in d["msg"], bad


def test_stop_message_rendered_in_the_run_language_pure():
    lt = phrase_text(None, "say_fault_stop", "ko", device=name_in(EYE["devices"]["find"], "ko"), tries=2, done=3)
    ko = speech.render(lt, "ko")
    assert "눈 장치" in ko and "2" in ko and "3" in ko and "eye" not in ko
    assert "eye unit" in speech.render(phrase_text(None, "say_fault_stop", "en", device="eye unit", tries=2, done=3), "en")
    assert phrase_text(None, "no_such_phrase_key", "en", device="x") is None


def test_never_suffix_two_health_values_differing_only_in_a_made_up_suffix_give_the_same_run(make_assist, env, monkeypatch):
    outs = []
    for tail in ("zz9", "qq1"):
        evs, robot, loop = _loop(make_assist, env, monkeypatch, EYE, {"find": {"code": "hw_fault"}},
                                 health={"ok": False, "error": "hw_fault:" + tail, "eye": "degraded:" + tail})
        d = done(evs)
        outs.append(([(e["event"], e.get("kind")) for e in evs], robot.asked, d["say"], d["msg"], d["status"], d["code"], d["device"],
                     [{k: v for k, v in e.items() if k not in ("t", "ts")} for e in laddered(loop)]))
    assert outs[0] == outs[1] and outs[0][2]


# ── the rules the ladder never touches, the options around it ─────────────────────────────────────────────────────────
def test_rules_without_retry_are_never_laddered(make_assist, env, monkeypatch):
    for code in ("software", "estop"):
        evs, robot, loop = _loop(make_assist, env, monkeypatch, EYE, {"find": {"code": code}}, health={"ok": False})
        assert done(evs)["status"] != "success" and not robot.asked and not laddered(loop), code
    opt, run, svc = _pure(EYE)
    assert _drain(opt.on_fault_stop(run, run.steps[0], "find", FailureCode.CANCELLED, "find", 1)) is None and not svc.asked
    assert _drain(opt.on_fault_stop(run, run.steps[0], "find", FailureCode.ABSENT, "find", 1)) is None and not svc.asked


def test_hw_wait_tries_come_first_the_ladder_acts_when_they_are_used_up(make_assist, env, monkeypatch):
    kinds = {"perception_unavailable": {"tries": 3}, "assumed": True}
    broken = {"find": {"code": "perception_unavailable"}}
    evs, robot, _ = _loop(make_assist, env, monkeypatch, EYE, broken, health={"ok": False})
    assert find_calls_before_ask(robot) == 1                                 # the engine stops at the first unhealthy reading
    evs, robot, loop = _loop(make_assist, env, monkeypatch, EYE, broken, health={"ok": False}, hw_wait="1", hw_more_tries="1",
                             extra={"hw_wait": {"kinds": kinds}}, answers=("no",))
    assert find_calls_before_ask(robot) == 3 and len(robot.asked) == 1 and done(evs)["status"] == "failed"
    assert [e for e in loop.lab_log if e["flag"] == "hw_wait.more_tries"]


def test_partial_end_and_effect_check_on_the_fault_stop_is_failed_with_the_ladder_sentence(make_assist, env, monkeypatch):
    two = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"},
                                                                       {"type": "on", "object": "cup", "from": "desk", "to": "dining table"}]}
    plan = PLAN + steps(f"MoveTo {DS}", "Find cup", "Pick cup", f"MoveTo {DT}", f"Place {DT}")
    pad = {"devices": {"place": "grip pad", "assumed": True}}
    sensor = {"effects": {"sensor": {"skill": "grasp_succeed", "unknown": ["hw_fault"]}, "releases": ["Place"], "floor": "floor", "source": "made-up pack"}}
    evs, robot, loop = _loop(make_assist, env, monkeypatch, pad, {"place": {"code": "hw_fault", "skip": 1}}, health={"ok": False}, task_goals=two,
                             plan=plan, partial_end="1", effect_check="1", extra={"effect_check": sensor}, answers=("I can't",))
    d = done(evs)
    assert robot.obj["apple"] == DT and d["status"] == "failed" and "grip pad" in d["say"] and len(robot.asked) == 1
    assert [e.get("reading") for e in loop.lab_log if e["flag"] == "effect_check"] == ["holding"]     # effect_check (on_step_failed) first, then the ladder
    assert not [e for e in loop.lab_log if e["flag"] == "partial_end"]


# ── pure helpers ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _pure(data, hand=None, health=None):
    cfg = resolve(SPEC, {"fault_ladder": "1"}, {}, None)
    spec_of = {"Pick": NS(pre=["found(?inputs)", "hand_empty()"], effects=["holding(?inputs)"]), "Find": NS(pre=[], effects=["found(?inputs)"])}
    robot = NS(manifest=NS(planner={"options": {"fault_ladder": data}}), health=lambda node=None: dict(health or {"ok": True}),
               knows_skill=lambda n: True, spec=spec_of.get)
    svc = NS(robot=robot, labs=[], asked=[], lab=lambda *a, **k: svc.labs.append((a, k)), hand_holds=lambda run: hand,
             ask=lambda run, q: svc.asked.append(q) or "", cancelled=lambda run: None, unsure_leftover=lambda run, t: None)
    run = NS(lang="en", i=0, steps=[{"action": "Find", "object": "cup"}], world=NS(holding=None), node=None, completed=[], asked=0)
    return FaultLadder(cfg, svc), run, svc


def _drain(gen):
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def test_data_readers_empty():
    assert entries(None) == {} and entries({"find": "x"}) == {} and entries({"find": "x", "assumed": True}) == {"find": "x"}
    assert name_in({"en": "eye unit"}, "ko") == "eye unit" and name_in("wheel set", "ko") == "wheel set"
    assert name_in(None, "en") is None and name_in({"ko": " "}, "ko") is None and name_in(3, "en") is None
