"""Lab round 7, part 2 (all options default OFF / empty; off = the earlier behaviour): the model that reads the request
(``understand_model``, ``understand_url``, ``understand_model_lang``), the runaway guard of a model other than the planner's
(``understand_max_tokens``, ``understand_max_goals``; fall back to the planner with an ``understand_fallback`` event, no
quoting repair) and the three schema / prompt flags ``understand_schema_strict``, ``understand_give_kind``,
``understand_verify_verdict``. Through the real AssistLoop.run_blocking with the symbolic FakeRobot and scripted LLMs
(the planner's client and the understand model's client are two different fakes). Languages are DATA: a made-up code "zz"
has a map entry or none. Synthetic ENV and requests written for these tests (no benchmark text)."""
import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.contract import Manifest, ManifestError
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import goal_fix, goals as goal_kinds
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, UNDERSTAND_PROMPT, UNDERSTAND_SCHEMA, Places
from test_sage_lab_r2_grounding import ENV as ENV2, HOUSE, G, U, first_understand, shape

ENV = {k: ({kk: vv for kk, vv in v.items() if kk != "loc"} if v.get("person") else v) for k, v in ENV2.items()}
OPTS = ("understand_model", "understand_url", "understand_model_lang", "understand_max_tokens", "understand_max_goals",
        "understand_schema_strict", "understand_give_kind", "understand_verify_verdict")
HEAD = "You turn a household robot task into goals"
CUP = U(G("on", "cup", "", "shelf@hall"))
MUG = U(G("on", "mug", "", "shelf@hall"))


class KwLLM(FakeLLM):
    """A scripted client that also keeps the keyword arguments of each call (temperature, seed, num_predict)."""

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
    """The cfgs robot_agent's ``init_llm_client`` was called with, and the client it hands out per model."""
    import robot_agent.connect.llm as llm_mod
    box = types_ns = type("Box", (), {})()
    box.cfgs, box.clients = [], {}

    def init(cfg):
        box.cfgs.append(dict(cfg))
        return box.clients[cfg["model"]]
    monkeypatch.setattr(llm_mod, "init_llm_client", init)
    return box


def go(make_assist, task, planner_llm, lang="en", answers=(), manifest=None, **opts):
    robot = FakeRobot(ENV, dict(HOUSE), answers=list(answers))
    loop = make_assist(robot, planner_llm, ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup")),
                       confirm_task=False, **opts)
    if manifest:
        loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, **manifest)
        loop.cfg.update(loop._um_cfg(opts))
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return evs, robot, loop


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at")}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


# ── registration and defaults ──
def test_options_are_registered_and_off_by_default(make_assist, monkeypatch):
    for f in OPTS:
        monkeypatch.delenv("ROBOT_AGENT_" + f.upper(), raising=False)
        assert f in ASSIST_OPTIONS and f in PLANNER_KEYS
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]))
    c = loop.cfg
    assert c["understand_model"] == "" and c["understand_model_lang"] == {} and c["understand_url"] is None
    assert c["understand_max_tokens"] is None and c["understand_max_goals"] is None
    assert not (c["understand_schema_strict"] or c["understand_give_kind"] or c["understand_verify_verdict"])
    for lang in ("en", "ko", "zz"):
        assert loop._um_for(lang) == ""
    assert loop._understand_llm() is loop._llm()


def test_default_prompt_schema_and_kinds_are_the_earlier_ones(make_assist):
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]))
    assert loop._flag_prompt(UNDERSTAND_PROMPT) is UNDERSTAND_PROMPT
    want = json.loads(json.dumps(UNDERSTAND_SCHEMA))
    want["properties"]["goals"]["items"]["properties"]["type"]["enum"] = goal_kinds.kinds_for(loop.robot.plan_actions)
    assert loop._understand_schema() == want
    assert "give" not in loop._kinds()


