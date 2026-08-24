# Indexability and Resource Inventory Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inventory embedded resources and export explainable HTML/PDF/image indexability signals without turning the site crawler into a recursive general asset crawler.

**Architecture:** Preserve the single-file runtime and BFS. Add small dataclasses and pure helpers for document extraction, resource classification, canonical comparison, and indexability, then integrate them into first-discovery state so each normalized URL is requested once while every reference increments `discovery_count`.

**Tech Stack:** Python 3.12, Requests 2.x, Beautiful Soup 4.x, Protego 0.6.x, standard-library `unittest`, `http.server`, `csv`, and `urllib.parse` on Windows PowerShell.

**Spec:** `docs/superpowers/specs/2026-08-24-indexability-resource-inventory-design.md`

## Global Constraints

- Preserve single-threaded GET requests and all current request, depth, redirect, HTML-size, robots, host-scope, and TLS defaults.
- Never automatically follow redirects or request external embedded resources.
- Only successful HTML responses recurse; other resources are terminal rows.
- Preserve first source metadata; count every repeated reference; request each normalized URL at most once.
- Export `YES`, `NO`, or `N/A`, never Python booleans.
- Keep crawl failures in `error` and SEO outcomes in the audit fields.
- Do not modify `shengborun_inventory.csv`, `urls.txt`, `.venv`, or unrelated files.
- Use TDD and Windows `.\.venv\Scripts\python.exe` commands throughout.

---

### Task 1: Extend the Schema and Fix Repeated Query Ordering

**Files:**
- Modify: `crawler.py:34-66,199-241`
- Test: `tests/test_crawler.py:51-117,382-396`

**Interfaces:**
- Produces: expanded `CSV_FIELDS` and `CrawlResult` fields from the spec.
- Produces: `_stable_query(pairs: list[tuple[str, str]]) -> str`.
- Preserves: `normalize_url(href: str, base_url: str | None = None) -> str | None`.

- [ ] **Step 1: Write failing schema and query tests**

```python
EXPECTED_CSV_FIELDS = [
    "url", "status_code", "final_url", "title", "canonical_url",
    "canonical_self_reference", "canonical_warning", "meta_robots",
    "x_robots_tag", "source_url", "source_tag", "source_attribute",
    "link_rel", "discovery_count", "crawl_depth", "content_type",
    "resource_type", "indexable", "indexability_reason", "error",
]

def test_sorts_names_but_preserves_repeated_value_order(self):
    self.assertEqual(
        normalize_url("https://example.com/?tag=b&x=1&tag=a"),
        "https://example.com/?tag=b&tag=a&x=1",
    )
```

Assert `CSV_FIELDS == EXPECTED_CSV_FIELDS` separately.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.UrlNormalizationTests -v
```

Expected: current tuple sorting reorders repeated values and the schema is incomplete.

- [ ] **Step 3: Implement the schema and stable name-only sorting**

Add all approved result fields with string defaults and `discovery_count: int = 1`. Implement:

```python
def _stable_query(pairs: list[tuple[str, str]]) -> str:
    return urlencode(sorted(pairs, key=lambda pair: pair[0]), doseq=True)
```

Use it in `normalize_url()` without deduplication.

Remove the two pre-existing no-op expressions `crawl,` and `main,` after `unittest.main()` while touching the test module; they are not test registrations and have no runtime purpose.

- [ ] **Step 4: Run focused and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.UrlNormalizationTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "feat: extend crawler audit schema"
```

---

### Task 2: Extract SEO Metadata and Typed HTML References

**Files:**
- Modify: `crawler.py:270-290`
- Test: `tests/test_crawler.py`

**Interfaces:**
- Produces: `DiscoveredReference(url: str, source_tag: str, source_attribute: str, link_rel: str = "", resource_hint: str = "")`.
- Produces: `HtmlDocument(title: str, canonical_values: list[str], meta_robots: str, references: list[DiscoveredReference])`.
- Produces: `parse_srcset(value: str) -> list[str]`.
- Replaces: `extract_html(response) -> tuple[HtmlDocument | None, str | None]`.

- [ ] **Step 1: Write failing extraction tests**

Use a real `requests.Response` containing duplicate anchors, `img src/srcset`, `source srcset`, `script src`, allowed/rejected link relations, canonical tags, and generic meta robots. Assert:

```python
self.assertEqual(parse_srcset("a.webp 1x, b.webp 2x"), ["a.webp", "b.webp"])
self.assertEqual(document.meta_robots, "index, nofollow")
self.assertEqual(document.canonical_values, ["/page/", "/other/"])
self.assertEqual(
    [(r.url, r.source_tag, r.source_attribute, r.link_rel) for r in document.references],
    [
        ("/next", "a", "href", ""),
        ("/image.webp", "img", "src", ""),
        ("/image-2.webp", "img", "srcset", ""),
        ("/app.js", "script", "src", ""),
        ("/style.css", "link", "href", "stylesheet"),
        ("/font.woff2", "link", "href", "preload"),
    ],
)
```

