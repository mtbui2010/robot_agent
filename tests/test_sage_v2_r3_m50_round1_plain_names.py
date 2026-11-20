"""An English line never says an ENV key or a snake_case id (synthetic ENV)."""
import types

from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places

ENV = {"hall_shelf@lobby_b": {"aliases": ["hall shelf"]}, "stool@room_c": {"aliases": []}}


def loop():
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={}))
    lp._env_places = Places(ENV)
    lp._llm_cfg = {"model": "m", "url": "http://127.0.0.1:1"}
    lp.cfg = {}
    lp._run_lang = "en"
    return lp


def test_english_line_says_names_not_ids():
    lp = loop()
    out = lp.language.speak("I can't get to the stool@room_c. Is the hall_shelf in the room_c clear?", "en")
    assert "@" not in out and "_" not in out
    assert "stool" in out


def test_localtext_english_is_cleaned_too():
    lp = loop()
    t = speech.LocalText("no_such_key", {}, text="Which one: stool@room_c or hall_shelf@lobby_b?")
    out = lp.language.speak(t, "en")
    assert "@" not in out and "_" not in out


def test_a_steps_own_text_is_not_the_final_sentence():
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = types.SimpleNamespace(plan_actions={"MoveTo", "Place"})
    for msg in ("Place the vase: the robot is at hall, not at shelf", "MoveTo shelf failed again the same way: path blocked"):
        assert str(lp._say_of_msg(msg)) == "Sorry, I could not finish the task."
    assert lp._say_of_msg("could not find the vase (looked at shelf)").startswith("could not find the vase")
