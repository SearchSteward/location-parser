"""Tests for job-posting location parsing and normalization.

Covers:
- ATS corruption patterns (Workday trailing req-ids, Greenhouse dict leaks,
  trailing ZIP codes, "N Locations" placeholders)
- Acronym/ordinal/mixed-case-safe title-casing
- Remote + foreign-location preservation ("Mexico, Remote" must not collapse
  to a bare US-eligible "Remote")
- is_us_or_remote() classification
"""

from __future__ import annotations

import unittest

from location_parser import (
    is_us_or_remote,
    normalize_location_text,
    parse_job_location,
    titlecase_place,
)


class TestWorkdayReqIdSuffixStripped(unittest.TestCase):
    """Strip ' / R-...' trailing req_id from Workday locationsText.

    Workday's API embeds the requisition id into the location string
    ("Gretna, Louisiana / R-26-0036719"). Asserts the corruption is gone and
    the city survives; exact output format is the parser's business.
    """

    def _assert_clean(self, raw: str, expected_city: str) -> None:
        out = normalize_location_text(raw)
        self.assertNotRegex(out, r"R-[0-9]", f"req_id leaked into output: {out!r}")
        self.assertNotIn("/", out, f"slash separator leaked: {out!r}")
        self.assertIn(expected_city, out, f"city missing from output: {out!r}")

    def test_us_city_state_with_req_id(self):
        self._assert_clean("Gretna, Louisiana / R-26-0036719", "Gretna")

    def test_state_abbrev_with_req_id(self):
        self._assert_clean("CT - Hartford / R-50499", "Hartford")

    def test_international_with_req_id(self):
        self._assert_clean("Warsaw, Masovian, Poland / R-021140", "Warsaw")

    def test_short_numeric_req_id(self):
        self._assert_clean("Tokyo / R-5438", "Tokyo")

    def test_alphanumeric_req_id(self):
        self._assert_clean("Manila, Philippines / R-107149", "Manila")

    def test_multilocation_placeholder_with_req_id(self):
        # "6 Locations / R-049718" — this test only guarantees the req_id
        # suffix gets stripped before the placeholder is parsed.
        out = normalize_location_text("6 Locations / R-049718")
        self.assertNotRegex(out, r"R-[0-9]")


class TestGreenhouseDictLeakStripped(unittest.TestCase):
    """Strip ', {name: ...}' YAML-flow-style dict literal from Greenhouse."""

    def _assert_clean(self, raw: str, expected_city: str) -> None:
        out = normalize_location_text(raw)
        self.assertNotIn("{name:", out, f"dict leak in output: {out!r}")
        self.assertNotIn("{", out, f"brace leaked: {out!r}")
        self.assertIn(expected_city, out, f"city missing from output: {out!r}")

    def test_simple_dict_leak(self):
        self._assert_clean("Bengaluru, {name: bangalore}", "Bengaluru")

    def test_hyphenated_city(self):
        self._assert_clean(
            "Boulogne-Billancourt, {name: boulogne-billancourt}",
            "Boulogne-Billancourt",
        )

    def test_dict_leak_with_trailing_country(self):
        self._assert_clean("London, {name: london}, GB", "London")

    def test_dict_leak_with_whitespace_padding(self):
        self._assert_clean(
            "Palo Alto Or Bellevue, {name: palo alto or bellevue }",
            "Palo Alto Or Bellevue",
        )

    def test_dict_leak_alone(self):
        # Pathological: only the leak, no real city. Strip leaves nothing.
        out = normalize_location_text("{name: bangalore}")
        self.assertNotIn("{", out)


class TestGreenhousePythonReprDictLeakStripped(unittest.TestCase):
    """Greenhouse has a second leak path that uses Python's str(dict) output:
    '<city> / {\\'name\\': \\'<city>\\'}'. The same regex strips both forms."""

    def _assert_clean(self, raw: str) -> None:
        out = normalize_location_text(raw)
        self.assertNotIn("{'name", out, f"python-repr leak in output: {out!r}")
        self.assertNotIn("{name", out, f"yaml leak in output: {out!r}")

    def test_trailing_repr_duplicate(self):
        self._assert_clean("San Francisco, CA / {'name': 'San Francisco, CA'}")

    def test_trailing_repr_with_remote_suffix(self):
        self._assert_clean("United States / {'name': 'United States'} / Remote")
        # The Remote keyword should still resolve through the parser.
        self.assertEqual(
            normalize_location_text("United States / {'name': 'United States'} / Remote"),
            "Remote",
        )

    def test_empty_dict_then_remote(self):
        # "{'name': ' '} / Remote" — the leading empty dict gets stripped,
        # leaving " / Remote" which parses as Remote.
        self.assertEqual(
            normalize_location_text("{'name': ' '} / Remote"),
            "Remote",
        )

    def test_dash_prefix_with_repr(self):
        # "- US / {'name': ' - US'} / Remote" — the strip removes the
        # repr, leaving "- US / Remote" which parses as Remote.
        self.assertEqual(
            normalize_location_text("- US / {'name': ' - US'} / Remote"),
            "Remote",
        )

    def test_third_form_capital_n_no_separator(self):
        # "London {Name: London, united kingdom}, GB" — whitespace-only
        # separator and capital N. The regex is case-insensitive and
        # tolerates leading whitespace alone.
        out = normalize_location_text("London {Name: London, united kingdom}, GB")
        self.assertNotIn("{", out)
        self.assertIn("London", out)

    def test_unclosed_brace_form(self):
        # "{Name: New York, NY, US" — a malformed leak with no closing brace.
        # The strip terminates on `}` OR end-of-string so these are consumed.
        out = normalize_location_text("{Name: New York, NY, US")
        self.assertNotIn("{", out)

    def test_unclosed_brace_with_clean_prefix(self):
        out = normalize_location_text("Phoenix, AZ {Name: phoenix, az")
        self.assertNotIn("{", out)
        self.assertIn("Phoenix", out)


