from __future__ import annotations

import argparse
import sys

from dataclasses import dataclass

from config import load_config
from connectcse_client import ConnectCSEClient, DownloadedFile
from drive_client import DriveShortcutRequest, ExistingDriveIndex, GoogleDriveUploader
from post_parser import PostContent, SharedLink, build_links_html, sanitize_filename


@dataclass
class SyncContext:
    portal: ConnectCSEClient
    drive: GoogleDriveUploader
    existing: ExistingDriveIndex


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync PDF, PowerPoint, Word files, and shared links from ConnectCSE to Google Drive."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log in and list matching files and links without uploading to Google Drive.",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Log in and print each post's files and links for debugging.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum pagination pages to scan (default: 5).",
    )
    return parser.parse_args()


def sync_post(post: PostContent, ctx: SyncContext) -> tuple[int, int]:
    uploaded = 0
    skipped = 0

    for item in post.attachments:
        if item.url in ctx.existing.source_urls:
            skipped += 1
            continue

        downloaded = ctx.portal.download_attachment(item)
        if downloaded is None or downloaded.filename in ctx.existing.filenames:
            skipped += 1
            continue

        file_id = ctx.drive.upload_file(downloaded)
        ctx.existing.source_urls.add(downloaded.source_url)
        ctx.existing.filenames.add(downloaded.filename)
        uploaded += 1
        print(f"Uploaded file: {downloaded.filename} (Drive ID: {file_id})")

    fallback_links: list[SharedLink] = []
    for link in post.shared_links:
        if link.url in ctx.existing.source_urls:
            skipped += 1
            continue

        if link.drive_id:
            shortcut_name = sanitize_filename(
                f"{post.title} - {link.label}" if link.label != post.title else post.title
            )
            if shortcut_name in ctx.existing.filenames:
                skipped += 1
                continue
            shortcut_id = ctx.drive.create_shortcut(
                DriveShortcutRequest(
                    name=shortcut_name,
                    target_id=link.drive_id,
                    source_url=link.url,
                )
            )
            if shortcut_id:
                ctx.existing.source_urls.add(link.url)
                ctx.existing.filenames.add(shortcut_name)
                uploaded += 1
                print(f"Created Drive shortcut: {shortcut_name} (Drive ID: {shortcut_id})")
                continue

        fallback_links.append(link)

    if fallback_links:
        note_key = f"{post.post_url}#links"
        note_name = f"{sanitize_filename(post.title)} - Links.html"
        if note_key in ctx.existing.source_urls or note_name in ctx.existing.filenames:
            skipped += len(fallback_links)
        else:
            note_file = DownloadedFile(
                source_url=note_key,
                filename=note_name,
                mime_type="text/html",
                content=build_links_html(post, fallback_links),
            )
            file_id = ctx.drive.upload_file(note_file)
            ctx.existing.source_urls.add(note_key)
            ctx.existing.filenames.add(note_name)
            uploaded += 1
            print(f"Uploaded links note: {note_name} (Drive ID: {file_id})")

    return uploaded, skipped


def main() -> int:
    args = parse_args()
    no_drive = args.dry_run or args.inspect
    config = load_config(dry_run=no_drive, require_drive=not no_drive)

    portal = ConnectCSEClient(config)
    print("Logging in to ConnectCSE...")
    portal.login()
    print("Login succeeded.")

    if args.inspect:
        print(portal.inspect_page())
        return 0

    posts = portal.discover_posts(max_pages=args.max_pages)
    total_files = sum(len(p.attachments) for p in posts)
    total_links = sum(len(p.shared_links) for p in posts)
    print(f"Scanned {len(posts)} post(s): {total_files} file link(s), {total_links} shared link(s).")

    if args.dry_run:
        for post in posts:
            if not post.attachments and not post.shared_links:
                continue
            print(f"\n[{post.title}] {post.post_url}")
            for att in post.attachments:
                print(f"  FILE: {att.filename} -> {att.url}")
            for link in post.shared_links:
                kind = "DRIVE SHORTCUT" if link.drive_id else "LINK"
                print(f"  {kind}: {link.label} -> {link.url}")
        return 0

    drive = GoogleDriveUploader(config)
    existing = drive.list_existing()
    print(
        f"Drive folder already has {len(existing.filenames)} item(s) "
        f"({len(existing.source_urls)} tracked by source URL)."
    )

    ctx = SyncContext(portal=portal, drive=drive, existing=existing)
    uploaded = 0
    skipped = 0
    for post in posts:
        up, sk = sync_post(post, ctx)
        uploaded += up
        skipped += sk

    print(f"Done. Uploaded/created: {uploaded}, Skipped: {skipped}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
