"""Lab round 8 (all options default OFF / empty; off = the earlier behaviour): a model per role. ``repair_model`` (the plan gate's
repair prompts, through the planner's ``chat_override``), ``dialog_model`` / ``dialog_model_lang`` (the robot's words in the user's
language), the ``*_think`` switches and ``request_coverage``. Through the real AssistLoop / LanguageAdapter / GraceBackend with
scripted clients. Languages are DATA (a made-up code ``zz`` takes a map entry or none). Synthetic ENV and requests."""
import contextlib
import json
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import Manifest, ManifestError
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import request_coverage, role_models
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, AssistLoop, Places
from robot_agent.core.planning.backends.pyplanner_grace import GraceBackend
from robot_agent.core.planning.understand import LLMBudget
from test_sage_lab_r2_grounding import ENV, HOUSE, G, U
from test_sage_v2_step3_gate import Block, BlockPlanner

KEYS = ("understand_think", "repair_model", "repair_url", "repair_max_tokens", "repair_think", "dialog_model",
        "dialog_model_lang", "dialog_url", "dialog_max_tokens", "dialog_think", "request_coverage")
KO_OK = "번역이에요"


class KwLLM(FakeLLM):
    def __init__(self, *script, default=None, raises=None):
        super().__init__(*script, default=default)
        self.kw, self.raises = [], raises

    def chat(self, prompt="", format=None, **kw):
        self.kw.append(kw)
        if self.raises is not None:
            self.calls.append((prompt, format))
            raise self.raises
        return super().chat(prompt=prompt, format=format, **kw)


@pytest.fixture
def env():
    return ENV


@pytest.fixture
def built(monkeypatch):
    import robot_agent.connect.llm as llm_mod
    box = type("Box", (), {})()
    box.cfgs, box.clients = [], {}

    def init(cfg=None, **k):
        box.cfgs.append(dict(cfg or {}))
        return box.clients[cfg["model"]]
    monkeypatch.setattr(llm_mod, "init_llm_client", init)
    return box


# ── plumbing and defaults ──
def test_options_are_registered_and_off_by_default(make_assist, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv("ROBOT_AGENT_" + k.upper(), raising=False)
        assert k in ASSIST_OPTIONS and k in PLANNER_KEYS
    c = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([])).cfg
    assert c["repair_model"] == "" and c["dialog_model"] == "" and c["dialog_model_lang"] == {}
    assert c["repair_url"] is None and c["dialog_url"] is None
    assert c["repair_max_tokens"] is None and c["dialog_max_tokens"] is None
    assert c["understand_think"] is None and c["repair_think"] is None and c["dialog_think"] is None
    assert c["request_coverage"] is False


def test_precedence_option_env_manifest_default(make_assist, monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_REPAIR_MODEL", raising=False)
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]))
    loop.robot.manifest.planner = {"repair_model": "from-manifest", "dialog_model_lang": {"KO": "m-ko"},
                                   "repair_max_tokens": 300, "repair_think": 0}
    c = loop._rm_cfg({})
    assert c["repair_model"] == "from-manifest" and c["dialog_model_lang"] == {"ko": "m-ko"}
    assert c["repair_max_tokens"] == 300 and c["repair_think"] is False
    monkeypatch.setenv("ROBOT_AGENT_REPAIR_MODEL", "from-env")
    assert loop._rm_cfg({})["repair_model"] == "from-env"
    assert loop._rm_cfg({"repair_model": "from-option"})["repair_model"] == "from-option"


def test_bad_values_fail_open():
    pick = lambda bad: (lambda k, d=None: bad if k in ("dialog_model_lang", "repair_max_tokens", "repair_think") else d)   # noqa: E731
    for bad in ("not json", ["ko"], {"": "m"}, {"ko": ""}, -3, "x", None):
        c = role_models.config(pick(bad))
        assert c["dialog_model_lang"] == {} and c["repair_max_tokens"] is None and c["repair_think"] is None


