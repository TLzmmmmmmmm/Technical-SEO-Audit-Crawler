import unittest

from crawler import allowed_hosts_for, is_allowed_host, normalize_url, origin_for


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


if __name__ == "__main__":
    unittest.main()
