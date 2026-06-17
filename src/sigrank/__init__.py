"""SigRank local telemetry agent.

A privacy-preserving CLI that scans local AI-platform conversation logs,
computes the 11-canonical-metric stack (free-tier proxies), and publishes
signed Schema v1.0 snapshots to the SigRank leaderboard API.

Token telemetry only. No conversation content ever leaves the device on the
free tier; the Pro tier (sig_army) runs entirely locally and only ships the
resulting numeric scores.
"""

__version__ = "0.1.0"

# The scoring ruleset version this agent was built against. Mirrors
# scoring_formula.md "Rule lock — Ruleset v1.0". The server rejects snapshots
# whose ruleset_version is not active, so this is kept in lockstep with CANON.
RULESET_VERSION = "1.0"

# Snapshot payload schema version (snapshot_payload.md).
SCHEMA_VERSION = "1.0"
