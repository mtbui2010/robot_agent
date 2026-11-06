"""Solution lab round 2 (all flags default OFF): grounding chain v2 (``go_steps``, ``kind_anchor``, ``name_canon``) and the
abstention re-ask (``reask_abstain``), through the real AssistLoop.run_blocking against the symbolic FakeRobot and a
scripted LLM. Synthetic ENV and requests written for these tests (no benchmark text). Off = the earlier events, byte for byte."""
import json
import types
import time

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import goal_fix
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places

ENV = {
    "sofa@living room": {"aliases": ["sofa", "소파"], "loc": {"x": 1.0, "y": 1.0}},
    "table@kitchen": {"aliases": ["table", "식탁"], "loc": {"x": 3.0, "y": 1.0}},
    "fridge@kitchen": {"aliases": ["fridge", "냉장고"], "openable": True, "loc": {"x": 4.0, "y": 1.0}},
    "shelf@hall": {"aliases": ["shelf", "선반"], "loc": {"x": 6.0, "y": 1.0}},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna"], "loc": {"x": 1.2, "y": 1.0}},
    "ben@kitchen": {"person": True, "aliases": ["ben"], "loc": {"x": 3.2, "y": 1.0}},
}
HOUSE = {"cup": "table@kitchen"}
FLAGS = ("go_steps", "kind_anchor", "name_canon", "reask_abstain")


@pytest.fixture
def env():
    return ENV


def G(t, o="", f="", to="", **kw):
    return {"type": t, "object": o, "from": f, "to": to, **kw}


def U(*goals, status="clear", question="", reason=""):
    return {"status": status, "reason": reason, "question": question, "goals": list(goals)}


