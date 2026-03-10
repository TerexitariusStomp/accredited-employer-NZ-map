from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from geopy.geocoders import ArcGIS, Nominatim

from build_accredited_map import build_map


def normalize_address(address: str) -> str:
    s = re.sub(r"\s+", " ", str(address or "")).strip(" ,")
    s = re.sub(r",\s*,+", ",", s)
    return s


def address_variants(address: str) -> List[str]:
    base = normalize_address(address)
    variants = [base]
    if "NEW ZEALAND" not in base.upper():
        variants.append(f"{base}, NEW ZEALAND")
    # Drop unit-level detail as a fallback when exact address fails.
    unit_dropped = re.sub(r"^(UNIT|SUITE|LEVEL|FLAT)\s+[^,]+,\s*", "", base, flags=re.IGNORECASE)
    unit_dropped = normalize_address(unit_dropped)
    if unit_dropped and unit_dropped not in variants:
        variants.append(unit_dropped)
        if "NEW ZEALAND" not in unit_dropped.upper():
            variants.append(f"{unit_dropped}, NEW ZEALAND")
    return variants


def load_cache(cache_path: Path) -> Dict[str, Dict[str, object]]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache_path: Path, cache: Dict[str, Dict[str, object]]) -> None:
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def geocode_one(address: str, arcgis: ArcGIS, nominatim: Nominatim) -> Optional[Tuple[float, float, str, str]]:
    for query in address_variants(address):
        try:
            loc = arcgis.geocode(query)
        except Exception:
            loc = None
        if loc:
            return float(loc.latitude), float(loc.longitude), "arcgis", query

        try:
            loc = nominatim.geocode(query)
        except Exception:
            loc = None
        if loc:
            return float(loc.latitude), float(loc.longitude), "nominatim", query
    return None


def candidate_addresses(df: pd.DataFrame) -> Sequence[str]:
    lat_r = df["lat"].round(6)
    lon_r = df["lon"].round(6)
    grp = df.groupby([lat_r, lon_r], dropna=False)
    keys = []
    for key, g in grp:
        if len(g) <= 1:
            continue
        if g["entity_name"].nunique() <= 1:
            continue
        if g["address"].nunique() <= 1:
            continue
        keys.append(key)

    if not keys:
        return []

    key_set = set(keys)
    mask = [(a, b) in key_set for a, b in zip(lat_r, lon_r)]
    subset = df[mask]
    return sorted(subset["address"].dropna().map(normalize_address).unique())


def refine(csv_in: Path, csv_out: Path, html_out: Path, cache_path: Path, pause: float) -> None:
    df = pd.read_csv(csv_in)
    if df.empty:
        raise RuntimeError("Input CSV is empty")

    targets = candidate_addresses(df)
    print(f"Target addresses for precision refinement: {len(targets)}")
    if not targets:
        df.to_csv(csv_out, index=False)
        build_map(df, html_out)
        print("No shared-coordinate candidates found; outputs rewritten unchanged.")
        return

    cache = load_cache(cache_path)
    arcgis = ArcGIS(timeout=15)
    nominatim = Nominatim(user_agent="accredited-employer-map-refine", timeout=15)

    resolved: Dict[str, Tuple[float, float]] = {}
    total = len(targets)

    for i, addr in enumerate(targets, start=1):
        if addr in cache and "lat" in cache[addr] and "lon" in cache[addr]:
            resolved[addr] = (float(cache[addr]["lat"]), float(cache[addr]["lon"]))
            if i % 100 == 0:
                print(f"[{i}/{total}] cache hit")
            continue

        hit = geocode_one(addr, arcgis, nominatim)
        if hit:
            lat, lon, provider, query = hit
            resolved[addr] = (lat, lon)
            cache[addr] = {"lat": lat, "lon": lon, "provider": provider, "query": query}
            print(f"[{i}/{total}] Geocoded via {provider}: {addr}")
        else:
            cache[addr] = {"status": "no_match"}
            print(f"[{i}/{total}] No match: {addr}")

        if i % 50 == 0:
            save_cache(cache_path, cache)
        time.sleep(pause)

    save_cache(cache_path, cache)

    addr_norm = df["address"].map(normalize_address)
    new_lat = addr_norm.map(lambda a: resolved.get(a, (None, None))[0])
    new_lon = addr_norm.map(lambda a: resolved.get(a, (None, None))[1])

    changed = new_lat.notna() & new_lon.notna()
    df.loc[changed, "lat"] = new_lat[changed]
    df.loc[changed, "lon"] = new_lon[changed]

    if "coord_precision" not in df.columns:
        df["coord_precision"] = "original"
    df.loc[changed, "coord_precision"] = "address_web"

    df.to_csv(csv_out, index=False)
    build_map(df, html_out)

    lat_r = df["lat"].round(6)
    lon_r = df["lon"].round(6)
    after_shared_rows = int(df.groupby([lat_r, lon_r]).filter(lambda x: len(x) > 1).shape[0])
    print(f"Updated rows with refined coordinates: {int(changed.sum())}")
    print(f"Rows still sharing coordinates after refinement: {after_shared_rows}")
    print(f"Saved refined CSV: {csv_out}")
    print(f"Saved refined map: {html_out}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refine shared map coordinates using live web geocoding")
    p.add_argument("--in-csv", default="matched_accredited_employers_with_coords.csv")
    p.add_argument("--out-csv", default="matched_accredited_employers_with_coords_refined.csv")
    p.add_argument("--out-html", default="accredited_employers_map_refined.html")
    p.add_argument("--cache", default="geocode_refine_cache.json")
    p.add_argument("--pause", type=float, default=0.1)
    return p.parse_args()


if __name__ == "__main__":
    a = parse_args()
    refine(
        csv_in=Path(a.in_csv),
        csv_out=Path(a.out_csv),
        html_out=Path(a.out_html),
        cache_path=Path(a.cache),
        pause=a.pause,
    )
