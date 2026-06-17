"""metrics/background.py — Background 3 (B.01 / B.02 / B.03).

CANON B.01–B.03. Background metrics carried in the snapshot's
`background_metrics` block.

  B.01 Message Volume (MV)      — messages in the current window.
  B.02 Account Age (AGE)        — days since the account/first-activity date.
  B.03 Total Messages (TM)      — all-time lifetime message count (APPEND-ONLY).

B.03 is APPEND-ONLY: the lifetime total may only ever increase. The agent keeps
a running maximum in the local settings store so a partial scan (e.g. a window
that misses old logs) can never *lower* the reported lifetime count. The caller
passes the prior persisted value and the newly observed count; this module
returns the monotonic maximum.
"""

from __future__ import annotations


def message_volume(window_message_count: int) -> int:
    """B.01 — messages in the current window."""
    return max(0, window_message_count)


def account_age_days(
    earliest_activity_epoch_days: float | None,
    reference_epoch_days: float | None,
) -> int:
    """B.02 — whole days between earliest activity and a reference day.

    Both inputs are epoch-day numbers (days since the Unix epoch) supplied by
    the caller, so this function performs no wall-clock read and stays
    deterministic. Returns 0 when either bound is missing or the span is
    negative."""
    if earliest_activity_epoch_days is None or reference_epoch_days is None:
        return 0
    days = int(reference_epoch_days - earliest_activity_epoch_days)
    return max(0, days)


def total_messages_lifetime(observed_count: int, prior_lifetime: int = 0) -> int:
    """B.03 — APPEND-ONLY lifetime total: max(observed, prior).

    A scan can only ever raise the lifetime count, never lower it, so a partial
    re-scan that sees fewer messages keeps the previously recorded maximum."""
    return max(max(0, observed_count), max(0, prior_lifetime))