Assert canonical/alternate/preconnect/dns-prefetch links are not ordinary references and `preload as="font"` supplies `resource_hint="font"`.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.HtmlExtractionTests -v
```

Expected: new parser interfaces do not exist.

- [ ] **Step 3: Implement bounded extraction**

Retain the streamed 5 MiB limit. Normalize rel tokens to lowercase for matching, store a space-joined rel value, collect all canonical hrefs in order, collect every generic `meta[name=robots]` content in document order into one `; `-joined string, and parse each srcset candidate independently.

- [ ] **Step 4: Run focused and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.HtmlExtractionTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Adapt existing call sites to the document object without adding crawl discovery behavior in this task.

- [ ] **Step 5: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "feat: extract SEO metadata and resource references"
```

---

### Task 3: Implement Resource, Canonical, and Indexability Pure Logic

**Files:**
- Modify: `crawler.py`
- Test: `tests/test_crawler.py`

**Interfaces:**
- Produces: `classify_resource(content_type: str, url: str, reference: DiscoveredReference | None = None) -> str`.
- Produces: `CanonicalAudit(display_url: str, self_reference: str, warning: str, blocker: str)`.
- Produces: `audit_canonical(values: list[str], final_url: str) -> CanonicalAudit`.
- Produces: `has_noindex(value: str) -> bool`.
- Produces: `apply_indexability(result: CrawlResult, canonical_blocker: str = "") -> None`.

- [ ] **Step 1: Write failing resource classification tests**

Cover every approved MIME class plus fallback by `as`, tag/rel, extension, and unknown:

```python
self.assertEqual(classify_resource("image/webp", "/x"), "image")
self.assertEqual(classify_resource("", "/font.woff2"), "font")
self.assertEqual(classify_resource("application/octet-stream", "/x"), "other")
```

- [ ] **Step 2: Write failing canonical matrix tests**

Cover missing, relative self, tracking/fragment self, other URL, invalid URL, duplicate same, and conflicting values:

```python
audit = audit_canonical(
    ["/products/?utm_source=test#details"],
    "https://example.com/products/",
)
self.assertEqual(audit.self_reference, "YES")
self.assertEqual(
    audit.warning,
    "Tracking parameters present; Fragment present",
)
```

Assert comparison uses `final_url` and distinguishes scheme, hostname, and trailing slash.

- [ ] **Step 3: Write failing indexability table tests**

Use literal HTML/PDF/image/N/A cases. Assert combined blocker order:

```python
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
```

- [ ] **Step 4: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.ResourceAuditTests -v
```

Expected: pure audit helpers are missing.

- [ ] **Step 5: Implement the pure helpers**

Use MIME-first classification. Treat `noindex` as a case-insensitive complete directive token, not a substring. Resolve canonical against `final_url`; retain tracking in display output, remove fragments there, but filter tracking/fragment in the comparison key. Pass `CanonicalAudit.blocker` to `apply_indexability()`. Apply blockers in status, robots, X-Robots-Tag, meta robots, canonical order; use `HTTP status unavailable` when an evaluated resource has no response status. Return N/A before SEO checks for excluded resource types and external rows.

- [ ] **Step 6: Run focused and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.ResourceAuditTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 7: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "feat: evaluate resource indexability"
```

---

### Task 4: Integrate Discovery Counts, External Rows, and Terminal Resources

**Files:**
- Modify: `crawler.py:344-553`
- Test: `tests/test_crawler.py:230-476`

**Interfaces:**
- Extends: `discover(url, source_url, depth, *, source_tag="", source_attribute="", link_rel="", resource_hint="", enqueue=True) -> bool`.
- Consumes: `HtmlDocument` and `classify_resource()`; Task 5 connects the SEO audit helpers.

- [ ] **Step 1: Write a failing embedded-resource acceptance test**

Add internal image/srcset, script, stylesheet, PDF, repeated logo references, and external embedded resources to local routes. Assert:

```python
self.assertEqual(rows[self.url("/logo.webp")]["resource_type"], "image")
self.assertEqual(rows[self.url("/logo.webp")]["discovery_count"], "3")
self.assertEqual(rows[self.url("/logo.webp")]["source_tag"], "img")
self.assertEqual(rows[self.url("/logo.webp")]["source_attribute"], "src")
self.assertEqual(self.server.hits["/logo.webp"], 1)
self.assertEqual(self.server.hits["/app.js"], 1)
self.assertNotIn(self.url("/fake-link-inside-js"), rows)
```

