"""S16b mutation, the understand prompt as a pack file: one changed word of the text must change a recorded trace (every
cassette with an understand call keys its model answer on the prompt). tools/golden_mutation_check.py loads this file."""


def understand_prompt_one_word(mp):
    from robot_agent.core.planning import assist_loop
    text = assist_loop.UNDERSTAND_PROMPT
    assert "household robot task" in text
    mp.setattr(assist_loop, "UNDERSTAND_PROMPT", text.replace("household robot task", "home robot task", 1))


MUTATIONS = {
    "understand_prompt_one_word": ("one word of the understand prompt text is changed (assist_loop.UNDERSTAND_PROMPT, the "
                                   "standard pack's prompts/understand.en.txt)", understand_prompt_one_word),
}
