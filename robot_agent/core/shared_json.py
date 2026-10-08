"""JSON files that several machines share and edit.

The configs folder lives on /remote_dir, mounted (sshfs) by every robot / PC
that runs the same robot package. Each backend kept a file's content in memory
and wrote ALL of it back on every change, so a save on one machine silently
undid whatever another machine had saved since it last read the file: robot A
adds a plan skill, robot B then edits an alias → B writes its own list and A's
plan skill is gone.

``SharedJson`` turns a save into a merge:

* ``read()`` returns the file's content and remembers it as the *base*;
* ``save(mine)`` re-reads the file (*theirs*) under a lock, keeps every item
  this process did not touch since its base, applies the ones it did (added,
  changed, removed, reordered), writes the result atomically (a tmp file of
  its own, then rename — the file never disappears; the previous version is
  kept as ``.bak``) and returns what it wrote. The caller adopts that, so it
  also picks up the other machines' changes;
* ``changed()`` tells — at most one ``stat`` per ``check_sec`` — that another
  process rewrote the file, so the owner can reload before answering a read.

An item changed on both sides keeps the value of the later save.

Locking: a thread lock, plus ``flock`` on ``.<file>.lock`` for processes on
the same machine. sshfs does not pass locks on to the other machines, so two
saves within the same few milliseconds could still race; re-reading right
before the write is what keeps that window that small.

sshfs caches attributes (measured on the robot, 2026-10-08): after another
machine rewrote a file, a plain ``stat`` kept the old size / mtime for ~19 s,
and the first ``open`` + ``read`` returned the new content cut to the OLD size
(``{"a": 1,``). An ``open`` refreshes the cache, so :func:`read_fresh` re-reads
when the size it got differs from the size after the read, and
:func:`stat_fresh` opens the file before ``stat``.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

_MISSING = object()


def stat_fresh(path):
    """``os.stat`` after an ``open`` — which makes sshfs drop its cached
    attributes (a bare stat can be ~20 s old). None when missing."""
    try:
        os.close(os.open(path, os.O_RDONLY))
    except OSError:
        pass
    try:
        return os.stat(path)
    except OSError:
        return None


def read_fresh(path) -> str:
    """Text of *path*, re-read when sshfs served it cut to a stale size.
    Raises OSError like ``read_text`` when it cannot be read."""
    path = Path(path)
    data = b''
    for _ in range(4):
        data = path.read_bytes()
        try:
            if os.stat(path).st_size == len(data):
                break
        except OSError:
            break
    return data.decode('utf-8')


def merge_map(base: dict | None, mine: dict | None, theirs: dict | None) -> dict:
    """3-way merge of ordered ``{key: value}`` maps.

    A key whose value *mine* changed since *base* (added, edited or removed)
    takes *mine*; every other key takes *theirs*. Order: *theirs*', with a key
    only *mine* has right after its predecessor in *mine* (a renamed item
    keeps its place) — unless *mine* reordered the keys it shares with *base*:
    then *mine*'s, with the keys only *theirs* has at the end."""
    base, mine, theirs = base or {}, mine or {}, theirs or {}
    values = {}
    for k in [*mine, *theirs, *base]:
        if k in values:
            continue
        m = mine.get(k, _MISSING)
        v = m if m != base.get(k, _MISSING) else theirs.get(k, _MISSING)
        if v is not _MISSING:
            values[k] = v
    if [k for k in mine if k in base] != [k for k in base if k in mine]:
        order = [k for k in mine if k in values]
        order += [k for k in theirs if k in values and k not in order]
    else:
        order = [k for k in theirs if k in values]
        prev = None
        for k in mine:
            if k not in values:
                continue
            if k not in order:
                order.insert(order.index(prev) + 1 if prev in order else 0, k)
            prev = k
    order += [k for k in values if k not in order]     # (cannot happen; never drop a value)
    return {k: values[k] for k in order}