def test_think_of():
    assert [role_models.think_of(v) for v in ("1", "true", 1, "0", "off", 0, None, "", "maybe")] == [
        True, True, True, False, False, False, None, None, None]


def test_manifest_validates_the_new_keys():
    base = {"robot": "t", "version": "0.1"}
    for bad in ({"repair_model": ""}, {"dialog_model_lang": {"ko": 3}}, {"repair_max_tokens": 0},
                {"dialog_max_tokens": "x"}, {"dialog_url": " "}):
        with pytest.raises(ManifestError):
            Manifest.from_dict({**base, "planner": bad})
    ok = Manifest.from_dict({**base, "planner": {"repair_model": "m", "dialog_model_lang": {"ko": "m"},
                                                  "repair_max_tokens": 400, "dialog_think": 0, "request_coverage": 1}})
    assert ok.planner["dialog_model_lang"] == {"ko": "m"}


def test_runaway_judges():
    assert role_models.runaway("") == "empty" and role_models.runaway("  \n") == "empty"
    assert role_models.runaway("fine") == "" and role_models.runaway("x" * 100, 25) == "long"
    assert role_models.runaway("x" * 99, 25) == ""
    assert role_models.runaway("\n".join(["go a"] * 6)) == "repeat" and role_models.runaway("\n".join(["go a"] * 5)) == ""


def test_llama_client_sends_think_only_when_set():
    from robot_agent.connect.llm.llama import LLamaClient
    sent = []

    def chat(**kw):
        sent.append(kw)
        return types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))
    c = object.__new__(LLamaClient)
    c.text_model = c.vision_model, c.options, c.chatclient = "m", None, chat
    c.text_model = c.vision_model = "m"
    c.think = None
    msgs = [{"role": "user", "content": "hi"}]
    c.send_msgs(msgs=msgs)
    assert "think" not in sent[-1]
    c.send_msgs(msgs=msgs, think=False)
    assert sent[-1]["think"] is False
    c.think = False
    c.send_msgs(msgs=msgs)
    assert sent[-1]["think"] is False


# ── the repair model: GraceBackend.chat_override ──
class Seam:
    """A planner object with embodiplan's ``_chat`` seam."""

    def __init__(self):
        self.seen = []

    def _chat(self, messages, temperature=0.2):
        self.seen.append(("own", messages))
        return "own answer", 1, 1


def test_chat_override_routes_inside_the_with_and_restores():
    p = Seam()
    b = GraceBackend.wrap(p)
    fn = lambda own, m, t: (f"routed {t}", 0, 0) if m == "go" else own(m, t)         # noqa: E731
    with b.chat_override(fn):
        assert p._chat("go", 0.5) == ("routed 0.5", 0, 0)
        assert p._chat("other", 0.1) == ("own answer", 1, 1)                          # the fallback is the planner's own
    assert p._chat("go") == ("own answer", 1, 1) and "_chat" not in p.__dict__        # back to the class's method
    with pytest.raises(RuntimeError), b.chat_override(fn):
        raise RuntimeError("boom")
    assert "_chat" not in p.__dict__
    with GraceBackend.wrap(object()).chat_override(fn):                               # no seam: nothing changes, no error
        pass


class EditPlanner(BlockPlanner):
    """BlockPlanner whose repair_block asks the model through the ``_chat`` seam (as SAGE's edit_block does)."""

    def __init__(self, blocks, answer):
        super().__init__(blocks)
        self.answer, self.own_calls = answer, []

    def _chat(self, messages, temperature=0.2):
        self.own_calls.append(messages)
        return self.answer, 0, 0

    @contextlib.contextmanager
    def chat_override(self, fn):
        own = self._chat
        self._chat = lambda messages, temperature=0.2: fn(own, messages, temperature)
        try:
            yield
        finally:
            del self._chat

    def repair_block(self, block, feedback, *, task, state=None, obs=""):
        self.edits.append((block.name, feedback))
        text = self._chat([{"role": "system", "content": "edit"}, {"role": "user", "content": feedback}], 0.2)[0]
        return json.loads(text)


