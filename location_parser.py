"""Location normalization for job posting location strings.

Parses free-text location fields into structured city/admin/country components
and detects remote roles. Handles abbreviations, city aliases, work-arrangement
prefixes ("hybrid in", "on-site in"), garbage values, and several well-known
ATS corruption patterns (trailing Workday req-ids, Greenhouse dict leaks,
trailing ZIP codes).

Extracted from the SearchSteward ingest pipeline. Location parsing originally
ported and adapted from Feashliaa/job-board-aggregator (MIT).

Pure functions, no I/O, standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


REMOTE_KEYWORDS = {
    "remote",
    "anywhere",
    "worldwide",
    "work from home",
    "wfh",
}

TIMEZONE_KEYWORDS = {
    "time zone",
    "timezone",
    "time zones",
    "timezones",
}

GARBAGE_LOCATIONS = {
    "",
    "not specified",
    "n/a",
    "none",
    "tbd",
    "unspecified",
    "multiple locations",
    "various",
    "flexible",
    "other",
    "global",
    "multiple",
    "varies",
    "various locations",
    "2 locations",
    "3 locations",
    "4 locations",
    "5 locations",
    "6 locations",
    "7 locations",
    "8 locations",
    "9 locations",
    "10 locations",
}

WORK_ARRANGEMENT_PREFIXES = [
    "hybrid in ",
    "hybrid - ",
    "hybrid: ",
    "hybrid, ",
    "on-site in ",
    "on site in ",
    "onsite in ",
    "in-office in ",
    "in office in ",
    "based in ",
    "located in ",
]

DIRECTION_EXPANSIONS = {
    " n ": " north ",
    " s ": " south ",
    " e ": " east ",
    " w ": " west ",
    " nw ": " northwest ",
    " ne ": " northeast ",
    " sw ": " southwest ",
    " se ": " southeast ",
}

ABBREVIATION_EXPANSIONS = {
    " ft ": " fort ",
    " mt ": " mount ",
    " pt ": " port ",
}

COUNTRY_ALIASES: dict[str, str] = {
    # North America
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united states": "US", "united states of america": "US", "america": "US",
    "can": "CA", "canada": "CA",
    "mx": "MX", "mex": "MX", "mexico": "MX",
    # UK / Ireland
    "gb": "GB", "gbr": "GB", "uk": "GB", "u.k.": "GB",
    "united kingdom": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "britain": "GB", "great britain": "GB",
    "ie": "IE", "irl": "IE", "ireland": "IE",
    # Europe
    "deu": "DE", "ger": "DE", "germany": "DE", "deutschland": "DE",
    "fr": "FR", "fra": "FR", "france": "FR",
    "es": "ES", "esp": "ES", "spain": "ES",
    "it": "IT", "ita": "IT", "italy": "IT",
    "nl": "NL", "nld": "NL", "netherlands": "NL", "holland": "NL",
    "be": "BE", "bel": "BE", "belgium": "BE",
    "ch": "CH", "che": "CH", "switzerland": "CH",
    "at": "AT", "aut": "AT", "austria": "AT",
    "se": "SE", "swe": "SE", "sweden": "SE",
    "no": "NO", "nor": "NO", "norway": "NO",
    "dk": "DK", "dnk": "DK", "denmark": "DK",
    "fi": "FI", "fin": "FI", "finland": "FI",
    "pl": "PL", "pol": "PL", "poland": "PL",
    "pt": "PT", "prt": "PT", "portugal": "PT",
    "cz": "CZ", "cze": "CZ", "czech republic": "CZ", "czechia": "CZ",
    "gr": "GR", "grc": "GR", "greece": "GR",
    "ro": "RO", "rou": "RO", "romania": "RO",
    "ua": "UA", "ukr": "UA", "ukraine": "UA",
    # Asia / Pacific
    "ind": "IN", "india": "IN",
    "cn": "CN", "chn": "CN", "china": "CN",
    "jp": "JP", "jpn": "JP", "japan": "JP",
    "kr": "KR", "kor": "KR", "korea": "KR", "south korea": "KR",
    "sg": "SG", "sgp": "SG", "singapore": "SG",
    "my": "MY", "mys": "MY", "malaysia": "MY",
    "ph": "PH", "phl": "PH", "philippines": "PH",
    "idn": "ID", "indonesia": "ID",
    "th": "TH", "tha": "TH", "thailand": "TH",
    "vn": "VN", "vnm": "VN", "vietnam": "VN",
    "hk": "HK", "hkg": "HK", "hong kong": "HK",
    "tw": "TW", "twn": "TW", "taiwan": "TW",
    "au": "AU", "aus": "AU", "australia": "AU",
    "nz": "NZ", "nzl": "NZ", "new zealand": "NZ",
    # Middle East / Africa
    "isr": "IL", "israel": "IL",
    "ae": "AE", "are": "AE", "united arab emirates": "AE", "uae": "AE",
    "sau": "SA", "saudi arabia": "SA",
    "tr": "TR", "tur": "TR", "turkey": "TR",
    "za": "ZA", "zaf": "ZA", "south africa": "ZA",
    "eg": "EG", "egy": "EG", "egypt": "EG",
    "ng": "NG", "nga": "NG", "nigeria": "NG",
    "ke": "KE", "ken": "KE", "kenya": "KE",
    # South America
    "br": "BR", "bra": "BR", "brazil": "BR", "brasil": "BR",
    "ar": "AR", "arg": "AR", "argentina": "AR",
    "cl": "CL", "chl": "CL", "chile": "CL",
    "col": "CO", "colombia": "CO",
    "pe": "PE", "per": "PE", "peru": "PE",
}

US_STATES: dict[str, str] = {
    "al": "AL", "alabama": "AL",
    "ak": "AK", "alaska": "AK",
    "az": "AZ", "arizona": "AZ",
    "ar": "AR", "arkansas": "AR",
    "ca": "CA", "california": "CA",
    "co": "CO", "colorado": "CO",
    "ct": "CT", "connecticut": "CT",
    "de": "DE", "delaware": "DE",
    "fl": "FL", "florida": "FL",
    "ga": "GA", "georgia": "GA",
    "hi": "HI", "hawaii": "HI",
    "id": "ID", "idaho": "ID",
    "il": "IL", "illinois": "IL",
    "in": "IN", "indiana": "IN",
    "ia": "IA", "iowa": "IA",
    "ks": "KS", "kansas": "KS",
    "ky": "KY", "kentucky": "KY",
    "la": "LA", "louisiana": "LA",
    "me": "ME", "maine": "ME",
    "md": "MD", "maryland": "MD",
    "ma": "MA", "massachusetts": "MA",
    "mi": "MI", "michigan": "MI",
    "mn": "MN", "minnesota": "MN",
    "ms": "MS", "mississippi": "MS",
    "mo": "MO", "missouri": "MO",
    "mt": "MT", "montana": "MT",
    "ne": "NE", "nebraska": "NE",
    "nv": "NV", "nevada": "NV",
    "nh": "NH", "new hampshire": "NH",
    "nj": "NJ", "new jersey": "NJ",
    "nm": "NM", "new mexico": "NM",
    "ny": "NY", "new york": "NY",
    "nc": "NC", "north carolina": "NC",
    "nd": "ND", "north dakota": "ND",
    "oh": "OH", "ohio": "OH",
    "ok": "OK", "oklahoma": "OK",
    "or": "OR", "oregon": "OR",
    "pa": "PA", "pennsylvania": "PA",
    "ri": "RI", "rhode island": "RI",
    "sc": "SC", "south carolina": "SC",
    "sd": "SD", "south dakota": "SD",
    "tn": "TN", "tennessee": "TN",
    "tx": "TX", "texas": "TX",
    "ut": "UT", "utah": "UT",
    "vt": "VT", "vermont": "VT",
    "va": "VA", "virginia": "VA",
    "wa": "WA", "washington": "WA",
    "wv": "WV", "west virginia": "WV",
    "wi": "WI", "wisconsin": "WI",
    "wy": "WY", "wyoming": "WY",
    "dc": "DC", "d c": "DC", "district of columbia": "DC",
    "washington dc": "DC", "washington d c": "DC",
}

# Build reverse mapping from country codes to canonical full names.
# Use a curated list of preferred names to avoid ambiguity and component regions.
_COUNTRY_CODE_TO_NAME: dict[str, str] = {
    "US": "United States",
    "CA": "Canada",
    "MX": "Mexico",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "BE": "Belgium",
    "CH": "Switzerland",
    "AT": "Austria",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "PL": "Poland",
    "PT": "Portugal",
    "CZ": "Czech Republic",
    "GR": "Greece",
    "RO": "Romania",
    "UA": "Ukraine",
    "IN": "India",
    "CN": "China",
    "JP": "Japan",
    "KR": "South Korea",
    "SG": "Singapore",
    "MY": "Malaysia",
    "PH": "Philippines",
    "ID": "Indonesia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "HK": "Hong Kong",
    "TW": "Taiwan",
    "AU": "Australia",
    "NZ": "New Zealand",
    "IL": "Israel",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "TR": "Turkey",
    "ZA": "South Africa",
    "EG": "Egypt",
    "NG": "Nigeria",
    "KE": "Kenya",
    "BR": "Brazil",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "PE": "Peru",
}

CITY_ALIASES: dict[str, str] = {
    # India
    "bangalore": "bengaluru",
    "bombay": "mumbai",
    "madras": "chennai",
    "calcutta": "kolkata",
    # China
    "peking": "beijing",
    # Common US disambiguations
    "new york": "new york city",
    "nyc": "new york city",
    "ny city": "new york city",
    "la": "los angeles",
    "sf": "san francisco",
    "san fran": "san francisco",
    # UK special
    "london city of": "london",
    "city of london": "london",
    # Metro areas
    "bay area": "san francisco",
    "sf bay area": "san francisco",
    "san francisco bay area": "san francisco",
    "greater boston": "boston",
    "boston metro": "boston",
    "nyc metro": "new york city",
    "ny metro": "new york city",
    "greater new york": "new york city",
    "new york metro": "new york city",
    "dc metro": "washington",
    "washington metro": "washington",
    "la metro": "los angeles",
    "greater los angeles": "los angeles",
    "greater chicago": "chicago",
    "chicago metro": "chicago",
    "greater seattle": "seattle",
    "seattle metro": "seattle",
    "greater london": "london",
    "london metro": "london",
}

NYC_BOROUGHS = {"bronx", "brooklyn", "queens", "staten island", "manhattan"}

JUNK_TOKEN_SUFFIXES = {
    "hq", "office", "headquarters", "hub", "campus",
    "location", "site", "center", "centre", "area",
    "township", "twp",
}

FAMOUS_CITY_DEFAULTS: dict[str, str] = {
    "london": "GB",
    "paris": "FR",
    "san francisco": "US",
    "moscow": "RU",
    "berlin": "DE",
    "madrid": "ES",
    "rome": "IT",
    "sydney": "AU",
    "toronto": "CA",
    "dublin": "IE",
    "athens": "GR",
    "vienna": "AT",
    "cairo": "EG",
    "boston": "US",
    "chicago": "US",
    "seattle": "US",
    "denver": "US",
    "portland": "US",
    "columbus": "US",
    "richmond": "US",
    "springfield": "US",
}


def normalize(s: Optional[str]) -> str:
    """Lowercase, strip diacritics/punctuation, expand abbreviations, collapse whitespace."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for ch in [".", "'", "`"]:
        s = s.replace(ch, "")
    s = " ".join(s.lower().split())

    if s.startswith("saint "):
        s = "st " + s[6:]
    s = s.replace(" saint ", " st ")

    padded = f" {s} "
    for abbrev, full in DIRECTION_EXPANSIONS.items():
        padded = padded.replace(abbrev, full)
    for abbrev, full in ABBREVIATION_EXPANSIONS.items():
        padded = padded.replace(abbrev, full)

    return padded.strip()