def test_default_run_is_identical_with_the_options_spelled_out_empty(make_assist, built):
    task = "Put the cup on the shelf."
    a = go(make_assist, task, FakeLLM(default=CUP))[0]
    b = go(make_assist, task, FakeLLM(default=CUP), understand_model="", understand_model_lang={})[0]
    assert scrub(a) == scrub(b) and not any(e["event"] == "understand_fallback" for e in a)
    assert built.cfgs == []                                         # no second client was ever built


# ── precedence of the model ──
def test_precedence_option_env_manifest_default(make_assist, monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_UNDERSTAND_MODEL", raising=False)
    mk = lambda **o: make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), **o)       # noqa: E731
    assert mk().cfg["understand_model"] == ""
    loop = mk()
    loop.robot.manifest.planner = {"understand_model": "from-manifest"}
    assert loop._um_cfg({})["understand_model"] == "from-manifest"
    monkeypatch.setenv("ROBOT_AGENT_UNDERSTAND_MODEL", "from-env")
    assert loop._um_cfg({})["understand_model"] == "from-env"
    assert loop._um_cfg({"understand_model": "from-option"})["understand_model"] == "from-option"
    assert mk(understand_model="from-option").cfg["understand_model"] == "from-option"


def test_the_planner_model_as_understand_model_is_the_planner(make_assist, built):
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_model="scripted")
    assert loop._um_for("en") == "" and loop._understand_llm() is loop._llm() and built.cfgs == []


def test_language_map_resolution(make_assist):
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]),
                       understand_model="base", understand_model_lang={"KO": "ko-model", "zz": "zz-model"})
    assert loop._um_for("ko") == "ko-model" and loop._um_for("ko-KR") == "ko-model"
    assert loop._um_for("zz") == "zz-model"
    assert loop._um_for("en") == "base" and loop._um_for("xx") == "base" and loop._um_for(None) == "base"
    assert loop.cfg["understand_model_lang"] == {"ko": "ko-model", "zz": "zz-model"}      # keys folded
    only = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_model_lang='{"ko": "m"}')
    assert only._um_for("ko") == "m" and only._um_for("en") == "" and only._um_for("zz") == ""     # JSON text accepted


def test_a_bad_language_map_fails_open(make_assist):
    for bad in ("not json", ["ko"], 5, {"": "m"}, {"ko": ""}):
        loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_model_lang=bad)
        assert loop.cfg["understand_model_lang"] == {} and loop._um_for("ko") == ""


def test_url_defaults_to_translate_url_then_the_planner(make_assist, built):
    built.clients["um"] = KwLLM(default=CUP)
    cases = [({}, None), ({"translate_url": "http://tr"}, "http://tr"),
             ({"translate_url": "http://tr", "understand_url": "http://um"}, "http://um")]
    for opts, want in cases:
        built.cfgs.clear()
        loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_model="um", **opts)
        loop._understand_llm()
        assert built.cfgs[0]["url"] == want and built.cfgs[0]["model"] == "um" and built.cfgs[0]["role"] == "understand"
        assert built.cfgs[0]["name"] == "fake"                      # the planner's own config otherwise


# ── routing through the real loop ──
def test_understand_prompts_go_to_the_understand_model_and_the_rest_to_the_planner(make_assist, built):
    um = KwLLM(("finds objects itself", CUP), U(status="ambiguous", question="Where is the cup?"), default=CUP)
    pl = KwLLM(default={"ranked": ["table@kitchen", "shelf@hall"]})
    built.clients["um"] = um
    # the cup is not where the plan first looks: the failed Find makes the search ask the planner's client for a ranking
    robot = FakeRobot(ENV, {"cup": "shelf@hall"})
    plan = steps("MoveTo table@kitchen", "Find cup", "Pick cup", "MoveTo shelf@hall", "Place shelf@hall")
    loop = make_assist(robot, pl, ScriptedPlanner(plan), confirm_task=False,
                       understand_model="um", reask_abstain="1", search_legs=1)
    evs = [e for e in loop.run_blocking("Put the cup on the shelf.")]
    assert um.calls and all(HEAD in p for p, _f in um.calls)
    assert len(um.calls) == 2                                       # the first reading and the abstention re-ask
    assert all(HEAD not in p for p, _f in pl.calls)
    assert not any(e["event"] == "understand_fallback" for e in evs)
    # the same sampling kwargs as the planner's understand (temperature, seed), and no cap unless asked for
    assert um.kw[0].get("temperature") == 0.0 and "num_predict" not in um.kw[0]
    assert pl.calls and "rank" in pl.calls[0][0].lower(), "the search ranking goes to the planner's client"


