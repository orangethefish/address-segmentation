# Teacher v2 implementation plan

- Date: 2026-11-19
- Status: Implemented on `main` and ported to `feature/implement-with-trie-and-dp`
- Flat baseline: `17a3316`
- Legacy hierarchy audit source: `39a76e3`

Implementation and validation are recorded in the [v2 results](teacher-v2-results.md).

## 1. Objective and agreed direction

Adapt the existing Vietnamese address matcher to the teacher's v2 notebook,
improve its handling of joined administrative markers, and produce a tested
submission in the required notebook and Python formats.

The teacher confirms that addresses form geographically valid combinations. The
submission interface provides three independent name lists. **Implement v2 on
`main` and treat the supplied dataset as flat.** Build the matcher from those
lists and use text evidence and source spans to resolve competing matches.
Use public answers for offline evaluation. The legacy relationship data and
hierarchy prototype live on `feat/support-for-hierachy`.

## 2. Sources to read and preserve

- [`teacher-info/Quick start_v2.docx`](../teacher-info/Quick%20start_v2.docx):
  submission instructions and teacher Q&A. Review its text and screenshots.
- [`teacher-info/GettingStarted_v2.ipynb`](../teacher-info/GettingStarted_v2.ipynb):
  executable interface, cell layout, scoring, and timer placement.
- [`teacher-info/v2_2_level_address/`](../teacher-info/v2_2_level_address/):
  `public.json`, `public_list_province.txt`, `public_list_ward.txt`, and
  `public_list_street.txt`.
- `docs/hierarchy-architecture.md` and `testcases/1/address_hierarchy.json` at
  `39a76e3` are historical sources for the audit in section 4.
- [`docs/address-segmentation-plan.md`](address-segmentation-plan.md),
  [`docs/architecture.md`](architecture.md), and [`README.md`](../README.md):
  current behavior and earlier grading assumptions that need reconciliation.

Keep the original files under `teacher-info/` byte for byte. The implementation
extends the existing matcher on `main`.

The executable v2 notebook takes precedence over older screenshots when their
interfaces disagree. One screenshot shows a parameterless constructor, whereas
the actual notebook passes three lists.

## 3. Confirmed template contract

```python
class Solution:
    def __init__(
        self,
        list_province: list[str],
        list_ward: list[str],
        list_street: list[str],
    ):
        # Prepare indexes from these arguments here.
        ...

    def process(self, s: str):
        return {
            "province": ...,  # Canonical supplied label, or None when unsupported.
            "ward": ...,
            "street": ...,
        }
```

The signature and three result fields are confirmed. Continuing the existing
null policy is a project decision: the public examples do not establish the
teacher's missing-component or ambiguity policy.

| Area | Confirmed behavior and implementation consequence |
| --- | --- |
| Lifecycle | The grader creates one `Solution`, then calls `process()` for each address. Build indexes once in `__init__()`. |
| Input data | Lists are passed into the constructor. Private grading data may differ; never bind the solution to the public inventories. |
| Timer | `perf_counter_ns()` surrounds only `solution.process(address)`. Imports, downloads, dictionary reads, construction, scoring, and Excel output are outside that interval. |
| Scoring | Each correctly matched field earns one point. The score is `round(correct_fields / (3 * case_count) * 10, 2)`. Complete-address accuracy remains a useful secondary diagnostic. |
| Equality | `_judge_normalize()` currently returns its input unchanged. Emit exact dictionary labels; do not add prefixes or replace unconventional spellings. |
| Returned object | The scorer adds normalized keys to the returned dictionary. Each call must return a fresh dictionary with exactly the three required keys initially. |
| Student cells | Team name; optional data downloads; optional dependency installation; imports; `Solution`. Student-created methods/functions belong under `Solution`. Preparation belongs in `__init__()`. |
| Grader cells | The last two cells are grading infrastructure and may be replaced. The solution must not rely on their public answers, globals, or execution before initialization. Preserve their supplied content in the submission template. |
| Extra artifacts | Own database/index downloads are permitted through `gdown` in the download cell. Shared files must remain available, and mounting the student's Drive is forbidden. Prefer no additional runtime artifacts for this adaptation. |
| Delivery | Submit `Group_xx/Group_xx.ipynb` and `Group_xx/Group_xx.py`. Clear notebook outputs before submission. |