def is_remote(location_str: Optional[str]) -> bool:
    """Return True if the location string indicates a remote job."""
    if not location_str:
        return False
    s = normalize(location_str)
    if s in GARBAGE_LOCATIONS:
        return False
    return any(kw in s for kw in REMOTE_KEYWORDS)


def _clean_token(t: str) -> str:
    words = t.split()
    while words and words[-1] in JUNK_TOKEN_SUFFIXES:
        words.pop()
    return " ".join(words)


def _strip_work_arrangement(normalized: str) -> str:
    for prefix in WORK_ARRANGEMENT_PREFIXES:
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return normalized


def _extract_country(tokens: list[str]) -> tuple[Optional[str], list[str]]:
    if len(tokens) <= 1:
        return None, tokens
    for i in range(len(tokens) - 1, -1, -1):
        if tokens[i] in COUNTRY_ALIASES:
            return COUNTRY_ALIASES[tokens[i]], tokens[:i] + tokens[i + 1:]
    return None, tokens


def _extract_us_state(tokens: list[str]) -> tuple[Optional[str], list[str]]:
    if len(tokens) <= 1:
        return None, tokens
    for i in range(len(tokens) - 1, -1, -1):
        if tokens[i] in US_STATES:
            return US_STATES[tokens[i]], tokens[:i] + tokens[i + 1:]
    return None, tokens


