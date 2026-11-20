"""Where things are inside the container (the mounts the make target sets); each can be moved with an environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    runs: Path
    data: Path
    code: Path
    cache: Path

    @classmethod
    def from_env(cls) -> "Paths":
        e = os.environ
        return cls(Path(e.get("VLA_RUNS", "/runs")), Path(e.get("VLA_DATA", "/data")), Path(e.get("VLA_CODE", "/code")), Path(e.get("VLA_CACHE", "/cache")))

    def run_dir(self, name: str) -> Path:
        return self.runs / name

    def ckpt_root(self, name: str) -> Path:
        return self.run_dir(name) / "checkpoints"
