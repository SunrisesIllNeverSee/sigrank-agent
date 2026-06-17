"""metrics/compression.py — M.01 Compression Ratio (free-tier proxy).

CANON M.01 / RS.xx. Free-tier proxy from token telemetry:

    compression = output / (output + fresh_input)

Interpretation: how much of the operator's productive token flow is *output*
(signal produced) versus *fresh input* (instruction typed in). A high ratio
means a lot of structured output relative to raw instruction — the BlitzStars
"efficiency" notion. Cache tokens are intentionally excluded from the
denominator: re-reading context is not the same as fresh instruction.

Result is in [0, 1] — matches Core5Raw.compression_ratio in the web app.

// TODO(M.01/RS.01): this is the token-telemetry proxy. The precision-tier
// (sig_army) value refines this with structural-necessity / SNR analysis and
// is delivered via the AuditProvider, not this module.
"""

from __future__ import annotations


def compression_ratio(output_tokens: int, fresh_input_tokens: int) -> float:
    """Return output / (output + fresh_input), clamped to [0, 1].

    Returns 0.0 when there is no productive token flow at all."""
    denom = output_tokens + fresh_input_tokens
    if denom <= 0:
        return 0.0
    ratio = output_tokens / denom
    # Guard against any float drift outside the canonical range.
    return max(0.0, min(1.0, ratio))
