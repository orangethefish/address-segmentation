# Vietnamese Address Segmentation (Stages A–E)

This package implements **stages A–E** of the address segmentation architecture specified in [docs/architecture.md](docs/architecture.md) and [docs/address-segmentation-plan.md](docs/address-segmentation-plan.md).

It provides a dictionary-constrained matcher with bounded typo, Telex/VNI, and whitespace recovery implemented strictly in the Python standard library for CPython 3.14.

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

## 3. Component Boundaries

- [`address_segmentation/__init__.py`](address_segmentation/__init__.py): Exports `AddressMatcher` as the stable application interface.
- [`address_segmentation/catalog.py`](address_segmentation/catalog.py): Validates label sequences, deduplicates identical labels, assigns stable deterministic IDs sorted lexicographically by `(field, canonical_label)`, and freezes collision-safe exact and folded lookup indexes into immutable tuples and sets.
- [`address_segmentation/normalize.py`](address_segmentation/normalize.py): Unicode NFC normalization, casefolding, diacritic folding (including `đ`/`Đ` -> `d`), and character-scanning tokenization. Preserves original text character slices and offsets for both precomposed NFC and decomposed NFD text. Preserves slash numeric forms (`3/2`, `56/33`).
- [`address_segmentation/matcher.py`](address_segmentation/matcher.py): Coordinates exact retrieval, bounded typo/typing recovery, shared work budgets, and joint selection.
- [`address_segmentation/approximate.py`](address_segmentation/approximate.py), [`verify.py`](address_segmentation/verify.py), and [`typing.py`](address_segmentation/typing.py): Indexed recovery, thresholded OSA edit verification, compact matching, and candidate-specific Telex/VNI verification.
- [`address_segmentation/io.py`](address_segmentation/io.py): Validates and loads dictionary JSON files (`streets`, `wards`, `provinces`) and streams JSON strings from `stdin` to JSON objects on `stdout`. Enforces one JSON string per line; rejects blank lines as malformed input.
- [`tests/fixtures/dictionary.json`](tests/fixtures/dictionary.json): Compact reference dictionary fixture with sample canonical Vietnamese address components.
- [`tests/test_foundation.py`](tests/test_foundation.py): Test suite covering canonical addresses, missing fields, empty inputs, non-string rejection, malformed dictionaries, accent/NFD handling, collisions, permuted inputs, numeric boundaries, shared span conflicts, catalog immutability, and CLI subprocess execution.

---

## 4. Joint selection and diagnostics

The matcher retrieves complete canonical forms and field-specific name bodies, recognizes administrative markers, and selects compatible candidates jointly. Different fields cannot reuse overlapping input spans. Distinct occurrences can support multiple fields. Unresolved label ties return null, preserving independently supported fields. Candidate pruning is conservative and reported explicitly.

```python
from address_segmentation.model import MatcherConfig
matcher = AddressMatcher(streets=["Hòa Bình"], provinces=["Hòa Bình"], config=MatcherConfig(top_k=8))
result, trace = matcher.identify_with_trace("Hòa Bình")
```

The trace shows candidates before and after verification and selection pruning, plus rejection reasons. Public `identify` results always contain only the three address fields. Evaluation reports measure candidate recall and identify the first stage that loses each recoverable expected label. Use `--config path.json` with the development CLI, evaluator, or benchmark to supply explicit `MatcherConfig` values; reports record the configuration.

General typo recovery, compact-word matching, and constrained Telex/VNI hypotheses are enabled by default. Set `enable_fuzzy`, `enable_compact`, or `enable_typing` to `false` in a configuration JSON to isolate each path. Typing and compact recovery also work with fuzzy matching disabled. Keyboard-neighbor weighting is deferred.

Recovery compares complete component spans with dictionary names. Telex/VNI shape keys and terminal tone keys are candidate-specific hypotheses; literal dictionary names remain competitors. Numeric names are exact only. Compact matching changes whitespace boundaries and preserves accent evidence; it does not remove numeric separators or internal punctuation. All recovery paths share deterministic operation budgets, and exhausted searches abstain on affected recovered fields.

See [stage E results and limits](docs/stage-e.md) for positive/negative controls, ablations, and measured performance. Reproduce the feature comparison with:

```bash
python3.14 -m tools.ablate --dictionary tests/fixtures/typing-dictionary.json --cases tests/fixtures/typing-cases.jsonl --output-dir reports/stage-e --timing
```

There is no geographic inference. The development JSONL protocol is provisional until the instructor supplies the grader contract.