def test_the_language_layer_and_the_dialogue_reads_use_the_understand_model(make_assist, built):
    um = KwLLM(U(status="ambiguous", question="Which shelf?"), default=CUP)
    pl = KwLLM(default=CUP)
    built.clients["um"] = um
    evs, _r, _l = go(make_assist, "Put the cup away.", pl, understand_model="um", answers=["on the shelf"])
    assert any(e["event"] == "understand" for e in evs)
    assert all(HEAD in p for p, _f in um.calls) and len(um.calls) >= 2          # the re-read after the answer too
    assert all(HEAD not in p for p, _f in pl.calls)


def test_a_runaway_falls_back_to_the_planner_with_no_quoting_repair(make_assist, built):
    um = KwLLM("{" + "x" * 4000, default="{" + "x" * 4000)
    pl = KwLLM(default=CUP)
    built.clients["um"] = um
    evs, _r, loop = go(make_assist, "Put the cup on the shelf.", pl, understand_model="um")
    assert len(um.calls) == 1                                         # one call, no "Your previous answer ..." repair
    assert all(not p.startswith("Your previous answer") for p, _f in um.calls + pl.calls)
    prompts = [p for p, _f in pl.calls if HEAD in p]
    assert prompts == [um.calls[0][0]]                                # ONE planner call, the original prompt
    fb = [e for e in evs if e["event"] == "understand_fallback"]
    assert fb == [{"event": "understand_fallback", "reason": "non_json", "model": "um"}]
    assert evs.index(fb[0]) < evs.index(first_understand(evs))        # logged before the reading it replaced
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")]
    assert loop.um_fallbacks == ["non_json"] and loop.um_last["fallback"] == "non_json"


def test_too_many_goals_and_three_identical_goals_are_runaways(make_assist, built):
    many = U(*[G("on", f"item{k}", "", "shelf@hall") for k in range(6)])
    same = U(*[G("on", "cup", "", "shelf@hall")] * 3)
    for answer, opts, why in ((many, {"understand_max_goals": 4}, "goals"), (same, {}, "repeat")):
        um, pl = KwLLM(default=answer), KwLLM(default=CUP)
        built.clients["um"] = um
        evs, *_ = go(make_assist, "Put the cup on the shelf.", pl, understand_model="um", **opts)
        assert [e["reason"] for e in evs if e["event"] == "understand_fallback"] == [why]
        assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")]
    # under the maximum, and two identical goals are not a runaway
    um, pl = KwLLM(default=many), KwLLM(default=CUP)
    built.clients["um"] = um
    evs, *_ = go(make_assist, "Put the items on the shelf.", pl, understand_model="um", understand_max_goals=6)
    assert not any(e["event"] == "understand_fallback" for e in evs) and not pl.calls
    assert AssistLoopRunaway(U(G("on", "cup", "", "x"), G("on", "cup", "", "x")), None) == ""
    assert AssistLoopRunaway(U(G("on", "cup", "", "x"), G("on", "mug", "", "x"), G("on", "cup", "", "x")), None) == ""


def AssistLoopRunaway(d, n):
    from robot_agent.core.planning.assist_loop import AssistLoop
    return AssistLoop.runaway(d, n)


