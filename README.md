# Website Asset Inventory and Indexability Crawler

A small, single-threaded Python tool for developers to inventory a static
HTTP(S) website and spot common technical blockers to indexing. It follows
internal HTML pages breadth-first, records embedded resources, and writes an
explainable CSV audit.

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
.\.venv\Scripts\python.exe crawler.py http://example.com/ --output inventory.csv
```

Optional controls are `--delay`, `--timeout`, `--max-pages`, and `--max-depth`.
Defaults are a 0.5 second request interval, 10 second timeout, 3,000 page
requests, and depth 10. The command prints a `key=value` completion summary,
including the stop reason and CSV path.

Run the tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

CSV and TXT files are ignored by Git except for `requirements.txt`, so normal
crawl outputs such as `inventory.csv` and `urls.txt` are not uploaded.

## What is discovered

Successful HTML responses can discover page links from `<a href>`, images from
`<img src/srcset>` and `<source src/srcset>`, scripts from `<script src>`, and
selected `<link href>` resources: stylesheet, icon, apple-touch-icon,
mask-icon, manifest, preload, and modulepreload.

Every `srcset` candidate becomes an independent URL. Canonical links are SEO
metadata, not resource rows. External `<a>` targets are ignored; external
embedded resources receive a row but are never requested. Internal resources
are requested once, but only successful HTML is parsed recursively. PDFs,
images, CSS, JavaScript, fonts, JSON, media, and other responses are terminal.

## CSV output

The UTF-8-with-BOM CSV preserves first-discovery order and contains:

```text
url,status_code,final_url,title,canonical_url,canonical_self_reference,canonical_warning,meta_robots,x_robots_tag,source_url,source_tag,source_attribute,link_rel,discovery_count,crawl_depth,content_type,resource_type,indexable,indexability_reason,error
```

First-discovery source metadata is retained. `discovery_count` starts at 1 and
increments for every later reference. `resource_type` is selected from `html`,
`pdf`, `image`, `css`, `javascript`, `font`, `json`, `media`, `other`, and
`unknown`, preferring response Content-Type over discovery hints and extensions.

`error` is reserved for crawl/runtime conditions such as `timeout`,
`robots_disallowed`, or `external_resource_not_requested`. SEO conclusions are
kept in `indexable` and `indexability_reason`.

## Indexability rules

The CSV uses `YES`, `NO`, and `N/A`:

- HTML is `YES` when status is exactly 200, generic meta robots and generic
  `X-Robots-Tag` contain no exact `noindex`, and canonical is missing or points
  to the response's `final_url`.
- PDF is `YES` when status is 200 and generic `X-Robots-Tag` has no `noindex`.
- Image is `YES` when status is 200 and robots.txt and generic
  `X-Robots-Tag` allow it.
- CSS, JavaScript, fonts, JSON, media, other, and unknown resources are `N/A`.
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
