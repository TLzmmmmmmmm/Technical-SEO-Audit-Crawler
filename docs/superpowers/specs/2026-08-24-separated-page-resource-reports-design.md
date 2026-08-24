# Page Audit and Resource Audit Reporting Design

Date: 2026-08-24

## 1. Goal

Refactor the crawler's reporting layer so one shared discovery, normalization,
fetch, deduplication, and audit pipeline produces two public reports:

```text
pages.csv
resources.csv
```

`pages.csv` represents the Page Audit and contains HTML document records only.
`resources.csv` represents the Resource Audit and contains every discovered
non-HTML record. Page count always means the number of HTML records; it never
includes images, CSS, JavaScript, PDFs, fonts, audio, video, or other assets.

The refactor must preserve existing discovery, robots, redirect, crawl-depth,
first-source, discovery-count, canonical, and indexability behavior unless this
document explicitly changes a reporting interface.

## 2. Current Architecture

The repository intentionally uses one runtime module, `crawler.py`, and one
test module, `tests/test_crawler.py`.

The current pipeline already has the desired shared behavior:

1. `extract_html()` finds anchors and supported embedded-resource references.
2. `normalize_url()` resolves, normalizes, and deduplicates discoveries.
3. `crawl()` owns one `seen` set, one BFS queue, robots checks, requests,
   redirects, first-source metadata, and `discovery_count`.
4. `classify_resource()` uses response MIME type before discovery hints and
   extensions.
5. `audit_canonical()` and `apply_indexability()` populate SEO audit fields.
6. One `CrawlResult` model represents both HTML and non-HTML records.

The conflict is limited to reporting: `write_csv()` currently exports every
record with one mixed schema, `CrawlSummary` reports mixed URL counts, and the
CLI accepts a single `--output` file path.

## 3. Architecture Decision

Keep the unified internal `CrawlResult` model and the unified `crawl()` pipeline.
Do not create separate page and resource crawlers or duplicate scheduling logic.

After all results are finalized, a new reporting function partitions records by
their final `resource_type` while retaining their original first-discovery order:

```text
resource_type == "html"  -> pages.csv
resource_type != "html"  -> resources.csv
```

This makes reporting separation a terminal operation and prevents page/resource
concepts from changing URL discovery or request behavior.

## 4. Files to Modify

- `crawler.py`
  - replace the mixed CSV schema with page and resource schemas;
  - replace `write_csv()` with a dual-report writer;
  - update `CrawlSummary`, summary calculation, CLI arguments, and presentation;
  - split audio and video resource classification for user-facing counts.
- `tests/test_crawler.py`
  - update existing fixtures to read both reports;
  - add separation, non-200 HTML, resource failure, summary, and CLI tests;
  - retain current discovery, canonical, first-source, and deduplication coverage.
- `README.md`
  - reposition the project as separate Page Audit and Resource Audit reports;
  - update usage, schemas, PowerShell filtering examples, and page-count wording.
- `docs/superpowers/specs/2026-08-03-site-inventory-crawler-design.md`
  - align the original baseline with the new reporting interface and terminology.

No new Python package or runtime dependency is required. The single-file runtime
is retained because this is a reporting refactor, not a crawler-framework split.

## 5. CLI and Output Paths

The confirmed public CLI replaces the conflicting single-file argument:

```powershell
.\.venv\Scripts\python.exe crawler.py https://example.com/ --output-dir .\audit
```

`--output-dir` defaults to the current directory. The crawler creates the
directory when necessary and writes exactly:

```text
<output-dir>\pages.csv
<output-dir>\resources.csv
```

The old `--output inventory.csv` option is removed. This is an intentional
breaking interface change because a single output file contradicts the core
two-report product model.

The Python entry point becomes conceptually:

```python
crawl(start_url, output_dir, *, delay, timeout, max_pages, max_depth, session)
```

No compatibility mixed CSV is written.

## 6. Page Audit Schema

