"""config/settings.py — agent configuration + filesystem layout.

The agent stores everything under ~/.sigrank (override with SIGRANK_HOME).
Configuration is a small JSON document (config.json) that `sigrank init`
writes and the other commands read. The ed25519 keypair lives in keypair.json
alongside it; the SQLite database in db.sqlite.

Nothing here reads the wall clock or an RNG at import time — all paths are pure
functions of the environment, and config is loaded lazily on demand.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_SERVER_URL = "https://signalaf.com"

# Codename rules (init prompt): 3-32 chars, alphanumeric plus dash/underscore.
CODENAME_MIN = 3
CODENAME_MAX = 32
CODENAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

# Primary platform enum — mirrors snapshot_payload.md platform.primary.
PLATFORMS: tuple[str, ...] = ("claude", "chatgpt", "gemini", "pi", "multi", "other")

# Source type enum — the adapter families the agent can scan.
SOURCE_TYPES: tuple[str, ...] = (
    "claude-code",
    "chatgpt",
    "cursor",
    "gemini",
    "codex",
    "pi",
    "generic-json",
)

# Scoring windows the `compute` command accepts (snapshot_payload window.type).
WINDOWS: tuple[str, ...] = ("today", "7d", "30d", "90d", "all_time")

# Sub-directory layout inside ~/.sigrank.
SUBDIRS: tuple[str, ...] = ("imports", "cache", "exports")

CONFIG_FILENAME = "config.json"
KEYPAIR_FILENAME = "keypair.json"
DB_FILENAME = "db.sqlite"


# ── Path helpers ───────────────────────────────────────────────────────────--


def sigrank_home() -> Path:
    """Root config directory. Honors SIGRANK_HOME, else ~/.sigrank."""
    override = os.environ.get("SIGRANK_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sigrank"


def config_path(home: Path | None = None) -> Path:
    return (home or sigrank_home()) / CONFIG_FILENAME


def keypair_path(home: Path | None = None) -> Path:
    return (home or sigrank_home()) / KEYPAIR_FILENAME


def db_path(home: Path | None = None) -> Path:
    return (home or sigrank_home()) / DB_FILENAME


# ── Validation ─────────────────────────────────────────────────────────────--


def validate_codename(codename: str) -> str:
    """Return the codename if valid, else raise ValueError with a clear message."""
    if not CODENAME_RE.match(codename):
        raise ValueError(
            f"Codename must be {CODENAME_MIN}-{CODENAME_MAX} characters, "
            "letters/digits/dash/underscore only."
        )
    return codename


def validate_platform(platform: str) -> str:
    if platform not in PLATFORMS:
        raise ValueError(f"Platform must be one of: {', '.join(PLATFORMS)}")
    return platform


def validate_source_type(source_type: str) -> str:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"Source type must be one of: {', '.join(SOURCE_TYPES)}")
    return source_type


def validate_window(window: str) -> str:
    if window not in WINDOWS:
        raise ValueError(f"Window must be one of: {', '.join(WINDOWS)}")
    return window


# ── Settings document ──────────────────────────────────────────────────────--


@dataclass
class Settings:
    """The persisted agent configuration (config.json)."""

    codename: str
    primary_platform: str
    server_url: str = DEFAULT_SERVER_URL
    device_id: str = ""
    # SIG_ARMY_DIR for Pro-tier integration. Empty => use the env / default.
    sig_army_dir: str = ""
    home: Path = field(default_factory=sigrank_home)

    def to_dict(self) -> dict[str, str]:
        return {
            "codename": self.codename,
            "primary_platform": self.primary_platform,
            "server_url": self.server_url,
            "device_id": self.device_id,
            "sig_army_dir": self.sig_army_dir,
        }

    def save(self) -> Path:
        """Write config.json deterministically (sorted keys, 2-space indent)."""
        path = config_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, home: Path | None = None) -> Settings:
        """Load config.json. Raises FileNotFoundError if the agent isn't init'd."""
        home = home or sigrank_home()
        path = config_path(home)
        if not path.exists():
            raise FileNotFoundError(
                f"No SigRank config at {path}. Run `sigrank init` first."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            codename=data["codename"],
            primary_platform=data["primary_platform"],
            server_url=data.get("server_url", DEFAULT_SERVER_URL),
            device_id=data.get("device_id", ""),
            sig_army_dir=data.get("sig_army_dir", ""),
            home=home,
        )


def is_initialized(home: Path | None = None) -> bool:
    """True if `sigrank init` has been run (config + keypair + db present)."""
    home = home or sigrank_home()
    return (
        config_path(home).exists()
        and keypair_path(home).exists()
        and db_path(home).exists()
    )
