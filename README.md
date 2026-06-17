---
type: Reference
title: sigrank-agent
description: Privacy-preserving local telemetry CLI that scans usage data and submits to SigRank.
resource: file:///Users/dericmchenry/Desktop/SigRank/Devins_Plans/sigrank-agent
tags: [sigrank, agent, cli, telemetry]
timestamp: 2026-06-16T00:00:00Z
---

# sigrank-agent

The **SigRank local telemetry agent** — a privacy-preserving CLI that scans your
local AI-platform conversation logs, computes the canonical SigRank metric stack
(free-tier proxies), and publishes **signed Schema v1.0 snapshots** to the
SigRank leaderboard API.

> **Token telemetry only.** On the free tier no conversation content ever leaves
> your device — the agent reads token counts, model ids, and content *lengths*,
> computes numeric scores locally, and ships only the resulting snapshot.

## Install

```bash
pip install -e .            # from this directory
# or, isolated:
python3.11 -m venv .venv && .venv/bin/pip install -e .
```

Requires Python ≥ 3.11.

## Quick start

```bash
sigrank init                                  # create ~/.sigrank (keypair, db, config)
sigrank source add claude-code ~/.claude/projects
sigrank scan                                  # parse sources into the local db
sigrank compute --window 30d                  # compute the metric stack
sigrank preview                               # inspect the snapshot
sigrank publish                               # sign (ed25519) + POST to the API
```

See `sigrank --help` or [`1_sigrank/1.6_agent/cli_commands.md`](../1_sigrank/1.6_agent/cli_commands.md)
for the full command surface (`init`, `source add/list/remove`, `scan`,
`compute`, `preview`, `publish`, `history`, `verify`, `config`, `version`).

## How it works

```
adapters/  →  parsers/  →  db/store (sqlite)  →  snapshots/builder  →  canonicalize → sign → publish
(read logs)   (normalize)   (local cache)         (metric stack)        (ed25519, server-verified)
```

* **Adapters** (`adapters/`) read a platform's native log format. `claude-code`
  (`~/.claude/projects/*/*.jsonl`) ships today; the registry in
  `adapters/__init__.py` is the extension point for the rest.
* **Snapshots** (`snapshots/builder.py`) are deterministic: every aggregate and
  timestamp comes from parsed telemetry, never the wall clock.
* **Signing** (`publish/signing.py`) uses a per-device ed25519 keypair generated
  at `init`; the server recomputes the canonical hash and verifies the signature.

## Configuration

`~/.sigrank/` (override with `SIGRANK_HOME`) holds `config.json`, `keypair.json`,
`db.sqlite`, and `imports/ cache/ exports/`. Other env overrides: `SIGRANK_SERVER`,
`SIGRANK_TIER`, `SIGRANK_LOG_LEVEL`, `SIGRANK_NO_COLOR`.

## License

CC-BY-NC-4.0.
