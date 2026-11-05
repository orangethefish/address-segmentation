# Cold-start construction review — 2026-11-05

This review describes the build-at-launch implementation. The dictionary has
since been confirmed fixed before submission; the implemented
[prepared-dictionary path](prepared-dictionary.md) and its measurements supersede
the conditional next-design discussion below. The original CLI measurements
remain valid for its build-at-launch lifecycle.

The runtime still fails the requested **fresh process per address** interpretation
of the 10 ms mean and 100 ms maximum limits. Removing repeated normalization
substantially improves construction, but changing indexing alone cannot bring
this entry point below 10 ms: importing the CLI without loading a dictionary or
matching anything already averages 36.302 ms on this machine.

The instructor's actual lifecycle, Python environment, dictionary delivery and
input protocol remain unverified. This review uses the user's conservative
timing assumption throughout; it does not change the grading scope to make a
result pass.

## What is timed

The existing corrected `tools.benchmark` runs the actual CLI in a fresh Python
process for each sampled address. Its parent timer starts before process launch
and stops upon receipt of the complete stdout result line. Startup, imports,
dictionary/config I/O, validation, construction, input JSON parsing, matching,
JSON serialization and stdout transfer/flush are included. There is no prediction
file. Report writes and output scoring happen after timing. Launch-to-exit is
also reported, separately. Changing the output destination cannot remove
dictionary construction or interpreter startup.

Warm `identify` timings and elapsed batch time divided by case count remain
diagnostics. They cannot establish compliance for independent script launches.
Fresh processes do not clear OS page caches or Python bytecode caches. Parent
reader scheduling is included; these measurements model piped console capture,
not terminal rendering.

## Implemented changes

Profiling the original construction found 48,348 calls to `tokenize_label` for
24,174 labels, and 508,083 calls to `fold_accents`. Profiling adds substantial
overhead; its durations are not used as request latency measurements.

- Validate and retain each label's normalized forms in one pass, rather than
  tokenizing again while constructing entries.
- Share normalized word forms in a dictionary local to one catalog build.
  Repeated name words reuse normalization. The cache is discarded after
  construction; it stores neither predictions nor state between launches.
- Scan dictionary token boundaries without allocating address `Token` objects
  and source-offset data that catalog construction does not use. Address and
  label normalization share the same boundary scanner, including combining marks
  and slash-connected numbers.
- Build typing/compact lookup keys from the catalog's existing folded words.

All recovery features remain enabled by default. Canonical labels, normalized
collisions, deterministic IDs, administrative types and ambiguity handling are
preserved. Tests cover NFD, other Unicode combining marks, repeated words,
canonical collisions, duplicates, validation and numeric exclusions.

## Measurements

Final measurements and validation are recorded in
[index-construction-results.json](index-construction-results.json). The full
reports under ignored `reports/index-review/` retain warm diagnostic records.

| Configuration / dataset | Cold mean ms | Observed max ms | Mean / max gates |
| --- | ---: | ---: | --- |
| Before, scale (historical) | 1797.262 | 2537.044 | FAIL / FAIL |
| Optimized, scale | 610.325 | 1378.699 | FAIL / FAIL |
| Optimized, scale, recovery disabled | 310.642 | 694.498 | FAIL / FAIL |
| Optimized, user | 57.325 | 98.321 | FAIL / PASS |

Scale index preparation falls from **1647.994 ms** to **555.178 ms** in the independent diagnostic workers. It is already included in the cold timing above.

The optimized scale profile makes 24,174 label-normalization calls and only 431 accent-folding calls during construction, compared with 48,348 and 508,083 respectively before optimization. Most remaining construction work creates catalog entries, exact maps, target forms and postings.

A local full-object serialization experiment produced a **14,319,269-byte** artifact. Loading it in a fresh Python process, identifying one address and emitting stdout averaged **310.834 ms**, with an observed maximum of **367.612 ms** (20 sampled cases, one round). Offline construction alone does not eliminate object reconstruction cost.

Thirty fresh-process probes each gave these launch-to-stdout means: constant output without package imports **9.856 ms**, package import plus constant output **30.453 ms**, CLI import plus constant output **36.302 ms**. They perform no dictionary or matching work and are startup references, not alternative solutions.

All 100 cold predictions and all five 1,000-address batch rounds match the reviewed expectations for both optimized datasets. Complete accuracy remains **1,000/1,000 reviewed user**, **994/1,000 original user**, **1,000/1,000 scale**, **15/15 foundation**, **14/14 recovery**, and **36/36 typing**. The six original-user differences remain the documented accent collisions.

