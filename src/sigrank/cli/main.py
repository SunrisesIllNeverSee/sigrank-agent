"""cli/main.py — the `sigrank-agent` command-line interface.

Wires the agent's compute modules (adapters → parser → store → snapshot builder
→ canonicalize → sign → publish) into the command surface documented in
cli_commands.md. The Typer `app` defined here is the console entry point
(`[project.scripts] sigrank-agent = "sigrank.cli.main:app"`). Renamed from the bare
`sigrank` command 2026-06-26 — that name now launches the MCP TUI; the module path
(`sigrank.cli.main`) and the PyPI package (`sigrank-agent`) are unchanged.

Design notes:
  * The CLI is the impure boundary: it reads the wall clock (window anchors,
    timestamps) and the RNG (keygen) so the underlying modules stay
    deterministic and replayable.
  * Every command degrades to a clear message + documented exit code (see
    cli_commands.md "Exit codes") rather than dumping a traceback.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from sigrank import RULESET_VERSION, SCHEMA_VERSION
from sigrank import __version__ as AGENT_VERSION
from sigrank.adapters import AdapterNotImplemented, SourceAdapter, get_adapter
from sigrank.config import settings as cfg
from sigrank.db.store import Store, open_store
from sigrank.snapshots import builder
from sigrank.snapshots.canonicalize import canonical_bytes, snapshot_hash

# ── Exit codes (cli_commands.md "Exit codes") ───────────────────────────────
EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_ADAPTER = 2
EXIT_DB = 3
EXIT_NETWORK = 4
EXIT_SERVER_REJECT = 5
EXIT_CONFIG = 6
EXIT_CANCELLED = 7

console = Console(stderr=False, no_color=bool(os.environ.get("SIGRANK_NO_COLOR")))
err = Console(stderr=True, no_color=bool(os.environ.get("SIGRANK_NO_COLOR")))

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="SigRank local telemetry agent — scan logs, compute the metric stack, "
    "publish signed snapshots.",
)
source_app = typer.Typer(no_args_is_help=True, help="Manage scan sources.")
app.add_typer(source_app, name="source")


# ── helpers ─────────────────────────────────────────────────────────────────


def _now() -> datetime:
    """Wall-clock UTC now. The single impure time read for the whole CLI."""
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    """Parse an ISO date/datetime (date-only allowed) into a UTC datetime."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        err.print(f"[red]Invalid date:[/] {value!r} (use ISO, e.g. 2026-05-08)")
        raise typer.Exit(EXIT_GENERAL) from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _home() -> Path:
    return cfg.sigrank_home()


def _require_settings() -> cfg.Settings:
    try:
        return cfg.Settings.load(_home())
    except FileNotFoundError:
        err.print("[red]Not initialized.[/] Run [bold]sigrank-agent init[/] first.")
        raise typer.Exit(EXIT_CONFIG) from None


def _open_db(*, init: bool = False) -> Store:
    try:
        return open_store(cfg.db_path(_home()), init=init)
    except Exception as exc:  # sqlite errors → DB exit code
        err.print(f"[red]Database error:[/] {exc}")
        raise typer.Exit(EXIT_DB) from exc


def _count_sessions_for_source(store: Store, source_id: str) -> int:
    return sum(1 for s in store.all_sessions() if s["source_id"] == source_id)


# Truthy tokens for the signing on/off switch.
_TRUE = ("1", "true", "yes", "on")


def _signing_on(store: Store) -> bool:
    """Whether to sign snapshots. Env SIGRANK_SIGNING wins; otherwise the
    persisted `signing_enabled` config (`sigrank-agent config signing_enabled true`).
    Default OFF — this version submits token telemetry unsigned."""
    env = os.environ.get("SIGRANK_SIGNING")
    if env is not None:
        return env.strip().lower() in _TRUE
    return (store.get_setting("signing_enabled") or "").strip().lower() in _TRUE


def _signing_status() -> str:
    """Human-readable signing state for `version` (no DB open unless init'd)."""
    env = os.environ.get("SIGRANK_SIGNING")
    if env is not None:
        return "on" if env.strip().lower() in _TRUE else "off"
    home = _home()
    if cfg.is_initialized(home):
        store = open_store(cfg.db_path(home))
        try:
            val = (store.get_setting("signing_enabled") or "").strip().lower()
            return "on" if val in _TRUE else "off"
        finally:
            store.close()
    return "off (default)"


