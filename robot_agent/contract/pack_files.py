"""The files of a pack declaration: ``pack: {file: ..., dir: ...}`` -> one mapping for ``contract.pack.parse``.

What: :func:`read` gathers the header and the sections a pack keeps in files next to its manifest, then parses the whole.
Why: a pack may keep one file per section, so a step (or a robot) adds its own file and edits no shared one
(docs/proposals/generalize_core_design.md, "Where a pack file lives"). Off: a ``pack:`` section with neither ``file`` nor
``dir`` is parsed as written; no file is opened.
"""

from __future__ import annotations

from pathlib import Path

from .pack import SECTIONS, PackDecl, PackError, parse


def _yaml(path: Path, errs: list) -> dict:
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        errs.append(f"{path.name}: cannot be read: {e}")
        return {}
    if data is not None and not isinstance(data, dict):
        errs.append(f"{path.name}: must be a mapping, got {type(data).__name__}")
    return data if isinstance(data, dict) else {}


def read(raw, base=None, where: str = "pack") -> PackDecl:
    """:func:`parse` after the files the pack names are read, relative to ``base`` (the manifest's folder): ``file`` is one
    YAML file with a ``pack:`` header and / or sections at its top level; ``dir`` holds one ``<section>.yaml`` per section
    (its top-level key is the section's name; a missing file is an empty section). A key given in two places is an error."""
    if not isinstance(raw, dict) or not (raw.get("file") or raw.get("dir")):
        return parse(raw, where)
    errs: list = []
    data = {k: v for k, v in raw.items() if k not in ("file", "dir")}
    found = []
    if base is None:
        errs.append(f"{where}: `file` / `dir` need a manifest that was read from a file")
    elif raw.get("file"):
        top = _yaml(Path(base) / str(raw["file"]), errs)
        head = top.pop("pack", None)
        found += [(str(raw["file"]), k, v) for src in (head if isinstance(head, dict) else {}, top) for k, v in src.items()]
    for sec in SECTIONS if base is not None and raw.get("dir") else ():
        f = Path(base) / str(raw["dir"]) / f"{sec}.yaml"
        top = _yaml(f, errs) if f.is_file() else {}
        errs += [f"{f.name}: holds the one top-level key `{sec}`, not {k!r}" for k in top if k != sec]
        found += [(f.name, sec, top.get(sec))]
    for src, k, v in found:
        if v is not None and k in data and data[k] != v:
            errs.append(f"{where}.{k}: given twice (also in {src})")
        elif v is not None:
            data[k] = v
    try:
        decl = parse(data, where)
    except PackError as e:
        raise PackError(errs + e.errors) from e
    if errs:
        raise PackError(errs)
    return decl


__all__ = ["read"]
