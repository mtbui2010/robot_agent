"""A person the words name once is the listener of a message, not also the recipient of a delivery (synthetic ENV)."""
from robot_agent.core.planning import goal_fix
from robot_agent.core.planning.assist_loop import Places

ENV = {"person_me": {"person": "me", "speaker": True, "aliases": ["me", "나"]},
       "person_o": {"person": "o", "aliases": ["Oma", "오마"]},
       "tray@den": {"aliases": ["tray", "쟁반"]}}


def goals(to):
    return [{"type": "say", "object": "dinner is ready", "from": "", "to": "person_o"},
            {"type": "on", "object": "tray", "from": "tray@den", "to": to, "hand": True}]


def test_listener_named_once_is_not_the_recipient():
    out, notes = goal_fix.shared_listener(goals("person_o"), "tell Oma dinner is ready and bring the tray", Places(ENV))
    assert out[1]["to"] == "person_me" and notes


def test_listener_named_twice_keeps_the_delivery():
    out, notes = goal_fix.shared_listener(goals("person_o"), "tell Oma dinner is ready and bring the tray to Oma", Places(ENV))
    assert out[1]["to"] == "person_o" and not notes


def test_korean_alias_counts_the_same():
    out, _n = goal_fix.shared_listener(goals("person_o"), "오마한테 저녁 먹으라고 전하고 쟁반을 갖다줄래요", Places(ENV))
    assert out[1]["to"] == "person_me"


def test_no_listener_no_change_and_no_speaker_no_change():
    g = [{"type": "on", "object": "tray", "from": "", "to": "person_o"}]
    assert goal_fix.shared_listener(g, "bring the tray to Oma", Places(ENV))[0] == g
    env = {k: v for k, v in ENV.items() if k != "person_me"}
    assert goal_fix.shared_listener(goals("person_o"), "tell Oma x and bring the tray", Places(env))[0] == goals("person_o")
