# Languages are data

What the core knows about a human language is **data**: one JSON file per language in
`robot_agent/core/planning/lang/<code>.json` (the language pack), the robot pack's i18n phrases and the manifest `planner`
numbers. No code of the package names a language for a rule. A language without a pack gets no behaviour from any pack rule
(fail open); a language with a pack gets exactly what the pack declares. English (`en`), Korean (`ko`) and Vietnamese (`vi`)
ship with the core; `ja` and `zh` are stubs that name the language and its characters.

Inventory of what is still written in code: `python tools/lang_inventory.py` (`--summary`, `--class particles`, `--format json`,
`--check N`). `tests/test_lang_inventory.py` fails when a class that was emptied gets a literal again.

## What a pack may hold

| key | what it is | read by |
|---|---|---|
| `lang`, `name`, `register` | the code, the English name and the register of what the robot says (a translate target needs `name`) | `language.LANGS` / `REGISTER` |
| `words` | regex tables `putin`, `on_top`, `hand`, `first_person`, `recipient`, `tell`, `open_words`, question patterns of the prompt language | `lexicon`, `goal_fix` |
| `patterns` | `into_before` / `into_after`, `ontop_*`, `on_before`, `open_verb`, `close_verb`, `from_after`, `self_correction`, `refusal_safety`, `refusal_capability`, `addressee` (`{name}` field), `answer_tail`, `object_marked` / `object_and`, `hazard_cleaning_*`, `place_food_*` | `langdata.rx(key)` joins the patterns of every language that has the key |
| `grammar` | `noun_particles`, `verb_noun_suffix`, `sep_or`, `speaker_forms`, `speaker_forms_weak`, `ascii_names`, `object_check`, `head_slip_ratio`, `object_particle`, `asr_hallucinations` | `understand`, `speech`, `persons`, `dialog_converge`, `announcer`, `connect.stt` |
| `dialogue` | `yes`, `no`, `fillers`, `near_yes_no`, `yn_words`, `polite`, `stop` / `pause` / `resume` (+ `_prefix`), `tail`, `head`, `filler_tokens`, `negation_*`, `fallback_<intent>[_sub]` | `interact/dialog.py`, `interact/intent_router.py` |
| `dialogue_ext` | polite endings, "not sure" phrases, room names, markers behind the option `dialog_agree2` | `dialog_ext` |
| `script` / `guard` | `letters`, `min_share`, `other_script` (what the language is written in); the translation guard (`forbid`, `min_share`, `latin_words`) | `language.localized`, `speech.guard` |
| `detect_marks`, `latin_script`, `non_ascii_min` | letters that mark a text as written in the language (the translate trigger); a Latin-script language is told by the share of non-ASCII letters | `language`, `lexicon.lang_of`, `understand._find` |
| `answer_unsure`, `articles`, `plural_rules`, `object_aliases` | phrases, label folding and regional names of one object | `dialog_unstick`, `goal_fix` |

Numbers that are not language facts (`restates`, the guard's garble ratio, the person snapshot's trust time) are manifest
`planner` keys; defaults are a priori values the manifest may replace.

The **prompt language** (the language the core's prompts and the model's own questions are written in) is English by default.
A robot sets another with `planner.prompt_lang` (or `$ROBOT_AGENT_PROMPT_LANG`); `langdata.prompt_lang()` is what the core
reads, and the pack of that language holds the patterns for the held reading. The English texts of the core itself
(`CORE_PHRASES`, the prompts, the `pivot_grammar` regexes listed by the inventory) are not data yet: a prompt language other
than English needs those texts too.

## Adding a language (Vietnamese, round 16, is the worked example)

1. Write `lang/<code>.json`. Start from the keys above; every key is optional. Vietnamese has `words` (into, on top, hand,
   recipient, tell, open), `patterns` (into / on-top markers, open and close verbs, self-correction markers, refusal words,
   hazard and food-place words), `grammar` (speaker words, the "or" separator), `dialogue` (yes / no, fillers, polite words,
   control words, negations, keyword fallbacks), `dialogue_ext` (not-sure phrases, room names) and `object_aliases`.
2. Mark who wrote it. `lang/vi.json` carries `review`: drafted by the model, not checked by a native speaker.
3. Add a test through the real `AssistLoop` (`tests/test_lang_vi_pack.py` is the model): the code must not change.

## Scripts: what assumed a non-Latin script and what drives it now

| place | before | now |
|---|---|---|
| `language.localized` | script block, else half of the letters non-ASCII (the pack's `non_ascii_min` only with a lab flag) | a pack with `latin_script` always uses its `non_ascii_min` |
| translate trigger (`language.hear`, `_speak`, `_translate_in`) | a fixed Hangul / Han / kana / Cyrillic regex | `langdata.has_marks`: the `detect_marks` of every pack |
| `speech.guard`, `_script_ok`, `_in_script` | Korean only, by Hangul | the `script` / `guard` blocks; a pack without them is not guarded (fail open: Latin letters cannot be told from English by script) |
| whole-word place match (`understand._find`) | ASCII phrases got word boundaries, Hangul got particles, everything else was a substring | a phrase in Latin letters with marks gets word boundaries when a pack declares `latin_script` |
| `lexicon.lang_of` | the `script.letters` of a pack | also the `detect_marks` of a `latin_script` pack |
| `dialog._vague`, `request_coverage.names_any`, `dialog_converge._toks` | ASCII or Hangul only | the same, plus Latin letters with marks for a `latin_script` pack |
| `dialog_agree` / `dialog_ext` plausibility | the pack's `script` | unchanged (no `script` = every answer plausible) |

Known gaps: `speech.say_name` and the read-back text still use the English texts of the core for the prompt language; a
Latin-script language is not checked for "English leaking into a translation" (no letters tell them apart): the pack's own
phrases (`q_confirm` ...) are the safe path, and the robot pack should supply them.

`isascii()` sites that are left on purpose (all of them read the **prompt language**, English, or fail safe for a Latin-script language):

| place | what it assumes | for a Latin-script language |
|---|---|---|
| `goal_fix.unrequested_open` (a trusted English reading) | the English reading is ASCII | the reading is English by contract; an accented name in it makes the check do nothing |
| `goal_fix.canon_names`, `canon_inventory` | the robot's object memory is named in ASCII when `canonical_lang` is `en` | a memory in another language needs `planner.canonical_lang` |
| `language.declared_name` | an ASCII alias of an ENV entry is its English head | an ASCII-only alias of the language is taken for an English head (a wrong name, never a crash) |
| `dialog_ext.Guard.plausible` | names to cut out before the script share is computed | a pack with no `script` is always plausible |
| `language.localized` | the share of non-ASCII letters | the pack's `non_ascii_min` (see above) |