def test_num_predict_is_sent_to_the_understand_model_only(make_assist, built):
    um, pl = KwLLM(default=CUP), KwLLM(default=CUP)
    built.clients["um"] = um
    go(make_assist, "Put the cup on the shelf.", pl, understand_model="um", understand_max_tokens=512)
    assert um.kw[0]["num_predict"] == 512
    # no cap when absent; no guard at all when the planner reads the request
    plain = KwLLM(default=CUP)
    go(make_assist, "Put the cup on the shelf.", plain, understand_max_tokens=512)
    assert "num_predict" not in plain.kw[0]


def test_sampling_options_pass_num_predict_to_the_server_options():
    from robot_agent.connect.llm.llm_object import sampling_options
    assert sampling_options({"num_ctx": 8192}, {"temperature": 0.0, "seed": 1, "num_predict": 512}) == \
        {"num_ctx": 8192, "temperature": 0.0, "seed": 1, "num_predict": 512}
    assert sampling_options(None, {}) is None


def test_a_missing_model_falls_back_to_the_planner(make_assist, built):
    um = KwLLM(raises=RuntimeError("model 'um' not found (404)"))
    pl = KwLLM(default=CUP)
    built.clients["um"] = um
    evs, _r, loop = go(make_assist, "Put the cup on the shelf.", pl, understand_model="um")
    assert [e["reason"] for e in evs if e["event"] == "understand_fallback"] == ["unavailable"]
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")]
    assert any(l.get("flag") == "understand_model" for l in loop.lab_log)       # logged
    # a client that cannot even be built (the server lacks the model at init) is the same
    built.clients.clear()
    pl2 = KwLLM(default=CUP)
    evs, *_ = go(make_assist, "Put the cup on the shelf.", pl2, understand_model="missing")
    assert [e["reason"] for e in evs if e["event"] == "understand_fallback"] == ["unavailable"]


def test_a_dead_server_still_ends_the_run_as_before(make_assist, built):
    built.clients["um"] = KwLLM(raises=RuntimeError("down"))
    pl = KwLLM(raises=RuntimeError("down"))
    evs, *_ = go(make_assist, "Put the cup on the shelf.", pl, understand_model="um")
    assert evs[-1]["event"] in ("done", "error") and evs[-1].get("status") != "success"


def test_a_ko_only_map_leaves_english_unchanged(make_assist, built):
    task = "Put the cup on the shelf."
    base = go(make_assist, task, KwLLM(default=CUP))[0]
    um, pl = KwLLM(default=CUP), KwLLM(default=CUP)
    built.clients["um"] = um
    en, *_ = go(make_assist, task, pl, understand_model_lang={"ko": "um"})
    assert scrub(en) == scrub(base) and not um.calls and built.cfgs == []
    ko, *_ = go(make_assist, "컵을 선반에 놔 줘.", pl, lang="ko", understand_model_lang={"ko": "um"})
    assert um.calls and all(HEAD in p for p, _f in um.calls)
    assert shape(first_understand(ko)["goals"]) == [("on", "cup", "shelf@hall")]


def test_a_made_up_language_code_with_no_map_uses_the_default(make_assist, built):
    um, pl = KwLLM(default=CUP), KwLLM(default=CUP)
    built.clients["um"] = um
    evs, *_ = go(make_assist, "Pon la taza en el estante.", pl, lang="zz", understand_model_lang={"ko": "um"})
    assert not um.calls and any(HEAD in p for p, _f in pl.calls)             # no entry for zz: the planner reads it
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")]
    evs, *_ = go(make_assist, "Pon la taza en el estante.", pl, lang="zz", understand_model="um")
    assert um.calls                                                          # the default model otherwise
    um.calls.clear()
    go(make_assist, "Pon la taza en el estante.", pl, lang="zz", understand_model_lang={"zz": "um"})
    assert um.calls                                                          # and a map for a made-up code works


