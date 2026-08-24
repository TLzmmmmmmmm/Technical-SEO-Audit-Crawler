# Separate Page and Resource Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve one unified crawler while exporting HTML Page Audit rows to `pages.csv`, non-HTML Resource Audit rows to `resources.csv`, and reporting page/resource metrics separately.

**Architecture:** Keep the existing `CrawlResult`, discovery, normalization, BFS, request, classification, canonical, and indexability pipeline. Add a terminal reporting partition with two explicit schemas, build summary metrics from the finalized internal results, and atomically replace the single-file crawl/CLI interface with the confirmed `--output-dir` interface.

**Tech Stack:** Python 3.12, Requests 2.x, Beautiful Soup 4.x, Protego 0.6.x, standard-library `unittest`, `http.server`, `csv`, and `urllib.parse` on Windows PowerShell.

**Spec:** `docs/superpowers/specs/2026-08-24-separated-page-resource-reports-design.md`

## Global Constraints

- Keep one shared crawler and one internal `CrawlResult` model.
- Page count means only records whose final `resource_type` is `html`.
- Write exact `pages.csv` and `resources.csv` filenames beneath `--output-dir`.
- Preserve first-discovery metadata and total-occurrence `discovery_count` semantics.
- Preserve current URL normalization, robots, redirect, crawl-depth, request, canonical, and indexability behavior.
- Prefer actual Content-Type over discovery hints and file extensions.
- Only successful HTML responses recursively discover more URLs.
- Do not parse CSS dependencies or add any other excluded feature from the spec.
- Use TDD and Windows `.\.venv\Scripts\python.exe` commands.
- Do not run a real-site crawl without separate explicit user authorization.
- Do not touch or stage existing user CSV/TXT crawl outputs.

---

### Task 1: Add Dual Report Schemas, Partitioning, and Audio/Video Types

**Files:**
- Modify: `crawler.py:30-97,449-516,717-726`
- Test: `tests/test_crawler.py`

**Interfaces:**
- Produces: `PAGE_CSV_FIELDS: list[str]`.
- Produces: `RESOURCE_CSV_FIELDS: list[str]`.
- Produces: `ReportPaths(pages: Path, resources: Path)`.
- Produces: `report_paths(output_dir: str | Path) -> ReportPaths`.
- Produces: `write_reports(results: dict[str, CrawlResult], output_dir: str | Path) -> ReportPaths`.
- Preserves temporarily: existing `write_csv()` and crawl call sites until Task 3.
- Changes: `classify_resource()` returns `audio` or `video`, never new `media` values.

- [ ] **Step 1: Write failing schema and partition tests**

Add `ReportWriterTests` with a temporary directory and internal records in mixed
first-discovery order:

```python
class ReportWriterTests(unittest.TestCase):
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

        paths = write_reports(results, self.temp_dir.name)

        self.assertEqual(paths.pages.name, "pages.csv")
        self.assertEqual(paths.resources.name, "resources.csv")
        self.assertEqual([row["url"] for row in self.read(paths.pages)], ["/page", "/missing"])
        self.assertEqual([row["url"] for row in self.read(paths.resources)], ["/image"])
        self.assertEqual(list(self.read(paths.pages)[0]), PAGE_CSV_FIELDS)
        self.assertEqual(list(self.read(paths.resources)[0]), RESOURCE_CSV_FIELDS)
```

Add a second test that calls `write_reports({}, output_dir)` and asserts both
header-only files exist with the exact schemas from the design.

- [ ] **Step 2: Write failing audio/video classification tests**

Extend the current classification table with:

```python
(("video/mp4", "/stream", None), "video")
(("audio/mpeg", "/stream", None), "audio")
(("", "/movie.mp4", None), "video")
(("", "/sound.mp3", None), "audio")
```

