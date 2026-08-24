# PowerShell Report Analysis

The crawler writes `pages.csv` for HTML documents and `resources.csv` for
non-HTML assets. PowerShell can filter, search, summarize, and export both
reports without modifying the originals.

The examples below assume the crawler used `--output-dir .\audit`. Change that
path when your reports are elsewhere.

## Import the reports

```powershell
$pages = @(Import-Csv .\audit\pages.csv)
$resources = @(Import-Csv .\audit\resources.csv)
$allRows = $pages + $resources
```

Wrapping `Import-Csv` in `@(...)` keeps the result consistently array-like,
including when a report contains zero or one row.

## Inspect available fields

Show every field and value from the first page:

```powershell
$pages | Select-Object -First 1 | Format-List *
```

Show selected fields as a table:

```powershell
$pages |
    Select-Object url, status_code, indexable, indexability_reason |
    Format-Table -AutoSize
```

## Extract URLs

Display only HTML page URLs:

```powershell
$pages | Select-Object -ExpandProperty url
```

Save one URL per line:

```powershell
$pages |
    Select-Object -ExpandProperty url |
    Set-Content .\urls.txt -Encoding UTF8
```

Save a one-column CSV that retains the `url` header:

```powershell
$pages |
    Select-Object url |
    Export-Csv .\urls.csv -NoTypeInformation -Encoding UTF8
```

Use `$allRows` instead of `$pages` when both page and resource URLs are needed.

## Find particular URLs

Find an exact normalized URL:

```powershell
$allRows | Where-Object { $_.url -eq "https://example.com/products/" }
```

Find URLs containing a path segment:

```powershell
$allRows |
    Where-Object { $_.url -like "*/products/*" } |
    Select-Object url, status_code, indexable, indexability_reason
```

Find PDF or image URLs with a regular expression:

```powershell
$resources |
    Where-Object { $_.url -match "\.(pdf|png|jpe?g|webp)(\?|$)" } |
    Select-Object url, resource_type, status_code, indexable
```

## Filter audit results

Find HTML pages that returned 200 and passed the indexability checks:

```powershell
$pages |
    Where-Object { $_.status_code -eq "200" -and $_.indexable -eq "YES" } |
    Select-Object url, final_url, title, canonical_url, indexability_reason
```

Find non-indexable pages:

```powershell
$pages |
    Where-Object { $_.indexable -eq "NO" } |
    Select-Object url, status_code, indexability_reason, error
```

Export complete non-indexable rows for review in Excel:

```powershell
$pages |
    Where-Object { $_.indexable -eq "NO" } |
    Export-Csv .\non_indexable.csv -NoTypeInformation -Encoding UTF8
```

Find indexable HTML pages whose canonical is missing:

```powershell
$pages |
    Where-Object {
        $_.status_code -eq "200" -and
        $_.indexable -eq "YES" -and
        $_.indexability_reason -eq "Canonical missing"
    } |
    Select-Object url, final_url, indexability_reason
```

Find canonical warnings:

```powershell
$pages |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_.canonical_warning) } |
    Select-Object url, canonical_url, canonical_warning
```

Find crawl/runtime errors independently from SEO conclusions:

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

## Count and summarize results

Count pages, resources, total unique URLs, and non-indexable pages:

```powershell
$pages.Count
$resources.Count
($pages.Count + $resources.Count)
($pages | Where-Object { $_.indexable -eq "NO" }).Count
```

Summarize page results by indexability value:

```powershell
$pages |
    Group-Object indexable |
    Sort-Object Name |
    Select-Object Name, Count
```

Summarize the most common non-indexability reasons:

```powershell
$pages |
    Where-Object { $_.indexable -eq "NO" } |
    Group-Object indexability_reason |
    Sort-Object Count -Descending |
    Select-Object Count, Name
```

Generated CSV and TXT analysis files are ignored by this repository's
`.gitignore`, except for `requirements.txt`.
