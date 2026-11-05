# User dataset 1

These are user-supplied development examples, not instructor data or a finalized
grader contract. The original `all_raw.json`, `list_street.txt`, `list_ward.txt` and
`list_province.txt` are retained byte for byte. The lists are flat correction
dictionaries; no geographic relationships are assumed.

`all_raw.json` contains 1,000 objects with `address` and the three-field `result`.
Every non-null result is present in its corresponding dictionary. There are no
duplicate addresses. The dictionaries contain 162 streets, 319 wards and 34
provinces, so this fixture is smaller than the documented street-scale target.

`review.json` records six ambiguous ward expectations at one-based rows 114, 208,
691, 740, 945 and 977. Each address uses accentless `Long Thanh`, and both
`Long Thành` and `Long Thạnh` exist with the same administrative prefix. The
documented ambiguity policy requires ward `null`; the other fields stay intact.
The original labels remain available for honest before/after comparisons.

From the repository root:

```sh
python3.14 -m tools.prepare_dataset --source-dir testcases/1 --review testcases/1/review.json --output-dir reports/user-1
```

The adapter writes `dictionary.json`, `raw-cases.jsonl`, reviewed `cases.jsonl`
and a provenance `manifest.json`. It rejects malformed input, labels outside the
dictionaries, conflicting duplicate inputs and stale collision reviews. Generated
files belong under ignored `reports/`. See [the review](../../docs/user-dataset-review.md)
for measurements and reproduction commands. These examples were inspected during
development; no held-out accuracy claim is made.