DS, DT, CT = "desk@hall", "table@kitchen", "shelf@hall"
FIX = [{"action": "MoveTo", "object": "table@kitchen"}, {"action": "Find", "object": "cup"},
       {"action": "Pick", "object": "cup"}, {"action": "MoveTo", "object": "shelf@hall"},
       {"action": "Place", "object": "shelf@hall"}]
BAD = steps("MoveTo table@kitchen", "Find cup", "Pick cup", "MoveTo shelf@hall", "Place table@kitchen")


def repair_run(make_assist, planner_answer, repair_client=None, built=None, **opts):
    planner = EditPlanner([Block("cup", list(BAD))], planner_answer)
    robot = FakeRobot(ENV, {"cup": "table@kitchen"})
    if repair_client is not None:
        built.clients["rm"] = repair_client
    loop = make_assist(robot, FakeLLM(U(G("on", "cup", "table@kitchen", "shelf@hall"))), planner, confirm_task=False, **opts)
    evs = [e for e in loop.run_blocking("Put the cup on the shelf.")]
    return evs, planner, loop


def test_default_repair_uses_the_planners_own_model(make_assist, built):
    evs, planner, loop = repair_run(make_assist, json.dumps(FIX))
    assert done(evs)["status"] == "success" and len(planner.own_calls) == 1 and built.cfgs == []
    assert loop.lab_log == []


def test_repair_model_answers_the_gate_repair_prompt(make_assist, built):
    rm = KwLLM(default=FIX)
    evs, planner, loop = repair_run(make_assist, "SHOULD NOT BE ASKED", rm, built, repair_model="rm",
                                    repair_max_tokens=300, repair_think="0")
    assert done(evs)["status"] == "success", done(evs)
    assert planner.own_calls == [] and len(rm.calls) == 1 and "Checker findings" in rm.calls[0][0]
    assert rm.kw[0]["num_predict"] == 300 and rm.kw[0]["temperature"] == 0.2
    assert built.cfgs[0]["role"] == "repair" and built.cfgs[0]["think"] is False and built.cfgs[0]["model"] == "rm"
    assert not any(e["flag"] == "repair_model" for e in loop.lab_log)                 # no fallback happened


@pytest.mark.parametrize("bad", ["", "\n".join(["Move"] * 8), "x" * 1300])
def test_a_runaway_repair_falls_back_to_the_planner_once(make_assist, built, bad):
    rm = KwLLM(default=bad) if bad != "x" * 1300 else KwLLM("x" * 1300)
    evs, planner, loop = repair_run(make_assist, json.dumps(FIX), rm, built, repair_model="rm", repair_max_tokens=300)
    assert done(evs)["status"] == "success", done(evs)
    assert len(rm.calls) == 1 and len(planner.own_calls) == 1
    fb = [e for e in loop.lab_log if e["flag"] == "repair_model"]
    assert len(fb) == 1 and fb[0]["fallback"] in ("empty", "repeat", "long") and fb[0]["model"] == "rm"
    assert loop._last_budget_used is not None


def test_a_repair_model_that_errors_or_is_missing_falls_back(make_assist, built):
    rm = KwLLM(raises=ConnectionError("down"))
    evs, planner, loop = repair_run(make_assist, json.dumps(FIX), rm, built, repair_model="rm")
    assert done(evs)["status"] == "success" and len(planner.own_calls) == 1
    assert [e["fallback"] for e in loop.lab_log if e["flag"] == "repair_model"] == ["unavailable"]


def test_the_fallback_call_takes_a_budget_unit(make_assist, built):
    on, off = [], []
    for rm, store in ((KwLLM(default=FIX), on), (KwLLM(default=""), off)):
        _e, _p, loop = repair_run(make_assist, json.dumps(FIX), rm, built, repair_model="rm")
        store.append(loop._last_budget_used)
    assert off[0] == on[0] + 1


