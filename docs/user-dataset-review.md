# User dataset review — 2026-11-05

The matcher now passes all 1,000 reviewed user cases and all 1,000 seeded scale
cases. On the unchanged original user expectations it improves from 233/1,000 to
994/1,000, with 761 gains and zero regressions. The remaining six differences are
irreducible accent collisions covered by the documented null-on-ambiguity policy.
The original accuracy review passed 144 tests on CPython 3.14.4. The timing
correction below passes 149 tests and compilation checks with the matcher unchanged.

This review uses the authoritative [requirements](address-segmentation-plan.md)
and [architecture](architecture.md). [Historical evidence](user-dataset-results.json)
retains source/dataset hashes, baseline identity, metric denominators, warm timing,
memory and the long-input probes. The [cold-process evidence](cold-start-results.json)
and timing correction below supersede the earlier runtime conclusions. The
historical warm measurements excluded initialization and cannot establish compliance
when each address is graded in a separate process.

## Data and expectations

The initial dirty state contained only untracked `testcases/`; there were no
tracked code edits. The four original files in `testcases/1` are preserved byte
for byte. They contain 1,000 unique addresses, 162 street labels, 319 ward labels
and 34 province labels. Every original expected label belongs to its dictionary.
The fixture is user supplied, independent of the instructor, and all its examples
were inspected during development. It is not a held-out validation set.

The explicit `tools.prepare_dataset` adapter accepts the existing array of
`address`/`result` objects and line-separated dictionary files. It validates the
schema and labels, records hashes, groups identical addresses, and rejects
conflicting duplicate inputs. It leaves the matching API and provisional JSONL
protocol unchanged. It does not infer a future instructor schema.

One-based source rows **114, 208, 691, 740, 945 and 977** contain `Long Thanh` without
accents. Both `Long Thành` and `Long Thạnh` exist with the matching `Phường` or `Xã`
prefix. The input supplies no distinguishing evidence, and flat dictionaries
provide no authorized geographic links. `testcases/1/review.json` documents each
collision, its original label, alternatives and reason. Reviewed expectations set
only the ward to null; the original address and other expected fields remain
intact. The adapter emits both original and reviewed cases, and checks that the
review still refers to the original label and real dictionary collisions.

| Dataset / expectations | Before | After | Wrong labels after |
| --- | --- | --- | --- |
| User, original | 233/1,000 | 994/1,000 | 0/3,000 non-null fields |
| User, reviewed | — | 1,000/1,000 | 0/2,994 non-null fields |
| Synthetic scale, unchanged | 897/1,000 | 1,000/1,000 | 0 |

The original user field accuracy is 2,994/3,000; all six differences are null
wards. Reviewed field accuracy is 3,000/3,000 with zero false non-null assignments
across the six expected-null fields. Original user candidate recall remains
3,000/3,000 before verification, after verification and after pruning. No user
case exhausts a recovery budget. The scale suite gains 103 complete cases with
zero regressions; 150 scale cases exercise conservative budget exhaustion and
still produce their expected results.

## Fixes and controls

- Preserve administrative type through exact-body, fuzzy, compact and typing
  matching. `P.` supports `Phường`, `X.` supports `Xã`, and `TT.` supports `Thị trấn`.
  Province city/province markers follow the same rule. Bare dictionary names
  remain usable; unmarked shared bodies retain their ambiguity.
- Derive province initials from canonical name bodies. `HN`, `HCM` and `TP.HCM`
  retrieve dictionary candidates without a hardcoded geographic table. Colliding
  initials remain competitors, and literal labels have stronger evidence.
- Prefer complete observed names over their contained fragments at the same
  occurrence. `Nguyễn Thị Minh Khai` retains its full street label even when
  `Minh Khai` is also a street or ward body. Conflicting exact accents, independent
  marked fields and separate occurrences remain competitors.
- Prevent exact windows from crossing commas, semicolons or newlines. A ward
  ending in `Nguyên` followed by province `Huế` cannot invent street `Nguyễn Huệ`.
- Require a street marker for numeric bodies of prefixed street labels.
  `Số 123` cannot borrow the missing `Đường` from canonical `Đường số 123`.
  This removes 102 false non-null assignments from the original scale run.
- Preserve explicit compact-match accents at their character positions, including
  `đ`. A lone incompatible dictionary entry cannot override observed accents.
- Index possible window lengths by first token, sweep candidate coverage, and
  index candidate starts and marker ends. This removes repeated full scans on
  long inputs, including repeated exact matches followed by unexplained noise.

Regression tests include positive and negative marker controls, initial collisions
and literal competitors, accent contradictions, separator boundaries, numeric
house/street distinctions, long repetitions, adapter validation and all reviewed
user cases. The previous 128 tests continue to pass unchanged.

## Performance against the documented limits

The measurements in this section precede the construction optimizations. See
the [subsequent construction review](cold-start-optimization.md) for current
timings, validation and alternatives; the cold timing contract is unchanged.

With the dictionary subsequently confirmed fixed, the
[prepared-dictionary review](prepared-dictionary.md) records a new submission
path and exhaustive test-case-1 cold measurements. The historical timings below
still describe the CLI that rebuilds from JSON on every launch.

**The current implementation fails the fresh-process grading assumption.** The
scale fixture fails both the 10 ms average and 100 ms maximum limits. The small
user fixture also fails the average limit; only its sampled maximum is below
100 ms. Earlier sub-millisecond timings covered `identify` after indexing and
could not support a runtime-compliance conclusion for isolated script launches.

