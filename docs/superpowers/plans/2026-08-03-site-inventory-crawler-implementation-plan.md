# Site Inventory Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal single-threaded Python crawler that discovers static internal links from a home page, obeys robots.txt, records every discovered URL once, and exports a CSV asset inventory.

**Architecture:** Keep all runtime behavior in one `crawler.py` file with small functions and dataclasses. Use a `deque` for BFS, a dictionary for first-discovery ordered results, Requests with automatic redirects disabled, Beautiful Soup for `2xx HTML` only, and `urllib.robotparser` for robots matching. Tests use `unittest` and a local HTTP server; no crawler framework or database is introduced.

**Tech Stack:** Python 3.12, Requests 2.x, Beautiful Soup 4.x, Python standard-library `unittest`, `http.server`, `csv`, `urllib.parse`, and `urllib.robotparser`.

## Global Constraints

- Runtime files remain `crawler.py`, `requirements.txt`, and `README.md`; tests remain in `tests/test_crawler.py`.
- Keep execution single-threaded with `REQUEST_DELAY = 0.5` seconds between every network request.
- Use `REQUEST_TIMEOUT = 10`, `MAX_PAGES = 3000`, `MAX_DEPTH = 10`, `MAX_REDIRECTS = 5`, and `MAX_HTML_BYTES = 5 * 1024 * 1024`.
- Send `User-Agent: LegacySiteInventoryBot/1.0`; match robots groups using product token `LegacySiteInventoryBot`.
- Never automatically follow HTTP redirects; enqueue internal targets and record but never request external targets.
- Treat the final home-page hostname and only its add/remove-`www.` alias as internal; keep HTTP/HTTPS and bare/`www` URLs distinct for recording and deduplication.
- Parse links only from `2xx` responses whose MIME type is `text/html` or `application/xhtml+xml`.
- Preserve ordinary query parameters, remove `utm_*`, `gclid`, `fbclid`, and `msclkid`, sort the remainder, and remove fragments.
- Export UTF-8 with BOM CSV fields in this exact order: `url,status_code,final_url,title,source_url,crawl_depth,content_type,error`.
- Do not add concurrency, login, browser automation, persistence, sitemap discovery, `<base href>`, canonical merging, forms, JavaScript rendering, a database, or a UI.

## File Map

- Create `crawler.py`: constants, dataclasses, URL helpers, rate limiting, robots handling, BFS, CSV, and CLI.
- Create `requirements.txt`: Requests and Beautiful Soup runtime dependencies only.
- Create `tests/__init__.py`: make test discovery predictable.
- Create `tests/test_crawler.py`: unit and local-server acceptance tests.
- Modify `README.md`: install, usage, output schema, behavior, and limitations.
- Add `docs/superpowers/specs/2026-08-03-site-inventory-crawler-design.md`: approved design baseline.
- Add `docs/superpowers/plans/2026-08-03-site-inventory-crawler-implementation-plan.md`: this plan.

---

### Task 1: Dependencies, Data Models, and URL Rules

**Files:**
- Create: `requirements.txt`
- Create: `crawler.py`
- Create: `tests/__init__.py`
- Create: `tests/test_crawler.py`

**Interfaces:**
- Produces: `CrawlItem`, `CrawlResult`, `CrawlSummary` dataclasses.
- Produces: `normalize_url(href: str, base_url: str | None = None) -> str | None`.
- Produces: `allowed_hosts_for(hostname: str) -> set[str]`.
- Produces: `is_allowed_host(url: str, allowed_hosts: set[str]) -> bool`.
- Produces: `origin_for(url: str) -> str`.

- [ ] **Step 1: Add dependencies and failing URL tests**

Create `requirements.txt`:

```text
requests>=2.31,<3
beautifulsoup4>=4.12,<5
```

Create tests that import the four URL helpers and assert:

