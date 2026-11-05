"""Audit supplied relationships and measure an isolated selection prototype.

This research tool leaves the maintained matcher and compiler unchanged. Its
generated bundle adds only whole-path compatibility to the existing selector;
it does not implement the complete architecture proposed in the design review.
"""

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import py_compile

from address_segmentation import AddressMatcher
from address_segmentation.normalize import fold_accents
from tools.benchmark_prepared import benchmark_prepared
from tools.compile_dictionary import compile_dictionary, dictionary_records, write_index
from tools.datasets import sha256_file
from tools.prepare_dataset import prepare_dataset


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate hierarchy key: {key!r}")
        result[key] = value
    return result


def area_masks(hierarchy):
    """Map canonical field/label pairs to bits for province/ward occurrences.

    Empty provinces receive a province-only bit; empty street lists still have
    a ward occurrence. Thus partial selections remain meaningful on sparse data.
    """
    if not isinstance(hierarchy, dict):
        raise ValueError("Hierarchy must be a province object")
    masks = defaultdict(int)
    nodes = []
    for province, wards in sorted(hierarchy.items()):
        if not isinstance(province, str) or not province.strip() or not isinstance(wards, dict):
            raise ValueError("Provinces must have nonblank names and ward objects")
        if not wards:
            nodes.append((province, None))
            masks["province", province] |= 1 << (len(nodes) - 1)
        for ward, streets in sorted(wards.items()):
            if not isinstance(ward, str) or not ward.strip() or not isinstance(streets, list):
                raise ValueError("Wards must have nonblank names and street arrays")
            if any(not isinstance(s, str) or not s.strip() for s in streets):
                raise ValueError("Street names must be nonblank strings")
            if len(set(streets)) != len(streets):
                raise ValueError(f"Duplicate street under {(province, ward)!r}")
            nodes.append((province, ward))
            bit = 1 << (len(nodes) - 1)
            masks["province", province] |= bit
            masks["ward", ward] |= bit
            for street in streets:
                masks["street", street] |= bit
    return nodes, dict(masks)