# ── manifest data ──
def test_the_manifest_checks_the_new_planner_keys():
    ok = Manifest.from_dict({"robot": "r", "planner": {
        "understand_model": "m", "understand_url": "http://x", "understand_model_lang": {"ko": "m2"},
        "understand_max_tokens": 512, "understand_max_goals": 8, "understand_give_kind": 1}})
    assert ok.planner["understand_max_tokens"] == 512
    for key, bad in (("understand_max_tokens", 0), ("understand_max_goals", "x"), ("understand_model", ""),
                     ("understand_model_lang", {"ko": ""}), ("understand_model_lang", ["ko"])):
        with pytest.raises(ManifestError, match=key):
            Manifest.from_dict({"robot": "r", "planner": {key: bad}})


# ── understand_schema_strict ──
def test_strict_schema_requires_said_and_content(make_assist):
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_schema_strict="1")
    sc = loop._understand_schema()
    assert sc["properties"]["goals"]["items"]["required"] == ["type", "object", "said", "content", "from", "to"]
    assert "said" in loop._flag_prompt(UNDERSTAND_PROMPT).split("Task:")[0].split("Object names")[1]
    off = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]))._understand_schema()
    assert off["properties"]["goals"]["items"]["required"] == ["type", "object", "from", "to"]
    assert {k: v for k, v in sc.items() if k != "properties"} == {k: v for k, v in off.items() if k != "properties"}


# ── understand_give_kind ──
def test_give_kind_is_in_the_schema_and_maps_to_the_person_path(make_assist):
    off = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]))
    on = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_give_kind="1")
    enum = lambda lp: lp._understand_schema()["properties"]["goals"]["items"]["properties"]["type"]["enum"]   # noqa: E731
    assert "give" not in enum(off) and enum(on) == enum(off) + ["give"]
    assert '"give"' in goal_kinds.prompt_lines(on._kinds()) and '"give"' not in goal_kinds.prompt_lines(off._kinds())
    assert goal_kinds.canonical([G("give", "cup", "", "me"), G("on", "mug", "", "shelf@hall")]) == \
        [G("on", "cup", "", "me"), G("on", "mug", "", "shelf@hall")]


def test_a_give_goal_becomes_a_hand_over_to_the_person(make_assist):
    give = U(G("give", "cup", "", "me"))
    evs, *_ = go(make_assist, "Bring me the cup.", FakeLLM(default=give), understand_give_kind="1")
    u = first_understand(evs)
    assert [(g["type"], g["object"], g["to"]) for g in u["goals"]] == [("on", "cup", "anna@living room")]
    off = go(make_assist, "Bring me the cup.", FakeLLM(default=give))[0]               # off: "give" is not a goal kind
    assert [g["type"] for g in first_understand(off)["goals"]] != ["on"]


def test_the_say_to_the_speaker_echo_is_dropped_and_a_real_message_kept():
    places = Places(ENV)
    echo = G("say", "Bring me the cup", "", "me")
    keep_other = G("say", "Bring me the cup", "", "ben@kitchen")                 # to someone else: relay_check's business
    keep_msg = G("say", "Your tea is ready", "", "me")                            # words of its own
    move = G("on", "cup", "", "me")
    out, dropped = goal_fix.drop_echo_say([echo, move], "Bring me the cup", places)
    assert out == [move] and len(dropped) == 1
    out, dropped = goal_fix.drop_echo_say([keep_other, keep_msg, move], "Bring me the cup", places)
    assert out == [keep_other, keep_msg, move] and dropped == []
    out, _d = goal_fix.drop_echo_say([G("say", "Bring me the cup", "", "anna@living room")], "Bring me the cup", places)
    assert out == []                                                                  # the speaker's own ENV key too


