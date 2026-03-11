from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from folium.plugins import TreeLayerControl
import folium
from pypdf import PdfReader

from export_google_maps_kml import write_kml, write_kmz

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


def normalize_name(value: str) -> str:
    s = value.upper().replace("&", " ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_pdf_line(line: str) -> Optional[Tuple[str, str, str]]:
    raw = re.sub(r"\s+", " ", line).strip()
    if not raw:
        return None
    lower = raw.lower()
    if any(lower.startswith(prefix) for prefix in HEADER_PREFIXES):
        return None

    for sector in sorted(SECTORS, key=len, reverse=True):
        idx = raw.find(sector)
        if idx <= 0:
            continue
        name = raw[:idx].strip(" -:\t")
        if len(name) < 2 or not any(ch.isalpha() for ch in name):
            continue
        subsector = raw[idx + len(sector) :].strip()
        if not subsector:
            subsector = "Unknown"
        return name, sector, subsector
    return None


def load_pdf_sector_index(pdf_path: Path) -> Dict[str, Tuple[str, str]]:
    reader = PdfReader(str(pdf_path))
    out: Dict[str, Tuple[str, str]] = {}
    for p in reader.pages:
        text = p.extract_text() or ""
        for line in text.splitlines():
            rec = parse_pdf_line(line)
            if not rec:
                continue
            name, sector, subsector = rec
            out[normalize_name(name)] = (sector, subsector)
    return out


def enrich_csv_with_categories(csv_path: Path, pdf_path: Path, out_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "normalized_name" not in df.columns:
        df["normalized_name"] = df["entity_name"].map(normalize_name)

    if pdf_path.exists():
        idx = load_pdf_sector_index(pdf_path)
        df["sector"] = df["normalized_name"].map(lambda n: idx.get(n, ("Unknown", "Unknown"))[0])
        df["subsector"] = df["normalized_name"].map(lambda n: idx.get(n, ("Unknown", "Unknown"))[1])
    else:
        if "sector" not in df.columns:
            df["sector"] = "Unknown"
        if "subsector" not in df.columns:
            df["subsector"] = "Unknown"
    df.to_csv(out_csv, index=False)
    return df


def popup_html(row: pd.Series) -> str:
    parts = [
        f"<b>{html.escape(str(row.get('pdf_company_name', '')))}</b>",
        f"Matched Entity: {html.escape(str(row.get('entity_name', '')))}",
        f"Sector: {html.escape(str(row.get('sector', 'Unknown')))}",
        f"Subsector: {html.escape(str(row.get('subsector', 'Unknown')))}",
        f"Address: {html.escape(str(row.get('address', '')))}",
    ]
    return "<br>".join(parts)


def build_filtered_html_map(df: pd.DataFrame, out_html: Path) -> None:
    center_lat = float(df["lat"].mean())
    center_lon = float(df["lon"].mean())
    m = folium.Map(location=[center_lat, center_lon], zoom_start=6, tiles="OpenStreetMap")

    sector_subsector_groups: Dict[Tuple[str, str], folium.FeatureGroup] = {}
    for (sector, subsector), group_df in df.groupby(["sector", "subsector"], dropna=False):
        sector_name = str(sector or "Unknown")
        subsector_name = str(subsector or "Unknown")
        fg = folium.FeatureGroup(name=f"{sector_name} / {subsector_name}", show=True)
        fg.add_to(m)
        sector_subsector_groups[(sector_name, subsector_name)] = fg

        for _, row in group_df.iterrows():
            folium.CircleMarker(
                location=[float(row["lat"]), float(row["lon"])],
                radius=4,
                color="#d1495b",
                weight=1,
                fill=True,
                fill_opacity=0.75,
                popup=folium.Popup(popup_html(row), max_width=420),
                tooltip=str(row.get("pdf_company_name", "")),
            ).add_to(fg)

    sector_nodes: List[dict] = []
    for sector_name in sorted({k[0] for k in sector_subsector_groups.keys()}):
        children = []
        subsectors = sorted([k[1] for k in sector_subsector_groups.keys() if k[0] == sector_name])
        for subsector_name in subsectors:
            layer = sector_subsector_groups[(sector_name, subsector_name)]
            count = len(df[(df["sector"] == sector_name) & (df["subsector"] == subsector_name)])
            children.append({"label": f"{subsector_name} ({count})", "layer": layer})

        sector_total = len(df[df["sector"] == sector_name])
        sector_nodes.append(
            {
                "label": f"{sector_name} ({sector_total})",
                "selectAllCheckbox": "All/None",
                "children": children,
            }
        )

    overlay_tree = {
        "label": "Accredited Employers",
        "selectAllCheckbox": "All/None",
        "children": sector_nodes,
    }
    TreeLayerControl(overlay_tree=overlay_tree, collapsed=False).add_to(m)

    title = """
    <div style="position: fixed; top: 10px; left: 50px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #999; font-size: 12px;">
      Use the top-right tree to toggle <b>Sector</b> or <b>Subsector</b> point groups.
    </div>
    """
    m.get_root().html.add_child(folium.Element(title))

    m.save(str(out_html))


def main() -> None:
    parser = argparse.ArgumentParser(description="Add sector/subsector filtering to HTML + KML/KMZ outputs")
    parser.add_argument("--pdf", default="accreditedemployers.pdf")
    parser.add_argument("--in-csv", default="matched_accredited_employers_with_coords.csv")
    parser.add_argument("--out-csv", default="matched_accredited_employers_with_coords.csv")
    parser.add_argument("--out-html", default="accredited_employers_map.html")
    parser.add_argument("--out-kml", default="accredited_employers_map_points.kml")
    parser.add_argument("--out-kmz", default="accredited_employers_map_points.kmz")
    args = parser.parse_args()

    df = enrich_csv_with_categories(Path(args.in_csv), Path(args.pdf), Path(args.out_csv))
    build_filtered_html_map(df, Path(args.out_html))

    count = write_kml(Path(args.out_csv), Path(args.out_kml))
    write_kmz(Path(args.out_kml), Path(args.out_kmz))

    print(f"Updated CSV with sector/subsector: {args.out_csv} ({len(df)} rows)")
    print(f"Updated HTML map with filter tags: {args.out_html}")
    print(f"Updated KML: {args.out_kml} ({count} placemarks)")
    print(f"Updated KMZ: {args.out_kmz}")


if __name__ == "__main__":
    main()
