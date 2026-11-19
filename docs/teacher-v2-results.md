# Teacher v2 implementation results

The `Group_01` draft contains the cleared [notebook](../Group_01/Group_01.ipynb)
and standalone [Python submission](../Group_01/Group_01.py). Both are generated
from the maintained matcher. `Solution` builds its indexes from the three
constructor lists and returns a fresh canonical three-field result on every
call. Geographic filtering is disabled for v2 because the legacy hierarchy
covers 2 of the 200 public triples.

## Accuracy

| Teacher public metric | Original matcher | Exported Solution |
| --- | ---: | ---: |
| Street | 200/200 | 200/200 |
| Ward | 184/200 | 200/200 |
| Province | 179/200 | 200/200 |
| Correct fields | 563/600 | 600/600 |
| Teacher score | 9.38/10 | **10.00/10** |
| Complete addresses | 167/200 | **200/200** |

Both Python versions reproduced the baseline and the final score. All 37
original field misses were recovered across 33 addresses, with zero public
regressions and zero exceptions. Predictions for all 1,000 reviewed legacy
addresses were identical before and after the change, including the six
reviewed ambiguity cases. Public rows were used to diagnose the joined-marker
failures. Private grading accuracy remains unmeasured.

A joined marker now gives the matcher an alternative dictionary lookup with
the original source span. Literal readings remain eligible, typed canonical
labels enforce marker compatibility, and fuzzy and typing recovery measure
the name body when there is enough marker evidence. The matcher retains its
conservative null policy for unsupported or ambiguous fields.

## Timing

Local measurements used CPython 3.12.3 and 3.14.4 on Linux x86-64 with an
Intel Core i7-9750H. Each public run used five passes with a new
`Solution` per pass. Two fresh interpreter runs per version gave 2,000
timed public calls each. `perf_counter_ns()` surrounded the full
`process()` call from the very first address, without a warmup. The
limits were checked on unrounded timings.

| Workload | Python | Per-pass mean range | Observed maximum | Mean ≤10 ms | Max ≤100 ms |
| --- | --- | ---: | ---: | :---: | :---: |
| Teacher public | 3.12.3 | 0.437–0.445 ms | 1.499 ms | Pass | Pass |
| Teacher public | 3.14.4 | 0.165–0.192 ms | 0.494 ms | Pass | Pass |
| 23,800 streets, 340 wards, 34 provinces | 3.12.3 | 0.752–1.010 ms | 50.769 ms | Pass | Pass |
| 23,800 streets, 340 wards, 34 provinces | 3.14.4 | 0.308–0.313 ms | 1.010 ms | Pass | Pass |
| Long noise, up to 41,006 characters | 3.12.3 | 12.934–13.192 ms | 44.498 ms | **Over** | Pass |
| Long noise, up to 41,006 characters | 3.14.4 | 3.875–3.985 ms | 14.388 ms | Pass | Pass |

The stress workloads used five passes each. Both runs scored all 600
expected fields on the synthetic scale cases and all 48 expected fields on
the long-noise cases. The long inputs were passed through in full.

| Public runtime diagnostics | Python 3.12.3 | Python 3.14.4 |
| --- | ---: | ---: |
| Median per fresh interpreter | 0.369–0.372 ms | 0.138–0.160 ms |
| p95 per fresh interpreter | 0.748–0.756 ms | 0.286–0.329 ms |
| p99 per fresh interpreter | 0.809–0.810 ms | 0.315–0.354 ms |
| First request across passes | 0.484–0.637 ms | 0.183–0.304 ms |
| Construction across passes | 19.944–21.288 ms | 6.301–8.219 ms |
| Construction peak Python allocations | 0.69 MiB | 0.65 MiB |

Construction was outside the timer. With 23,800 streets it took
1.19–1.23 s on 3.12 and 0.43–0.46 s on 3.14, with 68.3 and 67.7 MiB
peak traced Python allocations, respectively. Allocation tracing ran in
a separate construction and measures Python allocations rather than process
RSS.

## Verification and remaining check

- All 174 tests passed on 3.14. The 163 core and submission tests passed on
  3.12. The legacy prepared compiler and its hierarchy prototype use
  `marshal.dumps(..., allow_code=False)`, which fails on 3.12. The same
  failure was reproduced on the original commit. The v2 export uses
  standard-library source and builds its indexes directly from the lists.
- The package, exported Python file, and notebook solution cells produced the
  same public and fixture predictions. Isolated subprocesses ran outside the
  repository with its package unavailable. Checks included replacement and
  empty inventories, reordered and duplicate entries, unchanged input lists,
  and repeated calls after mutating a returned dictionary.
- The complete notebook and Python grader ran outside the repository under
  Python 3.14 and produced an Excel workbook at 10.00/10. The local replay
  supplied the original public files at the download step and provided
  XlsxWriter through the local environment. The notebook schema also passed
  validation. Both grader cell sources are preserved in the notebook.
- An interactive Colab run remains to be verified. Open the exported notebook
  in a fresh Colab runtime and select **Runtime → Run all**. Confirm the final
  cell shows 600/600 and creates `Group_01.xlsx`. Clear all outputs before
  downloading or submitting the notebook. The local replay did not verify
  Colab or the live Drive downloads.

## Reproduce locally

Run from the repository root.

```sh
python3.14 -m tools.build_submission --group Group_01
python3.14 -m unittest discover -s tests -q
python3.12 -m unittest discover -s tests -p test_teacher_v2.py -q
python3.14 -m tools.evaluate_teacher_v2 --output reports/teacher-v2/package.json
for runtime in python3.12 python3.14; do
  for run in 1 2; do
    "$runtime" -m tools.benchmark_solution --solution Group_01/Group_01.py \
      --output "reports/teacher-v2/public-$runtime-$run.json"
  done
  for workload in scale noisy; do
    "$runtime" -m tools.benchmark_solution --solution Group_01/Group_01.py \
      --workload "$workload" --output "reports/teacher-v2/$workload-$runtime.json"
  done
done
```

The benchmark writes its report before exiting with status 1 when a timing
limit fails. The reports include raw nanosecond measurements, slowest inputs,
per-pass summaries, interpreter metadata, and source and data hashes.

To reproduce the baseline in a temporary checkout from the same repository:

```sh
source_dir="$PWD/teacher-info/v2_2_level_address"
baseline_dir="$(mktemp -d)"
git archive 39a76e398198a94a4128d099e0ea8f2e26ed9151 | tar -x -C "$baseline_dir"
cp tools/evaluate_teacher_v2.py "$baseline_dir/tools/"
(cd "$baseline_dir" && python3.14 -m tools.evaluate_teacher_v2 \
  --source-dir "$source_dir" --output "$baseline_dir/baseline.json")
python3.14 -m tools.evaluate_teacher_v2 --baseline "$baseline_dir/baseline.json" \
  --output reports/teacher-v2/package.json
```
