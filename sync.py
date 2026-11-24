from __future__ import annotations

import argparse
import sys

from config import load_config
from connectcse_client import ConnectCSEClient
from drive_client import GoogleDriveUploader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync PDF, PowerPoint, and Word files from ConnectCSE to Google Drive."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log in and list matching files without uploading to Google Drive.",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Log in and print the links found on the student posts page for debugging.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum pagination pages to scan (default: 5).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(dry_run=args.dry_run or args.inspect, require_drive=not (args.dry_run or args.inspect))

    portal = ConnectCSEClient(config)
    print("Logging in to ConnectCSE...")
    portal.login()
    print("Login succeeded.")

    if args.inspect:
        print(portal.inspect_page())
        return 0

    attachments = portal.discover_attachments(max_pages=args.max_pages)
    print(f"Found {len(attachments)} candidate attachment link(s).")

    if args.dry_run:
        for idx, item in enumerate(attachments, start=1):
            label = f" [{item.post_title}]" if item.post_title else ""
            print(f"{idx}. {item.filename}{label} -> {item.url}")
        return 0

    drive = GoogleDriveUploader(config)
    existing = drive.list_existing()
    print(
        f"Drive folder already has {len(existing.filenames)} file(s) "
        f"({len(existing.source_urls)} tracked by source URL)."
    )

    uploaded = 0
    skipped = 0

    for item in attachments:
        if item.url in existing.source_urls:
            skipped += 1
            continue

        downloaded = portal.download_attachment(item)
        if downloaded is None:
            skipped += 1
            continue

        if downloaded.filename in existing.filenames:
            skipped += 1
            continue

        file_id = drive.upload_file(downloaded)
        existing.source_urls.add(downloaded.source_url)
        existing.filenames.add(downloaded.filename)
        uploaded += 1
        print(f"Uploaded: {downloaded.filename} (Drive ID: {file_id})")

    print(f"Done. Uploaded: {uploaded}, Skipped: {skipped}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