Change the old `video/mp4 -> media` expectation to `video`.

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.ReportWriterTests tests.test_crawler.ResourceAuditTests -v
```

Expected: new report constants/functions do not exist and media classification
does not distinguish audio from video.

- [ ] **Step 4: Implement exact schemas and report writer**

Add the exact public fields:

```python
PAGE_CSV_FIELDS = [
    "url", "status_code", "final_url", "title", "canonical_url",
    "canonical_self_reference", "canonical_warning", "meta_robots",
    "x_robots_tag", "source_url", "source_tag", "source_attribute",
    "link_rel", "discovery_count", "crawl_depth", "content_type",
    "indexable", "indexability_reason", "error",
]

RESOURCE_CSV_FIELDS = [
    "url", "status_code", "final_url", "resource_type", "content_type",
    "source_url", "source_tag", "source_attribute", "link_rel",
    "discovery_count", "crawl_depth", "indexable",
    "indexability_reason", "error",
]
```

Implement a small shared `_write_report()` helper and partition finalized values
strictly by `result.resource_type == "html"`. Always create both files and return
their `Path` values in `ReportPaths`.

- [ ] **Step 5: Split audio/video classification**

Map `audio/*` to `audio`, `video/*` to `video`, audio extensions to `audio`, and
video extensions to `video`. Add both to recognized discovery hints. Keep
`apply_indexability()` unchanged because both naturally use the excluded-type
`N/A` branch.

- [ ] **Step 6: Run focused and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.ReportWriterTests tests.test_crawler.ResourceAuditTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 7: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "feat: add separate report writer"
```

---

### Task 2: Add Pure Page/Resource Metric Calculation

**Files:**
- Modify: `crawler.py:156-169,728-756`
- Test: `tests/test_crawler.py`

**Interfaces:**
- Produces: `AuditMetrics` containing only derived page/resource report metrics.
- Produces: `_audit_metrics(results: dict[str, CrawlResult]) -> AuditMetrics`.
- Does not modify `CrawlSummary`, `crawl()`, or CLI call sites; Task 3 consumes
  this pure helper during the atomic interface switch.

- [ ] **Step 1: Write a failing summary calculation test**

Build finalized results containing two HTML records and eight resources:

```python
results = {
    "/ok": CrawlResult("/ok", resource_type="html", indexable="YES"),
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
    "/json": CrawlResult("/json", resource_type="json", error="timeout"),
}
```

Assert page count 2, indexable 1, non-indexable 1, resource counts for each named
type, Other 1, resource errors 1, and total unique URLs 10. Also set an HTML
`error` and assert `page_errors == 1`.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.SummaryTests -v
```

Expected: `AuditMetrics` and `_audit_metrics()` do not exist.

- [ ] **Step 3: Implement the pure metric model**

Use explicit derived fields:

```python
@dataclass
class AuditMetrics:
    pages_discovered: int
    indexable_pages: int
    non_indexable_pages: int
    page_errors: int
    resource_counts: dict[str, int]
    resource_errors: int
    total_unique_urls: int
```

Count a page/resource error when `error` is non-empty. Group `json`, `other`,
`unknown`, legacy `media`, and future unlisted types into `other` for presentation.

- [ ] **Step 4: Run focused and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.SummaryTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "feat: calculate page and resource metrics"
```

---

### Task 3: Atomically Integrate Dual Reports into Crawl and CLI

**Files:**
- Modify: `crawler.py:758-1140`
- Modify: `tests/test_crawler.py:571-1081`

**Interfaces:**
- Consumes: `write_reports()` and `_audit_metrics()`.
- Replaces: `crawl(start_url, output_path, ...)` with `crawl(start_url, output_dir, ...)`.
- Replaces: CLI `--output` with `--output-dir` defaulting to `.`.
- Replaces: mixed `CrawlSummary.discovered_urls`/`csv_path` with explicit report
  paths, operational fields, and copied values from `AuditMetrics`.
- Produces: `_summary(results, start_url, completion_reason, requested_urls, paths) -> CrawlSummary`.
- Produces: `format_summary(summary: CrawlSummary) -> str`.
- Removes: legacy mixed `CSV_FIELDS`, `write_csv()`, `csv_path`, and public dataclass-field dumping after all call sites migrate.

- [ ] **Step 1: Migrate local test fixtures to two reports**

In both acceptance test classes, store:

```python
self.output_dir = self.temp_dir.name
self.pages_csv = Path(self.output_dir) / "pages.csv"
self.resources_csv = Path(self.output_dir) / "resources.csv"
```

Add helpers:

```python
def read_report(self, path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

def read_rows(self):
    rows = self.read_report(self.pages_csv) + self.read_report(self.resources_csv)
    return {row["url"]: row for row in rows}
```

Pass `self.output_dir` to `crawl()`. Keep combined lookup only for legacy behavior
assertions; new separation assertions must read the individual report.

- [ ] **Step 2: Add failing output-separation acceptance assertions**

Extend controlled routes with an explicit image 404 and assert:

```python
pages = {row["url"]: row for row in self.read_report(self.pages_csv)}
resources = {row["url"]: row for row in self.read_report(self.resources_csv)}

self.assertIn(self.url("/about"), pages)              # HTML 200
self.assertIn(self.url("/redirect"), pages)           # HTML-inferred 302
self.assertIn(self.url("/missing"), pages)            # HTML 404
self.assertIn(self.url("/asset.pdf"), resources)
self.assertIn(self.url("/missing-image.webp"), resources)
self.assertEqual(resources[self.url("/missing-image.webp")]["status_code"], "404")
self.assertTrue(set(pages).isdisjoint(resources))
```

Assert exact `PAGE_CSV_FIELDS` and `RESOURCE_CSV_FIELDS` headers. Retain existing
checks for `<a>`, image/srcset, script, link, source/srcset, non-HTML termination,
first source, and `discovery_count`.

- [ ] **Step 3: Add failing CLI behavior test**

Update the CLI test route to discover one image and one stylesheet. Invoke:

```python
main([
    self.url(), "--output-dir", self.output_dir,
    "--delay", "0", "--timeout", "1",
    "--max-pages", "100", "--max-depth", "10",
])
```

Assert both files exist and output includes `Pages`, `Resources`, `Images`,
`CSS`, `Output`, both paths, and `Total unique URLs discovered`. Add a parser
test asserting old `--output` exits with code 2 and performs no request.

Add a direct `format_summary()` assertion that contains, in order:

```text
Pages
  Discovered ........ 2
  Indexable .......... 1
  Non-indexable ...... 1
Resources
  Images ............. 1
Output
  <pages path>
  <resources path>
Total unique URLs discovered: 10
```

It must not contain `Total URLs:` or describe 10 as pages.

- [ ] **Step 4: Run RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.CrawlerAcceptanceTests tests.test_crawler.IndexabilityAcceptanceTests tests.test_crawler.CliTests -v
```

Expected: crawl still writes one mixed file and CLI does not recognize
`--output-dir`.

- [ ] **Step 5: Replace crawl export atomically**

Rename the second `crawl()` parameter to `output_dir`. Resolve `ReportPaths`
once near initialization. On invalid Python API start input, write two empty
reports and return the new summary. In the existing `finally` block, finalize
all results, call `write_reports()`, and then close the owned session. Return
the new `_summary()` with `start_url`, finalized results, and both paths.

Do not alter queue, discovery, request, robots, redirect, canonical, or
indexability logic while making this change.

Define the final `CrawlSummary` with `start_url`, `completion_reason`, the seven
page/resource metric fields from `AuditMetrics`, existing operational counters,
and `pages_path`/`resources_path`. `_summary()` calls `_audit_metrics()` and
copies those values into the public summary.

- [ ] **Step 6: Replace CLI interface and presentation**

Change parser description to “Audit HTML pages and referenced resources
separately.” Add:

```python
parser.add_argument(
    "--output-dir",
    default=".",
    help="directory for pages.csv and resources.csv",
)
```

Remove `--output`. Pass `args.output_dir` to `crawl()` and print
`format_summary(summary)` once. Preserve existing exit-code rules for interrupt,
robots/start failure, and success.

`format_summary()` returns the approved hierarchy, includes completion reason
and all resource category lines, and leaves total unique URLs as the final
secondary metric. Do not loop through dataclass fields for presentation.

- [ ] **Step 7: Remove mixed-report compatibility code**

Delete old `CSV_FIELDS`, `write_csv()`, obsolete summary adapters, `csv_path`,
and the unused `dataclasses.fields` import. Search for every legacy name:

```powershell
Select-String -Path crawler.py,tests\test_crawler.py -Pattern 'CSV_FIELDS|write_csv|csv_path|--output(?!-dir)'
```

Expected: no live legacy interface references.

- [ ] **Step 8: Run focused and full GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_crawler.CrawlerAcceptanceTests tests.test_crawler.IndexabilityAcceptanceTests tests.test_crawler.CliTests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 9: Commit**

```powershell
git add crawler.py tests/test_crawler.py
git commit -m "feat: export page and resource audits separately"
```

---

### Task 4: Update Public Documentation and Release-Verify

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-03-site-inventory-crawler-design.md`
- Review: `crawler.py`, `tests/test_crawler.py`, `requirements.txt`, `.gitignore`

**Interfaces:**
- Documents: final Page Audit/Resource Audit purpose, schemas, CLI, page-count definition, filtering commands, and limitations.

- [ ] **Step 1: Rewrite README positioning and usage**

Lead with:

> A lightweight technical SEO crawler that audits HTML pages and referenced web resources separately.

Prominently include:

> **Page count refers only to HTML documents. Images, CSS, JavaScript, PDFs, and other assets are reported separately as resources.**

Document `--output-dir`, `pages.csv`, `resources.csv`, and the new console
hierarchy. Remove every instruction that implies one mixed `inventory.csv`.

- [ ] **Step 2: Update schemas and PowerShell tutorial**

List exact page and resource fields. Update filtering examples so indexable-page
workflows start with:

```powershell
$pages = Import-Csv .\audit\pages.csv
$pages | Where-Object { $_.status_code -eq "200" -and $_.indexable -eq "YES" }
```

Use `resources.csv` for resource health examples:

```powershell
$resources = Import-Csv .\audit\resources.csv
$resources | Where-Object { $_.status_code -ne "200" -or $_.error -ne "" }
```

Retain URL extraction, exact/partial search, export, grouping, and Git-ignore
guidance with the correct source report.

- [ ] **Step 3: Align the original design baseline**

Update its output, summary, classification, tests, and terminology sections and
link to the new separated-report design. Do not duplicate the entire new spec.

- [ ] **Step 4: Run final verification**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -X pycache_prefix="$env:TEMP\site-inventory-separated-reports" -m py_compile crawler.py tests\test_crawler.py
.\.venv\Scripts\python.exe crawler.py --help
git diff --check
git status --short --branch
git check-ignore -v --no-index pages.csv resources.csv urls.txt requirements.txt
```

Expected: all tests pass; compile/help exit 0; no whitespace errors; output
files remain ignored; `requirements.txt` remains the only TXT exception; only
approved source, tests, and documentation differ.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md docs/superpowers/specs/2026-08-03-site-inventory-crawler-design.md
git commit -m "docs: explain page and resource audits"
```

- [ ] **Step 6: Review the complete branch before external actions**

```powershell
git diff --stat origin/codex/site-inventory-crawler...HEAD
git diff --check origin/codex/site-inventory-crawler...HEAD
git log --oneline --decorate origin/codex/site-inventory-crawler..HEAD
git status --short --branch
```

Do not run the Shengborun live regression and do not push until the user chooses
those external actions after reviewing the local implementation result.