def run(make_assist, task, llm, tmp_path, memory=None, answers=(), **opts):
    if memory is not None:
        (tmp_path / "object_memory.json").write_text(json.dumps({"objects": memory}))
    robot = FakeRobot(ENV, dict(HOUSE), answers=answers)
    loop = make_assist(robot, llm, ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup")), **opts)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task)]
    return evs, robot


def first_understand(evs):
    return next(e for e in evs if e["event"] == "understand")


def shape(goals):
    return [(g["type"], g["object"], g["to"]) for g in goals]


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


# ── option plumbing ──
def test_flags_default_off_and_registered(make_assist, monkeypatch, env, tmp_path):
    for k in FLAGS:
        monkeypatch.delenv("ROBOT_AGENT_" + k.upper(), raising=False)
        assert k in ASSIST_OPTIONS and k in PLANNER_KEYS
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert all(mk().cfg[k] is False for k in FLAGS)
    assert all(mk(**{k: "1"}).cfg[k] is True for k in FLAGS)
    monkeypatch.setenv("ROBOT_AGENT_GO_STEPS", "1")
    assert mk().cfg["go_steps"] is True and mk(go_steps="0").cfg["go_steps"] is False
    monkeypatch.delenv("ROBOT_AGENT_GO_STEPS")
    lp = mk()
    lp.robot.manifest.planner = dict(lp.robot.manifest.planner or {}, name_canon=1)
    assert lp._v2_cfg({})["name_canon"] is True


# ── go_steps ──
LISTENER = U(G("go", "", "", "ben@kitchen"), G("say", "dinner is ready", "", "ben@kitchen"))
THERE_FIRST = U(G("go", "", "", "table@kitchen"), G("on", "cup", "table@kitchen", "shelf@hall"))
FINAL_GO = U(G("on", "cup", "table@kitchen", "shelf@hall"), G("go", "", "", "sofa@living room"))


@pytest.mark.parametrize("reading,task,kept", [
    (LISTENER, "Tell Ben that dinner is ready.", [("say", "dinner is ready", "ben@kitchen")]),
    (THERE_FIRST, "Take the cup from the table to the shelf.", [("on", "cup", "shelf@hall")]),
    (FINAL_GO, "Put the cup from the table on the shelf, then go to the sofa.",
     [("on", "cup", "shelf@hall"), ("go", "", "sofa@living room")]),
])
def test_go_steps_on_the_real_path(make_assist, tmp_path, reading, task, kept):
    evs, _ = run(make_assist, task, FakeLLM(reading), tmp_path, go_steps="1", confirm_task=False)
    assert shape(first_understand(evs)["goals"]) == kept


def test_go_steps_off_keeps_the_go_goal(make_assist, tmp_path):
    evs, _ = run(make_assist, "Tell Ben that dinner is ready.", FakeLLM(LISTENER), tmp_path, confirm_task=False)
    assert ("go", "", "ben@kitchen") in shape(first_understand(evs)["goals"])


def test_go_steps_never_empties_the_goal_set():
    goals = [G("go", "", "", "ben@kitchen")]
    assert goal_fix.go_steps(goals, "Go to Ben.", Places(ENV)) == (goals, [])


# ── kind_anchor ──
OPEN_FRIDGE = U(G("open", "fridge@kitchen", "", "fridge@kitchen"), G("on", "cup", "table@kitchen", "shelf@hall"))


@pytest.mark.parametrize("task,keeps_open", [
    ("Take the cup from the table to the shelf.", False),
    ("Open the fridge and take the cup from the table to the shelf.", True),
    ("식탁에서 컵을 선반에 놔 주세요.", False),
    ("냉장고를 열고 식탁에서 컵을 선반에 놔 주세요.", True),
])
def test_kind_anchor_on_the_real_path(make_assist, tmp_path, task, keeps_open):
    evs, _ = run(make_assist, task, FakeLLM(OPEN_FRIDGE), tmp_path, kind_anchor="1", confirm_task=False)
    kinds = [g["type"] for g in first_understand(evs)["goals"]]
    assert ("open" in kinds) is keeps_open


def test_kind_anchor_off_keeps_the_open_goal(make_assist, tmp_path):
    evs, _ = run(make_assist, "Take the cup from the table to the shelf.", FakeLLM(OPEN_FRIDGE), tmp_path,
                 confirm_task=False)
    assert "open" in [g["type"] for g in first_understand(evs)["goals"]]


@pytest.mark.parametrize("text,asked", [
    ("냉장고를 열어 주세요", True), ("냉장고 좀 열어줘", True), ("냉장고를 엽니다", True), ("문을 여세요", True),
    ("열쇠를 가져다 줘", False), ("컵을 식탁에 놔 주세요", False), ("Please open it", True), ("Put the cup away", False),
    ("냉장고를 열 수 있어요?", True), ("문 열지 마", True), ("열까요?", True),
    ("서랍을 열래요", True), ("열심히 해 주세요", False), ("열매를 가져다 줘", False)])
def test_open_words(text, asked):
    from robot_agent.core.planning import lexicon
    assert lexicon.has(goal_fix.OPEN_WORDS, text) is asked


def _lang_run(make_assist, tmp_path, task, lang, phrases=None):
    robot = FakeRobot(ENV, dict(HOUSE))
    loop = make_assist(robot, FakeLLM(default=OPEN_FRIDGE), ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup")),
                       kind_anchor="1", confirm_task=False)
    if phrases:
        loop.robot.i18n.phrases.update(phrases)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return [g["type"] for g in first_understand(evs)["goals"]]


@pytest.mark.parametrize("task,lang", [("Nimm die Tasse vom Tisch ins Regal.", "de"), ("Toma la taza de la mesa al estante.", "es"),
                                       ("テーブルのカップを棚に置いて。", "ja")])
def test_kind_anchor_does_nothing_for_a_language_without_open_words(make_assist, tmp_path, task, lang):
    assert "open" in _lang_run(make_assist, tmp_path, task, lang)


def test_kind_anchor_reads_the_packs_open_words_for_the_run_language(make_assist, tmp_path):
    pack = {"open_words": {"ja": "開け"}}
    assert "open" in _lang_run(make_assist, tmp_path, "冷蔵庫を開けて、テーブルのカップを棚に置いて。", "ja", pack)
    assert "open" not in _lang_run(make_assist, tmp_path, "テーブルのカップを棚に置いて。", "ja", pack)
    assert "open" in _lang_run(make_assist, tmp_path, "Nimm die Tasse vom Tisch ins Regal.", "de", pack)    # no German data


def test_open_words_for():
    assert goal_fix.open_words_for("de") is None and goal_fix.open_words_for("ja") is None
    assert goal_fix.open_words_for("en") and goal_fix.open_words_for("ko") and goal_fix.open_words_for(None)
    robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"open_words": {"de": "öffne|mach auf"}}))
    assert len(goal_fix.open_words_for("de", robot)) == 2


