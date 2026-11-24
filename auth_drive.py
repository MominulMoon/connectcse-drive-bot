from __future__ import annotations

import json
import sys
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from drive_client import DRIVE_SCOPE, TOKEN_URI

BOT_DIR = Path(__file__).resolve().parent
ENV_PATH = BOT_DIR / ".env"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"


@dataclass
class OAuthResult:
    code: str = ""
    error: str = ""


def upsert_env_values(env_path: Path, updates: dict[str, str]) -> None:
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            new_lines.append(f"{key}={remaining.pop(key)}")
        else:
            new_lines.append(line)

    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def wait_for_auth_code(client_id: str) -> tuple[str, str]:
    result = OAuthResult()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            params = parse_qs(urlparse(self.path).query)
            if "code" in params:
                result.code = params["code"][0]
                body = b"<h2>Google Drive connected. You can close this tab.</h2>"
            else:
                result.error = params.get("error", ["unknown_error"])[0]
                body = b"<h2>Authorization failed. Check your terminal.</h2>"

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": DRIVE_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    auth_url = f"{AUTH_URI}?{query}"
    print("Opening Google sign-in in your browser...")
    print(f"If the browser does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server.handle_request()
    server.server_close()

    if result.error:
        raise RuntimeError(f"Google authorization failed: {result.error}")
    if not result.code:
        raise RuntimeError("No authorization code was received from Google.")
    return result.code, redirect_uri


def main() -> int:
    secret_path = Path(sys.argv[1]) if len(sys.argv) > 1 else BOT_DIR / "client_secret.json"
    if not secret_path.exists():
        print(
            f"Could not find {secret_path}.\n"
            "Download your OAuth Desktop Client JSON from Google Cloud Console and save it as "
            f"{BOT_DIR / 'client_secret.json'}, then run this script again.",
            file=sys.stderr,
        )
        return 1

    raw = json.loads(secret_path.read_text(encoding="utf-8"))
    client_info = raw.get("installed") or raw.get("web") or {}
    client_id = str(client_info.get("client_id", "")).strip()
    client_secret = str(client_info.get("client_secret", "")).strip()

    if not client_id or not client_secret:
        print("Invalid client_secret.json: missing client_id or client_secret.", file=sys.stderr)
        return 1

    code, redirect_uri = wait_for_auth_code(client_id)
    resp = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    refresh_token = resp.json().get("refresh_token")

    if not refresh_token:
        print(
            "Google did not return a refresh token. Revoke app access in your Google Account "
            "security settings and run auth_drive.py again.",
            file=sys.stderr,
        )
        return 1

    upsert_env_values(
        ENV_PATH,
        {
            "GOOGLE_CLIENT_ID": client_id,
            "GOOGLE_CLIENT_SECRET": client_secret,
            "GOOGLE_REFRESH_TOKEN": str(refresh_token),
        },
    )
    print(f"Saved Google Drive OAuth credentials to {ENV_PATH}.")
    print("To switch Google accounts later, run auth_drive.py again and update GOOGLE_DRIVE_FOLDER_ID.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
