from collections import Counter
from contextlib import redirect_stdout
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit

import requests

import crawler as crawler_module

from crawler import (
    CSV_FIELDS,
    CanonicalAudit,
    CrawlResult,
    DiscoveredReference,
    HtmlDocument,
    RateLimiter,
    RobotsUnavailableError,
    USER_AGENT,
    apply_indexability,
    audit_canonical,
    allowed_hosts_for,
    crawl,
    classify_resource,
    extract_html,
    is_allowed_host,
    has_noindex,
    main,
    normalize_url,
    origin_for,
    parse_srcset,
    request_once,
    robots_allowed,
)


EXPECTED_CSV_FIELDS = [
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

EXPECTED_PAGE_CSV_FIELDS = [
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
    "indexable",
    "indexability_reason",
    "error",
]

EXPECTED_RESOURCE_CSV_FIELDS = [
    "url",
    "status_code",
    "final_url",
    "resource_type",
    "content_type",
    "source_url",
    "source_tag",
    "source_attribute",
    "link_rel",
    "discovery_count",
    "crawl_depth",
    "indexable",
    "indexability_reason",
    "error",
]


class _PolicyTestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.hits[path] += 1
        self.server.last_user_agent = self.headers.get("User-Agent", "")

        if path == "/robots.txt":
            status = self.server.robots_status
            body = self.server.robots_body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        elif path == "/redirect":
            body = b""
            self.send_response(302)
            self.send_header("Location", "/target")
        else:
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")

        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class UrlNormalizationTests(unittest.TestCase):
    def test_resolves_relative_url_and_removes_fragment_and_tracking(self):
        actual = normalize_url(
            "../product/?utm_source=newsletter&id=2&fbclid=x#details",
            "http://Example.com/catalog/page.html",
        )

        self.assertEqual(actual, "http://example.com/product/?id=2")

    def test_sorts_query_and_keeps_http_https_distinct(self):
        self.assertEqual(
            normalize_url("http://example.com/item?b=2&a=1"),
            "http://example.com/item?a=1&b=2",
        )
        self.assertNotEqual(
            normalize_url("http://example.com/"),
            normalize_url("https://example.com/"),
        )

    def test_sorts_query_names_but_preserves_repeated_value_order(self):
        self.assertEqual(
            normalize_url("https://example.com/?tag=b&x=1&tag=a"),
            "https://example.com/?tag=b&tag=a&x=1",
        )

    def test_csv_fields_match_indexability_schema(self):
        self.assertEqual(CSV_FIELDS, EXPECTED_CSV_FIELDS)

    def test_rejects_unsupported_or_invalid_links(self):
        for href in (
            "",
            "#only",
            "mailto:a@example.com",
            "javascript:void(0)",
            "data:text/plain,x",
        ):
            with self.subTest(href=href):
                self.assertIsNone(normalize_url(href, "http://example.com/"))

    def test_normalizes_default_ports_but_preserves_non_default_port(self):
        self.assertEqual(normalize_url("http://EXAMPLE.com:80"), "http://example.com/")
        self.assertEqual(normalize_url("https://EXAMPLE.com:443"), "https://example.com/")
        self.assertEqual(
            normalize_url("http://EXAMPLE.com:8080/path"),
            "http://example.com:8080/path",
        )

    def test_preserves_path_case_and_trailing_slash(self):
        self.assertEqual(normalize_url("http://example.com/Product/"), "http://example.com/Product/")
        self.assertNotEqual(
            normalize_url("http://example.com/Product"),
            normalize_url("http://example.com/Product/"),
        )

    def test_allows_only_bare_and_www_host_aliases(self):
        allowed = allowed_hosts_for("www.example.com")

        self.assertEqual(allowed, {"example.com", "www.example.com"})
        self.assertTrue(is_allowed_host("http://example.com/a", allowed))
        self.assertTrue(is_allowed_host("https://www.example.com/a", allowed))
        self.assertFalse(is_allowed_host("http://shop.example.com/a", allowed))

    def test_builds_origin_without_path_query_or_fragment(self):
        self.assertEqual(
            origin_for("http://Example.com:8080/a?x=1#part"),
            "http://example.com:8080",
        )


class HtmlExtractionTests(unittest.TestCase):
    @staticmethod
    def response_for(html):
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.encoding = "utf-8"
        response._content = html.encode("utf-8")
        response._content_consumed = True
        return response

    def test_srcset_returns_each_url_without_its_descriptor(self):
        self.assertEqual(
            parse_srcset("a.webp 1x, b.webp 2x, /wide.webp 1200w"),
            ["a.webp", "b.webp", "/wide.webp"],
        )

    def test_extracts_metadata_and_supported_resource_references(self):
        response = self.response_for(
            """
            <html><head>
              <title> Product   Page </title>
              <meta name="robots" content="index, follow">
              <meta name="ROBOTS" content="max-image-preview:large">
              <meta name="googlebot" content="noindex">
              <link rel="canonical" href="/page/">
              <link rel="canonical" href="/other/">
              <link rel="stylesheet" href="/style.css">
              <link rel="alternate" href="/feed.xml">
              <link rel="preconnect" href="https://cdn.example">
              <link rel="preload" href="/font.woff2" as="font">
            </head><body>
              <a href="/next">Next</a>
              <img src="/image.webp"
                   srcset="/image.webp 1x, /image-2.webp 2x">
              <source src="/fallback.webp"
                      srcset="/small.webp 400w, /large.webp 1200w">
              <script src="/app.js"></script>
            </body></html>
            """
        )

        document, error = extract_html(response)

        self.assertIsNone(error)
        self.assertIsInstance(document, HtmlDocument)
        self.assertEqual(document.title, "Product Page")
        self.assertEqual(
            document.meta_robots,
            "index, follow; max-image-preview:large",
        )
        self.assertEqual(document.canonical_values, ["/page/", "/other/"])
        self.assertEqual(
            document.references,
            [
                DiscoveredReference("/style.css", "link", "href", "stylesheet"),
                DiscoveredReference(
                    "/font.woff2", "link", "href", "preload", "font"
                ),
                DiscoveredReference("/next", "a", "href"),
                DiscoveredReference("/image.webp", "img", "src", resource_hint="image"),
                DiscoveredReference(
                    "/image.webp", "img", "srcset", resource_hint="image"
                ),
                DiscoveredReference(
                    "/image-2.webp", "img", "srcset", resource_hint="image"
                ),
                DiscoveredReference(
                    "/fallback.webp", "source", "src", resource_hint="image"
                ),
                DiscoveredReference(
                    "/small.webp", "source", "srcset", resource_hint="image"
                ),
                DiscoveredReference(
                    "/large.webp", "source", "srcset", resource_hint="image"
                ),
                DiscoveredReference(
                    "/app.js", "script", "src", resource_hint="javascript"
                ),
            ],
        )


class ReportWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    @staticmethod
    def read_report(path):
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_writes_non_overlapping_page_and_resource_reports(self):
        results = {
            "/page": CrawlResult(
                "/page", status_code=200, resource_type="html", title="Page"
            ),
            "/image": CrawlResult(
                "/image", status_code=200, resource_type="image"
            ),
            "/missing": CrawlResult(
                "/missing", status_code=404, resource_type="html"
            ),
        }

        paths = crawler_module.write_reports(results, self.temp_dir.name)
        page_rows = self.read_report(paths.pages)
        resource_rows = self.read_report(paths.resources)

        self.assertEqual(paths.pages.name, "pages.csv")
        self.assertEqual(paths.resources.name, "resources.csv")
        self.assertEqual([row["url"] for row in page_rows], ["/page", "/missing"])
        self.assertEqual([row["url"] for row in resource_rows], ["/image"])
        self.assertEqual(list(page_rows[0]), EXPECTED_PAGE_CSV_FIELDS)
        self.assertEqual(list(resource_rows[0]), EXPECTED_RESOURCE_CSV_FIELDS)
        self.assertTrue(
            {row["url"] for row in page_rows}.isdisjoint(
                row["url"] for row in resource_rows
            )
        )

    def test_empty_results_still_write_both_headers(self):
        paths = crawler_module.write_reports({}, self.temp_dir.name)

        with paths.pages.open(encoding="utf-8-sig", newline="") as handle:
            page_reader = csv.DictReader(handle)
            self.assertEqual(page_reader.fieldnames, EXPECTED_PAGE_CSV_FIELDS)
            self.assertEqual(list(page_reader), [])
        with paths.resources.open(encoding="utf-8-sig", newline="") as handle:
            resource_reader = csv.DictReader(handle)
            self.assertEqual(resource_reader.fieldnames, EXPECTED_RESOURCE_CSV_FIELDS)
            self.assertEqual(list(resource_reader), [])


class SummaryTests(unittest.TestCase):
    def test_counts_pages_and_resources_without_mixing_page_count(self):
        results = {
            "/ok": CrawlResult(
                "/ok", resource_type="html", indexable="YES", error="html_too_large"
            ),
            "/redirect": CrawlResult(
                "/redirect", status_code=301, resource_type="html", indexable="NO"
            ),
            "/image": CrawlResult("/image", resource_type="image"),
            "/css": CrawlResult("/css", resource_type="css"),
            "/js": CrawlResult("/js", resource_type="javascript"),
            "/pdf": CrawlResult("/pdf", resource_type="pdf"),
            "/font": CrawlResult("/font", resource_type="font"),
            "/video": CrawlResult("/video", resource_type="video"),
            "/audio": CrawlResult("/audio", resource_type="audio"),
            "/json": CrawlResult(
                "/json", resource_type="json", error="timeout"
            ),
        }

        metrics = crawler_module._audit_metrics(results)

        self.assertEqual(metrics.pages_discovered, 2)
        self.assertEqual(metrics.indexable_pages, 1)
        self.assertEqual(metrics.non_indexable_pages, 1)
        self.assertEqual(metrics.page_errors, 1)
        self.assertEqual(
            metrics.resource_counts,
            {
                "image": 1,
                "css": 1,
                "javascript": 1,
                "pdf": 1,
                "font": 1,
                "video": 1,
                "audio": 1,
                "other": 1,
            },
        )
        self.assertEqual(metrics.resource_errors, 1)
        self.assertEqual(metrics.total_unique_urls, 10)


class ResourceAuditTests(unittest.TestCase):
    def test_classifies_response_mime_types_and_fallbacks(self):
        cases = [
            (("text/html; charset=utf-8", "/x", None), "html"),
            (("application/xhtml+xml", "/x", None), "html"),
            (("application/pdf", "/x", None), "pdf"),
            (("image/webp", "/x", None), "image"),
            (("text/css", "/x", None), "css"),
            (("application/javascript", "/x", None), "javascript"),
            (("font/woff2", "/x", None), "font"),
            (("application/ld+json", "/x", None), "json"),
            (("video/mp4", "/x", None), "video"),
            (("audio/mpeg", "/x", None), "audio"),
            (("application/octet-stream", "/x", None), "other"),
            (("", "/document.pdf", None), "pdf"),
            (("", "/font.woff2", None), "font"),
            (("", "/movie.mp4", None), "video"),
            (("", "/sound.mp3", None), "audio"),
            (("", "/unknown", None), "unknown"),
            (
                (
                    "",
                    "/download",
                    DiscoveredReference(
                        "/download", "link", "href", "preload", "font"
                    ),
                ),
                "font",
            ),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(classify_resource(*arguments), expected)

    def test_noindex_matches_generic_directive_tokens_only(self):
        self.assertTrue(has_noindex("index, NOINDEX, follow"))
        self.assertTrue(has_noindex("index; noindex"))
        self.assertFalse(has_noindex("not-noindex,follow"))
        self.assertFalse(has_noindex("googlebot: noindex"))

    def test_audits_missing_self_warning_other_and_conflicting_canonical(self):
        missing = audit_canonical([], "https://example.com/products/")
        self.assertEqual(missing, CanonicalAudit("", "N/A", "", ""))

        self_reference = audit_canonical(
            ["/products/?utm_source=test#details"],
            "https://example.com/products/",
        )
        self.assertEqual(
            self_reference,
            CanonicalAudit(
                "https://example.com/products/?utm_source=test",
                "YES",
                "Tracking parameters present; Fragment present",
                "",
            ),
        )

        other = audit_canonical(
            ["https://example.com/other/"],
            "https://example.com/products/",
        )
        self.assertEqual(other.self_reference, "NO")
        self.assertEqual(other.blocker, "Canonicalized to another URL")

        duplicate = audit_canonical(
            ["/products/", "https://EXAMPLE.com:443/products/"],
            "https://example.com/products/",
        )
        self.assertEqual(duplicate.self_reference, "YES")
        self.assertEqual(duplicate.warning, "Multiple canonical tags")
        self.assertEqual(duplicate.display_url, "https://example.com/products/")

        conflicting = audit_canonical(
            ["/products/", "/other/"],
            "https://example.com/products/",
        )
        self.assertEqual(conflicting.self_reference, "NO")
        self.assertEqual(conflicting.blocker, "Conflicting canonical tags")
        self.assertEqual(
            conflicting.display_url,
            "https://example.com/products/; https://example.com/other/",
        )

    def test_canonical_comparison_uses_final_url_and_preserves_repeated_values(self):
        self.assertEqual(
            audit_canonical(
                ["?tag=b&tag=a"], "https://example.com/page?tag=b&tag=a"
            ).self_reference,
            "YES",
        )
        self.assertEqual(
            audit_canonical(
                ["?tag=a&tag=b"], "https://example.com/page?tag=b&tag=a"
            ).self_reference,
            "NO",
        )
        self.assertEqual(
            audit_canonical(
                ["https://example.com/final/"], "https://example.com/final/"
            ).self_reference,
            "YES",
        )

        for canonical in [
            "http://example.com/final/",
            "https://www.example.com/final/",
            "https://example.com/final",
        ]:
            with self.subTest(canonical=canonical):
                self.assertEqual(
                    audit_canonical(
                        [canonical], "https://example.com/final/"
                    ).self_reference,
                    "NO",
                )

    def test_invalid_canonical_is_a_blocker_instead_of_an_exception(self):
        audit = audit_canonical(
            ["http://[invalid", "https://example.com/page"],
            "https://example.com/page",
        )

        self.assertEqual(audit.self_reference, "NO")
        self.assertEqual(audit.blocker, "Invalid canonical URL")

    def test_applies_indexability_and_combines_blockers_in_fixed_order(self):
        result = CrawlResult(
            url="https://example.com/page",
            final_url="https://example.com/page",
            status_code=404,
            resource_type="html",
            meta_robots="noindex,follow",
            x_robots_tag="noindex",
            canonical_self_reference="NO",
        )

        apply_indexability(result, "Canonicalized to another URL")

        self.assertEqual(result.indexable, "NO")
        self.assertEqual(
            result.indexability_reason,
            "HTTP status 404; X-Robots-Tag noindex; Meta robots noindex; "
            "Canonicalized to another URL",
        )

    def test_applies_yes_no_and_na_resource_rules(self):
        cases = [
            (CrawlResult("/html", status_code=200, resource_type="html"), "YES", "Canonical missing"),
            (CrawlResult("/pdf", status_code=200, resource_type="pdf"), "YES", "OK"),
            (
                CrawlResult("/image", status_code=200, resource_type="image"),
                "YES",
                "Image resource allowed",
            ),
            (
                CrawlResult("/css", status_code=200, resource_type="css"),
                "N/A",
                "Resource type not evaluated",
            ),
            (
                CrawlResult(
                    "/external",
                    resource_type="javascript",
                    error="external_resource_not_requested",
                ),
                "N/A",
                "External resource not evaluated",
            ),
            (
                CrawlResult(
                    "/blocked",
                    resource_type="image",
                    error="robots_disallowed",
                ),
                "NO",
                "Blocked by robots.txt",
            ),
        ]
        for result, expected_indexable, expected_reason in cases:
            with self.subTest(url=result.url):
                apply_indexability(result)
                self.assertEqual(result.indexable, expected_indexable)
                self.assertEqual(result.indexability_reason, expected_reason)


class RequestAndRobotsTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _PolicyTestHandler)
        self.server.hits = Counter()
        self.server.last_user_agent = ""
        self.server.robots_status = 200
        self.server.robots_body = (
            "User-agent: LegacySiteInventoryBot\n"
            "Disallow: /private\n"
            "Allow: /private/public\n"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.session = requests.Session()

    def tearDown(self):
        self.session.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def test_rate_limiter_waits_only_for_remaining_interval(self):
        times = iter((10.0, 10.2, 10.5))
        sleeps = []
        limiter = RateLimiter(0.5, clock=lambda: next(times), sleeper=sleeps.append)

        limiter.wait()
        limiter.wait()

        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.3)

    def test_request_once_disables_redirects_and_sends_user_agent(self):
        response = request_once(self.session, self.url("/redirect"), RateLimiter(0), 1)
        self.addCleanup(response.close)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.history, [])
        self.assertEqual(self.server.hits["/target"], 0)
        self.assertEqual(self.server.last_user_agent, USER_AGENT)

    def test_robots_disallow_blocks_without_requesting_page(self):
        allowed = robots_allowed(
            self.url("/private/page"),
            self.session,
            RateLimiter(0),
            {},
            {"127.0.0.1"},
            1,
        )

        self.assertFalse(allowed)
        self.assertEqual(self.server.hits["/private/page"], 0)

    def test_robots_allow_uses_most_specific_rule(self):
        allowed = robots_allowed(
            self.url("/private/public/page"),
            self.session,
            RateLimiter(0),
            {},
            {"127.0.0.1"},
            1,
        )

        self.assertTrue(allowed)

    def test_missing_robots_allows_crawling(self):
        self.server.robots_status = 404

        allowed = robots_allowed(
            self.url("/page"),
            self.session,
            RateLimiter(0),
            {},
            {"127.0.0.1"},
            1,
        )

        self.assertTrue(allowed)

    def test_temporary_robots_failure_is_fatal(self):
        self.server.robots_status = 503

        with self.assertRaises(RobotsUnavailableError):
            robots_allowed(
                self.url("/page"),
                self.session,
                RateLimiter(0),
                {},
                {"127.0.0.1"},
                1,
            )

    def test_robots_policy_is_cached_per_origin(self):
        cache = {}

        first = robots_allowed(
            self.url("/private"), self.session, RateLimiter(0), cache, {"127.0.0.1"}, 1
        )
        second = robots_allowed(
            self.url("/public"), self.session, RateLimiter(0), cache, {"127.0.0.1"}, 1
        )

        self.assertFalse(first)
        self.assertTrue(second)
        self.assertEqual(self.server.hits["/robots.txt"], 1)


