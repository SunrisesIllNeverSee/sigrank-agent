"""metrics/prompt_complexity.py — M.02 Prompt Complexity (PLACEHOLDER proxy).

CANON M.02 / RS.04. This is a LOW-CONFIDENCE free-tier PLACEHOLDER. The real
Prompt Complexity is a precision-tier (sig_army) value delivered via the
AuditProvider; the agent must mark the free-tier estimate as confidence='low'
so the web app renders it with the gold-star placeholder treatment, never as a
canonical real value.

Placeholder formula (token-telemetry-only proxy, no content retained):

    PC = min(100, log10(unique_token_estimate + 1) * 20 * length_factor)

where:
  unique_token_estimate ~ a proxy for vocabulary breadth, derived from total
                          fresh-input tokens (we never inspect words on the
                          free tier — only counts), and
  length_factor         ~ a [0, 1] factor reflecting average message length,
                          saturating so very long messages do not run away.

// TODO(M.02/RS.04) low-confidence: this PLACEHOLDER is intentionally crude.
// It exists only so the free tier has *some* PC value to show with a gold star.
// The exact value comes from sig_army via AuditProvider.getExactPromptComplexity
// (confidence='exact'). Do NOT promote this to a real/canonical value.
"""

from __future__ import annotations

import math

#: Average characters per token used to convert char_len → token estimate.
#: A coarse industry rule-of-thumb (~4 chars/token); only affects the proxy.
_CHARS_PER_TOKEN = 4.0

#: Average message length (in tokens) at which length_factor saturates to 1.0.
_LENGTH_SATURATION_TOKENS = 200.0


def _length_factor(avg_message_tokens: float) -> float:
    """A [0, 1] saturating factor from average message length in tokens."""
    if avg_message_tokens <= 0:
        return 0.0
    return min(1.0, avg_message_tokens / _LENGTH_SATURATION_TOKENS)


def prompt_complexity_placeholder(
    fresh_input_tokens: int,
    total_chars: int,
    message_count: int,
) -> float:
    """Return the LOW-CONFIDENCE PC placeholder in [0, 100].

    Args:
        fresh_input_tokens: non-cached input tokens in the window (vocab proxy).
        total_chars: summed content length across messages (length proxy only —
            counts only, never retained text).
        message_count: number of messages in the window.

    // TODO(M.02/RS.04) low-confidence — placeholder only.
    """
    if message_count <= 0 or fresh_input_tokens <= 0:
        return 0.0

    # "unique token estimate" proxy: we cannot count unique tokens without the
    # text, so we use fresh input volume as a breadth stand-in. Crude by design.
    unique_estimate = fresh_input_tokens

    avg_message_tokens = (total_chars / _CHARS_PER_TOKEN) / message_count
    length_factor = _length_factor(avg_message_tokens)

    pc = math.log10(unique_estimate + 1) * 20.0 * length_factor
    return min(100.0, max(0.0, pc))


#: Confidence label the agent attaches to this metric in the snapshot. The web
#: app keys placeholder styling off this being 'low'.
CONFIDENCE = "low"