Recovery-disabled matching also passes these user and scale cases, but drops the recovery controls to **5/14** and typing controls to **14/36**. Those datasets alone cannot justify removing recovery. All **153 tests**, compilation and whitespace checks pass.

Scale Python allocation peak is **79,636,248 bytes**; process peak RSS is **228,956 KiB**, including allocation-tracing overhead. Neither is measured during latency runs.

The generated scale dictionary contains 23,800 streets, 340 wards and 34
provinces, for 24,174 labels. It is a scale fixture, not the upcoming official
dictionary. Its repeated vocabulary helps the word cache; the same speedup is
not guaranteed for a dictionary with mostly unique words.

Cold timing uses 20 seeded uniformly sampled addresses, repeated in five rounds
(100 fresh processes, **20 unique cases**), without cold-process warmups. A
sampled maximum is not a guarantee for all inputs. The earlier baseline is
retained in `cold-start-results.json`; before/after runs occurred at different
times on the same machine, so the improvement is approximate, not a controlled
hardware-independent ratio.

## What can meet the limits

There are two separate costs to address: dictionary preparation and Python
startup. An indexing change can reduce the former; it does not remove the
latter. The empty-interpreter probe is a local reference, not a proof that every
possible Python solution on every grader machine must exceed 10 ms.

**If the dictionary is fixed before submission and prepared data are accepted,**
the strongest next design is an offline dictionary compiler plus a compact,
queryable artifact. Store collision-safe canonical IDs, normalized full/body
keys, compact/typing keys and recovery postings. At runtime, look up only the
keys/postings required for the address, instead of recreating every Python entry
and postings tuple. Sorted tables with offsets or a read-only SQLite database
are candidate storage formats. These are designs to benchmark, not measured
solutions or promises of compliance. A minimal runtime entry point would also
need to reduce the current import cost.

Precomputation must be dictionary-derived, independent of test addresses, and
included reproducibly in the submitted data. Artifact identity must cover the
dictionary, normalization/schema version and enabled features. The loader must
reject stale/incompatible artifacts. If preprocessing is part of the grader's
timed run, its cost must remain in the total.

**If each run receives a new dictionary,** an artifact prepared for another
dictionary is unusable. The current build-per-run architecture is not viable at
the measured scale. A much smaller preparation path and runtime would need to
be developed and measured against the actual protocol; the current results do
not establish that such a redesign can meet 10 ms. Confirming the grader's
lifecycle is therefore a material engineering decision, not a justification for
excluding costs from the current benchmark.

## Should indexing be replaced with another feature?

Indexing is an implementation technique; typo recovery, compact-word matching,
typing recovery and ambiguity-safe selection are user-visible capabilities.
Removing an index does not by itself replace any of those capabilities.

The measured exact-only constructor still fails the cold limits. Brute-force
edit distance against 24,000 labels would also discard the benefit of bounded
shortlists. Deferring large index builds until the first noisy input can improve
typical exact requests, but that noisy request still pays the build cost and
does not solve the peak limit.

A lighter alternative worth testing is input-side bounded edit/Telex hypothesis
generation, looked up against a prepared exact lexicon, with length/character
buckets and thresholded verification for the remaining candidates. That could
replace eager trigram/typing postings, but still needs a searchable dictionary,
careful edit bounds, collision preservation and recall/negative-control tests.
It is a tradeoff in preparation, query work and recovery coverage, not a free
replacement. It is not implemented here because the measured startup floor
already prevents this entry point from meeting the mean target.

Keep the current matching capabilities and construction improvements. Prioritize
the grading lifecycle and dictionary-preparation contract, then prototype a
compact queryable dictionary and slim runtime if the contract permits them.
Do not claim the current implementation passes either cold limit.

## Reproduction

Run timing sequentially, without concurrent tests, profilers or other benchmarks:

```sh
python3.14 -m benchmarks.generate --output-dir reports/scale --seed 42
python3.14 -m tools.benchmark --dictionary reports/scale/dictionary.json --cases reports/scale/cases.jsonl --cold-cases 20 --processes 5 --output reports/index-review/optimized-scale-timing.json
python3.14 -m tools.benchmark --dictionary reports/user-1/dictionary.json --cases reports/user-1/cases.jsonl --cold-cases 20 --processes 5 --output reports/index-review/optimized-user-timing.json
```

The `reports/user-1` inputs come from the documented `tools.prepare_dataset`
command in the README. For the exact-only comparison, write a configuration
with `enable_fuzzy`, `enable_compact`, and `enable_typing` all `false`, and supply
it through `--config`. This is an ablation, not the default configuration.

The startup and local serialization probes are retained as source alongside
their raw observations in `index-construction-results.json`. The serialization
probe uses only its own locally generated trusted artifact; it is not a
supported index format or a submission loader.
