"""snapshots/canonicalize.py — deterministic JSON canonicalization + hashing.

The signature and snapshot_hash both cover the canonical form of the payload:
sorted keys, no insignificant whitespace, UTF-8 encoded (snapshot_payload.md
"Signing"). This MUST be byte-identical between agent and server, so the rules
live in one place and are exercised by both.

The `agent.signature` and `agent.snapshot_hash` fields are NOT part of the
signed/hashed body (they are derived from it), so canonicalization strips them
before serializing. The public_key and ruleset_version DO stay in the body.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

#: Fields under `agent` that are derived from the body and therefore excluded
#: from the canonical form that is hashed + signed.
_DERIVED_AGENT_FIELDS = ("signature", "snapshot_hash")


def _strip_derived(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with derived agent fields removed."""
    clone = copy.deepcopy(payload)
    agent = clone.get("agent")
    if isinstance(agent, dict):
        for fld in _DERIVED_AGENT_FIELDS:
            agent.pop(fld, None)
    return clone


def canonical_json(payload: dict[str, Any]) -> str:
    """Canonical JSON string: sorted keys, no whitespace, derived fields stripped."""
    body = _strip_derived(payload)
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """UTF-8 bytes of the canonical JSON — the exact bytes that get signed."""
    return canonical_json(payload).encode("utf-8")


def snapshot_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical bytes, prefixed `sha256:` (snapshot_payload.md)."""
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return f"sha256:{digest}"