Assert external script/image rows are recorded once, never requested, and use N/A/external reason/error; assert an external `<a>` is absent.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.CrawlerAcceptanceTests.test_embedded_resources_are_inventoried_without_non_html_recursion -v
```

Expected: embedded resource rows and source/count fields are absent.

- [ ] **Step 3: Extend first-discovery state**

On first discovery, store source metadata, hint-derived type, and count 1. On duplicate discovery, increment only the count; never overwrite source fields or enqueue again. For external embedded references, create a non-enqueued row with external N/A values. Continue dropping external anchors.

- [ ] **Step 4: Integrate responses and terminal-resource behavior**

Classify from actual Content-Type after every response. Populate the extracted document and discover references only for successful HTML. Keep PDF, image, CSS, JavaScript, font, JSON, media, other, and unknown rows terminal even if their bytes contain HTML-looking text. Store extracted document metadata on HTML rows, but defer canonical/indexability decisions to Task 5.

- [ ] **Step 5: Run acceptance and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.CrawlerAcceptanceTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 6: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "feat: inventory embedded site resources"
```

---

### Task 5: Add the End-to-End Indexability Matrix

**Files:**
- Modify: `tests/test_crawler.py`
- Modify: `crawler.py` only for defects exposed by RED tests

**Interfaces:**
- Verifies: final integrated CSV contract.

- [ ] **Step 1: Add controlled local routes**

Cover self/missing/tracking/other/duplicate/conflicting canonical, meta noindex, X-Robots noindex, multiple blockers, indexable/noindex PDF, indexable/robots-disallowed image, CSS, JS, font, and non-200 HTML.

- [ ] **Step 2: Add literal CSV assertions**

```python
self.assertEqual(rows[self.url("/self")]["indexable"], "YES")
self.assertEqual(rows[self.url("/self")]["indexability_reason"], "OK")
self.assertEqual(
    rows[self.url("/missing-canonical")]["indexability_reason"],
    "Canonical missing",
)
self.assertEqual(rows[self.url("/other-canonical")]["indexable"], "NO")
self.assertEqual(rows[self.url("/document.pdf")]["indexability_reason"], "OK")
self.assertEqual(
    rows[self.url("/photo.webp")]["indexability_reason"],
    "Image resource allowed",
)
self.assertEqual(rows[self.url("/style.css")]["indexable"], "N/A")
```

Also assert warnings, multiple blocker order, raw robots fields, and exact CSV order.

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.IndexabilityAcceptanceTests -v
```

Expected: audit fields remain blank because Task 4 stored metadata and resource types but did not yet connect the pure audit helpers.

- [ ] **Step 4: Connect response metadata to the pure audit helpers**

Store the raw `X-Robots-Tag` response value. For HTML, call `audit_canonical(document.canonical_values, result.final_url)`, copy its display/self/warning fields, then call `apply_indexability(result, audit.blocker)`. For PDF and image call `apply_indexability(result)` without a canonical blocker. Before every CSV export, finalize unrequested, robots-disallowed, redirect, limited, interrupted, and failed rows so evaluated resource types receive ordered reasons and excluded types receive N/A. Do not change literal expectations unless they contradict the approved spec.

- [ ] **Step 5: Run focused and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.IndexabilityAcceptanceTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 6: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "test: cover crawler indexability audit"
```

---

### Task 6: Document and Release-Verify the Upgrade

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-03-site-inventory-crawler-design.md`
- Review: `crawler.py`, `tests/test_crawler.py`, `requirements.txt`

**Interfaces:**
- Documents: final schema, discovery scope, indexability semantics, and limitations.

- [ ] **Step 1: Update README**

Replace the old schema and document YES/NO/N/A, canonical missing/warnings, external embedded rows, first-source fields, discovery counts, supported tags/link relations, Content-Type classification, and the non-guarantee of real search-engine inclusion.

- [ ] **Step 2: Align the original design baseline**

Update its HTML discovery, query sorting, CSV, response classification, tests, and exclusions, and link to the 2026-08-24 upgrade spec.

- [ ] **Step 3: Run final verification**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile crawler.py tests\test_crawler.py
.\.venv\Scripts\python.exe crawler.py --help
git diff --check
git status --short --branch
```

Expected: all tests pass, compile/help exit 0, no whitespace errors, only approved files differ, and the two user output files remain untouched.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md docs/superpowers/specs/2026-08-03-site-inventory-crawler-design.md
git commit -m "docs: explain resource indexability audit"
```

- [ ] **Step 5: Review before any push**

```powershell
git diff --stat origin/codex/site-inventory-crawler...HEAD
git diff --check origin/codex/site-inventory-crawler...HEAD
git log --oneline --decorate origin/codex/site-inventory-crawler..HEAD
```

Expected: only the approved audit upgrade, tests, specs/plans, and README are present. Never run a real-site crawl without separate explicit authorization.
