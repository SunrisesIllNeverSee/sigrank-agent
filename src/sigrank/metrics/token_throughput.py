"""metrics/token_throughput.py — M.05 Token Throughput (free-tier proxy).

CANON M.05. Free-tier proxy:

    token_throughput = output_tokens / active_minutes

Interpretation: signal produced per minute of active work — a velocity measure.

NOTE on the bridge: the web app's Core5Raw.token_throughput field and the
scoring_formula TT_SCORE step both log-normalize *total tokens*, not the
per-minute rate (TT_SCORE = min(100, 20·log10(total_tokens + 1))). So the
snapshot's `token_throughput` field carries TOTAL tokens for server scoring,
while this function exposes the human-facing output-per-minute throughput shown
in `preview`. The snapshot builder is responsible for placing total tokens in
the scored field; this module is the display/velocity proxy.

active_minutes is estimated from the session/message timeline (see metrics that
feed the builder); a zero or missing estimate yields 0.0.

// TODO(M.05/RS.xx): output-per-minute is the free-tier velocity proxy. The
// scored field submitted to the server is total tokens (log-normalized server
// side). Precision tier may weight by signal density.
"""

from __future__ import annotations


def token_throughput_per_minute(output_tokens: int, active_minutes: float) -> float:
    """Return output_tokens / active_minutes (signal velocity).

    Returns 0.0 when active_minutes is non-positive."""
    if active_minutes <= 0:
        return 0.0
    return output_tokens / active_minutes


def total_tokens(
    output_tokens: int,
    fresh_input_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
) -> int:
    """Total tokens (in + out + cache) — the value submitted as the scored
    `token_throughput` field for server-side log normalization."""
    return (
        output_tokens
        + fresh_input_tokens
        + cache_read_tokens
        + cache_creation_tokens
    )
