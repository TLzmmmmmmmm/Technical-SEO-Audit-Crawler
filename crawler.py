"""Minimal single-site URL inventory crawler."""

from __future__ import annotations

import argparse
from collections import deque
import csv
from dataclasses import dataclass, fields
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from protego import Protego
import requests


USER_AGENT = "LegacySiteInventoryBot/1.0"
ROBOTS_USER_AGENT = "LegacySiteInventoryBot"
REQUEST_DELAY = 0.5
REQUEST_TIMEOUT = 10
MAX_PAGES = 3000
MAX_DEPTH = 10
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 5 * 1024 * 1024
ROBOTS_MAX_BYTES = 512 * 1024
RESPECT_ROBOTS_TXT = True
FOLLOW_INTERNAL_REDIRECTS = True
FOLLOW_EXTERNAL_REDIRECTS = False
RECORD_FIRST_SOURCE_ONLY = True
PARSE_HTML_ONLY = True

CSV_FIELDS = [
    "url",
    "status_code",
    "final_url",
    "title",
    "canonical_url",
    "canonical_self_reference",
    "canonical_warning",
    "meta_robots",
    "x_robots_tag",
    "source_url",
    "source_tag",
    "source_attribute",
    "link_rel",
    "discovery_count",
    "crawl_depth",
    "content_type",
    "resource_type",
    "indexable",
    "indexability_reason",
    "error",
]

TRACKING_QUERY_NAMES = {"gclid", "fbclid", "msclkid"}
TRACKING_QUERY_PREFIXES = ("utm_",)
ALLOWED_SCHEMES = {"http", "https"}
RESOURCE_LINK_RELS = {
    "stylesheet",
    "icon",
    "apple-touch-icon",
    "mask-icon",
    "manifest",
    "preload",
    "modulepreload",
}
IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
RESOURCE_EXTENSIONS = {
    ".css": "css",
    ".js": "javascript",
    ".mjs": "javascript",
    ".pdf": "pdf",
    ".json": "json",
    ".woff": "font",
    ".woff2": "font",
    ".ttf": "font",
    ".otf": "font",
    ".eot": "font",
    ".mp3": "media",
    ".mp4": "media",
    ".ogg": "media",
    ".webm": "media",
}


@dataclass
class CrawlItem:
    url: str
    source_url: str
    crawl_depth: int


@dataclass(frozen=True)
class DiscoveredReference:
    url: str
    source_tag: str
    source_attribute: str
    link_rel: str = ""
    resource_hint: str = ""


@dataclass
class HtmlDocument:
    title: str
    canonical_values: list[str]
    meta_robots: str
    references: list[DiscoveredReference]


@dataclass(frozen=True)
class CanonicalAudit:
    display_url: str
    self_reference: str
    warning: str
    blocker: str


@dataclass
class CrawlResult:
    url: str
    status_code: int | None = None
    final_url: str = ""
    title: str = ""
    canonical_url: str = ""
    canonical_self_reference: str = ""
    canonical_warning: str = ""
    meta_robots: str = ""
    x_robots_tag: str = ""
    source_url: str = ""
    source_tag: str = ""
    source_attribute: str = ""
    link_rel: str = ""
    discovery_count: int = 1
    crawl_depth: int = 0
    content_type: str = ""
    resource_type: str = ""
    indexable: str = ""
    indexability_reason: str = ""
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


class RobotsUnavailableError(RuntimeError):
    """Raised when robots.txt cannot be fetched safely."""


class RateLimiter:
    """Enforce a minimum interval between request start times."""

    def __init__(
        self,
        delay: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = max(0.0, delay)
        self._clock = clock
        self._sleeper = sleeper
        self._last_request_started: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last_request_started is not None:
            remaining = self.delay - (now - self._last_request_started)
            if remaining > 0:
                self._sleeper(remaining)
                now = self._clock()
        self._last_request_started = now


def request_once(
    session: requests.Session,
    url: str,
    limiter: RateLimiter,
    timeout: float,
) -> requests.Response:
    """Send one rate-limited GET without following redirects."""
    limiter.wait()
    return session.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=(timeout, timeout),
        allow_redirects=False,
        stream=True,
    )


