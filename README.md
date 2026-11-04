# Vietnamese Address Segmentation (Stage A Foundation)

This package implements **Stage A Foundation** of the address segmentation architecture specified in [docs/architecture.md](docs/architecture.md) and [docs/address-segmentation-plan.md](docs/address-segmentation-plan.md).

It provides an exact-only, dictionary-constrained baseline implemented strictly in the Python standard library for CPython 3.14.

---

## 1. Quick Start and API

### Python API

```python
from address_segmentation import AddressMatcher

matcher = AddressMatcher(
    streets=["Nam Kỳ Khởi Nghĩa", "3/2"],
    wards=["Phường Bến Thành"],
    provinces=["Thành phố Hồ Chí Minh"],
)

result = matcher.identify("56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh")
# Result is always a fresh dictionary with exact keys 'street', 'ward', 'province':
# {
#   "street": "Nam Kỳ Khởi Nghĩa",
#   "ward": "Phường Bến Thành",
#   "province": "Thành phố Hồ Chí Minh"
# }
```

### CLI Adapter

The development CLI loads a JSON dictionary once, reads one JSON string per line from `stdin`, and writes one UTF-8 JSON object per line to `stdout`.

To run the CLI from the repository root:

```bash
python3.14 -m address_segmentation.io --dictionary tests/fixtures/dictionary.json
```

When invoking from an arbitrary directory or external working directory, ensure the repository root is included in `PYTHONPATH`:

```bash
PYTHONPATH=/path/to/repo python3.14 -m address_segmentation.io --dictionary /path/to/repo/tests/fixtures/dictionary.json
```

Example streaming invocation:

```bash
echo '"56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh"' | python3.14 -m address_segmentation.io --dictionary tests/fixtures/dictionary.json
# Output:
# {"street": "Nam Kỳ Khởi Nghĩa", "ward": "Phường Bến Thành", "province": "Thành phố Hồ Chí Minh"}
```

---

## 2. Test Execution

Unit tests are written with standard `unittest` and verify public API contracts, dictionary validation, normalization, conflict resolution, and CLI integration:

```bash
python3.14 -m unittest discover -s tests -v
```

The offline evaluation and benchmark commands write JSON reports with dataset and source hashes:

```bash
python3.14 -m tools.evaluate --dictionary tests/fixtures/dictionary.json --cases tests/fixtures/cases.jsonl --output reports/baseline.json
python3.14 -m tools.benchmark --dictionary tests/fixtures/dictionary.json --cases tests/fixtures/cases.jsonl --output reports/timing.json
python3.14 -m benchmarks.generate --output-dir reports/scale --seed 42
python3.14 -m tools.benchmark --dictionary reports/scale/dictionary.json --cases reports/scale/cases.jsonl --output reports/scale-timing.json
```

Evaluation reports separate wrong labels, false non-null assignments, and missed matches. Use `--baseline PATH` on the evaluation command to compare case-level gains and regressions against a report for the same dictionary and cases. Benchmarks separate import, loading, indexing, first request, warm requests, and JSONL process costs; memory runs in a separate process. Synthetic datasets exercise scale and controlled behaviors and do not establish accuracy on instructor data. Generated reports are ignored by Git; reviewed stage measurements are recorded under `docs/`.

---

## 3. Component Boundaries (Stage A)

- [`address_segmentation/__init__.py`](address_segmentation/__init__.py): Exports `AddressMatcher` as the stable application interface.
- [`address_segmentation/catalog.py`](address_segmentation/catalog.py): Validates label sequences, deduplicates identical labels, assigns stable deterministic IDs sorted lexicographically by `(field, canonical_label)`, and freezes collision-safe exact and folded lookup indexes into immutable tuples and sets.
- [`address_segmentation/normalize.py`](address_segmentation/normalize.py): Unicode NFC normalization, casefolding, diacritic folding (including `đ`/`Đ` -> `d`), and character-scanning tokenization. Preserves original text character slices and offsets for both precomposed NFC and decomposed NFD text. Preserves slash numeric forms (`3/2`, `56/33`).
- [`address_segmentation/matcher.py`](address_segmentation/matcher.py): Coordinates token-window matching, ranks evidence tiers (exact outranks folded), resolves same-field competing labels, and conservatively rejects cross-field evidence span conflicts.
- [`address_segmentation/io.py`](address_segmentation/io.py): Validates and loads dictionary JSON files (`streets`, `wards`, `provinces`) and streams JSON strings from `stdin` to JSON objects on `stdout`. Enforces one JSON string per line; rejects blank lines as malformed input.
- [`tests/fixtures/dictionary.json`](tests/fixtures/dictionary.json): Compact reference dictionary fixture with sample canonical Vietnamese address components.
- [`tests/test_foundation.py`](tests/test_foundation.py): Test suite covering canonical addresses, missing fields, empty inputs, non-string rejection, malformed dictionaries, accent/NFD handling, collisions, permuted inputs, numeric boundaries, shared span conflicts, catalog immutability, and CLI subprocess execution.

---

## 4. Stage A Scope and Limitations

Stage A deliberately establishes the exact-match foundation before approximate or heuristic algorithms are introduced:

1. **Exact token window matching only**:
   - Matches complete token windows against Unicode NFC casefolded forms and accent-folded forms.
   - Exact accented matches strictly outrank accent-folded matches.
   - Punctuation-delimited tokens preserve slash numbers (e.g. `3/2`); street `3/2` will not match inside house number `53/2`.
2. **Conservative ambiguity and collision rejection**:
   - Multiple distinct canonical labels competing in the same field return `null` (`None`) for that field.
   - Overlapping names and repeated cross-field occurrences: when evidence spans conflict across fields (e.g. `Hòa Bình` appearing as both street and province at the same address span), affected fields are conservatively set to `null` while preserving non-conflicting fields.
   - Full joint selection and contextual disambiguation are deferred to Stage C.
3. **No fuzzy or typing-aware recovery**:
   - Edit-distance matching, character n-gram approximate retrieval, and keyboard error corrections are deferred to later stages.
   - Telex/VNI residual modifier processing (`cungx`, `Đuơcj`, `cung4`) is deferred to Stage E.
4. **No automatic prefix manipulation**:
   - Administrative prefixes (`Phường`, `Thành phố`, `Đường`) are preserved from canonical labels; automatic stripping or synthetic insertion is not performed in Stage A.
5. **No geographic database inference**:
   - Unmentioned fields are emitted as `null` (`None`). Missing components are never hallucinated from outside map data.
