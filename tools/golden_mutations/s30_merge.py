"""S30 mutations, the merge of a pack chain (checked by the feature cassette s30_remove_replace of
tests/golden/scenario_sets/s30_merge.py): one behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""

import copy


def _merge():
    from robot_agent.core.pack import loader
    return loader, loader.merge


def pack_remove_ignored(mp):
    """``remove`` deletes nothing: the removed kinds stay in the prompt and in the schema enum."""
    loader, orig = _merge()

    def merge(chain):
        plain = []
        for d in chain:
            d = copy.copy(d)
            d.remove = {}
            plain.append(d)
        return orig(plain)
    mp.setattr(loader, "merge", merge)


def pack_entry_deep_merged(mp):
    """An entry of a later pack is merged into the base entry key by key instead of replacing it whole: the base ``prompt`` a
    replaced entry did not declare comes back."""
    loader, orig = _merge()

    def merge(chain):
        tables, origin, patterns, problems = orig(chain)
        seen = {}
        for d in chain:
            for t, names in d.remove.items():
                for n in names:
                    seen.pop((t, n), None)
            for t, entries in d.tables.items():
                for n, e in entries.items():
                    old = seen.get((t, n))
                    seen[(t, n)] = {**old, **copy.deepcopy(e)} if isinstance(old, dict) and isinstance(e, dict) else copy.deepcopy(e)
        for (t, n), e in seen.items():
            if n in tables[t]:
                tables[t][n] = e
        return tables, origin, patterns, problems
    mp.setattr(loader, "merge", merge)


MUTATIONS = {
    "pack_remove_ignored": ("`remove` of a pack deletes no name of its base (core/pack/loader.merge)", pack_remove_ignored),
    "pack_entry_deep_merged": ("an entry of a later pack is merged into the base entry key by key, not replaced whole "
                               "(core/pack/loader.merge)", pack_entry_deep_merged),
}