def parse_job_location(location_str: Optional[str]) -> dict:
    """Parse a free-text location string into structured components.

    Returns dict with keys: remote (bool), city (str|None), admin (str|None), country (str|None).
    """
    result: dict = {"remote": False, "city": None, "admin": None, "country": None}

    if not location_str:
        return result

    normalized = normalize(location_str)

    if not normalized or normalized in GARBAGE_LOCATIONS:
        return result

    # Check for remote keywords, but don't return early — strip them and continue parsing.
    if any(kw in normalized for kw in REMOTE_KEYWORDS):
        result["remote"] = True
        # Strip remote keywords and their surrounding punctuation/separators.
        for kw in REMOTE_KEYWORDS:
            # Strip ", remote", ", anywhere", etc. as complete patterns
            normalized = re.sub(
                r",\s*" + re.escape(kw) + r"\b", "", normalized, flags=re.IGNORECASE
            )
            # Strip "remote - ", "anywhere - ", etc.
            normalized = re.sub(
                r"\b" + re.escape(kw) + r"\s*[-—/]\s*", "", normalized, flags=re.IGNORECASE
            )
            # Strip "- remote", "- anywhere", etc. (including "/" separator)
            normalized = re.sub(
                r"\s*[-—/]\s*" + re.escape(kw) + r"\b", "", normalized, flags=re.IGNORECASE
            )
            # Strip "[remote]", "[anywhere]", etc.
            normalized = re.sub(
                r"\s*\[\s*" + re.escape(kw) + r"\s*\]", "", normalized, flags=re.IGNORECASE
            )
            # Strip "remote, ", "anywhere, ", etc.
            normalized = re.sub(
                r"\b" + re.escape(kw) + r"\s*,\s*", "", normalized, flags=re.IGNORECASE
            )
            # Strip bare "remote", "anywhere" (standalone keyword)
            normalized = re.sub(
                r"^\s*" + re.escape(kw) + r"\s*$", "", normalized, flags=re.IGNORECASE
            )
        normalized = normalized.strip(" ,-—/[]")
        # If nothing left after stripping remote, return early with just remote=True.
        if not normalized:
            return result
        # Continue parsing with the remaining location string.

    if any(kw in normalized for kw in TIMEZONE_KEYWORDS):
        result["remote"] = True
        return result

    normalized = _strip_work_arrangement(normalized)
    normalized = re.sub(r"\s*\([^)]*\)\s*", " ", normalized).strip()
    for pattern in ["- remote", "— remote"]:
        normalized = normalized.replace(pattern, "")
    normalized = normalized.strip(" ,-—")

    # Drop a trailing US ZIP so it doesn't derail city/state tokenization.
    normalized = _TRAILING_US_ZIP.sub("", normalized).strip(" ,-—")

    if not normalized:
        return result

    tokens = [_clean_token(t.strip()) for t in normalized.split(",") if t.strip()]
    tokens = [t for t in tokens if t]

    if not tokens:
        return result

    # Dedupe consecutive identical tokens
    deduped: list[str] = []
    for t in tokens:
        if not deduped or deduped[-1] != t:
            deduped.append(t)
    tokens = deduped

    joined = " ".join(tokens)
    if joined in CITY_ALIASES:
        result["city"] = CITY_ALIASES[joined]
        return result

    # Handle "city state" space-separated patterns (e.g. "Seattle WA")
    if len(tokens) == 1:
        words = tokens[0].split()
        if len(words) >= 2 and words[-1] in US_STATES:
            tokens = [" ".join(words[:-1]), words[-1]]

    # Single-token country-only (e.g. "US", "France")
    if len(tokens) == 1 and tokens[0] in COUNTRY_ALIASES:
        result["country"] = COUNTRY_ALIASES[tokens[0]]
        return result

    country, tokens = _extract_country(tokens)
    result["country"] = country

    if country == "US" or country is None:
        state, tokens_after = _extract_us_state(tokens)
        if state:
            result["admin"] = state
            tokens = tokens_after
            if country is None:
                result["country"] = "US"

    if tokens:
        if result["admin"] is None and len(tokens) >= 2:
            result["admin"] = tokens[-1]
            result["city"] = " ".join(tokens[:-1])
        else:
            result["city"] = tokens[0]

    if result["city"] and result["city"] in CITY_ALIASES:
        result["city"] = CITY_ALIASES[result["city"]]

    # Default country from famous-city lookup when still unknown
    if result["country"] is None and result["city"] and result["city"] in FAMOUS_CITY_DEFAULTS:
        result["country"] = FAMOUS_CITY_DEFAULTS[result["city"]]

    return result


