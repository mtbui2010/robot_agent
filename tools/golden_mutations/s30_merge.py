"""S30 mutations, the merge of ``extends`` and ``remove`` (checked by the cassettes of tests/golden/scenario_sets/s30_merge.py):
each changes ONE rule of core/pack/loader.py ``merge`` by monkeypatch; tools/golden_mutation_check.py loads this file."""

import copy


def _merge_with(mp, remove=True, later_wins=True, keep_place=True):
    """loader.merge written again with one rule switched off; the same four results."""
    from robot_agent.core.pack import loader

    def merge(chain):
        tables = {t: {} for t in loader.schema.TABLES}
        origin = {t: {} for t in loader.schema.TABLES}
        patterns = []
        for d in chain:
            for t, names in d.remove.items():
                for n in names if remove else ():
                    tables[t].pop(n, None)
                    origin[t].pop(n, None)
            for t, entries in d.tables.items():
                for n, e in entries.items():
                    if n in tables[t] and not later_wins:
                        continue
                    if n in tables[t] and not keep_place:
                        del tables[t][n]
                    tables[t][n] = copy.deepcopy(e)
                    origin[t][n] = d.name
            patterns += copy.deepcopy(d.failure_patterns)
        return tables, origin, patterns, []
    mp.setattr(loader, "merge", merge)


def remove_ignored(mp):
    _merge_with(mp, remove=False)


def base_entry_wins(mp):
    _merge_with(mp, later_wins=False)


def replaced_entry_moves_last(mp):
    _merge_with(mp, keep_place=False)


MUTATIONS = {
    "remove_ignored": ("a pack's `remove` deletes nothing (core/pack/loader.merge): the removed kinds stay in the prompt and enum",
                       remove_ignored),
    "base_entry_wins": ("an entry of a later pack does not replace the base entry (core/pack/loader.merge): the replaced kind keeps "
                        "the base prompt line", base_entry_wins),
    "replaced_entry_moves_last": ("a replaced entry loses its place and comes last (core/pack/loader.merge): the order of the kind "
                                  "lines in the understand prompt changes", replaced_entry_moves_last),
}
