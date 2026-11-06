"""Lab round 3, V2: `inventory_ask` counts remembered INSTANCES, not names. Names that refer to one remembered instance
(the same record id, or related by the pack's `object_aliases`) are one candidate; the which-question lists one name per
instance and declares its kind (`which_object`). Through the real AssistLoop.run_blocking against the symbolic FakeRobot.
Synthetic names written for these tests."""
import json
import time

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.core.planning import goal_fix, speech
from robot_agent.core.planning.assist_loop import Places
from test_sage_lab_r3 import DS, DT, ENV, U

NOW = time.time()
ALIASES = {"object_aliases": {"en": ["mug|coffee mug"]}}


def rec(**kw):
    return {"place": DS, "ts": NOW, "how": "seen", **kw}


def play(make_assist, env, tmp_path, memory, phrases=None, task="Put the mug on the dining table.", answers=()):
    (tmp_path / "object_memory.json").write_text(json.dumps({"objects": memory}))
    robot = FakeRobot(env, {"red mug": DS}, answers=answers)
    loop = make_assist(robot, FakeLLM(U(("on", "mug", "", DT))), ScriptedPlanner(steps(f"MoveTo {DS}")),
                       inventory_ask="1", confirm_task=False)
    if phrases:
        loop.robot.i18n.phrases.update(phrases)
    asked = []
    orig = loop._ask_io

    def spy(question, node, log_fn, lang):                      # what a host that reads the question sees
        asked.append(question)
        return orig(question, node, log_fn, lang)
    loop._ask_io = spy
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task)]
    return next(e for e in evs if e["event"] == "understand"), asked


def test_two_mugs_each_under_two_names_ask_with_two_options(make_assist, env, tmp_path):
    mem = {"blue mug": rec(), "blue coffee mug": rec(), "red mug": rec(), "red coffee mug": rec()}
    u, asked = play(make_assist, env, tmp_path, mem, ALIASES, answers=["the red mug"])
    assert "the red mug or the blue mug" in u["question"] and "coffee" not in u["question"]
    ask = speech.declared_ask(asked[0])                                    # the question declares its kind and options
    assert ask == {"intent": "choose", "options": ["red mug", "blue mug"], "about": "mug"}


def test_two_mugs_under_ids_of_the_record_ask_with_two_options(make_assist, env, tmp_path):
    mem = {"blue mug": rec(instance="m1"), "my favourite cup": rec(instance="m1"), "mug": rec(instance="m2")}
    cand = goal_fix.instances(["blue mug", "my favourite cup", "mug"], mem)
    assert cand == [["blue mug", "my favourite cup"], ["mug"]]


def test_one_mug_under_two_names_is_renamed_without_a_question(make_assist, env, tmp_path):
    for mem, ph in (({"blue mug": rec(), "blue coffee mug": rec()}, ALIASES),                       # alias data
                    ({"blue mug": rec(id="m1"), "my favourite cup": rec(id="m1")}, None)):            # one record id
        u, asked = play(make_assist, env, tmp_path, mem, ph)
        assert not u["question"] and u["goals"][0]["object"] == "blue mug" and u["goals"][0]["said"] == "mug"
        assert not asked


def test_names_without_any_link_stay_distinct_instances():
    """No alias data, no shared id: two names are two instances (the earlier behaviour)."""
    mem = {"blue mug": rec(), "red mug": rec(), "blue coffee mug": rec()}
    assert len(goal_fix.instances(list(mem), mem)) == 3
    out, which, _ = goal_fix.canon_inventory([{"type": "on", "object": "mug", "from": "", "to": DT}], mem, Places(ENV))
    assert len(which[1]) == 3


def test_alias_groups_are_pack_data_per_language():
    import types
    robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"object_aliases": {"de": ["tasse|kaffeetasse"]}}))
    assert goal_fix.alias_groups(robot, "de") == [["tasse", "kaffeetasse"]] and goal_fix.alias_groups(robot, "en") == []
    assert goal_fix.alias_groups(None, "en") == []
    mem = {"blaue tasse": {"place": DS}, "blaue kaffeetasse": {"place": DS}, "rote tasse": {"place": DS}}
    groups = goal_fix.alias_groups(robot, "de")
    assert goal_fix.instances(list(mem), mem, groups) == [["blaue tasse", "blaue kaffeetasse"], ["rote tasse"]]


def test_question_declaration_survives_calm_and_join():
    q = speech.LocalText("q_which_known", {"object": "mug", "text": "x"}, ask={"intent": "choose", "options": ["a", "b"]})
    assert speech.declared_ask(speech.LocalText.join(speech.LocalText("q_silence"), q))["options"] == ["a", "b"]
    assert speech.declared_ask(speech.LocalText("q_where_go")) is None


def test_which_question_options_are_a_list_slot_said_with_the_packs_separator():
    import types
    q = speech.LocalText("q_which_known", {"object": "mug", "text": "the red mug or the blue mug", "options": ["red mug", "blue mug"]},
                         text="I know of more than one mug: the red mug or the blue mug. Which one do you mean?")
    pack = {"q_which_known": {"de": "Ich kenne mehr als eine {object}: {options}. Welche meinst du?"}, "sep_or": {"de": " oder "}}
    robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases=pack))
    assert speech.render(q, "de", robot) == "Ich kenne mehr als eine mug: red mug oder blue mug. Welche meinst du?"
    robot.i18n.phrases.pop("sep_or")                                    # no pack separator: the earlier default
    assert speech.render(q, "de", robot).endswith("red mug or blue mug. Welche meinst du?")
    assert speech.render(q, "en") == str(q)
