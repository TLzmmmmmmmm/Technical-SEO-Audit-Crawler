# Website Page and Resource Audit Crawler

A lightweight technical SEO crawler that audits HTML pages and referenced web
resources separately. It follows internal HTML pages breadth-first, checks
embedded resources, and produces a focused Page Audit and Resource Audit.

> **Page count refers only to HTML documents. Images, CSS, JavaScript, PDFs,
> and other assets are reported separately as resources.**

This is a pre-deployment technical check. `indexable=YES` means the response
passes this tool's approved status, robots directive, and canonical rules; it
does not guarantee inclusion in Google, Bing, or another search engine.

## Windows PowerShell setup

Run these commands from the repository directory with Windows Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The `.venv` directory is local and ignored by Git. Recreate it on Windows;
never copy a Linux or WSL virtual environment.

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

Run the tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

CSV and TXT files are ignored by Git except for `requirements.txt`, so normal
crawl outputs such as `pages.csv`, `resources.csv`, and `urls.txt` are not
uploaded.

## What is discovered

Successful HTML responses can discover page links from `<a href>`, images from
`<img src/srcset>` and `<source src/srcset>`, scripts from `<script src>`, and
selected `<link href>` resources: stylesheet, icon, apple-touch-icon,
mask-icon, manifest, preload, and modulepreload.

Every `srcset` candidate becomes an independent URL. Canonical links are SEO
metadata, not resource rows. External `<a>` targets are ignored; external
embedded resources receive a row but are never requested. Internal resources
are requested once, but only successful HTML is parsed recursively. PDFs,
images, CSS, JavaScript, fonts, JSON, audio, video, and other responses are
terminal.

## Page Audit and Resource Audit

Both UTF-8-with-BOM reports preserve first-discovery order. `pages.csv` contains
HTML records only, including redirects, 4xx/5xx pages, robots-blocked pages, and
failed or limited HTML discoveries:

```text
url,status_code,final_url,title,canonical_url,canonical_self_reference,canonical_warning,meta_robots,x_robots_tag,source_url,source_tag,source_attribute,link_rel,discovery_count,crawl_depth,content_type,indexable,indexability_reason,error
```

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

## Indexability rules

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

## Finding and filtering CSV data in PowerShell

PowerShell reads CSV headers as property names. Import both reports once and
reuse them for the following examples:

```powershell
$pages = @(Import-Csv .\audit\pages.csv)
$resources = @(Import-Csv .\audit\resources.csv)
$allRows = $pages + $resources
```

Replace `.\audit` with the directory passed to `--output-dir`.

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

## Crawl behavior

- Requests are single-threaded, rate-limited, and use a 10 second connection and
  read timeout by default. TLS certificate verification keeps Requests defaults.
- Automatic redirects are disabled. Internal redirect targets are queued at the
  same depth; external redirect targets are recorded on the source row and are
  never requested. The initial home-page redirect chain is the only bootstrap
  exception used to establish the final allowed hostname.
- The allowed scope is the final home-page hostname plus exactly its add/remove
  `www.` alias. Other subdomains and domains are ignored. HTTP and HTTPS URLs,
  and bare and `www` URLs, remain distinct inventory entries.
- Query parameters are sorted by name for stable deduplication while same-name
  values retain their relative order. `utm_*`, `gclid`, `fbclid`, and `msclkid`
  are removed, and fragments are discarded.
- robots.txt is fetched per origin and parsed with Protego so the most specific
  `Allow`/`Disallow` rule applies. A missing robots file allows crawling; a 5xx,
  timeout, or network failure stops conservatively and is reported.
- Only successful `text/html` and `application/xhtml+xml` responses recurse.
  HTML bodies over 5 MiB are recorded but not parsed.

The project deliberately excludes concurrency, login, browser automation,
JavaScript rendering, forms, sitemap discovery, `<base href>`, databases,
checkpoint recovery, a GUI, and a general crawler framework.
