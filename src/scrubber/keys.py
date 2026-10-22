"""The pseudonymization secret: generated on this machine on first run and kept
in the state folder. It is never written to the output folder."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

KEY_FILE = "secret.key"


def load_or_create_key(state_dir: Path) -> bytes:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / KEY_FILE
    if path.exists():
        key = bytes.fromhex(path.read_text().strip())
        if len(key) < 32:
            raise SystemExit(f"{path} is corrupt (expected 32+ bytes of hex)")
        return key
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.hex() + "\n")
    return key
