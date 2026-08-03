from collections import Counter
from contextlib import redirect_stdout
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import socket
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit

import requests

from crawler import (
    CSV_FIELDS,
    RateLimiter,
    RobotsUnavailableError,
    USER_AGENT,
    allowed_hosts_for,
    crawl,
    is_allowed_host,
    main,
    normalize_url,
    origin_for,
    request_once,
    robots_allowed,
)


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
        self.assertEqual(list(all_rows[0]), CSV_FIELDS)
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
    crawl,
    main,
