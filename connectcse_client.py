from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from config import SyncConfig

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

DOWNLOAD_HINT_RE = re.compile(r"(?:^|/)(download|attachments?|files?|storage|uploads)(?:/|$)", re.I)


@dataclass(frozen=True)
class RemoteAttachment:
    url: str
    filename: str
    post_title: str


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

    def discover_attachments(self, max_pages: int = 5) -> list[RemoteAttachment]:
        visited_pages: set[str] = set()
        pages_to_visit: list[str] = [self.config.posts_url]
        seen_urls: set[str] = set()
        found: list[RemoteAttachment] = []

        while pages_to_visit and len(visited_pages) < max_pages:
            page_url = pages_to_visit.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            final_url, html = self.fetch_posts_html(page_url)
            soup = BeautifulSoup(html, "html.parser")

            for anchor in soup.find_all("a", href=True):
                href = str(anchor["href"]).strip()
                if not href or href.startswith(("#", "javascript:", "mailto:")):
                    continue
                abs_url = urljoin(final_url, href)

                if self._is_pagination_link(anchor, abs_url):
                    if abs_url not in visited_pages and abs_url not in pages_to_visit:
                        pages_to_visit.append(abs_url)
                    continue

                if abs_url in seen_urls:
                    continue

                if self._looks_like_attachment(anchor, abs_url):
                    seen_urls.add(abs_url)
                    filename = self._guess_filename(anchor, abs_url)
                    post_title = self._extract_context_title(anchor)
                    found.append(
                        RemoteAttachment(
                            url=abs_url,
                            filename=filename,
                            post_title=post_title,
                        )
                    )

        return found

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
                ext = mapped_ext
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
        final_url, html = self.fetch_posts_html()
        soup = BeautifulSoup(html, "html.parser")
        lines = [f"Final URL: {final_url}", f"Page title: {soup.title.string.strip() if soup.title and soup.title.string else '(none)'}"]
        anchors = soup.find_all("a", href=True)
        lines.append(f"Total links on page: {len(anchors)}")

        shown = 0
        for anchor in anchors:
            href = str(anchor["href"]).strip()
            abs_url = urljoin(final_url, href)
            text = " ".join(anchor.get_text(" ", strip=True).split())[:80]
            if self._looks_like_attachment(anchor, abs_url) or "classpost" in abs_url.lower():
                lines.append(f"  - text={text!r} | href={abs_url}")
                shown += 1
                if shown >= 40:
                    break

        if shown == 0:
            lines.append("No obvious attachment or classpost links matched. Sample links:")
            for anchor in anchors[:20]:
                href = urljoin(final_url, str(anchor["href"]).strip())
                text = " ".join(anchor.get_text(" ", strip=True).split())[:60]
                lines.append(f"  - text={text!r} | href={href}")

        return "\n".join(lines)

    def _is_pagination_link(self, anchor: Tag, abs_url: str) -> bool:
        parsed = urlparse(abs_url)
        posts_parsed = urlparse(self.config.posts_url)
        if parsed.netloc != posts_parsed.netloc or parsed.path != posts_parsed.path:
            return False
        if "page=" in parsed.query:
            return True
        rel = anchor.get("rel") or []
        return "next" in rel

    def _looks_like_attachment(self, anchor: Tag, abs_url: str) -> bool:
        parsed = urlparse(abs_url)
        path_lower = unquote(parsed.path).lower()
        if any(path_lower.endswith(ext) for ext in self.config.allowed_extensions):
            return True
        if anchor.has_attr("download"):
            return True
        text = " ".join(anchor.get_text(" ", strip=True).split()).lower()
        if any(text.endswith(ext) for ext in self.config.allowed_extensions):
            return True
        if parsed.netloc == urlparse(self.config.base_url).netloc and DOWNLOAD_HINT_RE.search(path_lower):
            if not path_lower.endswith((".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".ico")):
                return True
        return False

    def _guess_filename(self, anchor: Tag, abs_url: str) -> str:
        if anchor.get("download"):
            dl = str(anchor["download"]).strip()
            if dl:
                return _sanitize_filename(dl)
        path_name = unquote(PurePosixPath(urlparse(abs_url).path).name)
        if path_name and "." in path_name:
            return _sanitize_filename(path_name)
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if text:
            return _sanitize_filename(text)
        return "attachment"

    def _filename_from_response(self, resp: requests.Response, fallback: str) -> str:
        disposition = resp.headers.get("Content-Disposition", "")
        match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
        if match:
            return _sanitize_filename(unquote(match.group(1).strip('"')))
        match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
        if match:
            return _sanitize_filename(unquote(match.group(1).strip()))
        final_name = unquote(PurePosixPath(urlparse(resp.url).path).name)
        if final_name and "." in final_name:
            return _sanitize_filename(final_name)
        return _sanitize_filename(fallback)

    def _extract_context_title(self, anchor: Tag) -> str:
        card = anchor.find_parent(class_=re.compile(r"(card|post|item|list-group-item)", re.I))
        if card:
            heading = card.find(["h1", "h2", "h3", "h4", "h5", "strong"])
            if heading:
                return " ".join(heading.get_text(" ", strip=True).split())[:100]
        return ""


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    return cleaned[:180] or "attachment"
