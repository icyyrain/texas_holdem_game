"""Which server the desktop client talks to.

Players never type it. ``scripts/build_exe.ps1`` bundles ``assets/server_url.txt`` (kept out of
git, so the address is not published) into the exe; ``HOLDEM_SERVER`` overrides it, and a server
on this computer is the fallback for development.
"""

from __future__ import annotations

import os
from pathlib import Path

from holdem import protocol
from holdem.client.card_images import resource_root

SERVER_FILE = "server_url.txt"
LOCAL_SERVER = f"ws://127.0.0.1:{protocol.DEFAULT_PORT}"


def server_url(root: Path | None = None) -> str:
    override = os.environ.get("HOLDEM_SERVER", "").strip()
    if override:
        return override
    path = (resource_root() if root is None else root) / SERVER_FILE
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        lines = []
    return next((line for line in lines if line and not line.startswith("#")), LOCAL_SERVER)
