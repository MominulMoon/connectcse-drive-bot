from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>'\"()]+", re.I)
POST_DETAIL_RE = re.compile(r"/classposts/(\d+)/?$")
DOWNLOAD_ROUTE_RE = re.compile(r"/classposts/\d+/download(?:/\d+)?/?$", re.I)

GOOGLE_DRIVE_PATTERNS = (
    re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)"),
    re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"docs\.google\.com/(?:document|presentation|spreadsheets|forms)/d/([a-zA-Z0-9_-]+)"),
)


@dataclass(frozen=True)
class RemoteAttachment:
    url: str
    filename: str
    post_title: str


@dataclass(frozen=True)
class SharedLink:
    url: str
    label: str
    post_url: str
    post_title: str
    drive_id: str | None


@dataclass(frozen=True)
class PostContent:
    post_url: str
    title: str
    attachments: tuple[RemoteAttachment, ...]
    shared_links: tuple[SharedLink, ...]


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    return cleaned[:180] or "attachment"


def extract_google_drive_id(url: str) -> str | None:
    for pattern in GOOGLE_DRIVE_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    parsed = urlparse(url)
    if parsed.netloc.endswith("drive.google.com"):
        ids = parse_qs(parsed.query).get("id")
        if ids:
            return ids[0]
    return None


def extract_post_urls(page_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        abs_url = urljoin(page_url, str(anchor["href"]).strip())
        parsed = urlparse(abs_url)
        if parsed.netloc == urlparse(page_url).netloc and POST_DETAIL_RE.search(parsed.path):
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
            if clean not in seen:
                seen.add(clean)
                urls.append(clean)
    return urls


def extract_pagination_urls(posts_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    posts_parsed = urlparse(posts_url)
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        abs_url = urljoin(posts_url, str(anchor["href"]).strip())
        parsed = urlparse(abs_url)
        if parsed.netloc != posts_parsed.netloc or parsed.path != posts_parsed.path:
            continue
        rel = anchor.get("rel") or []
        if "page=" in parsed.query or "next" in rel:
            if abs_url not in urls:
                urls.append(abs_url)
    return urls


def parse_post_detail(
    post_url: str,
    html: str,
    allowed_extensions: tuple[str, ...],
) -> PostContent:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    content_col = main.select_one(".col-lg-8") or main

    heading = content_col.find(["h5", "h4", "h3", "h2", "h1"])
    title = " ".join(heading.get_text(" ", strip=True).split()) if heading else "Class Post"

    base_host = urlparse(post_url).netloc
    attachments: list[RemoteAttachment] = []
    seen_attachment_urls: set[str] = set()
    shared_links: list[SharedLink] = []
    seen_link_urls: set[str] = set()

    for anchor in content_col.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        abs_url = urljoin(post_url, href)
        parsed = urlparse(abs_url)
        path_lower = unquote(parsed.path).lower()

        is_download = bool(
            DOWNLOAD_ROUTE_RE.search(parsed.path)
            or anchor.has_attr("download")
            or any(path_lower.endswith(ext) for ext in allowed_extensions)
        )
        if is_download:
            if abs_url not in seen_attachment_urls:
                seen_attachment_urls.add(abs_url)
                attachments.append(
                    RemoteAttachment(
                        url=abs_url,
                        filename=_guess_filename(anchor, abs_url),
                        post_title=title,
                    )
                )
            continue

        if parsed.netloc == base_host:
            continue

        if abs_url not in seen_link_urls:
            seen_link_urls.add(abs_url)
            label = _extract_link_label(anchor, title)
            shared_links.append(
                SharedLink(
                    url=abs_url,
                    label=label,
                    post_url=post_url,
                    post_title=title,
                    drive_id=extract_google_drive_id(abs_url),
                )
            )

    raw_text = content_col.get_text("\n", strip=True)
    for match in URL_IN_TEXT_RE.findall(raw_text):
        cleaned_url = match.rstrip(".,;:!?")
        parsed = urlparse(cleaned_url)
        if parsed.netloc == base_host:
            continue
        path_lower = unquote(parsed.path).lower()
        if any(path_lower.endswith(ext) for ext in allowed_extensions):
            if cleaned_url not in seen_attachment_urls:
                seen_attachment_urls.add(cleaned_url)
                attachments.append(
                    RemoteAttachment(
                        url=cleaned_url,
                        filename=sanitize_filename(unquote(PurePosixPath(parsed.path).name)),
                        post_title=title,
                    )
                )
            continue
        if cleaned_url not in seen_link_urls:
            seen_link_urls.add(cleaned_url)
            shared_links.append(
                SharedLink(
                    url=cleaned_url,
                    label=title,
                    post_url=post_url,
                    post_title=title,
                    drive_id=extract_google_drive_id(cleaned_url),
                )
            )

    return PostContent(
        post_url=post_url,
        title=title,
        attachments=tuple(attachments),
        shared_links=tuple(shared_links),
    )


def build_links_html(post: PostContent, links: list[SharedLink]) -> bytes:
    safe_title = html_lib.escape(post.title)
    safe_post_url = html_lib.escape(post.post_url)
    items = []
    for link in links:
        safe_label = html_lib.escape(link.label or link.url)
        safe_url = html_lib.escape(link.url)
        items.append(f'<li><strong>{safe_label}</strong><br><a href="{safe_url}">{safe_url}</a></li>')
    body = "\n".join(items)
    doc = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        f"<meta charset=\"utf-8\">\n<title>{safe_title}</title>\n"
        "<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.6}"
        "li{margin-bottom:1rem}a{word-break:break-all}</style>\n</head>\n<body>\n"
        f"<h2>{safe_title}</h2>\n"
        f"<p>Source post: <a href=\"{safe_post_url}\">{safe_post_url}</a></p>\n"
        f"<ul>\n{body}\n</ul>\n</body>\n</html>\n"
    )
    return doc.encode("utf-8")


def _guess_filename(anchor: Tag, abs_url: str) -> str:
    if anchor.get("download"):
        dl = str(anchor["download"]).strip()
        if dl:
            return sanitize_filename(dl)
    path_name = unquote(PurePosixPath(urlparse(abs_url).path).name)
    if path_name and "." in path_name:
        return sanitize_filename(path_name)
    text = " ".join(anchor.get_text(" ", strip=True).split())
    if text and text.lower() != "download":
        return sanitize_filename(text)
    return "attachment"


def _extract_link_label(anchor: Tag, fallback: str) -> str:
    text = " ".join(anchor.get_text(" ", strip=True).split())
    if text and not text.startswith(("http://", "https://")):
        return text[:100]
    prev = anchor.find_previous(string=True)
    if prev:
        prev_text = " ".join(str(prev).split()).strip(":- ")
        if prev_text and not prev_text.startswith(("http://", "https://")) and len(prev_text) <= 80:
            return prev_text
    return fallback
