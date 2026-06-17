"""parsers/session.py — canonical normalized session + message models.

Every adapter (claude_code, chatgpt, ...) parses its native log format into
these shapes, so the rest of the pipeline (db store, metrics, snapshot builder)
never has to know which platform a message came from. Pydantic v2 models give
validation + deterministic serialization for free.

Token fields mirror the snapshot raw_telemetry block:
  input_tokens          — non-cached "fresh" input tokens
  output_tokens         — model output tokens
  cache_read_tokens     — cache hits (read)
  cache_creation_tokens — cache writes
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedMessage(BaseModel):
    """One normalized message with its token telemetry."""

    message_id: str
    session_id: str
    role: str | None = None
    model: str | None = None
    # ISO 8601 timestamp string (kept as a string for deterministic ordering).
    ts: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    # Character length of the textual content (used by the PC proxy only).
    char_len: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    def to_db_row(self) -> dict[str, object]:
        """Shape expected by Store.upsert_messages."""
        return {
            "message_id": self.message_id,
            "session_id": self.session_id,
            "role": self.role,
            "model": self.model,
            "ts": self.ts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "char_len": self.char_len,
        }


class ParsedSession(BaseModel):
    """One normalized conversation/session and its messages."""

    session_id: str
    source_id: str
    platform: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    raw_path: str | None = None
    messages: list[ParsedMessage] = Field(default_factory=list)

    @property
    def turns(self) -> int:
        """Conversation turns = message count for this session."""
        return len(self.messages)

    def models_used(self) -> list[str]:
        """Distinct, sorted model identifiers seen in this session."""
        return sorted({m.model for m in self.messages if m.model})

    def time_bounds(self) -> tuple[str | None, str | None]:
        """(earliest ts, latest ts) across messages, falling back to session bounds."""
        stamps = sorted(m.ts for m in self.messages if m.ts)
        start = stamps[0] if stamps else self.started_at
        end = stamps[-1] if stamps else self.ended_at
        return start, end