def _read_robots_body(response: requests.Response) -> str:
    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        remaining = ROBOTS_MAX_BYTES - len(body)
        if remaining <= 0:
            break
        body.extend(chunk[:remaining])
        if len(body) >= ROBOTS_MAX_BYTES:
            break
    encoding = response.encoding or "utf-8"
    return bytes(body).decode(encoding, errors="replace")


def robots_allowed(
    url: str,
    session: requests.Session,
    limiter: RateLimiter,
    cache: dict[str, Protego | None],
    redirect_hosts: set[str],
    timeout: float,
) -> bool:
    """Return whether robots.txt permits a URL, caching by origin."""
    origin = origin_for(url)
    if origin in cache:
        parser = cache[origin]
        return parser is None or parser.can_fetch(url, ROBOTS_USER_AGENT)

    robots_url = f"{origin}/robots.txt"
    visited: set[str] = set()

    for redirect_count in range(MAX_REDIRECTS + 1):
        if robots_url in visited:
            raise RobotsUnavailableError("robots.txt redirect loop")
        visited.add(robots_url)

        try:
            response = request_once(session, robots_url, limiter, timeout)
        except requests.RequestException as exc:
            raise RobotsUnavailableError(f"robots.txt request failed: {exc}") from exc

        with response:
            status = response.status_code
            if status == 200:
                parser = Protego.parse(_read_robots_body(response))
                cache[origin] = parser
                return parser.can_fetch(url, ROBOTS_USER_AGENT)

            if 400 <= status <= 499:
                cache[origin] = None
                return True

            if 300 <= status <= 399:
                location = response.headers.get("Location", "")
                target = normalize_url(location, robots_url)
                if target is None:
                    raise RobotsUnavailableError("invalid robots.txt redirect")
                if not is_allowed_host(target, redirect_hosts):
                    raise RobotsUnavailableError("external robots.txt redirect")
                if redirect_count >= MAX_REDIRECTS:
                    raise RobotsUnavailableError("robots.txt redirect limit exceeded")
                robots_url = target
                continue

            raise RobotsUnavailableError(f"robots.txt returned HTTP {status}")

    raise RobotsUnavailableError("robots.txt redirect limit exceeded")


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

    query = _stable_query(filtered_query)
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def _stable_query(pairs: list[tuple[str, str]]) -> str:
    """Sort query names while preserving the order of repeated values."""
    return urlencode(sorted(pairs, key=lambda pair: pair[0]), doseq=True)


def _is_tracking_query_name(name: str) -> bool:
    lowered_name = name.lower()
    return lowered_name in TRACKING_QUERY_NAMES or any(
        lowered_name.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES
    )


def _canonical_normalization(
    value: str, final_url: str
) -> tuple[str, str, list[str]] | None:
    """Return a canonical display URL, comparison key, and warnings."""
    candidate = value.strip()
    if not candidate:
        return None
    try:
        resolved = urljoin(final_url, candidate)
        parts = urlsplit(resolved)
    except ValueError:
        return None
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
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = host_for_netloc
    if port is not None and not default_port:
        netloc = f"{host_for_netloc}:{port}"

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    display_query = _stable_query(query_pairs)
    comparison_query = _stable_query(
        [(name, value) for name, value in query_pairs if not _is_tracking_query_name(name)]
    )
    path = parts.path or "/"
    display_url = urlunsplit((scheme, netloc, path, display_query, ""))
    comparison_key = urlunsplit((scheme, netloc, path, comparison_query, ""))
    warnings = []
    if any(_is_tracking_query_name(name) for name, _ in query_pairs):
        warnings.append("Tracking parameters present")
    if parts.fragment:
        warnings.append("Fragment present")
    return display_url, comparison_key, warnings