def test_kind_anchor_uses_a_trusted_english_reading_for_an_unlisted_language():
    g = OPEN_FRIDGE["goals"]
    assert goal_fix.kind_anchor_open(g, "Mach den Kuehlschrank auf.", Places(ENV), None, "de", "Open the fridge.")[1] == []
    assert len(goal_fix.kind_anchor_open(g, "Stell die Tasse hin.", Places(ENV), None, "de", "Put the cup away.")[1]) == 1
    assert goal_fix.kind_anchor_open(g, "Stell die Tasse hin.", Places(ENV), None, "de", "")[1] == []
    assert goal_fix.kind_anchor_open(g, "Stell die Tasse hin.", Places(ENV), None, "de", "Stell die Tasse hin.")[1] == []


def test_kind_anchor_lone_open_goal_is_kept():
    goals = [G("open", "fridge@kitchen", "", "fridge@kitchen")]
    assert goal_fix.kind_anchor_open(goals, "Do it.", Places(ENV)) == (goals, [])


def test_kind_anchor_needs_an_open_skill():
    class NoOpen:
        plan_actions = {"MoveTo", "Find", "Pick", "Place"}
    assert goal_fix.kind_anchor_open(OPEN_FRIDGE["goals"], "Do it.", Places(ENV), NoOpen())[1] == []


# ── name_canon ──
MEM = {n: {"place": p, "ts": time.time(), "how": "seen"}
       for n, p in (("mug", "table@kitchen"), ("red cup", "table@kitchen"), ("blue cup", "shelf@hall"))}


def test_name_canon_unique_place_renames_and_keeps_the_said_name(make_assist, tmp_path):
    reading = U(G("on", "mugs", "", "shelf@hall"))
    evs, _ = run(make_assist, "Put the mugs on the shelf.", FakeLLM(reading), tmp_path, memory=MEM, name_canon="1",
                 confirm_task=False)
    g = first_understand(evs)["goals"][0]
    assert (g["object"], g["said"], g["from"]) == ("mug", "mugs", "table@kitchen")


def test_name_canon_off_keeps_the_model_name(make_assist, tmp_path):
    reading = U(G("on", "mugs", "", "shelf@hall"))
    evs, _ = run(make_assist, "Put the mugs on the shelf.", FakeLLM(reading), tmp_path, memory=MEM, confirm_task=False)
    assert first_understand(evs)["goals"][0]["object"] == "mugs"


def test_name_canon_several_places_asks_which(make_assist, tmp_path):
    reading = U(G("on", "cup", "", "sofa@living room"))
    evs, _ = run(make_assist, "Put the cup on the sofa.", FakeLLM(reading), tmp_path, memory=MEM, name_canon="1",
                 answers=["the red cup"], confirm_task=False)
    q = first_understand(evs)["question"]
    assert "more than one cup" in q and "red cup" in q and "blue cup" in q


def test_name_canon_leaves_known_names_places_and_people():
    objs = {"mug": {"place": "table@kitchen"}, "mugs": {"place": "table@kitchen"}}
    goals = [G("on", "mug", "", "shelf@hall"), G("on", "ben", "", "shelf@hall"), G("on", "table", "", "shelf@hall")]
    out, which, notes = goal_fix.canon_names(goals, objs, Places(ENV))
    assert out == goals and which is None and notes == []


def test_name_canon_ignores_non_latin_memory_names():
    out, which, _ = goal_fix.canon_names([G("on", "컵", "", "shelf@hall")], {"컵": {"place": "table@kitchen"}}, Places(ENV))
    assert out[0]["object"] == "컵" and which is None