class TestCleanLocationsUntouched(unittest.TestCase):
    """Guard against the corruption regexes accidentally mangling clean input."""

    def test_clean_us_location(self):
        out = normalize_location_text("San Francisco, CA")
        self.assertIn("San Francisco", out)

    def test_remote_passthrough(self):
        self.assertEqual(normalize_location_text("Remote"), "Remote")

    def test_slash_in_middle_left_alone(self):
        # The req-id strip is anchored to end-of-string — mid-string slashes
        # with an R-token stay.
        out = normalize_location_text("New York / R-123, NY")
        self.assertIn("R-123", out)


class TestTitlecasePlaceAcronyms(unittest.TestCase):
    """`.title()` corrupts place names (Us / Nyc / Usa / Uk / Dc, 9Th)."""

    def test_unambiguous_acronyms_upper(self):
        self.assertEqual(titlecase_place("us - san francisco"), "US - San Francisco")
        self.assertEqual(titlecase_place("nyc"), "NYC")
        self.assertEqual(titlecase_place("uk"), "UK")
        self.assertEqual(titlecase_place("dc"), "DC")

    def test_ordinals_not_capitalized(self):
        self.assertEqual(
            titlecase_place("9th arrondissement of paris"), "9th Arrondissement of Paris"
        )

    def test_uppercase_state_codes_survive(self):
        """"Portland, OR" must not become "Portland, Or" — OR is Oregon, not a disjunction."""
        self.assertEqual(titlecase_place("Portland, OR"), "Portland, OR")
        self.assertEqual(titlecase_place("Indianapolis, IN"), "Indianapolis, IN")
        self.assertEqual(titlecase_place("Toronto, ON"), "Toronto, ON")

    def test_mixed_case_preserved(self):
        self.assertEqual(titlecase_place("McLean"), "McLean")

    def test_disjunction_capital_or_preserved(self):
        self.assertEqual(titlecase_place("Palo Alto Or Bellevue"), "Palo Alto Or Bellevue")


class TestTrailingZipStripped(unittest.TestCase):
    """Strip a trailing US ZIP so it doesn't derail city/state tokenization.

    Several ATSes (Paycom, Paylocity) emit "City, ST 12345". The ZIP token
    otherwise shifts the parse — "Washington, DC 20004" mis-resolves to
    state=WA ("washington" wins the state slot, "dc 20004" gets demoted to
    the city).
    """

    def test_washington_dc_zip_not_read_as_wa(self):
        out = normalize_location_text("Washington, DC 20004")
        self.assertEqual(out, "Washington, DC, US")
        self.assertNotIn("WA", out)

    def test_city_state_zip_resolves_clean(self):
        self.assertEqual(normalize_location_text("Buena Park, CA 90620"), "Buena Park, CA, US")

    def test_zip_plus_four(self):
        self.assertEqual(normalize_location_text("Seattle, WA 98101-1234"), "Seattle, WA, US")

    def test_hybrid_prefix_with_zip(self):
        # "Hybrid - City, ST ZIP": arrangement prefix AND zip both drop.
        out = normalize_location_text("Hybrid - Washington, DC 20004")
        self.assertEqual(out, "Washington, DC, US")

    def test_zipless_location_unaffected(self):
        # Guard: a clean location without a ZIP is unchanged by the strip.
        self.assertEqual(normalize_location_text("Austin, TX"), "Austin, TX, US")


class TestLocationCountPlaceholder(unittest.TestCase):
    """"9 Locations" is a count, not a place."""

    def test_count_strings_become_empty(self):
        for raw in ("9 Locations", "12 Locations", "1 Location", "  3 locations  "):
            self.assertEqual(normalize_location_text(raw), "", raw)

    def test_real_places_unaffected(self):
        self.assertNotEqual(normalize_location_text("New York, NY"), "")