def audit_canonical(values: list[str], final_url: str) -> CanonicalAudit:
    """Evaluate canonical tags against the response's final URL."""
    if not values:
        return CanonicalAudit("", "N/A", "", "")

    normalized_values = []
    warnings = []
    for value in values:
        normalized = _canonical_normalization(value, final_url)
        if normalized is None:
            display = "; ".join(dict.fromkeys(item.strip() for item in values))
            return CanonicalAudit(display, "NO", "", "Invalid canonical URL")
        display_url, comparison_key, value_warnings = normalized
        normalized_values.append((display_url, comparison_key))
        for warning in value_warnings:
            if warning not in warnings:
                warnings.append(warning)

    unique_comparison_keys = list(
        dict.fromkeys(comparison_key for _, comparison_key in normalized_values)
    )
    unique_display_urls = list(
        dict.fromkeys(display_url for display_url, _ in normalized_values)
    )
    if len(unique_comparison_keys) > 1:
        return CanonicalAudit(
            "; ".join(unique_display_urls),
            "NO",
            "; ".join(warnings),
            "Conflicting canonical tags",
        )

    if len(values) > 1:
        warnings.append("Multiple canonical tags")

    final_normalized = _canonical_normalization(final_url, final_url)
    if final_normalized is None:
        return CanonicalAudit(
            unique_display_urls[0],
            "NO",
            "; ".join(dict.fromkeys(warnings)),
            "Invalid final URL",
        )
    self_reference = unique_comparison_keys[0] == final_normalized[1]
    return CanonicalAudit(
        unique_display_urls[0],
        "YES" if self_reference else "NO",
        "; ".join(dict.fromkeys(warnings)),
        "" if self_reference else "Canonicalized to another URL",
    )


def has_noindex(value: str) -> bool:
    """Return whether generic robots directives contain an exact noindex token."""
    return any(
        token.strip().lower() == "noindex"
        for token in re.split(r"[;,]", value)
    )


def classify_resource(
    content_type: str,
    url: str,
    reference: DiscoveredReference | None = None,
) -> str:
    """Classify a response using its MIME type, then discovery hints and suffix."""
    mime_type = content_type.split(";", 1)[0].strip().lower()
    if mime_type in {"text/html", "application/xhtml+xml"}:
        return "html"
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type == "text/css":
        return "css"
    if mime_type in {
        "application/javascript",
        "text/javascript",
        "application/ecmascript",
        "text/ecmascript",
    }:
        return "javascript"
    if mime_type.startswith("font/") or mime_type in {
        "application/font-woff",
        "application/vnd.ms-fontobject",
        "application/x-font-ttf",
        "application/x-font-opentype",
    }:
        return "font"
    if mime_type == "application/json" or mime_type.endswith("+json"):
        return "json"
    if mime_type.startswith(("audio/", "video/")):
        return "media"
    if mime_type:
        return "other"

    if reference is not None and reference.resource_hint in {
        "html",
        "pdf",
        "image",
        "css",
        "javascript",
        "font",
        "json",
        "media",
    }:
        return reference.resource_hint
    if reference is not None:
        if reference.source_tag == "a":
            return "html"
        if reference.source_tag in {"img", "source"}:
            return "image"
        if reference.source_tag == "script":
            return "javascript"
        rel_tokens = set(reference.link_rel.split())
        if "stylesheet" in rel_tokens:
            return "css"
        if rel_tokens.intersection(
            {"icon", "shortcut", "apple-touch-icon", "mask-icon"}
        ):
            return "image"
        if "manifest" in rel_tokens:
            return "json"
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return RESOURCE_EXTENSIONS.get(suffix, "unknown")