The supplied notebook output shows Python 3.12. This is evidence for a
compatibility target, not a guarantee of the current or final Colab version.
The existing core matcher was exercised successfully under local Python 3.12
and 3.14. Keep the matching runtime standard-library-only; the teacher's scoring
infrastructure has its own pandas/Excel dependencies.

## 4. Hierarchy audit and decision

The existing hierarchy has 34 provinces, 2,380 province–ward occurrences, and
166,600 complete paths. Each province contains 70 ward occurrences and each
occurrence contains 70 streets. It was previously validated against the old
development dataset; that does not establish suitability for v2.

Coverage after field-aware removal of administrative prefixes, Unicode NFC
normalization, and case folding:

| v2 item | Covered by existing hierarchy |
| --- | ---: |
| Province labels | 32 / 32 |
| Ward labels | 27 / 125 |
| Street labels | 74 / 409 |
| Public examples' province–ward pairs | 19 / 200 |
| Public examples' complete paths | 2 / 200 |

The 200 expected triples break down as follows:

- 185 contain at least one name absent from the hierarchy.
- 9 have all names present but lack the expected province–ward relationship.
- 4 have the province–ward relationship but lack the expected street under it.
- 2 have a complete matching path (public rows 3 and 182, one-based).

Examples:

- Public row 1: `Phạm Viết Chánh / Cẩm Lệ / Đà Nẵng`; both the street and ward
  are absent.
- Public row 39: `Âu Cơ / Tây Hồ / Hà Nội`; all names exist, but the expected
  ward–province relationship is absent.
- Public row 67: `Điện Biên Phủ / Thanh Khê / Đà Nẵng`; the ward–province pair
  exists, but that street is absent under the pair.

Even accent folding raises inventory coverage only to 28 wards and 78 streets;
complete-path coverage remains 2/200. This is not merely a prefix or spelling
conversion problem. Hard filtering would rule out the expected complete answer
on 198 cases. Relationship bonuses also lack validation for v2.

**Decision:** treat the v2 lists as flat dictionaries on `main`. The audit
describes historical data from the separate hierarchy branch.

## 5. Reproduced baseline

The v2 files contain 200 distinct addresses, 32 province labels, 125 ward labels,
and 409 street labels. All expected values belong to their corresponding lists;
none of the 600 expected fields is null.

The unchanged matcher, using the v2 lists, produced the same predictions under
Python 3.12.3 and 3.14.4:

| Accuracy metric | Result |
| --- | ---: |
| Streets | 200 / 200 |
| Wards | 184 / 200 |
| Provinces | 179 / 200 |
| Total correct fields | 563 / 600 |
| Teacher-equivalent score | 9.38 / 10 |
| Complete addresses | 167 / 200 |

All 37 wrong fields occur with joined administrative markers. Traces locate the
first loss as follows:

| Field | Retrieval | Verification | Selection |
| --- | ---: | ---: | ---: |
| Ward | 13 | 3 | 0 |
| Province | 15 | 5 | 1 |

Examples include `PhườngHòa Cường`, `Thành phốCần Thơ`, `TPĐà Nẵng`,
`PBồ Đề`, and `TKhánh Hòa`. Do not assume a retrieval-only fix resolves every
failure; follow candidates through verification and selection.

Five full sequential passes were measured per interpreter, constructing a new
matcher for each pass, without an explicit request warmup. Only `identify()`
was timed for each address; these are core-matcher measurements, not a tested
submission adapter.

| Runtime | Round mean range | Maximum across all five passes | Construction range |
| --- | ---: | ---: | ---: |
| Python 3.12.3 | 0.465–0.487 ms | 1.542 ms | 18.28–19.42 ms |
| Python 3.14.4 | 0.206–0.241 ms | 1.034 ms | 7.28–8.24 ms |

The review checkout (`39a76e3`) passed all 168 tests on Python 3.14, including
five hierarchy prototype tests. The legacy prepared-bundle path has
version-specific behavior. See the results for the validation on `main`.

Local review artifacts, ignored by Git:

- `reports/teacher-v2-review/baseline-python3.12.json`
- `reports/teacher-v2-review/baseline-python3.14.json`
- `reports/teacher-v2-review/hierarchy-compatibility.json`

