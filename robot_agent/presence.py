"""Which machines run this robot package right now, and on which site.

The configs folder is shared (/remote_dir) by every robot / PC running the same
package. Each backend in server mode rewrites ``common/hosts/<host>.json``
every ``BEAT_SEC``::

    {"host", "boot_id", "pid", "pkg", "location", "seen"}

A record written within ``FRESH_SEC`` is a live machine. It is used to

* warn when two machines are on the same site: they then share its
  connections and Global Configs (``GET /config/locations`` → ``others``);
* refuse renaming / deleting a site another live machine is on;
* catch two machines with the same host id (cloned hostname, e.g. two Jetsons
  both ``m-ax-jetpack``): everything per machine — active location, world
  state, logs, task runs — is keyed by the host id, so they would share it.
  The record being rewritten by another kernel (``boot_id``) after this
  process started → ``clash``; set ``ROBOT_AGENT_HOST`` on one of them.

Clocks: ``seen`` is the writer's time and ages are measured with the reader's;
the robots and the dev PC are NTP-synced (checked 2026-10-08), and
``FRESH_SEC`` is three beats, so a few seconds of skew does not matter.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .core.shared_json import read_fresh
from .state import host_id

BEAT_SEC = 20
FRESH_SEC = 60

logger = logging.getLogger('robot_agent.presence')


def _boot_id() -> str:
    """Same for every process / container of one machine until it reboots."""
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        import uuid
        return f'node-{uuid.getnode():x}'


class Presence:
    def __init__(self, state):
        self.state = state
        self.dir = Path(state.common_dir) / 'hosts'
        self.host = host_id()
        self.boot_id = _boot_id()
        self.clash: dict | None = None      # the other machine using our host id
        self._last_write = time.time()      # records older than this are not a clash
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def file(self) -> Path:
        return self.dir / f'{self.host}.json'

    def _read(self, f: Path) -> dict | None:
        try:
            d = json.loads(read_fresh(f))
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def beat(self) -> None:
        """Note a clash, then (re)write this machine's record."""
        d = self._read(self.file)
        if d and d.get('boot_id') != self.boot_id and float(d.get('seen') or 0) > self._last_write:
            if self.clash is None:
                logger.warning(
                    f'another machine also runs as host "{self.host}" (site {d.get("location")!r}): '
                    f'per-machine files would be shared — set ROBOT_AGENT_HOST on one of them')
            self.clash = d
        rec = {'host': self.host, 'boot_id': self.boot_id, 'pid': os.getpid(),
               'pkg': self.state.robot_pkg, 'location': self.state.location, 'seen': time.time()}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f'.{self.host}.json.tmp.{os.getpid()}'
            tmp.write_text(json.dumps(rec))
            os.replace(tmp, self.file)
            self._last_write = rec['seen']
        except OSError as e:
            logger.warning(f'could not write {self.file}: {e}')

    def start(self) -> None:
        if self._thread is not None:
            return
        self.beat()
        for o in self.others():
            if o['location'] == self.state.location:
                logger.warning(f'site "{self.state.location}" is also in use on {o["host"]}: '
                               f'its connections and Global Configs are shared')

        def loop():
            while not self._stop.wait(BEAT_SEC):
                self.beat()
        self._thread = threading.Thread(target=loop, name='presence', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop beating and drop the record — unless another machine wrote it."""
        self._stop.set()
        d = self._read(self.file)
        if d and d.get('boot_id') == self.boot_id and d.get('pid') == os.getpid():
            try:
                self.file.unlink()
            except OSError:
                pass

    def others(self) -> list[dict]:
        """Live machines other than this one: ``[{host, location, pkg, age_sec}]``
        — including one using our host id (``clash``)."""
        now, out = time.time(), []
        try:
            files = sorted(self.dir.glob('*.json'))
        except OSError:
            files = []
        for f in files:
            if f == self.file:
                continue
            d = self._read(f)
            age = now - float((d or {}).get('seen') or 0)
            if d and age <= FRESH_SEC:
                out.append({'host': d.get('host') or f.stem, 'location': d.get('location'),
                            'pkg': d.get('pkg'), 'age_sec': round(age)})
        c = self.clash
        if c and now - float(c.get('seen') or 0) <= FRESH_SEC:
            out.append({'host': f'{self.host} (another machine, same host id)',
                        'location': c.get('location'), 'pkg': c.get('pkg'),
                        'age_sec': round(now - float(c.get('seen') or 0)), 'clash': True})
        return out

    def users_of(self, location: str) -> list[str]:
        """Other live machines on *location*."""
        return [o['host'] for o in self.others() if o.get('location') == location]