def apply_indexability(result: CrawlResult, canonical_blocker: str = "") -> None:
    """Populate indexability fields using the approved resource-specific rules."""
    if result.error == "external_resource_not_requested":
        result.indexable = "N/A"
        result.indexability_reason = "External resource not evaluated"
        return
    if result.resource_type not in {"html", "pdf", "image"}:
        result.indexable = "N/A"
        result.indexability_reason = "Resource type not evaluated"
        return
    if result.error == "robots_disallowed":
        result.indexable = "NO"
        result.indexability_reason = "Blocked by robots.txt"
        return

    blockers = []
    if result.status_code is None:
        blockers.append("HTTP status unavailable")
    elif result.status_code != 200:
        blockers.append(f"HTTP status {result.status_code}")
    if has_noindex(result.x_robots_tag):
        blockers.append("X-Robots-Tag noindex")
    if result.resource_type == "html":
        if has_noindex(result.meta_robots):
            blockers.append("Meta robots noindex")
        if canonical_blocker:
            blockers.append(canonical_blocker)

    if blockers:
        result.indexable = "NO"
        result.indexability_reason = "; ".join(blockers)
        return

    result.indexable = "YES"
    if result.resource_type == "image":
        result.indexability_reason = "Image resource allowed"
    elif result.resource_type == "html" and result.canonical_self_reference in {"", "N/A"}:
        result.indexability_reason = "Canonical missing"
    else:
        result.indexability_reason = "OK"


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


def _response_content_type(response: requests.Response) -> str:
    return response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()


def parse_srcset(value: str) -> list[str]:
    """Return URL candidates from a srcset attribute."""
    candidates = []
    for item in value.split(","):
        parts = item.strip().split()
        if parts:
            candidates.append(parts[0])
    return candidates


def _source_resource_hint(url: str, content_type: str) -> str:
    lowered_type = content_type.lower()
    if lowered_type.startswith("image/"):
        return "image"
    if lowered_type.startswith(("audio/", "video/")):
        return "media"
    if Path(urlsplit(url).path).suffix.lower() in IMAGE_EXTENSIONS:
        return "image"
    return ""


def extract_html(
    response: requests.Response,
) -> tuple[HtmlDocument | None, str | None]:
    """Read and parse one bounded HTML response."""
    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > MAX_HTML_BYTES:
            return None, "html_too_large"

    soup = BeautifulSoup(bytes(body), "html.parser")
    title = ""
    if soup.title is not None:
        title = " ".join(soup.title.get_text(" ", strip=True).split())

    meta_robots_values = []
    for tag in soup.find_all("meta"):
        name = tag.get("name", "")
        if isinstance(name, str) and name.lower() == "robots" and tag.get("content"):
            meta_robots_values.append(str(tag["content"]).strip())

    canonical_values = []
    references = []
    for tag in soup.find_all(["a", "img", "script", "source", "link"]):
        tag_name = tag.name.lower()
        if tag_name == "link":
            rel_tokens = [str(value).lower() for value in tag.get("rel", [])]
            if "canonical" in rel_tokens and tag.get("href"):
                canonical_values.append(str(tag["href"]))
                continue
            if not RESOURCE_LINK_RELS.intersection(rel_tokens) or not tag.get("href"):
                continue
            references.append(
                DiscoveredReference(
                    str(tag["href"]),
                    "link",
                    "href",
                    " ".join(rel_tokens),
                    str(tag.get("as", "")).lower(),
                )
            )
            continue

        if tag_name == "a" and tag.get("href"):
            references.append(DiscoveredReference(str(tag["href"]), "a", "href"))
            continue

        if tag_name == "script" and tag.get("src"):
            references.append(
                DiscoveredReference(
                    str(tag["src"]), "script", "src", resource_hint="javascript"
                )
            )
            continue

        if tag_name in {"img", "source"}:
            hint = "image" if tag_name == "img" else ""
            if tag.get("src"):
                src = str(tag["src"])
                references.append(
                    DiscoveredReference(
                        src,
                        tag_name,
                        "src",
                        resource_hint=hint
                        or _source_resource_hint(src, str(tag.get("type", ""))),
                    )
                )
            if tag.get("srcset"):
                for srcset_url in parse_srcset(str(tag["srcset"])):
                    references.append(
                        DiscoveredReference(
                            srcset_url,
                            tag_name,
                            "srcset",
                            resource_hint=hint
                            or _source_resource_hint(
                                srcset_url, str(tag.get("type", ""))
                            ),
                        )
                    )

    return (
        HtmlDocument(
            title=title,
            canonical_values=canonical_values,
            meta_robots="; ".join(meta_robots_values),
            references=references,
        ),
        None,
    )


