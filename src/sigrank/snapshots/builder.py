"""snapshots/builder.py — assemble a Schema v1.0 snapshot payload.

Pulls window telemetry out of the local Store, runs the free-tier metric
proxies, and produces the canonical payload dict described in
snapshot_payload.md (Schema v1.0). The builder is deterministic: all
timestamps and aggregates come from the parsed telemetry, and the caller passes
`submitted_at` explicitly (no wall-clock read here).

The resulting dict is what canonicalize + sign + publish operate on.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sigrank import RULESET_VERSION, SCHEMA_VERSION
from sigrank import __version__ as agent_version
from sigrank.db.store import Store
from sigrank.metrics import (
    background,
    compression,
    cross_thread,
    prompt_complexity,
    session_depth,
    token_throughput,
)

# Windows that map to a fixed number of days back from `window_end`.
# `today` is the single most recent calendar day; `all_time` has no lower bound.
_WINDOW_DAYS: dict[str, int | None] = {
    "today": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all_time": None,
}


@dataclass
class WindowTelemetry:
    """Aggregated raw telemetry for one scoring window."""

    output_tokens: int = 0
    fresh_input_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    sessions_count: int = 0
    turns_total: int = 0
    total_chars: int = 0
    active_minutes_est: int = 0
    models: list[str] = field(default_factory=list)
    earliest_ts: str | None = None
    latest_ts: str | None = None

    @property
    def total_tokens(self) -> int:
        return token_throughput.total_tokens(
            self.output_tokens,
            self.fresh_input_tokens,
            self.cache_read,
            self.cache_creation,
        )


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # Normalize a trailing Z to +00:00 for fromisoformat.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _epoch_days(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / 86400.0


def window_bounds(window_type: str, window_end: datetime) -> tuple[str | None, str]:
    """Return (start_iso | None, end_iso) for the given window relative to end.

    `all_time` returns a None start (open lower bound)."""
    end_iso = window_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    days = _WINDOW_DAYS.get(window_type)
    if days is None:
        return None, end_iso
    start_dt = datetime.fromtimestamp(
        window_end.timestamp() - days * 86400, tz=timezone.utc
    )
    start_iso = start_dt.isoformat().replace("+00:00", "Z")
    return start_iso, end_iso


def aggregate_window(
    store: Store, start_iso: str | None, end_iso: str | None
) -> WindowTelemetry:
    """Aggregate messages + sessions in [start, end) into WindowTelemetry."""
    messages = store.messages_in_window(start_iso, end_iso)
    agg = WindowTelemetry()
    session_ids: set[str] = set()
    models: set[str] = set()
    for m in messages:
        agg.output_tokens += int(m["output_tokens"] or 0)
        agg.fresh_input_tokens += int(m["input_tokens"] or 0)
        agg.cache_read += int(m["cache_read_tokens"] or 0)
        agg.cache_creation += int(m["cache_creation_tokens"] or 0)
        agg.total_chars += int(m["char_len"] or 0)
        agg.turns_total += 1
        session_ids.add(m["session_id"])
        if m["model"]:
            models.add(m["model"])
        ts = m["ts"]
        if ts:
            if agg.earliest_ts is None or ts < agg.earliest_ts:
                agg.earliest_ts = ts
            if agg.latest_ts is None or ts > agg.latest_ts:
                agg.latest_ts = ts
    agg.sessions_count = len(session_ids)
    agg.models = sorted(models)
    agg.active_minutes_est = estimate_active_minutes(agg.earliest_ts, agg.latest_ts, agg.turns_total)
    return agg


def estimate_active_minutes(
    earliest_ts: str | None, latest_ts: str | None, turns: int
) -> int:
    """Estimate active wall-clock minutes for the window.

    Span between first and last message in minutes, floored at one minute per
    turn so single-burst windows still register some activity. Deterministic:
    derived only from parsed timestamps."""
    per_turn_floor = max(1, turns)  # at least ~1 min per turn as a floor
    start = _parse_iso(earliest_ts)
    end = _parse_iso(latest_ts)
    if start and end and end >= start:
        span_minutes = int((end - start).total_seconds() // 60)
        return max(span_minutes, per_turn_floor)
    return per_turn_floor


@dataclass
class LifetimeContext:
    """All-time context the builder needs that spans beyond the window."""

    total_messages_lifetime: int
    account_age_days: int


def lifetime_context(store: Store, prior_lifetime: int = 0) -> LifetimeContext:
    """Compute B.02 / B.03 across ALL stored telemetry (not just the window)."""
    all_msgs = store.all_messages()
    observed = len(all_msgs)
    total_lifetime = background.total_messages_lifetime(observed, prior_lifetime)

    earliest_dt: datetime | None = None
    latest_dt: datetime | None = None
    for m in all_msgs:
        dt = _parse_iso(m["ts"])
        if dt is None:
            continue
        if earliest_dt is None or dt < earliest_dt:
            earliest_dt = dt
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
    age = background.account_age_days(_epoch_days(earliest_dt), _epoch_days(latest_dt))
    return LifetimeContext(total_messages_lifetime=total_lifetime, account_age_days=age)


def build_snapshot(
    *,
    store: Store,
    codename: str,
    device_id: str,
    primary_platform: str,
    window_type: str,
    window_end: datetime,
    submitted_at: datetime,
    public_key: str,
    tier: str = "free",
    prior_lifetime: int = 0,
    drift_ratio: float | None = None,
) -> dict:
    """Build a Schema v1.0 snapshot payload (canonicalize/sign/publish-ready).

    The `agent.signature` and `agent.snapshot_hash` fields are left empty here;
    publish/sign fills them in. All numeric metrics use the free-tier proxies
    unless a precision-tier `drift_ratio` is supplied (Pro path)."""
    start_iso, end_iso = window_bounds(window_type, window_end)
    agg = aggregate_window(store, start_iso, end_iso)
    life = lifetime_context(store, prior_lifetime)

    comp = compression.compression_ratio(agg.output_tokens, agg.fresh_input_tokens)
    ct = cross_thread.cross_thread_score(
        agg.cache_read, agg.cache_creation, agg.fresh_input_tokens
    )
    sd = session_depth.session_depth_avg(agg.turns_total, agg.sessions_count)
    # Scored token_throughput field carries TOTAL tokens (server log-normalizes).
    tt_total = agg.total_tokens
    pc = prompt_complexity.prompt_complexity_placeholder(
        agg.fresh_input_tokens, agg.total_chars, agg.turns_total
    )

    # E.01 Signal Force (raw) — (lifetime_messages × session_depth) / age.
    if life.account_age_days > 0:
        sf_raw = (life.total_messages_lifetime * sd) / life.account_age_days
    else:
        sf_raw = 0.0

    submitted_iso = submitted_at.astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )

    composites: dict[str, float | None] = {
        # signa_rate is intentionally omitted — the server is authoritative and
        # recomputes it from core_metrics (snapshot_payload.md note).
        "signal_force": round(sf_raw, 4),
    }
    if drift_ratio is not None:
        composites["drift_ratio"] = round(drift_ratio, 4)

    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "codename": codename,
        "device_id": device_id,
        "submitted_at": submitted_iso,
        "window": {
            "type": window_type,
            "start": start_iso if start_iso is not None else end_iso,
            "end": end_iso,
        },
        "platform": {
            "primary": primary_platform,
            "models": agg.models,
        },
        "core_metrics": {
            "compression_ratio": round(comp, 4),
            "prompt_complexity": round(pc, 4),
            "cross_thread_score": round(ct, 4),
            "session_depth_avg": round(sd, 4),
            "token_throughput": tt_total,
        },
        "background_metrics": {
            "message_volume": background.message_volume(agg.turns_total),
            "account_age_days": life.account_age_days,
            "total_messages_lifetime": life.total_messages_lifetime,
        },
        "composites": composites,
        "raw_telemetry": {
            "sessions_count": agg.sessions_count,
            "turns_total": agg.turns_total,
            "tokens_total": agg.total_tokens,
            "tokens_input_fresh": agg.fresh_input_tokens,
            "tokens_output": agg.output_tokens,
            "tokens_cache_read": agg.cache_read,
            "tokens_cache_creation": agg.cache_creation,
            "active_minutes_est": agg.active_minutes_est,
        },
        "tier": tier,
        "agent": {
            "version": agent_version,
            "ruleset_version": RULESET_VERSION,
            "snapshot_hash": "",
            "public_key": public_key,
        },
    }
    return payload


def store_snapshot(store: Store, snapshot_id: str, payload: dict, computed_at: str) -> None:
    """Persist a built snapshot into snapshot_local."""
    window = payload.get("window", {})
    store.save_snapshot(
        snapshot_id=snapshot_id,
        window_type=str(window.get("type", "")),
        window_start=window.get("start"),
        window_end=window.get("end"),
        computed_at=computed_at,
        payload=payload,
        snapshot_hash=payload.get("agent", {}).get("snapshot_hash") or None,
    )


def _unused_row_typing(row: sqlite3.Row) -> None:  # pragma: no cover
    """Keep the sqlite3 import meaningful for type readers."""
    _ = row