# ── init ────────────────────────────────────────────────────────────────────


@app.command()
def init(
    codename: str = typer.Option(None, help="Operator codename (3-32 chars)."),
    primary_platform: str = typer.Option(
        None, "--platform", help=f"One of: {', '.join(cfg.PLATFORMS)}."
    ),
    server_url: str = typer.Option(None, "--server", help="API server URL."),
    force: bool = typer.Option(False, "--force", help="Re-initialize if present."),
) -> None:
    """Initialize ~/.sigrank/ (keypair, config, database, subfolders)."""
    from sigrank.publish.signing import (
        SigningUnavailable,
        generate_device_keypair,
        save_keypair,
    )

    home = _home()
    if cfg.is_initialized(home) and not force:
        err.print(
            f"[yellow]Already initialized[/] at {home}. Use --force to overwrite."
        )
        raise typer.Exit(EXIT_CONFIG)

    home.mkdir(parents=True, exist_ok=True)
    for sub in cfg.SUBDIRS:
        (home / sub).mkdir(parents=True, exist_ok=True)

    # 1) device keypair — generated when signing is available so signing can be
    # flipped on later. Non-fatal if unavailable (signing is OFF by default).
    keypair = None
    try:
        keypair = generate_device_keypair()
        save_keypair(cfg.keypair_path(home), keypair)
    except SigningUnavailable as exc:
        err.print(
            f"[yellow]Signing unavailable[/] ({exc}). Continuing without a keypair — "
            "signing is off by default; enable later with "
            "[bold]sigrank-agent config signing_enabled true[/]."
        )
    device_id = keypair["device_id"] if keypair else str(uuid.uuid4())

    # 2) prompts (skipped when a flag supplied)
    if codename is None:
        codename = typer.prompt("Codename (3-32 chars, letters/digits/-/_)")
    try:
        codename = cfg.validate_codename(codename)
    except ValueError as exc:
        err.print(f"[red]{exc}")
        raise typer.Exit(EXIT_CONFIG) from exc

    if primary_platform is None:
        primary_platform = typer.prompt(
            f"Primary platform ({'/'.join(cfg.PLATFORMS)})", default="claude"
        )
    try:
        primary_platform = cfg.validate_platform(primary_platform)
    except ValueError as exc:
        err.print(f"[red]{exc}")
        raise typer.Exit(EXIT_CONFIG) from exc

    if server_url is None:
        server_url = typer.prompt("Server URL", default=cfg.DEFAULT_SERVER_URL)

    # 3) persist config + init db
    cfg.Settings(
        codename=codename,
        primary_platform=primary_platform,
        server_url=server_url,
        device_id=device_id,
        home=home,
    ).save()
    _open_db(init=True).close()

    console.print(f"[green]✓[/] Initialized {home}")
    if keypair:
        console.print(
            f"[green]✓[/] Generated device keypair: {keypair['public_key'][:32]}… "
            "(signing OFF by default — enable with `sigrank-agent config signing_enabled true`)"
        )
    else:
        console.print("[green]✓[/] Signing OFF — submitting token telemetry unsigned.")
    console.print(f"[green]✓[/] Codename: {codename}")
    console.print(f"[green]✓[/] Server: {server_url}")
    console.print(
        "[green]✓[/] Ready. Run [bold]sigrank-agent source add[/] to point at a data source."
    )


# ── source add / list / remove ──────────────────────────────────────────────