def test_the_echo_never_asks_what_should_i_say(make_assist):
    answer = U(G("say", "Bring me the cup", "", "me"), G("on", "cup", "", "me"))
    on = go(make_assist, "Bring me the cup.", FakeLLM(default=answer), understand_give_kind="1")[0]
    u = first_understand(on)
    assert [g["type"] for g in u["goals"]] == ["on"] and "What should I say" not in str(u["question"])
    assert u["filtered"] == ["say to the speaker dropped: it repeats the request (Bring me the cup)"]
    off = go(make_assist, "Bring me the cup.", FakeLLM(default=answer))[0]
    assert [g["type"] for g in first_understand(off)["goals"]] == ["say", "on"]         # today the echo stays a goal
    goals, q = goal_fix.relay_check([G("say", "Bring me the cup", "", "me")], "Bring me the cup", Places(ENV))
    assert q == "What should I say?"                                                  # ... and the relay check asks


# ── understand_verify_verdict ──
def lacking_loop(make_assist, monkeypatch, drop=("Close",), **opts):
    from robot_agent.core.planning.robot import RobotView
    full = set(RobotView.plan_actions.fget(make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([])).robot))
    monkeypatch.setattr(RobotView, "plan_actions", property(lambda self: full - set(drop)))
    return full


def test_verdict_schema_names_the_missing_action_from_the_robots_lack(make_assist, monkeypatch):
    full = lacking_loop(make_assist, monkeypatch, drop=("Close",))
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), understand_verify_verdict="1")
    sc = loop._understand_schema()
    assert sc["properties"]["missing_action"]["enum"] == [""] + sorted(
        a for a in goal_kinds.domain_actions() if a not in full - {"Close"})
    assert "Close" in sc["properties"]["missing_action"]["enum"] and "Pick" not in sc["properties"]["missing_action"]["enum"]
    assert "location_unknown" in sc["properties"]["reason_type"]["enum"] and "missing_action" in sc["required"]
    assert list(sc["properties"]).index("missing_action") == list(sc["properties"]).index("reason_type") + 1


def test_an_infeasible_verdict_without_a_missing_action_on_covered_goals_is_dropped(make_assist):
    bad = {**CUP, "status": "infeasible", "reason": "cannot do that", "reason_type": "missing_skill", "missing_action": ""}
    off = go(make_assist, "Put the cup on the shelf.", FakeLLM(default=bad))[0]
    assert first_understand(off)["status"] == "infeasible" and any(e.get("override") for e in off)    # today: an override
    on, _r, loop = go(make_assist, "Put the cup on the shelf.", FakeLLM(default=bad), understand_verify_verdict="1")
    assert first_understand(on)["status"] == "clear" and not any(e.get("override") for e in on)
    assert shape(first_understand(on)["goals"]) == [("on", "cup", "shelf@hall")]
    assert loop.lab_log[0] == {"flag": "understand_verify_verdict", "part": "dropped"}


def test_an_infeasible_verdict_that_names_a_lacking_action_stands(make_assist, monkeypatch):
    lacking_loop(make_assist, monkeypatch, drop=("Close",))
    stand = {**U(G("close", "fridge@kitchen")), "status": "infeasible", "reason": "no close skill",
             "reason_type": "missing_skill", "missing_action": "Close"}
    evs, *_ = go(make_assist, "Close the fridge.", FakeLLM(default=stand), understand_verify_verdict="1")
    assert done_status(evs) == "refused"


def test_location_unknown_with_goals_is_clear(make_assist):
    ans = {**CUP, "status": "infeasible", "reason": "the cup's place is unknown", "reason_type": "location_unknown",
           "missing_action": ""}
    evs, _r, loop = go(make_assist, "Put the cup on the shelf.", FakeLLM(default=ans), understand_verify_verdict="1")
    assert first_understand(evs)["status"] == "clear" and loop.lab_log[0]["part"] == "location_unknown"


def test_verdict_off_changes_nothing_in_the_answer(make_assist):
    ans = {**CUP, "status": "clear", "missing_action": "", "reason_type": ""}
    evs = go(make_assist, "Put the cup on the shelf.", FakeLLM(default=ans))[0]
    assert shape(first_understand(evs)["goals"]) == [("on", "cup", "shelf@hall")]


def done_status(evs):
    last = [e for e in evs if e["event"] in ("done", "error")][-1]
    return last.get("status")
