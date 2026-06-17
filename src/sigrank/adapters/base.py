"""adapters/base.py — the source adapter contract.

An adapter knows how to discover and parse one platform's local log format
into the canonical ParsedSession / ParsedMessage models. The scan pipeline
iterates registered sources, picks the adapter for each source_type, and asks
it to yield sessions.

Adapters must be deterministic: given the same files on disk they must yield
the same sessions in the same order (no RNG, no wall-clock reads). The `--since`
filter is applied by the scanner over the parsed `ts` values, so adapters do
not need to read the clock themselves.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from sigrank.parsers.session import ParsedSession


class SourceAdapter(ABC):
    """Abstract base for all platform log adapters."""

    #: The source_type token this adapter handles (must match config.SOURCE_TYPES).
    source_type: str = ""

    #: Default discovery root (e.g. ~/.claude/projects). Adapters override.
    default_root: str = ""

    def __init__(self, source_id: str, root: Path):
        self.source_id = source_id
        self.root = Path(root).expanduser()

    @abstractmethod
    def discover(self) -> list[Path]:
        """Return the concrete log files this adapter will parse, sorted."""

    @abstractmethod
    def parse_file(self, path: Path) -> Iterator[ParsedSession]:
        """Parse one discovered file into zero or more ParsedSession objects."""

    def sessions(self) -> Iterator[ParsedSession]:
        """Yield all sessions across all discovered files (deterministic order)."""
        for path in self.discover():
            yield from self.parse_file(path)

    # ── shared helpers ────────────────────────────────────────────────────--

    @staticmethod
    def stable_id(*parts: str) -> str:
        """A deterministic id from the given parts (sha1, first 16 hex chars)."""
        joined = "\x1f".join(parts)
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