def audit(hierarchy, dictionary, raw):
    nodes, masks = area_masks(hierarchy)
    labels = {f: sorted(label for field, label in masks if field == f)
              for f in ("street", "ward", "province")}
    differences = {f: {"only_list": sorted(set(dictionary[f + "s"]) - set(values)),
                       "only_hierarchy": sorted(set(values) - set(dictionary[f + "s"]))}
                   for f, values in labels.items()}
    if any(v for difference in differences.values() for v in difference.values()):
        raise ValueError(f"Hierarchy and flat dictionaries disagree: {differences}")
    triples = {(p, w, s) for p, wards in hierarchy.items()
               for w, streets in wards.items() for s in streets}
    ward_provinces, street_wards, street_provinces = defaultdict(set), defaultdict(set), defaultdict(set)
    for p, w in nodes:
        if w is not None:
            ward_provinces[w].add(p)
    for p, w, s in triples:
        street_wards[s].add(w)
        street_provinces[s].add(p)
    checked = mismatches = pairwise_false_positives = 0
    example = None
    for p in labels["province"]:
        for w in labels["ward"]:
            pair_mask = masks["province", p] & masks["ward", w]
            for s in labels["street"]:
                actual = (p, w, s) in triples
                predicted = bool(pair_mask & masks["street", s])
                checked += 1
                mismatches += actual != predicted
                if (not actual and p in ward_provinces[w] and w in street_wards[s]
                        and p in street_provinces[s]):
                    pairwise_false_positives += 1
                    if example is None:
                        example = dict(street=s, ward=w, province=p)
    invalid_rows, collisions = [], []
    for number, record in enumerate(raw, 1):
        result = record["result"]
        domain = None
        for field, label in result.items():
            if label is not None:
                value = masks[field, label]
                domain = value if domain is None else domain & value
        if domain == 0:
            invalid_rows.append(number)
        ward, province, street = (result[f] for f in ("ward", "province", "street"))
        alternatives = [w for w in labels["ward"] if ward is not None and fold_accents(w) == fold_accents(ward)]
        if len(alternatives) > 1:
            viable = [w for w in alternatives
                      if masks["ward", w] & masks.get(("province", province), (1 << len(nodes)) - 1)
                      & masks.get(("street", street), (1 << len(nodes)) - 1)]
            collisions.append(dict(row=number, alternatives=alternatives, viable=viable))
    return {
        "counts": {**{f + "s": len(v) for f, v in labels.items()},
                   "province_ward_occurrences": len(nodes), "valid_triples": len(triples)},
        "dictionary_difference": differences,
        "ward_province_count_histogram": dict(sorted(Counter(map(len, ward_provinces.values())).items())),
        "streets_per_ward_histogram": dict(sorted(Counter(len(ss) for wards in hierarchy.values() for ss in wards.values()).items())),
        "mask_values_marshal_bytes": sum(len(marshal.dumps(v, 4, allow_code=False)) for v in masks.values()),
        "maximum_mask_bytes": max(((v.bit_length() + 7) // 8 for v in masks.values()), default=0),
        "exhaustive_triples_checked": checked, "mask_mismatches": mismatches,
        "invalid_triples_passing_pairwise_checks": pairwise_false_positives,
        "pairwise_counterexample": example,
        "raw_cases": len(raw), "invalid_expected_rows": invalid_rows,
        "ward_label_collisions": collisions,
    }


def prototype_source(source):
    """Add relationship checks to generated source, solely for this experiment."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "select_candidates":
            node.args.args.append(ast.arg(arg="hierarchy"))
            index = next(i for i, item in enumerate(node.body)
                         if isinstance(item, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "choices" for t in item.targets))
            node.body[index:index] = ast.parse(
                'masks = {c.entry.entry_id: hierarchy[c.entry.entry_id] for c in retained_candidates}'
            ).body
            outer = next(n for n in node.body if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "cs")
            outer.body[:0] = ast.parse('street_mask = masks[cs.entry.entry_id] if cs is not None else None').body
            middle = next(n for n in outer.body if isinstance(n, ast.For) and n.target.id == "cw")
            middle.body[:0] = ast.parse('''
pair_mask = masks[cw.entry.entry_id] if cw is not None else None
if street_mask is not None:
    pair_mask = street_mask if pair_mask is None else street_mask & pair_mask
if pair_mask == 0:
    continue
''').body
            inner = next(n for n in middle.body if isinstance(n, ast.For) and n.target.id == "cp")
            inner.body[:0] = ast.parse('''
if cp is not None and pair_mask is not None and not (pair_mask & masks[cp.entry.entry_id]):
    continue
''').body
        if isinstance(node, ast.ClassDef) and node.name == "AddressMatcher":
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "select_candidates":
                    child.args.append(ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()), attr="_hierarchy", ctx=ast.Load()))
        if isinstance(node, ast.ClassDef) and node.name == "PreparedMatcher":
            constructor = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
            block = next(n for n in constructor.body if isinstance(n, ast.Try))
            block.body[:0] = ast.parse('self._hierarchy = PreparedMap(self._store, "hierarchy")').body
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "RUNTIME_ID" for t in node.targets))]
    ast.fix_missing_locations(tree)
    source = ast.unparse(tree) + "\n"
    identity = hashlib.sha256(source.encode()).hexdigest()
    return source + f"RUNTIME_ID = {identity!r}\n", identity


def build_prototype(dictionary_path, hierarchy_path, destination):
    """Only dictionary and hierarchy enter compilation; no cases or predictions."""
    manifest = compile_dictionary(dictionary_path, destination)
    hierarchy = json.loads(hierarchy_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    _, masks = area_masks(hierarchy)
    dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))
    matcher = AddressMatcher(**dictionary)
    expected_keys = {(entry.field, entry.canonical_label) for entry in matcher.catalog.entries}
    if expected_keys != set(masks):
        raise ValueError("Prototype requires equal dictionary and hierarchy label sets")
    source, runtime_id = prototype_source((destination / "matcher_runtime.py").read_text(encoding="utf-8"))
    (destination / "matcher_runtime.py").write_text(source, encoding="utf-8")
    records = list(dictionary_records(matcher))
    records.extend(("hierarchy", entry.entry_id, masks[entry.field, entry.canonical_label])
                   for entry in matcher.catalog.entries)
    metadata = dict(manifest["metadata"], runtime_id=runtime_id,
                    hierarchy_hash=sha256_file(hierarchy_path),
                    hierarchy_policy="authoritative-no-inference")
    manifest["metadata"] = write_index(destination / "dictionary.idx", metadata, records)
    py_compile.compile(str(destination / "matcher_runtime.py"), dfile="matcher_runtime.py", doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    manifest["experiment"] = "whole-path selection only; not production hierarchy support"
    manifest["investigation_source_hash"] = sha256_file(__file__)
    manifest["files"] = {name: {"bytes": (destination / name).stat().st_size,
                                "sha256": sha256_file(destination / name)} for name in manifest["files"]}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("testcases/1"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--benchmark-rounds", type=int, default=0)
    parser.add_argument("--cold-cases", type=int, default=0)
    args = parser.parse_args(argv)
    if args.benchmark_rounds < 0 or args.cold_cases < 0:
        parser.error("benchmark-rounds and cold-cases must be non-negative")
    source, output = args.source_dir.resolve(), args.output_dir.resolve()
    if output == source or source in output.parents:
        parser.error("Output must be outside the raw source directory")
    output.mkdir(parents=True, exist_ok=True)
    prepare_dataset(source, output / "data")
    dictionary_path = output / "data" / "dictionary.json"
    cases_path = output / "data" / "raw-cases.jsonl"
    hierarchy_path = source / "address_hierarchy.json"
    dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))
    raw = json.loads((source / "all_raw.json").read_text(encoding="utf-8"))
    hierarchy = json.loads(hierarchy_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    summary = audit(hierarchy, dictionary, raw)
    summary["source_hashes"] = {p.name: sha256_file(p) for p in source.iterdir() if p.is_file()}
    summary["investigation_source_hash"] = sha256_file(__file__)
    compile_dictionary(dictionary_path, output / "flat-bundle")
    build_prototype(dictionary_path, hierarchy_path, output / "hierarchy-bundle")
    spec = importlib.util.spec_from_file_location("hierarchy_prototype", output / "hierarchy-bundle" / "matcher_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flat = AddressMatcher(**dictionary)
    changed, flat_correct, hierarchy_correct = [], 0, 0
    with module.PreparedMatcher(output / "hierarchy-bundle" / "dictionary.idx") as matcher:
        for number, record in enumerate(raw, 1):
            before, after = flat.identify(record["address"]), matcher.identify(record["address"])
            flat_correct += before == record["result"]
            hierarchy_correct += after == record["result"]
            if before != after:
                changed.append(dict(row=number, address=record["address"], before=before, after=after))
    summary["prototype"] = dict(flat_correct=flat_correct, hierarchy_correct=hierarchy_correct,
                                changed_cases=changed,
                                index_bytes={v: (output / (v + "-bundle") / "dictionary.idx").stat().st_size
                                             for v in ("flat", "hierarchy")})
    for variant in ("flat", "hierarchy") if args.benchmark_rounds else ():
        report = benchmark_prepared(output / (variant + "-bundle"), dictionary_path, cases_path,
                                    processes=args.benchmark_rounds, cold_cases=args.cold_cases, no_site=True)
        report_path = output / (variant + "-timing.json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary.setdefault("timing", {})[variant] = {
            "phases": report["phases"], "thresholds": report["thresholds"],
            "coverage": report["cold_coverage"], "report_hash": sha256_file(report_path),
        }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": summary["counts"],
                      "mask_mismatches": summary["mask_mismatches"],
                      "flat_correct": flat_correct, "hierarchy_correct": hierarchy_correct}))


if __name__ == "__main__":
    main()