_TRAILING_WORKDAY_REQ_ID = re.compile(r"\s*/\s*R-[A-Za-z0-9-]+\s*$")

# A trailing US ZIP ("Seattle, WA 98101" / "…-1234") is a common ATS shape
# (Paycom, Paylocity). The 5-digit token otherwise shifts tokenization — e.g.
# "Washington, DC 20004" mis-parses to state=WA because "washington" wins the
# state slot while "dc 20004" is demoted to the city. City/state is what
# matching needs, not the ZIP, so drop it before parsing.
_TRAILING_US_ZIP = re.compile(r"[,\s]+\d{5}(?:-\d{4})?\s*$")

# "9 Locations" is Workday's multi-location placeholder — a COUNT, not a place.
# The true list is lost upstream, so the honest value is empty, not a number.
_LOCATION_COUNT_PLACEHOLDER = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)

# `.title()` corrupts place names: "US - San Francisco" becomes "Us - San Francisco",
# "NYC" becomes "Nyc", "9th Arrondissement" becomes "9Th Arrondissement".
#
# Deliberately conservative: only unambiguous acronyms. "CA" is excluded — it is California
# AND Canada. "LA", "SF", "NY" are excluded — they are also ordinary words or ambiguous in
# a city slot.
_LOCATION_ACRONYMS = {
    "US", "USA", "U.S.", "U.S.A.", "UK", "DC", "NYC", "EU", "UAE",
    "EMEA", "APAC", "LATAM", "DACH", "ANZ",
}

