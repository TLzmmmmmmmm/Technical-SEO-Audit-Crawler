# Website Page and Resource Audit Crawler

A lightweight technical SEO crawler that audits HTML pages and referenced web
resources separately. It follows internal HTML pages breadth-first, checks
embedded resources, and produces a focused Page Audit and Resource Audit.

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

## Usage

```powershell
.\.venv\Scripts\python.exe crawler.py http://example.com/ --output-dir .\audit
```

Optional controls are `--delay`, `--timeout`, `--max-pages`, and `--max-depth`.
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

- HTML is `YES` when status is exactly 200, generic meta robots and generic
  `X-Robots-Tag` contain no exact `noindex`, and canonical is missing or points
  to the response's `final_url`.
- PDF is `YES` when status is 200 and generic `X-Robots-Tag` has no `noindex`.
- Image is `YES` when status is 200 and robots.txt and generic
  `X-Robots-Tag` allow it.
- CSS, JavaScript, fonts, JSON, audio, video, other, and unknown resources are
  `N/A`.
- A robots-blocked HTML/PDF/image is `NO`; an unevaluated external resource is
  `N/A`.

PDFs and images do not require canonical. Only generic robots directives are
evaluated; crawler-scoped values such as `googlebot: noindex` are not treated as
generic `noindex`. Multiple blockers are reported in status, robots,
X-Robots-Tag, meta robots, canonical order.

A missing HTML canonical is allowed and reported as `Canonical missing`.
Canonical comparison uses `final_url`, preserves repeated query-value order,
and ignores tracking parameters and fragments for equivalence. The displayed
canonical retains tracking parameters but removes fragments. Non-blocking
problems appear in `canonical_warning`, including `Tracking parameters present`,
`Fragment present`, and `Multiple canonical tags`. A canonical pointing to a
different URL, an invalid canonical, or conflicting canonical tags makes HTML
`NO`.

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
$allRows = $pages + $resources
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

<details>
<summary>More PowerShell analysis examples</summary>

### Inspect the available fields

Show every field and value from the first row:

```powershell
$pages | Select-Object -First 1 | Format-List *
```

Show selected fields as a table:

```powershell
$pages |
    Select-Object url, status_code, indexable, indexability_reason |
    Format-Table -AutoSize
```

### Extract one column

Display only HTML page URLs:

```powershell
$pages | Select-Object -ExpandProperty url
```

Save one URL per line to `urls.txt`:

```powershell
$pages |
    Select-Object -ExpandProperty url |
    Set-Content .\urls.txt -Encoding UTF8
```

To keep a one-column CSV with its `url` header instead, use:

```powershell
$pages |
    Select-Object url |
    Export-Csv .\urls.csv -NoTypeInformation -Encoding UTF8
```

Use `$allRows` instead of `$pages` in either command when a list of every page
and resource URL is required.

### Find particular URLs

Find an exact normalized URL:

```powershell
$allRows | Where-Object { $_.url -eq "https://example.com/products/" }
```

Find URLs containing a word or path segment. PowerShell's `-like` comparison is
case-insensitive by default:

```powershell
$allRows |
    Where-Object { $_.url -like "*/products/*" } |
    Select-Object url, status_code, indexable, indexability_reason
```

Use `-match` for a regular-expression search, for example PDF or image URLs:

```powershell
$resources |
    Where-Object { $_.url -match "\.(pdf|png|jpe?g|webp)(\?|$)" } |
    Select-Object url, resource_type, status_code, indexable
```

### Filter indexability results

Show every row that the crawler evaluated as non-indexable:

```powershell
$pages |
    Where-Object { $_.indexable -eq "NO" } |
    Select-Object url, status_code, indexability_reason, error |
    Format-Table -AutoSize
```

Save those complete rows to another CSV for review in Excel:

```powershell
$pages |
    Where-Object { $_.indexable -eq "NO" } |
    Export-Csv .\non_indexable.csv -NoTypeInformation -Encoding UTF8
```

Find the common submission-ready subset: HTML pages with status 200 and
`indexable=YES`. Because every `pages.csv` row is HTML, no `resource_type`
condition is needed:

```powershell
$pages |
    Where-Object {
        $_.status_code -eq "200" -and
        $_.indexable -eq "YES"
    } |
    Select-Object url, final_url, title, canonical_url, indexability_reason
```

Find indexable HTML 200 pages whose canonical is missing:

```powershell
$pages |
    Where-Object {
        $_.status_code -eq "200" -and
        $_.indexable -eq "YES" -and
        $_.indexability_reason -eq "Canonical missing"
    } |
    Select-Object url, final_url, indexability_reason
```

Find rows with any canonical warning:

```powershell
$pages |
    Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.canonical_warning)
    } |
    Select-Object url, canonical_url, canonical_warning
```

Find request or crawl errors independently from SEO conclusions:

```powershell
$allRows |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_.error) } |
    Select-Object url, status_code, error
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

### Count and summarize results

Count HTML pages, resources, total unique URLs, and non-indexable pages:

```powershell
$pages.Count
$resources.Count
($pages.Count + $resources.Count)
($pages | Where-Object { $_.indexable -eq "NO" }).Count
```

Summarize results by indexability value:

```powershell
$pages |
    Group-Object indexable |
    Sort-Object Name |
    Select-Object Name, Count
```

Summarize the most common reasons for `NO`:

```powershell
$pages |
    Where-Object { $_.indexable -eq "NO" } |
    Group-Object indexability_reason |
    Sort-Object Count -Descending |
    Select-Object Count, Name
```

The generated `.csv` files and `urls.txt` are covered by this repository's
`.gitignore`, so these local analysis results are not included in Git commits.

</details>