# ── the chain: flags on, nothing to fix = the events of flags off ──
def test_chain_on_changes_nothing_when_nothing_applies(make_assist, tmp_path):
    reading = U(G("on", "cup", "table@kitchen", "shelf@hall"))
    task = "Take the cup from the table to the shelf."
    a, ra = run(make_assist, task, FakeLLM(reading), tmp_path)
    b, rb = run(make_assist, task, FakeLLM(reading), tmp_path, go_steps="1", kind_anchor="1", name_canon="1",
                reask_abstain="1")
    assert json.dumps(scrub(a), sort_keys=True, default=str) == json.dumps(scrub(b), sort_keys=True, default=str)
    assert ra.trace == rb.trace


# ── reask_abstain ──
ABSTAIN = U(status="ambiguous", question="Where is the cup?")
TAKEN = U(G("on", "cup", "", "shelf@hall"))
OTHER = U(G("on", "plate", "", "shelf@hall"))
NOTE = r"finds objects itself"


def test_reask_takes_a_reading_with_goals_covering_the_object(make_assist, tmp_path):
    llm = FakeLLM((NOTE, TAKEN), ABSTAIN)
    evs, _ = run(make_assist, "Put the cup on the shelf.", llm, tmp_path, reask_abstain="1", confirm_task=False)
    u = first_understand(evs)
    assert len(llm.calls) == 2 and "finds objects itself (" in llm.calls[1][0] and "Find" in llm.calls[1][0]
    assert shape(u["goals"]) == [("on", "cup", "shelf@hall")] and not u["question"]
    assert any(e.get("reask_taken") is True for e in evs if e["event"] == "understand_check")


def test_reask_off_asks_the_model_question_with_one_call(make_assist, tmp_path):
    llm = FakeLLM(ABSTAIN)
    evs, _ = run(make_assist, "Put the cup on the shelf.", llm, tmp_path, answers=["on the table"], confirm_task=False)
    assert len(llm.calls) >= 1 and "finds objects itself" not in llm.calls[0][0]
    assert first_understand(evs)["question"] == "Where is the cup?" and not first_understand(evs)["goals"]


def test_reask_rejects_goals_for_another_object(make_assist, tmp_path):
    llm = FakeLLM((NOTE, OTHER), ABSTAIN)
    evs, _ = run(make_assist, "Put the cup on the shelf.", llm, tmp_path, reask_abstain="1", answers=["on the table"],
                 confirm_task=False)
    u = first_understand(evs)
    assert not u["goals"] and u["question"] == "Where is the cup?"
    assert any(e.get("reask") is True and e.get("reask_taken") is False for e in evs if e["event"] == "understand_check")


def test_reask_rejects_an_unsafe_reading(make_assist, tmp_path):
    unsafe = {**TAKEN, "status": "unsafe", "reason": "no"}
    llm = FakeLLM((NOTE, unsafe), ABSTAIN)
    evs, _ = run(make_assist, "Put the cup on the shelf.", llm, tmp_path, reask_abstain="1", answers=["on the table"],
                 confirm_task=False)
    assert not first_understand(evs)["goals"]


def test_reask_not_for_a_question_about_the_destination(make_assist, tmp_path):
    llm = FakeLLM(U(status="ambiguous", question="Where should I put the cup?"))
    run(make_assist, "Take the cup.", llm, tmp_path, reask_abstain="1", answers=["on the shelf"], confirm_task=False)
    assert all("finds objects itself" not in c[0] for c in llm.calls)


def test_abstains_on_location_trigger():
    f = goal_fix.abstains_on_location
    assert f(U(status="ambiguous", question="Where is the red cup located?")) == ["red cup"]
    assert f(U(status="infeasible", reason="The location of the cup is not specified.")) == []
    assert f(U(status="ambiguous", question="Where should I put the cup?")) is None
    assert f(U(G("on", "cup", "", "shelf@hall"), status="ambiguous", question="Where is the cup?")) is None
    assert f(U(status="clear", question="Where is the cup?")) is None
    assert goal_fix.covers_objects(TAKEN["goals"], ["cups"]) and not goal_fix.covers_objects(OTHER["goals"], ["cup"])