# Words that stay lowercase inside a name unless they lead it.
#
# `or`, `in`, `on`, `at` are deliberately ABSENT: they collide with the state/province codes
# OR (Oregon), IN (Indiana), ON (Ontario), AT (Austria). Lowercasing "Portland, OR" to
# "Portland, or" would corrupt the place — a lowercase "or" reads as a "city or remote"
# disjunction, not a state.
_LOCATION_MINOR_WORDS = {"of", "and", "the", "de", "du", "da", "van", "der"}

# "9th", "1st", "22nd" — `.title()` renders these "9Th", "1St", "22Nd".
_ORDINAL_TOKEN = re.compile(r"^\d+(st|nd|rd|th)$", re.IGNORECASE)


# Canadian province codes. Not represented anywhere else in this module, but "Toronto, ON"
# hits the same casing corruption as "Portland, OR".
_CA_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


def _upper_preserve() -> set[str]:
    """Codes that must survive as-is when the input already spells them in caps.

    "Portland, OR" must not become "Portland, Or". Only an ALREADY-uppercase token is
    preserved — a lowercase "or" carries no signal that it means Oregon.
    """
    return set(US_STATES.values()) | set(COUNTRY_ALIASES.values()) | _CA_PROVINCES


def _titlecase_token(token: str, *, is_first: bool) -> str:
    if not token:
        return token
    if token.upper() in _LOCATION_ACRONYMS:
        return token.upper()
    if token.isupper() and token in _upper_preserve():
        return token
    if _ORDINAL_TOKEN.match(token):
        return token.lower()
    if not is_first and token.lower() in _LOCATION_MINOR_WORDS:
        return token.lower()
    # Leave mixed-case tokens alone ("McLean", "DeKalb"); `.title()` would flatten them.
    if not token.isupper() and not token.islower():
        return token
    return token[:1].upper() + token[1:].lower()


