"""adapters — source adapter registry.

Maps a config source_type token (config.SOURCE_TYPES) to the concrete
SourceAdapter subclass that reads that platform's logs. Only `claude-code` is
implemented today; the remaining source types are declared in the config enum
but raise `AdapterNotImplemented` until their adapters land.
"""

from __future__ import annotations

from pathlib import Path

from sigrank.adapters.base import SourceAdapter
from sigrank.adapters.claude_code import ClaudeCodeAdapter

#: source_type → adapter class. Extend as new adapters are written.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    ClaudeCodeAdapter.source_type: ClaudeCodeAdapter,
}


class AdapterNotImplemented(NotImplementedError):
    """Raised for a known source_type that has no adapter yet."""


def get_adapter(source_type: str, source_id: str, root: Path | str) -> SourceAdapter:
    """Instantiate the adapter for `source_type`, rooted at `root`.

    Raises AdapterNotImplemented for a source_type with no registered adapter."""
    cls = ADAPTERS.get(source_type)
    if cls is None:
        raise AdapterNotImplemented(
            f"No adapter for source type '{source_type}'. "
            f"Available: {', '.join(sorted(ADAPTERS)) or '(none)'}."
        )
    return cls(source_id, Path(root))


__all__ = ["ADAPTERS", "AdapterNotImplemented", "get_adapter", "SourceAdapter"]