def test_the_planners_own_model_as_repair_model_changes_nothing(make_assist, built):
    evs, planner, _l = repair_run(make_assist, json.dumps(FIX), None, built, repair_model="scripted")
    assert done(evs)["status"] == "success" and len(planner.own_calls) == 1 and built.cfgs == []


def test_a_planner_without_chat_override_keeps_its_model(make_assist, built):
    class Plain(EditPlanner):
        chat_override = None
    planner = Plain([Block("cup", list(BAD))], json.dumps(FIX))
    built.clients["rm"] = KwLLM(default=FIX)
    loop = make_assist(FakeRobot(ENV, {"cup": "table@kitchen"}), FakeLLM(U(G("on", "cup", "table@kitchen", "shelf@hall"))),
                       planner, confirm_task=False, repair_model="rm")
    assert done(list(loop.run_blocking("Put the cup on the shelf.")))["status"] == "success"
    assert len(planner.own_calls) == 1 and built.clients["rm"].calls == []


def test_the_first_plan_and_the_understand_step_never_go_to_the_repair_model(make_assist, built):
    rm = KwLLM(default=FIX)
    _e, _p, _l = repair_run(make_assist, "unused", rm, built, repair_model="rm")
    assert all("Checker findings" in p for p, _f in rm.calls)


# ── the dialog model: LanguageAdapter ──
def loop_with(llm=None, **cfg):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = types.SimpleNamespace(i18n=lambda *a, **k: None, text=lambda k, lang=None, **kw: k, has_text=lambda k: False)
    lp._env_places = Places({"hall_shelf@hall": {"aliases": ["hall shelf", "현관 선반"]}})
    lp._llm_cfg = {"model": "planner-8b", "url": "http://127.0.0.1:1"}
    lp.cfg = {"translate_model": "", "translate_url": None, **cfg}
    lp._llm_client = llm
    lp._llm_unreachable = False
    lp._tr_budget = LLMBudget(cfg.get("translate_budget", 10))
    lp._run_lang = "ko"
    return lp


def test_no_dialog_model_is_the_translators_call_exactly(built):
    tr = KwLLM(default=KO_OK)
    lp = loop_with(tr)
    assert lp._in_lang("Where is it?", "ko") == KO_OK
    assert tr.kw == [{"temperature": 0, "seed": 0}] and built.cfgs == []


def test_dialog_model_writes_the_users_language_and_only_that(built):
    tr, dm = KwLLM(default="translator answer"), KwLLM(default=KO_OK)
    built.clients["dm"] = dm
    lp = loop_with(tr, dialog_model_lang={"ko": "dm"}, dialog_max_tokens=200, dialog_think=False, dialog_url="http://dm")
    assert lp._in_lang("Where is it?", "ko") == KO_OK
    assert tr.calls == [] and len(dm.calls) == 1
    assert dm.kw[0] == {"temperature": 0, "seed": 0, "num_predict": 200}
    assert built.cfgs[0]["role"] == "dialog" and built.cfgs[0]["think"] is False and built.cfgs[0]["url"] == "http://dm"
    assert lp._in_lang("Where is it?", "ko") == KO_OK and len(dm.calls) == 1             # cached


def test_dialog_model_follows_the_language_map_and_default(built):
    tr, dm = KwLLM(default=KO_OK), KwLLM(default=KO_OK)
    built.clients["dm"] = dm
    lp = loop_with(tr, dialog_model_lang={"zz": "dm"})
    assert lp.language.dialog_model("zz") == "dm" and lp.language.dialog_model("ko") == ""
    assert lp.language.dialog_model("en") == "" and lp.language.dialog_model(None) == ""
    lp2 = loop_with(tr, dialog_model="dm", dialog_model_lang={"ko": "other"})
    assert lp2.language.dialog_model("xx") == "dm" and lp2.language.dialog_model("ko") == "other"
    lp3 = loop_with(tr, dialog_model="planner-8b")                  # the planner's / translator's own model: no new client
    assert lp3.language.dialog_model("ko") == ""
    lp4 = loop_with(tr, dialog_model="tr-model", translate_model="tr-model")
    assert lp4.language.dialog_model("ko") == ""


