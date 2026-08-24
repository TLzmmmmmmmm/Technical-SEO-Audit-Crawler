# Website Asset Inventory Crawler

A small, single-threaded Python crawler that starts from an HTTP(S) home page,
discovers static internal `<a href>` links with breadth-first search, and writes
the discovered assets to CSV.

## Windows PowerShell setup

Run these commands from the repository directory:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The `.venv` directory is intentionally local and ignored by Git. Recreate it on
Windows instead of copying a Linux or WSL virtual environment.

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

## CSV output

The UTF-8-with-BOM CSV preserves first-discovery order and contains:

```text
url,status_code,final_url,title,source_url,crawl_depth,content_type,error
```

Every normalized internal URL is recorded once. URLs blocked by robots.txt or
left unrequested by a depth, page, interruption, or robots failure limit remain
in the inventory with a stable `error` marker. Non-HTML responses retain their
status and Content-Type but are not parsed.

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
- Query parameters are retained and sorted for stable deduplication. `utm_*`,
  `gclid`, `fbclid`, and `msclkid` are removed, and fragments are discarded.
- robots.txt is fetched per origin and parsed with Protego so the most specific
  `Allow`/`Disallow` rule applies. A missing robots file allows crawling; a 5xx,
  timeout, or network failure stops conservatively and is reported.
- Only successful `text/html` and `application/xhtml+xml` responses are parsed.
  HTML bodies over 5 MiB are recorded but not parsed.

The first version deliberately excludes concurrency, login, browser automation,
JavaScript rendering, forms, sitemap discovery, `<base href>`, canonical merging,
databases, checkpoint recovery, a GUI, and a general crawler framework.
