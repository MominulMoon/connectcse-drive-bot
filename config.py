from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_EXTENSIONS = (".pdf", ".ppt", ".pptx", ".doc", ".docx")


@dataclass(frozen=True)
class SyncConfig:
    email: str
    password: str
    base_url: str
    posts_url: str
    drive_folder_id: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    allowed_extensions: tuple[str, ...]
    dry_run: bool = False


def load_config(dry_run: bool = False, require_drive: bool = True) -> SyncConfig:
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, interpolate=False, override=True)

    raw_exts = os.getenv("ALLOWED_EXTENSIONS", ",".join(DEFAULT_EXTENSIONS))
    exts = tuple(
        ext.strip().lower() if ext.strip().startswith(".") else f".{ext.strip().lower()}"
        for ext in raw_exts.split(",")
        if ext.strip()
    )

    config = SyncConfig(
        email=os.getenv("CONNECTCSE_EMAIL", "").strip(),
        password=os.getenv("CONNECTCSE_PASSWORD", ""),
        base_url=os.getenv("CONNECTCSE_BASE_URL", "https://connectcse.ruet.ac.bd").rstrip("/"),
        posts_url=os.getenv(
            "CONNECTCSE_POSTS_URL",
            "https://connectcse.ruet.ac.bd/classposts/student",
        ).strip(),
        drive_folder_id=os.getenv(
            "GOOGLE_DRIVE_FOLDER_ID",
            "1Y8PC9eUSeDqeotM4FOrqt0N6wP6s4FTH",
        ).strip(),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        google_refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN", "").strip(),
        allowed_extensions=exts or DEFAULT_EXTENSIONS,
        dry_run=dry_run,
    )

    missing = []
    if not config.email:
        missing.append("CONNECTCSE_EMAIL")
    if not config.password:
        missing.append("CONNECTCSE_PASSWORD")

    if require_drive and not dry_run:
        if not config.drive_folder_id:
            missing.append("GOOGLE_DRIVE_FOLDER_ID")
        if not config.google_client_id:
            missing.append("GOOGLE_CLIENT_ID")
        if not config.google_client_secret:
            missing.append("GOOGLE_CLIENT_SECRET")
        if not config.google_refresh_token:
            missing.append("GOOGLE_REFRESH_TOKEN")

    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {joined}")

    return config