They contain detailed timings, failures, and provenance. They may not accompany
a fresh checkout; regenerate evidence when absent. The figures above remain a
record of the reviewed baseline. No held-out accuracy claim is made: the public
data and its failures have already been inspected.

## 6. Implementation work packages

### A. Official data adapter and grader-equivalent evaluation

Extend `tools/prepare_dataset.py` or add a clearly named v2 adapter that accepts
the actual teacher filenames. Avoid copying/renaming raw files merely to satisfy
the old adapter's `all_raw.json` and `list_*.txt` assumptions.

Requirements:

- Preserve canonical labels and original public expectations exactly.
- Validate schema, label membership, and duplicate/conflicting addresses.
- Record raw source hashes, source identity, and dictionary counts.
- Identify this dataset as teacher-provided; do not reuse the old adapter's
  unconditional `official_data: False` metadata.
- Keep the old development dataset and its six reviewed ambiguity expectations
  separate. Do not apply `testcases/1/review.json` to v2.
- Report correct fields, score out of ten, per-field accuracy, complete-address
  accuracy, exceptions, and case-level gains/regressions.
- Keep expected answers entirely outside the runtime solution.

Likely files: `tools/prepare_dataset.py`, `tools/evaluate.py`,
`tools/datasets.py`, and focused adapter/evaluation tests. A separate adapter is
acceptable if it better preserves existing command behavior.

Exit condition: the unmodified matcher reproduces 563/600 correct fields using
the new evaluation path.

### B. Dictionary-constrained joined-marker recognition

Inspect `address_segmentation/retrieve.py`, `normalize.py`, `catalog.py`,
`typing.py`, and the pipeline/selector before choosing the smallest coherent
change. Model an attached administrative marker as an interpretation supported
by a field and a dictionary name, rather than a destructive global rewrite.

Required behavior:

- Recognize attached long and abbreviated markers, including multiword marker
  endings attached to names (`Thành phốCần Thơ`).
- Support accented/case variants through the existing normalization rules.
- Preserve original source offsets and span-conflict rules.
- Preserve literal name interpretations when a leading letter could be either
  part of a name or a marker.
- Do not freely strip `P`, `T`, or `X` from arbitrary words.
- Avoid charging a recognized administrative marker as a typo inside a name.
- Preserve numeric street/house-number distinctions and administrative-type
  checks where the canonical dictionary actually supplies a type.
- Keep exact, fuzzy, compact, and typing behavior consistent; retain bounded
  recovery work and existing ambiguity safeguards.

Use generalized tests with names beyond the public failures. Do not add
address-to-answer lookups, row-specific rules, or public-answer-derived aliases.

Exit condition: reproduce and resolve the diagnosed marker failures where
unambiguous, with no unexplained regressions. Target all 600 public fields, but
do not weaken general correctness merely to reach that number. Document any
remaining ambiguity or failure.

### C. Teacher-compatible source and notebook export

Add a reproducible export/build path, for example `tools/build_submission.py`.
Keep the maintained matching implementation as the source of truth, and make
the generated submission reviewable.

- Produce the exact `Solution` constructor and `process()` interface.
- Build indexes from constructor lists, not cached public data, in `__init__()`.
- Put student algorithm functions/methods under `Solution`, following the
  teacher's instruction. A thin wrapper importing this repository is not a
  self-contained submission. Do not hide module-level implementation behind
  dynamic execution or encoded source to evade the class rule.
- Prefer a deterministic source transformation/export over an independently
  maintained second algorithm. If structural refactoring is necessary, verify
  prediction parity before and after it.
- Keep imports in the imports cell and preparation in the constructor. Do not
  move setup into the first timed request.
- Preserve the notebook's cell roles and final two grader cells.
- Generate both `.ipynb` and `.py` from consistent source. Explicitly handle
  notebook shell commands in the Python export so they do not become invalid
  Python syntax. Document whether full exported grading execution requires
  IPython; verify the solution source is independently importable/testable.
- Clear outputs and execution counts in the deliverable notebook.
- Avoid extra downloads and dependencies when the standard-library matcher is
  sufficient. Remove unnecessary student-cell dependency installation, while
  preserving teacher-owned grading cells.
- Verify operation outside the repository and without its package on the path.

The actual group name is unknown. Make it a build parameter. Use an explicitly
marked `Group_xx` draft only if necessary; do not silently adopt the template's
`Group_01` as the user's identity. Final naming requires the real group name.

