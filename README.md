# Vietnamese Address Segmentation

This package implements **stages A–E** of the address segmentation architecture specified in [docs/architecture.md](docs/architecture.md) and [docs/address-segmentation-plan.md](docs/address-segmentation-plan.md).

It provides a dictionary-constrained matcher with bounded typo, Telex/VNI, and whitespace recovery in the Python standard library. The teacher v2 submission has been tested on CPython 3.12 and 3.14.

## Teacher v2 submission

The grader builds one `Solution(list_province, list_ward, list_street)` and times each `process(s)` call. Both backends index those three flat lists during construction and match fields using text evidence. Each request returns a fresh dictionary with `province`, `ward`, and `street`. The grader scores exact canonical values one field at a time. Unsupported or ambiguous fields return `None` under the project's conservative policy.

Build both files from the maintained matcher. Replace `Group_01` with your assigned group name.

```sh
python3.14 -m tools.build_submission --group Group_01
python3.14 -m tools.evaluate_teacher_v2 --output reports/teacher-v2/package.json
python3.14 -m tools.benchmark_solution --solution Group_01/Group_01.py --output reports/teacher-v2/public-314.json
```

The notebook preserves the teacher's grading cells and clears saved outputs. The Python file is importable with the standard library alone. Running it as a script also runs the grader, whose public downloads require `gdown` and whose workbook uses pandas and XlsxWriter.

See the [v2 implementation report](docs/teacher-v2-results.md) for public accuracy, stress tests, reproduction commands, and the remaining Colab check.

Build and score the trie + DP variant in a separate directory for comparison.

```sh
python3.14 -m tools.build_submission --group Group_01 --backend trie_dp --output-dir reports/teacher-v2-trie-dp/trie_dp
python3.14 -m tools.evaluate_teacher_v2 --backend trie_dp --output reports/teacher-v2-trie-dp/package-trie_dp.json
python3.14 -m tools.benchmark_solution --solution reports/teacher-v2-trie-dp/trie_dp/Group_01/Group_01.py --output reports/teacher-v2-trie-dp/public-trie_dp-314.json
```

The checked-in submission uses the default `indexed` backend. See the
[v2 backend comparison](docs/teacher-v2-results.md#trie-and-dp-comparison) for
accuracy, request latency, and index construction costs.

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

### Legacy fixed-dictionary CLI adapter

For historical fresh-process runs with a fixed dictionary, use the
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
comes from the legacy dataset preparation command below. Keep the complete
generated bundle, including its source and prepared bytecode. This path targets
CPython 3.14 and uses a launch-to-result timing boundary.

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

The JSONL protocol supports local development and historical fresh-process benchmarks.

## Trie and DP backend

Both backends support the teacher's v2 contract, including joined
administrative markers. Select `trie_dp` explicitly through the same API:

```python
from address_segmentation import AddressMatcher
from address_segmentation.model import MatcherConfig

matcher = AddressMatcher(
    streets=["An Phú", "An Phú Đông"],
    wards=["Phường Bến Thành"],
    provinces=["Thành phố Hồ Chí Minh"],
    config=MatcherConfig(backend="trie_dp"),
)
```

The prototype uses token tries for exact full/body names, character tries for
initials and compact names, a token trie for typing hypotheses, and a character
trie with banded OSA DP for ordinary typo search. It builds no trigram index.
Canonical validation, Unicode/source spans, typed administrative markers,
Telex/VNI verification, numeric restrictions, completeness scoring and
null-on-ambiguity rules are shared with the default implementation.

Joint selection uses forward/backward DP over source positions and eight masks
of already-used fields. It computes competing label/null scores rather than
breaking traceback ties. `top_k` is unused by this backend: repeated evidence
can support a field beyond the old cutoff. Candidate endpoint sorting costs
O(C log C), followed by O(8*C) selection work after completeness filtering.

`max_trie_nodes` bounds inspected fuzzy-trie edges per request (default 12,000),
including length and field rejections. `max_verifications` bounds terminal
checks and closer-split distance checks. Recovery retains the existing span,
per-span candidate and shared typing budgets. Exhaustion rejects affected
recovered fields and unresolved exact prefixes, preserving independent exact
evidence. Diagnostics include the trie edge counter, including zero on exact
and empty inputs.

Run a comparison or build an offline bundle with the supplied configuration:

```sh
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --output reports/trie-dp-prototype/baseline.json
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --config tests/fixtures/trie-dp-config.json --baseline reports/trie-dp-prototype/baseline.json --output reports/trie-dp-prototype/prototype.json
python3.14 -m tools.compile_dictionary --dictionary reports/user-1/dictionary.json --config tests/fixtures/trie-dp-config.json --output-dir reports/trie-dp-prototype/bundle
python3.14 -S reports/trie-dp-prototype/bundle/submission.py
python3.14 -m tools.benchmark_prepared --bundle reports/trie-dp-prototype/bundle --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --cold-cases 30 --processes 3 --no-site --output reports/trie-dp-prototype/timing.json
```

The existing compiler stores individual trie nodes and the runtime reads them on
demand. There is no dictionary-wide reconstruction at launch. The prepared
bundle retains the same CPython, bytecode and launch-flag requirements described
above. This is an experimental implementation; the existing bounded span
extraction and selected Telex/VNI rules still limit supported noise patterns.

### Historical prototype measurements

All 175 tests, compilation and whitespace checks pass on CPython 3.14.4. The
prototype matches the baseline on all 65 control cases, 1,000 reviewed user
cases and 1,000 synthetic scale cases, with zero regressions. Original user
accuracy stays 994/1,000 because the six accent collisions remain null.
Complete prepared/source traces agree on every user and scale case. These are
development fixtures, not an independent accuracy holdout.

The cold comparison uses **30 uniformly sampled unique cases per dataset,
repeated in three rounds**, with shipped bytecode. Each table cell summarizes
90 fresh processes; each run also checks three complete 1,000-case batches.
Every sampled prediction and every batch prediction matches its expectation.

| Dictionary | Indexed `-S` mean ms | Trie + DP `-S` mean ms | Trie + DP `-S` max ms | Trie + DP normal-startup mean ms |
| --- | ---: | ---: | ---: | ---: |
| User: 515 labels | 8.252 | 8.663 | 10.453 | 12.048 |
| Scale: 24,174 labels | 9.621 | 8.962 | 9.508 | 12.632 |

The prototype passes the sampled launch-to-stdout limits with `-S`; normal
startup fails the 10 ms mean. Even with `-S`, mean launch-to-exit times are
10.632 ms (user) and 10.952 ms (scale), so an exit-based timer fails the mean.
The sampled maximum covers this launch-to-stdout workload. The teacher v2
notebook measures `Solution.process()` after construction.

Warm prototype matching averages 0.149 ms for user cases and 0.375 ms for scale
cases, excluding initialization. Construction is slower and the node-based
prepared artifacts are larger: about 1.22 MB versus 0.60 MB for user data, and
40.34 MB versus 29.25 MB at scale (decimal MB). This prototype demonstrates
feasibility and removes selection's top-K cutoff; it does not establish a
general speed or memory improvement.

[Recorded results](docs/trie-dp-results.json) retain source/dataset identities,
configurations, accuracy comparisons, construction/warm statistics, sampled
case IDs, launch-to-exit measurements and hashes of full reports. Raw reports,
bundles and the measurement scripts remain under ignored
`reports/trie-dp-prototype/`. The reproduction commands above use the same
sample size, rounds and seed.
