"""Configuration loading for the InformaCast Fusion report tool.

Reads from environment variables (optionally populated from a .env file via
python-dotenv). Keeping credentials out of source and out of CLI args avoids
tokens ending up in shell history or process listings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # no-op if .env doesn't exist


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    token: str
    base_url: str
    timeout: int

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("IC_FUSION_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "IC_FUSION_TOKEN is not set. Copy .env.example to .env and add your "
                "Fusion API bearer token (Admin > Users > your account > User Tokens)."
            )

        base_url = os.environ.get(
            "IC_FUSION_BASE_URL", "https://api.icmobile.singlewire.com/api/v1"
        ).rstrip("/")

        timeout_raw = os.environ.get("IC_FUSION_TIMEOUT", "30")
        try:
            timeout = int(timeout_raw)
        except ValueError:
            raise ConfigError(f"IC_FUSION_TIMEOUT must be an integer, got {timeout_raw!r}")

        return cls(token=token, base_url=base_url, timeout=timeout)
