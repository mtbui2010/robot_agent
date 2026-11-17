# Option `heard_check`: a goal's own quote must be in what was heard

**Class.** The speech recogniser garbles a word and the reader fills the gap with content the person never said: an object
("put the X in the drawer" heard with X garbled, read as a book), or a message (a garbled "go and charge" read as a new say
goal). The goal carries the reader's own quote of the person's words (`said` of an `on` / `in` goal, the message of a `say`
goal), and that quote is not in the heard words. The engine checks words -> goals (`understand.find_issues`: a heard word with no
goal) and F4.8 checks only goals WITHOUT `said`; nothing checks goals -> words, so the run plans the invented goal.

**Rule** (pure function `unheard_goals(goals, heard_tokens, data)`): the quote of an `on` / `in` goal is its `said`, of a `say`
goal its `content` (else its `object`, the message); any other goal, or a goal with an empty quote, is never flagged. The
mentions of the ENV's places and people (heads and aliases, `understand.without_places`) are cut from the heard words and from the
quote. A quote token is FOUND when a heard token shares its first `min_prefix` characters (all of them when the quote token is
shorter), or when the quote token and the heard token cut to the same length have a character similarity (`difflib` ratio) of at
least `min_ratio` (an ASR slip inside the same word). A quote is found when ANY of its tokens is found (the reader may correct a
misheard word or add a colour). Strings are compared lower-cased, as Unicode; no word of any language is in the module.

**Script decision.** `said` is defined by the understand prompt as the person's own words in their language. A quote whose main
script (Unicode script of most of its letters) is not the main script of the heard words cannot be compared letter by letter (the
reader wrote it in another language): it is not checked (fail open), like a goal with no quote. An English run has no `said`.

**Effect.** One issue `{kind: object, what: <quote>}` per unheard goal, added on the read-back (hook `on_issues`, order 82: after
`ask_focus`, which drops an object issue that names a goal field). The engine's own path follows: the re-sample of the
understanding (`understand_samples`, consensus of issue-free readings), else its one question (`issue_question`: `q_repeat`),
then the read-back names the doubt. No new question, no new call. The option only adds issues.

**Data** `planner.options.heard_check.match`: `{min_prefix, min_ratio, source | assumed: true}`; missing or unlabelled: the option
does nothing. Starting values `min_prefix 2`, `min_ratio 0.5` are ASSUMED (one shared syllable or letter pair at the start of the
word; half the letters of the word), not tuned on benchmark data.

**Not covered.** A goal read from a request said DURING a run (`_new_command` / `_llm_new_task`) has no read-back stage, so no
`on_issues` call; a garble the reader corrected to another real word that shares letters with the heard one is accepted.

**Switch off.** Key `enabled` (run option `heard_check`), default off. Lab flag `heard_check` `{goals, unheard}`.