```python
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
        for href in ("", "#only", "mailto:a@example.com", "javascript:void(0)", "data:text/plain,x"):
            self.assertIsNone(normalize_url(href, "http://example.com/"))

    def test_allows_only_bare_and_www_host_aliases(self):
        allowed = allowed_hosts_for("www.example.com")
        self.assertTrue(is_allowed_host("http://example.com/a", allowed))
        self.assertTrue(is_allowed_host("https://www.example.com/a", allowed))
        self.assertFalse(is_allowed_host("http://shop.example.com/a", allowed))
```

- [ ] **Step 2: Run tests and verify the import failure**

Run:

```bash
python3 -m unittest tests.test_crawler.UrlNormalizationTests -v
```

Expected: FAIL because `crawler.py` or the imported helpers do not exist.

- [ ] **Step 3: Implement the minimal URL layer and dataclasses**

Use these exact result fields:

```python
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
```

Implement `normalize_url()` with `urljoin`, `urlsplit`, `parse_qsl(keep_blank_values=True)`, filtered/sorted parameters, `urlencode(..., doseq=True)`, and `urlunsplit`. Lowercase scheme/hostname, strip default ports, preserve non-default ports/path case/trailing slash, set an empty path to `/`, and return `None` for a fragment-only link or unsupported scheme.

Implement `allowed_hosts_for()` by lowercasing the hostname and returning the hostname plus exactly one add/remove-`www.` alias. Implement `is_allowed_host()` against `urlsplit(url).hostname`. Implement `origin_for()` as normalized `scheme://netloc` with no path/query/fragment.

- [ ] **Step 4: Run the URL tests and full test discovery**

Run:

```bash
python3 -m unittest tests.test_crawler.UrlNormalizationTests -v
python3 -m unittest discover -s tests -v
```

Expected: all Task 1 tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add requirements.txt crawler.py tests/__init__.py tests/test_crawler.py
git commit -m "feat: add crawler URL foundations"
```

---

### Task 2: Rate-Limited Requests and robots.txt

**Files:**
- Modify: `crawler.py`
- Modify: `tests/test_crawler.py`

**Interfaces:**
- Consumes: `origin_for()`, `allowed_hosts_for()`, `is_allowed_host()`.
- Produces: `RateLimiter(delay: float)` with `wait() -> None`.
- Produces: `request_once(session, url, limiter, timeout) -> requests.Response`.
- Produces: `RobotsUnavailableError`.
- Produces: `robots_allowed(url, session, limiter, cache, redirect_hosts, timeout) -> bool`.

- [ ] **Step 1: Write failing request and robots tests**

Add a reusable local HTTP server fixture with route counters. Test these exact cases:

```python
def test_request_once_disables_redirects_and_sends_user_agent(self):
    response = request_once(self.session, self.url("/redirect"), RateLimiter(0), 1)
    self.assertEqual(response.status_code, 302)
    self.assertEqual(self.server.last_user_agent, USER_AGENT)

def test_robots_disallow_blocks_without_requesting_page(self):
    cache = {}
    allowed = robots_allowed(
        self.url("/private/page"), self.session, RateLimiter(0), cache,
        {"127.0.0.1"}, 1,
    )
    self.assertFalse(allowed)
    self.assertEqual(self.server.hits["/private/page"], 0)

def test_missing_robots_allows_crawling(self):
    self.server.robots_status = 404
    self.assertTrue(robots_allowed(
        self.url("/page"), self.session, RateLimiter(0), {}, {"127.0.0.1"}, 1
    ))

def test_temporary_robots_failure_is_fatal(self):
    self.server.robots_status = 503
    with self.assertRaises(RobotsUnavailableError):
        robots_allowed(
            self.url("/page"), self.session, RateLimiter(0), {}, {"127.0.0.1"}, 1
        )
```

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
python3 -m unittest tests.test_crawler.RequestAndRobotsTests -v
```

Expected: FAIL because request/robots interfaces are missing.

- [ ] **Step 3: Implement request and robots behavior**