def titlecase_place(text: str) -> str:
    """Title-case a place name without destroying acronyms, ordinals, or mixed case.

    `"us - san francisco"` -> `"US - San Francisco"`   (`.title()` gives `"Us - San Francisco"`)
    `"nyc or san francisco"` -> `"NYC or San Francisco"`
    `"9th arrondissement of paris"` -> `"9th Arrondissement of Paris"`
    `"McLean"` -> `"McLean"`
    """
    if not text:
        return text
    out: list[str] = []
    first = True
    # Split on whitespace and hyphens, keeping the separators.
    for piece in re.split(r"(\s+|-)", text):
        if not piece or piece.isspace() or piece == "-":
            out.append(piece)
            continue
        out.append(_titlecase_token(piece, is_first=first))
        first = False
    return "".join(out)


# Greenhouse leaks its API location object into the location string two ways.
# YAML-flow style ("{name: bangalore}") appears comma-prefixed. Python repr
# style ("{'name': 'San Francisco, CA'}") appears slash-separated and may
# stand alone before " / Remote". Both are stripped here before
# parse_job_location runs.
_GREENHOUSE_DICT_LEAK = re.compile(
    # Terminate on `}` OR end-of-string so malformed leaks without a close
    # brace ("{Name: New York, NY, US") are still stripped. Without the
    # alternation, the strip silently no-ops on those.
    r"\s*[,/]?\s*\{['\"]?name['\"]?\s*:[^}]*(?:\}|$)",
    re.IGNORECASE,
)