def keyed(items, key: Callable[[dict], str]) -> dict:
    """List of dicts → ordered ``{key: item}``. Items sharing a key (two
    connections named ``rf-detr`` with no id) are told apart by their rank:
    ``rf-detr``, ``rf-detr#1`` … — non-dict items are dropped."""
    out, seen = {}, {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        k = str(key(it))
        n = seen.get(k, 0)
        seen[k] = n + 1
        out[k if n == 0 else f'{k}#{n}'] = it
    return out


def merge_list(key: Callable[[dict], str]):
    """A ``merge`` for a JSON list of dicts identified by ``key(item)``."""
    def merge(base, mine, theirs):
        return list(merge_map(keyed(base, key), keyed(mine, key), keyed(theirs, key)).values())
    return merge


def _host() -> str:
    from ..state import host_id
    return host_id()


class SharedJson:
    """One shared JSON file; see the module doc.

    ``merge(base, mine, theirs) -> result`` combines whole documents (``None``
    for a side that has no file); the default merges top-level dict keys."""

    def __init__(self, path, merge: Callable | None = None, default=None,
                 indent: int = 2, ensure_ascii: bool = True, check_sec: float = 1.0):
        self.path = Path(path)
        self.merge = merge or merge_map
        self.default = default
        self.indent, self.ensure_ascii = indent, ensure_ascii
        self.check_sec = check_sec
        self.base = None                  # the document as last read / written
        self._stamp = None                # (mtime_ns, size) of that version
        self._checked = 0.0
        self._lock = threading.RLock()

    @property
    def _bak(self) -> Path:
        return self.path.with_name(self.path.name + '.bak')     # skills.json.bak

    # ── reading ─────────────────────────────────────────────────────────────
    def _stat(self):
        st = stat_fresh(self.path)
        return (st.st_mtime_ns, st.st_size) if st is not None else None

    def _read_disk(self):
        """(document, stamp); the ``.bak`` (stamp None) when the file does not
        parse; (None, None) when there is no file."""
        stamp = self._stat()                 # before reading: a later change is still noticed
        try:
            return json.loads(read_fresh(self.path)), stamp
        except FileNotFoundError:
            return None, None
        except (OSError, ValueError) as e:   # (UnicodeDecodeError is a ValueError)
            print(f'[SharedJson] {self.path.name} unreadable ({e}); trying the backup')
        try:
            return json.loads(read_fresh(self._bak)), None
        except (OSError, ValueError):
            return None, None

    def read(self):
        """The file's document (``default`` when missing); it becomes the base."""
        with self._lock:
            doc, stamp = self._read_disk()
            # base is a private copy: the caller may edit what it gets in place
            self.base, self._stamp = copy.deepcopy(doc), stamp
            self._checked = time.monotonic()
            return doc if doc is not None else self.default

    def changed(self, force: bool = False) -> bool:
        """True when the file is not the version this process last read or
        wrote — another process saved it since. One ``stat`` per check_sec."""
        now = time.monotonic()
        if not force and now - self._checked < self.check_sec:
            return False
        self._checked = now
        stamp = self._stat()
        return stamp is not None and stamp != self._stamp

    # ── writing ─────────────────────────────────────────────────────────────
    def save(self, mine, merge: bool = True):
        """Write *mine* merged with whatever the file holds now; returns the
        document written (adopt it). ``merge=False`` writes *mine* as is."""
        with self._lock, self._file_lock():
            doc = mine
            if merge:
                theirs, _ = self._read_disk()
                if theirs is not None:
                    doc = self.merge(self.base, mine, theirs)
            self._write(doc)
            self.base, self._stamp = copy.deepcopy(doc), self._stat()
            self._checked = time.monotonic()
            return doc

    def _write(self, doc) -> None:
        text = json.dumps(doc, indent=self.indent, ensure_ascii=self.ensure_ascii)
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f'.{p.name}.tmp.{_host()}.{os.getpid()}')
        tmp.write_text(text, encoding='utf-8')
        if p.exists():
            try:                            # copy, never move: the file must not vanish
                shutil.copyfile(p, self._bak)
            except OSError:
                pass
        os.replace(tmp, p)

    def _file_lock(self):
        return _FileLock(self.path.with_name(f'.{self.path.name}.lock'))


class _FileLock:
    """flock on a side file — serialises the processes of one machine."""

    def __init__(self, path: Path):
        self.path, self.fd = path, None

    def __enter__(self):
        try:
            import fcntl
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o664)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except Exception:
            self._close()
        return self

    def __exit__(self, *exc):
        self._close()
        return False

    def _close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)           # closing releases the flock
            except OSError:
                pass
            self.fd = None
