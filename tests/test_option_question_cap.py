"""Option ``question_cap`` (default OFF): at most ``max_questions`` questions per task over every option that asks through the engine services
``ask`` / ``ask_clarify`` (the run's own counters count the clarify rounds and read-backs too), and never the same normalised question twice.
Made-up objects ("florp", "quib"), made-up asking options registered in a test registry, made-up questions. No benchmark text."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import Key, Option, OptionSpec, Registry
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.option_services import LoopServices, refresh_options
from robot_agent.options import question_cap
from robot_agent.options.question_cap import SPEC, QuestionCap, normalised

GOOD = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "florp", "from": "counter", "to": "dining table"}]}
PLAN = steps("MoveTo counter@kitchen", "Find florp", "Pick florp", "MoveTo dining table@kitchen", "Place dining table@kitchen")


def make(planner=None, **keys):
    cfg = resolve(SPEC, {"question_cap": "1", **{f"question_cap.{k}": v for k, v in keys.items()}}, {}, None)
    svc = NS(robot=NS(manifest=NS(planner={} if planner is None else planner)), labs=[])
    svc.lab = lambda *a, **k: svc.labs.append(k)
    return QuestionCap(cfg, svc), svc


def run_(asked=0, confirms=0, dialogue=()):
    return NS(asked=asked, confirms=confirms, dialogue=list(dialogue))


def asker(name, questions, order):
    """A made-up option that asks ``questions`` through the engine service ``ask`` when the goals are read, then lets the run go on."""
    spec = OptionSpec(name=name, keys=(Key("enabled", "bool", False),), hooks=("on_goals",), order=order, enabled="enabled")

    class Asker(Option):
        def on_goals(self, run):
            for q in questions:
                self.svc.ask(run, q)
            return None
            yield
    return spec, Asker


def loop_with(make_assist, env, askers, answers, cap=None, **opts):
    """The real loop; its options are the made-up askers and question_cap (a test registry); ``cap`` is pack data merged after the loop was built."""
    robot = FakeRobot(env, {"florp": "counter@kitchen"}, start="counter@kitchen", answers=list(answers))
    loop = make_assist(robot, FakeLLM(("florp", GOOD), default=GOOD), ScriptedPlanner(PLAN), detector_miss_rate=0, ask_help=False)
    reg = Registry(builtin=None, entry_points=None)
    reg.register(SPEC, QuestionCap)
    on = {}
    for i, qs in enumerate(askers):
        reg.register(*asker(f"asker{i}", qs, 30 + i))
        on[f"asker{i}.enabled"] = "1"
    refresh_options(loop, {**on, **opts}, environ={}, registry=reg)
    if cap is not None:
        loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, options={"question_cap": {"max_questions": cap, "assumed": True}})
    evs = run_task(loop, "Put the florp on the dining table.", max_events=3000)
    return done(evs), robot, loop


def labs(loop):
    return [e["why"] for e in loop.lab_log if e["flag"] == "question_cap"]


QS = ["Is the florp heavy?", "Is the  FLORP heavy", "Should I hurry?", "Is the quib nearby?", "Do you want a tune?"]


def test_question_cap_registered_default_off_and_every_option_question_is_asked_as_before_off(make_assist, env):
    spec = REGISTRY.get("question_cap")
    assert spec.key("enabled").default is False and not resolve(SPEC, {}, {}, None)["enabled"]
    d, robot, loop = loop_with(make_assist, env, [QS], ["yes"] * 5, cap=3)                         # off: the cap data is there, nothing reads it
    assert robot.asked == QS and labs(loop) == []
    assert d["status"] == "success" and robot.obj["florp"] == "dining table@kitchen"


def test_question_cap_enabled_with_no_cap_declared_vetoes_nothing_empty():
    opt, svc = make()                                                                                 # on, nothing declared
    r = run_(asked=9, confirms=4, dialogue=[("robot", QS[0])])
    assert [opt.ask_veto(r, q) for q in QS + QS] == [None] * 10 and svc.labs == []
    foreign, _ = make(planner={"options": {"another_option": {"max_questions": 1, "assumed": True}}})   # another option's data is not this one's
    assert foreign.ask_veto(run_(asked=5), QS[0]) is None


def test_question_cap_unlabelled_cap_data_vetoes_nothing_empty():
    """Pack data with neither ``source`` nor ``assumed: true`` is not used (fail open): in the manifest or as a run option."""
    bare, svc = make(planner={"options": {"question_cap": {"max_questions": 1}}})
    assert bare.ask_veto(run_(asked=4, confirms=2), QS[0]) is None and svc.labs == []
    blank, _ = make(planner={"options": {"question_cap": {"max_questions": 1, "source": "  "}}})        # an empty source is no label
    assert blank.ask_veto(run_(asked=4), QS[0]) is None
    run_opt, _ = make(max_questions=1)
    assert run_opt.ask_veto(run_(asked=4), QS[0]) is None
    sourced, _ = make(planner={"options": {"question_cap": {"max_questions": 1, "source": "a made-up robot sheet"}}})
    assert sourced.ask_veto(run_(asked=1), QS[0]) == "cap"


def test_cap_counts_the_runs_own_questions_clarify_rounds_and_read_backs():
    opt, svc = make(max_questions=3, assumed="1")
    assert opt.ask_veto(run_(asked=1, confirms=1), QS[0]) is None
    assert opt.ask_veto(run_(asked=2, confirms=1), QS[2]) == "cap" and svc.labs == [{"why": "cap", "asked": 3}]
    assert opt.ask_veto(run_(asked=3), QS[3]) == "cap"


def test_cap_is_read_again_from_the_manifest_at_run_time_and_a_bad_value_is_no_cap():
    planner = {}
    opt, _svc = make(planner=planner)
    assert opt.ask_veto(run_(asked=2), QS[0]) is None
    planner["options"] = {"question_cap": {"max_questions": 2, "assumed": True}}                       # an overlay merged after the loop was built
    assert opt.ask_veto(run_(asked=2), QS[2]) == "cap"
    planner["options"] = {"question_cap": {"max_questions": "many", "assumed": True}}
    assert opt.ask_veto(run_(asked=2), QS[3]) is None


def test_no_repeat_the_same_normalised_question_is_vetoed_another_passes():
    opt, svc = make(no_repeat="1")
    r = run_()
    assert normalised(QS[0]) == normalised(QS[1]) == "is the florp heavy"
    assert [opt.ask_veto(r, q) for q in QS[:3]] == [None, "repeat", None]
    clar = run_(dialogue=[("robot", "Which quib do you mean?"), ("user", "the red one")])            # the loop's own clarify question
    assert opt.ask_veto(clar, "which QUIB do you mean") == "repeat" and opt.ask_veto(clar, "the red one?") is None
    assert [e["why"] for e in svc.labs] == ["repeat", "repeat"]
    plain, _ = make(max_questions=5, assumed="1")                                                                # no_repeat off: a repeat is asked
    assert plain.ask_veto(run_(), QS[0]) is None and plain.ask_veto(run_(), QS[1]) is None


def test_cap_a_vetoed_question_is_not_put_ask_gives_no_answer_and_clarify_reads_on():
    calls = []
    lp = NS(options=NS(query=lambda name, run, q: "cap" if name == "ask_veto" else None),
            _ask_heard=lambda *a: calls.append(a) or ("yes", 1), _ask_clarify=lambda *a: calls.append(a), _log_fn=lambda i: None)
    svc, r = LoopServices(lp), NS(asked=3, heard=0, node=None, lang="en")
    assert svc.ask(r, "Is the quib blue?") == "" and r.asked == 3
    gen = svc.ask_clarify(r, "Is the quib blue?")
    try:
        next(gen)
        raise AssertionError("a vetoed clarify yields nothing")
    except StopIteration as stop:
        assert stop.value is None
    assert calls == []


def test_cap_loop_final_one_option_stops_asking_at_the_cap_and_the_robot_acts(make_assist, env):
    d, robot, loop = loop_with(make_assist, env, [QS], ["yes"] * 5, cap=3, question_cap="1", question_cap_no_repeat="1")
    assert robot.asked == [QS[0], QS[2], QS[3]]                                                     # the repeat and the fourth are never put
    assert labs(loop) == ["repeat", "cap"]
    assert d["status"] == "success" and robot.obj["florp"] == "dining table@kitchen"


def test_no_repeat_loop_final_two_options_share_one_cap_and_one_record_the_robot_acts(make_assist, env):
    """Regression shape of the class: each option bounds only its own questions, so two of them asked four, one of them twice."""
    first, second = ["Is the quib nearby?", "Should I hurry?"], ["is the quib NEARBY", "Do you want a tune?", "Is the florp heavy?"]
    d, robot, loop = loop_with(make_assist, env, [first, second], ["no"] * 5)
    assert len(robot.asked) == 5                                                                     # off: five questions, one asked twice
    d, robot, loop = loop_with(make_assist, env, [first, second], ["no"] * 5, cap=3, question_cap="1", question_cap_no_repeat="1")
    assert robot.asked == ["Is the quib nearby?", "Should I hurry?", "Do you want a tune?"]
    assert len({normalised(q) for q in robot.asked}) == 3 and labs(loop) == ["repeat", "cap"]
    assert d["status"] == "success" and robot.obj["florp"] == "dining table@kitchen"


def test_question_cap_record_starts_empty_with_each_task():
    opt, _svc = make(no_repeat="1")
    r = run_()
    assert opt.ask_veto(r, QS[0]) is None and opt.ask_veto(r, QS[0]) == "repeat"
    opt.on_start(r)                                                                                    # the next task of the same run object
    assert opt.ask_veto(r, QS[0]) is None
    assert question_cap.SPEC.hooks == ("on_start",) and question_cap.SPEC.services == ("ask_veto",)
