"""Connection settings, read from environment variables / a .env file (never from the
command line, so passwords stay out of shell history)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader: KEY=VALUE lines, # comments, optional quotes. Existing env wins."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass
class Config:
    host: str
    user: str | None = None
    password: str | None = None
    domain: str | None = None
    port: int = 3389
    width: int = 1920
    height: int = 1080
    cert_fingerprint: str | None = None
    ignore_cert: bool = False
    settle_seconds: float = 0.8  # screen must be quiet this long before the next step
    settle_timeout: float = 15.0  # give up waiting for a quiet screen after this long
    report_unsettled: bool = True  # note in the log/report when a step's screen never settled
    type_interval: float = 0.0  # pause between typed characters (fast typing can drop keys)
    runs_dir: Path = Path("runs")
    secrets: dict[str, str] = field(default_factory=dict)  # name -> value, used by type_secret

    @classmethod
    def from_env(cls, **overrides) -> Config:
        load_dotenv()
        e = os.environ
        host = overrides.pop("host", None) or e.get("RDP_HOST")
        if not host:
            raise SystemExit("RDP_HOST is not set (put it in .env or pass --host)")
        cfg = cls(
            host=host,
            user=e.get("RDP_USER") or None,
            password=e.get("RDP_PASSWORD") or None,
            domain=e.get("RDP_DOMAIN") or None,
            port=int(e.get("RDP_PORT", "3389")),
            width=int(e.get("RDP_WIDTH", "1920")),
            height=int(e.get("RDP_HEIGHT", "1080")),
            cert_fingerprint=e.get("RDP_CERT_FINGERPRINT") or None,
            ignore_cert=e.get("RDP_IGNORE_CERT", "0") == "1",
        )
        # every RDP_SECRET_<NAME>=value becomes a secret a script can type by name
        for k, v in e.items():
            if k.startswith("RDP_SECRET_") and v:
                cfg.secrets[k.removeprefix("RDP_SECRET_").lower()] = v
        if cfg.password:
            cfg.secrets.setdefault("password", cfg.password)
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        return cfg