class _CrawlerTestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.hits[path] += 1

        if path == "/disconnect":
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return

        if path == "/robots.txt":
            status = self.server.robots_status
            headers = {"Content-Type": "text/plain; charset=utf-8"}
            body = self.server.robots_body
        else:
            status, headers, body = self.server.routes.get(
                path,
                (404, {"Content-Type": "text/html; charset=utf-8"}, "not found"),
            )

        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, format, *args):
        return


class CrawlerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_csv = f"{self.temp_dir.name}\\inventory.csv"

        self.server = self.start_server()
        self.addCleanup(self.stop_server, self.server)
        self.secondary_server = self.start_server(robots_status=503)
        self.addCleanup(self.stop_server, self.secondary_server)
        self.configure_routes()

    def start_server(self, *, robots_status=200):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _CrawlerTestHandler)
        server.hits = Counter()
        server.routes = {}
        server.robots_status = robots_status
        server.robots_body = (
            "User-agent: LegacySiteInventoryBot\n"
            "Disallow: /private\n"
        )
        server.thread = threading.Thread(target=server.serve_forever, daemon=True)
        server.thread.start()
        return server

    @staticmethod
    def stop_server(server):
        server.shutdown()
        server.server_close()
        server.thread.join(timeout=2)

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def secondary_url(self, path):
        return f"http://127.0.0.1:{self.secondary_server.server_port}{path}"

    @staticmethod
    def html(body):
        return 200, {"Content-Type": "text/html; charset=utf-8"}, body

    def configure_routes(self):
        self.server.routes.update(
            {
                "/": self.html(
                    '<title> Home  Page </title>'
                    '<a href="/about">About</a>'
                    '<a href="/about#team">Duplicate</a>'
                    '<a href="/about?utm_source=test">Tracked duplicate</a>'
                    '<a href="/redirect">Redirect</a>'
                    '<a href="/asset.pdf">PDF</a>'
                    '<a href="/missing">Missing</a>'
                    '<a href="/private">Private</a>'
                    '<a href="http://example.invalid/out">External</a>'
                ),
                "/about": self.html("<title>About</title>"),
                "/redirect": (302, {"Location": "/redirect-target"}, b""),
                "/redirect-target": self.html("<title>Target</title>"),
                "/asset.pdf": (200, {"Content-Type": "application/pdf"}, b"PDF"),
                "/missing": (404, {"Content-Type": "text/html"}, '<a href="/hidden">x</a>'),
                "/private": self.html("private"),
                "/redirect-home": (302, {"Location": "/redirect-target"}, b""),
                "/external-redirect-home": (
                    302,
                    {"Location": "http://example.invalid/out"},
                    b"",
                ),
                "/external-redirect-index": self.html(
                    '<a href="/external-redirect-home">redirect</a>'
                ),
                "/error-home": (
                    404,
                    {"Content-Type": "text/html"},
                    '<a href="/linked-from-404">x</a>',
                ),
                "/failure-home": self.html(
                    '<a href="/disconnect">bad</a><a href="/after-failure">good</a>'
                ),
                "/after-failure": self.html("ok"),
                "/depth/0": self.html('<a href="/depth/1">one</a>'),
                "/depth/1": self.html('<a href="/depth/2">two</a>'),
                "/depth/2": self.html("too deep"),
                "/page-limit-home": self.html(
                    '<a href="/page-limit-one">one</a>'
                    '<a href="/page-limit-two">two</a>'
                ),
                "/page-limit-one": self.html("one"),
                "/page-limit-two": self.html("two"),
                "/secondary-origin-home": self.html(
                    f'<a href="{self.secondary_url("/blocked-by-unreachable-robots")}">bad robots</a>'
                    '<a href="/queued-after-secondary">queued</a>'
                ),
                "/queued-after-secondary": self.html("queued"),
                "/large-html": self.html(
                    "x" * 64 + '<a href="/inside-large-html">hidden</a>'
                ),
                "/first-source-home": self.html(
                    '<a href="/first-source">first</a><a href="/second-source">second</a>'
                ),
                "/first-source": self.html('<a href="/shared">shared</a>'),
                "/second-source": self.html('<a href="/shared">shared</a>'),
                "/shared": self.html("shared"),
                "/interrupt-home": self.html(
                    '<a href="/interrupt-active">active</a>'
                    '<a href="/interrupt-queued">queued</a>'
                ),
                "/interrupt-active": self.html("active"),
                "/interrupt-queued": self.html("queued"),
                "/resource-home": self.html(
                    '<img src="/logo.webp">'
                    '<img src="/logo.webp" srcset="/logo.webp 1x, /responsive.webp 2x">'
                    '<script src="/app.js"></script>'
                    '<link rel="stylesheet" href="/style.css">'
                    '<link rel="preload" as="document" href="/document.pdf">'
                    '<script src="http://cdn.example.invalid/external.js"></script>'
                    '<img src="http://cdn.example.invalid/external.webp">'
                    '<a href="http://example.invalid/external-page">external page</a>'
                ),
                "/logo.webp": (200, {"Content-Type": "image/webp"}, b"logo"),
                "/responsive.webp": (
                    200,
                    {"Content-Type": "image/webp"},
                    b"responsive",
                ),
                "/app.js": (
                    200,
                    {"Content-Type": "application/javascript"},
                    '<a href="/fake-link-inside-js">not HTML</a>',
                ),
                "/style.css": (200, {"Content-Type": "text/css"}, b"body{}"),
                "/document.pdf": (200, {"Content-Type": "application/pdf"}, b"PDF"),
            }
        )

    def read_rows(self):
        with open(self.output_csv, encoding="utf-8-sig", newline="") as handle:
            return {row["url"]: row for row in csv.DictReader(handle)}

    def run_crawl(self, path, **overrides):
        options = {"delay": 0, "timeout": 1, "max_pages": 100, "max_depth": 10}
        options.update(overrides)
        return crawl(self.url(path), self.output_csv, **options)

    def test_bfs_records_assets_once_and_obeys_robots(self):
        summary = self.run_crawl("/")
        rows = self.read_rows()

        self.assertEqual(summary.completion_reason, "queue_exhausted")
        self.assertEqual(self.server.hits["/about"], 1)
        self.assertEqual(self.server.hits["/private"], 0)
        self.assertEqual(rows[self.url("/private")]["error"], "robots_disallowed")
        self.assertEqual(rows[self.url("/asset.pdf")]["content_type"], "application/pdf")
        self.assertEqual(rows[self.url("/redirect")]["status_code"], "302")
        self.assertNotIn("http://example.invalid/out", rows)
        with open(self.output_csv, encoding="utf-8-sig", newline="") as handle:
            all_rows = list(csv.DictReader(handle))
        self.assertEqual(list(all_rows[0]), EXPECTED_CSV_FIELDS)
        self.assertEqual(len(all_rows), len({row["url"] for row in all_rows}))

    def test_internal_redirect_target_keeps_depth(self):
        self.run_crawl("/redirect-home")
        self.assertEqual(self.read_rows()[self.url("/redirect-target")]["crawl_depth"], "0")

    def test_external_redirect_is_recorded_but_target_is_not_added(self):
        self.run_crawl("/external-redirect-index")
        rows = self.read_rows()
        source = rows[self.url("/external-redirect-home")]
        self.assertEqual(source["final_url"], "http://example.invalid/out")
        self.assertEqual(source["error"], "external_redirect")
        self.assertNotIn("http://example.invalid/out", rows)

    def test_error_page_html_does_not_produce_links(self):
        self.run_crawl("/error-home")
        self.assertNotIn(self.url("/linked-from-404"), self.read_rows())

    def test_disconnected_request_is_recorded_and_queue_continues(self):
        summary = self.run_crawl("/failure-home")
        rows = self.read_rows()
        self.assertIn(rows[self.url("/disconnect")]["error"], {"connection_error", "request_error"})
        self.assertEqual(rows[self.url("/after-failure")]["status_code"], "200")
        self.assertEqual(summary.completion_reason, "queue_exhausted")

    def test_depth_limited_discovery_is_exported_without_request(self):
        self.run_crawl("/depth/0", max_depth=1)
        row = self.read_rows()[self.url("/depth/2")]
        self.assertEqual(row["crawl_depth"], "2")
        self.assertEqual(row["error"], "max_depth_exceeded")
        self.assertEqual(self.server.hits["/depth/2"], 0)

    def test_page_limited_queue_is_exported_without_request(self):
        summary = self.run_crawl("/page-limit-home", max_pages=2)
        rows = self.read_rows()
        self.assertEqual(summary.completion_reason, "max_pages_reached")
        self.assertEqual(rows[self.url("/page-limit-two")]["error"], "max_pages_reached")
        self.assertEqual(self.server.hits["/page-limit-two"], 0)

    def test_secondary_origin_robots_failure_stops_and_marks_queue(self):
        summary = self.run_crawl("/secondary-origin-home")
        rows = self.read_rows()
        self.assertEqual(summary.completion_reason, "robots_unreachable")
        self.assertEqual(
            rows[self.secondary_url("/blocked-by-unreachable-robots")]["error"],
            "robots_unreachable",
        )
        self.assertEqual(
            rows[self.url("/queued-after-secondary")]["error"],
            "crawl_stopped_robots_unreachable",
        )

    def test_oversized_html_is_recorded_without_parsing_links(self):
        with mock.patch("crawler.MAX_HTML_BYTES", 32):
            self.run_crawl("/large-html")
        rows = self.read_rows()
        self.assertEqual(rows[self.url("/large-html")]["error"], "html_too_large")
        self.assertNotIn(self.url("/inside-large-html"), rows)

    def test_first_source_url_wins(self):
        self.run_crawl("/first-source-home")
        self.assertEqual(
            self.read_rows()[self.url("/shared")]["source_url"],
            self.url("/first-source"),
        )

    def test_interrupt_marks_active_and_queued_urls(self):
        real_request_once = request_once

        def interrupt_active(session, url, limiter, timeout):
            if url == self.url("/interrupt-active"):
                raise KeyboardInterrupt
            return real_request_once(session, url, limiter, timeout)

        with mock.patch("crawler.request_once", side_effect=interrupt_active):
            summary = self.run_crawl("/interrupt-home")

        rows = self.read_rows()
        self.assertEqual(summary.completion_reason, "interrupted")
        self.assertEqual(rows[self.url("/interrupt-active")]["error"], "interrupted")
        self.assertEqual(rows[self.url("/interrupt-queued")]["error"], "interrupted")

    def test_embedded_resources_are_inventoried_without_non_html_recursion(self):
        self.run_crawl("/resource-home")
        rows = self.read_rows()

        logo = rows[self.url("/logo.webp")]
        self.assertEqual(logo["resource_type"], "image")
        self.assertEqual(logo["discovery_count"], "3")
        self.assertEqual(logo["source_url"], self.url("/resource-home"))
        self.assertEqual(logo["source_tag"], "img")
        self.assertEqual(logo["source_attribute"], "src")
        self.assertEqual(self.server.hits["/logo.webp"], 1)
        self.assertEqual(self.server.hits["/responsive.webp"], 1)
        self.assertEqual(self.server.hits["/app.js"], 1)
        self.assertEqual(self.server.hits["/style.css"], 1)
        self.assertEqual(self.server.hits["/document.pdf"], 1)
        self.assertNotIn(self.url("/fake-link-inside-js"), rows)

        stylesheet = rows[self.url("/style.css")]
        self.assertEqual(stylesheet["source_tag"], "link")
        self.assertEqual(stylesheet["source_attribute"], "href")
        self.assertEqual(stylesheet["link_rel"], "stylesheet")
        self.assertEqual(stylesheet["resource_type"], "css")

        external_script = rows["http://cdn.example.invalid/external.js"]
        self.assertEqual(external_script["discovery_count"], "1")
        self.assertEqual(external_script["indexable"], "N/A")
        self.assertEqual(
            external_script["indexability_reason"],
            "External resource not evaluated",
        )
        self.assertEqual(
            external_script["error"], "external_resource_not_requested"
        )
        self.assertIn("http://cdn.example.invalid/external.webp", rows)
        self.assertNotIn("http://example.invalid/external-page", rows)


class IndexabilityAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_csv = f"{self.temp_dir.name}\\audit.csv"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CrawlerTestHandler)
        self.server.hits = Counter()
        self.server.robots_status = 200
        self.server.robots_body = (
            "User-agent: LegacySiteInventoryBot\nDisallow: /blocked-image.webp\n"
        )
        self.server.routes = self.routes()
        self.server.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.thread.join(timeout=2)

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    @staticmethod
    def html(body, **headers):
        return 200, {"Content-Type": "text/html", **headers}, body

    def routes(self):
        page_links = "".join(
            f'<a href="/{name}">{name}</a>'
            for name in [
                "self",
                "missing-canonical",
                "tracking",
                "other-canonical",
                "duplicate-canonical",
                "conflicting-canonical",
                "meta-noindex",
                "x-noindex",
                "multiple-blockers",
                "document.pdf",
                "document-noindex.pdf",
                "not-found",
            ]
        )
        resources = (
            '<img src="/photo.webp">'
            '<img src="/blocked-image.webp">'
            '<link rel="stylesheet" href="/style.css">'
            '<script src="/app.js"></script>'
            '<link rel="preload" as="font" href="/font.woff2">'
        )
        return {
            "/audit-home": self.html(page_links + resources),
            "/self": self.html('<link rel="canonical" href="/self">'),
            "/missing-canonical": self.html("missing"),
            "/tracking": self.html(
                '<link rel="canonical" href="/tracking?utm_source=test#details">'
            ),
            "/other-canonical": self.html(
                '<link rel="canonical" href="/different">'
            ),
            "/duplicate-canonical": self.html(
                '<link rel="canonical" href="/duplicate-canonical">'
                '<link rel="canonical" href="/duplicate-canonical">'
            ),
            "/conflicting-canonical": self.html(
                '<link rel="canonical" href="/conflicting-canonical">'
                '<link rel="canonical" href="/different">'
            ),
            "/meta-noindex": self.html(
                '<meta name="robots" content="noindex,follow">'
            ),
            "/x-noindex": self.html("x", **{"X-Robots-Tag": "noindex"}),
            "/multiple-blockers": self.html(
                '<meta name="robots" content="noindex">'
                '<link rel="canonical" href="/different">',
                **{"X-Robots-Tag": "noindex"},
            ),
            "/document.pdf": (200, {"Content-Type": "application/pdf"}, b"PDF"),
            "/document-noindex.pdf": (
                200,
                {"Content-Type": "application/pdf", "X-Robots-Tag": "noindex"},
                b"PDF",
            ),
            "/photo.webp": (200, {"Content-Type": "image/webp"}, b"image"),
            "/blocked-image.webp": (
                200,
                {"Content-Type": "image/webp"},
                b"blocked",
            ),
            "/style.css": (200, {"Content-Type": "text/css"}, b"body{}"),
            "/app.js": (200, {"Content-Type": "application/javascript"}, b""),
            "/font.woff2": (200, {"Content-Type": "font/woff2"}, b"font"),
            "/not-found": (404, {"Content-Type": "text/html"}, "missing"),
        }

    def read_rows(self):
        with open(self.output_csv, encoding="utf-8-sig", newline="") as handle:
            return {row["url"]: row for row in csv.DictReader(handle)}

    def test_exports_explainable_indexability_matrix(self):
        crawl(
            self.url("/audit-home"),
            self.output_csv,
            delay=0,
            timeout=1,
            max_pages=100,
            max_depth=10,
        )
        rows = self.read_rows()

        self.assertEqual(rows[self.url("/self")]["indexable"], "YES")
        self.assertEqual(rows[self.url("/self")]["indexability_reason"], "OK")
        self.assertEqual(
            rows[self.url("/missing-canonical")]["indexability_reason"],
            "Canonical missing",
        )
        tracking = rows[self.url("/tracking")]
        self.assertEqual(tracking["canonical_self_reference"], "YES")
        self.assertEqual(
            tracking["canonical_url"], self.url("/tracking?utm_source=test")
        )
        self.assertEqual(
            tracking["canonical_warning"],
            "Tracking parameters present; Fragment present",
        )
        self.assertEqual(rows[self.url("/other-canonical")]["indexable"], "NO")
        self.assertEqual(
            rows[self.url("/other-canonical")]["indexability_reason"],
            "Canonicalized to another URL",
        )
        self.assertEqual(
            rows[self.url("/duplicate-canonical")]["canonical_warning"],
            "Multiple canonical tags",
        )
        self.assertEqual(
            rows[self.url("/conflicting-canonical")]["indexability_reason"],
            "Conflicting canonical tags",
        )
        self.assertEqual(
            rows[self.url("/meta-noindex")]["indexability_reason"],
            "Meta robots noindex",
        )
        self.assertEqual(rows[self.url("/x-noindex")]["x_robots_tag"], "noindex")
        self.assertEqual(
            rows[self.url("/x-noindex")]["indexability_reason"],
            "X-Robots-Tag noindex",
        )
        self.assertEqual(
            rows[self.url("/multiple-blockers")]["indexability_reason"],
            "X-Robots-Tag noindex; Meta robots noindex; "
            "Canonicalized to another URL",
        )
        self.assertEqual(rows[self.url("/document.pdf")]["indexability_reason"], "OK")
        self.assertEqual(
            rows[self.url("/document-noindex.pdf")]["indexability_reason"],
            "X-Robots-Tag noindex",
        )
        self.assertEqual(
            rows[self.url("/photo.webp")]["indexability_reason"],
            "Image resource allowed",
        )
        blocked_image = rows[self.url("/blocked-image.webp")]
        self.assertEqual(blocked_image["resource_type"], "image")
        self.assertEqual(blocked_image["indexable"], "NO")
        self.assertEqual(blocked_image["indexability_reason"], "Blocked by robots.txt")
        self.assertEqual(self.server.hits["/blocked-image.webp"], 0)
        for path in ["/style.css", "/app.js", "/font.woff2"]:
            self.assertEqual(rows[self.url(path)]["indexable"], "N/A")
        self.assertEqual(
            rows[self.url("/not-found")]["indexability_reason"],
            "HTTP status 404",
        )


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_csv = f"{self.temp_dir.name}\\cli-inventory.csv"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CrawlerTestHandler)
        self.server.hits = Counter()
        self.server.robots_status = 404
        self.server.robots_body = ""
        self.server.routes = {
            "/": (200, {"Content-Type": "text/html"}, "<title>CLI</title>"),
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def url(self, path="/"):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def test_main_writes_csv_and_prints_summary(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    self.url(),
                    "--output",
                    self.output_csv,
                    "--delay",
                    "0",
                    "--timeout",
                    "1",
                    "--max-pages",
                    "100",
                    "--max-depth",
                    "10",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("completion_reason=queue_exhausted", stdout.getvalue())
        self.assertIn(f"csv_path={self.output_csv}", stdout.getvalue())

    def test_invalid_scheme_is_an_argparse_error_without_request(self):
        with self.assertRaises(SystemExit) as raised:
            main(["ftp://example.com/"])

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(sum(self.server.hits.values()), 0)

    def test_non_positive_page_limit_is_an_argparse_error_without_request(self):
        with self.assertRaises(SystemExit) as raised:
            main([self.url(), "--max-pages", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(sum(self.server.hits.values()), 0)


if __name__ == "__main__":
    unittest.main()
