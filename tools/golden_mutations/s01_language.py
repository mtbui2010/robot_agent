"""S01 mutations, language and answers (checked by tests/golden/scenario_sets/s01_language.py and s01_answers.py): one behaviour
each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def say_not_translated(mp):
    from robot_agent.core.planning.assist_loop import AssistLoop
    mp.setattr(AssistLoop, "_in_lang", lambda self, text, lang, *a, **k: text)


def korean_phrases_lost(mp):
    from robot_agent.core.planning import speech
    orig = speech.phrase
    mp.setattr(speech, "phrase", lambda robot, key, lang: None if (lang or "en")[:2] == "ko" else orig(robot, key, lang))


def silence_is_yes(mp):
    from robot_agent.interact import dialog
    orig = dialog.yes_no
    mp.setattr(dialog, "yes_no", lambda text: (True, "") if not str(text or "").strip() else orig(text))


def correction_rest_lost(mp):
    from robot_agent.interact import dialog
    orig = dialog.yes_no

    def yes_no(text):
        v, rest = orig(text)
        return (v, "") if v is False else (v, rest)
    mp.setattr(dialog, "yes_no", yes_no)


def menu_retry_unheard(mp):
    from robot_agent.options import bt_goal
    orig = bt_goal.read_menu
    mp.setattr(bt_goal, "read_menu", lambda a, w, p, lg: "none" if orig(a, w, p, lg) == "retry" else orig(a, w, p, lg))


MUTATIONS = {
    "say_not_translated": ("nothing the robot says is translated (AssistLoop._in_lang is the identity)", say_not_translated),
    "korean_phrases_lost": ("no phrase of the robot or of the language pack is found for Korean (speech.phrase)", korean_phrases_lost),
    "silence_is_yes": ("a silent answer to a yes / no question is read as yes (interact.dialog.yes_no)", silence_is_yes),
    "correction_rest_lost": ("the rest of a 'no, ...' answer (the correction) is dropped (interact.dialog.yes_no)", correction_rest_lost),
    "menu_retry_unheard": ("a retry word answered to the bt_goal menu is not heard (options.bt_goal.read_menu)", menu_retry_unheard),
}
