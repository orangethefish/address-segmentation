# Stage E completion evidence

Stages D and E are complete against controlled fixtures on CPython 3.14.4. All 128 tests pass. Compilation and the JSONL protocol smoke test pass, including a different working directory and a dictionary path containing spaces. Machine-readable measurements, configurations, dataset hashes, source hashes, recall and case-level gains/regressions are recorded in [stage-results.json](stage-results.json).

## Reviewed changes

The inherited stage D work now passes its recovery tests and the evaluator's strict case schema. Removed a call-stack-dependent configuration default; feature defaults are ordinary deterministic booleans. The verification-budget test now supplies two actual competing candidates, so its exhaustion assertion exercises truncated work rather than assuming a one-candidate shortlist must exhaust the budget.

Stage D uses rare-first trigram postings and a short-name fallback, with thresholded optimal string alignment distance. Exact prefixes remain eligible for replacement by a verified longer component. Numeric names remain exact only.

Stage E adds two independent default-enabled features:

- `enable_compact`: dictionary-key lookup for joined/split whitespace, preserving composed accent evidence and collisions. Internal punctuation and numeric separators are not removed by this path.
- `enable_typing`: dictionary-constrained Telex/VNI shape and terminal-tone hypotheses. `cungx` and `cung4` support `cũng`; explicit tone evidence distinguishes `cùng`. Mixed `Đuơcj`, raw `dduowcj`, and VNI `du7o7c5` support `được`. Existing folded/fuzzy paths cover `Duợc` and `Ngươiư`.

Literal dictionary spellings compete normally and keep stronger exact evidence. An explicit residual tone cannot be silently discarded by general edit recovery to select a differently toned label. No terminal-`l` discount or keyboard-neighbor weighting is enabled. Both features also run independently when `enable_fuzzy` is false.

Recovery paths share postings and verification limits. The candidate shortlist allowance is split conservatively between fuzzy and typing paths; the reported candidate count sums their high-water marks, an upper bound on combined candidates per span. The span counter covers the same deterministically extracted hypotheses used by both paths. Exhaustion prevents an affected recovered label or suspicious exact prefix from acquiring false uniqueness; independent exact fields survive. Duplicate paths for the same entry/span contribute one score.

## Feature ablation

The 36-case typing fixture contains positive examples, literal controls, collisions, numeric controls, unrelated text, tone mismatches, combined fields and reordered components. Every enabled rule has explicit controls. Synthetic labels such as `cũng` and `được` illustrate dictionary-constrained spelling intentions; they are not geographic assertions.

Each row uses the same dictionary and cases. `compact` and `typing` each add only that feature to stage D; `stage_e` enables both. False non-null counts use all 65 expected-null fields. Timing has tracing disabled, five fresh processes, ten warmup calls and three seeded repetitions per case. Memory is measured separately.

| Configuration | Complete correct | False non-null | Warm mean ms | Observed max ms |
| --- | --- | --- | --- | --- |
| exact | 14/36 | 0/65 | 0.040 | 0.107 |
| stage_d | 21/36 | 1/65 | 0.107 | 0.255 |
| compact | 23/36 | 1/65 | 0.173 | 0.427 |
| typing | 34/36 | 0/65 | 0.192 | 0.468 |
| stage_e | 36/36 | 0/65 | 0.226 | 0.509 |

Both features have positive isolated gains: compact adds two correct cases, typing adds thirteen, and the combination adds fifteen over stage D, with zero regressions. Typing also removes stage D's one false non-null assignment. The original fixture stays 15/15; the general recovery fixture stays 14/14, compared with 5/14 under exact-only matching. There are no new false non-null assignments on either fixture.

## Scale and limits

The seeded scale fixture has 23,800 streets, 340 wards, 34 provinces and 1,000 cases, including shared prefixes, collisions, numeric names, misses, repeated tokens and long adversarial inputs. Stage E's warmed mean is **0.402 ms** and observed maximum is **3.971 ms**, within the local request targets. Index preparation averages **1640.6 ms** and is measured separately. The Python allocation peak is **98,207,583 bytes**; peak process RSS is **275,456 KiB**, including allocation-tracing overhead. These are different memory measurements.

The typing fixture's warmed mean is 0.226 ms, with an observed maximum of 0.509 ms. These measurements establish local fixture behavior, not instructor-data accuracy or official timing compliance. Initialization scope, input length limits and grader lifecycle remain unknown.

Typing recovery supports selected shape keys and syllable-final tone keys with word boundaries; it is not a full Telex/VNI input engine. Arbitrary joined raw typing streams, multiple mixed errors and unmarked multi-component spans may remain unrecoverable. Short ordinary fuzzy names and numeric repairs deliberately abstain. Stages F and G remain pending.

## Reproduction

Run from the repository root:

```sh
python3.14 -m unittest discover -s tests -v
python3.14 -m compileall -q address_segmentation tools benchmarks tests
python3.14 -m tools.ablate --dictionary tests/fixtures/typing-dictionary.json --cases tests/fixtures/typing-cases.jsonl --output-dir reports/stage-e --timing
python3.14 -m tools.ablate --dictionary tests/fixtures/recovery-dictionary.json --cases tests/fixtures/recovery-cases.jsonl --output-dir reports/stage-d
python3.14 -m tools.ablate --dictionary tests/fixtures/dictionary.json --cases tests/fixtures/cases.jsonl --output-dir reports/regression
python3.14 -m benchmarks.generate --output-dir reports/scale --seed 42
python3.14 -m tools.benchmark --dictionary reports/scale/dictionary.json --cases reports/scale/cases.jsonl --config reports/stage-e/stage_d-config.json --output reports/scale-stage-d-timing.json
python3.14 -m tools.benchmark --dictionary reports/scale/dictionary.json --cases reports/scale/cases.jsonl --output reports/scale-stage-e-timing.json
```

Run timing commands sequentially without concurrent tests or other benchmarks. Full generated reports remain under ignored `reports/`; reviewed evidence is retained in `docs/`.
