from __future__ import annotations

import math
import unittest

from src.common.json_codec import StrictJSONError, dumps_json, loads_json


class StrictJsonCodecTest(unittest.TestCase):
    def test_rejects_non_standard_and_overflow_numbers(self) -> None:
        for text in ("NaN", "Infinity", "-Infinity", "1e1000000"):
            with self.subTest(text=text):
                with self.assertRaises(StrictJSONError):
                    loads_json(text)

    def test_rejects_duplicate_object_keys(self) -> None:
        with self.assertRaisesRegex(StrictJSONError, "Duplicate JSON object key"):
            loads_json('{"product_name": "first", "product_name": "second"}')

    def test_rejects_unpaired_unicode_surrogates(self) -> None:
        with self.assertRaisesRegex(StrictJSONError, "Unicode"):
            loads_json('"\\ud800"')

    def test_dumper_rejects_non_finite_numbers(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(StrictJSONError):
                    dumps_json({"value": value})

    def test_round_trip_preserves_standard_json_values(self) -> None:
        payload = {"name": "Hospital", "amount": 12.5, "active": True}

        self.assertEqual(loads_json(dumps_json(payload)), payload)


if __name__ == "__main__":
    unittest.main()
