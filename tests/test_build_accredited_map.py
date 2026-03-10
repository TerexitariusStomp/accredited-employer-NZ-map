import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build_accredited_map import (
    extract_name_from_pdf_line,
    load_address_rows,
    normalize_name,
)


class ParsingTests(unittest.TestCase):
    def test_normalize_name_collapses_case_and_punctuation(self):
        self.assertEqual(normalize_name("  Foo & Bar, Ltd.  "), "FOO BAR LTD")

    def test_extract_name_from_pdf_line_with_sector_split(self):
        line = "Truco Limited Transport, Postal and Warehousing Road Transport"
        self.assertEqual(extract_name_from_pdf_line(line), "Truco Limited")

    def test_load_address_rows_uses_mapped_columns(self):
        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "sample.csv"
            csv_path.write_text(
                "ENTITY_NAME,START_DATE,ADDR1,ADDR2,ADDR3,ADDR4,POSTCODE,COUNTRY\n"
                "EXAMPLE CO,01/01/2020,1 Main St,,Auckland,,1010,NEW ZEALAND\n",
                encoding="utf-8",
            )
            rows = load_address_rows(
                csv_path,
                "test_source",
                {
                    "entity_name": "ENTITY_NAME",
                    "start_date": "START_DATE",
                    "a1": "ADDR1",
                    "a2": "ADDR2",
                    "a3": "ADDR3",
                    "a4": "ADDR4",
                    "postcode": "POSTCODE",
                    "country": "COUNTRY",
                },
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].entity_name, "EXAMPLE CO")
            self.assertEqual(rows[0].postcode, "1010")


if __name__ == "__main__":
    unittest.main()