def sanitize_location_for_display(location_str: Optional[str]) -> Optional[str]:
    """Strip req-id / dict-leak corruption from a location for display, without
    re-parsing or blanking a currently-good value (read-side, conservative).

    Does NOT normalize or parse — just strips known corruptions in-place.
    Returns None if input is None, empty string if input is empty.
    """
    if not location_str:
        return location_str
    loc = _TRAILING_WORKDAY_REQ_ID.sub("", location_str)          # trailing "/ R-123"
    loc = _GREENHOUSE_DICT_LEAK.sub("", loc)                      # "{name: ...}"
    loc = re.sub(r"\s*/\s*R-[A-Za-z0-9-]+\s*(?=/|\s|$)", "", loc) # mid-string "/ R-011756"
    loc = re.sub(r"\s+/\s+/\s+", " / ", loc)                      # collapse "/ /"
    loc = re.sub(r"\s*/\s*$", "", loc)                            # trailing "/"
    return loc.strip()


def normalize_location_text(location_str: Optional[str]) -> str:
    """Return a clean, canonical single-line location string.

    Strips work-arrangement prefixes, garbage values, and parentheticals.
    Returns empty string for garbage/unresolvable inputs.

    For remote jobs:
    - remote + US location → bare "Remote"
    - remote + non-US location → "<Place>, Remote"
    """
    if not location_str:
        return ""
    if _LOCATION_COUNT_PLACEHOLDER.match(location_str):
        # "9 Locations" is a count, not a place. The real list is lost upstream.
        return ""
    # Strip two upstream-adapter corruptions before parsing.
    # Workday's API embeds the req_id into locationsText as "City, ST / R-12345".
    # Greenhouse occasionally leaks a YAML-flow-style dict literal alongside
    # the resolved name ("Bengaluru, {name: bangalore}"). Both confound
    # (company, role, location) dedup keys and pollute display.
    location_str = _TRAILING_WORKDAY_REQ_ID.sub("", location_str)
    location_str = _GREENHOUSE_DICT_LEAK.sub("", location_str)
    parsed = parse_job_location(location_str)

    if parsed["remote"]:
        city = titlecase_place(parsed["city"]) if parsed["city"] else None
        country_code = parsed["country"]
        admin = parsed["admin"]

        # Check if location is US: country=="US" or admin is a US state.
        us_state_abbrevs = set(US_STATES.values())
        is_us_location = country_code == "US" or (admin and admin.upper() in us_state_abbrevs)

        if is_us_location:
            # US remote → bare "Remote"
            return "Remote"
        else:
            # Non-US remote → preserve location + ", Remote"
            # Convert country code back to full name for readability
            country = _COUNTRY_CODE_TO_NAME.get(country_code, country_code) if country_code else None
            parts = [p for p in [city, admin, country] if p]
            if parts:
                return ", ".join(parts) + ", Remote"
            else:
                # Fallback: no geographic info, just bare Remote
                return "Remote"

    city = titlecase_place(parsed["city"]) if parsed["city"] else None
    parts = [p for p in [city, parsed["admin"], parsed["country"]] if p]
    if parts:
        return ", ".join(parts)
    return ""


def is_us_or_remote(location_str: Optional[str]) -> bool:
    """True when the location denotes a US role or a bare (country-less) remote role.

    Semantics:
    - True for US locations ("Seattle, WA", "United States", "Cupertino, California"),
      US-remote combinations ("Remote, US", "California, Remote"), and bare "Remote"
      with no other geography (US-remote by convention — adjust downstream if your
      product's default differs).
    - False for explicit international locations ("London, UK", "Mexico, Remote",
      "Remote - Philippines"), region-qualified remote ("EMEA [Remote]"), and
      empty/garbage strings.
    """
    parsed = parse_job_location(location_str)
    if parsed["country"] == "US":
        return True
    if parsed["country"] is not None:
        return False
    # A US state name standing alone ("California", "California, Remote") parses as a
    # city token, not an admin — recognize it before falling through.
    if parsed["admin"] in set(US_STATES.values()):
        return True
    if parsed["city"] and parsed["city"] in US_STATES:
        return True
    if parsed["remote"]:
        # Bare remote with no residual geography defaults to US-remote. A residual
        # city/region ("EMEA", "Latin America") means the remote role is scoped there.
        return parsed["city"] is None and parsed["admin"] is None
    return False
