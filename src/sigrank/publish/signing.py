"""publish/signing.py — ed25519 device keypair + snapshot signing.

The agent identifies itself with a per-device ed25519 keypair generated at
`sigrank-agent init` and stored in `~/.sigrank/keypair.json`. Snapshots are signed
over their *canonical bytes* (snapshots/canonicalize.canonical_bytes), which the
server recomputes and verifies against `agent.public_key`.

PyNaCl is imported lazily inside each function so that the rest of the CLI
(`--help`, `version`, `scan`, `compute`) keeps working even if libsodium/PyNaCl
is unavailable; only `init`, `publish`, and `verify` actually require it.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

# Public keys are emitted as `ed25519:<base64>` (cli_commands.md `init` output).
PUBLIC_KEY_PREFIX = "ed25519:"


class SigningUnavailable(RuntimeError):
    """Raised when PyNaCl cannot be imported (signing path unavailable)."""


def _nacl_signing():
    try:
        from nacl import signing  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SigningUnavailable(
            "PyNaCl is required for keypair/signing operations. "
            "Install it with `pip install pynacl`."
        ) from exc
    return signing


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def public_key_raw(public_key: str) -> bytes:
    """Decode an `ed25519:<base64>` public key to its 32 raw bytes."""
    body = public_key[len(PUBLIC_KEY_PREFIX):] if public_key.startswith(
        PUBLIC_KEY_PREFIX
    ) else public_key
    return _b64d(body)


def generate_device_keypair() -> dict[str, str]:
    """Generate a fresh ed25519 keypair + device id.

    Returns a dict with `private_key` (base64), `public_key`
    (`ed25519:<base64>`), and a random `device_id` (uuid4)."""
    signing = _nacl_signing()
    sk = signing.SigningKey.generate()
    vk = sk.verify_key
    return {
        "private_key": _b64e(bytes(sk)),
        "public_key": PUBLIC_KEY_PREFIX + _b64e(bytes(vk)),
        "device_id": str(uuid.uuid4()),
    }


def save_keypair(path: Path, keypair: dict[str, str]) -> Path:
    """Write keypair.json deterministically with restrictive permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(keypair, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    try:
        path.chmod(0o600)  # private key — owner read/write only
    except OSError:  # pragma: no cover - non-POSIX fallback
        pass
    return path


def load_keypair(path: Path) -> dict[str, str]:
    """Load keypair.json. Raises FileNotFoundError if the agent isn't init'd."""
    if not path.exists():
        raise FileNotFoundError(
            f"No keypair at {path}. Run `sigrank-agent init` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def sign_bytes(private_key_b64: str, message: bytes) -> str:
    """Sign `message` with the device private key; return base64 signature."""
    signing = _nacl_signing()
    sk = signing.SigningKey(_b64d(private_key_b64))
    return _b64e(sk.sign(message).signature)


def verify_bytes(public_key: str, message: bytes, signature_b64: str) -> bool:
    """Verify a base64 signature over `message` against an `ed25519:` key."""
    signing = _nacl_signing()
    from nacl.exceptions import BadSignatureError  # type: ignore

    vk = signing.VerifyKey(public_key_raw(public_key))
    try:
        vk.verify(message, _b64d(signature_b64))
        return True
    except BadSignatureError:
        return False