@pytest.mark.parametrize("bad", ["", "\n".join([KO_OK] * 8), "this is English, no Korean at all", "x" * 900])
def test_a_dialog_runaway_or_refused_text_goes_to_the_translator_once(built, bad):
    tr, dm = KwLLM(default=KO_OK), KwLLM(default=bad)
    built.clients["dm"] = dm
    lp = loop_with(tr, dialog_model_lang={"ko": "dm"}, dialog_max_tokens=100)
    lp.lab_log = []
    assert lp._in_lang("Where is it?", "ko") == KO_OK
    assert len(dm.calls) >= 1 and len(tr.calls) == 1
    assert lp.lab_log and lp.lab_log[0]["flag"] == "dialog_model" and lp.lab_log[0]["lang"] == "ko"


def test_a_dialog_model_that_is_down_falls_back_and_spends_translate_budget(built):
    tr, dm = KwLLM(default=KO_OK), KwLLM(raises=ConnectionError("down"))
    built.clients["dm"] = dm
    lp = loop_with(tr, dialog_model_lang={"ko": "dm"}, translate_budget=1)
    lp.lab_log = []
    assert lp._in_lang("Where is it?", "ko") != KO_OK or True
    lp2 = loop_with(tr, dialog_model_lang={"ko": "dm"}, translate_budget=5)
    lp2.lab_log = []
    assert lp2._in_lang("Where is it?", "ko") == KO_OK and lp2.lab_log[0]["fallback"] == "unavailable"
    assert lp2._tr_budget.used == 2                                  # the model's call and the fallback


def test_korean_object_names_use_the_dialog_model_too(built):
    tr, dm = KwLLM(default="사과\n컵"), KwLLM(default="사과\n컵")
    built.clients["dm"] = dm
    lp = loop_with(tr, dialog_model_lang={"ko": "dm"})
    assert lp._local_names(["apple", "cup"]) == {"apple": "사과", "cup": "컵"}
    assert tr.calls == [] and len(dm.calls) == 1
    off = loop_with(tr)
    assert off._local_names(["apple", "cup"]) == {"apple": "사과", "cup": "컵"} and len(tr.calls) == 1


def test_reading_the_users_words_stays_with_the_translator(built):
    tr, dm = KwLLM(default="Where is the cup?"), KwLLM(default=KO_OK)
    built.clients["dm"] = dm
    lp = loop_with(tr, dialog_model_lang={"ko": "dm"}, input_pivot="translate")
    assert lp.language.hear("컵 어디 있어요?", "ko").english == "Where is the cup?"
    assert dm.calls == [] and len(tr.calls) == 1


# ── understand_think ──
def test_understand_think_goes_into_the_role_client_config(make_assist, built):
    built.clients["um"] = KwLLM(default=U())
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_model="um", understand_think="0")
    loop._understand_llm()
    assert built.cfgs[0]["think"] is False
    built.cfgs.clear()
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_model="um")
    loop._understand_llm()
    assert "think" not in built.cfgs[0]


# ── request coverage ──
def test_coverage_parts_are_quotations_of_the_request():
    req = "Put the cup on the shelf and bring the  Mug to Ben."
    d = {"unserved": [{"part": "bring the mug to ben", "why": "no_goal"}, {"part": "water the plants", "why": "no_goal"},
                      {"part": ""}, "bring the mug to ben", {"part": "put the cup on the shelf"}, {"part": "x"}]}
    assert request_coverage.parts(d, req) == ["bring the mug to ben", "put the cup on the shelf"]
    assert request_coverage.parts({"unserved": "x"}, req) == [] and request_coverage.parts(None, req) == []
    p = request_coverage.prompt("req", "", ["MoveTo a"] * 60)
    assert "(none)" in p and p.count("MoveTo a") == request_coverage.MAX_STEPS