`pages.csv` uses UTF-8 with BOM and contains HTML records in first-discovery
order with this exact public schema:

```text
url
status_code
final_url
title
canonical_url
canonical_self_reference
canonical_warning
meta_robots
x_robots_tag
source_url
source_tag
source_attribute
link_rel
discovery_count
crawl_depth
content_type
indexable
indexability_reason
error
```

`resource_type` is omitted because every row is HTML. HTML 200, redirects, 4xx,
5xx, request failures, robots-blocked rows, limited rows, and interrupted rows
remain eligible for the Page Audit when their MIME type or discovery context
classifies them as HTML. Non-200 records must not be filtered out during export.

## 7. Resource Audit Schema

`resources.csv` uses UTF-8 with BOM and contains every non-HTML record in
first-discovery order with this exact schema:

```text
url
status_code
final_url
resource_type
content_type
source_url
source_tag
source_attribute
link_rel
discovery_count
crawl_depth
indexable
indexability_reason
error
```

HTML-only metadata is intentionally omitted. Existing PDF and image
indexability outcomes remain visible through `indexable` and
`indexability_reason`; CSS, JavaScript, fonts, JSON, media, other, and unknown
resources remain `N/A` when applicable.

An empty partition still produces a header-only CSV so downstream scripts can
rely on both files existing after every crawl outcome.

## 8. Classification and Non-200 Records

Actual response Content-Type remains the primary classification signal. When no
usable response MIME type exists, first-discovery context and the URL extension
remain fallbacks. This preserves important records:

- an `<a href>` target with an HTML 404 response belongs to `pages.csv`;
- an initial or anchor-discovered redirect inferred as HTML belongs to
  `pages.csv` when the response provides no more specific MIME type;
- an `<img>` returning 404 remains an image resource when MIME or discovery
  context identifies it as an image;
- `/download?id=123` returning `application/pdf` belongs to `resources.csv` as
  `pdf`, regardless of its extension or anchor origin.

Resource types `audio` and `video` replace the current combined `media` result
for corresponding MIME types and extension fallbacks. Existing `media` values
are not emitted by new crawls. JSON, other, and unknown remain distinct in the
CSV but are grouped into Other in the compact CLI summary.

## 9. Preserved Discovery Semantics

The supported sources remain:

```text
<a href>
<img src>
<img srcset>
<script src>
<link href>
<source src>
<source srcset>
```

Every srcset candidate is normalized independently. CSS `url(...)` and
`@import` are not parsed. Only successful HTML responses recurse.

Each normalized URL still has one internal `CrawlResult` and is requested at
most once. Every occurrence increments `discovery_count`; it is not a unique
referring-page count. `source_url`, `source_tag`, `source_attribute`, and
`link_rel` remain first-discovery-wins. External anchors remain ignored and
external embedded resources remain non-requested Resource Audit rows.

## 10. Canonical and Indexability Semantics

Canonical analysis continues to apply only to HTML and compares the normalized
canonical against normalized `final_url`. Existing normalization is preserved:
scheme and hostname case folding, default-port removal, fragment removal,
stable name-only query sorting, repeated-value order preservation, tracking
parameter removal for equivalence, and preservation of HTTP/HTTPS, apex/www,
path case, and trailing-slash differences.

Canonical missing remains `YES / Canonical missing` when no other blocker
exists. Tracking or fragment warnings remain advisory. Canonical to another
URL, conflicting tags, or invalid canonical remain blockers.

HTML continues to evaluate HTTP status, generic meta robots, generic
X-Robots-Tag, and canonical. PDF and image keep their current HTTP-level rules
without requiring canonical. CSS, JavaScript, font, JSON, audio, video, other,
and unknown use `N/A / Resource type not evaluated`.

## 11. Summary Model and CLI Presentation

`CrawlSummary` retains operational completion information needed by callers but
adds explicit report paths and page/resource metrics. The user-facing CLI is a
formatted hierarchy rather than a dataclass field dump:

