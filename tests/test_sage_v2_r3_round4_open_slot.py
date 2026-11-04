"""An open / close goal has no 'where to put' slot, and a raw ENV key is never said in a slot (synthetic places)."""
import types

from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.planning.understand import find_issues, issue_question

ENV = {"oven_door@hall": {"aliases": ["oven door", "오븐 문"], "openable": True},
       "tray@hall": {"aliases": ["tray", "쟁반"]}}


def test_open_goal_raises_no_where_put_question():
    ps = Places(ENV)
    goals = [{"type": "open", "object": "oven_door@hall"}, {"type": "on", "object": "mug", "from": "", "to": "tray@hall"}]
    issues = find_issues(goals, "put the mug on the tray", ps)
    unsaid = [i for i in issues if i["kind"] == "unsaid"]
    assert unsaid and all(not i["object"] for i in unsaid)
    q = issue_question(issues, ps)
    assert "oven_door" not in str(q) and "Where should I put" not in str(q)


def test_object_slot_with_env_key_is_a_name():
    R = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"q_where_put": {"ko": "{object}은 어디에 둘까요?"}}),
                              has_text=lambda k: False)
    out = speech.render(speech.LocalText("q_where_put", {"object": "oven_door@hall"}), "ko", R, Places(ENV))
    assert out is None or "@" not in out