Exit condition: exported Python and notebook solution code produce the same
predictions as the package and satisfy the structural contract.

### D. Benchmark the actual submission interface

Add a notebook-contract benchmark mode or a focused harness such as
`tools/benchmark_solution.py`. Keep historical cold-process benchmark behavior
explicitly separate; do not relabel old reports as new-contract evidence.

Primary protocol:

1. Import the exported solution and construct it outside the request timer.
2. Call `process()` once per case in the supplied order, timing the entire call
   with `perf_counter_ns()`. Include the first call; perform no request warmup.
3. Capture predictions and failures outside the timer. A failed call is a
   release failure, not a missing timing sample to silently discard.
4. Repeat five full passes, constructing a new solution for each pass. Report
   per-pass results and the overall observed maximum. Use additional fresh
   interpreter runs to assess variation; initialization remains separately timed.
5. Record mean, p50, p95, p99, maximum, first call, per-case timings, slowest
   inputs, source/data hashes, interpreter, and environment.

The retained project limits are mean **≤10 ms** and maximum **≤100 ms**, unless
new teacher guidance supersedes them. The v2 scorer displays rounded timing and
does not itself enforce those limits. Compare unrounded measurements, not the
displayed values. Local public-data success does not certify all private inputs
or performance on Colab.

Measure construction time and memory separately. Run larger dictionaries near
the existing approximately 23,800-street scale target and long/noisy inputs as
separate stress workloads. Do not silently truncate inputs to obtain a pass.
Profile only if new measurements justify optimization; the current bottleneck
is correctness/packaging, not interpreter startup.

### E. Documentation and release verification

Update `README.md` and the relevant architecture/plan documents to distinguish:

- the official v2 lifecycle and interface;
- old fresh-process assumptions and historical prepared-bundle measurements;
- the flat-list contract for v2 on `main`;
- public-data scores versus broader regression and stress evidence;
- local validation versus a verified clean Colab run.

Do not remove useful legacy compiler functionality just because it is no longer
the preferred submission path. It is not needed for the new timed boundary and
must not impose a fixed dictionary or Python-minor-version dependency on v2.

Produce a reviewable final report with score changes, regressions, timing,
runtime versions, reproduction commands, artifact paths, and remaining limits.
Do not publish to Drive, submit to LMS, or contact the teacher automatically.

## 7. Test matrix and release gates

| Area | Required checks |
| --- | --- |
| Existing behavior | Run the current Python 3.14 suite; preserve old dataset semantics and prepared-bundle tests. |
| Marker changes | Joined/separated markers; long/short forms; accents and case; multiword markers; literal names beginning with marker-like letters; multiple plausible interpretations. |
| Numeric and span behavior | Numeric street names, house numbers, nested slash numbers, punctuation, shared names across fields, overlapping candidates, and original offsets. |
| Unsupported/ambiguous input | Missing fields, empty/noise input, colliding names, and contradictory evidence. Public data contains no null expectations, so use independent fixtures. |
| Runtime state | Multiple instances with different dictionaries, reordered/duplicate entries, repeated calls, input-list integrity, and fresh result dictionaries after grader mutation. |
| Export parity | Package, generated Python, and notebook solution code agree on fixtures and all public cases. |
| Structural compliance | Exact constructor signature, methods under `Solution`, correct cell roles, no repository imports or public-answer access, valid export syntax, cleared deliverable outputs. |
| Compatibility | Execute submission/core tests under Python 3.12 and 3.14; isolate any historical prepared-format limitations rather than masking failures. |
| Private-grader simulation | Replace public data/lists in a local harness; verify no dependency on fixed filenames, labels, public scorer globals, or prior notebook state inside `Solution`. |
| Timing | Benchmark exported `process()` with the protocol above, plus separate stress workloads. |
| Clean environment | Run from a directory outside the repository. Execute the complete notebook in a fresh Colab session when access is available. |

A local simulated notebook run is not proof of a successful real Colab run.
If interactive Colab access is unavailable, finish all independent work and
provide exact validation steps, clearly identifying that remaining check.

Avoid broad test churn. Add tests for meaningful contracts and failure modes;
run the appropriate suite after each coherent change, then complete the release
checks once the implementation stabilizes.

## 8. Implementation sequence and remaining release details

