"""Build teacher v2 geographic links from local administrative and map snapshots."""

import argparse
from collections import defaultdict
import json
from math import cos, radians
from pathlib import Path
import re

import osmium
from osmium.replication import get_replication_header
from shapely import LineString, STRtree, make_valid
from shapely.affinity import scale
from shapely.geometry import shape
from shapely.ops import unary_union

from address_segmentation.catalog import extract_prefix_body, validate_field_sequence
from address_segmentation.normalize import fold_accents, tokenize_label
from tools.datasets import sha256_file
from tools.evaluate_teacher_v2 import write_report

TEACHER_SOURCE = Path("teacher-info/v2_2_level_address")
NAME_TAGS = ("name", "name:vi", "official_name", "alt_name")
TONE_SPELLINGS = {
    **dict(zip(("oà", "oá", "oả", "oã", "oạ"), ("òa", "óa", "ỏa", "õa", "ọa"))),
    **dict(zip(("oè", "oé", "oẻ", "oẽ", "oẹ"), ("òe", "óe", "ỏe", "õe", "ọe"))),
    **dict(zip(("uỳ", "uý", "uỷ", "uỹ", "uỵ"), ("ùy", "úy", "ủy", "ũy", "ụy"))),
}
TONE_PATTERN = re.compile("|".join(TONE_SPELLINGS))


def administrative_key(name, field):
    nfc, folded = tokenize_label(name)
    if field == "ward" and folded[:2] == ("dac", "khu") and len(folded) > 2:
        return folded[2:]
    _, _, _, folded_body = extract_prefix_body(field, nfc, folded)
    return folded_body


def street_key(name):
    nfc, _ = tokenize_label(name)
    if nfc[:2] == ("đường", "phố") and len(nfc) > 2:
        nfc = nfc[2:]
    elif nfc and nfc[0] in {"đường", "phố"} and len(nfc) > 1:
        nfc = nfc[1:]
    return TONE_PATTERN.sub(lambda match: TONE_SPELLINGS[match[0]], " ".join(nfc))


def inventory_lookup(labels, key):
    lookup = defaultdict(set)
    for label in labels:
        lookup[key(label)].add(label)
    return lookup


def administrative_areas(admin_path, dictionary):
    """Match the constructor labels to official codes, preserving repeated names."""
    provinces = inventory_lookup(dictionary["provinces"], lambda name: administrative_key(name, "province"))
    wards = inventory_lookup(dictionary["wards"], lambda name: administrative_key(name, "ward"))
    areas = {}
    for province in json.loads(admin_path.read_text(encoding="utf-8")):
        province_labels = provinces.get(administrative_key(province["name"], "province"))
        if not province_labels:
            continue
        province_code = str(province["code"]).zfill(2)
        for ward in province["wards"]:
            ward_labels = wards.get(administrative_key(ward["name"], "ward"))
            if ward_labels:
                ward_code = str(ward["code"]).zfill(5)
                areas[ward_code] = (province_code, province_labels, ward_labels)
    return areas


def load_ward_geometries(root, areas):
    """Read individual ward GeoJSON files named by official ward code."""
    polygons, files = {}, {}
    filename = re.compile(r"(?:[0-9]{2}-)?([0-9]{5})(?:_.*)?\.geojson$")
    for path in sorted(root.rglob("*.geojson")):
        match = filename.fullmatch(path.name)
        if match is None or match[1] not in areas:
            continue
        code = match[1]
        if code in polygons:
            raise ValueError(f"Duplicate ward boundary for {code}")
        data = json.loads(path.read_text(encoding="utf-8"))
        features = data["features"]
        if not features or any(str(f["properties"]["code"]).zfill(5) != code for f in features):
            raise ValueError(f"Incorrect ward code in {path}")
        polygons[code] = unary_union([make_valid(shape(f["geometry"])) for f in features])
        if polygons[code].is_empty or polygons[code].area == 0:
            raise ValueError(f"No ward polygon in {path}")
        files[path.relative_to(root).as_posix()] = sha256_file(path)
    missing = sorted(areas.keys() - polygons.keys())
    if missing:
        raise ValueError(f"Missing boundaries for ward codes {missing}")
    return polygons, files


def find_street_links(roads_path, polygons, street_labels, minimum_metres):
    """Accumulate line overlap by street label and ward code."""
    exact = inventory_lookup(street_labels, street_key)
    folded = inventory_lookup(street_labels, lambda name: fold_accents(street_key(name)))
    codes = list(polygons)
    tree = STRtree(list(polygons.values()))
    lengths = defaultdict(float)
    processor = (osmium.FileProcessor(str(roads_path)).with_locations()
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
                 .with_filter(osmium.filter.KeyFilter("highway")))
    for way in processor:
        if way.tags.get("highway") in {"construction", "proposed"}:
            continue
        names = set()
        for tag in NAME_TAGS:
            for alias in way.tags.get(tag, "").split(";"):
                if not alias.strip():
                    continue
                key = street_key(alias)
                matches = exact.get(key)
                if not matches and key == fold_accents(key):
                    possible = folded.get(key, ())
                    matches = possible if len(possible) == 1 else ()
                names.update(matches or ())
        if not names or len(way.nodes) < 2 or not all(node.location.valid() for node in way.nodes):
            continue
        line = LineString([(node.lon, node.lat) for node in way.nodes])
        for ward_idx in tree.query(line, predicate="intersects"):
            code = codes[ward_idx]
            clipped = line.intersection(polygons[code])
            if clipped.length == 0:
                continue
            # The degree-to-metre scale at the clipped segment's latitude is
            # sufficient here to reject tiny boundary overlaps.
            x_scale = 111_320 * cos(radians(clipped.centroid.y))
            length = scale(clipped, xfact=x_scale, yfact=110_660, origin=(0, 0)).length
            for label in names:
                lengths[code, label] += length
    return {key for key, length in lengths.items() if length >= minimum_metres}