def _request_error(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exc, requests.ConnectionError):
        return "connection_error"
    return "request_error"


def write_csv(results: dict[str, CrawlResult], output_path: str | Path) -> None:
    """Write crawl results in first-discovery order using UTF-8 with BOM."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in results.values():
            writer.writerow({field: getattr(result, field) for field in CSV_FIELDS})


def _summary(
    results: dict[str, CrawlResult],
    completion_reason: str,
    requested_urls: int,
    output_path: str | Path,
) -> CrawlSummary:
    values = list(results.values())
    return CrawlSummary(
        completion_reason=completion_reason,
        discovered_urls=len(values),
        requested_urls=requested_urls,
        successful_responses=sum(
            result.status_code is not None and 200 <= result.status_code < 300
            for result in values
        ),
        redirects=sum(
            result.status_code is not None and 300 <= result.status_code < 400
            for result in values
        ),
        request_failures=sum(
            result.error in {"timeout", "tls_error", "connection_error", "request_error"}
            for result in values
        ),
        robots_disallowed=sum(result.error == "robots_disallowed" for result in values),
        depth_limited=sum(result.error == "max_depth_exceeded" for result in values),
        page_limited=sum(result.error == "max_pages_reached" for result in values),
        csv_path=str(output_path),
    )


def crawl(
    start_url: str,
    output_path: str | Path,
    *,
    delay: float = REQUEST_DELAY,
    timeout: float = REQUEST_TIMEOUT,
    max_pages: int = MAX_PAGES,
    max_depth: int = MAX_DEPTH,
    session: requests.Session | None = None,
) -> CrawlSummary:
    """Crawl one site breadth-first and export every discovered URL."""
    normalized_start = normalize_url(start_url)
    results: dict[str, CrawlResult] = {}
    queue: deque[CrawlItem] = deque()
    seen: set[str] = set()
    robots_cache: dict[str, Protego | None] = {}
    limiter = RateLimiter(delay)
    requested_urls = 0
    completion_reason = ""
    own_session = session is None
    active_session = session or requests.Session()

    def discover(
        url: str,
        source_url: str,
        depth: int,
        *,
        source_tag: str = "",
        source_attribute: str = "",
        link_rel: str = "",
        resource_hint: str = "",
        enqueue: bool = True,
    ) -> bool:
        if url in seen:
            results[url].discovery_count += 1
            return False
        seen.add(url)
        reference = DiscoveredReference(
            url,
            source_tag,
            source_attribute,
            link_rel,
            resource_hint,
        )
        result = CrawlResult(
            url=url,
            source_url=source_url,
            source_tag=source_tag,
            source_attribute=source_attribute,
            link_rel=link_rel,
            crawl_depth=depth,
            resource_type=classify_resource("", url, reference),
        )
        results[url] = result
        if depth > max_depth:
            result.error = "max_depth_exceeded"
        elif enqueue:
            queue.append(CrawlItem(url, source_url, depth))
        return True

    def discover_references(
        references: list[DiscoveredReference],
        page_url: str,
        depth: int,
        allowed_hosts: set[str],
    ) -> None:
        for reference in references:
            target = normalize_url(reference.url, page_url)
            if target is None:
                continue
            internal = is_allowed_host(target, allowed_hosts)
            if not internal and reference.source_tag == "a":
                continue
            discover(
                target,
                page_url,
                depth + 1,
                source_tag=reference.source_tag,
                source_attribute=reference.source_attribute,
                link_rel=reference.link_rel,
                resource_hint=reference.resource_hint,
                enqueue=internal,
            )
            if not internal:
                external_result = results[target]
                external_result.error = "external_resource_not_requested"
                apply_indexability(external_result)

    def classify_response(result: CrawlResult) -> None:
        reference = DiscoveredReference(
            result.url,
            result.source_tag,
            result.source_attribute,
            result.link_rel,
            result.resource_type,
        )
        result.resource_type = classify_resource(
            result.content_type,
            result.url,
            reference,
        )

    def store_document(result: CrawlResult, document: HtmlDocument) -> None:
        result.title = document.title
        result.meta_robots = document.meta_robots
        canonical = audit_canonical(document.canonical_values, result.final_url)
        result.canonical_url = canonical.display_url
        result.canonical_self_reference = canonical.self_reference
        result.canonical_warning = canonical.warning
        apply_indexability(result, canonical.blocker)

    def finalize_results() -> None:
        for result in results.values():
            if result.resource_type == "html" and not result.canonical_self_reference:
                result.canonical_self_reference = "N/A"
            if not result.indexable:
                apply_indexability(result)

    def mark_queue(error: str) -> None:
        for item in queue:
            if not results[item.url].error:
                results[item.url].error = error

    if normalized_start is None:
        write_csv(results, output_path)
        if own_session:
            active_session.close()
        return _summary(results, "start_url_failed", requested_urls, output_path)

    discover(normalized_start, "", 0, resource_hint="html", enqueue=False)
    current = CrawlItem(normalized_start, "", 0)
    active_url = current.url
    allowed_hosts: set[str] = set()
    home_references: list[DiscoveredReference] = []
    redirect_count = 0

    try:
        while not completion_reason:
            active_url = current.url
            result = results[current.url]
            if requested_urls >= max_pages:
                result.error = "max_pages_reached"
                completion_reason = "max_pages_reached"
                break

            try:
                if RESPECT_ROBOTS_TXT and not robots_allowed(
                    current.url,
                    active_session,
                    limiter,
                    robots_cache,
                    allowed_hosts_for(urlsplit(current.url).hostname or ""),
                    timeout,
                ):
                    result.error = "robots_disallowed"
                    completion_reason = "start_url_failed"
                    break
            except RobotsUnavailableError:
                result.error = "robots_unreachable"
                completion_reason = "robots_unreachable"
                break

            requested_urls += 1
            try:
                response = request_once(active_session, current.url, limiter, timeout)
            except requests.RequestException as exc:
                result.error = _request_error(exc)
                completion_reason = "start_url_failed"
                break

            with response:
                result.status_code = response.status_code
                result.final_url = current.url
                result.content_type = _response_content_type(response)
                result.x_robots_tag = response.headers.get("X-Robots-Tag", "").strip()
                classify_response(result)

                if 300 <= response.status_code < 400:
                    target = normalize_url(response.headers.get("Location", ""), current.url)
                    if target is None:
                        result.error = "invalid_redirect"
                        completion_reason = "start_url_failed"
                        break
                    result.final_url = target
                    if redirect_count >= MAX_REDIRECTS or target in seen:
                        completion_reason = "start_url_redirect_limit"
                        break
                    discover(
                        target,
                        current.url,
                        0,
                        resource_hint="html",
                        enqueue=False,
                    )
                    current = CrawlItem(target, result.url, 0)
                    redirect_count += 1
                    continue

                allowed_hosts = allowed_hosts_for(urlsplit(current.url).hostname or "")
                if (
                    200 <= response.status_code < 300
                    and result.content_type in {"text/html", "application/xhtml+xml"}
                ):
                    document, html_error = extract_html(response)
                    if html_error:
                        result.error = html_error
                    elif document is not None:
                        store_document(result, document)
                        home_references = document.references
                completion_reason = "queue_exhausted"

        if completion_reason == "queue_exhausted":
            completion_reason = ""
            discover_references(home_references, current.url, 0, allowed_hosts)

        while not completion_reason and queue:
            if requested_urls >= max_pages:
                mark_queue("max_pages_reached")
                completion_reason = "max_pages_reached"
                break

            item = queue.popleft()
            active_url = item.url
            result = results[item.url]
            try:
                if RESPECT_ROBOTS_TXT and not robots_allowed(
                    item.url,
                    active_session,
                    limiter,
                    robots_cache,
                    allowed_hosts,
                    timeout,
                ):
                    result.error = "robots_disallowed"
                    continue
            except RobotsUnavailableError:
                result.error = "robots_unreachable"
                mark_queue("crawl_stopped_robots_unreachable")
                completion_reason = "robots_unreachable"
                break

            requested_urls += 1
            try:
                response = request_once(active_session, item.url, limiter, timeout)
            except requests.RequestException as exc:
                result.error = _request_error(exc)
                continue

            with response:
                result.status_code = response.status_code
                result.final_url = item.url
                result.content_type = _response_content_type(response)
                result.x_robots_tag = response.headers.get("X-Robots-Tag", "").strip()
                classify_response(result)

                if 300 <= response.status_code < 400:
                    target = normalize_url(response.headers.get("Location", ""), item.url)
                    if target is None:
                        result.error = "invalid_redirect"
                    else:
                        result.final_url = target
                        if is_allowed_host(target, allowed_hosts):
                            if FOLLOW_INTERNAL_REDIRECTS:
                                discover(
                                    target,
                                    item.url,
                                    item.crawl_depth,
                                    resource_hint="html",
                                )
                        elif not FOLLOW_EXTERNAL_REDIRECTS:
                            result.error = "external_redirect"
                    continue

                if not 200 <= response.status_code < 300:
                    continue
                if result.content_type not in {"text/html", "application/xhtml+xml"}:
                    continue

                document, html_error = extract_html(response)
                if html_error:
                    result.error = html_error
                elif document is not None:
                    store_document(result, document)
                    discover_references(
                        document.references,
                        item.url,
                        item.crawl_depth,
                        allowed_hosts,
                    )

        if not completion_reason:
            completion_reason = "queue_exhausted"
    except KeyboardInterrupt:
        active_result = results.get(active_url)
        if (
            active_result is not None
            and active_result.status_code is None
            and not active_result.error
        ):
            active_result.error = "interrupted"
        mark_queue("interrupted")
        completion_reason = "interrupted"
    finally:
        finalize_results()
        write_csv(results, output_path)
        if own_session:
            active_session.close()

    return _summary(results, completion_reason, requested_urls, output_path)


def _http_url(value: str) -> str:
    normalized = normalize_url(value)
    if normalized is None:
        raise argparse.ArgumentTypeError("start_url must be an absolute HTTP or HTTPS URL")
    return normalized


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a CSV inventory of one website.")
    parser.add_argument("start_url", type=_http_url, help="HTTP(S) home page to crawl")
    parser.add_argument("--output", default="inventory.csv", help="output CSV path")
    parser.add_argument("--delay", type=_non_negative_float, default=REQUEST_DELAY)
    parser.add_argument("--timeout", type=_positive_float, default=REQUEST_TIMEOUT)
    parser.add_argument("--max-pages", type=_positive_int, default=MAX_PAGES)
    parser.add_argument("--max-depth", type=_non_negative_int, default=MAX_DEPTH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = crawl(
        args.start_url,
        args.output,
        delay=args.delay,
        timeout=args.timeout,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
    )
    for field in fields(CrawlSummary):
        print(f"{field.name}={getattr(summary, field.name)}")

    if summary.completion_reason == "interrupted":
        return 130
    if summary.completion_reason in {
        "robots_unreachable",
        "start_url_failed",
        "start_url_redirect_limit",
    }:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
