# Indexability Model

The crawler provides a deterministic pre-deployment check. `indexable=YES`
means a URL passes the rules documented here; it does not guarantee that Google,
Bing, or another search engine will crawl or include the URL in an index.

The reports use `YES`, `NO`, and `N/A`.

## HTML

An HTML document is `YES` when all of these conditions are satisfied:

1. `status_code` is exactly 200.
2. Generic meta robots directives contain no exact `noindex` token.
3. Generic `X-Robots-Tag` directives contain no exact `noindex` token.
4. Canonical is missing or is self-equivalent to the response's `final_url`.

A missing canonical is allowed and produces `Canonical missing` rather than a
blocking result. A canonical pointing to another URL, an invalid canonical, or
conflicting canonical tags makes the HTML document `NO`.

## PDF

A PDF is `YES` when its status is 200 and its generic `X-Robots-Tag` does not
contain `noindex`. PDFs do not require canonical metadata.

## Image

An image is `YES` when its status is 200, `robots.txt` allows the request, and
its generic `X-Robots-Tag` does not contain `noindex`. Images do not require
canonical metadata.

## Other resources

CSS, JavaScript, fonts, JSON, audio, video, other, and unknown resources use
`N/A`. An external resource that the crawler records but deliberately does not
request also uses `N/A`.

A robots-blocked HTML document, PDF, or image uses `NO`.

## Robots directive handling

Only generic directive values are evaluated for `noindex`. Crawler-scoped
values such as `googlebot: noindex` are not interpreted as generic `noindex`.
This matches the project's crawler-independent rule rather than attempting to
simulate Googlebot, Bingbot, or another named crawler.

When multiple blocking conditions are present, reasons are reported in this
order:

1. HTTP status
2. `robots.txt`
3. `X-Robots-Tag`
4. Meta robots
5. Canonical

## Canonical comparison

Canonical self-reference is compared with the response's `final_url`, not the
original requested `url`.

For equivalence, normalization:

- resolves relative canonical URLs;
- removes fragments;
- removes `utm_*`, `gclid`, `fbclid`, and `msclkid` tracking parameters;
- sorts query parameters by name; and
- preserves the original relative order of values belonging to the same query
  parameter name.

Tracking parameters and fragments do not make an otherwise self-equivalent
canonical non-indexable. They produce non-blocking warnings instead:

- `Tracking parameters present`
- `Fragment present`
- `Multiple canonical tags`

The displayed canonical retains tracking parameters but removes its fragment.
Conflicting canonical tags remain a blocking condition.

## Report fields

- `indexable`: `YES`, `NO`, or `N/A`.
- `indexability_reason`: `OK`, a non-blocking explanation such as
  `Canonical missing`, or one or more blocking reasons.
- `canonical_self_reference`: whether the normalized canonical is equivalent to
  `final_url`.
- `canonical_warning`: non-blocking canonical quality findings.
- `error`: crawl/runtime conditions such as `timeout`, `robots_disallowed`, or
  `external_resource_not_requested`; it is separate from SEO conclusions.
