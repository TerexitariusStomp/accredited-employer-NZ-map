from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def esc(s: object) -> str:
    text = "" if s is None else str(s)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_html(rows: list[dict], center_lat: float, center_lon: float) -> str:
    sectors = sorted({r["sector"] for r in rows})
    subsectors = sorted({r["subsector"] for r in rows})

    rows_json = json.dumps(rows, ensure_ascii=False)
    sectors_json = json.dumps(sectors, ensure_ascii=False)
    subsectors_json = json.dumps(subsectors, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Accredited Employer Map</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\"/>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css\"/>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css\"/>
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .filter-panel {{
      position: absolute;
      top: 10px;
      right: 10px;
      z-index: 1000;
      background: #fff;
      border: 1px solid #999;
      border-radius: 6px;
      padding: 10px;
      width: 320px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.15);
      font-family: Arial, sans-serif;
      font-size: 14px;
    }}
    .filter-panel label {{ display: block; font-weight: 600; margin-bottom: 4px; }}
    .filter-panel select {{ width: 100%; margin-bottom: 10px; padding: 6px; }}
    .filter-stats {{ font-size: 12px; color: #333; }}
    .downloads {{ margin-top: 8px; font-size: 12px; }}
    .downloads a {{ margin-right: 10px; }}
  </style>
</head>
<body>
  <div id=\"map\"></div>

  <div class=\"filter-panel\">
    <label for=\"sectorFilter\">Sector</label>
    <select id=\"sectorFilter\"></select>

    <label for=\"subsectorFilter\">Subsector</label>
    <select id=\"subsectorFilter\"></select>

    <div class=\"filter-stats\" id=\"filterStats\"></div>
    <div class=\"downloads\">
      Downloads:
      <a href=\"accredited_employers_map_points.kml\" download>KML</a>
      <a href=\"accredited_employers_map_points.kmz\" download>KMZ</a>
    </div>
  </div>

  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
  <script src=\"https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js\"></script>
  <script>
    const rows = {rows_json};
    const sectors = {sectors_json};
    const subsectors = {subsectors_json};

    const map = L.map('map').setView([{center_lat}, {center_lon}], 6);
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    const cluster = L.markerClusterGroup();
    map.addLayer(cluster);

    const sectorFilter = document.getElementById('sectorFilter');
    const subsectorFilter = document.getElementById('subsectorFilter');
    const filterStats = document.getElementById('filterStats');

    function fillSelect(sel, values, label) {{
      sel.innerHTML = '';
      const all = document.createElement('option');
      all.value = '';
      all.textContent = `All ${{label}}`;
      sel.appendChild(all);
      for (const v of values) {{
        const o = document.createElement('option');
        o.value = v;
        o.textContent = v;
        sel.appendChild(o);
      }}
    }}

    fillSelect(sectorFilter, sectors, 'Sectors');
    fillSelect(subsectorFilter, subsectors, 'Subsectors');

    function passes(row) {{
      const s = sectorFilter.value;
      const ss = subsectorFilter.value;
      if (s && row.sector !== s) return false;
      if (ss && row.subsector !== ss) return false;
      return true;
    }}

    function popup(row) {{
      return `<b>${{row.pdf_company_name}}</b><br>` +
             `Matched Entity: ${{row.entity_name}}<br>` +
             `Sector: ${{row.sector}}<br>` +
             `Subsector: ${{row.subsector}}<br>` +
             `Address: ${{row.address}}`;
    }}

    function render() {{
      cluster.clearLayers();
      let shown = 0;
      for (const row of rows) {{
        if (!passes(row)) continue;
        const m = L.circleMarker([row.lat, row.lon], {{
          radius: 5,
          color: '#d1495b',
          weight: 1,
          fillOpacity: 0.8
        }});
        m.bindPopup(popup(row));
        m.bindTooltip(row.pdf_company_name, {{sticky: true}});
        cluster.addLayer(m);
        shown += 1;
      }}
      filterStats.textContent = `${{shown.toLocaleString()}} points shown of ${{rows.length.toLocaleString()}}`;
    }}

    sectorFilter.addEventListener('change', render);
    subsectorFilter.addEventListener('change', render);
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate robust interactive filtered HTML map")
    parser.add_argument("--in-csv", default="matched_accredited_employers_with_coords.csv")
    parser.add_argument("--out-html", default="accredited_employers_map.html")
    args = parser.parse_args()

    df = pd.read_csv(args.in_csv)
    df = df.dropna(subset=["lat", "lon"]).copy()
    if "sector" not in df.columns:
      df["sector"] = "Unknown"
    if "subsector" not in df.columns:
      df["subsector"] = "Unknown"

    rows = []
    for _, r in df.iterrows():
        rows.append(
            {
                "pdf_company_name": esc(r.get("pdf_company_name", "")),
                "entity_name": esc(r.get("entity_name", "")),
                "sector": esc(r.get("sector", "Unknown")),
                "subsector": esc(r.get("subsector", "Unknown")),
                "address": esc(r.get("address", "")),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
            }
        )

    center_lat = float(df["lat"].mean())
    center_lon = float(df["lon"].mean())

    html_text = build_html(rows, center_lat, center_lon)
    Path(args.out_html).write_text(html_text, encoding="utf-8")
    print(f"Wrote {args.out_html} with {len(rows)} points")


if __name__ == "__main__":
    main()