@source_app.command("add")
def source_add(
    source_type: str = typer.Argument(..., help=f"One of: {', '.join(cfg.SOURCE_TYPES)}."),
    path: str = typer.Argument(..., help="Path to the source logs."),
    name: str = typer.Option(None, "--name", help="Friendly label for the source."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Register a source for the agent to scan."""
    try:
        source_type = cfg.validate_source_type(source_type)
    except ValueError as exc:
        err.print(f"[red]{exc}")
        raise typer.Exit(EXIT_CONFIG) from exc

    root = Path(path).expanduser()
    source_id = SourceAdapter.stable_id(source_type, str(root))
    label = name or source_type

    try:
        adapter = get_adapter(source_type, source_id, root)
    except AdapterNotImplemented as exc:
        err.print(f"[red]{exc}")
        raise typer.Exit(EXIT_ADAPTER) from exc

    discovered = adapter.discover()
    if not root.exists():
        err.print(f"[yellow]Warning:[/] path does not exist yet: {root}")
    console.print(
        f"Source [bold]{label}[/] ({source_type}) → {root}\n"
        f"  Dry scan: found [bold]{len(discovered)}[/] candidate session file(s)."
    )
    if not yes and not typer.confirm("Add this source?", default=True):
        err.print("Cancelled.")
        raise typer.Exit(EXIT_CANCELLED)

    _require_settings()
    store = _open_db(init=True)
    try:
        store.add_source(source_id, source_type, str(root), label, _iso(_now()))
    finally:
        store.close()
    console.print(f"[green]✓[/] Added source [bold]{label}[/]. Run [bold]sigrank-agent scan[/].")


@source_app.command("list")
def source_list() -> None:
    """List configured sources."""
    _require_settings()
    store = _open_db(init=True)
    try:
        rows = store.list_sources()
        if not rows:
            console.print("No sources. Add one with [bold]sigrank-agent source add[/].")
            return
        table = Table(show_edge=False, pad_edge=False)
        for col in ("NAME", "TYPE", "PATH", "SESSIONS", "LAST SCAN"):
            table.add_column(col)
        for r in rows:
            last = store.get_setting(f"lastscan:{r['source_id']}") or "—"
            table.add_row(
                r["label"] or r["source_id"],
                r["source_type"],
                r["path"],
                str(_count_sessions_for_source(store, r["source_id"])),
                last,
            )
        console.print(table)
    finally:
        store.close()


@source_app.command("remove")
def source_remove(name: str = typer.Argument(..., help="Source name or id.")) -> None:
    """Remove a configured source."""
    _require_settings()
    store = _open_db(init=True)
    try:
        n = store.remove_source(name)
    finally:
        store.close()
    if n:
        console.print(f"[green]✓[/] Removed source [bold]{name}[/].")
    else:
        err.print(f"[yellow]No source named[/] {name!r}.")
        raise typer.Exit(EXIT_GENERAL)


# ── scan ────────────────────────────────────────────────────────────────────


@app.command()
def scan(
    source: str = typer.Option(None, "--source", help="Only scan this source."),
    since: str = typer.Option(None, "--since", help="Only messages on/after this date."),
    force: bool = typer.Option(False, "--force", help="Re-scan (reserved)."),
) -> None:
    """Parse sources into the local database."""
    _ = force  # re-scan is always idempotent (INSERT OR REPLACE)
    _require_settings()
    since_iso = _iso(_parse_dt(since)) if since else None
    store = _open_db(init=True)
    try:
        if source:
            row = store.get_source(source)
            if row is None:
                err.print(f"[red]No source named[/] {source!r}.")
                raise typer.Exit(EXIT_GENERAL)
            sources = [row]
        else:
            sources = store.list_sources()
        if not sources:
            console.print("No sources. Add one with [bold]sigrank-agent source add[/].")
            return

        grand_sessions = grand_messages = 0
        for r in sources:
            try:
                adapter = get_adapter(r["source_type"], r["source_id"], r["path"])
            except AdapterNotImplemented as exc:
                err.print(f"[yellow]Skipping {r['label']}: {exc}")
                continue
            s_count = m_count = 0
            for sess in adapter.sessions():
                msgs = sess.messages
                if since_iso is not None:
                    msgs = [m for m in msgs if (m.ts or "") >= since_iso]
                    if not msgs:
                        continue
                start, end = sess.time_bounds()
                store.upsert_session(
                    sess.session_id,
                    r["source_id"],
                    sess.platform,
                    start,
                    end,
                    len(msgs),
                    sess.raw_path,
                )
                m_count += store.upsert_messages(m.to_db_row() for m in msgs)
                s_count += 1
            store.set_setting(f"lastscan:{r['source_id']}", _iso(_now()))
            grand_sessions += s_count
            grand_messages += m_count
            console.print(
                f"Scanning [bold]{r['label']}[/]: {s_count} session(s), "
                f"{m_count:,} message(s)."
            )
        console.print(
            f"[green]✓[/] Scan complete — {grand_sessions} session(s), "
            f"{grand_messages:,} message(s). Run [bold]sigrank-agent compute --window 30d[/]."
        )
    finally:
        store.close()


# ── compute ─────────────────────────────────────────────────────────────────


@app.command()
def compute(
    window: str = typer.Option("30d", "--window", "-w", help=f"{', '.join(cfg.WINDOWS)}."),
    end: str = typer.Option(None, "--end", help="Window anchor (ISO); default now."),
    start: str = typer.Option(None, "--start", help="Window start (ISO); sets anchor."),
) -> None:
    """Compute the metric stack for a measurement window."""
    try:
        window = cfg.validate_window(window)
    except ValueError as exc:
        err.print(f"[red]{exc}")
        raise typer.Exit(EXIT_CONFIG) from exc

    settings = _require_settings()
    from sigrank.publish.signing import SigningUnavailable, load_keypair

    # A keypair is OPTIONAL here: snapshots compute fine unsigned. If one exists
    # we embed its public key so the snapshot is signing-ready; publish decides
    # whether to actually sign (the signing on/off switch).
    keypair: dict[str, str] | None = None
    try:
        keypair = load_keypair(cfg.keypair_path(_home()))
    except (FileNotFoundError, SigningUnavailable):
        keypair = None

    now = _now()
    window_end = _parse_dt(end) if end else now
    if start:
        # Anchor by start: window_end = start + window length (fixed-day windows).
        days = builder._WINDOW_DAYS.get(window)
        start_dt = _parse_dt(start)
        if days is not None:
            window_end = datetime.fromtimestamp(
                start_dt.timestamp() + days * 86400, tz=UTC
            )

    store = _open_db(init=True)
    try:
        payload = builder.build_snapshot(
            store=store,
            codename=settings.codename,
            device_id=settings.device_id or (keypair["device_id"] if keypair else ""),
            primary_platform=settings.primary_platform,
            window_type=window,
            window_end=window_end,
            submitted_at=now,
            public_key=keypair["public_key"] if keypair else "",
            tier=os.environ.get("SIGRANK_TIER", "free"),
        )
        digest = snapshot_hash(payload)
        payload["agent"]["snapshot_hash"] = digest
        sid = f"{digest.split(':', 1)[1][:7]}-{window}"
        builder.store_snapshot(store, sid, payload, _iso(now))
    finally:
        store.close()

    core = payload["core_metrics"]
    bg = payload["background_metrics"]
    comp = payload["composites"]
    w = payload["window"]
    console.print(f"\n[bold]Window:[/] {window}  ({w['start']} → {w['end']})\n")
    console.print("[bold]Core 5[/]")
    console.print(f"  Compression Ratio : {core['compression_ratio']}")
    console.print(f"  Prompt Complexity : {core['prompt_complexity']}")
    console.print(f"  Cross-Thread Score: {core['cross_thread_score']}")
    console.print(f"  Session Depth     : {core['session_depth_avg']}")
    console.print(f"  Token Throughput  : {core['token_throughput']:,}")
    console.print("\n[bold]Background 3[/]")
    console.print(f"  Message Volume    : {bg['message_volume']:,}")
    console.print(f"  Account Age       : {bg['account_age_days']} days")
    console.print(f"  Total Messages    : {bg['total_messages_lifetime']:,}")
    console.print("\n[bold]Composites[/]")
    console.print(f"  Signal Force      : {comp.get('signal_force')}")
    console.print("  (SIGNA RATE computed server-side)")
    console.print(f"\nSnapshot ID: [bold]{sid}[/]\nStatus: ready to publish\n")


# ── preview ─────────────────────────────────────────────────────────────────


@app.command()
def preview(show_json: bool = typer.Option(False, "--json", help="Print raw payload.")) -> None:
    """Display the most recent computed snapshot before publishing."""
    _require_settings()
    store = _open_db(init=True)
    try:
        row = store.latest_snapshot()
    finally:
        store.close()
    if row is None:
        err.print("No snapshot yet. Run [bold]sigrank-agent compute[/] first.")
        raise typer.Exit(EXIT_GENERAL)
    payload = json.loads(row["payload_json"])
    if show_json:
        console.print_json(json.dumps(payload))
        return

    core = payload["core_metrics"]
    raw = payload["raw_telemetry"]
    console.rule("[bold]SIGRANK SNAPSHOT — PREVIEW")
    console.print(f"Codename:  {payload['codename']}")
    console.print(f"Device:    {payload['device_id']}")
    console.print(f"Window:    {payload['window']['type']}  "
                  f"({payload['window']['start']} → {payload['window']['end']})")
    console.print(f"Platform:  {payload['platform']['primary']} "
                  f"({', '.join(payload['platform']['models']) or '—'})")
    console.print(f"Generated: {payload['submitted_at']}\n")
    console.print("[bold]CORE 5[/]")
    for k, v in core.items():
        console.print(f"  {k:<22}{v}")
    console.print("\n[bold]RAW TELEMETRY[/]")
    for k, v in raw.items():
        console.print(f"  {k:<22}{v:,}" if isinstance(v, int) else f"  {k:<22}{v}")
    console.print(f"\nPAYLOAD HASH:  {payload['agent']['snapshot_hash']}")
    console.print(f"RULESET:       {payload['agent']['ruleset_version']}")
    console.print(f"SCHEMA:        {payload['schema_version']}")
    console.print("\nRun [bold]sigrank-agent publish[/] to transmit.")


# ── publish ─────────────────────────────────────────────────────────────────


@app.command()
def publish() -> None:
    """Sign and transmit the most recent snapshot to the API."""
    settings = _require_settings()
    from sigrank.publish.signing import SigningUnavailable, load_keypair, sign_bytes

    store = _open_db(init=True)
    try:
        row = store.latest_snapshot()
        if row is None:
            err.print("No snapshot to publish. Run [bold]sigrank-agent compute[/] first.")
            raise typer.Exit(EXIT_GENERAL)
        payload = json.loads(row["payload_json"])
        sid = row["snapshot_id"]

        console.print(f"Publishing snapshot {sid}...")

        # Signing on/off switch (default OFF — token telemetry submits unsigned).
        signature: str | None = None
        headers: dict[str, str] = {}
        if _signing_on(store):
            try:
                keypair = load_keypair(cfg.keypair_path(_home()))
                signature = sign_bytes(keypair["private_key"], canonical_bytes(payload))
            except (FileNotFoundError, SigningUnavailable) as exc:
                err.print(
                    f"[red]Signing is ON but cannot sign:[/] {exc}\n"
                    "Run [bold]sigrank-agent init[/], or turn it off with "
                    "[bold]sigrank-agent config signing_enabled false[/]."
                )
                raise typer.Exit(EXIT_CONFIG) from exc
            headers["X-Agent-Signature"] = signature
            console.print("  Signing       [green]✓[/]")
        else:
            console.print("  Signing       [dim]— off[/]")

        try:
            import httpx  # lazy: only publish needs the network stack
        except Exception as exc:  # pragma: no cover
            err.print("[red]httpx not installed[/] — cannot publish.")
            raise typer.Exit(EXIT_NETWORK) from exc

        url = settings.server_url.rstrip("/") + "/api/v1/snapshots"
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            store.log_publish(sid, _iso(_now()), "network_error", None, None, signature)
            err.print(f"  Posting       [red]✗[/]\n[red]Network error:[/] {exc}")
            raise typer.Exit(EXIT_NETWORK) from exc

        body: dict | None
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}

        if resp.status_code >= 400:
            store.log_publish(sid, _iso(_now()), "rejected", resp.status_code, body, signature)
            err.print(f"  Posting       [red]✗[/]\nServer rejected ({resp.status_code}): {body}")
            raise typer.Exit(EXIT_SERVER_REJECT)

        store.log_publish(sid, _iso(_now()), "received", resp.status_code, body, signature)
        store.set_setting(f"published:{sid}", "1")
        console.print("  Posting       [green]✓[/]\n  Validated     [green]✓[/]\n")
        console.print(f"Server response:\n  {json.dumps(body, indent=2)}")
        console.print(
            f"\n[green]✓[/] Published. Visit "
            f"https://signalaf.com/user/{settings.codename} shortly."
        )
    finally:
        store.close()


# ── history ─────────────────────────────────────────────────────────────────


@app.command()
def history(limit: int = typer.Option(20, "--limit", help="Max rows.")) -> None:
    """Show local publish history."""
    _require_settings()
    store = _open_db(init=True)
    try:
        rows = store.publish_history(limit=limit)
        if not rows:
            console.print("No publish history yet.")
            return
        table = Table(show_edge=False, pad_edge=False)
        for col in ("WHEN", "SNAPSHOT", "STATUS", "HTTP"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                r["attempted_at"],
                r["snapshot_id"],
                r["status"],
                str(r["http_status"] if r["http_status"] is not None else "—"),
            )
        console.print(table)
    finally:
        store.close()


# ── verify ──────────────────────────────────────────────────────────────────


@app.command()
def verify() -> None:
    """Re-verify the signature + hash of the most recent snapshot."""
    settings = _require_settings()
    from sigrank.publish.signing import SigningUnavailable, load_keypair, verify_bytes

    store = _open_db(init=True)
    try:
        row = store.latest_snapshot()
        if row is None:
            err.print("No snapshot to verify.")
            raise typer.Exit(EXIT_GENERAL)
        payload = json.loads(row["payload_json"])
        sid = row["snapshot_id"]
        # 1) hash integrity
        recomputed = snapshot_hash(payload)
        stored = payload.get("agent", {}).get("snapshot_hash", "")
        hash_ok = bool(stored) and recomputed == stored
        console.print(
            f"Hash      {'[green]✓[/]' if hash_ok else '[red]✗[/]'} {recomputed}"
        )
        # 2) signature (from the latest publish_log entry for this snapshot)
        sig = None
        for h in store.publish_history(limit=200):
            if h["snapshot_id"] == sid and h["signature"]:
                sig = h["signature"]
                break
        if sig is None:
            console.print("Signature  [yellow]—[/] (not published yet; nothing to verify)")
            raise typer.Exit(EXIT_OK if hash_ok else EXIT_GENERAL)
        try:
            keypair = load_keypair(cfg.keypair_path(_home()))
            sig_ok = verify_bytes(keypair["public_key"], canonical_bytes(payload), sig)
        except (FileNotFoundError, SigningUnavailable) as exc:
            err.print(f"[red]Cannot verify signature:[/] {exc}")
            raise typer.Exit(EXIT_CONFIG) from exc
        console.print(f"Signature {'[green]✓[/]' if sig_ok else '[red]✗[/]'}")
        _ = settings
        raise typer.Exit(EXIT_OK if (hash_ok and sig_ok) else EXIT_GENERAL)
    finally:
        store.close()


# ── config ──────────────────────────────────────────────────────────────────

_SETTINGS_KEYS = ("codename", "primary_platform", "server_url", "device_id", "sig_army_dir")


@app.command()
def config(
    key: str = typer.Argument(None, help="Config key."),
    value: str = typer.Argument(None, help="New value (omit to display)."),
    list_all: bool = typer.Option(False, "--list", help="Show all config."),
) -> None:
    """Get or set configuration values."""
    settings = _require_settings()
    store = _open_db(init=True)
    try:
        if list_all or key is None:
            table = Table(show_edge=False, pad_edge=False)
            table.add_column("KEY")
            table.add_column("VALUE")
            for k, v in settings.to_dict().items():
                table.add_row(k, str(v))
            console.print(table)
            return

        if key in _SETTINGS_KEYS:
            if value is None:
                console.print(getattr(settings, key))
            else:
                setattr(settings, key, value)
                settings.save()
                console.print(f"[green]✓[/] {key} = {value}")
        else:
            # Nested / extension keys live in the DB key/value store.
            if value is None:
                console.print(store.get_setting(key) or "(unset)")
            else:
                store.set_setting(key, value)
                console.print(f"[green]✓[/] {key} = {value}")
    finally:
        store.close()


# ── version ─────────────────────────────────────────────────────────────────


@app.command()
def version() -> None:
    """Show agent version + ruleset compatibility."""
    table = Table(show_header=False, show_edge=False, pad_edge=False)
    table.add_row("sigrank-agent", AGENT_VERSION)
    table.add_row("schema_version", SCHEMA_VERSION)
    table.add_row("ruleset_supported", RULESET_VERSION)
    table.add_row("python", _platform.python_version())
    table.add_row("platform", f"{sys.platform} / {_platform.machine()}")
    table.add_row("signing", _signing_status())
    console.print(table)


if __name__ == "__main__":  # pragma: no cover
    app()
