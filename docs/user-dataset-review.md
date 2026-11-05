# User dataset review — 2026-11-05

The matcher now passes all 1,000 reviewed user cases and all 1,000 seeded scale
cases. On the unchanged original user expectations it improves from 233/1,000 to
994/1,000, with 761 gains and zero regressions. The remaining six differences are
irreducible accent collisions covered by the documented null-on-ambiguity policy.
All 144 tests and compilation checks pass on CPython 3.14.4.

This review uses the authoritative [requirements](address-segmentation-plan.md)
and [architecture](architecture.md). [Machine-readable evidence](user-dataset-results.json)
retains source/dataset hashes, baseline identity, metric denominators, timing
phases, memory and the long-input probes. These measurements supersede earlier
stage measurements for the updated implementation; historical evidence is retained.

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

Latency was measured sequentially with tracing disabled, five fresh worker
processes, ten warmup calls, three seeded repetitions per case and no prediction
cache: 15,000 warm requests per dataset. Memory ran in a separate process.

| Measurement | User fixture | Scale fixture | Request target |
| --- | --- | --- | --- |
| Warm mean | 0.107 ms | 0.347 ms | ≤ 10 ms |
| Warm p95 | 0.136 ms | 1.160 ms | — |
| Maximum, first or warm request | 0.285 ms | 3.106 ms | ≤ 100 ms |
| Mean index preparation | 22.8 ms | 1,534.0 ms | Timing scope unresolved |
| Full JSONL process, all 1,000 requests | 199.9 ms | 1,949.7 ms | Separate lifecycle measurement |
| Traced Python allocation peak | 2,747,526 bytes | 98,229,609 bytes | No specified memory cap |
| Process peak RSS, including tracing overhead | 29,960 KiB | 275,732 KiB | No specified memory cap |

The scale fixture contains the specified 23,800 streets, 340 wards, 34 provinces
and 1,000 cases. Both measured request workloads meet the official numeric targets
and provisional engineering goals of mean ≤ 5 ms and maximum ≤ 50 ms locally.

Additional single-process probes repeated `qzxv `, `Lê Lợi, ` and `Hòa Bình, `
20, 200 and 1,000 times, with noisy suffixes on the two longest named inputs.
Five timed calls per input covered 100–10,004 characters; the largest observed
request was **40.150 ms**. Before the scan fixes, exploratory collision probes
exceeded 250 ms. These long-input probes test maximum latency separately from
the representative dataset averages. Repeated candidates may intentionally
trigger conservative top-K abstention, which remains visible in traces.

These are local development measurements. **Official timing compliance remains
unverified**: the instructor runtime, maximum input length and initialization
scope are unknown. Scale index preparation alone exceeds 100 ms if charged to
each request. No input is silently truncated, and no universal time guarantee is
made for unlimited input. The implementation remains offline and standard-library
Python; official protocol integration and release packaging remain pending.

## Reproduction

Run from the repository root, with timing commands sequentially:

```sh
python3.14 -m tools.prepare_dataset --source-dir testcases/1 --review testcases/1/review.json --output-dir reports/user-1
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/raw-cases.jsonl --output reports/user-1/raw-evaluation.json
python3.14 -m tools.evaluate --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --output reports/user-1/reviewed-evaluation.json
python3.14 -m tools.benchmark --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --output reports/user-1/timing.json
python3.14 -m benchmarks.generate --output-dir reports/review-scale --seed 42
python3.14 -m tools.evaluate --dictionary reports/review-scale/dictionary.json --cases reports/review-scale/cases.jsonl --output reports/review-scale/evaluation.json
python3.14 -m tools.benchmark --dictionary reports/review-scale/dictionary.json --cases reports/review-scale/cases.jsonl --output reports/review-scale/timing.json
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
is retained here and in `user-dataset-results.json`.
