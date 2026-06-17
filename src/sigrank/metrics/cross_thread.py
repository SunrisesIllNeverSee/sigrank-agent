"""metrics/cross_thread.py — M.03 Cross-Thread Referencing (free-tier proxy).

CANON M.03. Free-tier proxy from cache telemetry:

    cross_thread = (cache_read / (cache_read + cache_creation + fresh_input)) * 100

Interpretation: cache *reads* mean the operator is pulling prior context forward
across turns/threads — continuity of signal. Cache *creation* and *fresh input*
represent new context being established. A high ratio of reads to total context
flow proxies strong cross-thread continuity.

Result is in [0, 100] — matches Core5Raw.cross_thread in the web app.

// TODO(M.03/RS.xx): token-telemetry proxy. Precision tier measures actual
// referential links between threads (sig_army thread_map), not cache reuse.
"""

from __future__ import annotations


def cross_thread_score(
    cache_read_tokens: int, cache_creation_tokens: int, fresh_input_tokens: int
) -> float:
    """Return (cache_read / (cache_read + cache_creation + fresh_input)) * 100.

    Clamped to [0, 100]; returns 0.0 when there is no input context at all."""
    denom = cache_read_tokens + cache_creation_tokens + fresh_input_tokens
    if denom <= 0:
        return 0.0
    score = (cache_read_tokens / denom) * 100.0
    return max(0.0, min(100.0, score))