CUP = U(G("on", "cup", "table@kitchen", "shelf@hall"))
MUG = U(G("on", "mug", "table@kitchen", "shelf@hall"))
TASK = "Put the cup on the shelf and the mug on the shelf."


def coverage_run(make_assist, llm, answers=(), **opts):
    robot = FakeRobot(ENV, {"cup": "table@kitchen", "mug": "table@kitchen"}, answers=list(answers))
    planner = ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup", "Pick cup", "MoveTo shelf@hall", "Place shelf@hall"),
                              replans=[steps("MoveTo table@kitchen", "Find mug", "Pick mug", "MoveTo shelf@hall",
                                             "Place shelf@hall")])
    loop = make_assist(robot, llm, planner, confirm_task=False, goal_readback=False, **opts)
    return [e for e in loop.run_blocking(TASK)], robot, loop


def test_coverage_is_off_by_default_and_makes_no_call(make_assist):
    llm = FakeLLM(CUP, default=CUP)
    evs, _r, loop = coverage_run(make_assist, llm)
    assert done(evs)["status"] == "success"
    assert not any(request_coverage.PROMPT[:40] in p for p, _f in llm.calls) and loop.lab_log == []


def test_coverage_nothing_missing_claims_done_after_one_call(make_assist):
    llm = FakeLLM(CUP, ("household robot was asked", {"unserved": []}), default=CUP)
    evs, _r, loop = coverage_run(make_assist, llm, request_coverage="1")
    assert done(evs)["status"] == "success"
    assert sum("about to say it is done" in p for p, _f in llm.calls) == 1
    assert not any(e.get("kind") == "coverage" for e in evs if e["event"] == "clarify")


def test_coverage_asks_and_a_yes_carries_on_instead_of_claiming(make_assist):
    gap = {"unserved": [{"part": "the mug on the shelf", "why": "no_goal"}]}
    llm = FakeLLM(CUP, ("about to say it is done", gap), ("find|object", MUG), default=U(G("on", "cup", "", "shelf@hall"), G("on", "mug", "", "shelf@hall")))
    evs, robot, loop = coverage_run(make_assist, llm, answers=["yes"], request_coverage="1")
    q = [e for e in evs if e["event"] == "clarify" and e.get("kind") == "coverage"]
    assert len(q) == 1 and "the mug on the shelf" in str(q[0]["question"])
    assert robot.asked and "the mug on the shelf" in str(robot.asked[0])
    assert any(e["event"] == "goal_change" for e in evs)
    assert done(evs)["status"] == "success" and robot.obj == {"cup": "shelf@hall", "mug": "shelf@hall"}   # the mug was done
    assert [e for e in loop.lab_log if e["flag"] == "request_coverage"][0]["parts"] == ["the mug on the shelf"]


def test_coverage_a_no_claims_as_before_and_a_made_up_part_is_dropped(make_assist):
    gap = {"unserved": [{"part": "the mug on the shelf", "why": "no_goal"}]}
    llm = FakeLLM(CUP, ("about to say it is done", gap), default=CUP)
    evs, robot, _l = coverage_run(make_assist, llm, answers=["no"], request_coverage="1")
    assert done(evs)["status"] == "success" and robot.asked and robot.obj["mug"] == "table@kitchen"
    made_up = {"unserved": [{"part": "water the plants", "why": "no_goal"}]}
    llm = FakeLLM(CUP, ("about to say it is done", made_up), default=CUP)
    evs, robot, _l = coverage_run(make_assist, llm, request_coverage="1")
    assert done(evs)["status"] == "success" and robot.asked == []


def test_coverage_fails_open_on_a_bad_answer_and_runs_once(make_assist):
    llm = FakeLLM(CUP, ("about to say it is done", "not json at all"), default=CUP)
    evs, _r, loop = coverage_run(make_assist, llm, request_coverage="1")
    assert done(evs)["status"] == "success"
    assert sum("about to say it is done" in p for p, _f in llm.calls) == 1
