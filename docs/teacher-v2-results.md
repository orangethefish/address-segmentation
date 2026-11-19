# Teacher v2 implementation results

The `Group_01` draft contains the cleared [notebook](../Group_01/Group_01.ipynb)
and standalone [Python submission](../Group_01/Group_01.py). Both are generated
from the maintained matcher. `Solution` builds its indexes from the three
flat constructor lists and returns a fresh canonical three-field result on
every call. The implementation lives on `main`. The legacy hierarchy audit is
recorded in the [implementation plan](teacher-v2-implementation-plan.md#4-hierarchy-audit-and-decision).

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

Initial local measurements used CPython 3.12.3 and 3.14.4 on Linux x86-64 with an
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

On `main`, five public passes per interpreter reproduced the full score and
met both timing limits. Each pass started with a newly constructed `Solution`.

| Python | Mean `process()` time | Observed maximum |
| --- | ---: | ---: |
| 3.12.3 | 0.486 ms | 2.350 ms |
| 3.14.4 | 0.208 ms | 0.672 ms |

## Verification and remaining check

- On `main`, all 169 tests passed on 3.14 and the 159 core and submission
  tests passed on 3.12. The legacy prepared-bundle tests target 3.14 because
  the compiler uses `marshal.dumps(..., allow_code=False)`.
- The package, exported Python file, and notebook solution cells produced the
  same public and fixture predictions. Isolated subprocesses ran outside the
  repository with its package unavailable. Checks included replacement and
  empty inventories, reordered and duplicate entries, unchanged input lists,
  and repeated calls after mutating a returned dictionary. The checked-in
  submission files matched a clean rebuild byte for byte on `main`.
- During initial implementation, the complete notebook and Python grader ran
  outside the repository under Python 3.14 at 10.00/10. The local replay
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
git archive 17a3316 | tar -x -C "$baseline_dir"
cp tools/evaluate_teacher_v2.py "$baseline_dir/tools/"
(cd "$baseline_dir" && python3.14 -m tools.evaluate_teacher_v2 \
  --source-dir "$source_dir" --output "$baseline_dir/baseline.json")
python3.14 -m tools.evaluate_teacher_v2 --baseline "$baseline_dir/baseline.json" \
  --output reports/teacher-v2/package.json
```

## Trie and DP comparison

On `feature/implement-with-trie-and-dp`, both backends use the v2 flat lists
and recognize joined markers. Trie recovery uses banded OSA on the marker's
body and passes the original source interval to joint DP selection. Both
fuzzy backends apply the v2 closer-split check, including the extra distance
check in the verification budget. The checked-in draft uses `indexed`.

| Correctness | Indexed | Trie + DP |
| --- | ---: | ---: |
| Public correct fields | 600/600 | 600/600 |
| Public complete addresses | 200/200 | 200/200 |
| Reviewed legacy complete addresses | 1,000/1,000 | 1,000/1,000 |
| Scale correct fields | 600/600 | 600/600 |
| Long-noise correct fields | 48/48 | 48/48 |

The trie port gained 33 fields across 31 public addresses over the pre-port
trie, with zero regressions. Both backends preserved the six expected nulls
in the reviewed legacy dataset. Exported predictions were identical across
backends, runtimes, and passes on all three timed workloads, with zero
exceptions. The [pre-port comparison](teacher-v2-implementation-plan.md#measured-starting-point)
records the historical reference scores and timings.

### Request latency and construction

Both standalone Python exports ran on the same host and in alternating
backend order. Each interpreter ran two fresh processes per backend and
workload, with five passes per process and a fresh `Solution` each pass. The
timer covered `process()` starting from the first request. Values below are
run-level mean ranges and the largest observed request times, in milliseconds.

| Workload | Python | Indexed mean | Trie + DP mean | Indexed max | Trie + DP max |
| --- | --- | ---: | ---: | ---: | ---: |
| Teacher public | 3.12.3 | 0.436–0.437 | 0.652–0.721 | 1.377 | 5.325 |
| Teacher public | 3.14.4 | 0.175–0.179 | 0.314–0.326 | 0.486 | 2.589 |
| 23,800 streets | 3.12.3 | 0.843–0.938 | 0.828–0.869 | 54.025 | 3.334 |
| 23,800 streets | 3.14.4 | 0.363–0.437 | 0.373–0.391 | 2.898 | 1.781 |
| Long noise | 3.12.3 | 12.153–13.602 | 14.515–16.302 | 45.782 | 48.587 |
| Long noise | 3.14.4 | 4.011–4.453 | 4.943–5.069 | 20.911 | 15.344 |

Both backends met the 10 ms mean and 100 ms maximum project limits on public
and scale workloads. Both exceeded the mean limit on Python 3.12 with inputs
up to 41,006 characters. Public p95 was 0.746 ms for indexed and 2.318–2.499 ms
for trie + DP on Python 3.12. On Python 3.14 it was 0.304–0.314 ms and
0.963–0.979 ms, respectively. Public first requests across passes took
0.489–0.595 ms for indexed and 0.535–0.708 ms for trie + DP on Python 3.12,
and 0.193–0.393 ms and 0.285–0.431 ms on Python 3.14.

Public index construction median ranges were 19.31–19.57 ms for indexed and
36.52–42.25 ms for trie + DP on Python 3.12, and 6.52–6.97 ms and
16.23–17.03 ms on Python 3.14. At scale they were 1.27–1.38 s and 2.24–2.25 s
on Python 3.12, and 0.48–0.50 s and 0.80–0.84 s on Python 3.14. Peak traced
construction allocations were 0.65–0.69 MiB for indexed and 2.82–2.89 MiB
for trie + DP on public data, and 67.78–68.25 MiB and 94.87–98.93 MiB at scale.
These measurements keep construction and allocation tracing outside the
request timer.

Untimed traces show that 56 of 200 public addresses entered recovery on each
backend. Every public recovery stayed within budget. On scale, indexed hit
the per-span candidate limit on 30 cases and trie + DP stayed within budget.
The scale predictions were identical. The slowest indexed scale sample on
Python 3.12 used the exact path in an untimed replay. Both backends hit the
span limit on four long-noise cases. The public latency and construction
results favor indexed as the default.

### Verification and reproduction

All 187 tests passed on Python 3.14, including the prepared runtime tests.
The 176 core and submission tests passed on Python 3.12. Export tests ran
both notebook solution cells and Python drafts outside the repository, with
replacement inventories and the original public data. The complete notebook
and Python grader for each backend also scored 600/600 in a local replay
using the checked-in public data. A live Colab run and private grading
remain to be verified.

```sh
report_dir=reports/teacher-v2-trie-dp
for backend in indexed trie_dp; do
  python3.14 -m tools.build_submission --group Group_xx --backend "$backend" \
    --output-dir "$report_dir/$backend"
done
python3.14 -m tools.evaluate_teacher_v2 --backend indexed --output "$report_dir/package-indexed.json"
python3.14 -m tools.evaluate_teacher_v2 --backend trie_dp --baseline "$report_dir/package-indexed.json" \
  --output "$report_dir/package-trie_dp.json"
python3.14 -m unittest discover -s tests -q

python3.14 -m tools.benchmark_solution --solution "$report_dir/trie_dp/Group_xx/Group_xx.py" \
  --workload public --output "$report_dir/public-trie_dp-python3.14.json"
```

Run the same benchmark with the indexed export, both runtimes, and
`--workload scale` or `--workload noisy` for the stress runs. The ignored
report directory contains the 24 raw benchmark reports with input and
artifact hashes, per-pass results, percentiles, slowest rows, and predictions.