`RateLimiter.wait()` uses `time.monotonic()` and sleeps only for the remaining delay. `request_once()` calls:

```python
limiter.wait()
return session.get(
    url,
    headers={"User-Agent": USER_AGENT},
    timeout=(timeout, timeout),
    allow_redirects=False,
    stream=True,
)
```

`robots_allowed()` caches policy by `origin_for(url)`. Fetch `<origin>/robots.txt` manually with at most `MAX_REDIRECTS` responses. Parse `200` with `RobotFileParser.parse()`, treat all `4xx` as allow-all, raise `RobotsUnavailableError` for `5xx`, network/timeout errors, invalid redirects, redirect loops, too many redirects, or a redirect outside `redirect_hosts`. Use `ROBOTS_USER_AGENT = "LegacySiteInventoryBot"` in `can_fetch()`.

- [ ] **Step 4: Run focused and full tests**

```bash
python3 -m unittest tests.test_crawler.RequestAndRobotsTests -v
python3 -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add crawler.py tests/test_crawler.py
git commit -m "feat: enforce request and robots policies"
```

---

### Task 3: BFS Crawl, Redirects, Limits, and CSV

**Files:**
- Modify: `crawler.py`
- Modify: `tests/test_crawler.py`

**Interfaces:**
- Consumes: URL helpers, dataclasses, `RateLimiter`, `request_once()`, and `robots_allowed()`.
- Produces: `extract_html(response) -> tuple[str, list[str], str | None]` where the third value is an error marker.
- Produces: `crawl(start_url, output_path, *, delay, timeout, max_pages, max_depth, session=None) -> CrawlSummary`.
- Produces: `write_csv(results, output_path) -> None`.

- [ ] **Step 1: Write failing BFS acceptance tests**

Configure local routes for `/`, `/about`, `/duplicate`, `/redirect`, `/asset.pdf`, `/missing`, and `/private`. The home page contains relative links, duplicates, fragments, an external link, a tracking URL, and a robots-disallowed link. Assert:

```python
summary = crawl(
    self.url("/"), self.output_csv,
    delay=0, timeout=1, max_pages=100, max_depth=10,
)
self.assertEqual(summary.completion_reason, "queue_exhausted")
self.assertEqual(self.server.hits["/about"], 1)
self.assertEqual(self.server.hits["/private"], 0)

with open(self.output_csv, encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

self.assertEqual(list(rows[0]), CSV_FIELDS)
self.assertEqual(len({row["url"] for row in rows}), len(rows))
self.assertEqual(row_by_path(rows, "/private")["error"], "robots_disallowed")
self.assertEqual(row_by_path(rows, "/asset.pdf")["content_type"], "application/pdf")
self.assertEqual(row_by_path(rows, "/redirect")["status_code"], "302")
```

Add these focused methods to `CrawlerAcceptanceTests`; the local server fixture exposes routes named in each test and `read_rows()` returns a URL-keyed dictionary:

