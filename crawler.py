"""Minimal single-site URL inventory crawler."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


USER_AGENT = "LegacySiteInventoryBot/1.0"
ROBOTS_USER_AGENT = "LegacySiteInventoryBot"
REQUEST_DELAY = 0.5
REQUEST_TIMEOUT = 10
MAX_PAGES = 3000
MAX_DEPTH = 10
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 5 * 1024 * 1024

CSV_FIELDS = [
    "url",
    "status_code",
    "final_url",
    "title",
    "source_url",
    "crawl_depth",
    "content_type",
    "error",
]

TRACKING_QUERY_NAMES = {"gclid", "fbclid", "msclkid"}
TRACKING_QUERY_PREFIXES = ("utm_",)
ALLOWED_SCHEMES = {"http", "https"}


@dataclass
class CrawlItem:
    url: str
    source_url: str
    crawl_depth: int


@dataclass
class CrawlResult:
    url: str
    status_code: int | None = None
    final_url: str = ""
    title: str = ""
    source_url: str = ""
    crawl_depth: int = 0
    content_type: str = ""
    error: str = ""


@dataclass
class CrawlSummary:
    completion_reason: str
    discovered_urls: int
    requested_urls: int
    successful_responses: int
    redirects: int
    request_failures: int
    robots_disallowed: int
    depth_limited: int
    page_limited: int
    csv_path: str


def normalize_url(href: str, base_url: str | None = None) -> str | None:
    """Resolve and normalize an HTTP(S) URL for deduplication."""
    if not isinstance(href, str):
        return None

    candidate = href.strip()
    if not candidate or candidate.startswith("#"):
        return None

    resolved = urljoin(base_url or "", candidate)
    parts = urlsplit(resolved)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None

    hostname = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None

    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    is_default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = host_for_netloc
    if port is not None and not is_default_port:
        netloc = f"{host_for_netloc}:{port}"

    filtered_query = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered_name = name.lower()
        if lowered_name in TRACKING_QUERY_NAMES:
            continue
        if any(lowered_name.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        filtered_query.append((name, value))

    query = urlencode(sorted(filtered_query), doseq=True)
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def allowed_hosts_for(hostname: str) -> set[str]:
    """Return a hostname and only its add/remove-www alias."""
    normalized = hostname.strip().lower().rstrip(".")
    if normalized.startswith("www."):
        bare = normalized[4:]
        return {normalized, bare} if bare else {normalized}
    return {normalized, f"www.{normalized}"}


def is_allowed_host(url: str, allowed_hosts: set[str]) -> bool:
    """Return whether a URL hostname is in the crawler's host allow-list."""
    try:
        hostname = urlsplit(url).hostname
    except ValueError:
        return False
    return hostname is not None and hostname.lower().rstrip(".") in allowed_hosts


def origin_for(url: str) -> str:
    """Return the normalized scheme and authority for an HTTP(S) URL."""
    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError(f"Invalid HTTP(S) URL: {url!r}")
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
