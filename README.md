# sigrank-agent

**SigRank local telemetry agent.** Scans your AI platform logs on-device, computes your token cascade, and publishes a signed snapshot to the [SigRank leaderboard](https://signalaf.com).

Token counts only. Nothing else ever leaves your machine.

→ **[signalaf.com](https://signalaf.com)**

---

## What this is for

`sigrank-agent` is for operators who want to get their **real usage data** onto the leaderboard — not a paste, not an estimate, but a locally-computed, cryptographically signed snapshot from your actual log files.

Once published, your profile appears on signalaf.com with your full cascade across all four measurement windows (7d / 30d / 90d / all-time). Your profile workspace on signalaf.com will list every cascade you've published, organized by model and window, so you have a full history of your efficiency over time.

**The companion tool** [`sigrank-mcp`](https://www.npmjs.com/package/sigrank-mcp) lets you check the leaderboard, see your rank, and compare operators from your terminal — no publish needed.

---

## Install

```bash
pip install sigrank-agent
```

Requires Python ≥ 3.11. Or install isolated with `pipx` (recommended):

```bash
pipx install sigrank-agent
sigrank version
```

---

## Quick start

Five commands from zero to published:

```bash
# 1. Initialize — creates ~/.sigrank/ with keypair, database, config
sigrank init

# 2. Point at your Claude logs (the default path)
sigrank source add claude-code ~/.claude/projects

# 3. Scan — reads your logs, loads into local database
sigrank scan

# 4. Compute — runs the metric stack for a window
sigrank compute --window 30d

# 5. Preview — inspect what will be published
sigrank preview

# 6. Publish — sign with ed25519 and POST to signalaf.com
sigrank publish
```

---

## All commands

### `sigrank init`

Initialize `~/.sigrank/` — keypair, config, database, subfolders. Run once.

```
Codename (3-32 chars, letters/digits/-/_): YourCodename
Primary platform [claude]:
Server URL [https://signalaf.com]:

✓ Initialized /Users/you/.sigrank
✓ Generated device keypair: ed25519:xxxx… (signing OFF by default)
✓ Codename: YourCodename
✓ Ready. Run sigrank source add to point at a data source.
```

---

### `sigrank source`

Manage which log directories the agent scans.

```bash
sigrank source add claude-code ~/.claude/projects   # add Claude Code logs
sigrank source list                                  # show configured sources
sigrank source remove claude-code                   # remove a source
```

**Currently supported source type:** `claude-code` — reads `~/.claude/projects/*/*.jsonl`

More adapters coming (Codex, Amp, Gemini, and others are supported in `sigrank-mcp`'s
reader and will land in the agent as the platform list expands).

---

### `sigrank scan`

Parse configured sources into the local SQLite database.

```bash
sigrank scan                          # scan all sources
sigrank scan --source claude-code     # scan one source only
sigrank scan --since 2026-06-01       # only messages on/after this date
```

```
✓ Scanned claude-code  ·  1,208 files  ·  84,302 messages
```

Token counts, model IDs, and message timestamps are stored locally. No content is read.

---

### `sigrank compute`

Run the 11-metric stack over your scanned data for a measurement window.

```bash
sigrank compute                       # default: 30d
sigrank compute --window 7d
sigrank compute --window 90d
sigrank compute --window all_time
```

**Windows:** `today` · `7d` · `30d` · `90d` · `all_time`

```
✓ Computed snapshot  ·  window: 30d  ·  2026-06-24T06:00:00Z

  Υ Yield       12,847.2
  SNR               92.3%
  Leverage       2,041.1x
  10xDEV             3.31
  Velocity           9.0x
  Class         TRANSMITTER

  Pillars
  input          1,251,211
  output        11,296,121
  cache_create 128,196,310
  cache_read 2,555,179,769
```

---

### `sigrank preview`

Inspect the computed snapshot before publishing. Shows the full payload.

```bash
sigrank preview           # formatted display
sigrank preview --json    # raw JSON payload
```

---

### `sigrank publish`

Sign the snapshot with your device keypair (ed25519) and POST it to signalaf.com.

```bash
sigrank publish
```

```
  Signing       ✓
  Posting       ✓
  Validated     ✓

✓ Published. Visit https://signalaf.com/user/YourCodename shortly.
```

The server recomputes the canonical hash and verifies your signature before accepting
the snapshot. Your profile updates within seconds.

---

### `sigrank history`

Show your local publish history.

```bash
sigrank history           # last 20
sigrank history --limit 5
```

```
  #   published_at              window    Υ Yield    class
  ─────────────────────────────────────────────────────────
  1   2026-06-24 06:00:00 UTC   30d       12,847.2   TRANSMITTER
  2   2026-06-23 18:00:00 UTC   7d         8,231.4   TRANSMITTER
  3   2026-06-22 12:00:00 UTC   all_time  18,436.98  TRANSMITTER
```

---

### `sigrank verify`

Re-verify the signature and hash of the most recent snapshot.

```bash
sigrank verify
```

---

### `sigrank config`

Get or set configuration values.

```bash
sigrank config --list                         # show all config
sigrank config codename                       # get one value
sigrank config signing_enabled true           # enable ed25519 signing
sigrank config server_url https://signalaf.com
```

---

### `sigrank version`

```bash
sigrank version

sigrank-agent     │ 0.1.0
schema_version    │ 1.0
ruleset_supported │ 1.0
python            │ 3.11.x
platform          │ darwin / arm64
signing           │ off
```

---

## Your profile on signalaf.com

Once you publish, your profile at `signalaf.com/user/your-codename` shows:

- Your current rank and class tier
- Cascade metrics across all four windows (7d / 30d / 90d / all-time)
- Your full publish history — every cascade you've submitted, by model and window

**Profile workspace** (coming): a full operator workspace where you can manage all
your submitted cascades, see model-by-model breakdowns, and track your efficiency
over time. Future support for importing full sessions and, eventually, chats.

---

## How it works

```
scan (read logs) → compute (metric stack) → preview → publish (sign + POST)
```

- **Local database** — all parsed data lives in `~/.sigrank/db.sqlite`. Nothing leaves your machine until you explicitly `publish`.
- **Deterministic** — given the same logs, the same snapshot is always produced. No randomness, no wall-clock reads in the metric pipeline.
- **Signed** — each snapshot is signed with a per-device ed25519 keypair generated at `init`. The server verifies before accepting.
- **Token-only** — content character *lengths* are used as a proxy for one metric (Prompt Complexity). Content text is never stored, transmitted, or logged.

### Metric stack

| Metric | What it measures |
|---|---|
| **Υ Yield** | `(cache_read × output) / input²` — overall cascade efficiency |
| **SNR** | Signal-to-noise ratio — cache pull vs total push |
| **Leverage** | `cache_read / input` — memory amplification |
| **10xDEV** | `log₁₀(Leverage)` — orders of magnitude above baseline |
| **Velocity** | `output / input` — generation rate |

---

## Configuration reference

`~/.sigrank/` holds everything:

| File | Contents |
|---|---|
| `config.json` | Codename, server URL, signing flag, platform |
| `keypair.json` | ed25519 device keypair (never transmitted) |
| `db.sqlite` | Parsed telemetry, computed snapshots, publish history |
| `imports/` | Reserved for future session import |
| `cache/` | Scan state cache |
| `exports/` | Local snapshot exports |

Override the home directory: `SIGRANK_HOME=/path/to/.sigrank`

Other env vars: `SIGRANK_SERVER` · `SIGRANK_TIER` · `SIGRANK_LOG_LEVEL` · `SIGRANK_NO_COLOR`

---

## Privacy

- **No content, ever.** The agent reads token counts, model IDs, timestamps, and content *lengths* only. Message text never leaves your device.
- **Local by default.** All data stays in `~/.sigrank/` until you run `sigrank publish`.
- **You control the keypair.** Signing is off by default. Enable it when you're ready: `sigrank config signing_enabled true`.
- **Open source.** The full agent source is at [github.com/SunrisesIllNeverSee/sigrank-agent](https://github.com/SunrisesIllNeverSee/sigrank-agent).

---

## Links

- **Leaderboard:** [signalaf.com](https://signalaf.com)
- **Terminal client (view + compare):** [sigrank-mcp on npm](https://www.npmjs.com/package/sigrank-mcp)
- **PyPI:** [pypi.org/project/sigrank-agent](https://pypi.org/project/sigrank-agent/)
- **Source:** [github.com/SunrisesIllNeverSee/sigrank-agent](https://github.com/SunrisesIllNeverSee/sigrank-agent)

---

## License

CC-BY-NC-4.0 — © 2026 Deric J. McHenry / Ello Cello LLC