```python
def test_internal_redirect_target_keeps_depth(self):
    crawl(self.url("/redirect-home"), self.output_csv, delay=0, timeout=1,
          max_pages=20, max_depth=10)
    rows = self.read_rows()
    self.assertEqual(rows[self.url("/redirect-target")]["crawl_depth"], "0")

def test_external_redirect_is_recorded_but_target_is_not_added(self):
    crawl(self.url("/external-redirect-home"), self.output_csv, delay=0, timeout=1,
          max_pages=20, max_depth=10)
    rows = self.read_rows()
    source = rows[self.url("/external-redirect-home")]
    self.assertEqual(source["final_url"], "http://example.invalid/out")
    self.assertEqual(source["error"], "external_redirect")
    self.assertNotIn("http://example.invalid/out", rows)

def test_error_page_html_does_not_produce_links(self):
    crawl(self.url("/error-home"), self.output_csv, delay=0, timeout=1,
          max_pages=20, max_depth=10)
    self.assertNotIn(self.url("/linked-from-404"), self.read_rows())

def test_disconnected_request_is_recorded_and_queue_continues(self):
    summary = crawl(self.url("/failure-home"), self.output_csv, delay=0, timeout=1,
                    max_pages=20, max_depth=10)
    rows = self.read_rows()
    self.assertIn(rows[self.url("/disconnect")]["error"],
                  {"connection_error", "request_error"})
    self.assertEqual(rows[self.url("/after-failure")]["status_code"], "200")
    self.assertEqual(summary.completion_reason, "queue_exhausted")

def test_depth_limited_discovery_is_exported_without_request(self):
    crawl(self.url("/depth/0"), self.output_csv, delay=0, timeout=1,
          max_pages=20, max_depth=1)
    rows = self.read_rows()
    self.assertEqual(rows[self.url("/depth/2")]["crawl_depth"], "2")
    self.assertEqual(rows[self.url("/depth/2")]["error"], "max_depth_exceeded")
    self.assertEqual(self.server.hits["/depth/2"], 0)

def test_page_limited_queue_is_exported_without_request(self):
    summary = crawl(self.url("/page-limit-home"), self.output_csv, delay=0, timeout=1,
                    max_pages=2, max_depth=10)
    rows = self.read_rows()
    self.assertEqual(summary.completion_reason, "max_pages_reached")
    self.assertEqual(rows[self.url("/page-limit-two")]["error"], "max_pages_reached")
    self.assertEqual(self.server.hits["/page-limit-two"], 0)

def test_secondary_origin_robots_failure_stops_and_marks_queue(self):
    summary = crawl(self.url("/secondary-origin-home"), self.output_csv, delay=0,
                    timeout=1, max_pages=20, max_depth=10)
    rows = self.read_rows()
    self.assertEqual(summary.completion_reason, "robots_unreachable")
    self.assertEqual(rows[self.secondary_url("/blocked-by-unreachable-robots")]["error"],
                     "robots_unreachable")
    self.assertEqual(rows[self.url("/queued-after-secondary")]["error"],
                     "crawl_stopped_robots_unreachable")

def test_oversized_html_is_recorded_without_parsing_links(self):
    with mock.patch("crawler.MAX_HTML_BYTES", 32):
        crawl(self.url("/large-html"), self.output_csv, delay=0, timeout=1,
              max_pages=20, max_depth=10)
    rows = self.read_rows()
    self.assertEqual(rows[self.url("/large-html")]["error"], "html_too_large")
    self.assertNotIn(self.url("/inside-large-html"), rows)

def test_first_source_url_wins(self):
    crawl(self.url("/first-source-home"), self.output_csv, delay=0, timeout=1,
          max_pages=20, max_depth=10)
    rows = self.read_rows()
    self.assertEqual(rows[self.url("/shared")]["source_url"], self.url("/first-source-home"))
```

- [ ] **Step 2: Run BFS tests and verify failure**

```bash
python3 -m unittest tests.test_crawler.CrawlerAcceptanceTests -v
```

Expected: FAIL because `crawl()`, HTML extraction, and CSV export are missing.

- [ ] **Step 3: Implement the BFS state machine**

Use insertion-ordered `dict[str, CrawlResult]` plus `set[str]` and `deque[CrawlItem]`. Add each normalized URL to results/seen at discovery time. Bootstrap the home redirect chain at depth 0, count each attempted page request, record every response, establish allowed hosts from the final response hostname, and parse that already-fetched final home response without requesting it again.

For normal BFS:

```text
dequeue -> page cap -> robots -> request -> record -> classify
```

Resolve `Location` relative to the redirect source. Enqueue internal redirect targets at the same depth. Drop external anchor links entirely; record external redirect targets only in the internal source row. Map Requests exceptions to stable errors: `timeout`, `tls_error`, `connection_error`, or `request_error`.

