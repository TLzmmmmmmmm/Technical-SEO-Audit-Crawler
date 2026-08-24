# Technical SEO Audit Crawler

A lightweight technical SEO crawler that follows internal HTML pages
breadth-first, audits referenced resources, and generates separate Page Audit
and Resource Audit reports.

> **Page count refers only to HTML documents. Images, CSS, JavaScript, PDFs,
> and other assets are reported separately as resources.**

This is a pre-deployment technical check. `indexable=YES` means the response
passes this tool's approved status, robots directive, and canonical rules; it
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

Output
  audit\pages.csv
  audit\resources.csv

Total unique URLs discovered: 82
```

`pages.csv` answers which HTML pages are indexable. `resources.csv` identifies
referenced assets and failures. Page totals refer only to HTML documents.

## Architecture

The crawler uses one discovery and request pipeline, then separates finalized
records by response type:

```text
Start URL
  -> normalize URL and establish allowed host
  -> check robots.txt and crawl limits
  -> request without automatic redirects
  -> classify by Content-Type
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

Common options are `--delay`, `--timeout`, `--max-pages`, and `--max-depth`.
Defaults are a 0.5 second request interval, 10 second timeout, 3,000 page
requests, and depth 10. `--output-dir` defaults to the current directory. Every
crawl writes:

```text
audit\pages.csv
audit\resources.csv
```

The terminal summary reports discovered/indexable HTML pages first, then
resource counts by type. `Total unique URLs discovered` is shown separately and
means pages plus resources; it is not a page count.

CSV and TXT files are ignored by Git except for `requirements.txt`, so normal
crawl outputs such as `pages.csv`, `resources.csv`, and `urls.txt` are not
uploaded.

## Reports

Both UTF-8-with-BOM reports preserve first-discovery order. `pages.csv` contains
HTML records only, including redirects, 4xx/5xx pages, robots-blocked pages, and
failed or limited HTML discoveries:

### Page Audit: `pages.csv`

```text
url,status_code,final_url,title,canonical_url,canonical_self_reference,canonical_warning,meta_robots,x_robots_tag,source_url,source_tag,source_attribute,link_rel,discovery_count,crawl_depth,content_type,indexable,indexability_reason,error
```

### Resource Audit: `resources.csv`

`resources.csv` contains all non-HTML discoveries without HTML-only SEO fields:

```text
url,status_code,final_url,resource_type,content_type,source_url,source_tag,source_attribute,link_rel,discovery_count,crawl_depth,indexable,indexability_reason,error
```

First-discovery source metadata is retained. `discovery_count` starts at 1 and
increments for every later occurrence; it is not a unique-referring-page count.
`resource_type` is selected from `pdf`, `image`, `css`, `javascript`, `font`,
`json`, `audio`, `video`, `other`, and `unknown`, preferring response
Content-Type over discovery hints and extensions.

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
  `<img src/srcset>` and `<source src/srcset>`, scripts from `<script src>`, and
  selected `<link href>` resources: stylesheet, icon, apple-touch-icon,
  mask-icon, manifest, preload, and modulepreload.
- Every `srcset` candidate becomes an independent URL. Canonical links are SEO
  metadata, not resource rows. External page links are ignored; external
  embedded resources receive a row but are never requested.
- Requests are single-threaded, rate-limited, and use a 10 second connection and
  read timeout by default. TLS verification keeps Requests defaults.
- Automatic redirects are disabled. Internal targets are queued at the same
  depth; external targets are recorded but never requested. The initial home
  page redirect chain is the bootstrap exception used to establish scope.
- Scope is the final home-page hostname plus exactly its add/remove `www.`
  alias. Other subdomains and domains are ignored. HTTP/HTTPS and bare/`www`
  URLs remain distinct inventory entries.
- Query parameters are sorted by name while same-name values retain their
  relative order. `utm_*`, `gclid`, `fbclid`, and `msclkid` are removed, and
  fragments are discarded.
- `robots.txt` is fetched per origin. A missing file permits crawling; a 5xx,
  timeout, or network failure stops conservatively and is reported.
- Only successful `text/html` and `application/xhtml+xml` responses recurse.
  HTML bodies over 5 MiB are recorded but not parsed.

## Scope and Limitations

The project intentionally stays small and predictable. It does not provide:

- Concurrent or distributed crawling
- JavaScript rendering or browser automation
- Login, session, form, or authenticated crawling
- Sitemap discovery or `<base href>` processing
- Database storage or checkpoint recovery
- A GUI or a general-purpose crawler framework
- A guarantee that a search engine will crawl or index a URL

## Testing

The `unittest` suite uses local HTTP servers and does not contact a live
website. It covers URL normalization, robots rules, redirects, BFS discovery,
resource classification, indexability, reports, limits, failures, interrupt
handling, and the command-line entry point.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Tech Stack

- Python 3.12
- Requests for HTTP
- Beautiful Soup 4 for HTML parsing
- Protego for `robots.txt` rules
- Standard-library `csv`, `urllib.parse`, `argparse`, and `unittest`
- Windows PowerShell for setup, execution, and report analysis

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