```text
Crawl completed: https://example.com/
Completion reason: queue_exhausted

Pages
  Discovered ........ 67
  Indexable .......... 66
  Non-indexable ...... 1
  Errors ............. 0

Resources
  Images ............. 76
  CSS ................ 5
  JavaScript ......... 0
  PDF ................ 0
  Font ............... 0
  Video .............. 0
  Audio .............. 0
  Other .............. 0
  Errors ............. 0

Output
  pages.csv
  resources.csv

Total unique URLs discovered: 148
```

Exact spacing is not contractual. The labels and semantics are. Page errors
and resource errors count rows with a non-empty `error`. Indexable pages count
`indexable == "YES"`; non-indexable pages count `indexable == "NO"`.

The resource categories map as follows:

- Images: `image`
- CSS: `css`
- JavaScript: `javascript`
- PDF: `pdf`
- Font: `font`
- Video: `video`
- Audio: `audio`
- Other: `json`, `other`, `unknown`, and any future unlisted non-HTML type

`Total unique URLs discovered` remains secondary and equals pages plus
resources. It must never be labeled as a page count.

## 12. Error and Stop Behavior

The reporting refactor does not change request, robots, redirect, page-limit,
depth-limit, interrupt, or conservative-stop behavior. The `finally` path
finalizes all internal results and writes both reports for normal completion,
errors, robots failures, and user interruption. Invalid start input handled by
the Python API also produces both header-only reports.

## 13. Test Strategy

Use the existing `unittest` local HTTP servers and TDD. Tests must establish:

1. Exact, non-overlapping schemas and header-only report behavior.
2. HTML 200, HTML redirect, and HTML 404 rows appear in `pages.csv`.
3. Image, CSS, JavaScript, PDF, font, audio, video, and other rows appear in
   `resources.csv` and never recursively parse.
4. Failed/non-200 resources retain status/error in `resources.csv`.
5. Every supported HTML discovery attribute remains covered.
6. Deduplication, total occurrence counting, and first-source metadata remain
   unchanged across the partition.
7. Existing canonical normalization/indexability tests remain green, including
   repeated query values and HTTP/www/path/trailing-slash distinctions.
8. Summary page count excludes every resource; resource counts match types.
9. CLI accepts `--output-dir`, writes both files, and prints the page/resource
   hierarchy with total URL count secondary.
10. Full existing tests pass after fixtures are migrated from one mixed CSV to
    the two reports.

No live-site counts are hardcoded. The known Shengborun 67-page/81-resource
split is an optional external regression expectation, not an automated rule.

## 14. Documentation and Limitations

README positioning becomes: “A lightweight technical SEO crawler that audits
HTML pages and referenced web resources separately.” It must prominently state:

> **Page count refers only to HTML documents. Images, CSS, JavaScript, PDFs,
> and other assets are reported separately as resources.**

PowerShell filtering examples use `pages.csv` for indexable-page workflows and
`resources.csv` for asset health checks.

This version does not add CSS dependency parsing, browser rendering, sitemap
discovery, referring-page counts, backlink analysis, image-alt audits,
performance tooling, search-engine APIs/submission, text exports, databases, or
dashboards. A real-site regression crawl requires separate explicit user
authorization because it performs external requests and writes new local
reports.

## 15. Acceptance Criteria

- One shared crawl produces both CSV files on every completion path.
- No normalized URL appears in both files.
- Every internal result appears in exactly one report.
- `pages.csv` contains HTML records only and includes non-200 HTML records.
- `resources.csv` contains all non-HTML records without HTML-only columns.
- Page count equals the number of `pages.csv` data rows.
- CLI clearly separates Page Audit and Resource Audit metrics.
- Existing discovery, deduplication, first-source, canonical, indexability,
  robots, redirect, and crawl-limit tests remain green.
- The README and design baseline use Page/Resource terminology consistently.
