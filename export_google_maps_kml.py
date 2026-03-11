from __future__ import annotations

import argparse
import csv
import html
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def safe(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def build_description(row: dict[str, str]) -> str:
    lines = [
        f"<b>PDF Name:</b> {safe(row.get('pdf_company_name', ''))}",
        f"<b>Matched Entity:</b> {safe(row.get('entity_name', ''))}",
        f"<b>Sector:</b> {safe(row.get('sector', 'Unknown'))}",
        f"<b>Subsector:</b> {safe(row.get('subsector', 'Unknown'))}",
        f"<b>Address:</b> {safe(row.get('address', ''))}",
        f"<b>Postcode:</b> {safe(row.get('postcode', ''))}",
    ]
    return "<br/>".join(lines)


def placemark_xml(row: dict[str, str]) -> str:
    name = safe(row.get("pdf_company_name", ""))
    desc = build_description(row)
    lon = row.get("lon", "")
    lat = row.get("lat", "")
    return (
        "    <Placemark>\n"
        f"      <name>{name}</name>\n"
        f"      <description><![CDATA[{desc}]]></description>\n"
        "      <Point>\n"
        f"        <coordinates>{lon},{lat},0</coordinates>\n"
        "      </Point>\n"
        "    </Placemark>\n"
    )


def read_rows(csv_path: Path) -> Iterable[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lat = (row.get("lat") or "").strip()
            lon = (row.get("lon") or "").strip()
            if not lat or not lon:
                continue
            try:
                float(lat)
                float(lon)
            except ValueError:
                continue
            yield row


def write_kml(csv_path: Path, kml_path: Path) -> int:
    grouped = defaultdict(lambda: defaultdict(list))
    count = 0
    for row in read_rows(csv_path):
        sector = (row.get("sector") or "").strip() or "Unknown"
        subsector = (row.get("subsector") or "").strip() or "Unknown"
        grouped[sector][subsector].append(row)
        count += 1

    with kml_path.open("w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n')
        out.write("  <Document>\n")
        out.write(f"    <name>{safe(kml_path.stem)}</name>\n")
        out.write("    <Style id=\"employerPoint\">\n")
        out.write("      <IconStyle><scale>0.8</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href></Icon></IconStyle>\n")
        out.write("    </Style>\n")

        for sector in sorted(grouped):
            out.write("    <Folder>\n")
            out.write(f"      <name>{safe(sector)}</name>\n")
            for subsector in sorted(grouped[sector]):
                out.write("      <Folder>\n")
                out.write(f"        <name>{safe(subsector)}</name>\n")
                for row in grouped[sector][subsector]:
                    out.write(placemark_xml(row))
                out.write("      </Folder>\n")
            out.write("    </Folder>\n")

        out.write("  </Document>\n")
        out.write("</kml>\n")
    return count


def write_kmz(kml_path: Path, kmz_path: Path) -> None:
    with zipfile.ZipFile(kmz_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(kml_path, arcname="doc.kml")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export accredited employer points to KML/KMZ for Google Maps")
    parser.add_argument("--in-csv", default="matched_accredited_employers_with_coords.csv")
    parser.add_argument("--out-kml", default="accredited_employers_map_points.kml")
    parser.add_argument("--out-kmz", default="accredited_employers_map_points.kmz")
    args = parser.parse_args()

    csv_path = Path(args.in_csv)
    kml_path = Path(args.out_kml)
    kmz_path = Path(args.out_kmz)

    count = write_kml(csv_path, kml_path)
    write_kmz(kml_path, kmz_path)

    print(f"Wrote KML: {kml_path} ({count} placemarks)")
    print(f"Wrote KMZ: {kmz_path}")


if __name__ == "__main__":
    main()
