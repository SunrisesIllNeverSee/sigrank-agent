"""metrics/session_depth.py — M.04 Session Depth (free-tier proxy).

CANON M.04. Free-tier proxy:

    session_depth = turns / sessions     (average turns per session)

Interpretation: deeper sessions mean longer sustained reasoning chains rather
than shallow one-shot prompts. This is the raw `session_depth_avg` the server
later bucketizes via RS.02 — the agent submits the raw average, NOT the bucket
score.

Result is in [0, ∞) — matches Core5Raw.session_depth (raw) in the web app.

// TODO(M.04/RS.02): the agent submits the raw turns/sessions average; the
// server applies the RS.02 bucketization (depth_score) at scoring time.
"""

from __future__ import annotations


def session_depth_avg(turns_total: int, sessions_count: int) -> float:
    """Return turns_total / sessions_count (average turns per session).

    Returns 0.0 when there are no sessions."""
    if sessions_count <= 0:
        return 0.0
    return turns_total / sessions_count
