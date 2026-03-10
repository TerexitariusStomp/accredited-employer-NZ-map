from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from pypdf import PdfReader

SECTORS: Tuple[str, ...] = (
    "Administrative and Support Services",
    "Accommodation and Food Services",
    "Agriculture, Forestry and Fishing",
    "Arts and Recreation Services",
    "Construction",
    "Education and Training",
    "Electricity, Gas, Water and Waste Services",
    "Financial and Insurance Services",
    "Health Care and Social Assistance",
    "Information Media and Telecommunications",
    "Manufacturing",
    "Mining",
    "Other Services",
    "Professional, Scientific and Technical Services",
    "Public Administration and Safety",
    "Rental, Hiring and Real Estate Services",
    "Retail Trade",
    "Transport, Postal and Warehousing",
    "Wholesale Trade",
)

HEADER_PREFIXES = (
    "appendix",
    "list of accredited employers",
    "released under",
    "companyname sector",
)


@dataclass
class AddressRow:
    entity_name: str
    normalized_name: str
    start_date: pd.Timestamp
    address: str
    postcode: str
    source: str


def normalize_name(value: str) -> str:
    s = value.upper().replace("&", " ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_name_from_pdf_line(line: str) -> Optional[str]:
    raw = re.sub(r"\s+", " ", line).strip()
    if not raw:
        return None

    lower = raw.lower()
    if any(lower.startswith(prefix) for prefix in HEADER_PREFIXES):
        return None

    for sector in sorted(SECTORS, key=len, reverse=True):
        idx = raw.find(sector)
        if idx > 0:
            name = raw[:idx].strip(" -:\t")
            if len(name) >= 2 and any(ch.isalpha() for ch in name):
                return name
    return None


def extract_accredited_names(pdf_path: Path) -> List[str]:
    reader = PdfReader(str(pdf_path))
    names: List[str] = []
    seen: set[str] = set()

    for page in reader.pages:
        text = page.extract_text() or ""
        for line in text.splitlines():
            name = extract_name_from_pdf_line(line)
            if not name:
                continue
            norm = normalize_name(name)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            names.append(name)

    return names


def parse_date(value: object) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.min
    text = str(value).strip()
    if not text:
        return pd.Timestamp.min
    parsed = pd.to_datetime(text, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return pd.Timestamp.min
    return parsed


def build_address(addr_parts: Sequence[object]) -> str:
    clean = [str(p).strip() for p in addr_parts if str(p).strip() and str(p).strip().lower() != "nan"]
    return ", ".join(clean)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def load_address_rows(csv_path: Path, source: str, col_map: Dict[str, str]) -> List[AddressRow]:
    df = pd.read_csv(csv_path, dtype=str, usecols=list(col_map.values()))
    rows: List[AddressRow] = []

    for _, r in df.iterrows():
        entity = (r.get(col_map["entity_name"]) or "").strip()
        if not entity:
            continue

        address = build_address(
            [
                r.get(col_map["a1"], ""),
                r.get(col_map["a2"], ""),
                r.get(col_map["a3"], ""),
                r.get(col_map["a4"], ""),
                r.get(col_map["postcode"], ""),
                r.get(col_map["country"], ""),
            ]
        )
        if not address:
            continue

        rows.append(
            AddressRow(
                entity_name=entity,
                normalized_name=normalize_name(entity),
                start_date=parse_date(r.get(col_map["start_date"], "")),
                address=address,
                postcode=clean_text(r.get(col_map["postcode"], "")),
                source=source,
            )
        )

    return rows


def update_best_from_csv(
    csv_path: Path,
    source: str,
    col_map: Dict[str, str],
    target_normalized_names: set[str],
    best_by_name: Dict[str, AddressRow],
) -> None:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            entity = clean_text(r.get(col_map["entity_name"], ""))
            if not entity:
                continue
            norm = normalize_name(entity)
            if norm not in target_normalized_names:
                continue

            address = build_address(
                [
                    r.get(col_map["a1"], ""),
                    r.get(col_map["a2"], ""),
                    r.get(col_map["a3"], ""),
                    r.get(col_map["a4"], ""),
                    r.get(col_map["postcode"], ""),
                    r.get(col_map["country"], ""),
                ]
            )
            if not address:
                continue

            row = AddressRow(
                entity_name=entity,
                normalized_name=norm,
                start_date=parse_date(r.get(col_map["start_date"], "")),
                address=address,
                postcode=clean_text(r.get(col_map["postcode"], "")),
                source=source,
            )
            existing = best_by_name.get(norm)
            if existing is None or row.start_date > existing.start_date:
                best_by_name[norm] = row


def build_best_address_index(all_rows: Iterable[AddressRow]) -> Dict[str, AddressRow]:
    best: Dict[str, AddressRow] = {}
    for row in all_rows:
        existing = best.get(row.normalized_name)
        if existing is None or row.start_date > existing.start_date:
            best[row.normalized_name] = row
    return best


def geocode_addresses(addresses: Sequence[str], cache_path: Path, pause_seconds: float = 1.0) -> Dict[str, Tuple[float, float]]:
    try:
        from geopy.geocoders import ArcGIS, Nominatim
    except ImportError as exc:
        raise RuntimeError("Missing geopy. Install with: pip install geopy") from exc

    cache: Dict[str, List[float]] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    arcgis = ArcGIS(timeout=15)
    nominatim = Nominatim(user_agent="accredited-employer-map", timeout=15)

    result: Dict[str, Tuple[float, float]] = {}
    total = len(addresses)

    for i, addr in enumerate(addresses, start=1):
        if addr in cache:
            lat, lon = cache[addr]
            result[addr] = (lat, lon)
            continue

        loc = None
        try:
            loc = arcgis.geocode(addr)
        except Exception:
            loc = None

        if not loc:
            try:
                loc = nominatim.geocode(addr)
            except Exception:
                loc = None

        if loc:
            result[addr] = (float(loc.latitude), float(loc.longitude))
            cache[addr] = [float(loc.latitude), float(loc.longitude)]
            print(f"[{i}/{total}] Geocoded: {addr}")
        else:
            print(f"[{i}/{total}] No match: {addr}")

        time.sleep(pause_seconds)

    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return result


def normalize_postcode(postcode: object) -> str:
    digits = re.sub(r"[^0-9]", "", clean_text(postcode))
    if not digits:
        return ""
    return digits.zfill(4)


def postcode_coords_map(postcodes: Sequence[object]) -> Dict[str, Tuple[float, float]]:
    try:
        import pgeocode
    except ImportError:
        return {}

    unique = sorted({normalize_postcode(p) for p in postcodes if normalize_postcode(p)})
    if not unique:
        return {}

    nomi = pgeocode.Nominatim("nz")
    lookup: Dict[str, Tuple[float, float]] = {}
    for pc in unique:
        row = nomi.query_postal_code(pc)
        lat = row.get("latitude")
        lon = row.get("longitude")
        if pd.isna(lat) or pd.isna(lon):
            continue
        lookup[pc] = (float(lat), float(lon))
    return lookup


def build_map(df: pd.DataFrame, out_html: Path) -> None:
    try:
        import folium
        from folium.plugins import MarkerCluster
    except ImportError as exc:
        raise RuntimeError("Missing folium. Install with: pip install folium") from exc

    center_lat = float(df["lat"].mean())
    center_lon = float(df["lon"].mean())
    m = folium.Map(location=[center_lat, center_lon], zoom_start=6, tiles="OpenStreetMap")
    cluster = MarkerCluster().add_to(m)

    for _, row in df.iterrows():
        popup = (
            f"<b>{row['pdf_company_name']}</b><br>"
            f"Matched Entity: {row['entity_name']}<br>"
            f"Address: {row['address']}<br>"
            f"Source: {row['address_source']}"
        )
        folium.Marker([row["lat"], row["lon"]], popup=popup).add_to(cluster)

    m.save(str(out_html))


def run(
    pdf_path: Path,
    public_csv: Path,
    service_csv: Path,
    office_csv: Path,
    out_csv: Path,
    out_html: Path,
    cache_path: Path,
    geocode_unresolved: bool,
) -> None:
    accredited_names = extract_accredited_names(pdf_path)
    print(f"Extracted accredited names: {len(accredited_names)}")
    target_norms = {normalize_name(n) for n in accredited_names}
    best_by_name: Dict[str, AddressRow] = {}

    update_best_from_csv(
        public_csv,
        "public_address",
        {
            "entity_name": "ENTITY_NAME",
            "start_date": "START_DATE",
            "a1": "ADDRESS_1",
            "a2": "ADDRESS_2",
            "a3": "ADDRESS_3",
            "a4": "ADDRESS_4",
            "postcode": "ADDRESS_POSTCODE",
            "country": "ADDRESS_COUNTRY",
        },
        target_norms,
        best_by_name,
    )
    update_best_from_csv(
        service_csv,
        "address_for_service",
        {
            "entity_name": "ENTITY_NAME",
            "start_date": "START_DATE",
            "a1": "ADDRESS_FOR_SERVICE_1",
            "a2": "ADDRESS_FOR_SERVICE_2",
            "a3": "ADDRESS_FOR_SERVICE_3",
            "a4": "ADDRESS_FOR_SERVICE_4",
            "postcode": "ADDRESS_FOR_SERVICE_POSTCODE",
            "country": "ADDRESS_FOR_SERVICE_COUNTRY",
        },
        target_norms,
        best_by_name,
    )
    update_best_from_csv(
        office_csv,
        "registered_office",
        {
            "entity_name": "ENTITY_NAME",
            "start_date": "START_DATE",
            "a1": "REGISTERED_OFFICE_ADDRESS_1",
            "a2": "REGISTERED_OFFICE_ADDRESS_2",
            "a3": "REGISTERED_OFFICE_ADDRESS_3",
            "a4": "REGISTERED_OFFICE_ADDRESS_4",
            "postcode": "REGISTERED_OFFICE_ADDRESS_POSTCODE",
            "country": "REGISTERED_OFFICE_ADDRESS_COUNTRY",
        },
        target_norms,
        best_by_name,
    )
    print(f"Unique accredited entities with addresses: {len(best_by_name)}")

    matches = []
    for pdf_name in accredited_names:
        norm = normalize_name(pdf_name)
        addr = best_by_name.get(norm)
        if not addr:
            continue
        matches.append(
            {
                "pdf_company_name": pdf_name,
                "entity_name": addr.entity_name,
                "normalized_name": norm,
                "address": addr.address,
                "postcode": addr.postcode,
                "address_source": addr.source,
            }
        )

    matched_df = pd.DataFrame(matches)
    print(f"Accredited employers matched to address rows: {len(matched_df)}")

    if matched_df.empty:
        out_csv.write_text("", encoding="utf-8")
        print("No matched employers found. CSV created empty; map not generated.")
        return

    pc_lookup = postcode_coords_map(matched_df["postcode"].tolist())
    matched_df["postcode_norm"] = matched_df["postcode"].map(normalize_postcode)
    matched_df["lat"] = matched_df["postcode_norm"].map(lambda p: pc_lookup.get(p, (None, None))[0])
    matched_df["lon"] = matched_df["postcode_norm"].map(lambda p: pc_lookup.get(p, (None, None))[1])

    unresolved = matched_df[matched_df["lat"].isna() | matched_df["lon"].isna()].copy()
    if geocode_unresolved and not unresolved.empty:
        unique_addresses = sorted(unresolved["address"].dropna().unique())
        geocoded = geocode_addresses(unique_addresses, cache_path=cache_path, pause_seconds=0.2)
        matched_df.loc[matched_df["lat"].isna(), "lat"] = matched_df.loc[
            matched_df["lat"].isna(), "address"
        ].map(lambda a: geocoded.get(a, (None, None))[0])
        matched_df.loc[matched_df["lon"].isna(), "lon"] = matched_df.loc[
            matched_df["lon"].isna(), "address"
        ].map(lambda a: geocoded.get(a, (None, None))[1])
    matched_df = matched_df.dropna(subset=["lat", "lon"]).copy()
    matched_df = matched_df.drop(columns=["postcode_norm"])

    matched_df.to_csv(out_csv, index=False)
    print(f"Saved matched CSV: {out_csv} ({len(matched_df)} rows with coordinates)")

    if matched_df.empty:
        print("No geocoded rows; map was not generated.")
        return

    build_map(matched_df, out_html)
    print(f"Saved map HTML: {out_html}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create map of accredited employers that can be matched to location data")
    parser.add_argument("--pdf", default="accreditedemployers.pdf")
    parser.add_argument("--public-csv", default="companies_public_address.csv")
    parser.add_argument("--service-csv", default="companies_address_for_service.csv")
    parser.add_argument("--office-csv", default="companies_registered_office_address.csv")
    parser.add_argument("--out-csv", default="matched_accredited_employers_with_coords.csv")
    parser.add_argument("--out-html", default="accredited_employers_map.html")
    parser.add_argument("--cache", default="geocode_cache.json")
    parser.add_argument(
        "--geocode-unresolved",
        action="store_true",
        help="Fallback to online address geocoding for rows without postcode coordinates",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        pdf_path=Path(args.pdf),
        public_csv=Path(args.public_csv),
        service_csv=Path(args.service_csv),
        office_csv=Path(args.office_csv),
        out_csv=Path(args.out_csv),
        out_html=Path(args.out_html),
        cache_path=Path(args.cache),
        geocode_unresolved=args.geocode_unresolved,
    )