class TestRemoteForeignLocationPreservation(unittest.TestCase):
    """Foreign locations are preserved when combined with remote keywords.

    A parser that early-returns on remote keywords loses the geographic
    qualifier: "Mexico, Remote" → bare "Remote" → US-eligible by mistake.

    Transformation table:
    - "Mexico, Remote" → "Mexico, Remote" (not US-eligible)
    - "Latin America [Remote]" → "Latin America, Remote" (not US-eligible)
    - "Remote - Philippines" → "Philippines, Remote" (not US-eligible)
    - "EMEA [Remote]" → "EMEA, Remote" (not US-eligible)
    - "Remote, US" → "Remote" (US-eligible)
    - "US, Remote" → "Remote" (US-eligible)
    - "Remote - United States" → "Remote" (US-eligible)
    - "Remote" → "Remote" (US-eligible)
    """

    def test_mexico_remote(self):
        result = normalize_location_text("Mexico, Remote")
        self.assertFalse(
            is_us_or_remote(result),
            f"Mexico, Remote should be foreign, got {result!r}",
        )
        self.assertIn(
            "mexico",
            result.lower(),
            f"Mexico should be preserved in output, got {result!r}",
        )

    def test_latin_america_remote(self):
        result = normalize_location_text("Latin America [Remote]")
        self.assertFalse(
            is_us_or_remote(result),
            f"Latin America, Remote should be foreign, got {result!r}",
        )
        self.assertIn(
            "latin america",
            result.lower(),
            f"Latin America should be preserved, got {result!r}",
        )

    def test_remote_philippines(self):
        result = normalize_location_text("Remote - Philippines")
        self.assertFalse(
            is_us_or_remote(result),
            f"Remote - Philippines should be foreign, got {result!r}",
        )
        self.assertIn(
            "philippines",
            result.lower(),
            f"Philippines should be preserved, got {result!r}",
        )

    def test_emea_remote(self):
        result = normalize_location_text("EMEA [Remote]")
        self.assertFalse(
            is_us_or_remote(result),
            f"EMEA, Remote should be foreign, got {result!r}",
        )
        self.assertIn(
            "emea",
            result.lower(),
            f"EMEA should be preserved, got {result!r}",
        )

    def test_remote_us(self):
        result = normalize_location_text("Remote, US")
        self.assertEqual(result, "Remote")
        self.assertTrue(is_us_or_remote(result))

    def test_us_remote(self):
        result = normalize_location_text("US, Remote")
        self.assertEqual(result, "Remote")
        self.assertTrue(is_us_or_remote(result))

    def test_remote_dash_united_states(self):
        result = normalize_location_text("Remote - United States")
        self.assertEqual(result, "Remote")
        self.assertTrue(is_us_or_remote(result))

    def test_bare_remote(self):
        result = normalize_location_text("Remote")
        self.assertEqual(result, "Remote")
        self.assertTrue(is_us_or_remote(result))

    def test_non_remote_locations_unchanged(self):
        result = normalize_location_text("Mexico City, Mexico")
        self.assertFalse(
            is_us_or_remote(result),
            f"Mexico City, Mexico should be foreign, got {result!r}",
        )
        self.assertIn("mexico", result.lower())

    def test_london_uk_unchanged(self):
        result = normalize_location_text("London, UK")
        self.assertFalse(
            is_us_or_remote(result),
            f"London, UK should be foreign, got {result!r}",
        )
        self.assertIn("london", result.lower())


class TestParseJobLocationWithRemoteForeign(unittest.TestCase):
    """parse_job_location extracts both the remote flag and geographic info."""

    def test_mexico_remote_parsed_components(self):
        result = parse_job_location("Mexico, Remote")
        self.assertTrue(result["remote"], f"remote flag should be True, got {result}")
        self.assertEqual(result["country"], "MX", f"country should be MX, got {result}")

    def test_latin_america_remote_parsed_components(self):
        result = parse_job_location("Latin America [Remote]")
        self.assertTrue(result["remote"], f"remote flag should be True, got {result}")

    def test_philippines_remote_parsed_components(self):
        result = parse_job_location("Remote - Philippines")
        self.assertTrue(result["remote"], f"remote flag should be True, got {result}")
        self.assertEqual(result["country"], "PH", f"country should be PH, got {result}")


class TestRemoteForeignEdgeCases(unittest.TestCase):
    """Edge cases and variant formats for remote + foreign."""

    def test_remote_parentheses_mexico(self):
        result = normalize_location_text("Mexico (Remote)")
        self.assertFalse(
            is_us_or_remote(result),
            f"Mexico (Remote) should be foreign, got {result!r}",
        )

    def test_remote_multiple_countries(self):
        result = normalize_location_text("Remote - Canada, Mexico")
        self.assertFalse(
            is_us_or_remote(result),
            f"Remote with Canada/Mexico should be foreign, got {result!r}",
        )

    def test_us_state_remote(self):
        result = normalize_location_text("California, Remote")
        self.assertTrue(
            is_us_or_remote(result),
            f"California, Remote should be US-eligible, got {result!r}",
        )

    def test_us_city_remote(self):
        result = normalize_location_text("San Francisco, Remote")
        self.assertTrue(
            is_us_or_remote(result),
            f"San Francisco, Remote should be US-eligible, got {result!r}",
        )


if __name__ == "__main__":
    unittest.main()
