"""The understand prompt as a pack file (Step S16b of docs/proposals/generalize_core_design.md).

The text of ``UNDERSTAND_PROMPT`` lives in the standard pack's ``prompts/understand.en.txt``; the old constant is copied in
tests/legacy_ref/s16b_understand_prompt.py. Proved here: the file is the old constant byte for byte (hash, length, no newline
added at the end), the engine's name is a view of the file, the format slots are the old ones, and the pure rule of
core/pack/prompts.py (the last pack of a chain that holds a prompt gives it) on made-up pack folders. Made-up packs only."""

import hashlib
import string
from pathlib import Path

import pytest

from legacy_ref import s16b_understand_prompt as old
from robot_agent.core.pack import prompts
from robot_agent.core.planning import assist_loop

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "robot_agent/packs/household/prompts/understand.en.txt"


# -- the default data equals the old constant -----------------------------------------------------------------------------------
def test_the_reference_copy_is_the_recorded_constant():
    assert hashlib.sha256(old.LEGACY_UNDERSTAND_PROMPT.encode("utf-8")).hexdigest() == old.LEGACY_SHA256
    assert len(old.LEGACY_UNDERSTAND_PROMPT) == old.LEGACY_LENGTH


def test_the_pack_file_is_the_old_constant_byte_for_byte():
    raw = FILE.read_bytes()
    assert raw == old.LEGACY_UNDERSTAND_PROMPT.encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == old.LEGACY_SHA256
    assert not raw.endswith(b"\n") and b"\r" not in raw


def test_the_engines_name_is_a_view_of_the_file():
    assert assist_loop.UNDERSTAND_PROMPT == old.LEGACY_UNDERSTAND_PROMPT
    assert assist_loop.UNDERSTAND_PROMPT is prompts.text("understand")
    assert prompts.text("understand") is prompts.text("understand")          # read once
    assert prompts.pick(prompts.default_folders(), "understand") == FILE


def test_the_format_slots_are_the_old_ones():
    def slots(t):
        return [n for _, n, _, _ in string.Formatter().parse(t) if n is not None]
    assert slots(assist_loop.UNDERSTAND_PROMPT) == slots(old.LEGACY_UNDERSTAND_PROMPT)
    assert set(slots(old.LEGACY_UNDERSTAND_PROMPT)) == {"abilities", "cannot", "places", "answers", "robot", "goal_kinds", "task",
                                                        "original", "dialogue"}


def test_the_default_chain_is_the_root_pack_then_the_base():
    assert prompts.default_names() == ["core", "household"]
    assert [f.name for f in prompts.default_folders()] == ["core", "household"]


# -- the pure rule on made-up pack folders --------------------------------------------------------------------------------------
def put(folder: Path, name: str, text: str) -> Path:
    f = folder / prompts.FOLDER / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(text.encode("utf-8"))
    return f


def test_the_last_pack_that_holds_a_prompt_gives_it(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    put(a, "understand.en.txt", "from a")
    later = put(b, "understand.en.txt", "from b")
    put(c, "other.en.txt", "not this one")
    assert prompts.pick([a, b, c], "understand") == later
    assert prompts.pick([b, a], "understand") == a / prompts.FOLDER / "understand.en.txt"
    assert prompts.pick([a, b], "understand", "ko") is None
    assert prompts.pick([], "understand") is None


def test_the_language_is_the_first_two_letters():
    assert prompts.file_name("understand") == "understand.en.txt"
    assert prompts.file_name("understand", "ko-KR") == "understand.ko.txt"
    assert prompts.file_name("understand", "") == "understand.en.txt"


def test_a_prompt_no_pack_holds_is_a_key_error():
    with pytest.raises(KeyError):
        prompts.text("no_such_prompt")
