# Technical SEO Audit Crawler

[![Tests](https://github.com/TLzmmmmmmmm/Web_Crawler_Practice/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/TLzmmmmmmmm/Web_Crawler_Practice/actions/workflows/tests.yml)

A technical SEO crawler that follows internal HTML pages breadth-first, audits
referenced resources, and generates separate Page Audit and Resource Audit
reports.

> **Page count refers to URLs classified as HTML page records, including page
> discoveries that could not be requested or confirmed. URLs classified as
> images, CSS, JavaScript, PDFs, and other resources are reported separately.**

This is a pre-deployment technical check. `indexable=YES` means the response
passes the status, robots-directive, and canonical rules documented below; it
does not guarantee inclusion in Google, Bing, or another search engine.

## Features

- Breadth-first crawling of internal HTML pages with configurable limits.
- Per-origin `robots.txt` enforcement using Protego.
- Explicit internal/external redirect control.
- Stable URL normalization and request deduplication.
- Canonical, meta robots, and `X-Robots-Tag` indexability checks.
- Discovery and auditing of images, CSS, JavaScript, PDFs, fonts, and media.
- Separate page and resource reports with first-discovery context and reference
  counts.

## Example Output

The numbers below are example values, but the labels and layout match the
terminal summary:

```text
Crawl completed: https://example.com/
Completion reason: queue_exhausted

Pages
  Discovered ........ 42
  Indexable .......... 38
  Non-indexable ...... 4
  Errors ............. 0

Resources
  Images ............. 27
  CSS ................ 3
  JavaScript ......... 6
  PDF ................ 1
  Font ............... 1
  Video .............. 0
  Audio .............. 0
  Other .............. 2
  Errors ............. 0

Output
  audit\pages.csv
  audit\resources.csv

Total unique URLs discovered: 82
```

`pages.csv` records URLs classified as HTML pages, their crawl results, and
their indexability decisions. `resources.csv` records URLs classified as
non-HTML resources and their crawl results.

## Architecture

The crawler uses one discovery and request pipeline, then separates finalized
records by response type:

```text
Start URL
  -> normalize URL and establish allowed host
  -> check robots.txt and crawl limits
  -> request without automatic redirects
  -> classify from Content-Type when present; otherwise use discovery context
     and URL extension
     -> HTML: audit SEO metadata, discover links/resources, continue BFS
     -> non-HTML: audit and record the resource, do not recurse
  -> finalize indexability
  -> write pages.csv and resources.csv
```

Each normalized internal URL is requested at most once. External embedded
resources are inventoried but never requested; external page links are not
added to the crawl queue.

## Quick Start

Run these commands from the repository directory with Windows Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The `.venv` directory is local and ignored by Git. Recreate it on Windows;
never copy a Linux or WSL virtual environment.

Run an audit:

```powershell
.\.venv\Scripts\python.exe crawler.py https://example.com/ --output-dir .\audit
```

Available options are `--delay`, `--timeout`, `--max-pages`, and `--max-depth`.
Defaults are a 0.5 second request interval, 10 second timeout, 3,000 requested
content URLs, and depth 10. The `--max-pages` counter includes the requested
start-page redirect chain and queued internal pages and resources; `robots.txt`
requests are not included. `--output-dir` defaults to the current directory.
During finalization, the crawler writes:

```text
audit\pages.csv
audit\resources.csv
```

The terminal summary reports discovered/indexable HTML pages first, then
resource counts by type. `Total unique URLs discovered` is shown separately and
means pages plus resources; it is not a page count.

The repository's `.gitignore` ignores `*.csv` and `*.txt`, with an exception for
`requirements.txt`.

## Reports

Both UTF-8-with-BOM reports preserve first-discovery order. `pages.csv` contains
records classified as HTML pages, including redirects, 4xx/5xx responses,
robots-blocked discoveries, and failed or limited page discoveries:

### Page Audit: `pages.csv`

```text
url,status_code,final_url,title,canonical_url,canonical_self_reference,canonical_warning,meta_robots,x_robots_tag,source_url,source_tag,source_attribute,link_rel,discovery_count,crawl_depth,content_type,indexable,indexability_reason,error
```

### Resource Audit: `resources.csv`

`resources.csv` contains all records not classified as HTML, without HTML-only
SEO fields:

```text
url,status_code,final_url,resource_type,content_type,source_url,source_tag,source_attribute,link_rel,discovery_count,crawl_depth,indexable,indexability_reason,error
```

First-discovery source metadata is retained. `discovery_count` starts at 1 and
increments for every later occurrence; it is not a unique-referring-page count.
`resource_type` is selected from `pdf`, `image`, `css`, `javascript`, `font`,
`json`, `audio`, `video`, `other`, and `unknown`. Classification uses response
Content-Type when present and otherwise uses discovery hints and extensions.

`error` is reserved for crawl/runtime conditions such as `timeout`,
`robots_disallowed`, or `external_resource_not_requested`. SEO conclusions are
kept in `indexable` and `indexability_reason`.

## Indexability Model

The reports use `YES`, `NO`, and `N/A`:

- HTML is `YES` when status is 200, generic robots directives contain no
  `noindex`, and canonical is missing or self-equivalent to `final_url`.
- PDF is `YES` when status is 200 and `X-Robots-Tag` permits indexing.
- Image is `YES` when status is 200 and robots.txt and `X-Robots-Tag` permit it.
- CSS, JavaScript, fonts, JSON, audio, video, other, and unknown resources are
  `N/A`.

PDFs and images do not require canonical. See
[`docs/indexability.md`](docs/indexability.md) for canonical normalization,
robots directive handling, blocker precedence, and edge-case behavior.

## Crawl Behavior

- Successful HTML responses discover page links from `<a href>`, images from
  `<img src/srcset>`, media resources from `<source src/srcset>`, scripts from
  `<script src>`, and selected `<link href>` resources: stylesheet, icon,
  apple-touch-icon, mask-icon, manifest, preload, and modulepreload.
- Comma-separated `srcset` entries are extracted as individual resource URLs.
  Canonical links are SEO metadata, not resource rows. External page links are
  ignored; external embedded resources receive a row but are never requested.
- Requests are single-threaded and use the configured delay, which defaults to
  0.5 seconds. Connection and read timeouts both default to 10 seconds. TLS
  verification keeps Requests defaults.
- Automatic redirects are disabled. Internal targets are queued at the same
  depth; external targets are recorded but never requested. The initial home
  page redirect chain is the bootstrap exception used to establish scope.
- Request scope is the final home-page hostname plus exactly its add/remove
  `www.` alias. Page links to other hosts are ignored; supported embedded
  resources on other hosts are recorded without being requested. HTTP/HTTPS
  and bare/`www` URLs remain distinct inventory entries.
- Query parameters are sorted by name while same-name values retain their
  relative order. `utm_*`, `gclid`, `fbclid`, and `msclkid` are removed, and
  fragments are discarded.
- `robots.txt` is fetched per origin. A missing file permits crawling; a 5xx,
  timeout, or network failure stops conservatively and is reported.
- Only successful `text/html` and `application/xhtml+xml` responses recurse.
  HTML bodies over 5 MiB are recorded but not parsed.

## Scope and Limitations

The project intentionally excludes:

- Concurrent or distributed crawling
- JavaScript rendering or browser automation
- Configured login, authentication, or form submission
- Sitemap discovery or `<base href>` processing
- Database storage or checkpoint recovery
- A GUI
- A guarantee that a search engine will crawl or index a URL

## Testing

The `unittest` suite uses local HTTP servers and does not contact a live
website. It covers URL normalization, URL discovery and deduplication, robots
rules, redirects, resource classification, indexability, reports, limits,
failures, interrupt handling, and the command-line entry point.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Tech Stack

- Python 3.12
- Requests for HTTP
- Beautiful Soup 4 for HTML parsing
- Protego for `robots.txt` rules
- Standard-library `csv`, `urllib.parse`, `argparse`, and `unittest`

The setup, execution, and report-analysis commands in this README target
Windows PowerShell.

## PowerShell Analysis Examples

PowerShell reads CSV headers as property names. Import both reports once and
reuse them for the following examples:

```powershell
$pages = @(Import-Csv .\audit\pages.csv)
$resources = @(Import-Csv .\audit\resources.csv)
```

Replace `.\audit` with the directory passed to `--output-dir`.

Show HTML pages that returned 200 and passed the indexability checks:

```powershell
$pages |
    Where-Object { $_.status_code -eq "200" -and $_.indexable -eq "YES" } |
    Select-Object url, final_url, title, canonical_url
```

Find non-indexable pages:

```powershell
$pages |
    Where-Object { $_.indexable -eq "NO" } |
    Select-Object url, status_code, indexability_reason, error
```

Find unavailable or failed resources:

```powershell
$resources |
    Where-Object {
        $_.status_code -ne "200" -or
        -not [string]::IsNullOrWhiteSpace($_.error)
    } |
    Select-Object url, resource_type, status_code, error
```

For additional filtering, export, and aggregation examples, see
[`docs/powershell-analysis.md`](docs/powershell-analysis.md).

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE) for details.
