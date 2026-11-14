"""Load a local `.env` (KEY=VALUE lines) into os.environ without printing anything. Never overrides real env vars.
Used for local runs only; in Railway the variables come from the service settings."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = Path(__file__).resolve().parents[1] / '.env') -> list[str]:
    p = Path(path)
    loaded = []
    if not p.exists():
        return loaded
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded
