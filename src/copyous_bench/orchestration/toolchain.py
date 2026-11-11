"""Node/pnpm locations (mise installs by default, overridable by environment)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_MISE = Path.home() / ".local/share/mise/installs"


@dataclass(frozen=True)
class Toolchain:
    node_bin: Path
    pnpm_dir: Path

    @classmethod
    def from_env(cls) -> Toolchain:
        return cls(
            node_bin=Path(os.environ.get("COPYOUS_NODE_BIN", _MISE / "node/26.13.0/bin")),
            pnpm_dir=Path(os.environ.get("COPYOUS_PNPM_DIR", _MISE / "pnpm/12.15.0")),
        )

    @property
    def node(self) -> Path:
        return self.node_bin / "node"

    @property
    def pnpm(self) -> Path:
        return self.pnpm_dir / "pnpm"

    def entries(self) -> list[str]:
        return [str(self.node_bin), str(self.pnpm_dir)]

    def prepend_to(self, path: str) -> str:
        return ":".join([*self.entries(), path])
