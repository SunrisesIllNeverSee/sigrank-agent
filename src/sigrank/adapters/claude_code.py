"""adapters/claude_code.py — Claude Code session-log adapter.

Reads Claude Code's local transcript files at ~/.claude/projects/*/*.jsonl.
Each line is one JSON event; assistant message events carry a `message` object
with a `usage` block and a `model` field:

    {
      "type": "assistant",
      "message": {
        "model": "claude-opus-4-7",
        "usage": {
          "input_tokens": 1234,
          "output_tokens": 567,
          "cache_read_input_tokens": 89012,
          "cache_creation_input_tokens": 3456
        }
      },
      "timestamp": "2026-05-14T13:42:00.123Z",
      ...
    }

We extract token telemetry from `message.usage` and the model from
`message.model`. One .jsonl file == one session. Lines that do not parse, or do
not carry a usage block, are skipped (we only ever read token counts + model +
content length — never message text content leaves the device).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from sigrank.adapters.base import SourceAdapter
from sigrank.parsers.session import ParsedMessage, ParsedSession

DEFAULT_ROOT = "~/.claude/projects"


def _content_char_len(message: dict) -> int:
    """Best-effort character length of a message's content (for the PC proxy).

    We only count lengths, never retain the text. Content may be a string or a
    list of content blocks (Claude's structured format)."""
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    total += len(text)
            elif isinstance(block, str):
                total += len(block)
        return total
    return 0


class ClaudeCodeAdapter(SourceAdapter):
    """Adapter for Claude Code ~/.claude/projects/*/*.jsonl transcripts."""

    source_type = "claude-code"
    default_root = DEFAULT_ROOT

    def discover(self) -> list[Path]:
        if not self.root.exists():
            return []
        # ~/.claude/projects/<project>/<session>.jsonl — sorted for determinism.
        return sorted(self.root.glob("*/*.jsonl"))

    def parse_file(self, path: Path) -> Iterator[ParsedSession]:
        session_id = self.stable_id(self.source_id, str(path))
        messages: list[ParsedMessage] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for idx, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            role = message.get("role") or event.get("type")
            model = message.get("model")
            ts = event.get("timestamp") or event.get("ts")
            # A message with no usage block still counts as a turn, but carries
            # no token telemetry.
            input_tokens = output_tokens = cache_read = cache_creation = 0
            if isinstance(usage, dict):
                input_tokens = int(usage.get("input_tokens", 0) or 0)
                output_tokens = int(usage.get("output_tokens", 0) or 0)
                cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
                cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
            messages.append(
                ParsedMessage(
                    message_id=self.stable_id(session_id, str(idx)),
                    session_id=session_id,
                    role=role,
                    model=model,
                    ts=ts,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_creation,
                    char_len=_content_char_len(message),
                )
            )
        if not messages:
            return
        start, end = None, None
        stamps = sorted(m.ts for m in messages if m.ts)
        if stamps:
            start, end = stamps[0], stamps[-1]
        yield ParsedSession(
            session_id=session_id,
            source_id=self.source_id,
            platform="claude",
            started_at=start,
            ended_at=end,
            raw_path=str(path),
            messages=messages,
        )
