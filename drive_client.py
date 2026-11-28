from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import requests

from config import SyncConfig
from connectcse_client import DownloadedFile

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TOKEN_URI = "https://oauth2.googleapis.com/token"
FILES_URI = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URI = "https://www.googleapis.com/upload/drive/v3/files"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class ExistingDriveIndex:
    source_urls: set[str]
    filenames: set[str]


@dataclass(frozen=True)
class DriveShortcutRequest:
    name: str
    target_id: str
    source_url: str


class GoogleDriveUploader:
    def __init__(self, config: SyncConfig) -> None:
        self.folder_id = config.drive_folder_id
        self.session = requests.Session()
        self._subfolders: dict[str, str] = {}
        access_token = self._fetch_access_token(config)
        self.session.headers.update({"Authorization": f"Bearer {access_token}"})

    def get_or_create_subfolder(self, name: str) -> str:
        if name in self._subfolders:
            return self._subfolders[name]

        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"'{self.folder_id}' in parents and trashed = false "
            f"and mimeType = '{FOLDER_MIME}' and name = '{escaped}'"
        )
        resp = self.session.get(
            FILES_URI,
            params={"q": query, "spaces": "drive", "fields": "files(id)", "pageSize": 1},
            timeout=30,
        )
        resp.raise_for_status()
        files = resp.json().get("files", [])
        if files:
            folder_id = str(files[0]["id"])
        else:
            created = self.session.post(
                FILES_URI,
                params={"fields": "id"},
                json={"name": name, "mimeType": FOLDER_MIME, "parents": [self.folder_id]},
                timeout=30,
            )
            created.raise_for_status()
            folder_id = str(created.json()["id"])

        self._subfolders[name] = folder_id
        return folder_id

    def _fetch_access_token(self, config: SyncConfig) -> str:
        resp = requests.post(
            TOKEN_URI,
            data={
                "client_id": config.google_client_id,
                "client_secret": config.google_client_secret,
                "refresh_token": config.google_refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Failed to refresh Google Drive access token ({resp.status_code}): {resp.text}"
            )
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError("Google token response did not include an access_token.")
        return str(token)

    def list_existing(self, folder_id: str | None = None) -> ExistingDriveIndex:
        target_folder = folder_id or self.folder_id
        source_urls: set[str] = set()
        filenames: set[str] = set()
        page_token: str | None = None
        query = f"'{target_folder}' in parents and trashed = false"

        while True:
            params: dict[str, str | int] = {
                "q": query,
                "spaces": "drive",
                "fields": "nextPageToken, files(id, name, appProperties)",
                "pageSize": 200,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = self.session.get(FILES_URI, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()

            for item in payload.get("files", []):
                name = item.get("name")
                if name:
                    filenames.add(str(name))
                props = item.get("appProperties") or {}
                src = props.get("source_url")
                if src:
                    source_urls.add(str(src))

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return ExistingDriveIndex(source_urls=source_urls, filenames=filenames)

    def upload_file(self, item: DownloadedFile, folder_id: str | None = None) -> str:
        metadata = {
            "name": item.filename,
            "parents": [folder_id or self.folder_id],
            "appProperties": {"source_url": item.source_url},
        }
        boundary = f"connectcse-{uuid.uuid4().hex}"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                json.dumps(metadata).encode("utf-8"),
                f"\r\n--{boundary}\r\n".encode("utf-8"),
                f"Content-Type: {item.mime_type}\r\n\r\n".encode("utf-8"),
                item.content,
                f"\r\n--{boundary}--\r\n".encode("utf-8"),
            ]
        )

        resp = self.session.post(
            UPLOAD_URI,
            params={"uploadType": "multipart", "fields": "id, name"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            data=body,
            timeout=120,
        )
        resp.raise_for_status()
        return str(resp.json()["id"])

    def create_shortcut(self, item: DriveShortcutRequest, folder_id: str | None = None) -> str | None:
        metadata = {
            "name": item.name,
            "mimeType": SHORTCUT_MIME,
            "parents": [folder_id or self.folder_id],
            "shortcutDetails": {"targetId": item.target_id},
            "appProperties": {"source_url": item.source_url},
        }
        resp = self.session.post(
            FILES_URI,
            params={"fields": "id, name"},
            json=metadata,
            timeout=30,
        )
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return str(resp.json()["id"])