def add_street_evidence(nested, street_labels, path):
    """Add links corroborated by independently published locality records."""
    if path is None:
        return 0
    known_streets = set(street_labels)
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("Street evidence must contain an array of records")
    added = 0
    for record in records:
        if not isinstance(record, dict) or record.keys() != {"province", "ward", "street", "source"}:
            raise ValueError(f"Invalid street evidence record: {record!r}")
        province, ward, street, source = (record[key] for key in ("province", "ward", "street", "source"))
        if (not all(isinstance(value, str) for value in (province, ward, street, source))
                or ward not in nested.get(province, {}) or street not in known_streets
                or not source.startswith("https://")):
            raise ValueError(f"Invalid street evidence record: {record!r}")
        streets = nested[province][ward]
        added += street not in streets
        streets.add(street)
    return added


def build_hierarchy(source_dir, admin_path, boundaries_dir, roads_path, output_dir,
                    minimum_metres=5, *, street_evidence=None):
    inventory_paths = {field: source_dir / f"public_list_{field}.txt"
                       for field in ("province", "ward", "street")}
    dictionary = {
        field + "s": validate_field_sequence(path.read_text(encoding="utf-8").strip().splitlines(), field)
        for field, path in inventory_paths.items()
    }
    areas = administrative_areas(admin_path, dictionary)
    polygons, boundary_hashes = load_ward_geometries(boundaries_dir, areas)
    links = find_street_links(roads_path, polygons, dictionary["streets"], minimum_metres)
    nested = {province: {} for province in dictionary["provinces"]}
    for _, provinces, wards in areas.values():
        for province in provinces:
            for ward in wards:
                nested[province].setdefault(ward, set())
    for code, street in links:
        _, provinces, wards = areas[code]
        for province in provinces:
            for ward in wards:
                nested[province][ward].add(street)
    supplemental_paths = add_street_evidence(nested, dictionary["streets"], street_evidence)
    hierarchy = {
        province: {ward: sorted(streets) for ward, streets in sorted(wards.items())}
        for province, wards in sorted(nested.items())
    }
    ward_labels = {ward for wards in hierarchy.values() for ward in wards}
    street_labels = {street for wards in hierarchy.values() for streets in wards.values() for street in streets}
    snapshot = get_replication_header(str(roads_path))
    summary = {
        "administrative_sha256": sha256_file(admin_path),
        "boundary_sha256": boundary_hashes,
        "road_sha256": sha256_file(roads_path),
        "road_timestamp": snapshot.timestamp.isoformat() if snapshot and snapshot.timestamp else None,
        "inventory_sha256": {field: sha256_file(path) for field, path in inventory_paths.items()},
        "minimum_overlap_metres": minimum_metres,
        "street_evidence_sha256": sha256_file(street_evidence) if street_evidence is not None else None,
        "supplemental_street_paths": supplemental_paths,
        "ward_codes": len(areas),
        "province_ward_pairs": sum(len(wards) for wards in hierarchy.values()),
        "street_ward_province_paths": sum(len(streets) for wards in hierarchy.values() for streets in wards.values()),
        "mapped_ward_labels": len(ward_labels),
        "mapped_street_labels": len(street_labels),
        "unmapped_street_labels": sorted(set(dictionary["streets"]) - street_labels),
    }
    write_report(output_dir / "dictionary.json", dictionary)
    write_report(output_dir / "address_hierarchy_v2.json", hierarchy)
    write_report(output_dir / "sources.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=TEACHER_SOURCE)
    parser.add_argument("--admin", type=Path, required=True, help="Local Vietnam Provinces API depth=2 JSON")
    parser.add_argument("--boundaries", type=Path, required=True, help="Directory of ward GeoJSON files")
    parser.add_argument("--roads", type=Path, required=True, help="Local OSM PBF")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-overlap-metres", type=int, default=5)
    parser.add_argument("--street-evidence", type=Path, help="Optional independently sourced street links")
    args = parser.parse_args()
    if args.minimum_overlap_metres < 0:
        parser.error("--minimum-overlap-metres must be nonnegative")
    summary = build_hierarchy(args.source_dir, args.admin, args.boundaries, args.roads,
                              args.output_dir, args.minimum_overlap_metres,
                              street_evidence=args.street_evidence)
    print(f"{summary['province_ward_pairs']} province–ward pairs and "
          f"{summary['street_ward_province_paths']} complete paths")


if __name__ == "__main__":
    main()
