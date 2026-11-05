# Vietnamese Address Identification - Consolidated Technical Plan

**Status:** Authoritative design specification; stages A–E are implemented against controlled fixtures. See [architecture](architecture.md) and [stage E evidence](stage-e.md) for implementation status.
**Working language/runtime:** Pure Python, provisionally CPython 3.14.  
**Primary reference:** Instructor slide deck, *Address Segmentation* (12 pages), plus decisions and examples from our discussion.  
**Last consolidated:** 2026-11-04.

> **Core principle:** Be permissive about how users write an address, but selective about what the system claims to have identified. All non-null outputs must come from the supplied dictionaries.

## 1. Scope and provenance

The task is **dictionary-constrained identification of address components**, not geocoding, general spelling correction, machine-learning-based named entity recognition, or validation against an external geographic database. Given a single free-form input string, return one canonical `street`, `ward`, and `province` where supported; otherwise return `null` for the unsupported field.

To prevent assumptions from becoming accidental requirements, the document distinguishes:

- **[SLIDE]** Explicitly shown or stated in the instructor's PDF.
- **[USER]** An additional requirement or assumption established during our discussion.
- **[DESIGN]** Our selected engineering approach, subject to benchmarking and evaluation.
- **[OPEN]** Not established yet; must be checked when the data, starter code, or grader instructions arrive.

The slide's broader context discusses OCR and inconsistent address representations (pp. 2-5). Our exercise begins **after** the input has become a text string; we are not implementing OCR or image-based address detection (p. 6).

## 2. Requirements and evaluation rules

| Requirement | Source/status | Technical consequence |
|---|---|---|
| Input: one address text string; output: street, ward, province | [SLIDE] pp. 6, 8 | Build a single-address matcher with an exact three-field result. |
| Database acts as a correction reference, even if its content is unconventional | [SLIDE] p. 6 | Preserve database labels exactly; do not override with outside geography. |
| Approximately 34 provinces, 34 x 10 wards, and 34 x 10 x 70 streets (~23,800 street entries) | [SLIDE] p. 7 | Prepare for this scale, but do not assume exact counts or uniqueness. |
| Development set of 1,000 examples, with private testing | [SLIDE] p. 9 | Develop generalizable rules and hold out validation examples. |
| Score weighting: 80% private, 20% public | [SLIDE] p. 11 | Avoid overfitting to public examples. |
| Maximum time for one request <= 0.1 s | [SLIDE] p. 11 | Bound expensive fallback work; test worst cases. |
| Average time per request <= 0.01 s | [SLIDE] p. 11 | Provide an inexpensive fast path for typical addresses. |
| No machine-learning approach; algorithms only | [SLIDE] p. 11 | Deterministic rules, dictionaries, indexes, and string algorithms. |
| Runs on a different machine with no internet; source and related data must be submitted | [SLIDE] p. 11 | Offline, reproducible, self-contained release package. |
| Detailed, understandable README required | [SLIDE] p. 11 | Document environment, interface, setup, and execution. |
| Cheating/copying results in a zero for the whole course | [SLIDE] p. 11 | Write an original implementation and follow all course policies. |
| Course exercise includes a three-week period, one free test, and partial private-data release two days before deadline | [SLIDE] p. 11 | Treat free testing as final validation, not primary debugging. |
| Script must be entirely Python; inability to compile causes immediate failure | [USER] | Treat compilation and packaging as release-blocking gates. |
| CPython 3.14 is the provisional development target | [USER] | Test 3.14 now; re-check when official environment is disclosed. |

**Important distinction:** The slide explicitly says copying or cheating gets a course-wide zero. The immediate-failure interpretation for inability to compile was separately provided by the user; the slide does not explicitly equate every requirement violation with that penalty.

## 3. Input/output contract

### 3.1 Expected result

The screenshot on p. 8 shows a development example with an `address` and a nested `result`. We take the **inner `result` object** as the provisional output of our matcher; the outer sample structure belongs to the dataset/evaluator unless the grader later says otherwise.

Example input:

```text
56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh
```

Expected result:

```json
{
  "street": "Nam Kỳ Khởi Nghĩa",
  "ward": "Phường Bến Thành",
  "province": "Thành phố Hồ Chí Minh"
}
```

In particular, `Phường` and `Thành phố` are retained where they are part of the canonical values; the system must not globally strip or invent administrative prefixes.

### 3.2 Result invariants

- Exactly three keys, always present: `street`, `ward`, `province`.
- Each value is a canonical string from the corresponding dictionary or Python `None` (JSON `null`).
- No unsupported label, invented geographic correction, confidence score, debugging field, or raw user text in the public result unless the official contract asks for it.
- `"null"` and `"None"` are strings and **not** the correct representation of a missing value.
- Use UTF-8 data and `json.dumps(..., ensure_ascii=False)` at a JSON boundary so Vietnamese text remains legible.
- Matching logic accepts one Python string and returns one fresh Python dictionary. I/O protocol and serialization are delegated to a thin adapter.

Proposed empty/meaningless input behavior (`""`, whitespace, `","`, `", , ,"`):

```json
{"street": null, "ward": null, "province": null}
```

This is a **design choice**, consistent with the example of a dictionary-missing field becoming null, not a case explicitly enumerated by the slide.

### 3.3 Preserve canonical labels - the most important rule

The slide (p. 6) shows the same address input with two different correction dictionaries. In the second version, the canonical street is `LTK` instead of `Lý Thường Kiệt`; the ward `Diên Hồng` is missing; and the canonical province is `TP. Hồ Chí Minh`. The expected fields are accordingly:

```json
{"street": "LTK", "ward": null, "province": "TP. Hồ Chí Minh"}
```

Therefore, **normalization and typo repair are lookup aids, never replacement output labels**. Do not return a plausible name absent from the actual supplied dictionary.

## 4. Dataset assumptions and preparation

### 4.1 Flat dictionaries as the default

**[USER, 2026-11-05]** On `feat/support-for-hierachy`,
`testcases/1/address_hierarchy.json` supplies complete, authoritative
relationships. Missing relationships are invalid, unmentioned components remain
null, and conflicting matches yield the strongest consistent subset with nulls
for unresolved fields. The [hierarchy architecture](hierarchy-architecture.md)
supersedes the flat assumption for hierarchy mode and records an isolated
prototype's runtime evidence. Existing flat matching remains a supported mode.

**[DESIGN]** Assume three separate, independently searchable sets of street, ward, and province labels. Page 6 lists these separately. The directory screenshot on p. 9 appears to show `all_raw`, `list_district`, `list_province`, `list_ward`, and `README` - notably **not a clearly named `list_street` file**. It supports separate-file packaging but does **not** prove the delivered schema or geographic relationships.

Without verified links between entries, do not assume a street belongs to exactly one ward or that ward and province membership can be inferred. Geographic links may be added as optional evidence only if supplied and permitted. A street name may occur in multiple wards; preserve such relationships if the eventual schema includes them.

### 4.2 Canonical entries and collision-safe indexes

Give each dictionary entry an internal ID and store its exact canonical label and field type. Build additional matching keys, for example:

- Unicode NFC-normalized form;
- case-insensitive form;
- accent-insensitive form, explicitly mapping `đ` and `Đ` to `d`;
- controlled space-insensitive form;
- aliases and initial-letter representations where justified.

Do **not** overwrite entries when normalized spellings collide. One normalized key must map to **all** candidate IDs. The same text can also occur in different fields (e.g. `Hòa Bình` as a street and province), so field identity and matched span must remain explicit.

Precompute dictionary-side forms and indexes once per process wherever the evaluation protocol permits. Preserve stable behavior regardless of input dictionary order.

### 4.3 Similar public data for early prototyping

Previously identified resources include:

- [vietnamese-provinces-database](https://github.com/thanglequoc/vietnamese-provinces-database): administrative province/ward data; no guarantee that its data match the instructor's street dictionary.
- [vietnam_dataset](https://github.com/thien0291/vietnam_dataset): an older Vietnam address dataset with street names; its vintage makes it unsuitable as an unquestioned authority for current administrative structure.
- [Geofabrik's Vietnam OpenStreetMap extract](https://download.geofabrik.de/asia/vietnam.html): raw map-derived data rather than a ready-to-use noisy-address benchmark.

These are **optional external prototype sources**, not substitutes for the official correction database, and must not silently redefine ground truth. Confirm licensing, permitted external-data use, and administrative versions before packaging anything. Prefer a small, controlled fixture first.

## 5. Proposed algorithmic architecture

```text
ONE-TIME PREPARATION
  Load/validate dictionaries
    -> canonical labels and internal IDs
    -> normalized forms and aliases
    -> exact indexes + approximate-retrieval index

PER ADDRESS
  Preserve original text
    -> make non-destructive normalized views
    -> discover candidate name spans (exact/alias)
    -> recover additional candidates (bounded fuzzy/typing-aware)
    -> verify candidate-to-span explanations
    -> jointly select street/ward/province (or null)
    -> emit canonical three-field dictionary
```

### 5.1 Do not destroy the input during cleaning

Keep the original text alongside derived views and an offset mapping back to the original. Unicode normalization differs from accent removal; accent removal is deliberately lossy. Administrative words (`đường`, `phường`, `p.`, `tp.`) are evidence of field type, not mere stop words.

Digits and punctuation require context. The slide shows street `3/2` (p. 6), alongside examples with house numbers `53`, `56/33`, and `69/49/59` (p. 8). Thus, **never globally delete all digits, slashes, or punctuation**. Separately test numbers in street names, numbered wards, street-level components, and house-number prefixes.

Commas, whitespace, and typical component order are helpful but optional. Inputs may omit commas, include extra delivery instructions, or change order.

### 5.2 Candidate retrieval - favor recall within strict limits

Initially use normalized-name hash maps plus a token trie or bounded token-window scan for exact and alias matches. Aho-Corasick is a possible multi-pattern exact-matching optimization **only if profiling justifies it**.

Each candidate should record:

- canonical entry ID, field type, and exact output label;
- original input start/end span;
- matched representation/path (exact, alias, accent-folded, fuzzy, Telex/VNI, etc.);
- correction explanation and cost;
- contextual signals such as nearby markers and boundaries.

Candidates supported by different matching views should be deduplicated without counting repeated paths as independent confirmations.

For approximate street search, retrieve a shortlist from character fragments (e.g. character n-grams) before costly edit-distance verification. The smaller province/ward dictionaries may permit direct bounded comparison; benchmark first. Support alternate retrieval paths for short, numeric, initial-letter, and heavily corrupted forms that may not share enough n-grams.

**Critical non-greedy rule:** Finding an exact substring is not the same as identifying the intended full name. With ward labels `An Phú` and `An Phú Đông`, input `phuong an phu dnog` must preserve the possible longer fuzzy candidate instead of stopping permanently at the exact prefix.

### 5.3 Bounded verification, not unconstrained autocorrect

For candidate spans, assess ordinary insertion, deletion, substitution, and adjacent-transposition errors. A thresholded/early-stopped dynamic-programming distance is one possible baseline. Compare **candidate name spans** with dictionary names, not whole addresses with every street.

Verification should consider length, full-name coverage, original text evidence, and the meaning of edits. A short numeric name generally needs stricter acceptance rules than a long distinct street name. Any pruning heuristic (length bounds, edit bands, n-gram thresholds) must be validated against the actual supported edit operations, especially multi-character typing transformations.

Do not generate and permanently apply a single "corrected address" up front. Literal text and possible corrections should remain competing hypotheses until selection.

## 6. Error taxonomy and planned recovery

| Family | Examples / concern | Method |
|---|---|---|
| Empty/noise-only | `""`, `","`, repeated punctuation | Early harmless return; no fuzzy search. |
| Case/Unicode/accents | upper/lower case, accentless input, decomposed Unicode | NFC and alternate matching views; preserve accent collisions. |
| Ordinary spelling errors | missing/extra/wrong letters, adjacent swaps | Bounded edit verification. |
| Word boundaries | joined words, split words, repeated whitespace | Controlled compact matching and flexible spans. |
| Abbreviations | `TP HCM`, `p.`, `LTK` | Explicit aliases or cautious initials; ambiguous expansions stay separate. |
| Structure | no commas, field reorder, missing components | Joint interpretation of independently discovered spans. |
| Extra text | names, delivery notes, repeated phrases | Leave irrelevant text unassigned. |
| Numeric ambiguity | `3/2`, house numbers, numbered wards | Preserve numeric evidence and use contextual parsing. |
| Telex remnants / mixed composition | `cungx`, `Đuơcj`, malformed vowel marks | Input-method-specific interpretations constrained by dictionary entries. |
| VNI remnants | `cung4`, residual modifier digits | Consider VNI semantics without treating every digit as a modifier. |
| Keyboard-adjacent substitutions | neighboring keys on a known layout | Optional, layout-aware weighted edit rule. |
| Failed editing attempts | typing `l` when Backspace was intended | General multi-edit recovery before specialized patterns. |

### 6.1 Vietnamese Telex/VNI details

The discussion raised these specific **illustrative spelling intentions**, which should be included in synthetic testing if the intended word appears in a test dictionary:

| Observed input | Intended form | Important interpretation |
|---|---|---|
| `cungx` | `cũng` | Residual Telex `x` indicates ngã, not merely an arbitrary appended `x`. |
| `Ngươiư` | `người` | Mixed composition/extra vowel; exact user keystrokes are not uniquely inferable. |
| `Duợc` | `được` | Distinct initial consonant and vowel forms; accent-folding can help retrieve. |
| `Đuơcj` | `được` | Residual `j` (nặng) plus incomplete vowel transformation. |
| `cung4` | `cũng` | Potential raw VNI ngã representation; a digit could also be literal. |

Useful Telex correspondences include `x` (ngã), `j` (nặng), `dd` -> `đ`, `uw` -> `ư`, and `ow` -> `ơ`. Common VNI examples include `4` (ngã), `u7` -> `ư`, `o7` -> `ơ`, and `d9` -> `đ`. Interpretation is contextual, not a blanket global rewrite: modifier keys can appear literally or coexist with already composed text.

**Design:** Keep original text; retrieve candidates using original, accent-insensitive, and selected typing-aware views; then apply candidate-specific, bounded verification. A full Telex/VNI decoder is not a prerequisite, and it would be dangerous to interpret all numbers/letters as modifiers automatically.

### 6.2 Keyboard and editing-action errors

One constructed sequence: user intends `An`, types `Ank` mistakenly, then hits `l` rather than Backspace, leaving `Ankl`. Removing only `l` still leaves `Ank`; recovery may need two edits. Do not assign low deletion cost to every terminal `l`. If neighbor-key rules are added, make keyboard layouts explicit and compare improvement against negative controls.

### 6.3 Combined mistakes and irreducible ambiguity

Test combined perturbations, not only one error at a time. For example, an address may have no accents, joined words, a misspelled street, and abbreviated province simultaneously.

Not every transformed input has a uniquely recoverable answer. `tan an` may collapse multiple accented dictionary entries; deleting `Đông` from `An Phú Đông` can yield the **different valid entry** `An Phú`. Synthetic tests must not blindly preserve the source label after an information-destroying mutation.

## 7. Joint selection and rejection

Although dictionaries may be flat, interpretation should be **joint**. Select up to one candidate per field, plus a null option. Avoid greedy field extraction and deletion of already-matched text.

Use explainable evidence:

1. Strong, complete exact match or supported alias.
2. Amount and location of input text explained by the candidate.
3. Context markers: street/ward/province prefixes and nearby separators.
4. Correction severity, including typing-aware versus arbitrary edits.
5. Conflicting use of the same original span by different fields.
6. Soft address-order expectations, without requiring a fixed template.
7. Geographic consistency only if relationships are actually available and authorized.

An exact `An Phú` prefix and a mostly matching `An Phú Đông` compete; the longer candidate should not be dropped before evaluating coverage and repair cost. Conversely, simply preferring longer names would overcorrect.

A small beam/top-K per field can bound combinations. As an illustration, five candidates plus null for each of three fields produce at most `6^3 = 216` combinations. **K = 5 is not a settled parameter**: first measure whether the correct answer survives retrieval and pruning.

A candidate needs adequate **absolute** evidence, not merely a better score than the alternatives. Also check whether close competing candidates remain. A tie between different canonical labels should not be settled by input file order. Returning null for unresolved ambiguity is our provisional approach, pending actual scoring guidance.

Return well-supported fields independently: an uncertain ward does not require nullifying a reliable street/province. Do not infer an unmentioned field solely from external geographic knowledge unless the task clarifies that behavior.

## 8. Performance engineering in pure Python

### 8.1 Why shortlist-first matters

A full scan of approximately 23,800 street entries with ordinary dynamic-programming edit distance is a poor default. Illustratively, a 20-by-20 comparison against each entry entails ~9.52 million DP-cell updates for **one span**, before adding additional spans or input-method alternatives. This is an operation-count illustration, **not a measured runtime**.

Optimize by *avoiding work*:

- Precompute normalized dictionary forms and indexes.
- Use entry IDs and compact postings in retrieval structures.
- Deduplicate candidate/span comparisons from multiple retrieval paths.
- Cache derived span forms within a request.
- Limit span windows, approximate candidates, edit comparisons, and joint combinations.
- Keep the exact/alias path inexpensive; invoke fuzzy/typing-aware search when justified.
- Handle empty/noise-only strings before heavy matching.
- Keep the initial implementation synchronous and single-threaded unless profiling suggests otherwise.

Rules must be benchmarked for both **latency and candidate recall**. Restricting candidate counts too aggressively can meet runtime while quietly losing the correct answer.

### 8.2 Separate startup from request costs

The ideal process lifecycle is **initialize indexes once, match many strings**. The grader may instead instantiate the matcher per request or start a new process each time. These are materially different performance conditions. Measure:

- interpreter startup;
- import and dictionary loading;
- index construction;
- first request;
- subsequent requests;
- mean, upper percentiles, and worst observed latency.

**[USER, 2026-11-05]** Until the official timing mechanism is supplied, assume
each address is graded by launching a fresh script and reading its console
output. The primary benchmark includes interpreter startup, imports, dictionary
I/O, reindexing, matching and stdout, measured externally until the complete
result line is received. Record process exit separately. Compare this inclusive distribution
to both limits. Shared-process warm calls and amortized batch throughput are
diagnostics and cannot establish compliance under this assumption. Use all cases
by default; explicitly label any sampled run and retain its case IDs and timings.

The [construction review](cold-start-optimization.md) records removal of repeated
normalization, current cold measurements, feature-removal controls and startup
probes. Both scale limits still fail. Offline compilation into queryable
dictionary data is a conditional next design if the submission contract permits
it; it must not be assumed to exclude timed preparation or solve Python startup.

**[USER, subsequent confirmation]** The dictionary is fixed before submission.
The [prepared-dictionary implementation](prepared-dictionary.md) now compiles it
offline and measures a standalone runtime on test case 1. Report launch-flag and
bytecode-packaging dependencies explicitly; a passing prepared-bundle result
does not change the documented failure of the original build-at-launch CLI.

Do not assume moving initialization into module import excludes it from grading time. An unconstrained input length also makes a universal wall-clock bound unprovable without additional contractual limits; test long adversarial inputs rather than silently truncating them.

## 9. Python implementation and compilation-safe packaging

### 9.1 Development language and dependencies

**[USER]** Fully Python; target CPython 3.14 provisionally. **[DESIGN]** Use the Python standard library only, without runtime pip installation, external executables, compiled extensions, or internet calls. Useful built-ins/modules include `dict`, `set`, `list`, `unicodedata`, `pathlib`, `json`, `typing`, `time`, `py_compile`, and `unittest`.

Avoid depending on newly introduced 3.14 features unless necessary. If the official grader later uses an older version, test and adjust syntax accordingly.

### 9.2 Module boundaries and provisional API

Keep algorithm components conceptually separate: loader, normalization, index builder, candidate discovery, verifier, selector, adapter. The initial delivery can use one clear `submission.py` file plus dictionary files and development tests to minimize import-path risk; change packaging if a starter template requires it.

Provisional internal interface (not the official grader signature):

```text
matcher = AddressMatcher(streets, wards, provinces)  # prepare once
prediction = matcher.identify(address_string)        # new dict per call
```

The adapter is solely responsible for CLI/stdin/JSON/function-call protocol once supplied. Core logic must not rely on interactive prompts, print debugging to stdout, network calls, or per-request file reads. All request-specific mutable state stays local to the call.

### 9.3 Execution gates - all are release blocking

| Gate | Requirement |
|---|---|
| Syntax compilation | All submitted `.py` files compile under the target interpreter. |
| Import | The submission imports cleanly with no unexpected side effects. |
| Initialization | Required files exist, encodings and schemas are valid, indexes build. |
| Invocation | The exact grader-facing function/CLI contract is honored. |
| Output format | Exactly the required fields and value types; no debug pollution. |
| Edge-case behavior | Invalid/empty content does not crash; implementation defects remain visible. |
| Offline environment | A fresh machine with the allowed interpreter and no internet can run it. |
| Timing | Average and maximum satisfy the official timing definition. |

**Compilation is necessary but not sufficient.** Python's `py_compile` checks syntax/bytecode compilation; it does not guarantee that imports, file loading, or function calls succeed. Avoid blanket `except Exception: return {all nulls}` because that could mask broken installation or programming errors as valid predictions.

Data paths should be derived from the module location (or an official data-directory argument), not the process's working directory. Specify UTF-8 explicitly after inspecting the actual files. Test launching from a path with spaces and from a different working directory.

### 9.4 Preflight checks (planned, not yet executed)

```bash
python3.14 --version
python3.14 -m py_compile submission.py
python3.14 -c "import submission"
python3.14 -m unittest discover -s tests -v
```

Compile every Python module if the release contains more than one. Add a grader-adapter smoke test, not just internal unit tests. Finally copy the exact release bundle into a fresh, offline environment and re-run the checks there.

## 10. Testing strategy and acceptance criteria

### 10.1 A controlled challenge dictionary first

Before using a large online dataset, construct a small fixture containing:

- Exact screenshot-style address examples and canonical prefixes.
- Alternative correction database with `LTK` and a missing `Diên Hồng` ward.
- A numeric street (`3/2`) plus slash-separated house numbers.
- Overlapping names (`An Phú`, `An Phú Đông`).
- Shared names across fields and accent-folding collisions.
- Ambiguous abbreviation expansions.
- Typing-aware examples and negative controls.
- Empty dictionaries, duplicates, and permuted entry order.

### 10.2 Test sources

1. **Handwritten edge cases:** empty, punctuation-only, exact sample, abbreviation, overlapping candidates, missing fields, and malformed address structure.
2. **Synthetic general mutations:** capitalization, accents, spacing, insert/delete/substitute/swap characters, extra text, and combinations.
3. **Typing-process simulations:** Telex/VNI modifiers left literal, partial conversion, keyboard-neighbor errors, and failed correction actions.
4. **Adversarial and invariant cases:** repeated words, very long strings, conflicting fields, duplicate names, deterministic behavior, and non-null dictionary membership.

Keep separate groups for **recoverable**, **unsupported**, and **genuinely ambiguous** strings. Synthetic perturbations that turn one valid name into another should not automatically keep the original label as ground truth.

### 10.3 Metrics

- Complete-object accuracy and per-field accuracy.
- False non-null assignments, missed recoverable matches, and null/tie behavior.
- Candidate retrieval recall **before** final selection.
- Failures categorized by retrieval, normalization, segmentation/span finding, verification, ranking, and rejection.
- Runtime mean, tail percentiles, worst observed request, startup cost, index-build time, and memory usage.
- Accuracy gain **and regression** from each added rule family, especially Telex/VNI and keyboard rules.

When the 1,000 development cases arrive, preserve a held-out validation subset; avoid sharing near-duplicate generated samples across training-style development and validation splits (although **no ML is used**). The private test carries most of the final score, so rule generalization matters more than public-case memorization.

## 11. Stepwise implementation plan

| Stage | Deliverable | Exit criterion |
|---|---|---|
| 1. Contract/fixture | Provisional input-output schema, compact canonical dictionaries, benchmark and test scaffolding | Empty inputs and canonical outputs are well defined. |
| 2. Executable Python baseline | Importable module, data loader, exact-name/alias matcher, JSON adapter placeholder | Compile/import/init/smoke tests pass on CPython 3.14. |
| 3. Span-aware matching | Context markers, exact-overlap handling, candidate tracking, joint field selection | Exact partial prefixes do not suppress plausible complete matches. |
| 4. General typo recovery | Bounded retrieval and verification, spaced/joined-word cases | Recall improves without unacceptable false matches or latency. |
| 5. Telex/VNI and keyboard recovery | Residual modifiers, mixed composition, conservative editing-action rules | Provided examples and counterexamples pass. |
| 6. Real-dataset integration | Inspect actual files, reconcile prefixes/duplicates/relations, evaluate on development examples | Predictions remain canonical; failures are categorized and fixed systematically. |
| 7. Performance and release | Optimize with profiling; freeze dependencies; README; offline verification | Exact submission meets compilation, interface, correctness, and timed tests. |

**Scope control:** Do not begin with a complex universal spelling-correction engine. Establish a fully runnable exact baseline first, then add only rules that demonstrably improve identification under the performance budget.

## 12. Outstanding questions to resolve from official materials

1. **Grader protocol:** importable function signature, class interface, stdin/stdout format, or JSON file? Does it expect the inner three-field result, or a wrapper?
2. **Official Python version:** is CPython 3.14 accepted, and what standard-library/platform constraints apply?
3. **Data schema:** are lists genuinely independent? Why does the screenshot include `list_district` rather than `list_street`? Are there canonical aliases, link IDs, or duplicate records?
4. **Scoring details:** is a case correct only when all three fields match, or are fields scored independently? What is the prescribed behavior for ambiguity?
5. **Missing-component inference:** must unmentioned fields be null, or may inferential matches receive credit?
6. **Timing measurement:** are imports, loading, and first-request initialization counted? How many calls share a process? What input-length limits exist?
7. **Allowed artifacts:** what are the policies on supplementary alias tables, public reference data, vendored code, and standard-library support?
8. **Permitted error policy:** may the matcher return null for unresolved ties, and how strict should typo repair be under the evaluator's labels?

These uncertainties **do not block** the exact-match foundation. They do block claiming that the final interface, null policy, or runtime compliance is already settled.

## 13. Final design commitments

- **Canonical database labels are authoritative.** Never replace them with outside map data or normalized spellings.
- **Flat dictionaries are the default assumption.** Do not hardcode unsupported geographic relationships.
- **Separate retrieval from selection.** Broad candidate search, narrow evidence-based acceptance.
- **Keep the original input.** All normalization, abbreviation handling, and typing interpretations are non-destructive hypotheses.
- **Treat Telex/VNI and keyboard errors as first-class phenomena.** They should inform both retrieval and verification, not become ad-hoc global replacements.
- **Reject unsupported answers.** `None` is a legitimate field result, not a sign of implementation failure.
- **Protect runtime and reliability together.** Pure Python, deterministic behavior, bounded expensive work, and a release process that proves compilation, import, initialization, protocol, and offline execution.
- **Benchmark every added feature.** Record improvements and regressions in accuracy, candidate recall, and latency before accepting it.

---

## References and notes

**Primary source:** *Address Segmentation*, instructor slide deck provided in this project. Most relevant pages: **5** (noisy OCR examples), **6** (database-dependent correction and null), **7** (dataset scale), **8** (JSON-like sample records and canonical labels), **9** (development files and private test), **10** (example scoring table), **11** (constraints and submission rules).

**Additional project requirements:** Python-only submission, compilation as an immediate-failure condition, and a provisional CPython 3.14 target were provided during our discussion, not verified from the slides.

**Optional prototype resources:** [vietnamese-provinces-database](https://github.com/thanglequoc/vietnamese-provinces-database), [vietnam_dataset](https://github.com/thien0291/vietnam_dataset), and [Geofabrik Vietnam](https://download.geofabrik.de/asia/vietnam.html). These must be validated independently and are not replacements for the instructor's test dictionary.
