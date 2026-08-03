from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from urllib.parse import urlsplit

import requests

from crawler import (
    RateLimiter,
    RobotsUnavailableError,
    USER_AGENT,
    allowed_hosts_for,
    is_allowed_host,
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


if __name__ == "__main__":
    unittest.main()