Implementation sequence:

1. Reproduce baseline and introduce the official adapter/scoring path.
2. Implement marker recognition and verify regressions.
3. Build the `Solution` export and prove package/export parity.
4. Benchmark, run compatibility/stress checks, and address measured failures.
5. Update documentation, generate deliverables, and perform clean-session checks.

The assigned group name, final grading interpreter, and teacher policy for
unmentioned or ambiguous components are still unconfirmed. The checked-in
`Group_01` files are drafts. Use the assigned name for the final build and
confirm it in a clean Colab run. See the [v2 results](teacher-v2-results.md) for
the validation commands and current timing evidence.

## 9. Teacher v2 on the trie and DP branch

Both backends now implement the flat-list contract on
`feature/implement-with-trie-and-dp`. See the
[v2 backend comparison](teacher-v2-results.md#trie-and-dp-comparison) for the
post-port results.

The teacher's [quick start](../teacher-info/Quick%20start_v2.docx) confirms the
submission layout and valid geographic combinations. The
[executable notebook](../teacher-info/GettingStarted_v2.ipynb) defines the
three-list constructor, exact-label scoring, and `process()` timer. Follow the
notebook's constructor where the quick-start screenshot shows an older form.

### Measured starting point

All three variants ran as standalone Python exports on the original 200
teacher public addresses. The two pre-port exports use the matcher at
`afe07a9`, packaged for this measurement with the exporter from `e884ae7`.
Their public predictions matched the branch package. The v2 indexed
reference is the checked-in export at `e884ae7`.

| Variant | Street / 200 | Ward / 200 | Province / 200 | Correct fields / 600 | Score / 10 | Complete / 200 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Branch indexed before v2 | 200 | 184 | 179 | 563 | 9.38 | 167 |
| Branch trie + DP before v2 | 200 | 187 | 180 | 567 | 9.45 | 169 |
| Main indexed with v2 | 200 | 200 | 200 | 600 | 10.00 | 200 |

The pre-port trie recovered four fields across three rows via ordinary fuzzy
matching, with zero regressions against branch indexed. Of its 33 remaining
misses, 29 first lose the expected label at retrieval, three at verification,
and one at selection. The trie retrieval trace already applies OSA distance
and marker filtering, so its stage counts describe this backend's pipeline.
No public case exhausted a trie recovery budget in the pre-port trace.

Each interpreter had two fresh runs per variant with five passes per run.
These are run-level mean ranges and the highest observed per-request maximum,
in milliseconds. All passes had stable predictions, zero exceptions, and met
the project's public timing limits. Peak traced construction allocations were
0.65–0.69 MiB for indexed and 2.82–2.89 MiB for the pre-port trie.

| Variant | Python 3.12.3 mean | 3.12.3 max | Python 3.14.4 mean | 3.14.4 max |
| --- | ---: | ---: | ---: | ---: |
| Branch indexed before v2 | 0.511–0.586 | 1.965 | 0.219–0.245 | 0.695 |
| Branch trie + DP before v2 | 1.076–1.233 | 8.724 | 0.401–0.422 | 2.708 |
| Main indexed with v2 | 0.392–0.439 | 1.569 | 0.180–0.200 | 1.038 |

The trie was slower on the pre-port public comparison. Measure again after
the port, since exact recognition can bypass fuzzy traversal. The runs cover
public input only. Raw timing samples, predictions, failures, and data and
export hashes are in ignored `reports/teacher-v2-trie-dp/export-*.json`.

### Implementation sequence

1. **Integrate v2 in a worktree for the trie branch.** Use `e884ae7` as the v2
   source and keep `indexed` available as the control. A merge preview found
   conflicts in `address_segmentation/approximate.py`,
   `address_segmentation/matcher.py`, and `README.md`. Retain the v2
   tokenizer, joined exact retrieval, shared span extraction and typing rules.
   Keep the branch's trie catalog and indexes, banded OSA traversal, and
   forward/backward interval selection across the eight field masks.
2. **Share the joined-marker acceptance rules.** Compute `span_data` once in
   `AddressMatcher` and pass it to both fuzzy backends and typing recovery.
   Exact and compact/typing lookups can use the existing trie catalog and
   typing-index interfaces. For trie typo search, use the split body text
   with the original source span, enforce the marker's field and type, and
   filter typed entries to their body form for a joined query. Preserve
   literal candidates and apply v2's closer-split guard to ordinary fuzzy
   matches so deletion of a conflicting marker cannot rescue a label.
   Propagate `body_start` for tone checks. Apply these rules within the
   node, terminal, per-span and shared typing budgets. Retain exhaustion
   diagnostics and independent exact evidence. Run DP on original source
   intervals, respecting overlap and label/null ties.
3. **Export both variants with the same notebook contract.** Add a
   `--backend {indexed,trie_dp}` selection to `tools.build_submission` and
   `tools.evaluate_teacher_v2`. Record the backend in evaluation metadata.
   Include `trie.py` after its dependencies and before `matcher.py` in the
   exporter, and select the backend internally in `Solution.__init__`.
   Write the variants to separate output directories using the same group
   name. Prove package, standalone Python and notebook prediction parity,
   including with replacement inventories outside the repository. Preserve
   both grader cells and clear all outputs. In the legacy compiler, write
   indexed fuzzy length metadata only for the indexed backend. The trie
   stores length bounds per node. Run the prepared/source parity tests
   separately on Python 3.14.
4. **Check correctness before ranking performance.** Run the branch's trie
   OSA and DP reference tests, v2 parity checks and the 1,000 reviewed legacy
   cases, including the six ambiguity cases. Run joined and separated marker
   controls on both backends, covering compact input, Telex/VNI, transpositions,
   decomposed accents, wrong administrative types, literal marker-like names,
   numeric streets and bounded-work abstention. Target 600/600 fields and
   200/200 complete public addresses with zero exceptions. Report every
   remaining field difference and where the expected candidate was lost,
   along with false non-null matches on independent cases.
5. **Compare exports from the same integrated revision.** Use
   `tools.benchmark_solution` on Python 3.12 and 3.14, with identical data
   hashes and case order for each workload. Record the revision and each
   export's own hash. Alternate backend order across fresh interpreter runs.
   Use five passes per run, a fresh `Solution` each pass and no request
   warmup. Report public field and complete-address
   gains/regressions, mean/p50/p95/p99/max and first-call latency, construction
   time and peak traced allocations. Repeat with the 23,800-street scale and
   long-noise workloads. Tag slow cases with exact or recovery path and
   budget outcomes using untimed traces. Compare against the pre-port
   baselines above and the indexed v2 control from the same revision. Treat
   the project's 10 ms mean and 100 ms maximum as timing gates and keep
   the final backend choice open until correctness and timing are measured.

### Reproduce the pre-port exports

Run from the repository root at `e884ae7`. The temporary exporter adjustment
keeps the tokenization and house-number helpers used by the older source.

```sh
review_dir="$(mktemp -d)"
git archive afe07a9 | tar -x -C "$review_dir"
mkdir -p "$review_dir/teacher-info"
cp tools/build_submission.py "$review_dir/tools/"
cp teacher-info/GettingStarted_v2.ipynb "$review_dir/teacher-info/"
(cd "$review_dir" && python3.14 - <<'PY'
from tools import build_submission as builder

builder.MODULES = (*builder.MODULES[:-1], "trie", builder.MODULES[-1])
builder.DEVELOPMENT_ONLY -= {"is_base_alnum", "is_house_number_token"}
api = builder.API
for backend in ("indexed", "trie_dp"):
    builder.API = api.replace(
        "provinces=list_province,",
        f'provinces=list_province, config=self.MatcherConfig(backend="{backend}"),',
    )
    builder.build_submission("Group_xx", f"exported/{backend}")
PY
)
benchmark_exports() {
  runtime="$1"
  run="$2"
  shift 2
  for variant in "$@"; do
    case "$variant" in
      main-indexed) submission="Group_01/Group_01.py" ;;
      branch-*) submission="$review_dir/exported/${variant#branch-}/Group_xx/Group_xx.py" ;;
    esac
    "$runtime" -m tools.benchmark_solution --solution "$submission" \
      --output "reports/teacher-v2-trie-dp/reproduced/export-$variant-$runtime-run$run.json"
  done
}
for runtime in python3.12 python3.14; do
  benchmark_exports "$runtime" 1 main-indexed branch-indexed branch-trie_dp
  benchmark_exports "$runtime" 2 branch-trie_dp branch-indexed main-indexed
done
```
