from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from config import SyncConfig
from post_parser import (
    PostContent,
    RemoteAttachment,
    extract_pagination_urls,
    extract_post_urls,
    parse_post_detail,
    sanitize_filename,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

ALLOWED_MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


@dataclass(frozen=True)
class DownloadedFile:
    source_url: str
    filename: str
    mime_type: str
    content: bytes


class ConnectCSEClient:
    def __init__(self, config: SyncConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def login(self) -> None:
        login_url = f"{self.config.base_url}/login"
        page = self.session.get(login_url, timeout=30)
        page.raise_for_status()

        soup = BeautifulSoup(page.text, "html.parser")
        token_input = soup.find("input", {"name": "_token"})
        if not isinstance(token_input, Tag) or not token_input.get("value"):
            raise RuntimeError("Could not find Laravel _token on the login page.")

        form = soup.find("form")
        action = login_url
        if isinstance(form, Tag) and form.get("action"):
            action = urljoin(login_url, str(form["action"]))

        payload = {
            "_token": str(token_input["value"]),
            "email": self.config.email,
            "password": self.config.password,
        }
        headers = {
            "Referer": login_url,
            "Origin": self.config.base_url,
        }
        resp = self.session.post(
            action,
            data=payload,
            headers=headers,
            timeout=30,
            allow_redirects=True,
        )
        resp.raise_for_status()

        if urlparse(resp.url).path.rstrip("/") == "/login":
            err_soup = BeautifulSoup(resp.text, "html.parser")
            messages = [
                " ".join(el.get_text(" ", strip=True).split())
                for el in err_soup.select(".invalid-feedback, .alert, .text-danger")
                if el.get_text(strip=True)
            ]
            detail = f" Server said: {' | '.join(messages)}" if messages else ""
            raise RuntimeError(
                f"Login failed. Check CONNECTCSE_EMAIL and CONNECTCSE_PASSWORD.{detail}"
            )

    def fetch_posts_html(self, url: str | None = None) -> tuple[str, str]:
        target = url or self.config.posts_url
        resp = self.session.get(target, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        if urlparse(resp.url).path.rstrip("/") == "/login":
            raise RuntimeError("Session redirected to /login while fetching class posts.")
        return resp.url, resp.text

    def discover_posts(self, max_pages: int = 5) -> list[PostContent]:
        visited_pages: set[str] = set()
        pages_to_visit: list[str] = [self.config.posts_url]
        post_urls: list[str] = []
        seen_post_urls: set[str] = set()

        while pages_to_visit and len(visited_pages) < max_pages:
            page_url = pages_to_visit.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            final_url, html = self.fetch_posts_html(page_url)
            for next_page in extract_pagination_urls(self.config.posts_url, html):
                if next_page not in visited_pages and next_page not in pages_to_visit:
                    pages_to_visit.append(next_page)

            for post_url in extract_post_urls(final_url, html):
                if post_url not in seen_post_urls:
                    seen_post_urls.add(post_url)
                    post_urls.append(post_url)

        posts: list[PostContent] = []
        for post_url in post_urls:
            final_post_url, post_html = self.fetch_posts_html(post_url)
            posts.append(
                parse_post_detail(
                    final_post_url,
                    post_html,
                    self.config.allowed_extensions,
                )
            )
        return posts

    def download_attachment(self, item: RemoteAttachment) -> DownloadedFile | None:
        resp = self.session.get(item.url, timeout=60, allow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type.startswith("text/html"):
            return None

        filename = self._filename_from_response(resp, item.filename)
        ext = PurePosixPath(filename).suffix.lower()

        if ext not in self.config.allowed_extensions:
            mapped_ext = ALLOWED_MIME_TO_EXT.get(content_type)
            if mapped_ext and mapped_ext in self.config.allowed_extensions:
                filename = f"{filename}{mapped_ext}"
            else:
                return None

        mime_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return DownloadedFile(
            source_url=item.url,
            filename=filename,
            mime_type=mime_type,
            content=resp.content,
        )

    def inspect_page(self) -> str:
        posts = self.discover_posts(max_pages=2)
        lines = [f"Discovered {len(posts)} post detail page(s):"]
        for post in posts:
            lines.append(f"\n[{post.title}] {post.post_url}")
            for att in post.attachments:
                lines.append(f"  FILE: {att.filename} -> {att.url}")
            for link in post.shared_links:
                kind = f"DRIVE({link.drive_id})" if link.drive_id else "LINK"
                lines.append(f"  {kind}: {link.label} -> {link.url}")
        return "\n".join(lines)

    def _filename_from_response(self, resp: requests.Response, fallback: str) -> str:
        disposition = resp.headers.get("Content-Disposition", "")
        match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
        if match:
            return sanitize_filename(unquote(match.group(1).strip('"')))
        match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
        if match:
            return sanitize_filename(unquote(match.group(1).strip()))
        final_name = unquote(PurePosixPath(urlparse(resp.url).path).name)
        if final_name and "." in final_name:
            return sanitize_filename(final_name)
        return sanitize_filename(fallback)
