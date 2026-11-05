# Vietnamese Address Segmentation (Stages A–E)

This package implements **stages A–E** of the address segmentation architecture specified in [docs/architecture.md](docs/architecture.md) and [docs/address-segmentation-plan.md](docs/address-segmentation-plan.md).

It provides a dictionary-constrained matcher with bounded typo, Telex/VNI, and whitespace recovery implemented strictly in the Python standard library for CPython 3.14.

The `feat/support-for-hierachy` branch includes a
[hierarchy architecture and feasibility study](docs/hierarchy-architecture.md)
for the supplied `testcases/1/address_hierarchy.json`. An isolated prototype
resolves all 1,000 original results and meets the local cold timing targets with
the prepared launch configuration. The package API below remains the flat
implementation; the design records the production integration stages.

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

For a dictionary fixed before submission, use the new
[offline dictionary compiler and standalone runtime](docs/prepared-dictionary.md).
It prepares dictionary lookups before submission and queries them on demand in
each fresh process, retaining all matching features:

```sh
python3.14 -m tools.compile_dictionary --dictionary reports/user-1/dictionary.json --output-dir reports/prepared-user-1
python3.14 -S reports/prepared-user-1/submission.py
```

The [prepared-dictionary review](docs/prepared-dictionary.md) records complete
test-case-1 cold timings, default versus `-S` startup and source-only comparisons,
packaging requirements and reproduction commands. The `reports/user-1` dictionary
comes from the dataset preparation command below. Keep the complete generated
bundle, including its source and prepared bytecode. This path targets CPython 3.14;
the instructor's launch and packaging contract still determines applicability.

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

Evaluation reports separate wrong labels, false non-null assignments, and missed matches. Use `--baseline PATH` on the evaluation command to compare case-level gains and regressions against a report for the same dictionary and cases. Synthetic datasets exercise scale and controlled behaviors and do not establish accuracy on instructor data. Generated reports are ignored by Git; reviewed stage measurements are recorded under `docs/`.

The benchmark's primary timing assumes **a new script process for every address**.
The parent measures launch through receipt of the complete stdout result line, including interpreter/imports,
dictionary reads, all indexing, matching, and JSONL output captured from stdout.
The measured program writes no prediction file. Report JSON is written by the
harness after measurement. Launch-to-exit time is recorded separately; cleanup
after output is available does not inflate the primary timing. Each CLI process
must still exit successfully within the timeout.

By default every case runs in a separate process in each of five rounds, with
no cold-process warmup. This can take hours for a large dictionary: 1,000 cases
mean 5,000 index builds. For a shorter, explicitly sampled assessment use:

```bash
python3.14 -m tools.benchmark --dictionary reports/scale/dictionary.json --cases reports/scale/cases.jsonl --cold-cases 20 --output reports/scale-cold-timing.json
```

`--cold-cases N` selects a seeded uniform sample without replacement, reused
across rounds. `--cold-cases 0` (the default) covers all cases. Reports retain
case IDs, individual timings, predictions and sample coverage; a sampled maximum
cannot certify the full dataset. `--processes` controls rounds; `--warmup` and
`--repetitions` affect only warm diagnostics. A CLI failure, malformed stdout or
`--cli-timeout` expiry aborts measurement. A successful benchmark command means
the report was produced; check its printed PASS/FAIL and `thresholds` for timing.

Schema-v2 `thresholds` apply the 10 ms mean and 100 ms maximum limits to
`phases.cold_process_request`. `first_request` means the first **identify call
after indexing** and remains diagnostic, as do warm calls and shared-process
batch throughput. Import/load/index phases are measured independently and must
not be added to the already inclusive cold time. Memory is isolated from latency.
Fresh processes do not clear OS page caches or Python bytecode caches. See the
[updated timing review](docs/user-dataset-review.md#performance-against-the-documented-limits)
for measured results under this grading assumption.

The [construction optimization review](docs/cold-start-optimization.md) records
the subsequent removal of repeated dictionary normalization, new cold timings,
startup overhead probes and alternatives. Construction is faster with all
features preserved, but **the fresh-process mean and maximum still fail**.
Removing recovery indexes alone does not meet the limits.

---

### User-supplied dataset

`testcases/1` is a development dataset supplied by the user, with 1,000 addresses,
162 streets, 319 wards and 34 provinces. Its four original files are preserved.
Convert its provisional `address`/`result` array and text dictionaries explicitly:

```bash
python3.14 -m tools.prepare_dataset --source-dir testcases/1 --review testcases/1/review.json --output-dir reports/user-1
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/raw-cases.jsonl --output reports/user-1/raw-evaluation.json
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --output reports/user-1/reviewed-evaluation.json
python3.14 -m tools.benchmark --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --output reports/user-1/timing.json
```

The adapter records source hashes, validates canonical membership, and groups
identical inputs together. `raw-cases.jsonl` preserves every original expectation.
`cases.jsonl` applies six audited null-on-ambiguity expectations from `review.json`:
accentless `Long Thanh` cannot distinguish `Long Thành` from `Long Thạnh` of the
same administrative type. Omitting `--review` preserves all original expectations
in both outputs. See [review results](docs/user-dataset-review.md) for accuracy,
performance and remaining official-contract limits.

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

The matcher retrieves complete canonical forms and field-specific name bodies, recognizes administrative markers, and selects compatible candidates jointly. Marker types distinguish `Phường`, `Xã` and `Thị trấn`; numbered street bodies require a street marker. Province body initials such as `HN` and `TP.HCM` are derived from dictionary entries, retain collisions, and rank below literal names. Complete names suppress contained fragments at the same occurrence while preserving explicit accent evidence. Matching respects commas, semicolons and newlines as component boundaries. Different fields cannot reuse overlapping input spans. Distinct occurrences can support multiple fields. Unresolved label ties return null, preserving independently supported fields. Candidate pruning is conservative and reported explicitly.

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
