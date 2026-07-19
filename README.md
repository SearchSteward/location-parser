# location-parser

[![tests](https://github.com/searchsteward/location-parser/actions/workflows/tests.yml/badge.svg)](https://github.com/searchsteward/location-parser/actions/workflows/tests.yml) [![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Parse the location strings that job boards actually emit.**

The location normalizer from [SearchSteward](https://searchsteward.com)'s
ingest pipeline, published as a standalone, dependency-free Python library.
It turns the free-text location field of a job posting into structured
`{city, admin, country, remote}` components and a clean display string.

Real ATS location fields are not clean data. Across millions of scraped
postings we've stored, all of these are *actual values* from the location
field:

| Raw value | What it should mean |
|---|---|
| `Gretna, Louisiana / R-26-0036719` | Workday appends the requisition id |
| `San Francisco, CA / {'name': 'San Francisco, CA'}` | Greenhouse leaks its API dict |
| `Washington, DC 20004` | trailing ZIP makes "washington" parse as WA state |
| `9 Locations` | a count, not a place |
| `Us - San Francisco`, `Nyc`, `9Th Arrondissement` | `.title()` corruption |
| `Mexico, Remote` | remote — but **not** US-remote |
| `Hybrid - Chicago, IL` | work arrangement glued onto the place |

This library handles all of them.

## Install

```bash
# One file, stdlib only — vendor it:
curl -O https://raw.githubusercontent.com/searchsteward/location-parser/main/location_parser.py
```

A PyPI package (`steward-location-parser`) is planned; the file above is the same code.

## Usage

```python
from location_parser import (
    parse_job_location, normalize_location_text, titlecase_place, is_us_or_remote,
)

parse_job_location("Hybrid - Washington, DC 20004")
# {'remote': False, 'city': 'washington', 'admin': 'DC', 'country': 'US'}

normalize_location_text("Gretna, Louisiana / R-26-0036719")
# 'Gretna, LA, US'

normalize_location_text("Mexico, Remote")
# 'Mexico, Remote'          — the country survives; this is NOT a US-remote role

normalize_location_text("Remote - United States")
# 'Remote'

titlecase_place("us - san francisco")
# 'US - San Francisco'      — .title() would give 'Us - San Francisco'

is_us_or_remote("EMEA [Remote]")   # False — remote, but scoped to a region
is_us_or_remote("California, Remote")  # True
```

## What it handles

- **300+ country/state/city aliases** — `"deutschland"` → `DE`, `"bombay"` →
  `mumbai`, `"sf bay area"` → `san francisco`, full US state names and
  abbreviations, Canadian provinces.
- **Remote detection that keeps the geography.** `"Remote - Philippines"` is
  remote *and* Philippine — collapsing it to bare `"Remote"` is how foreign
  roles sneak into US-only feeds. Bare `"Remote"` with no other geography
  defaults to US-remote.
- **ATS corruption stripping:** Workday trailing req-ids, both Greenhouse
  dict-leak formats (YAML-flow and Python-repr, including unclosed-brace
  variants), trailing US ZIPs, `"N Locations"` placeholders, work-arrangement
  prefixes ("hybrid in", "on-site in", "based in").
- **Casing that doesn't destroy meaning.** `titlecase_place` preserves
  acronyms (`NYC`, `EMEA`), already-uppercase state codes (`Portland, OR` —
  where `.lower()` would turn Oregon into a disjunction), ordinals (`9th`),
  and mixed case (`McLean`).
- **Garbage rejection:** `"Not specified"`, `"TBD"`, `"Various"`, `"Flexible"`
  parse to nothing instead of fake places.

## Scope

- Rule-based and deterministic — no geocoding service, no network, no
  dependencies. Ambiguity is resolved conservatively (e.g. `"CA"` is never
  guessed between California and Canada from casing alone).
- US-centric admin parsing (states first-class, provinces recognized for
  casing); international locations resolve to `city + country`.

Location parsing originally ported and adapted from
[Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) (MIT).

## About

Built and maintained by [SearchSteward](https://searchsteward.com) — a
job-search radar that watches 40,000+ company career pages and scores every
new opening against your résumé. A location filter is only as good as its
parser, so we publish the parser.

## License

[MIT](LICENSE). Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