The constructor creates the catalog, approximate index and typing/compact index
for every matcher. The CLI constructs a matcher on every launch, before reading
stdin. Thus every separately graded run rebuilds the full dictionary. Moving
that work into imports or dividing a batch duration by its address count would
not remove the cost from a fresh-process request.

The corrected benchmark invokes the real `address_segmentation.io` CLI with one
JSON address on stdin and captures its result from stdout through an OS pipe.
The parent starts a monotonic timer immediately before process launch and stops
when it receives the complete result line. This includes interpreter startup,
imports, dictionary/config reads, validation and indexing, input parsing,
matching, output serialization, flushing and pipe transfer. A parent reader
thread drains stdout and timestamps complete lines; pipe/thread setup happens
before timing, while reader scheduling overhead is included. Process exit is
recorded separately, and each CLI must finish successfully within its timeout.
There is no prediction file in the measured program. Harness fixture parsing,
input encoding, output scoring and report-file writes are outside the timer.

Both datasets ran sequentially on CPython 3.14.4, with tracing disabled for
latency. Each uses **20 unique uniformly sampled addresses, seed 42, repeated in
five seeded rounds: 100 fresh interpreters and full index builds per dataset**.
There are zero cold-process warmup calls. This is an explicitly sampled timing
assessment, not a measurement of all 1,000 cold cases. Selected IDs, every cold
measurement and prediction, source/dataset hashes, platform, configuration,
phase summaries and memory are retained in [the reviewed evidence](cold-start-results.json).
All 100 cold outputs and all 1,000 outputs in each of five batch rounds match
the reviewed expectations for each dataset. Warm diagnostics separately cover
15,000 calls per dataset (ten warmups, three repetitions, five workers). Memory
runs in a separate process.

| Measurement | User fixture | Scale fixture | Scope / target |
| --- | --- | --- | --- |
| Dictionary labels | 515 | 24,174 | Full dictionary in every process |
| **Cold launch-to-stdout mean** | **68.284 ms** | **1,797.262 ms** | **≤ 10 ms; both fail** |
| Cold launch-to-stdout p95 | 77.545 ms | 2,050.084 ms | Sampled distribution |
| **Cold launch-to-stdout maximum** | **87.194 ms** | **2,537.044 ms** | **≤ 100 ms; scale fails** |
| Launch-to-exit mean | 75.662 ms | 1,835.245 ms | Separate diagnostic |
| Mean index preparation | 24.701 ms | 1,647.994 ms | Independently measured; already included in cold time |
| Warm identify mean | 0.112 ms | 0.387 ms | Excludes startup; diagnostic only |
| Shared-process batch mean, all 1,000 addresses | 203.551 ms | 2,461.066 ms | Launch through last stdout line |
| Batch elapsed / 1,000 | 0.204 ms | 2.461 ms | Throughput; not per-request maximum latency |

The scale dictionary is 23,800 streets + 340 wards + 34 provinces. Index
preparation alone averages about 1.65 seconds. Capturing stdout instead of writing
predictions to JSON files does not remove this initialization cost. Phase
summaries come from separate diagnostic processes; do not sum them into the
already inclusive cold timing.

The default benchmark measures every case separately in each of five rounds.
For this 1,000-case scale dataset that means 5,000 index builds and can take
hours. `--cold-cases 20` explicitly chooses the shorter sampled assessment above;
it does not choose the fastest cases. A sampled maximum is only the largest
observed sample, not a bound for unmeasured inputs, and the repeated sample does
not become 100 unique cases. Fresh processes do not clear OS page caches or
Python bytecode caches. These limitations do not rescue the observed scale
failures. The actual instructor environment and protocol still require
verification; under the requested local timing assumption the result is a fail.

Historical single-process long-input probes covered 100–10,004 characters with
a largest observed matching call of 40.150 ms. They remain diagnostic evidence
in `user-dataset-results.json`; they excluded initialization and are not part of
this cold-process sample. No input is silently truncated. The matcher remains
unchanged, offline and standard-library Python. All 149 tests, compilation and
whitespace checks pass after the timing correction.

## Reproduction

Run from the repository root, with timing commands sequentially:

```sh
python3.14 -m tools.prepare_dataset --source-dir testcases/1 --review testcases/1/review.json --output-dir reports/user-1
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/raw-cases.jsonl --output reports/user-1/raw-evaluation.json
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --output reports/user-1/reviewed-evaluation.json
python3.14 -m tools.benchmark --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --cold-cases 20 --output reports/user-1/cold-timing.json
python3.14 -m benchmarks.generate --output-dir reports/review-scale --seed 42
python3.14 -m tools.evaluate --dictionary reports/review-scale/dictionary.json --cases reports/review-scale/cases.jsonl --output reports/review-scale/evaluation.json
python3.14 -m tools.benchmark --dictionary reports/review-scale/dictionary.json --cases reports/review-scale/cases.jsonl --cold-cases 20 --output reports/review-scale/cold-timing.json
python3.14 -m unittest discover -s tests
python3.14 -m compileall -q address_segmentation tools benchmarks tests
git diff --check
```

Before/after evaluation used the baseline commit recorded in the evidence JSON
in an isolated scratch directory, with identical prepared dictionaries and cases.
The user and scale benchmarks share the recorded final matcher source hash.
The JSONL CLI also produced all 1,000 reviewed results correctly from an external
working directory with a dictionary path containing spaces and empty stderr.
Generated full reports remain under ignored `reports/`; compact reviewed evidence
is retained in `cold-start-results.json`; historical accuracy and warm diagnostics
remain in `user-dataset-results.json`. Omit `--cold-cases 20` (or use
`--cold-cases 0`) for full cold-process coverage of every case.