Use streamed reads. For HTML, stop when accumulated bytes exceed `MAX_HTML_BYTES`; parse accepted bytes with Beautiful Soup only when the MIME type is HTML. Normalize title whitespace with `" ".join(title.split())`.

Before every return, export all discovered records. Mark unresolved queued rows with `max_pages_reached`, `crawl_stopped_robots_unreachable`, or `interrupted` as appropriate. Compute `CrawlSummary` counters from final records and request attempts.

- [ ] **Step 4: Run focused and full tests**

```bash
python3 -m unittest tests.test_crawler.CrawlerAcceptanceTests -v
python3 -m unittest discover -s tests -v
```

Expected: all tests PASS with no network access outside the local test server.

- [ ] **Step 5: Commit Task 3**

```bash
git add crawler.py tests/test_crawler.py
git commit -m "feat: crawl internal URLs and export inventory"
```

---

### Task 4: CLI, Documentation, and Release Verification

**Files:**
- Modify: `crawler.py`
- Modify: `README.md`
- Modify: `tests/test_crawler.py`

**Interfaces:**
- Consumes: `crawl()` and `CrawlSummary`.
- Produces: `build_parser() -> argparse.ArgumentParser`.
- Produces: `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write failing CLI tests**

Add tests that call `main()` with a local home URL and temporary CSV path, override delay to zero, capture stdout, and assert:

```python
exit_code = main([
    self.url("/"), "--output", self.output_csv,
    "--delay", "0", "--timeout", "1",
    "--max-pages", "100", "--max-depth", "10",
])
self.assertEqual(exit_code, 0)
self.assertIn("completion_reason=queue_exhausted", stdout.getvalue())
self.assertIn(f"csv_path={self.output_csv}", stdout.getvalue())
```

Also assert invalid schemes and non-positive numeric limits produce argparse errors without starting a request.

- [ ] **Step 2: Run the CLI tests and verify failure**

```bash
python3 -m unittest tests.test_crawler.CliTests -v
```

Expected: FAIL because CLI interfaces or validation are missing.

- [ ] **Step 3: Implement CLI and update README**

Expose positional `start_url` and options `--output`, `--delay`, `--timeout`, `--max-pages`, and `--max-depth`. Defaults come from constants. Print every `CrawlSummary` field as one `key=value` line and return 0 for completed/limited crawls, 1 for an invalid or unreachable start, and 130 for interruption.

Document these exact commands:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python crawler.py http://example.com/ --output inventory.csv
python -m unittest discover -s tests -v
```

README must explain CSV fields, robots behavior, host scope, query normalization, limits, HTTP-only compatibility, and explicit first-version exclusions.

- [ ] **Step 4: Run all automated and syntax checks**

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile crawler.py tests/test_crawler.py
python3 crawler.py --help
git diff --check
```

Expected: tests PASS, compilation succeeds, help exits 0, and `git diff --check` is silent.

- [ ] **Step 5: Commit Task 4**

```bash
git add crawler.py tests/test_crawler.py README.md
git commit -m "docs: add crawler CLI usage"
```

---

### Task 5: Final Review and Push

**Files:**
- Review only: all tracked files.

**Interfaces:**
- Consumes: completed Tasks 1-4.
- Produces: a clean, tested feature branch pushed to `origin`.

- [ ] **Step 1: Run final verification from a clean test process**

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile crawler.py tests/test_crawler.py
git diff --check
git status --short --branch
```

Expected: all tests PASS, compilation succeeds, no whitespace errors, and no uncommitted implementation changes.

- [ ] **Step 2: Review branch diff and commit history**

```bash
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
git log --oneline --decorate origin/main..HEAD
```

Confirm the branch contains only the approved crawler, tests, dependencies, README, design, and plan.

- [ ] **Step 3: Push the implementation branch**

```bash
git push -u origin codex/site-inventory-crawler
```

Expected: the remote branch is created and upstream tracking is configured.
